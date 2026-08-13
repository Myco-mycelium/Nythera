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
//! **`clone` is deliberately NOT implemented** (returns `ERR_INTERNAL`):
//! a direct `clone(2)` wrapper must specify what the child runs on its
//! new stack, and the only safe answer in this FFI design is a Rust
//! child entry point — a Python callback is not callable from a raw
//! child (no GIL, no interpreter). The direct-syscall launcher uses
//! `fork(2)` (via the manager's `os.fork`) as its child-creation
//! primitive: `unshare(CLONE_NEWPID)` only affects the caller's
//! children, so a fork is required for the container's PID-1 anyway.
//! `clone` remains future work for a fully Rust-native child entry
//! point (no Python between fork and exec).

use libc::{c_char, c_int, c_ulong, c_void};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
/// 1.1.0 adds `nyrqis_syscalls_mount` / `nyrqis_syscalls_mount_proc`
/// (the direct-syscall launcher's procfs mount); the loader requires
/// this minimum so an older 1.0.x library is skipped before the new
/// symbols are resolved.
pub const ABI_VERSION: u32 = 0x0001_0100;

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

/// clone(3) wrapper returning a new child's PID (or 0 in the child).
/// **NOT IMPLEMENTED — deferred** (see module docs): the child-side
/// entry point is a design decision for the direct-syscall launcher
/// transition, so this returns `ERR_INTERNAL` rather than pretending.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_syscalls_clone(
    _flags: u64,
    _stack: *mut u8,
    _stack_size: usize,
    _ptid: *mut i32,
    _ctid: *mut i32,
    _tls: u64,
) -> i64 {
    ERR_INTERNAL as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0100);
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
        // PR_GET_NAME (15) writes the calling thread's name into the
        // caller buffer — a safe, unprivileged, deterministic read that
        // exercises the full wrapper path (variadic libc call + errno
        // contract). Any name is acceptable; the call must succeed.
        let mut buf = [0i8; 16];
        let rc = unsafe { nyrqis_syscalls_prctl(15, buf.as_mut_ptr() as u64, 0, 0, 0) };
        assert_eq!(rc, 0);
        // The buffer must contain at least a terminating NUL.
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
    fn clone_is_deferred() {
        assert_eq!(
            unsafe { nyrqis_syscalls_clone(0, std::ptr::null_mut(), 0, std::ptr::null_mut(), std::ptr::null_mut(), 0) },
            ERR_INTERNAL as i64
        );
    }
}
