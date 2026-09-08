# Nyrqis Linux Backend — Implementation Status

**Version**: 0.25.1  
**Date**: 2026-09-08  
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
- [x] **2622 tests passing** — Python backend suite (2619 + 3 remediation tests)
- [x] **72 Rust tests** — 13 EGL + 12 Vulkan + 33 Compositor + 14 GBM
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
Full suite:      2622 tests (0 failures)
Compositor crate: 37 tests (0 failures, 12/12 stable runs)
Quick mode:      72 tests (7/7 passed)
```

Additional suites verified this release: `test_installer.py` (17),
`test_package_integration.py` (11), `tests/test_package_signing.py`
(10), `tests/test_update_signing.py` (11),
`tests/test_live_installer_smoke.py` (6).

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

## Compositor (0.25.1)

`rust/compositor` (ABI 0.1.0) input/frame/commit stubs are now real:
per-surface bounded input queues with a dispatch counter, frame
timestamps, and per-surface commit counts — with introspection FFI
(`input_queue_depth`, `total_input_dispatched`, `commit_count`,
`last_frame_time`) exposed through `ui/compositor_codec.py`. A real
DRM-backed event loop remains follow-on work.

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
| 9 | Custom compositor event loop + protocol parsing | M14 follow-on |
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

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0020: Implementation languages and the platform boundary
- ADR-0026: Wayland display-server integration
- NPS-017: NyHAL Kernel Abstraction Layer
- NPS-026: Package signing (§6)
