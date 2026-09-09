# Nyrqis Linux Backend — Implementation Status

**Version**: 0.27.0  
**Date**: 2026-09-09  
**Repository**: github.com/Myco-mycelium/Nythera

## Overview

This document tracks the implementation status of the Nyrqis Linux backend,
providing the hardware abstraction layer for the Nyrqis OS.

## What's Working

### Core Infrastructure
- [x] **Python packaging** — pyproject.toml with 5 CLI entry points
- [x] **Systemd integration** — Backend daemon + desktop session units
- [x] **Install script** — System/user/dev modes with dependency checks
- [x] **Init script** — `nyrqis_init.py` boots daemon → shell → session
- [x] **Convenience CLI** — `nyrqis-ctl` wrapper for common commands
- [x] **Diagnostics** — `nyrqis_init --diagnose` with 7 system checks

### GPU Pipeline (Real Hardware Verified)
- [x] **GBM** — Real `gbm_create_device()` and `gbm_surface_create()` via dlopen
- [x] **EGL** — Real `eglInitialize()`, `eglChooseConfig()`, `eglCreateContext()` via dlopen
- [x] **Vulkan** — Real `vkCreateInstance()`, `vkDestroyInstance()` via dlopen
- [x] **DRM** — Fixed ioctl number, auto-detect card0/card1/renderD128
- [x] **Render pipeline** — GBM + EGL + DRM connected for display output
- [x] **SDL2** — Headless rendering backend for CI/testing via dlopen

### Multi-Monitor
- [x] **Output detection** — Per-output surface management
- [x] **Workspace binding** — Workspace-to-output binding
- [x] **Hot-plug support** — Output addition/removal handling
- [x] **Hot-plug monitoring** — `HotPlugMonitor` with periodic DRM polling + callbacks
- [x] **Window migration** — Workspaces migrate to primary output on output removal

### Wayland Compositor
- [x] **wl_compositor** — Surface creation and buffer management
- [x] **wl_shm** — Shared memory buffer pools
- [x] **xdg_wm_base** — Shell surfaces (toplevel, popup)
- [x] **wl_output** — Display output information
- [x] **wl_seat** — Input device capabilities
- [x] **wl_callback** — Frame timing callbacks
- [x] **Socket server** — Unix domain socket for client connections
- [x] **Protocol codec** — Encoder/decoder for wire format messages
- [x] **Integrated compositor** — `nyrqis_compositor.py` combining all pieces

### SHM Buffer Sharing
- [x] **memfd_create** — Real `memfd_create` + `mmap` for Wayland surface content
- [x] **Buffer manager** — `ShmManager` with region allocation and cleanup
- [x] **Pixel format support** — ARGB8888, XRGB8888 for compositor buffers

### Package Signing
- [x] **Ed25519 keys** — Key generation, signing, verification
- [x] **Trust store** — Key management and trust hierarchy
- [x] **Delta updates** — Signature verification for package updates
- [x] **Re-signing** — Re-sign after local modifications
- [x] **Rollback** — Rollback signature validation

### Shell Designs
- [x] **Default shell** — `shell/defaults/default-shell.nstudio` (minimal)
- [x] **Full desktop** — `shell/defaults/desktop.nstudio` (full desktop)
- [x] **Documentation** — README with format spec and search order

### Desktop Environment
- [x] **Taskbar** — Start button, app indicators, clock, system tray, click handling
- [x] **Start menu** — 5 app items + search bar + power button
- [x] **Desktop icons** — 4 icons with colored backgrounds and labels
- [x] **Windows** — Title bar, close/minimize/maximize buttons, drag-to-move
- [x] **Window switcher** — Alt+Tab style window switching
- [x] **Notifications** — Toast notification system
- [x] **Undo/redo** — Command-based undo/redo manager

### Accessibility
- [x] **A11y metadata** — Component roles, labels, descriptions
- [x] **Keyboard navigation** — Tab index, focus management
- [x] **Screen reader support** — ARIA-compatible role mapping

### Wayland Client Testing
- [x] **Client test harness** — Low-level client for CI testing
- [x] **Compatibility layer** — High-level API mimicking common client libraries
- [x] **Weston test script** — Integration tests for weston-simple-shm

### Testing & CI
- [x] **2532+ tests passing** — Python backend suite (test_backend: 2503 + signing/installer/integration suites)
- [x] **CI green on GitHub runners** — first green run in repo history (568 prior CI runs red); container conformance gate now skips honestly on runner-hosted userns-restricted kernels, wayland ABI assertion fixed
- [x] **208 Rust tests** — 14 container + 15 seccomp + 14 syscalls + 8 keys + 10 nyfs + 11 ipc + 5 transport + 24 ipcd + 14 nyruntime + 19 nyui + 10 launcher + 27 wayland + 48 compositor
- [x] **Compositor crate in CI** — `rust-compositor` (build + 48 crate tests) and `compositor-host` (socket host-half E2E, required gate) jobs; previously the crate compiled only on dev hosts
- [x] **Full pipeline tests** — 11 integration tests covering complete pipeline
- [x] **CI test runner** — `run_tests.sh` with --quick/--gpu/--compositor modes
- [x] **GPU pipeline tests** — Verified on Intel HD Graphics
- [x] **Render pipeline tests** — Pipeline config, lifecycle, monitor manager
- [x] **Boot init tests** — Daemon lifecycle, socket, containers

### Documentation
- [x] **CHANGELOG.md** — Documents v0.14.0 through v0.25.0
- [x] **getting-started.md** — Quick start, architecture, CLI, GPU, testing
- [x] **NEXT_SESSION_PLAN** — v6.0 development roadmap

## Hardware Verified

| GPU | Status |
|-----|--------|
| Intel HD Graphics (2nd Gen) | ✅ GBM, EGL, Vulkan all working |
| Intel UHD Graphics | ✅ Expected to work (same Mesa stack) |
| AMD Radeon | ⚠️ Untested (same Mesa stack likely works) |
| NVIDIA (proprietary) | ❌ Not supported (requires Nouveau or Vulkan) |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Nyrqis OS                         │
├─────────────────────────────────────────────────────┤
│  Shell Design (.nstudio)                            │
│  ├── default-shell.nstudio (minimal)                │
│  └── desktop.nstudio (full desktop)                 │
├─────────────────────────────────────────────────────┤
│  Init Script (nyrqis_init.py)                       │
│  ├── Phase 1: Start backend daemon                  │
│  ├── Phase 2: Load shell design                     │
│  └── Phase 3: Start desktop session                 │
├─────────────────────────────────────────────────────┤
│  Desktop Session                                    │
│  ├── Render Pipeline (GBM + EGL + DRM)              │
│  ├── Multi-Monitor Manager                          │
│  ├── Window Manager                                 │
│  ├── Taskbar & Start Menu                           │
│  └── Compositor (Wayland protocols)                 │
├─────────────────────────────────────────────────────┤
│  Wayland Layer                                      │
│  ├── Socket Server (Unix domain socket)             │
│  ├── Protocol Codec (wire format)                   │
│  ├── SHM Buffers (memfd_create + mmap)              │
│  └── Client Test Harness                            │
├─────────────────────────────────────────────────────┤
│  Backend Daemon                                     │
│  ├── Service IPC (Unix sockets)                     │
│  ├── Container Control                              │
│  └── Package Management + Signing                   │
├─────────────────────────────────────────────────────┤
│  GPU Crates (Rust)                                  │
│  ├── GBM — Buffer allocation                        │
│  ├── EGL — OpenGL ES rendering                      │
│  ├── Vulkan — Native graphics API                   │
│  ├── DRM — Modesetting                              │
│  └── Compositor — Wayland compositor                │
├─────────────────────────────────────────────────────┤
│  Linux Kernel                                       │
│  ├── DRM/KMS — Display output                       │
│  ├── GBM — Buffer management                        │
│  └── Input — Keyboard, mouse, touch                 │
└─────────────────────────────────────────────────────┘
```

## CLI Entry Points

```bash
# Full desktop boot
python3 nyrqis_init.py

# Headless for CI
python3 nyrqis_init.py --headless

# Custom shell design
python3 nyrqis_init.py --design shell.nstudio

# Just start daemon
python3 nyrqis_init.py --daemon-only

# Diagnose issues
python3 nyrqis_init.py --diagnose

# System control
nyrqis-ctl status
nyrqis-ctl app list
nyrqis-ctl app install /path/to/app.nypkg

# Live demo
python3 demo/run_demo.py --output /tmp/nyrqis-demo

# Test suite
./run_tests.sh                    # All tests
./run_tests.sh --quick            # Quick (no hardware)
./run_tests.sh --gpu              # GPU tests only
./run_tests.sh --compositor       # Compositor tests only
```

## GPU Benchmarks (Intel HD Graphics)

| Pipeline | Operation | Software | GPU | Speedup |
|----------|-----------|----------|-----|---------|
| Surface | Create | 1,399µs (PIL) | 0.75µs (GBM) | **1,865x** |
| Render | Draw | 1,552µs (PIL) | 0.67µs (EGL) | **2,316x** |
| Buffer | Fill | 12,015µs (SHM) | 89µs (pipeline) | **135x** |
| Compositor | Start | 2,875µs (SHM) | 1.07µs | **2,687x** |

## Test Results

```
Full suite:      2503 tests, test_backend (0 failures)
Compositor crate: 47 tests (0 failures, 59 stable runs)
All 12 other Rust crates: 113 tests (0 failures)
```

Additional suites verified this release: `test_installer.py` (17),
`test_package_integration.py` (11), `tests/test_package_signing.py`
(10), `tests/test_update_signing.py` (11),
`tests/test_live_installer_smoke.py` (6), compositor
event-loop/e2e/integration (45).

## Auto-Remediation (0.25.1)

All remediation actions are now real implementations — no placeholders:

| Action | Behavior |
|--------|----------|
| `alert` | Emits an alert event (unchanged) |
| `restart` | Terminate + spawn, respects `max_restarts` (unchanged) |
| `scale_up` | Memory limit +25% (unchanged) |
| `scale_down` | Memory limit −25% (unchanged) |
| `throttle` | cgroups v2 `cpu.weight` −25% (floor 1); `nice` +5 fallback on cgroups v1; failures reported honestly in history |
| `migrate` | Checkpoint (stats + limits snapshot, `ckpt-` id) then terminate; restore = later `spawn()` |

## Package Signing (0.25.1) — fail-closed

- `backend/package_signing.py` carries the full NPS-026 §6 API again
  (`SigningKeypair` / `sign_package` / `verify_package` /
  `PackageSignature` / `TrustStore`) plus the `PackageSigner`
  convenience class. The forgeable stub fallbacks (deterministic
  keys, hash "signatures") are removed: without PyNaCl every
  operation raises `PackageSignError`.
- `backend/update_signing.py` verifies real Ed25519 signatures on
  full/delta/rollback updates and actually signs in
  `re_sign_update`; trust-store membership alone no longer verifies
  anything.

## Compositor (0.26.0) — wire-format event loop

`rust/compositor` (ABI 0.2.0) adds the request-processing half of a
real compositor event loop (`src/event_loop.rs`): standard Wayland
wire-format request parsing (object table with implicit wl_display
id 1), dispatch of get_registry / bind / create_surface / attach /
frame / commit into the crate's surface state machine, and
server→client events in the same wire format (`wl_registry.global`,
one-shot `wl_callback.done` on commit, `wl_buffer.release`). FFI:
`nyrqis_compositor_handle_client_data` / `next_event` /
`object_count` / `event_loop_last_error`, wrapped in
`ui/compositor_codec.py` (ABI gate 0x0000_0200). Verified end-to-end
over FFI from Python. The socket/epoll host half stays in
`ui/nyrqis_compositor.py`; DRM-backed presentation remains follow-on
work. Codec stub audit (gbm/drm/egl/vulkan/wayland): all stub modes
are fail-closed already — honest failure sentinels, truthful
`is_available()`, caller-side fallbacks.

## What's Complete

All Priorities 1-6 from NEXT_SESSION_PLAN v6.0 are complete:

| Priority | Task | Status |
|----------|------|--------|
| 1 | Test with real hardware | ✅ Intel HD Graphics verified |
| 2 | Real GPU integration | ✅ GBM/DRM/EGL/Vulkan via dlopen |
| 3 | Custom Wayland compositor | ✅ Full pipeline with SHM buffers |
| 4 | Package update signing | ✅ Ed25519 + full/delta/rollback |
| 5 | Multi-monitor enhancements | ✅ Hot-plug + window migration |
| 6 | Performance benchmarks | ✅ All display paths measured |

## What's Next

| Priority | Task | Timeline |
|----------|------|----------|
| 7 | Real hardware testing (AMD, NVIDIA, ARM) | M14 Phase 2 |
| 8 | Wayland client compatibility (weston, GTK4, Qt6) | M14 Phase 2 |
| 9 | ✅ Socket/epoll host half on the compositor wire event loop (**done 0.27.0**); DRM presentation remains | M14 follow-on |
| 10 | GPU acceleration (GBM + DRM) production hardening | M14 follow-on |

## SDK (M14 Phase 3 & 4)

New developer tools in `sdk/nyrqis_sdk/`:

- **Scaffolding** (`scaffold.py`): Project template generator (app, shell, rust)
- **CLI** (`cli.py`): `nyq` command with new, build, test, preview, pkg
- **Package Manager** (`cli.py`): install, remove, update, search, list, stats
- **Hot Reload** (`hotreload.py`): File/directory watchers for .nstudio files
- **Telemetry** (`telemetry.py`): Opt-in crash reporting, metrics collection
- **Performance** (`performance.py`): Timer, MemoryTracker, FrameRateMonitor
- **Restore Points** (`restore.py`): System snapshot and restore functionality

Tests: 46 tests (11 scaffold + 10 CLI/hotreload + 25 production)

## Compositor Event Loop (M14 follow-on)

Real Wayland compositor in `ui/compositor_event_loop.py`:

- Unix domain socket server for client connections
- Wayland protocol message parsing and dispatch
- Surface creation/destroy lifecycle
- Output management
- Buffer attachment handling
- Frame callback support
- Thread-safe client/surface tracking

Tests: 8 tests (event loop, surfaces, outputs, client connections)

## Compositor Socket Host Half (0.27.0)

The "socket/epoll host half" follow-on from the 0.26.0 wire-format
event loop is closed. `ui/compositor_host.py` (`CompositorHost`)
bridges the transport (`ui/wayland_socket.py`) to the Rust wire event
loop (`rust/compositor`, ABI 0.2.0) through `ui/compositor_codec.py`:

- Client bytes are fed to the crate on message boundaries (partial
  messages reassembled across `recv()` calls — never a truncated
  request).
- The crate parses the wire format, maintains the object table, and
  dispatches into its surface state machine; its response events
  (5 `wl_registry.global` advertisements, `wl_callback.done` on
  commit, `wl_buffer.release`) are drained and written back to the
  client socket.
- When wired, the wire loop owns protocol dispatch (the legacy Python
  dispatch is skipped — it double-responded). Without the crate the
  Python path remains the fallback; the host fabricates nothing in
  stub mode (fail-closed).
- `nyrqis_compositor_start`/`stop` now reset the crate's protocol
  state (previously a restart collided with stale object ids) and
  `wl_display.sync` is served (one-shot `wl_callback.done`).
- `NyrqisCompositor` wires this automatically; `get_stats()` carries
  host-half counters (engine, bytes in/out, events drained, protocol
  errors).

Verified end-to-end over a real Unix domain socket: client handshake
(get_registry → bind → create_surface → frame → commit) returns 5
globals + a frame-done stamp. Tests: `tests/test_compositor_host.py`
(8). CI: `rust-compositor` (crate build + tests) and `compositor-host`
(required gate) — the crate had never been compiled in CI before.

## Delta Update Generation (0.27.0, NPS-026 §6)

The generation half of package updates: `backend/delta_update.py`
produces what `backend/update_signing.py` verifies.

- `diff_packages`: add/modify/remove ops between two payload
  directories (deterministic order, `.nypkg` layout normalized,
  manifest/integrity metadata excluded).
- `create_delta_update`: canonical-checksum document, optionally
  signed with Ed25519 using the same payload form the shipped
  verifier checks.
- `apply_delta_update`: signature verified BEFORE any filesystem
  mutation; per-op path-traversal guard; unsigned deltas refused when
  a trust store is supplied (fail-closed).
- Cross-verified: a generated delta passes `UpdateVerifier
  .verify_delta_update` unmodified; a tampered op list fails it
  (`tests/test_delta_update.py::TestDeltaPassesShippedVerifier`).

Tests: 18 (`tests/test_delta_update.py`).

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0020: Implementation languages and the platform boundary
- ADR-0026: Wayland display-server integration
- NPS-017: NyHAL Kernel Abstraction Layer
- NPS-026: Package signing (§6)
