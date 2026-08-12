# Rust seccomp module — FFI boundary contract and conformance plan

**Status: scaffold.** This module is the first ADR-0020 migration. No
Rust toolchain exists on the dev host yet, so the crate is a designed,
unbuilt scaffold. The pure-Python implementation
(`backend/seccomp.py`, 970 lines, fully tested) remains the only
shipped implementation until the Rust module passes the conformance
suite *through the FFI*.

## Why this module first

ADR-0020 priority #1: the seccomp policy compiler is security-critical
(the BPF program it emits runs in the kernel and is the container's
enforcement surface) and it is already a well-bounded pure function
(policy → instruction list), which makes it the natural first FFI
module. The migration's evidence gate is the security posture (a
memory-safe compiler eliminates an entire class of memory-corruption
bugs in the trust boundary), not a performance claim.

## FFI surface (the ABI rule)

The boundary **MUST** be a versioned FFI surface per ABI-001 and
ADR-0020's ABI rule: no shared mutable state, no pointers into Python
objects; only owned data crossing stable, versioned entry points. All
entry points take and return plain data:

- `nyrqis_seccomp_version() -> uint32` — module ABI version
  (semver-major * 10000 + minor * 100 + patch).
- `nyrqis_seccomp_build_program(policy_json: *const u8, policy_len: usize, arch: u32, out: *mut *mut u8, out_len: *mut usize) -> i32` — compile a policy (JSON, the serialized form of `build_policy`'s output) into the classic-BPF instruction list. Returns 0 on success, a negative `NyrqisErr` code on failure; `out` receives an allocated byte buffer (4 bytes per instruction) the caller frees via `nyrqis_seccomp_free`.
- `nyrqis_seccomp_validate_program(program: *const u8, program_len: usize) -> i32` — validate jump offsets/bounds (mirror of `seccomp.validate_program`).
- `nyrqis_seccomp_simulate(program: *const u8, program_len: usize, syscall_nr: u32, audit_arch: u32, args: *const u64, args_len: usize) -> i64` — evaluate the program against a syscall (mirror of `seccomp.simulate`); returns the `SECCOMP_RET_*` verdict or a negative error.
- `nyrqis_seccomp_free(ptr: *mut u8)` — free a buffer returned by this module.

Error codes (`NyrqisErr`): `-1` policy parse, `-2` unsupported arch,
`-3` invalid program, `-4` internal. The Python side maps these back to
`PolicyError`/`ValueError` exactly as the pure-Python path does today.

Serialization: the policy JSON uses the same keys the Python
`build_policy`/`build_allowlist_policy` outputs use (capability set →
`{deny, deny_on_flags, allow, allow_when_flags_clear}` rules), so the
two implementations share one wire format and the conformance suite
feeds identical inputs to both.

## Python-side integration (design)

`backend/seccomp.py` gains a loader at the top of its hot functions:

1. Try to load `libnyrqis_seccomp.so` (searched: env override
   `NYRQIS_RUST_LIB`, then the crate's `target/release/`, then
   `LD_LIBRARY_PATH`).
2. Check `nyrqis_seccomp_version()` ≥ the minimum the Python side
   requires; on mismatch, fall back.
3. Call the FFI; map errors; free buffers.
4. On ANY load/call failure: log once and fall back to the existing
   pure-Python path — the Python implementation remains the correctness
   floor and the tests keep passing unchanged either way.

The conformance rule: the Rust path is only "shipped" when the full
Python test suite runs green with the Rust loader *forced* (env
`NYRQIS_RUST_FORCE=1`), i.e. every seccomp test drives the Rust module
through the FFI.

## Conformance test plan (runs when a toolchain exists)

0. **Prerequisite (Python side):** implement the policy → JSON
   serializer (`build_policy`/`build_allowlist_policy` currently
   return policy objects, not JSON) and round-trip it in the existing
   test suite. The FFI contract above depends on it.
1. `cargo build --release` → produces `libnyrqis_seccomp.so`.
2. Copy the cdylib to a known path; run the existing
   `python3 test_backend.py TestSeccompEnforcement` +
   `TestDefaultDenyAllowlist` (≈25 tests) with the loader active.
3. Run the same with `NYRQIS_RUST_FORCE=1` — every seccomp test must
   pass through the FFI (this is the definition of "ported").
4. Differential fuzz (short): random policies + random
   syscall/args, assert Python path and Rust path return identical
   verdicts (seeded, deterministic, recorded in the crate's tests).
5. Record the result in `IMPLEMENTATION_STATUS.md`; only then does the
   module leave "scaffold" status.

## Not in scope (first pass)

- The live `prctl(PR_SET_SECCOMP, …)` install path (stays Python for
  now; it is a thin syscall wrapper).
- The syscall-number tables (`_SYSCALLS`): ported verbatim and kept
  in lockstep with `backend/seccomp.py`'s hand-maintained tables (no
generator exists; a lockstep check belongs in the conformance suite).
