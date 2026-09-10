---
title: Next Development Session Plan
version: 6.4.0
date: 2026-09-10
---

# Next Development Session Plan

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **6,443+** (Python: 6,168 — 6,133 passing + 35 skipped — + Rust: 275 across 18 crates) |
| Rust crates | **18** (all built and verified) |
| Python codecs | **8** (wayland, gbm, drm, egl, vulkan, nstudio, compositor, shm) |
| GPU pipelines | **4** verified on real hardware (GBM, DRM, EGL, Vulkan) |
| Packaging | **pyproject.toml** + systemd units + install script |
| Shell designs | **2** (default-shell.nstudio, desktop.nstudio) |
| Init script | **nyrqis_init.py** (daemon → shell → session) |
| Render pipeline | **render_pipeline.py** (GBM + EGL + DRM connected) |
| Multi-monitor | **multi_monitor.py** (output detection, workspace binding, window migration) |
| SHM buffers | **shm_buffer.py** (memfd_create + mmap for Wayland surface content) |
| Wayland compositor | **nyrqis_compositor.py** (integrated socket + codec + render) |
| Wayland socket | **wayland_socket.py** (Unix domain socket for client connections) |
| Wayland protocol | **wayland_protocol.py** (encoder/decoder for wire format) |
| DRM backend | **drm_backend.py** (real kernel UAPI: two-call connector query, dumb-buffer ADDFB2, SETCRTC presentation, honest non-master failure) |
| Benchmarks | **benchmarks_full.py** + **benchmarks_software.py** (all display paths) |
| Compositor presentation | **compositor_presentation.py** (DRM-detect → DRMBackend attach → software fallback; frame lifecycle stats) |
| Package repository | **package_repo.py** (signed index, publish/verify/download) + **nyrqisctl_repo.py** CLI |

## Session 4 (2026-09-10) — v0.28.0 released: UI apps to spec, presentation + package repo, real DRM UAPI

### v0.28.0 Release (Tagged, GitHub release published)
| Milestone | Status |
|-----------|--------|
| 16 UI apps brought to test spec | ✅ packet_analyzer, virtual_keyboard, disk_health, calendar_app, markdown_editor, network_monitor, password_manager, screen_recorder, audio_mixer, font_manager + their test groups (6,133 Python tests passing, up from 2,532) |
| Compositor presentation half | ✅ `ui/compositor_presentation.py` — DRM-detect, DRMBackend attach, honest software fallback, frame lifecycle stats; end-to-end test suite |
| Package repository | ✅ `backend/package_repo.py` (signed index, publish/verify/download) + `nyrqisctl_repo.py` CLI |
| Rust compositor restart fix | ✅ `nyrqis_compositor_start` now tears down previous-session clients/surfaces/outputs (outputs previously accumulated until MAX_OUTPUTS exhausted in any long-lived process) |
| DRM backend rewritten to real UAPI | ✅ Query ioctls now use the kernel's two-call protocol with pointer arrays and correct struct-size ioctl numbers; presentation allocates a dumb buffer + ADDFB2 before SETCRTC (fb_id=0 would have disabled scanout); fails closed without DRM master |
| On-host hardware verification | ✅ `verify_presentation.py` (read-only DRM probe, byte-exact SHM compositing check, DRM-path posture; wired into `run_tests.sh --gpu`). On `/dev/dri/card1`: 2 CRTCs, 3 connectors, 3 encoders, connector 64 enumerated with 5 real modes, kernel EPERM without master handled honestly |
| Docs + CI | ✅ CI green on both pushes (29 jobs incl. rust-compositor + compositor-host gates); v0.28.0 annotated tag + GitHub release |

## Session 3 (2026-09-08) — CI green + compositor wire event loop

### First Green CI on GitHub Runners
| Item | Status |
|------|--------|
| rust/wayland ABI test | ✅ Fixed: asserted 0x0001_0100 after the multi-monitor bump to 0x0001_0200 (crate never compiled on the dev host — CI was the only compiler) |
| Container conformance gate | ✅ Fixed: `test_app_launch_creates_container` did a real namespace spawn without the `_direct_launch_supported()` probe guard; GitHub-runner kernels block the uid_map write (`root map write: Operation not permitted`), so the job had never passed there. Now skips like the PID-1-init tests. |
| Result | ✅ First green CI run in repo history (prior 568 runs red); push 6c6a48d |

### Compositor Wire-Format Event Loop (ABI 0.1.0 → 0.2.0)
| Item | Status |
|------|--------|
| `rust/compositor/src/event_loop.rs` | ✅ Standard Wayland wire-format request parser (object table, implicit wl_display id 1), dispatch of get_registry / bind / create_surface / attach / frame / commit into the crate state machine |
| Server→client events | ✅ `wl_registry.global` ×5, one-shot `wl_callback.done` on commit (stamped with commit count), `wl_buffer.release` retirement |
| FFI + Python | ✅ `handle_client_data` / `next_event` / `object_count` / `event_loop_last_error`; `ui/compositor_codec.py` ABI gate 0x0000_0200 |
| End-to-end | ✅ Python client → 84-byte wire stream → 5 globals + frame-done; surface lands in state machine with commit_count 1 |
| Tests | ✅ Compositor crate 37 → **47** (59 stable runs; crate-wide test lock unified) |

### Codec Stub Audit (gbm/drm/egl/vulkan/wayland)
| Item | Status |
|------|--------|
| Fail-closed posture | ✅ Already consistent: stub modes return honest failure sentinels (−1/False/None, version 0), `is_available()` truthful, callers fall back — no changes needed |

## Post-Plan Session (2026-09-08)

### Auto-Remediation Actions Completed
| Action | Status |
|--------|--------|
| `throttle` | ✅ Real: cgroups v2 cpu.weight −25% (floor 1), `nice` +5 fallback on v1, honest failure reporting |
| `migrate` | ✅ Real: checkpoint (stats + limits snapshot, `ckpt-` id) + terminate; restore = later spawn |
| Tests | ✅ 3 new (suite 2619 → **2622**) |

### Package Signing Fail-Closed (NPS-026 §6)
| Item | Status |
|------|--------|
| Full NPS-026 API restored | ✅ `SigningKeypair`/`sign_package`/`verify_package`/`PackageSignature`/`TrustStore` (a rewrite had dropped them; installer import was broken) |
| Forgeable stubs removed | ✅ Without PyNaCl everything raises `PackageSignError` — no deterministic keys, no hash "signatures" |
| `update_signing.py` real Ed25519 | ✅ Full/delta/rollback verify the signature itself; `re_sign_update` actually signs; forged 64-byte signature rejected (new test) |

### Compositor Stubs Implemented (rust/compositor)
| Item | Status |
|------|--------|
| `process_input` | ✅ Per-surface bounded queues (256, oldest-dropped) + global dispatch counter |
| `send_frame_callback` | ✅ Records delivery timestamp per surface |
| `commit_surface` | ✅ Per-surface commit counts; callback delivered on first commit |
| Introspection FFI | ✅ `input_queue_depth` / `total_input_dispatched` / `commit_count` / `last_frame_time` in `ui/compositor_codec.py` |
| Test stability | ✅ Parallel-harness interleaving fixed with a test lock; 37 tests, 12/12 clean runs |

### Live Installer
| Item | Status |
|------|--------|
| Completion summary bug | ✅ Dead-code condition fixed (summary now prints after install) |
| GUI narrow-width crash | ✅ `render_user_setup` side panel clamped (PIL ValueError at <1280px) |
| Smoke tests | ✅ New `tests/test_live_installer_smoke.py` (6 tests): all 16 steps registered, text mode completes, GUI renders all 15 screens |

## What's Been Completed This Session

### v0.23.0 Release (Tagged)
| Milestone | Status |
|-----------|--------|
| Python packaging (pyproject.toml) | ✅ 5 CLI entry points |
| Systemd integration | ✅ Backend daemon + desktop session units |
| Install script | ✅ System/user/dev modes |
| Unified init script | ✅ nyrqis_init.py boots daemon → shell → session |
| Compositor FFI | ✅ ui/compositor_codec.py with ABI gate |
| Default shell designs | ✅ shell/defaults/default-shell.nstudio + desktop.nstudio |
| GBM real hardware | ✅ Device → surface → buffer on Intel HD Graphics |
| DRM device auto-detect | ✅ Tries card0, card1, renderD128 |
| DRM ioctl fix | ✅ Corrected MODE_GETRESOURCES size (60 bytes) |
| Entry point tests | ✅ 8 new tests |
| Boot integration tests | ✅ 24 tests |

### Post v0.23.0 Session (19 commits)
| Milestone | Status |
|-----------|--------|
| Wayland compositor protocols | ✅ XDG shell, frame callbacks, output geometry, seat capabilities |
| Delta update signing | ✅ Full/delta/rollback verification |
| Init diagnostics | ✅ `nyrqis_init --diagnose` with 7 system checks |
| udev rules | ✅ DRM device access without root |
| GPU benchmarks | ✅ GBM/EGL/Vulkan/Compositor performance |
| nyrqis-ctl wrapper | ✅ Convenience CLI |
| GPU pipeline tests | ✅ 21 integration tests on real hardware |
| **EGL real hardware** | ✅ Real eglInitialize/eglChooseConfig/eglCreateContext via dlopen |
| **Vulkan real hardware** | ✅ Real vkCreateInstance via dlopen |
| **Render pipeline** | ✅ GBM + EGL + DRM connected |
| **Multi-monitor** | ✅ Output detection, workspace binding, window migration |
| **SHM buffer sharing** | ✅ memfd_create + mmap for Wayland surface content |
| **Wayland socket server** | ✅ Unix domain socket for client connections |
| **Wayland protocol codec** | ✅ Encoder/decoder for wire format messages |
| **DRM/KMS backend** | ✅ Connector detection + atomic modesetting |
| **Integrated compositor** | ✅ nyrqis_compositor.py combining all pieces |
| **E2E compositor tests** | ✅ 10 tests with mock Wayland client |
| **Software benchmarks** | ✅ PIL + raw pixel + SHM buffer baselines |
| **CHANGELOG.md** | ✅ Documents v0.14.0 through v0.23.0 |
| **getting-started.md** | ✅ Quick start, architecture, CLI, GPU, testing |
| **Release tag** | ✅ v0.23.0 tagged and pushed |

## What's Left

### Priority 1: Test with Real Hardware (COMPLETE)

**Status**: ✅ All verified on Intel HD Graphics
- GBM: device → surface → buffer (1920x1080 ARGB8888)
- DRM: device open with auto-detection
- EGL: display → config → context → make_current → swap_buffers
- Vulkan: instance → device → swapchain → acquire image

### Priority 2: Real GBM/DRM/EGL/Vulkan Integration (COMPLETE)

**Status**: ✅ All wired to real hardware
- GBM: Real gbm_create_device() and gbm_surface_create() via dlopen
- EGL: Real eglInitialize(), eglChooseConfig(), eglCreateContext() via dlopen
- Vulkan: Real vkCreateInstance() via dlopen
- DRM: Fixed ioctl number, auto-detect device paths

### Priority 3: Custom Wayland Compositor (COMPLETE)

**Status**: ✅ All implemented
- [x] wl_compositor, wl_shm, xdg_wm_base protocols
- [x] Input handling (wl_seat, wl_keyboard, wl_pointer)
- [x] Output management (wl_output)
- [x] Frame callbacks (wl_callback)
- [x] DRM/KMS backend for display output
- [x] Real Wayland socket for client connections
- [x] Surface buffer sharing via shared memory

### Priority 4: Package Update Signing (COMPLETE)

**Status**: ✅ Implemented
- Delta update signature verification
- Re-signing after local modifications
- Rollback signature validation

### Priority 5: Multi-Monitor Enhancements (COMPLETE)

**Status**: ✅ All implemented
- [x] Output-specific surface creation
- [x] Multi-surface rendering pipeline
- [x] Workspace-to-output binding
- [x] Window migration on output removal
- [x] Output hot-plug event handling (HotPlugMonitor with periodic DRM polling)

### Priority 6: Performance Benchmarks (COMPLETE)

**Status**: ✅ All display paths measured
- [x] GBM/EGL/Vulkan/Compositor performance metrics
- [x] Software rendering (PIL) baseline
- [x] Raw pixel operations baseline
- [x] SHM buffer operations baseline
- [x] SDL2 headless rendering (via sdl2_codec.py)

## Session 4 (2026-09-09) — Socket host half + delta generation

### Compositor Socket Host Half (M14 follow-on closed)
| Item | Status |
|------|--------|
| `ui/compositor_host.py` (`CompositorHost`) | ✅ Bridges `WaylandSocketServer` bytes ↔ Rust wire event loop via `compositor_codec`; drains response events back to the socket |
| Partial-message reassembly | ✅ Feeds the crate on message boundaries only — `recv()` fragmentation never becomes a truncated request |
| Single-dispatch ownership | ✅ Wire loop owns protocol dispatch when wired; legacy Python dispatch skipped (it double-responded); stub mode fabricates nothing (fail-closed) |
| Session-scoped protocol state (Rust) | ✅ `start`/`stop` reset the object table + queues — a restart no longer collides with stale object ids; `wl_display.sync` served |
| `NyrqisCompositor` wiring | ✅ Automatic bridge + `get_stats()` host counters (engine, bytes in/out, events, protocol errors) |
| Tests | ✅ `tests/test_compositor_host.py` (8): real-socket handshake, reassembly, per-client isolation, disconnect cleanup, stub fallback; CI `compositor-host` required gate |
| CI gap closed | ✅ `rust-compositor` job added — the 48-test crate had **never been compiled in CI** |

### Delta Update Generation (NPS-026 §6 generation half)
| Item | Status |
|------|--------|
| `backend/delta_update.py` | ✅ `diff_packages` (add/modify/remove, deterministic, `.nypkg`-normalized) + `create_delta_update` (canonical checksum, optional Ed25519) |
| `apply_delta_update` | ✅ Signature verified BEFORE filesystem mutation; per-op path-traversal guard; unsigned refused when a trust store is supplied |
| Cross-verified with the shipped verifier | ✅ Generated deltas pass `UpdateVerifier.verify_delta_update` unmodified; tampered op lists fail (`TestDeltaPassesShippedVerifier`) |
| Tests | ✅ `tests/test_delta_update.py` (18) |

## Next Priorities

### Priority 7: Real Hardware Testing (Week 1)
- Test on AMD Radeon GPU
- Test on NVIDIA (Nouveau driver)
- Test on ARM Mali GPU (Raspberry Pi)

### Priority 8: Wayland Client Compatibility (Week 2-3)
- Test with weston-simple-shm
- Test with weston-terminal
- Test with GTK4 applications
- Test with Qt6 applications

### Priority 9: Package Manager Integration (Week 4)
- Package signing with Ed25519 keys
- Delta update generation
- Repository management

### Priority 10: Desktop Environment (Week 5-6)
- Window manager integration
- Taskbar and system tray
- File manager
- Terminal emulator

## Timeline

| Week | Priority | Deliverable |
|------|----------|-------------|
| 1 | 1 | ✅ Test with real hardware (COMPLETE) |
| 1 | Packaging | ✅ pyproject.toml, systemd, install script (COMPLETE) |
| 1 | Init | ✅ nyrqis_init.py boot-to-desktop (COMPLETE) |
| 1 | GPU | ✅ GBM/DRM/EGL/Vulkan verified on hardware (COMPLETE) |
| 1-2 | 2 | ✅ Real GBM/DRM/EGL/Vulkan integration (COMPLETE) |
| 2-3 | Render | ✅ Render pipeline + multi-monitor (COMPLETE) |
| 3-4 | 3 | ✅ Custom Wayland compositor (COMPLETE) |
| 4-5 | 4 | ✅ Package update signing (COMPLETE) |
| 5-6 | 5 | ✅ Multi-monitor enhancements (COMPLETE) |
| 6-7 | 6 | ✅ Performance benchmarks (COMPLETE) |
| 8-9 | 7 | Real hardware testing |
| 10-12 | 8 | Wayland client compatibility |
| 13-14 | 9 | Package manager integration |
| 15-17 | 10 | Desktop environment |

## Success Criteria

| Metric | Target | Current |
|--------|--------|---------|
| Tests passing | 6,200+ | **6,133** (Python) + 275 (Rust crates) |
| GPU rendering | GBM/EGL/DRM/Vulkan path working on real hardware | ✅ Verified |
| Packaging | pip install + systemd | ✅ Implemented |
| Boot-to-desktop | nyrqis_init.py works end-to-end | ✅ Verified |
| Package signing | Update signing verified | ✅ Implemented |
| Multi-monitor | Per-output rendering working | ✅ Implemented |
| Benchmarks | All display paths measured | ✅ Implemented |
| Custom compositor | Automated CI testing | ✅ Implemented |
| Wayland clients | weston-simple-shm working | Pending |

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0020: Implementation languages and the platform boundary
- ADR-0026: Wayland display-server integration
- NPS-017: NyHAL Kernel Abstraction Layer
- NPS-026: Package signing (§6)
- M14 Plan: `00-platform/M14_PLAN.md`
- M15 Plan: `00-platform/M15_PLAN.md`
