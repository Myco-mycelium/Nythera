//! Nyrqis seccomp BPF policy compiler — ADR-0020 first migration.
//!
//! **SCAFFOLD — NOT YET BUILT.** No policy logic is implemented here;
//! every entry point returns `ERR_INTERNAL` so the surface is defined
//! without pretending to work. A Rust toolchain is required to build
//! this crate (none exists on the dev host as of 2026-08-12). Until
//! the conformance plan in this directory's README.md is executed, the
//! pure-Python `backend/seccomp.py` remains the only shipped
//! implementation and the tests in `test_backend.py` are the
//! correctness floor.
//!
//! This file intentionally contains only the FFI contract from the
//! README (ABI rule of ADR-0020): versioned, plain-data entry points,
//! no shared mutable state.

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns).
pub const ERR_POLICY_PARSE: i32 = -1;
pub const ERR_UNSUPPORTED_ARCH: i32 = -2;
pub const ERR_INVALID_PROGRAM: i32 = -3;
pub const ERR_INTERNAL: i32 = -4;

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_version() -> u32 {
    ABI_VERSION
}

/// Compile a serialized policy (JSON) into a classic-BPF instruction
/// list. Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_build_program(
    _policy: *const u8,
    _policy_len: usize,
    _arch: u32,
    _out: *mut *mut u8,
    _out_len: *mut usize,
) -> i32 {
    ERR_INTERNAL
}

/// Validate jump offsets/bounds of a classic-BPF program.
/// Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_validate_program(
    _program: *const u8,
    _program_len: usize,
) -> i32 {
    ERR_INTERNAL
}

/// Evaluate a program against a syscall; returns a SECCOMP_RET_*
/// verdict. Scaffold: returns ERR_INTERNAL.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_simulate(
    _program: *const u8,
    _program_len: usize,
    _syscall_nr: u32,
    _audit_arch: u32,
    _args: *const u64,
    _args_len: usize,
) -> i64 {
    ERR_INTERNAL as i64
}

/// Free a buffer returned by this module. Scaffold: no-op.
#[no_mangle]
pub extern "C" fn nyrqis_seccomp_free(_ptr: *mut u8) {}

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
        // ABI contract (rust/seccomp/README.md).
        let _ = nyrqis_seccomp_version;
        let _ = nyrqis_seccomp_build_program;
        let _ = nyrqis_seccomp_validate_program;
        let _ = nyrqis_seccomp_simulate;
        let _ = nyrqis_seccomp_free;
    }
}
