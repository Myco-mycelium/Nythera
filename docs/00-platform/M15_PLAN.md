---
title: M15 — GPU Acceleration & Production Readiness
document_id: M15-PLAN
version: 1.0.0
status: Draft
owners: [Nyrqis Engineering]
created: 2026-09-01
updated: 2026-09-01
depends_on: [ADR-0010, ADR-0026, NPS-026, NPS-017]
---

# M15 — GPU Acceleration & Production Readiness

## Context

M14 completed the foundational Wayland integration and package signing.
M15 focuses on GPU-accelerated rendering and production hardening.

### What M14 Delivered

| Deliverable | Status |
|-------------|--------|
| Wayland display server integration (ADR-0026) | ✅ Accepted |
| Package signing (NPS-026 §6) | ✅ Implemented |
| GBM buffer allocation crate | ✅ Scaffold + 14 tests |
| DRM atomic modesetting crate | ✅ Scaffold + 7 tests |
| SDL2 Wayland GPU rendering | ✅ 21 tests (headless/X11) |
| Multi-monitor support | ✅ wl_output + hot-plug detection |
| Build architecture spec (NPC-007) | ✅ Documented |
| Cross-architecture testing | ✅ arm64 CI |

### Test Counts

| Suite | Tests |
|-------|-------|
| Python backend | 2,500 |
| Package signing | 31 |
| Package installer | 17 |
| Package integration | 11 |
| SDL2 Wayland | 21 |
| GBM crate | 14 |
| DRM crate | 7 |
| Wayland crate | 19 |
| Other Rust crates | 66 |
| **Total** | **2,686** |

## Milestone Goals

| Goal | Priority | Est. Effort |
|------|----------|-------------|
| GPU-accelerated rendering via GBM/DRM/EGL | High | Large |
| Wayland compositor for testing | High | Medium |
| Package installer hardening | High | Medium |
| Multi-monitor enhancements | Medium | Small |
| Performance benchmarks | Medium | Medium |

## Phase 1: GPU-Accelerated Rendering

### 1.1 Real GBM Integration

**Goal**: Replace stub GBM crate with real `libgbm` FFI bindings.

**Components**:
- `dlopen`/`dlsym` for `libgbm.so` at runtime
- `gbm_create_device()`, `gbm_surface_create()`, `gbm_bo_lock()`
- Buffer export to DMA-BUF for Wayland submission
- Error handling and fallback to software rendering

**Dependencies**:
- `libgbm-dev` (Ubuntu: `libgbm-dev`)
- DRM render node (`/dev/dri/renderD128`)

**Estimated scope**: Medium — 2 weeks

### 1.2 Real DRM Integration

**Goal**: Replace stub DRM crate with real DRM ioctl wrappers.

**Components**:
- `DRM_IOCTL_MODE_GETRESOURCES` — enumerate CRTCs/connectors
- `DRM_IOCTL_MODE_GETCONNECTOR` — query connector properties
- `DRM_IOCTL_MODE_ATOMIC` — atomic modesetting commit
- `DRM_IOCTL_MODE_GETPROPERTY` / `DRM_IOCTL_MODE_SETPROPERTY`
- Framebuffer creation from GBM bo

**Dependencies**:
- GBM buffers (1.1)
- DRM master access or `renderD` node

**Estimated scope**: Large — 4 weeks

### 1.3 EGL Integration

**Goal**: OpenGL ES rendering into GBM buffers.

**Components**:
- EGL display/surface/context creation
- OpenGL ES 3.0 rendering pipeline
- Wayland EGL extension for buffer sharing
- Shader compilation and uniform management

**Dependencies**:
- GBM buffers (1.1)
- Mesa/EGL drivers

**Estimated scope**: Large — 4 weeks

### 1.4 Pragmatic Path: SDL2 Wayland Backend

**Alternative**: Use SDL2's Wayland backend for GPU-accelerated rendering.

**Benefits**:
- SDL2 handles GBM/EGL internally
- No custom DRM/EGL code needed
- Works with existing `SDLCompositor`

**Status**: ✅ Ready — tested with headless, X11, and Wayland fallback.

**Estimated scope**: 1 day testing with real compositor

## Phase 2: Wayland Compositor for Testing

### 2.1 Install and Configure Sway

**Goal**: Set up Sway as a test compositor for GPU rendering.

**Components**:
- Install Sway on development machines
- Configure Sway for headless testing
- Create test scripts for compositor lifecycle
- Document testing workflow

**Dependencies**:
- sudo access on development machines

**Estimated scope**: Small — 1 day

### 2.2 Compositor Integration Tests

**Goal**: End-to-end tests with a real Wayland compositor.

**Components**:
- Start Sway in headless mode (`sway -d`)
- Run SDL2 Wayland rendering tests
- Verify GPU-accelerated output
- Capture screenshots for validation

**Dependencies**:
- Sway installed (2.1)

**Estimated scope**: Small — 2 days

## Phase 3: Package Installer Hardening

### 3.1 Signature Verification in Install Flow

**Goal**: Ensure all packages are verified before installation.

**Components**:
- Mandatory signature check (NPS-026 §6.1)
- Trust store persistence (`~/.nyrqis/trust-store.json`)
- Reject unsigned packages with clear error message
- Display publisher info in permission prompt

**Dependencies**:
- `backend/package_signing.py` ✅
- `backend/installer.py` ✅

**Status**: ✅ Already implemented

### 3.2 Package Update Signing

**Goal**: Verify signatures on package updates.

**Components**:
- Delta update signature verification
- Re-signing after local modifications
- Rollback signature validation

**Dependencies**:
- 3.1 (already complete)

**Estimated scope**: Small — 1 week

## Phase 4: Multi-Monitor Enhancements

### 4.1 Dynamic Output Detection

**Goal**: Handle hot-plug events (monitor connect/disconnect).

**Components**:
- `wl_output` event listener (geometry, mode, done, scale)
- Dynamic monitor list updates
- Window migration on output removal

**Status**: ✅ Partially implemented
- `OutputChange` enum + `check_output_changes()` FFI
- DesktopSession auto-syncs after `poll_and_dispatch()`

**Remaining**:
- Full `wl_output` protocol listener (real-time events without polling)
- Window migration logic

**Estimated scope**: Small — 1 week

### 4.2 Per-Output Rendering

**Goal**: Render different content on each monitor.

**Components**:
- Output-specific surface creation
- Multi-surface rendering pipeline
- Workspace-to-output binding

**Estimated scope**: Medium — 2 weeks

## Phase 5: Performance Benchmarks

### 5.1 Rendering Benchmarks

**Goal**: Measure rendering performance across all paths.

**Components**:
- Software rendering (PIL) baseline
- SDL2 headless rendering
- SDL2 X11 rendering
- SDL2 Wayland rendering (with compositor)
- Frame time and FPS measurements

**Estimated scope**: Small — 1 week

### 5.2 Display Pipeline Benchmarks

**Goal**: Measure the full display pipeline latency.

**Components**:
- Render → SHM buffer → Wayland submit latency
- Render → GBM buffer → DRM commit latency
- Event dispatch latency
- Multi-monitor scaling overhead

**Estimated scope**: Small — 1 week

## Timeline

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1-2 | 1.1 | Real GBM integration |
| 3-4 | 1.2 | Real DRM integration |
| 5-6 | 1.3 | EGL integration |
| 7 | 2.1-2.2 | Sway setup + compositor tests |
| 8 | 3.2 | Package update signing |
| 9-10 | 4.1-4.2 | Multi-monitor enhancements |
| 11-12 | 5.1-5.2 | Performance benchmarks |

## Success Criteria

| Metric | Target |
|--------|--------|
| Tests passing | 2,800+ |
| GPU-accelerated rendering | GBM/DRM path working |
| Package signing | Update signing verified |
| Multi-monitor | Dynamic output detection working |
| Benchmarks | All display paths measured |

## Dependencies

| Dependency | Status | Notes |
|------------|--------|-------|
| Wayland compositor | Needs install | Sway for testing |
| libgbm-dev | Needed | For GBM buffers |
| Mesa/EGL | Needed | For EGL integration |
| DRM render node | Needed | `/dev/dri/renderD128` |
| PyNaCl | ✅ Installed | For package signing |
| SDL2 | ✅ Installed | For GPU-accelerated rendering |
