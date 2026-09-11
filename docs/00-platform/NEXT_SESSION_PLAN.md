---
title: Next Development Session Plan
version: 6.5.0
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

## Session 7 (2026-09-11) — release 0.29.0; design-language rollout phase 2.1 (compositor tokens)

| Item | Status |
|------|--------|
| Release 0.29.0 | ✅ Committed, tagged `v0.29.0`, pushed (main + tag); `live-iso` CI kicked off on the push |
| Phase 2.1 — compositor token layer | ✅ `NstudioDocument.design_tokens` (loader preserves `designTokens`; older loaders tolerate it) + `DESIGN_TOKENS` defaults in `ui/compositor`; merge at render time is **opt-in** — no tokens → pixel-identical render; radius applies to buttons/window frames, `surface.translucency` drives real alpha-blended taskbar translucency (crop-blend-paste); THEMES key sets unchanged (pinned by tests). 3 new tests (opt-in, radius merge, cross-document isolation); 27/27 compositor, 12/12 suites |
| Phase 2.2 — desktop.nstudio restyle | ✅ `shell/defaults/desktop.nstudio` carries the designTokens vocabulary (bar 0.85 alpha, radius tokens, 44-px targets under a 56-px bar, paired start-menu motion); ids/behaviors/bindings/locales preserved; fixture copy untouched. Verified through the real loader + token compositor (both screens render); 83 affected tests + 12/12 suites green |
| Next: phase 2.3 | The 16 finished apps, per the spec §7 adoption checklist, grouped: form-heavy → list/table → media/creative |

## Session 6 (2026-09-11) — ADR rot gates; live-demo ISO; design language; ep-limits loop-dispatch fix

| Item | Status |
|------|--------|
| Benchmark rot gates, all three close-out records | ✅ `tools/benchmark_gate.py` grew ADR-0007 (§31: ratio-curve flat, LZ4 fast-path premise, lossless round-trips) and ADR-0013 (§33: exact EEVDF re-derivation — request-size law, 10:1 share accuracy, RT-starvation row) alongside the existing ADR-0009 checks; 10 invariants, `--only adrNNNN` selector; CI benchmarks job + `run_tests.sh` |
| Real-daemon ep-limits bug found + fixed | ✅ The limiter ops resolved the manager off the attached server, but the Rust serving loop's dispatch handoff attaches services to a reply SINK — every `ep-limits` op failed on the packaged daemon while all unit tests (floor path) passed. `ControlService` takes an explicit `ipc_manager`; regression tests pin the sink wiring + a CLI-against-real-daemon e2e |
| `ep-limits metrics --watch` verified e2e | ✅ First real-daemon exercise: banner renders, Ctrl-C exits 0 |
| Design language | ✅ `docs/reference/design-language.md` (HIG structure + Material feel: tokens, motion vocabulary mapped to the NUI easing enum, 44 px targets, the Android-feel boundary); `shell/defaults/default-shell.nstudio` restyled as the reference implementation (designTokens, theme overrides, contract-valid properties only, paired enter/exit menu motion) |
| Live-demo ISO | ✅ `packaging/live/` — `build-live-iso.sh` (debootstrap/tarball/tree → zstd squashfs → hybrid UEFI+BIOS ISO), autologin `demo` user, `nyrqis-demo` session (daemon up, desktop attempt, capability probe printing exactly what the booted machine is missing); `live-iso` CI workflow builds + smoke-checks + publishes the artifact |
| Boot smoke (headless QEMU) | ✅ `tests/boot_smoke.py` — serial-console handshake (`NYRQIS_BOOT_SMOKE_PONG=1` = daemon answers ping); wired into CI with serial-log upload; pass/pong-fail/no-marker paths verified against a simulated QEMU. **Unverified: the first real chrooted build + boot (no sudo/xorriso/KVM on the dev host) — CI closes it** |
| Suite | ✅ 2,633 backend tests OK (full), 12/12 run_tests.sh suites |

### Carry-forward: design-language rollout (phase 2)

The spec + reference shell are done; the rest of the surface area is
not. Phased so each phase ships green independently:

1. **Compositor theme layer** — carry the tokens (spacing/radius/elevation/accent-as-action) into `ui/compositor`'s Eclipse/Solar renderers so `.nstudio` designs get token-driven polish for free; degrade per the renderer-honesty rule.
2. **`desktop.nstudio` (full shell)** — restyle the 30-component desktop: Dock + AppGrid + Launcher + window chrome to the §2/§4 tokens; note the tests/fixtures copy is pinned by schema tests and stays untouched.
3. **The 16 finished apps** — sweep per the spec §7 adoption checklist, grouped: form-heavy (settings, calendar, contacts-like) → list/table (package, process, network) → media/creative (paint, recorder, model viewer). Each group lands with its suite green.
4. **Contract additions where the language needs new properties** (e.g. `cornerRadius` on Button) — a deliberate `nui-api-v1.json` registry bump with back-compat notes, NOT ad-hoc properties; that is its own review.

## Session 5 (2026-09-10, later) — benchmark close-outs unblock 3 held ADRs; version-drift gate; vendor-agnostic GPU conformance

| Item | Status |
|------|--------|
| Version-drift CI gate | ✅ `tools/check_version_drift.py` — pyproject version must equal the newest CHANGELOG release (the 0.22.0-through-4-releases drift can't recur); new `version-drift` CI job |
| ADR-0007 close-out data | ✅ `tests/benchmark_adr0007.py` — real-asset zstd sweep (ratio flat ~1.07 at every level), real LZ4 fast path (2.7× zstd-1 at equal ratio; zlib approximation retired), concurrent scaling (2.2× @ 8 threads). BENCHMARK_RESULTS §31 |
| ADR-0009 close-out data | ✅ `tests/benchmark_bucket.py` — sweep (steady state ≈ refill; shipped default = 4.5% of capacity) + adversarial (shared bucket starves a 250 Hz client under flood → per-sender fairness is the missing mechanism). BENCHMARK_RESULTS §32 |
| ADR-0013 tuning data | ✅ `tests/benchmark_adr0013.py` — EEVDF discrete-event simulation: request size (not a slice knob) governs interactive latency, Linux-6.6 weight table recommended, RT reserve shown non-optional (100% RT starves fair class). BENCHMARK_RESULTS §33; BENCHMARK_PLAN §5 method |
| Vulkan FFI slot-leak fix | ✅ Found by the new conformance harness via test_boot_integration: the crate's destroy functions left `Some(..)` in the slot tables while `alloc_slot` only reuses `None` — 4 create/destroy cycles exhausted instances forever. All three destroys now `take()` the slot; crate tests 12 green |
| DRM driver identification | ✅ `query_driver()` (DRM_IOCTL_VERSION, native 64-byte layout) — identifies i915 1.6.0 "Intel Graphics" on this host; the vendor-matrix primitive |
| Vendor-agnostic GPU conformance | ✅ `tests/test_gpu_vendor_conformance.py` (9 tests) — identical assertions regardless of driver + matrix-row report; the suite AMD/NVIDIA hosts will run unchanged (M15 Phase 2 instrument; no AMD/NVIDIA hardware on this host, honestly skipped) |
| Suite | ✅ 6,139 passed + 38 skipped |

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
