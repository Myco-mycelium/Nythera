---
title: Next Development Session Plan
version: 2.0.0
date: 2026-09-01
---

# Next Development Session Plan

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **2,720** (2,500 Python + 31 signing + 17 installer + 11 integration + 21 SDL2 Wayland + 12 EGL + 10 DRM + 14 GPU integration + 14 GBM Rust + 19 Wayland Rust + 7 DRM Rust + 10 EGL Rust) |
| CI status | 26/27 jobs passing (1 pre-existing container FFI flaky) |
| Rust crates | **16** (nycore, seccomp, syscalls, keys, container, launcher, ipc, ipcd, transport, nyfs, nyruntime, nyui, wayland, gbm, drm, egl) |
| Python codecs | **5** (wayland_codec, gbm_codec, drm_codec, egl_codec, nstudio_codec) |
| Wayland | Full integration (Rust crate + Python FFI + DesktopSession + multi-monitor + hot-plug + wl_output listener) |
| GBM | Real FFI bindings (dlopen) + 14 tests |
| DRM | Real ioctl wrappers + 7 tests |
| EGL | Integration crate + 10 tests |
| SDL2 Wayland | 21 tests (headless, X11, Wayland fallback) |
| Package signing | Ed25519 + TrustStore + CLI + 59 tests |
| Build architecture | Documented (NPC-007 gap 9 satisfied) |

## What's Been Completed

| Milestone | Status |
|-----------|--------|
| SDL2 Wayland GPU rendering | ✅ Tested headless/X11/fallback |
| Package signing integration | ✅ Installer rejects unsigned packages |
| Dynamic multi-monitor | ✅ Hot-plug detection via check_output_changes() |
| wl_output protocol listener | ✅ geometry/mode/done/scale callbacks |
| Build architecture spec | ✅ NPC-007 gap 9 satisfied |
| Real GBM FFI bindings | ✅ dlopen of libgbm.so with 9 function pointers |
| Real DRM ioctl wrappers | ✅ MODE_GETRESOURCES, MODE_GETCONNECTOR |
| EGL integration crate | ✅ Display/surface/context lifecycle |
| GPU integration tests | ✅ 14 tests for full pipeline |

## What's Left

### Priority 1: Test with Real Hardware (1 day)

**Goal**: Verify the full GPU rendering pipeline on a machine with a GPU.

**Tasks**:
1. Install `libgbm-dev` and `libegl1-mesa-dev`
2. Run the GPU integration test suite with real hardware
3. Verify GBM buffer allocation works end-to-end
4. Verify EGL context creation and OpenGL rendering
5. Verify DRM atomic modesetting with a real connector

**Blocker**: Needs `sudo` access to install packages.

### Priority 2: Custom Wayland Compositor (Large effort)

**Goal**: Build a minimal Wayland compositor for testing and development.

**Why**: A custom compositor would allow:
- Automated GPU rendering tests in CI
- Deterministic test environment
- Full control over the Wayland protocol

**Tasks**:
1. Implement `wl_compositor`, `wl_shm`, `xdg_wm_base` protocols
2. Implement input handling (`wl_seat`, `wl_keyboard`, `wl_pointer`)
3. Implement output management (`wl_output`)
4. Implement frame callbacks (`wl_callback`)

**Estimated scope**: Large — 4-6 weeks

### Priority 3: Vulkan Rendering (ADR-0010) (Large effort)

**Goal**: Implement Vulkan as the native graphics API per ADR-0010.

**Why**: ADR-0010 specifies Vulkan as the native graphics API for Nyrqis.

**Tasks**:
1. Add `ash` or `vulkano` dependency
2. Implement Vulkan instance/device/swapchain
3. Integrate with GBM buffers for Wayland submission
4. Add Vulkan rendering pipeline

**Estimated scope**: Large — 6-8 weeks

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
| 1 | 1 | Test with real hardware |
| 2-3 | 4 | Package update signing |
| 4-6 | 5 | Multi-monitor enhancements |
| 7-8 | 6 | Performance benchmarks |
| 9-14 | 2 | Custom Wayland compositor |
| 15-22 | 3 | Vulkan rendering |

## Success Criteria

| Metric | Target |
|--------|--------|
| Tests passing | 2,800+ |
| GPU rendering | GBM/EGL/DRM path working on real hardware |
| Package signing | Update signing verified |
| Multi-monitor | Per-output rendering working |
| Benchmarks | All display paths measured |
| Custom compositor | Automated CI testing |

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0026: Wayland display-server integration
- NPS-026: Package signing (§6)
- NPS-017: NyHAL Kernel Abstraction Layer
- M14 Plan: `00-platform/M14_PLAN.md`
- M15 Plan: `00-platform/M15_PLAN.md`
