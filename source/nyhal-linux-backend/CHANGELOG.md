# Changelog

All notable changes to the Nyrqis Linux Backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.23.0] - 2026-09-02

### Added

#### Packaging & Installation
- **pyproject.toml**: Python packaging with 5 CLI entry points (`nyrqisctl`, `nyrqis-backend`, `nyrqis-session`, `nyrqis-run`, `nyrqis-init`)
- **Systemd units**: `nyrqis-backend.service` (DynamicUser, NoNewPrivileges, ProtectSystem) + `nyrqis-desktop.service` (user session)
- **Install script**: `packaging/install.sh` with system-wide, user-local, and dev modes; dependency checks; Rust crate builds
- **udev rules**: `packaging/udev/90-nyrqis-drm.rules` for DRM device access without root
- **DRM setup script**: `packaging/setup-drm.sh` with --check/--install modes

#### Boot & Desktop
- **nyrqis_init.py**: Unified boot script that boots daemon, loads shell design, and starts desktop session
- **nyrqis-ctl**: Convenience wrapper script with socket/config passthrough
- **Default shell designs**: `shell/defaults/default-shell.nstudio` (minimal) + `desktop.nstudio` (full 30-component desktop)
- **Shell defaults README**: Documents design format, search order, component types

#### GPU & Rendering
- **Compositor FFI**: `ui/compositor_codec.py` with ABI gate + honest stub fallback
- **Wayland SHM protocol**: `rust/compositor/src/wayland.rs` with pool/buffer management
- **DRM atomic commit**: Real `DRM_IOCTL_MODE_SET_CRTC` for connector → CRTC mapping
- **GBM surface creation**: Real `gbm_surface_create()` via dlopen
- **DRM device auto-detect**: Tries card0, card1, renderD128 when no path specified
- **GPU benchmarks**: `tests/benchmarks_gpu.py` with min/median/p95/max statistics

#### Tests
- **GPU pipeline tests**: 21 integration tests for GBM, EGL, Vulkan, DRM, Compositor
- **Boot integration tests**: 24 tests for daemon lifecycle, socket communication, container control
- **Entry point tests**: Verify pyproject.toml entry points resolve correctly
- **Shell defaults tests**: Verify shell designs exist, load, and validate

### Fixed
- **DRM ioctl number**: Corrected `DRM_IOCTL_MODE_GETRESOURCES` size (60 bytes, was 64)
- **nui_load**: Read shell design file content before sending to daemon (was sending file path)
- **Vulkan test**: Skip lifecycle test when no Vulkan driver on hardware

### Changed
- **IMPLEMENTATION_STATUS.md**: Version bump to 0.23.0, GBM updated from "stub" to "real hardware verified"
- **NEXT_SESSION_PLAN.md**: Version 4.0 with session 2026-09-02 accomplishments

### Hardware Verified
- **GBM**: Device → surface → buffer (1920x1080 ARGB8888, stride 7680) on Intel HD Graphics
- **DRM**: Device open with auto-detection (card0/card1/renderD128)
- **EGL**: Display → config → context via real libEGL.so
- **Vulkan**: Instance → device → swapchain via real libvulkan.so

## [0.22.0] - 2026-08-18

### Added
- **NUI import gate**: Parse + validate .nstudio documents against NUI contract tables
- **NUI expression language**: State refs, comparisons, &&/||/!, if/min/max/contains/format
- **NUI runtime**: State management, event dispatch, binding application, action execution
- **NUI compositor**: PIL-based renderer with Eclipse/Solar themes, 30+ component renderers
- **SDL2 compositor**: High-performance GPU-accelerated rendering
- **Desktop session**: Interactive desktop with window management, hit-test, event routing
- **Wayland integration**: Rust crate + Python FFI + DesktopSession + multi-monitor
- **Package signing**: Ed25519 signing/verification with TrustStore
- **Package installer**: Mandatory signature verification, integrity tree validation

### Changed
- **66 NUI component types**: Shell, Data, Form, Media, Developer categories
- **6 reference shell screens**: desktop, security center, vault workspace, widgets, windows, shell draft

## [0.14.0] - 2026-08-15

### Added
- **NyVault encrypted storage**: Argon2id + XChaCha20-Poly1305 envelope encryption
- **KEK rotation**: Rotate key without re-encrypting blocks
- **FUSE passthrough**: Kernel mount for encrypted volumes
- **Per-container quotas**: Byte quotas with EDQUOT enforcement
- **Path-scoped grants**: Subtree-level access control
- **Streaming**: Wire-level chunked transfer for large payloads
- **Systemd integration**: Service units with security hardening
- **Persistent state**: Crash-recovery reporting via daemon state file
- **Health checks**: Liveness probes on dedicated socket
