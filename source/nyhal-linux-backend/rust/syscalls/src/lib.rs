//! Nyrqis direct syscall wrappers — ADR-0020 migration priority #2.
//!
//! **SCAFFOLD — NOT YET BUILT.** No syscall logic is implemented here;
//! every entry point returns `ERR_INTERNAL` so the surface is defined
//! without pretending to work. A Rust toolchain is required to build
//! this crate (none exists on the dev host as of 2026-08-13; CI builds
//! it on every push). Until the conformance plan in this directory's
//! README.md is executed, the Python backend keeps using
//! `unshare(1)`/`ctypes` and the tests in `test_backend.py` are the
//! correctness floor.
//!
//! This file intentionally contains only the FFI contract from the
//! README (ABI rule of ADR-0020): versioned, plain-data entry points,
//! no shared mutable state.

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns, following the Linux syscall
// convention of negative errno). ERR_INTERNAL is deliberately OUTSIDE
// the errno range (1..=4095): the contract maps -errno -> OSError, so
// an in-range internal code (e.g. -4 = EINTR) would be reported as a
// real kernel error.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL
pub const ERR_NOT_IMPLEMENTED: i32 = -38; // ENOSYS
pub const ERR_INTERNAL: i32 = -4096;

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_version() -> u32 {
    ABI_VERSION
}

/// Set the UTS hostname (replaces the Python backend's `ctypes`
/// `sethostname(2)`; the container hostname is an argv entry, never
/// shell-interpolated — FIND-BACKEND-004). Returns 0 or negative
/// errno. Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_sethostname(
    _name: *const u8,
    _len: usize,
) -> i32 {
    ERR_INTERNAL
}

/// prctl(2) wrapper (used for PR_SET_HOSTNAME fallback and, later,
/// PR_SET_NO_NEW_PRIVS/PR_SET_SECCOMP in the seccomp install path).
/// Returns 0 or negative errno. Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_prctl(
    _option: u32,
    _a2: u64,
    _a3: u64,
    _a4: u64,
    _a5: u64,
) -> i32 {
    ERR_INTERNAL
}

/// unshare(2) wrapper (replaces the `unshare(1)` subprocess in
/// `backend/container.py` — the plan's direct-syscall transition,
/// `docs/implementation_plan.md` §4.1). Returns 0 or negative errno.
/// Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_unshare(_flags: u64) -> i32 {
    ERR_INTERNAL
}

/// clone(3) wrapper returning a new child's PID (or 0 in the child).
/// The parent-side return is a pid_t; negative errno on failure.
/// Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_syscalls_clone(
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
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn ffi_symbols_exist() {
        // The loader requires these entry points; presence is part of the
        // ABI contract (rust/syscalls/README.md).
        let _ = nyrqis_syscalls_version;
        let _ = nyrqis_syscalls_sethostname;
        let _ = nyrqis_syscalls_prctl;
        let _ = nyrqis_syscalls_unshare;
        let _ = nyrqis_syscalls_clone;
    }
}
