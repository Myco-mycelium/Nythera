# Rust seccomp module — FFI boundary contract and conformance plan

**Status: scaffold — Python-side conformance groundwork + FFI loader
DONE, Rust implementation BLOCKED on a toolchain.** As of 2026-08-12
the dev host has no Rust toolchain and rustup's toolchain download does
not complete in reasonable time (several attempts). The Python side of
the conformance plan — the wire format and the FFI loader — is
implemented and verified (below); the Rust crate itself remains a
designed, unbuilt scaffold. The pure-Python implementation
(`backend/seccomp.py`, fully tested) remains the only shipped
implementation until the Rust module passes the conformance suite
*through the FFI* (`.github/workflows/ci.yml` runs a non-blocking
conformance job that forces the full Python suite through the FFI on
every push and turns green automatically once the port lands).

### Conformance groundwork done (verified on the Python side)

- `SeccompPolicy.to_json()` / `policy_from_json()` — the policy wire
  format, round-trip tested for both postures and both architectures
  (`test_policy_json_roundtrip`).
- The round-trip test **found and fixed two real aarch64 syscall-table
  bugs** (verified against `/usr/include/asm-generic/unistd.h`):
  `readlink: 76` (no readlink on arm64; 76 is splice) and
  `faccessat: 49` (should be 48; `access: 48` doesn't exist on arm64).
  The collisions silently aliased syscalls in compiled filters and
  made the wire format ambiguous.
- `test_syscall_tables_have_unique_numbers` guards both tables against
  regressions (numbers are the FFI wire vocabulary).
- **FFI loader implemented** in `backend/seccomp.py`:
  `_load_rust_backend()` (search order `$NYRQIS_RUST_LIB` → the crate's
  `target/release/` → bare name; ABI version check against
  `MIN_RUST_ABI_VERSION`), wired into `build_program`,
  `validate_program`, and `simulate`. On ANY load or call failure it
  logs once and falls back to the pure-Python path; `NYRQIS_RUST_FORCE=1`
  turns failures into `PolicyError` (the conformance gate). The wire
  format for compiled programs is `struct sock_filter` — 8 bytes per
  instruction (u16 code, u8 jt, u8 jf, u32 k), the exact kernel layout;
  `_program_to_rust_bytes` / `_program_from_rust_bytes` encode/decode it
  and are pinned by `TestRustFfILoader` (`test_wire_format_is_…`).

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
- `nyrqis_seccomp_build_program(policy_json: *const u8, policy_len: usize, arch: u32, out: *mut *mut u8, out_len: *mut usize) -> i32` — compile a policy (JSON, the serialized form of `build_policy`'s output) into the classic-BPF instruction list. Returns 0 on success, a negative `NyrqisErr` code on failure; `out` receives an allocated byte buffer — **8 bytes per instruction, `struct sock_filter` layout (u16 code, u8 jt, u8 jf, u32 k), the same layout the kernel consumes** — the caller frees via `nyrqis_seccomp_free`.
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

0. **Prerequisite (Python side):** policy → JSON serializer + round-trip
   — **DONE (2026-08-12)**: `to_json()`/`policy_from_json()` shipped and
   tested; aarch64 table bugs found by it fixed.
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
