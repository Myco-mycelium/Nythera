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
| NUI (.nstudio) import gate (`nyui/`, ADR-0025 — the UI layer's first compiled artifact) | **IMPLEMENTED 2026-08-16** (ABI 1.0.0) | `rust-nyui` build + required `rust-nyui-conformance` gate green (floor's suite forced through the FFI; error messages byte-identical on single-issue documents) |
| Wayland display-server client (`wayland/`, ADR-0026 — display integration) | **PHASE 1b COMPLETE 2026-09-01** (ABI 1.1.0) | real wl_display connection + wl_shm SHM buffer submission (memfd_create + mmap + wl_shm_pool + wl_buffer); xdg-shell (xdg_surface + xdg_toplevel + set_title); wl_seat input binding; event handler callback; 17 unit tests; DesktopSession event loop integration |

All nine crates build and pass their unit tests **in CI on every push**
(the `dtolnay/rust-toolchain@stable` jobs) and locally on the dev host
(the system rustc 1.75 builds them; a maintained stable toolchain via
rustup is also being installed there). Each crate keeps its dependency
surface minimal (`libc`-only, except `nyui/` which uses `serde_json`
for .nstudio parsing and `wayland/` which uses `wayland-client` for
protocol bindings), and each has a **required, blocking conformance
gate** that forces the relevant Python test classes through the FFI
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

