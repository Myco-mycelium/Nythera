---
title: Next Development Session Plan
version: 1.0.0
date: 2026-09-01
---

# Next Development Session Plan

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **2,587** (2,500 Python + 14 GBM Rust + 31 signing + 17 installer + 11 integration + 14 wayland) |
| CI status | All jobs passing (arm64-conformance ✅, rust-wayland ✅, ci ✅) |
| GBM crate | Scaffold + 14 tests, zero warnings |
| Package signing | Ed25519 + TrustStore + CLI + 59 tests |
| Wayland | Full integration (Rust crate + Python FFI + DesktopSession + multi-monitor) |

## Priority 1: SDL2 Wayland GPU Rendering (1 week)

**Goal**: Test and verify GPU-accelerated rendering through SDL2's Wayland backend.

**Why next**: The `SDLCompositor` already has `wayland=True` support. This is the fastest path to GPU-accelerated rendering without building a full GBM/DRM/EGL stack.

**Tasks**:
1. Test with a real Wayland compositor (Sway, weston, or mutter)
2. Verify EGL context creation via SDL2
3. Add rendering benchmarks (FPS, frame time)
4. Document the GPU-accelerated rendering path

**Blocker**: Needs a Wayland compositor for testing (not available in headless CI).

## Priority 2: Package Signing Integration (2 weeks)

**Goal**: Wire package signing into the installer so unsigned packages are rejected.

**Why next**: NPS-026 requires signed packages. The crypto primitives exist but aren't wired into the install flow.

**Tasks**:
1. Integrate `PackageSignature` into `nyrqisctl install`
2. Load trust store from `~/.nyrqis/trust-store.json`
3. Reject unsigned packages (NPS-026 §6.1)
4. Add `nyrqisctl trust add/remove` commands
5. Integration tests for the full signing + install flow

## Priority 3: Dynamic Multi-Monitor Support (1 week)

**Goal**: Handle hot-plug events (monitor connect/disconnect).

**Why next**: The `wl_output` binding is in place. Adding event listeners for geometry/mode/done events completes multi-monitor support.

**Tasks**:
1. Add `wl_output` event listener in Rust crate
2. Handle `output.geometry`, `output.mode`, `output.done`, `output.scale` events
3. Dynamic monitor list updates in Python
4. Window migration on output removal
5. Tests for hot-plug scenarios

## Priority 4: Build Architecture Specification (2 weeks)

**Goal**: Document the build architecture (NPC-007 gap 9).

**Why next**: Required for contributor onboarding and reproducible builds.

**Tasks**:
1. Toolchain requirements (Rust 1.75+, Python 3.10+)
2. Build graph (dependencies between crates)
3. Cross-compilation targets (x86_64, aarch64)
4. Reproducible builds (deterministic output)
5. CI/CD pipeline documentation

## Priority 5: DRM Atomic Modesetting (4 weeks)

**Goal**: Direct scanout of GPU buffers to display.

**Why next**: Enables direct hardware scanout without compositor overhead.

**Tasks**:
1. DRM ioctl wrapper for `DRM_IOCTL_MODE_ATOMIC`
2. CRTC/plane/connector enumeration
3. Frame presentation via DRM page flip
4. Buffer export to DMA-BUF
5. Integration with GBM crate

**Note**: This is a large effort best suited for a dedicated milestone.

## Deferred (Long-term)

| Item | Est. Effort | Notes |
|------|-------------|-------|
| EGL integration | 4-6 weeks | Requires Mesa/EGL drivers |
| Custom Wayland compositor | Large | Full Wayland protocol implementation |
| Vulkan rendering (ADR-0010) | Large | Per ADR-0010, Vulkan is native graphics API |
| `openat2` eBPF filter | Medium | Needs eBPF, not classic BPF |

## Success Metrics for Next Session

| Metric | Target |
|--------|--------|
| SDL2 Wayland GPU rendering | Verified with real compositor |
| Package signing | Installer rejects unsigned packages |
| Dynamic multi-monitor | Hot-plug events handled |
| Tests passing | 2,600+ |
