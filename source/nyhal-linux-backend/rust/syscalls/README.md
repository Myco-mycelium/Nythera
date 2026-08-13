# Rust syscalls module — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-13 (`sethostname` / `prctl` / `unshare`);
`clone` deliberately deferred.** The wrappers replace the Python
backend's `ctypes` calls (`sethostname(2)` and `prctl(2)` in
`backend/launcher.py`) and, in a later pass, the `unshare(1)` subprocess
in `backend/container.py` (the direct-syscall launcher transition,
`docs/implementation_plan.md` §4.1). CI builds and unit-tests the crate
on every push; the dev host still has no Rust toolchain, so **CI is the
compiler and the test runner for this crate**.

The Python side of the conformance plan is **done**: the shared loader
`backend/rust_syscalls.py` (search order `$NYRQIS_RUST_LIB` → the
crate's `target/release/` → bare name, ABI version check, `ctypes`
fallbacks, `NYRQIS_RUST_FORCE=1`) is wired into `launcher.py`'s
`set_hostname` (FIND-BACKEND-004: the hostname is an argv entry, never
shell-interpolated). The container launch path still uses `unshare(1)`;
switching it to the direct `unshare(2)`/`clone` primitives is the next
step and needs the child-entry-point design (below).

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
  the hostname is an argv entry, never shell-interpolated). `name` is a
  caller-owned buffer of `len` bytes (no NUL requirement —
  `sethostname(2)` uses `len`). Returns 0 or negative errno.
- `nyrqis_syscalls_prctl(option: u32, a2..a5: u64) -> i32` — mirror of
  `launcher.py`'s `ctypes` `prctl` (PR_SET_HOSTNAME fallback today;
  PR_SET_NO_NEW_PRIVS / PR_SET_SECCOMP for the seccomp install path in
  a later pass). Returns 0 or negative errno.
- `nyrqis_syscalls_unshare(flags: u64) -> i32` — replaces
  `unshare(1)` in `backend/container.py` (the plan's direct-syscall
  transition). `flags` is the `CLONE_NEW*` bit mask. Returns 0 or
  negative errno.
- `nyrqis_syscalls_clone(flags: u64, stack: *mut u8, stack_size: usize,
  ptid: *mut i32, ctid: *mut i32, tls: u64) -> i64` — **NOT
  IMPLEMENTED** (returns `ERR_INTERNAL`): a direct `clone(2)` wrapper
  must specify what the child runs on its new stack, and the only safe
  answer in this FFI design is a Rust child entry point — a Python
  callback is not callable from a raw child (no GIL, no interpreter).
  The direct-syscall launcher transition must define that entry point
  before this surface is exercised; until then the container launch
  keeps using `unshare(1)`.

Error codes: negative errno values passed through unchanged from the
kernel (`-22` EINVAL, `-38` ENOSYS). `-4096` is the module's internal
error code, deliberately **outside** the errno range (1..=4095) so the
`-errno → OSError` mapping can never misreport it as a real kernel
error (an in-range code like `-4` would surface as EINTR). The Python
side maps negative errno to `OSError` exactly as the `ctypes` path does
today, and treats `-4096` as an internal failure.

## Python-side integration (implemented)

`backend/rust_syscalls.py` is the shared loader used by
`backend/launcher.py` (and, in the direct-syscall transition,
`backend/container.py`). It mirrors the seccomp loader's contract:
search order, ABI-version check, cache, and on ANY load or call failure
it logs once and falls back to the pure-ctypes path (the correctness
floor). `NYRQIS_RUST_FORCE=1` turns routing failures into errors (the
conformance gate); a clean negative errno returned by a working FFI
call is the kernel's answer and passes through — only routing failures
(module missing, call raising) raise under force. `set_hostname()`
routes `sethostname(2)` through the FFI when loaded and falls back to
`prctl(PR_SET_HOSTNAME)` exactly as before; `unshare(flags)` raises
`OSError` on failure.

## Conformance test plan (status)

0. **Prerequisite (Python side):** the shared loader + forced-mode flag
   — **DONE (2026-08-13)**: `backend/rust_syscalls.py` shipped and
   tested (`TestRustSyscallsLoader`), wired into `launcher.set_hostname`.
1. `cargo build --release` → produces `libnyrqis_syscalls.so`; `cargo
   test` green (ABI version, symbol presence, errno contract,
   `prctl(PR_GET_NAME)` round-trip) — **DONE, CI-verified** (the crate
   builds and its tests pass in the `rust-syscalls` CI job on every
   push).
2. Run the existing launcher/container tests with the loader active —
   **DONE, CI-verified** (the backend suite passes with the loader in
   the loop; the crate is absent on the dev host so the ctypes fallback
   is exercised there).
3. Run the same with `NYRQIS_RUST_FORCE=1` once the crate is present —
   the definition of "ported" for the hostname path. **PENDING a host
   with the crate built** (the conformance harness for syscalls is
   lightweight today because the launch path still uses `unshare(1)`).
4. Live checks (host-dependent, recorded not gated): a container launch
   using the Rust `unshare` + `sethostname` wrappers still isolates PID/
   mount/UTS namespaces and sets the hostname without a shell —
   **PENDING the direct-syscall launcher transition.**
5. Record the result in `IMPLEMENTATION_STATUS.md` — **DONE
   (2026-08-13)**.

## Next step: the direct-syscall launcher transition

Replace `container.py`'s `unshare --user --map-root-user --pid
--mount-proc --fork --uts --mount --ipc --` subprocess with the direct
primitives: `unshare(CLONE_NEWUSER|…)` → write the child's
`/proc/self/{setgroups,uid_map,gid_map}` (the `--map-root-user`
equivalent) → `unshare(CLONE_NEWPID|CLONE_NEWNS|CLONE_NEWUTS|CLONE_NEWIPC)`
→ fork → remount `/proc` → exec the launcher. This requires the
`clone` child-entry-point design (above) and its own conformance pass;
it is deliberately NOT rushed into this round.
