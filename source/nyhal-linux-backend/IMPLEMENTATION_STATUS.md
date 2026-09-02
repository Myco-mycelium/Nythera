# Nyrqis Linux Backend — Implementation Status

**Version**: 0.24.0  
**Date**: 2026-09-02  
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

### Multi-Monitor
- [x] **Output detection** — Per-output surface management
- [x] **Workspace binding** — Workspace-to-output binding
- [x] **Hot-plug support** — Output addition/removal handling

### Wayland Compositor
- [x] **wl_compositor** — Surface creation and buffer management
- [x] **wl_shm** — Shared memory buffer pools
- [x] **xdg_wm_base** — Shell surfaces (toplevel, popup)
- [x] **wl_output** — Display output information
- [x] **wl_seat** — Input device capabilities
- [x] **wl_callback** — Frame timing callbacks

### Package Signing
- [x] **Delta updates** — Signature verification for package updates
- [x] **Re-signing** — Re-sign after local modifications
- [x] **Rollback** — Rollback signature validation

### Shell Designs
- [x] **Default shell** — `shell/defaults/default-shell.nstudio` (minimal)
- [x] **Full desktop** — `shell/defaults/desktop.nstudio` (full desktop)
- [x] **Documentation** — README with format spec and search order

### Testing
- [x] **2,654 tests passing** — Python + Rust
- [x] **72 Rust tests** — 13 EGL + 12 Vulkan + 33 Compositor + 14 GBM
- [x] **21 GPU pipeline tests** — Verified on Intel HD Graphics
- [x] **22 render pipeline tests** — Pipeline config, lifecycle, monitor manager
- [x] **34 boot init + update signing tests** — Daemon lifecycle, socket, containers

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
│  └── Compositor (Wayland protocols)                 │
├─────────────────────────────────────────────────────┤
│  Backend Daemon                                     │
│  ├── Service IPC (Unix sockets)                     │
│  ├── Container Control                              │
│  └── Package Management                             │
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
```

## GPU Benchmarks (Intel HD Graphics)

| Pipeline | Operation | Median | P95 |
|----------|-----------|--------|-----|
| GBM | device open | 2.1 ms | 3.4 ms |
| GBM | surface create | 1.8 ms | 2.9 ms |
| EGL | display init/terminate | 5.6 µs | 8.4 µs |
| Vulkan | instance create/destroy | 3.2 ms | 4.8 ms |
| Compositor | start/stop | 2.3 µs | 3.4 µs |
| Compositor | output/surface | 10.4 µs | 20.5 µs |

## What's Left

| Priority | Task | Status |
|----------|------|--------|
| 3 | Custom Wayland compositor | In progress |
| 5 | Multi-monitor enhancements | Partial |
| 6 | Performance benchmarks | Partial |

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0020: Implementation languages and the platform boundary
- ADR-0026: Wayland display-server integration
- NPS-017: NyHAL Kernel Abstraction Layer
- NPS-026: Package signing (§6)
