//! Nyrqis launcher-init — the compiled container PID-1 (ADR-0020).
//!
//! The first process inside the container's new namespaces (exec'd by
//! the Rust clone child entry — zero Python between clone and exec).
//! It becomes the namespace's PID-1 init, mirroring `launcher.py`:
//!
//! 1. set the hostname (no shell — FIND-BACKEND-004)
//! 2. harden cgroup mounts (defense in depth — FIND-BACKEND-003)
//! 3. bring loopback up (own network namespace, best-effort)
//! 4. fork the container command; the child installs the container's
//!    pre-built seccomp filter (prctl — the install path is the
//!    platform-critical part; policy compilation stays in the backend
//!    above the boundary) and execs the command
//! 5. the init forwards supervisor signals, reaps the command, and
//!    exits with its status (or dies by its signal)
//!
//! The init runs UNFILTERED by design (the model tini uses): the
//! seccomp policy applies to the command child and its descendants, so
//! a container without CAP_PROCESS_SPAWN cannot EPERM the init's own
//! fork.

use nyrqis_launcher::{
    FORWARD_SIGNALS, bring_loopback_up, harden_cgroup_mounts,
    install_seccomp, load_bpf, parse_argv, set_hostname,
};
use std::os::raw::c_int;
use std::sync::atomic::{AtomicI32, Ordering};

/// The container command's pid, read by the signal forwarder (an
/// async-signal-safe atomic load + kill — no allocation in the
/// handler).
static CHILD_PID: AtomicI32 = AtomicI32::new(0);

extern "C" fn forward(signum: c_int) {
    let pid = CHILD_PID.load(Ordering::Relaxed);
    if pid > 0 {
        unsafe { libc::kill(pid, signum) };
    }
}

fn err_out(msg: &str) -> ! {
    let mut bytes = b"nyrqis launcher: ".to_vec();
    bytes.extend_from_slice(msg.as_bytes());
    bytes.push(b'\n');
    // Bypass std's buffering: this process may be a fork of the init.
    unsafe {
        libc::write(2, bytes.as_ptr() as *const libc::c_void, bytes.len());
        libc::_exit(1);
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let (args, command) = match parse_argv(&argv) {
        Ok(x) => x,
        Err(e) => {
            eprintln!("nyrqis launcher: {e}");
            std::process::exit(2);
        }
    };
    if command.is_empty() {
        eprintln!("nyrqis launcher: no command provided");
        std::process::exit(3);
    }

    // Step 1 — hostname, no shell (FIND-BACKEND-004). Best-effort:
    // failures are logged (via exit code only — no logging framework
    // in the container) and never fatal.
    let _ = set_hostname(&args.hostname);

    // Step 2 — cgroup mount hardening (FIND-BACKEND-003, defense in
    // depth).
    let _ = harden_cgroup_mounts();

    // Step 3 — usable loopback (own network namespace, best-effort).
    // Before the seccomp install: backend setup, not container
    // behavior.
    let _ = bring_loopback_up();

    // Step 4 — become the container's PID-1 init. Reset the
    // dispositions Python ignores at startup (SIGPIPE/SIGXFSZ): SIG_IGN
    // survives fork AND exec, so the pre-init launcher leaked an
    // ignored SIGPIPE into the container command.
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
        libc::signal(libc::SIGXFSZ, libc::SIG_DFL);
    }

    let child_pid = unsafe { libc::fork() };
    if child_pid < 0 {
        err_out("fork failed");
    }
    if child_pid == 0 {
        // The container command — still trusted launcher code until the
        // exec below. Install the container's seccomp filter on THIS
        // process (the exec'd command and its descendants then run
        // filtered) and exec. On failure, report and die with the
        // conventional statuses.
        match load_bpf(&args.bpf_file) {
            Ok(Some(program)) => {
                let (ok, errno) = install_seccomp(&program);
                if !ok {
                    eprintln!(
                        "nyrqis launcher: data-plane enforcement NOT in effect: \
                         seccomp install failed (errno={errno})"
                    );
                    if args.strict_seccomp {
                        std::process::exit(4);
                    }
                }
            }
            Ok(None) => {} // no policy file — enforcement OFF (parity)
            Err(e) => {
                eprintln!("nyrqis launcher: {e}");
                if args.strict_seccomp {
                    std::process::exit(4);
                }
            }
        }
        let cmd0 = command[0].as_str();
        let cargs: Vec<std::ffi::CString> = match command
            .iter()
            .map(|a| std::ffi::CString::new(a.as_str()))
            .collect()
        {
            Ok(c) => c,
            Err(_) => err_out("command contains a NUL byte"),
        };
        let mut ptrs: Vec<*const libc::c_char> =
            cargs.iter().map(|c| c.as_ptr()).collect();
        ptrs.push(std::ptr::null());
        unsafe {
            // execvp: PATH search with the current environment — the
            // same semantics as the Python launcher's execvpe with
            // os.environ.copy().
            libc::execvp(cmd0.as_ptr() as *const libc::c_char, ptrs.as_ptr());
            let err = std::io::Error::last_os_error();
            match err.raw_os_error() {
                Some(libc::ENOENT) => {
                    eprintln!("nyrqis launcher: command not found: {cmd0}");
                    libc::_exit(127);
                }
                _ => {
                    eprintln!("nyrqis launcher: exec failed: {err}");
                    libc::_exit(126);
                }
            }
        }
    }

    // Init: forward supervisor signals, then supervise the command to
    // completion. PID-1 semantics discard signals sent before a handler
    // is installed, so the forwarders MUST be installed before the
    // manager can signal us — the manager waits for SigCgt anyway.
    CHILD_PID.store(child_pid, Ordering::Relaxed);
    for sig in FORWARD_SIGNALS {
        unsafe {
            libc::signal(sig, forward as *const () as libc::sighandler_t);
        }
    }

    let mut status: c_int = 0;
    let waited = unsafe { libc::waitpid(child_pid, &mut status, 0) };
    if waited < 0 {
        std::process::exit(1);
    }
    if libc::WIFSIGNALED(status) {
        let sig = libc::WTERMSIG(status);
        // Die by the same signal so the manager observes WIFSIGNALED
        // (matching Popen's negative-returncode semantics).
        unsafe {
            libc::signal(sig, libc::SIG_DFL);
            libc::raise(sig);
            // Only reached if the signal was ignored; fall through to a
            // conventional status.
            libc::_exit(128 + sig);
        }
    }
    let code = if libc::WIFEXITED(status) {
        libc::WEXITSTATUS(status)
    } else {
        1
    };

    // Brief best-effort sweep for orphans the command left behind (the
    // 50 x 10ms bound mirrors the Python launcher: 0.5s max added to a
    // container's exit; stragglers are SIGKILLed when this PID 1
    // exits regardless).
    for _ in 0..50 {
        let mut st: c_int = 0;
        let r = unsafe { libc::waitpid(-1, &mut st, libc::WNOHANG) };
        if r == 0 {
            break;
        }
        if r < 0 {
            break; // ECHILD — no children left
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    std::process::exit(code);
}
