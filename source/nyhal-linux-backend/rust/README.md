# Rust Migration Workspace (ADR-0020)

First ADR-0020 migration landing pad for the Linux backend. Per
**ADR-0020** (Implementation Languages and the Platform Boundary), the
canonical language matrix assigns the platform layers — including
NyHAL (Rust-first), security services, storage, and networking — to
compiled languages, and the platform-boundary principle rules that
**platform-critical execution paths must not depend on the Python
interpreter**; Python remains the unrestricted language for tooling
above the boundary. Migrations land behind a versioned FFI boundary
(ABI-001); user-space tooling, tests, and benchmarks stay Python. See
`docs/reference/adr/ADR-0020-implementation-languages.md` for the full
matrix and the three normative rules.## Status

| Component | Status | Evidence gate (Migration rule) |
|-----------|--------|--------------------------------|
| `seccomp/` policy compiler (BPF generate/validate/simulate) | **IMPLEMENTED 2026-08-13** (ABI 1.1.0) | golden BPF programs byte-identical to the pure-Python compiler; `rust-seccomp` build + required `rust-seccomp-conformance` gate green in CI |
| `syscalls/` (sethostname/prctl/unshare/mount) | **IMPLEMENTED 2026-08-13** (ABI 1.1.0) — wired into the direct-syscall launcher | `rust-syscalls` build + required `rust-syscalls-conformance` gate green |
| NyFS checksum/compression hot path (`nyfs/`) | **IMPLEMENTED 2026-08-13** (ABI 1.0.0) | `rust-nyfs` build + required `rust-nyfs-conformance` gate green |
| IPC wire codec (`ipc/`) | **IMPLEMENTED 2026-08-13** (ABI 1.0.0) | `rust-ipc` build + required `rust-ipc-conformance` gate green (byte-identical wire) |
| Container launch-plan primitives (`container/`) | **IMPLEMENTED 2026-08-13** (ABI 1.0.0) | `rust-container` build + required `rust-container-conformance` gate green |
| IPC transport hot path (`transport/`) | **IMPLEMENTED 2026-08-14** (ABI 2.0.0, caller-supplied buffers) | `rust-transport` build + required `rust-transport-conformance` gate green; §20 benchmark data |
| IPC serving loop (`ipcd/`, ADR-0021 — the first NyRuntime-shaped artifact) | **IMPLEMENTED 2026-08-15** (ABI 1.0.0) | `rust-ipcd` build + required `rust-ipcd-conformance` gate green; §22 A/B: beats the Python floor ~2.8× at the wire median |

All seven crates build and pass their unit tests **in CI on every push**
(the `dtolnay/rust-toolchain@stable` jobs) and locally on the dev host
(the system rustc 1.75 builds them; a maintained stable toolchain via
rustup is also being installed there). Each crate is `libc`-only, and
each has a **required, blocking conformance gate** that forces the
relevant Python test classes through the FFI
(`NYRQIS_RUST_LIB=... NYRQIS_RUST_FORCE=1`), so a semantic regression
in any Rust module fails the build.

This is the concrete application of ADR-0020's **platform-boundary
rule**: the enforcement paths below the boundary (seccomp, storage
hot paths, IPC core/transport/serving loop, container launch) must not
depend on the Python interpreter in their shipped form — the Python
implementations remain the reference behavior and the fallback, while
the Rust modules behind the versioned FFI boundary are the platform
direction. The serving loop is the first step of the ADR-0021
NyRuntime direction: the loop itself owns the per-message dispatch
cycle, so the ctypes boundary tax is paid once per batch, not twice
per round trip.

