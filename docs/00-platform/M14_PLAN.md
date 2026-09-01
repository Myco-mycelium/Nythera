---
title: M14 — Next Development Milestone Plan
document_id: M14-PLAN
version: 1.0.0
status: Draft
owners: [Nyrqis Engineering]
created: 2026-09-01
updated: 2026-09-01
depends_on: [ADR-0010, ADR-0026, NPS-026]
---

# M14 — Next Development Milestone Plan

## Context

The Nyrqis Linux Backend is substantially complete:
- **2,548 tests passing** (2,500 Python + 17 Rust + 31 package signing)
- **Wayland display server integration** (ADR-0026) — Phases 1-2 complete
- **Package signing** (NPS-026 §6) — Ed25519 via PyNaCl implemented
- **Cross-architecture testing** — aarch64 seccomp tables validated
- **All 7 threat model phases** complete

M14 focuses on the remaining high-priority items: GPU acceleration,
package signing integration, and the build architecture.

## Milestone Goals

| Goal | Priority | Est. Effort |
|------|----------|-------------|
| GPU-accelerated rendering via GBM/DRM | High | Large |
| Package signing integration into installer | High | Medium |
| Build architecture specification | Medium | Medium |
| Multi-monitor enhancements | Medium | Small |

## Phase 1: GPU Acceleration (GBM/DRM)

### 1.1 GBM Buffer Allocation

**Goal**: Allocate GPU-accessible buffers for hardware-accelerated rendering.

**Components**:
- Rust crate `rust/gbm/` wrapping `libgbm`
- `gbm_device` creation from DRM node
- `gbm_surface` for rendering targets
- Buffer export to DMA-BUF for Wayland submission

**Dependencies**:
- `libgbm-dev` (Ubuntu: `libgbm-dev`)
- DRM node access (`/dev/dri/renderD128`)

**Estimated scope**: Medium — 2-3 weeks

### 1.2 DRM Atomic Modesetting

**Goal**: Direct scanout of GPU buffers to display.

**Components**:
- DRM ioctl wrapper for `DRM_IOCTL_MODE_ATOMIC`
- CRTC/plane/connector enumeration
- Frame presentation via DRM page flip

**Dependencies**:
- GBM buffers (1.1)
- DRM master access or `renderD` node

**Estimated scope**: Large — 4-6 weeks

### 1.3 EGL Integration

**Goal**: OpenGL/Vulkan rendering into GBM buffers.

**Components**:
- EGL display/surface/context creation
- OpenGL ES 3.0 rendering pipeline
- Wayland EGL extension for buffer sharing

**Dependencies**:
- GBM buffers (1.1)
- Mesa/EGL drivers

**Estimated scope**: Large — 4-6 weeks

### 1.4 Pragmatic Path: SDL2 Wayland Backend

**Alternative**: Use SDL2's Wayland backend for GPU-accelerated rendering.

**Benefits**:
- SDL2 handles GBM/EGL internally
- No custom DRM/EGL code needed
- Works with existing `SDLCompositor`

**Implementation**:
```python
compositor = SDLCompositor(wayland=True, headless=False)
compositor.render_screen(document)
```

**Status**: Ready to test with a real Wayland compositor.

**Estimated scope**: Small — 1 week testing + integration

## Phase 2: Package Signing Integration

### 2.1 Installer Integration

**Goal**: Verify package signatures during installation.

**Components**:
- Integrate `PackageSignature` into `nyrqisctl install`
- Load trust store from `~/.nyrqis/trust-store.json`
- Reject unsigned packages (NPS-026 §6.1)
- Display publisher info in permission prompt

**Dependencies**:
- `backend/package_signing.py` ✅
- `nyrqisctl sign` CLI ✅

**Estimated scope**: Medium — 2 weeks

### 2.2 Publisher Key Enrollment

**Goal**: Allow users to trust publisher keys.

**Components**:
- `nyrqisctl trust add <keyfile>` command
- Trust store persistence
- Key revocation support
- Integration with NPS-027 (Package Trust Model)

**Dependencies**:
- `TrustStore` class ✅

**Estimated scope**: Small — 1 week

### 2.3 Update Signing

**Goal**: Verify signatures on package updates.

**Components**:
- Delta update signature verification
- Re-signing after local modifications
- Rollback signature validation

**Dependencies**:
- 2.1 (installer integration)

**Estimated scope**: Small — 1 week

## Phase 3: Build Architecture

### 3.1 Build System Specification

**Goal**: Document the build architecture (NPC-007 gap 9).

**Components**:
- Toolchain requirements (Rust 1.75+, Python 3.10+, .NET 8)
- Build graph (dependencies between crates)
- Cross-compilation targets (x86_64, aarch64)
- Reproducible builds (deterministic output)

**Estimated scope**: Medium — 2 weeks

### 3.2 CI/CD Pipeline

**Goal**: Complete CI pipeline with all required gates.

**Components**:
- Rust crate builds + conformance gates ✅
- Python test suite ✅
- Cross-architecture testing ✅
- Package signing verification
- Release artifact signing

**Estimated scope**: Medium — 2 weeks

## Phase 4: Multi-Monitor Enhancements

### 4.1 Dynamic Output Detection

**Goal**: Handle hot-plug events (monitor connect/disconnect).

**Components**:
- `wl_output` event listener (geometry, mode, done, scale)
- Dynamic monitor list updates
- Window migration on output removal

**Dependencies**:
- `wl_output` binding ✅

**Estimated scope**: Small — 1 week

### 4.2 Per-Output Rendering

**Goal**: Render different content on each monitor.

**Components**:
- Output-specific surface creation
- Multi-surface rendering pipeline
- Workspace-to-output binding

**Estimated scope**: Medium — 2 weeks

## Timeline

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1-2 | 1.4 | SDL2 Wayland GPU-accelerated rendering |
| 3-4 | 2.1-2.2 | Installer signing + trust store |
| 5-6 | 1.1-1.2 | GBM buffers + DRM modesetting |
| 7-8 | 3.1-3.2 | Build architecture + CI |
| 9-10 | 4.1-4.2 | Multi-monitor enhancements |
| 11-12 | 1.3 | EGL integration + Vulkan prep |

## Success Criteria

| Metric | Target |
|--------|--------|
| Tests passing | 2,600+ |
| GPU-accelerated rendering | SDL2 Wayland path working |
| Package signing | Installer rejects unsigned packages |
| Build docs | Build architecture spec complete |
| Multi-monitor | Dynamic output detection working |

## Dependencies

| Dependency | Status | Notes |
|------------|--------|-------|
| Wayland compositor | ✅ Ready | For testing |
| libgbm-dev | Needed | For GBM buffers |
| Mesa/EGL | Needed | For EGL integration |
| PyNaCl | ✅ Installed | For package signing |
| SDL2 | ✅ Installed | For GPU-accelerated rendering |
