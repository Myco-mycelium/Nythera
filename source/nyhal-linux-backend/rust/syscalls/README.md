# Rust syscalls module — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-13 (`sethostname` / `prctl` / `unshare` /
`mount` / `mount_proc`); `clone` deliberately deferred.** The wrappers
replace the Python backend's `ctypes` calls (`sethostname(2)` and
`prctl(2)` in `backend/launcher.py`) and the `unshare(1)` subprocess in
`backend/container.py`: the **direct-syscall launcher** of
`docs/implementation_plan.md` §4.1 landed the same day (see below).
CI builds and unit-tests the crate on every push; the dev host still has
no Rust toolchain, so **CI is the compiler and the test runner for this
crate**.

The Python side of the conformance plan is **done**: the shared loader
`backend/rust_syscalls.py` (search order `$NYRQIS_RUST_LIB` → the
crate's `target/release/` → bare name, ABI version check, `ctypes`
fallbacks, `NYRQIS_RUST_FORCE=1`) is wired into `launcher.py`'s
`set_hostname` (FIND-BACKEND-004: the hostname is an argv entry, never
shell-interpolated) and into `container.py`'s direct-syscall launcher
(`unshare` / `mount_proc`). `unshare(1)` is retained only as an opt-in
legacy path (`ContainerManager(use_direct_syscalls=False)`).

## Why this module second

ADR-0020 priority #2: `backend/container.py` used to shell out to
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

Entry points (ABI 1.1.0):

| Symbol | libc call | Notes |
| --- | --- | --- |
| `nyrqis_syscalls_version` | — | module ABI version (u32) |
| `nyrqis_syscalls_sethostname` | `sethostname(2)` | name + len; no NUL required |
| `nyrqis_syscalls_prctl` | `prctl(2)` | option + 4 args (u64 each) |
| `nyrqis_syscalls_unshare` | `unshare(2)` | CLONE_NEW* flag mask |
| `nyrqis_syscalls_mount` | `mount(2)` | source/target/fstype buffers, flags, data |
| `nyrqis_syscalls_mount_proc` | `mount(2)` | no-arg procfs mount (see below) |
| `nyrqis_syscalls_clone` | — | **deferred** (see below) |

## The direct-syscall launcher (§4.1) — how the container is launched

`unshare(2)` moves the *calling* process into the new namespaces, so
the manager must never call it directly. `ContainerManager._spawn_direct`
forks a **namespace-setup child** that performs the same sequence
`unshare(1)` used to, then forks the container's PID-1:

1. `unshare(CLONE_NEWUSER)` + write the root uid/gid maps (the
   `--map-root-user` equivalent). The caller's real uid/gid are captured
   **before** the unshare: inside the new, still-unmapped namespace
   `getuid()` reports 65534 (nobody), and the kernel refuses to map an
   id that is not the caller's own.
2. `unshare(CLONE_NEWNS | CLONE_NEWUTS | CLONE_NEWIPC)` — now permitted
   because the setup child holds full capabilities in the new user
   namespace.
3. `unshare(CLONE_NEWPID)` — this affects only the *next* fork, so the
   child forks again.
4. PID-1 mounts a hardened procfs (`mount_proc`, the `--mount-proc`
   equivalent: nosuid, nodev, noexec) and execs the launcher
   (`backend/launcher.py`), which sets the hostname, hardens cgroup
   mounts, installs the container's seccomp filter, and execs the
   container's command.

The setup child relays the container's PID to the manager through a
pipe, waits for it, and exits with its exit status (or dies by its
signal) — so the manager's `wait()` reaps the setup child and decodes
the container's status exactly as the old Popen path did. `mount_proc`
is a **no-argument** entry point on purpose: it is called between `fork`
and `exec`, where the child must not allocate or touch Python objects.

## `clone` — deliberately deferred

A direct `clone(2)` wrapper must specify what the child runs on its new
stack, and the only safe answer in this FFI design is a Rust child entry
point — a Python callback is not callable from a raw child (no GIL, no
interpreter). The direct-syscall launcher does not need it: `fork(2)`
(via the manager's `os.fork`) is the child-creation primitive here, and
`unshare(CLONE_NEWPID)` makes the forked grandchild the container's
PID-1. `clone` remains future work for a fully Rust-native child entry
point (no Python between fork and exec).

## Conformance

- The `rust-syscalls` CI job builds the crate and runs `cargo test`
  (ABI pin, -errno contract, null guards, symbol presence, mount
  errno behavior).
- The `rust-syscalls-conformance` CI job builds the crate and runs the
  syscalls-facing Python test classes
  (`TestRustSyscallsLoader`, `TestRustSyscallsConformance`,
  `TestDirectSyscallLaunch`, `TestLauncherSecurity`) with
  `NYRQIS_RUST_FORCE=1` and `NYRQIS_RUST_LIB` pointing at the built
  cdylib — a required, blocking job. It runs only those classes, not
  the full suite: forcing the syscalls lib would make the *separate*
  seccomp loader fail its own force check.
- Hosts without the crate fall back to the pure-`ctypes` floor, which
  the tests exercise the same way the pre-ADR-0020 code did.
