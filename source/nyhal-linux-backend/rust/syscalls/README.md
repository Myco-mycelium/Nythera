# Rust syscalls module — FFI boundary contract and conformance plan

**Status: scaffold — NOT YET BUILT.** As of 2026-08-13 the dev host has
no Rust toolchain (rustup's toolchain download does not complete in
reasonable time on this host). The crate is a designed, unbuilt scaffold
whose job is to record the FFI boundary contract for
`docs/implementation_plan.md` §4.1's direct-syscall transition
(ADR-0020 migration priority #2). CI (`.github/workflows/ci.yml`) builds
it on every push so it stays compiling even though nothing compiles
locally. The Python backend keeps using `unshare(1)` and `ctypes`
`sethostname(2)` until the conformance plan below passes.

## Why this module second

ADR-0020 priority #2: `backend/container.py` currently shells out to
`unshare(1)` and `backend/launcher.py` calls `sethostname(2)` via
`ctypes`. These are platform-critical execution paths (container launch
is part of the isolation boundary), so per the platform-boundary rule
they must not depend on the Python interpreter in their shipped form.
The syscall wrappers are thin, pure functions (flags in, errno out) —
the natural second FFI module after the seccomp policy compiler.

## FFI surface (the ABI rule)

The boundary **MUST** be a versioned FFI surface per ABI-001 and
ADR-0020's ABI rule: no shared mutable state, no pointers into Python
objects; only owned data crossing stable, versioned entry points. All
entry points take and return plain data. Return convention: **0 on
success, negative errno on failure** (the Linux syscall convention), so
the Python side maps `-errno` to `OSError(errno, os.strerror(errno))`
exactly as the `ctypes` path does today.

- `nyrqis_syscalls_version() -> uint32` — module ABI version
  (semver-major * 10000 + minor * 100 + patch).
- `nyrqis_syscalls_sethostname(name: *const u8, len: usize) -> i32` —
  mirror of `launcher.py`'s `ctypes` `sethostname(2)` (FIND-BACKEND-004:
  the hostname is an argv entry, never shell-interpolated). Returns 0 or
  negative errno.
- `nyrqis_syscalls_prctl(option: u32, a2..a5: u64) -> i32` — mirror of
  `launcher.py`'s `ctypes` `prctl` (PR_SET_HOSTNAME fallback today;
  PR_SET_NO_NEW_PRIVS / PR_SET_SECCOMP for the seccomp install path in
  a later pass). Returns 0 or negative errno.
- `nyrqis_syscalls_unshare(flags: u64) -> i32` — replaces
  `unshare(1)` in `backend/container.py`. Returns 0 or negative errno.
- `nyrqis_syscalls_clone(flags: u64, stack: *mut u8, stack_size: usize,
  ptid: *mut i32, ctid: *mut i32, tls: u64) -> i64` — direct clone
  wrapper; parent sees the child PID, the child sees 0; negative errno
  on failure. The `*mut i32` pointers are caller-owned scratch for
  parent/child TID reporting (NULL when unused).

Error codes: negative errno values passed through unchanged from the
kernel (`-22` EINVAL, `-38` ENOSYS). `-4096` is the module's internal
error code, deliberately **outside** the errno range (1..=4095) so the
`-errno → OSError` mapping can never misreport it as a real kernel
error (an in-range code like `-4` would surface as EINTR). The Python
side maps negative errno to `OSError` exactly as the `ctypes` path
does today, and treats `-4096` as an internal failure.

## Python-side integration (design)

`backend/launcher.py` and `backend/container.py` gain a shared loader
(analogous to `backend/seccomp.py`'s `_load_rust_backend`): search
`$NYRQIS_RUST_LIB` → the crate's `target/release/` → `LD_LIBRARY_PATH`,
ABI-version check, then route `sethostname`/`prctl`/`unshare`/`clone`
through the FFI, falling back to the current `ctypes`/`unshare(1)` path
on any failure. `NYRQIS_RUST_FORCE=1` turns failures into errors (the
conformance gate).

## Conformance test plan (runs when a toolchain exists)

0. **Prerequisite (Python side):** the loader + a forced-mode flag —
   **PENDING** (the seccomp loader pattern is the template).
1. `cargo build --release` → produces `libnyrqis_syscalls.so`; `cargo
   test` green (ABI version + symbol-presence tests).
2. Copy the cdylib to a known path; run the existing launcher/container
   tests with the loader active.
3. Run the same with `NYRQIS_RUST_FORCE=1` — every launcher/container
   test must pass through the FFI (this is the definition of "ported").
4. Live checks (host-dependent, recorded not gated): a container launch
   using the Rust `unshare` + `sethostname` wrappers still isolates PID/
   mount/UTS namespaces and sets the hostname without a shell.
5. Record the result in `IMPLEMENTATION_STATUS.md`; only then does the
   module leave "scaffold" status.

## Not in scope (first pass)

- The seccomp install path (`prctl(PR_SET_SECCOMP, …)`) — the seccomp
  crate owns policy compilation; this module provides the `prctl`
  primitive it installs through, but the install orchestration stays in
  the Python launcher until the seccomp module ships.
- `setns`, `mount`, cgroup wiring — future passes, same ABI pattern.
