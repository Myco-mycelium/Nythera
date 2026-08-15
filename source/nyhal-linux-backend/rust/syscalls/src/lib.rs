//! Nyrqis direct syscall wrappers — ADR-0020 migration priority #2.
//!
//! **Implemented 2026-08-13 (sethostname / prctl / unshare / mount;
//! direct-syscall container launch landed 2026-08-13).** These wrappers
//! replace the Python backend's `ctypes` calls (`sethostname(2)` and
//! `prctl(2)` in `backend/launcher.py`) and the `unshare(1)` subprocess
//! in `backend/container.py` (the direct-syscall launcher transition,
//! `docs/implementation_plan.md` §4.1). Each entry point calls exactly
//! one libc function and returns **0 on success or a negative errno on
//! failure** (the Linux syscall convention), so the Python side maps
//! `-errno` to `OSError(errno, os.strerror(errno))` exactly as the
//! `ctypes` path does today.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state, no pointers into
//! Python objects; caller-owned buffers stay owned by the caller.
//!
//! **`clone` is IMPLEMENTED (ABI 1.2.0) with a Rust child entry point**
//! (`nyrqis_syscalls_clone` + `nyrqis_syscalls_launch_child`):
//! `clone(2)` with fork semantics (no `CLONE_VM` — the child gets a
//! private copy-on-write address space, so the stack argument is
//! ignored) and a caller-supplied Rust entry that runs in the child
//! and never returns. The direct-syscall launcher passes the
//! container's PID-1 entry (`launch_child`: root maps, hardened proc
//! mount, PDEATHSIG, exec the launcher) with ALL the namespace flags
//! in one call, so the child is created directly in its namespaces —
//! no Python between fork and exec (the crate-less fallback keeps the
//! two-stage `os.fork` setup child in `backend/container.py`).
//!
//! The FFI clone validates: `CLONE_VM` is rejected (the child entry
//! needs a private address space — a thread-shared stack would make
//! the entry's stack frame dangle), the entry must be non-null, and
//! the argument pointer must be non-null. Returns the child's pid to
//! the parent; the child runs `entry(arg)` and `_exit`s with its
//! return (never returns). The `arg` pointer references parent memory
//! inherited copy-on-write — the parent must not rely on the child's
//! mutations and the child must not free it.

use libc::{c_char, c_int, c_ulong, c_void};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
/// 1.1.0 adds `nyrqis_syscalls_mount` / `nyrqis_syscalls_mount_proc`
/// (the direct-syscall launcher's procfs mount); 1.2.0 replaces the
/// `nyrqis_syscalls_clone` stub with the real fork-semantics clone +
/// the Rust child entry (`nyrqis_syscalls_launch_child`). The loader
/// requires this minimum so an older library is skipped before the
/// new symbols are resolved.
pub const ABI_VERSION: u32 = 0x0001_0200;

// NyrqisErr codes (negative i32 returns, following the Linux syscall
// convention of negative errno). ERR_INTERNAL is deliberately OUTSIDE
// the errno range (1..=4095): the contract maps -errno -> OSError, so
// an in-range internal code (e.g. -4 = EINTR) would be reported as a
// real kernel error.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL
pub const ERR_NOT_IMPLEMENTED: i32 = -38; // ENOSYS
pub const ERR_INTERNAL: i32 = -4096;

/// Capture errno from the last libc call (0 if the platform reported
/// none). `last_os_error` reads the thread's errno, which is exactly
/// the value the failing call set.
fn last_errno() -> i32 {
    std::io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(0)
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_version() -> u32 {
    ABI_VERSION
}

/// Set the UTS hostname (replaces the Python backend's `ctypes`
/// `sethostname(2)`; the container hostname is an argv entry, never
/// shell-interpolated — FIND-BACKEND-004). Returns 0 or negative
/// errno. `name` is a caller-owned buffer of `len` bytes (no NUL
/// requirement — `sethostname(2)` uses `len`).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_sethostname(
    name: *const u8,
    len: usize,
) -> i32 {
    if name.is_null() {
        return ERR_INVALID_ARGS;
    }
    let rc = libc::sethostname(name as *const c_char, len);
    if rc == 0 {
        0
    } else {
        -last_errno()
    }
}

/// prctl(2) wrapper (PR_SET_HOSTNAME fallback today; PR_SET_NO_NEW_PRIVS
/// / PR_SET_SECCOMP for the seccomp install path in a later pass).
/// Returns 0 or negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_prctl(
    option: u32,
    a2: u64,
    a3: u64,
    a4: u64,
    a5: u64,
) -> i32 {
    let rc = libc::prctl(
        option as c_int,
        a2 as c_ulong,
        a3 as c_ulong,
        a4 as c_ulong,
        a5 as c_ulong,
    );
    if rc == 0 {
        0
    } else {
        -last_errno()
    }
}

/// unshare(2) wrapper (replaces the `unshare(1)` subprocess in
/// `backend/container.py` — the plan's direct-syscall transition,
/// `docs/implementation_plan.md` §4.1). `flags` is the CLONE_NEW* bit
/// mask (e.g. CLONE_NEWUSER|CLONE_NEWNS|CLONE_NEWUTS|CLONE_NEWIPC).
/// Returns 0 or negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_unshare(flags: u64) -> i32 {
    let rc = libc::unshare(flags as c_int);
    if rc == 0 {
        0
    } else {
        -last_errno()
    }
}

/// mount(2) wrapper (the direct-syscall launcher's generic primitive;
/// `nyrqis_syscalls_mount_proc` is the no-arg convenience entry point
/// used post-fork). All four path/fstype arguments are caller-owned
/// NUL-terminated buffers; `data` is opaque (NULL for procfs).
/// Returns 0 or negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_mount(
    source: *const c_char,
    target: *const c_char,
    fstype: *const c_char,
    flags: u64,
    data: *const c_void,
) -> i32 {
    if target.is_null() || fstype.is_null() {
        return ERR_INVALID_ARGS;
    }
    let rc = libc::mount(source, target, fstype, flags as c_ulong, data);
    if rc == 0 {
        0
    } else {
        -last_errno()
    }
}

/// Mount a fresh procfs at `/proc` (the container init's view of its
/// own PID namespace — unshare(1)'s `--mount-proc` equivalent).
/// No-argument on purpose: the call happens in the container child
/// between `fork` and `exec`, where the child must not allocate or
/// touch Python objects, so no buffers cross the boundary here.
/// Hardened like `unshare(1)`'s proc mount: nosuid, nodev, noexec.
/// Returns 0 or negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_mount_proc() -> i32 {
    const PROC: &[u8] = b"proc\0";
    const PROC_MOUNT: &[u8] = b"/proc\0";
    let rc = libc::mount(
        PROC.as_ptr() as *const c_char,
        PROC_MOUNT.as_ptr() as *const c_char,
        PROC.as_ptr() as *const c_char,
        (libc::MS_NOSUID | libc::MS_NODEV | libc::MS_NOEXEC) as c_ulong,
        std::ptr::null(),
    );
    if rc == 0 {
        0
    } else {
        -last_errno()
    }
}

/// The child entry point type: runs in the clone child, never returns
/// (its return is the child's exit status).
pub type ChildEntry = unsafe extern "C" fn(*const c_void) -> i32;

/// The libc::clone callback — dispatches to the caller's entry and
/// exits with its return. The `arg` it receives points at a
/// `CloneCtx` on the parent's stack: the child is a fork (no
/// `CLONE_VM`), so it owns a copy-on-write view of that memory even
/// after the parent's frame is gone.
extern "C" fn _clone_trampoline(arg: *mut c_void) -> c_int {
    if arg.is_null() {
        return 125;
    }
    // SAFETY: the child is a fork (no CLONE_VM); the ctx lives on the
    // parent's stack, which the child owns a copy-on-write view of.
    let ctx = unsafe { &*(arg as *const CloneCtx) };
    let rc = unsafe { (ctx.entry)(ctx.arg) };
    unsafe { libc::_exit(rc as c_int) };
}

#[repr(C)]
struct CloneCtx {
    entry: ChildEntry,
    arg: *const c_void,
}

/// clone(2) with fork semantics and a Rust child entry point
/// (ABI 1.2.0 — the direct-syscall launcher's fully Rust-native
/// child, no Python between fork and exec).
///
/// ``flags`` is the CLONE_NEW* mask (CLONE_VM is rejected: the child
/// must get a private address space — the entry's stack frame lives on
/// the parent's stack). The child runs ``entry(arg)`` and exits with
/// its return; the parent receives the child's pid. Returns 0 or
/// negative errno on failure (``ERR_INVALID_ARGS`` for a null entry /
/// arg or CLONE_VM). The caller must OR in SIGCHLD (or another child
/// signal) so the child is reapable — clone(2) with a zero signal
/// byte leaves the child unreaped on exit.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_clone(
    flags: u64,
    entry: Option<ChildEntry>,
    arg: *const c_void,
) -> i32 {
    let entry = match entry {
        Some(e) => e,
        None => return ERR_INVALID_ARGS,
    };
    if arg.is_null() {
        return ERR_INVALID_ARGS;
    }
    if flags & (libc::CLONE_VM as u64) != 0 {
        return ERR_INVALID_ARGS; // fork semantics only (see module docs)
    }
    let mut ctx = CloneCtx { entry, arg };
    // The child needs a real stack: glibc's clone bootstrap switches
    // the child's rsp to the passed stack pointer EVEN without
    // CLONE_VM (the kernel ignores it, the libc wrapper does not), so
    // a NULL/dummy pointer would crash the child's entry bootstrap.
    // mmap a per-call stack; the parent unmaps its copy after clone
    // returns (the child is a fork — it owns a copy-on-write view of
    // the mapping, so the unmap cannot affect it).
    const STACK_SIZE: usize = 64 * 1024;
    let stack = libc::mmap(
        std::ptr::null_mut(),
        STACK_SIZE,
        libc::PROT_READ | libc::PROT_WRITE,
        libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
        -1,
        0,
    );
    if stack == libc::MAP_FAILED {
        return -last_errno();
    }
    // The stack grows down: the libc wrapper expects the TOP of the
    // region (16-byte aligned for the child's bootstrap frame).
    let top = (stack as usize + STACK_SIZE) as *mut c_void;
    let rc = libc::clone(
        _clone_trampoline,
        top,
        flags as c_int,
        &mut ctx as *mut CloneCtx as *mut c_void,
    );
    libc::munmap(stack, STACK_SIZE);
    if rc < 0 {
        -last_errno()
    } else {
        rc
    }
}

/// The container's PID-1 launch entry (the direct-syscall launcher's
/// Rust-native child): written by the MANAGER before clone — inside
/// the new user namespace getuid() reports the overflow uid 65534, so
/// the real uid/gid to map MUST cross in the argument struct.
#[repr(C)]
pub struct LaunchArgs {
    pub write_fd: c_int,
    pub uid: u32,
    pub gid: u32,
    pub argc: usize,
    pub argv: *const *const c_char,
}

/// Write ``content`` to ``path`` (plain open/write/close — the only
/// libc calls the entry may use; no allocation between clone and
/// exec). Returns 0 or -errno.
unsafe fn _write_file(path: &[u8], content: &[u8]) -> i32 {
    let mut path_buf = [0u8; 64];
    if path.len() >= path_buf.len() {
        return -libc::EINVAL;
    }
    path_buf[..path.len()].copy_from_slice(path);
    let fd = libc::open(
        path_buf.as_ptr() as *const c_char,
        libc::O_WRONLY,
    );
    if fd < 0 {
        return -last_errno();
    }
    let mut written: usize = 0;
    while written < content.len() {
        let n = libc::write(
            fd,
            content[written..].as_ptr() as *const c_void,
            content.len() - written,
        );
        if n < 0 {
            let e = -last_errno();
            libc::close(fd);
            return e;
        }
        written += n as usize;
    }
    libc::close(fd);
    0
}

/// Format ``0 <id> 1\n`` into a stack buffer (no allocation).
fn _map_line(id: u32, buf: &mut [u8; 32]) -> &[u8] {
    // "0 " + decimal id + " 1\n"
    let mut idx = 2;
    let mut id = id as u64;
    let mut digits = [0u8; 10];
    let mut nd = 0;
    if id == 0 {
        digits[0] = b'0';
        nd = 1;
    } else {
        while id > 0 {
            digits[nd] = b'0' + (id % 10) as u8;
            nd += 1;
            id /= 10;
        }
    }
    buf[0] = b'0';
    buf[1] = b' ';
    for i in 0..nd {
        buf[idx] = digits[nd - 1 - i];
        idx += 1;
    }
    buf[idx] = b' ';
    buf[idx + 1] = b'1';
    buf[idx + 2] = b'\n';
    &buf[..idx + 3]
}

/// Report a failure to the manager (ERR: protocol) and return the exit
/// code (125 = generic setup failure, the manager reaps the child).
unsafe fn _fail(write_fd: c_int, msg: &[u8]) -> i32 {
    let mut buf = [0u8; 512];
    let prefix = b"ERR:";
    if write_fd >= 0 && prefix.len() + msg.len() < buf.len() {
        buf[..prefix.len()].copy_from_slice(prefix);
        buf[prefix.len()..prefix.len() + msg.len()].copy_from_slice(msg);
        buf[prefix.len() + msg.len()] = b'\n';
        let n = prefix.len() + msg.len() + 1;
        let _ = libc::write(write_fd, buf.as_ptr() as *const c_void, n);
    }
    let _ = libc::write(
        2,
        b"nyrqis launch child: setup failed\n".as_ptr() as *const c_void,
        b"nyrqis launch child: setup failed\n".len(),
    );
    125
}

/// The container PID-1's launch entry (the Rust-native child entry
/// point of the direct-syscall launcher, ABI 1.2.0). Runs in the clone
/// child — created by ``nyrqis_syscalls_clone`` with ALL the namespace
/// flags (NEWUSER|NEWNS|NEWUTS|NEWIPC|NEWPID[|NEWNET]|SIGCHLD) — and
/// performs the launcher's pre-exec duties entirely in Rust:
///
/// 1. Root maps: ``setgroups deny`` (best effort), then ``uid_map`` /
///    ``gid_map`` mapping the manager-captured uid/gid to root.
/// 2. ``PR_SET_PDEATHSIG(SIGKILL)`` — if the manager dies before this
///    process execs, the container is killed instead of orphaned
///    (cleared on exec; the residual window matches the fork path).
/// 3. Hardened procfs mount at ``/proc``.
/// 4. ``execv`` the launcher (argv crossed in ``LaunchArgs``).
///
/// No allocation, no logging, no Python — between clone and exec only
/// plain libc calls run. Returns an exit code on failure (the
/// trampoline exits with it); never returns on success.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_launch_child(
    arg: *const c_void,
) -> i32 {
    if arg.is_null() {
        return 125;
    }
    let args = &*(arg as *const LaunchArgs);
    // 1. Root maps. The setgroups knob may be absent (older kernels):
    //    best effort, like the fork path's floor.
    let _ = _write_file(b"/proc/self/setgroups\0", b"deny\n");
    let mut uid_line = [0u8; 32];
    let mut gid_line = [0u8; 32];
    let uid_map = _map_line(args.uid, &mut uid_line);
    let gid_map = _map_line(args.gid, &mut gid_line);
    if _write_file(b"/proc/self/uid_map\0", uid_map) != 0 {
        return _fail(args.write_fd, b"root map write failed");
    }
    if _write_file(b"/proc/self/gid_map\0", gid_map) != 0 {
        return _fail(args.write_fd, b"root map write failed");
    }
    // 2. PDEATHSIG — SIGKILL (9) if the manager (our parent) dies.
    libc::prctl(libc::PR_SET_PDEATHSIG, 9, 0, 0, 0);
    // 3. Hardened procfs mount.
    const PROC: &[u8] = b"proc\0";
    const PROC_MOUNT: &[u8] = b"/proc\0";
    let rc = libc::mount(
        PROC.as_ptr() as *const c_char,
        PROC_MOUNT.as_ptr() as *const c_char,
        PROC.as_ptr() as *const c_char,
        (libc::MS_NOSUID | libc::MS_NODEV | libc::MS_NOEXEC) as c_ulong,
        std::ptr::null(),
    );
    if rc != 0 {
        return _fail(args.write_fd, b"proc mount failed");
    }
    // 4. Close the error pipe (the manager's bounded read ends at EOF
    //    — success), then exec the launcher. On success this never
    //    returns; on failure close again (harmless) and report.
    let _ = libc::close(args.write_fd);
    let argv = std::slice::from_raw_parts(args.argv, args.argc);
    libc::execv(argv[0], args.argv as *const *const c_char);
    // execv failed — report the errno to stderr, then exit 126 (the
    // manager's wait decodes it as the container's status).
    let err = last_errno();
    let _ = libc::write(
        2,
        b"nyrqis launch child: execv failed: errno=".as_ptr() as *const c_void,
        b"nyrqis launch child: execv failed: errno=".len(),
    );
    let mut digits = [0u8; 8];
    let mut nd = 0;
    let mut e = err as u32;
    while e > 0 {
        digits[nd] = b'0' + (e % 10) as u8;
        nd += 1;
        e /= 10;
    }
    if nd == 0 {
        digits[0] = b'0';
        nd = 1;
    }
    while nd > 0 {
        let _ = libc::write(
            2,
            digits[nd - 1..nd].as_ptr() as *const c_void,
            1,
        );
        nd -= 1;
    }
    let _ = libc::write(2, b"\n".as_ptr() as *const c_void, 1);
    126
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0200);
    }

    #[test]
    fn ffi_symbols_exist() {
        // The loader requires these entry points; presence is part of the
        // ABI contract (rust/syscalls/README.md).
        let _ = nyrqis_syscalls_version;
        let _ = nyrqis_syscalls_sethostname;
        let _ = nyrqis_syscalls_prctl;
        let _ = nyrqis_syscalls_unshare;
        let _ = nyrqis_syscalls_mount;
        let _ = nyrqis_syscalls_mount_proc;
        let _ = nyrqis_syscalls_clone;
        let _ = nyrqis_syscalls_launch_child;
    }

    #[test]
    fn err_internal_is_outside_errno_range() {
        // The contract maps -errno -> OSError; ERR_INTERNAL must never
        // collide with a real errno (1..=4095), else an internal failure
        // would be misreported (e.g. -4 = EINTR).
        assert!(!(1..=4095).contains(&(-ERR_INTERNAL)));
        assert_eq!(ERR_INVALID_ARGS, -libc::EINVAL);
        assert_eq!(ERR_NOT_IMPLEMENTED, -libc::ENOSYS);
    }

    #[test]
    fn prctl_get_name_roundtrip() {
        // PR_GET_NAME (16) writes the calling thread's name into the
        // caller buffer — a safe, unprivileged, deterministic read that
        // exercises the full wrapper path (variadic libc call + errno
        // contract). Any name is acceptable; the call must succeed and
        // must actually WRITE it (15 is PR_SET_NAME: passing an empty
        // buffer succeeds and writes nothing, which would make the
        // "contains a NUL" assertion pass trivially).
        let mut buf = [0i8; 16];
        let rc = unsafe { nyrqis_syscalls_prctl(16, buf.as_mut_ptr() as u64, 0, 0, 0) };
        assert_eq!(rc, 0);
        // The thread name is a NUL-terminated string; the first byte
        // must be non-NUL, proving the kernel wrote into the buffer.
        assert_ne!(buf[0], 0, "PR_GET_NAME wrote nothing");
        // And the rest is NUL-terminated.
        assert!(buf.iter().any(|&b| b == 0));
    }

    #[test]
    fn sethostname_null_pointer_is_einval() {
        // The contract guards the buffer before touching it.
        assert_eq!(unsafe { nyrqis_syscalls_sethostname(std::ptr::null(), 0) }, ERR_INVALID_ARGS);
    }

    #[test]
    fn unshare_invalid_flags_returns_negative_errno() {
        // unshare(0xFFFF_FFFF) must fail with EINVAL (no valid flag
        // combo spans that bit pattern), proving the -errno mapping.
        let rc = unsafe { nyrqis_syscalls_unshare(0xFFFF_FFFF) };
        assert!(rc < 0, "expected a negative errno, got {rc}");
        assert_eq!(rc, -libc::EINVAL);
    }

    #[test]
    fn mount_null_target_is_einval() {
        // The contract guards the arguments before touching the kernel.
        assert_eq!(
            unsafe {
                nyrqis_syscalls_mount(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                )
            },
            ERR_INVALID_ARGS
        );
    }

    #[test]
    fn mount_missing_target_returns_negative_errno() {
        // A mount whose target directory does not exist fails with a
        // deterministic negative errno (ENOENT or EPERM depending on
        // capability checks) — proving the wrapper reaches the kernel
        // and returns the -errno convention without side effects.
        const SOURCE: &[u8] = b"proc\0";
        const TARGET: &[u8] = b"/nonexistent-nyrqis-conformance\0";
        const FSTYPE: &[u8] = b"proc\0";
        let rc = unsafe {
            nyrqis_syscalls_mount(
                SOURCE.as_ptr() as *const c_char,
                TARGET.as_ptr() as *const c_char,
                FSTYPE.as_ptr() as *const c_char,
                0,
                std::ptr::null(),
            )
        };
        assert!(rc < 0, "expected a negative errno, got {rc}");
        assert!(
            rc == -libc::ENOENT || rc == -libc::EPERM || rc == -libc::EACCES,
            "unexpected errno {rc}"
        );
    }

    #[test]
    fn mount_proc_returns_negative_errno_when_unprivileged() {
        // mount_proc on a host where the caller lacks CAP_SYS_ADMIN must
        // fail with -EPERM and never crash (the CI runner is unprivileged;
        // as root the call fails differently, so accept any negative
        // errno). This pins the no-arg post-fork entry point.
        let rc = unsafe { nyrqis_syscalls_mount_proc() };
        assert!(rc < 0, "expected a negative errno, got {rc}");
    }

    #[test]
    fn clone_forks_and_runs_child_entry() {
        // Fork semantics: the child runs the entry (writing through the
        // arg) and exits with its return; the parent gets the pid and
        // reaps the exact status. SIGCHLD in the low byte (the launcher
        // passes it too) makes the child a normal zombie — a zero
        // signal byte would auto-release it and waitpid would ECHILD.
        extern "C" fn entry(arg: *const c_void) -> i32 {
            let fd = arg as usize as c_int;
            let msg = b"hi\0";
            let n = unsafe {
                libc::write(fd, msg.as_ptr() as *const c_void, 3)
            };
            if n == 3 {
                7 // the child's exit code
            } else {
                8
            }
        }
        let mut fds = [0i32; 2];
        assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
        let pid = unsafe {
            nyrqis_syscalls_clone(
                libc::SIGCHLD as u64,
                Some(entry),
                fds[1] as usize as *const c_void,
            )
        };
        assert!(pid > 0, "expected a child pid, got {pid}");
        unsafe { libc::close(fds[1]) };
        let mut buf = [0u8; 4];
        let n = unsafe { libc::read(fds[0], buf.as_mut_ptr() as *mut c_void, 4) };
        assert_eq!(n, 3, "the child never wrote through the arg");
        assert_eq!(&buf[..3], b"hi\0");
        let mut status = 0;
        let wpid = unsafe { libc::waitpid(pid, &mut status, 0) };
        assert_eq!(wpid, pid);
        assert!(libc::WIFEXITED(status));
        assert_eq!(libc::WEXITSTATUS(status), 7);
        unsafe { libc::close(fds[0]) };
    }

    #[test]
    fn clone_rejects_clone_vm() {
        // CLONE_VM shares the address space — the child entry's stack
        // frame (on the parent's stack) would dangle. Rejected by
        // contract.
        extern "C" fn entry(_arg: *const c_void) -> i32 {
            0
        }
        assert_eq!(
            unsafe {
                nyrqis_syscalls_clone(
                    libc::CLONE_VM as u64,
                    Some(entry),
                    std::ptr::null(),
                )
            },
            ERR_INVALID_ARGS
        );
    }

    #[test]
    fn clone_rejects_null_entry_or_arg() {
        // Both the entry and the arg must be non-null (the contract
        // guards before touching the kernel).
        assert_eq!(
            unsafe {
                nyrqis_syscalls_clone(0, None, 1 as *const c_void)
            },
            ERR_INVALID_ARGS
        );
        extern "C" fn entry(_arg: *const c_void) -> i32 {
            0
        }
        assert_eq!(
            unsafe {
                nyrqis_syscalls_clone(0, Some(entry), std::ptr::null())
            },
            ERR_INVALID_ARGS
        );
    }

    #[test]
    fn launch_child_null_arg_is_safe() {
        // The entry guards its argument before touching it (no crash
        // on a null LaunchArgs pointer).
        assert_eq!(
            unsafe { nyrqis_syscalls_launch_child(std::ptr::null()) },
            125
        );
    }

    #[test]
    fn map_line_formats_root_maps() {
        // The uid/gid map line the entry writes: "0 <id> 1\n".
        let mut buf = [0u8; 32];
        assert_eq!(_map_line(1000, &mut buf), b"0 1000 1\n");
        assert_eq!(_map_line(0, &mut buf), b"0 0 1\n");
        assert_eq!(_map_line(4294967294, &mut buf), b"0 4294967294 1\n");
    }
}
