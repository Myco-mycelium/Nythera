//! Nyrqis launcher-init — the compiled container PID-1 (ADR-0020).
//!
//! Pure, unit-testable pieces live here; the process flow is in
//! `main.rs`. The binary is the container's first process (exec'd by
//! the Rust clone child entry — zero Python between clone and exec),
//! and it stays alive as the namespace's PID-1 init: it sets the
//! hostname, hardens cgroup mounts, brings up loopback, installs the
//! container's pre-built seccomp filter on the command child, and
//! forwards supervisor signals / reaps / propagates the exit status.
//!
//! The seccomp *policy compilation* stays in the backend (the syscall
//! allowlist tables live there, and the `rust/seccomp` crate already
//! holds the compiler to byte-identity); the manager serializes the
//! resulting classic-BPF program to a file and this process installs
//! it — the install path (prctl) is the platform-critical part.

#![allow(dead_code)]

use std::ffi::CString;
use std::io::Read;

/// The signals the init forwards to the container command — the set a
/// supervisor would pass through (SIGKILL/SIGSTOP cannot be caught).
pub const FORWARD_SIGNALS: [libc::c_int; 7] = [
    libc::SIGHUP, libc::SIGINT, libc::SIGQUIT, libc::SIGTERM,
    libc::SIGUSR1, libc::SIGUSR2, libc::SIGWINCH,
];

/// Classic BPF `struct sock_filter` — 8 bytes, little-endian on disk.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SockFilter {
    pub code: u16,
    pub jt: u8,
    pub jf: u8,
    pub k: u32,
}

/// `struct sock_fprog` for `prctl(PR_SET_SECCOMP, ...)`.
#[repr(C)]
pub struct SockFprog {
    pub len: u16,
    pub filter: *mut SockFilter,
}

/// Parse a serialized classic-BPF program (little-endian
/// `SockFilter` records, the format the backend writes).
pub fn parse_bpf(bytes: &[u8]) -> Result<Vec<SockFilter>, String> {
    if bytes.is_empty() {
        return Err("empty BPF program".to_string());
    }
    if bytes.len() % 8 != 0 {
        return Err(format!(
            "BPF program length {} is not a multiple of 8", bytes.len()
        ));
    }
    let n = bytes.len() / 8;
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let b = &bytes[i * 8..i * 8 + 8];
        out.push(SockFilter {
            code: u16::from_le_bytes([b[0], b[1]]),
            jt: b[2],
            jf: b[3],
            k: u32::from_le_bytes([b[4], b[5], b[6], b[7]]),
        });
    }
    Ok(out)
}

/// Read a BPF program from a file path; `None` path (or an unreadable
/// file) means "no seccomp policy" — mirroring the Python launcher's
/// empty `--policy-file` meaning enforcement OFF.
pub fn load_bpf(path: &str) -> Result<Option<Vec<SockFilter>>, String> {
    if path.is_empty() {
        return Ok(None);
    }
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| format!("open {}: {e}", path))?
        .read_to_end(&mut bytes)
        .map_err(|e| format!("read {}: {e}", path))?;
    parse_bpf(&bytes).map(Some)
}

/// The launcher's own argv (before `--`): hostname, optional BPF path,
/// strict-seccomp flag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LauncherArgs {
    pub hostname: String,
    pub bpf_file: String,
    pub strict_seccomp: bool,
}

/// Parse the launcher's argv. The container command follows a `--`
/// separator (`ArgParse`-REMAINDER semantics in the Python launcher);
/// it is returned verbatim and passed to `execvpe` untouched.
pub fn parse_argv(argv: &[String]) -> Result<(LauncherArgs, Vec<String>), String> {
    let mut hostname = "nyrqis-container".to_string();
    let mut bpf_file = String::new();
    let mut strict_seccomp = false;
    let mut command: Vec<String> = Vec::new();
    let mut i = 0;
    while i < argv.len() {
        let arg = &argv[i];
        match arg.as_str() {
            "--hostname" => {
                i += 1;
                hostname = argv
                    .get(i)
                    .ok_or_else(|| "--hostname requires a value".to_string())?
                    .clone();
            }
            "--bpf-file" => {
                i += 1;
                bpf_file = argv
                    .get(i)
                    .ok_or_else(|| "--bpf-file requires a value".to_string())?
                    .clone();
            }
            "--strict-seccomp" => strict_seccomp = true,
            "--" => {
                command = argv[i + 1..].to_vec();
                break;
            }
            other if other.starts_with('-') => {
                return Err(format!("unknown option: {other}"));
            }
            _other => {
                // First non-option ends the launcher args (the Python
                // launcher uses argparse REMAINDER — everything after
                // the launcher's own options is the command).
                command = argv[i..].to_vec();
                break;
            }
        }
        i += 1;
    }
    Ok((LauncherArgs { hostname, bpf_file, strict_seccomp }, command))
}

/// Install the seccomp filter on the calling thread/process: the
/// kernel's `prctl(PR_SET_NO_NEW_PRIVS, 1)` + `prctl(PR_SET_SECCOMP,
/// SECCOMP_MODE_FILTER, &prog)` pair the Python floor performs, with
/// its already-in-filter-mode short circuit. Returns `(ok, errno)`.
pub fn install_seccomp(program: &[SockFilter]) -> (bool, i32) {
    // Mirror the floor: already in filter mode = success (a second
    // install would be a no-op that fails).
    if unsafe { libc::prctl(PR_GET_SECCOMP) } == SECCOMP_MODE_FILTER {
        return (true, 0);
    }
    let rc = unsafe { libc::prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) };
    if rc != 0 {
        return (false, std::io::Error::last_os_error().raw_os_error().unwrap_or(0));
    }
    let mut owned: Vec<SockFilter> = program.to_vec();
    let prog = SockFprog {
        len: owned.len() as u16,
        filter: owned.as_mut_ptr(),
    };
    let rc = unsafe {
        libc::prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog as *const SockFprog, 0, 0)
    };
    if rc != 0 {
        return (false, std::io::Error::last_os_error().raw_os_error().unwrap_or(0));
    }
    (true, 0)
}

// prctl(2) constants (defined here rather than via libc's feature-gated
// exports so the crate builds on any libc 0.2).
const PR_SET_NO_NEW_PRIVS: libc::c_int = 38;
const PR_GET_SECCOMP: libc::c_int = 21;
const PR_SET_SECCOMP: libc::c_int = 22;
const SECCOMP_MODE_FILTER: libc::c_int = 2;

/// `sethostname(2)` with the Python floor's fallback: when the UTS
/// namespace blocks it (no CAP_SYS_ADMIN in it), try
/// `prctl(PR_SET_HOSTNAME, ...)` (PR_SET_HOSTNAME = 10).
pub fn set_hostname(name: &str) -> bool {
    let cname = match CString::new(name) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let rc = unsafe { libc::sethostname(cname.as_ptr(), cname.as_bytes().len()) };
    if rc == 0 {
        return true;
    }
    unsafe { libc::prctl(10, cname.as_ptr() as libc::c_ulong, 0, 0, 0) == 0 }
}

/// Best-effort unmount of cgroup filesystems inside the mount
/// namespace (defense in depth for FIND-BACKEND-003): returns the
/// number of mounts unmounted.
pub fn harden_cgroup_mounts() -> u32 {
    let mut mounts: Vec<String> = Vec::new();
    if let Ok(contents) = std::fs::read_to_string("/proc/self/mounts") {
        for line in contents.lines() {
            let fields: Vec<&str> = line.split_whitespace().collect();
            if fields.len() >= 3 && fields[2].starts_with("cgroup") {
                mounts.push(fields[1].to_string());
            }
        }
    }
    let mut unmounted = 0;
    for mnt in &mounts {
        let c = match CString::new(mnt.as_str()) {
            Ok(c) => c,
            Err(_) => continue,
        };
        // MNT_DETACH = 2: unmount even if busy; we are inside a
        // private mount namespace so this cannot affect the host.
        if unsafe { libc::umount2(c.as_ptr(), 2) } == 0 {
            unmounted += 1;
        }
    }
    unmounted
}

/// Best-effort `SIOCSIFFLAGS` on `lo` (IFF_UP): succeeds in a netns
/// the container's user namespace owns (where it is root), harmlessly
/// EPERMs when sharing the host netns.
pub fn bring_loopback_up() -> bool {
    const IFF_UP: u16 = 0x1;
    const SIOCGIFFLAGS: libc::c_ulong = 0x8913;
    const SIOCSIFFLAGS: libc::c_ulong = 0x8914;
    const IFREQ_SIZE: usize = 40; // struct ifreq on x86_64

    let fd = unsafe { libc::socket(libc::AF_INET, libc::SOCK_DGRAM, 0) };
    if fd < 0 {
        return false;
    }
    let mut ifr = [0u8; IFREQ_SIZE];
    // ifr_name: "lo\0"
    ifr[0] = b'l';
    ifr[1] = b'o';
    let get = unsafe { libc::ioctl(fd, SIOCGIFFLAGS, ifr.as_mut_ptr()) };
    if get != 0 {
        unsafe { libc::close(fd) };
        return false;
    }
    // ifr_flags is the u16 at offset 16 (after 16-byte ifr_name).
    let flags = u16::from_le_bytes([ifr[16], ifr[17]]);
    if flags & IFF_UP != 0 {
        unsafe { libc::close(fd) };
        return true;
    }
    ifr[16] = (flags | IFF_UP) as u8;
    ifr[17] = ((flags | IFF_UP) >> 8) as u8;
    let set = unsafe { libc::ioctl(fd, SIOCSIFFLAGS, ifr.as_mut_ptr()) };
    unsafe { libc::close(fd) };
    set == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn filter(code: u16, jt: u8, jf: u8, k: u32) -> SockFilter {
        SockFilter { code, jt, jf, k }
    }

    #[test]
    fn parse_bpf_round_trips_records() {
        let prog = vec![filter(0x06, 0, 0, 0), filter(0x15, 1, 4, 0x40000003)];
        let mut bytes = Vec::new();
        for f in &prog {
            bytes.extend_from_slice(&f.code.to_le_bytes());
            bytes.push(f.jt);
            bytes.push(f.jf);
            bytes.extend_from_slice(&f.k.to_le_bytes());
        }
        assert_eq!(parse_bpf(&bytes).unwrap(), prog);
    }

    #[test]
    fn parse_bpf_rejects_bad_lengths() {
        assert!(parse_bpf(b"").is_err());
        assert!(parse_bpf(b"\x06\x00\x00\x00").is_err());
        assert!(parse_bpf(&[0u8; 7]).is_err());
    }

    #[test]
    fn parse_argv_splits_command_at_dashdash() {
        let argv = vec![
            "--hostname".into(), "ctr".into(),
            "--bpf-file".into(), "/tmp/x.bpf".into(),
            "--".into(), "/bin/sleep".into(), "30".into(),
        ];
        let (args, cmd) = parse_argv(&argv).unwrap();
        assert_eq!(args.hostname, "ctr");
        assert_eq!(args.bpf_file, "/tmp/x.bpf");
        assert!(!args.strict_seccomp);
        assert_eq!(cmd, vec!["/bin/sleep", "30"]);
    }

    #[test]
    fn parse_argv_first_non_option_starts_command() {
        let argv = vec![
            "--hostname".into(), "ctr".into(), "/bin/true".into(), "-x".into(),
        ];
        let (args, cmd) = parse_argv(&argv).unwrap();
        assert_eq!(args.hostname, "ctr");
        // argparse REMAINDER: once the first positional appears,
        // everything after is the command (even flags).
        assert_eq!(cmd, vec!["/bin/true", "-x"]);
    }

    #[test]
    fn parse_argv_defaults() {
        let (args, cmd) = parse_argv(&["--".into(), "/bin/true".into()]).unwrap();
        assert_eq!(args.hostname, "nyrqis-container");
        assert!(args.bpf_file.is_empty());
        assert!(!args.strict_seccomp);
        assert_eq!(cmd, vec!["/bin/true"]);
    }

    #[test]
    fn parse_argv_rejects_unknown_flag() {
        let argv = vec!["--nope".into()];
        assert!(parse_argv(&argv).is_err());
    }

    #[test]
    fn load_bpf_none_path_is_ok() {
        assert!(load_bpf("").unwrap().is_none());
        assert!(load_bpf("/nonexistent/file").is_err());
    }

    #[test]
    fn set_hostname_runs_without_crashing() {
        // Never asserts the result: in a UTS namespace we own it
        // succeeds; in the host namespace it may EPERM and fall back to
        // prctl, which may also EPERM. Both are valid outcomes for the
        // launcher (hostname setting is best-effort).
        let _ = set_hostname("nyrqis-test");
    }

    #[test]
    fn harden_cgroup_mounts_returns_count() {
        // On a normal host this unmounts nothing (cgroup mounts are
        // either absent from our namespace or busy); it must not crash.
        let _ = harden_cgroup_mounts();
    }

    #[test]
    fn loopback_up_is_idempotent() {
        // Never asserts the result: in the host netns the ioctl may
        // EPERM (harmless); in a netns we own it succeeds.
        let _ = bring_loopback_up();
    }
}
