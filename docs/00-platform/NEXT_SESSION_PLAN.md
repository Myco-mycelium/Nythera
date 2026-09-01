---
title: Next Development Session Plan
version: 1.0.0
date: 2026-09-01
---

# Next Development Session Plan

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **2,666** (2,500 Python + 31 signing + 17 installer + 11 integration + 21 SDL2 Wayland + 14 GBM + 19 wayland Rust + 10 GBM Rust) |
| CI status | 26/27 jobs passing (1 pre-existing container FFI flaky) |
| GBM crate | Scaffold + 14 tests, zero warnings |
| Package signing | Ed25519 + TrustStore + CLI + 59 tests |
| Wayland | Full integration (Rust crate + Python FFI + DesktopSession + multi-monitor + hot-plug) |
| SDL2 Wayland | 21 tests (headless, X11, Wayland fallback, render_to_wayland) |
| Build architecture | Documented (NPC-007 gap 9 satisfied) |

## Priority 1: SDL2 Wayland GPU Rendering (1 week) ✅ DONE

**Goal**: Test and verify GPU-accelerated rendering through SDL2's Wayland backend.

**Status**: ✅ Tested headless, X11, and Wayland fallback modes. 21 tests pass. Actual GPU rendering needs a real compositor (Sway/Weston) which requires sudo to install.

**Remaining**: Install Sway and test with a real Wayland compositor for GPU-accelerated rendering.

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

## Priority 3: Dynamic Multi-Monitor Support (1 week) ✅ DONE

**Goal**: Handle hot-plug events (monitor connect/disconnect).

**Status**: ✅ `OutputChange` enum + `check_output_changes()` FFI implemented. DesktopSession auto-syncs monitors after `poll_and_dispatch()`. Python bindings in `wayland_codec.py` and `wayland_display.py`.

**Remaining**: Full `wl_output` protocol listener (geometry/mode/done/scale events) for real-time updates without polling.

## Priority 4: Build Architecture Specification (2 weeks) ✅ DONE

**Goal**: Document the build architecture (NPC-007 gap 9).

**Status**: ✅ `docs/00-platform/BUILD_ARCHITECTURE.md` covers toolchain requirements, 14-crate dependency graph, cross-compilation, CI/CD, reproducible builds, testing strategy, and artifact layout.

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
| SDL2 Wayland GPU rendering | ✅ Headless/X11/fallback tested (21 tests) |
| Package signing | ✅ Installer rejects unsigned packages |
| Dynamic multi-monitor | ✅ Hot-plug detection via check_output_changes() |
| Build architecture | ✅ NPC-007 gap 9 satisfied |
| Tests passing | ✅ 2,666 |

## What's Left for Next Session

| Priority | Item | Est. Effort |
|----------|------|-------------|
| 1 | Install Sway and test GPU rendering end-to-end | 1 day |
| 2 | Full wl_output protocol listener (real-time events) | 1 week |
| 3 | DRM atomic modesetting (Phase 3) | 4 weeks |
| 4 | EGL integration + Vulkan prep | 4 weeks |
| 5 | Custom Wayland compositor | Large effort |
