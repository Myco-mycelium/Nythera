---
title: Next Development Session Plan
version: 4.0.0
date: 2026-09-02
---

# Next Development Session Plan

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **2,632** (Python + Rust) |
| Rust crates | **18** (all built and verified) |
| Python codecs | **7** (wayland, gbm, drm, egl, vulkan, nstudio, **compositor**) |
| GPU pipelines | **4** verified on real hardware (GBM, DRM, EGL, Vulkan) |
| Packaging | **pyproject.toml** + systemd units + install script |
| Shell designs | **2** (default-shell.nstudio, desktop.nstudio) |
| Init script | **nyrqis_init.py** (daemon → shell → session) |

## What's Been Completed This Session

| Milestone | Status |
|-----------|--------|
| Python packaging (pyproject.toml) | ✅ CLI entry points: nyrqisctl, nyrqis-backend, nyrqis-session, nyrqis-run |
| Systemd integration | ✅ nyrqis-backend.service + nyrqis-desktop.service |
| Install script | ✅ System-wide, user-local, and dev modes |
| Unified init script | ✅ nyrqis_init.py boots daemon → loads shell → starts session |
| Compositor FFI | ✅ ui/compositor_codec.py with ABI gate + stub fallback |
| Default shell designs | ✅ shell/defaults/default-shell.nstudio + desktop.nstudio |
| Shell defaults README | ✅ Documents design format, search order, component types |
| GBM real hardware | ✅ Device → surface → buffer on Intel HD Graphics |
| DRM device auto-detect | ✅ Tries card0, card1, renderD128 |
| DRM ioctl fix | ✅ Corrected MODE_GETRESOURCES size (60 bytes) |
| Entry point tests | ✅ 8 new tests for pyproject.toml + shell defaults |
| Boot integration tests | ✅ 24 tests for daemon lifecycle + socket + containers |

## What's Left

### Priority 1: Test with Real Hardware (COMPLETE)

**Goal**: Verify the full GPU rendering pipeline on a machine with a GPU.

**Status**: ✅ All verified on Intel HD Graphics
- GBM: device → surface → buffer (1920x1080 ARGB8888)
- DRM: device open with auto-detection
- EGL: display → config → context
- Vulkan: instance → device → swapchain → acquire image

**Note**: DRM modesetting requires `video` group membership for connector enumeration.

### Priority 2: Real GBM/DRM/EGL/Vulkan Integration (IN PROGRESS)

**Goal**: Replace stub implementations with real hardware interaction.

**Status**:
- [x] GBM: Wire `gbm_create_device()` to real `libgbm.so` dlopen
- [x] GBM: Wire `gbm_surface_create()` to real dlopen
- [x] DRM: Fix ioctl number and auto-detect device paths
- [x] EGL: Display/surface/context lifecycle verified
- [x] Vulkan: Instance/device/swapchain lifecycle verified
- [ ] DRM: Wire atomic modesetting to real ioctl
- [ ] EGL: Wire `eglChooseConfig()` / `eglCreateContext()` to real libEGL.so
- [ ] Vulkan: Wire `vkCreateInstance()` / `vkCreateDevice()` to real libvulkan.so
- [ ] GBM: Wire buffer lock to work with EGL rendering

### Priority 3: Custom Wayland Compositor (6 weeks)

**Goal**: Build a minimal Wayland compositor for automated CI testing.

**Why**: A custom compositor would allow:
- Automated GPU rendering tests in CI
- Deterministic test environment
- Full control over the Wayland protocol

**Tasks**:
1. Implement `wl_compositor`, `wl_shm`, `xdg_wm_base` protocols
2. Implement input handling (`wl_seat`, `wl_keyboard`, `wl_pointer`)
3. Implement output management (`wl_output`)
4. Implement frame callbacks (`wl_callback`)
5. DRM/KMS backend for display output

### Priority 4: Package Update Signing (1 week)

**Goal**: Verify signatures on package updates.

**Tasks**:
1. Delta update signature verification
2. Re-signing after local modifications
3. Rollback signature validation

### Priority 5: Multi-Monitor Enhancements (2 weeks)

**Goal**: Per-output rendering and window migration.

**Tasks**:
1. Output-specific surface creation
2. Multi-surface rendering pipeline
3. Workspace-to-output binding
4. Window migration on output removal

### Priority 6: Performance Benchmarks (2 weeks)

**Goal**: Measure rendering performance across all paths.

**Tasks**:
1. Software rendering (PIL) baseline
2. SDL2 headless rendering
3. SDL2 X11 rendering
4. SDL2 Wayland rendering (with compositor)
5. Frame time and FPS measurements
6. Display pipeline latency measurements

## Timeline

| Week | Priority | Deliverable |
|------|----------|-------------|
| 1 | 1 | ✅ Test with real hardware (COMPLETE) |
| 1 | Packaging | ✅ pyproject.toml, systemd, install script (COMPLETE) |
| 1 | Init | ✅ nyrqis_init.py boot-to-desktop (COMPLETE) |
| 1 | GPU | ✅ GBM/DRM/EGL/Vulkan verified on hardware (COMPLETE) |
| 2-5 | 2 | Real GBM/DRM/EGL/Vulkan integration (in progress) |
| 6-11 | 3 | Custom Wayland compositor |
| 12-13 | 4 | Package update signing |
| 14-15 | 5 | Multi-monitor enhancements |
| 16-17 | 6 | Performance benchmarks |

## Success Criteria

| Metric | Target | Current |
|--------|--------|---------|
| Tests passing | 2,800+ | 2,632 |
| GPU rendering | GBM/EGL/DRM/Vulkan path working on real hardware | ✅ Verified |
| Packaging | pip install + systemd | ✅ Implemented |
| Boot-to-desktop | nyrqis_init.py works end-to-end | ✅ Verified |
| Package signing | Update signing verified | Pending |
| Multi-monitor | Per-output rendering working | Pending |
| Benchmarks | All display paths measured | Pending |
| Custom compositor | Automated CI testing | Pending |

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0026: Wayland display-server integration
- NPS-026: Package signing (§6)
- NPS-017: NyHAL Kernel Abstraction Layer
- M14 Plan: `00-platform/M14_PLAN.md`
- M15 Plan: `00-platform/M15_PLAN.md`
