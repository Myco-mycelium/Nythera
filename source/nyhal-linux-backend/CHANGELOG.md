# Changelog

All notable changes to the Nyrqis Linux Backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.26.0] - 2026-09-08

### Changed

#### CI repairs (repo's first green CI on GitHub runners)
- **rust/wayland**: the crate's ABI was bumped to 0x0001_0200 (1.2.0) for multi-monitor support, but its unit test still asserted 0x0001_0100 — the crate had never been compiled on the dev host (no Rust toolchain), so CI was the only compiler and the job failed since 2026-09-05. Assertion updated; 27 crate tests pass.
- **test_backend**: `test_app_launch_creates_container` performs a real namespace spawn but lacked the `_direct_launch_supported()` probe guard every other real-launch test uses. On GitHub runners the kernel blocks the uid_map write (the probe's docstring has documented this exact limitation since the guard was written), so `Rust container FFI conformance (required gate)` had never passed there — every spawn in the job failed with `root map write: Operation not permitted`. The test now skips on such hosts like the PID-1-init tests do.

#### Compositor (rust/compositor, ABI 0.1.0 → 0.2.0)
- **Wire-format event loop added** (`rust/compositor/src/event_loop.rs`): the request-processing half of a real compositor event loop. Parses client→compositor requests from the standard Wayland wire format (8-byte header: object id, `size << 16 | opcode`), maintains the object table (wl_display fixed as id 1 per the protocol, created implicitly), and dispatches `wl_display.get_registry`, `wl_registry.bind`, `wl_compositor.create_surface`, `wl_surface.attach`/`frame`/`commit` into the crate-root surface state machine. Server→client events are encoded in the same wire format: `wl_registry.global` advertisements (5 globals), one-shot `wl_callback.done` on commit (stamped with the surface's commit count), and `wl_buffer.release` retirement.
- **New FFI**: `nyrqis_compositor_handle_client_data` (feed client bytes, returns bytes consumed or -1 on protocol error), `nyrqis_compositor_next_event` (drain the next outbound event; an oversized buffer keeps the event queued), `nyrqis_compositor_object_count`, `nyrqis_compositor_event_loop_last_error`. Exposed through `ui/compositor_codec.py` (`handle_client_data`, `next_event`, `object_count`, `event_loop_last_error`), whose ABI gate moves to 0x0000_0200.
- **Argument decoding is positional per request** (the wire format carries no type tags): requests read `u32`/string arguments through an `ArgReader` per the protocol layout — the earlier draft's try-string-else-int heuristic misparsed `wl_registry.bind` (whose first argument is an int) and was replaced before landing.
- End-to-end verified over FFI: a Python client sends get_registry → bind → create_surface → frame → commit (84 bytes), receives 5 globals + `wl_callback.done` (stamp = commit count 1), and the surface lands in the crate state machine with `commit_count == 1`.
- Crate tests 37 → **47** (59 consecutive clean runs after unifying the crate-wide test lock).

#### Codec stub audit (gbm/drm/egl/vulkan/wayland)
- Audited all five GPU/display codecs' stub modes for fail-closed posture: every crate-absent call returns an honest failure sentinel (`-1`/`False`/`None`; version functions return 0), `is_available()` reports the truth, and callers (`ui/wayland_display.py`) check `< 0` and fall back (PIL). No forgeable-success paths — no changes needed; the audit outcome is recorded here.

## [0.25.1] - 2026-09-08

### Changed

#### Auto-Remediation
- **`throttle` action implemented**: was a placeholder string; now lowers the container's cgroups v2 `cpu.weight` by 25% (floor 1) via `set_cpu_weight`, with a best-effort `nice` fallback (+5, capped at 19) on hosts without a writable `cpu.weight` (cgroups v1). Failure is reported honestly in the remediation history (`throttle failed: …`), not silently swallowed.
- **`migrate` action implemented**: was a placeholder string; now checkpoints the container (`_checkpoint_container`: stats + limits snapshot with a `ckpt-` id recorded under `_remediation.checkpoints`) and terminates it; restore is a later `spawn()` of the same container object. Closes the last two placeholder remediation actions in `configure_remediation`.

#### Package Signing (NPS-026 §6) — fail-closed, no forgeable stubs
- **`backend/package_signing.py` restored to the full NPS-026 API** (`SigningKeypair`, `sign_package`, `verify_package`, `PackageSignature`, `TrustStore`, `PackageSignError`, `HAS_NACL`), which a prior rewrite had silently dropped — `backend/installer.py` failed to import and `test_installer.py` / `test_package_integration.py` were broken at collection. Both suites pass again (17 + 11 tests).
- **Forgeable stub crypto removed**: the PyNaCl-absent fallback paths (deterministic keys, `sha256(b"stub-" + …)` "signatures") are gone. PyNaCl is a hard dependency (`pyproject.toml`); without it every operation raises `PackageSignError` — fail-closed per NPS-026 §6 and FIND-PACKAGE-001.
- **`backend/update_signing.py` verifies real Ed25519**: `verify_full_update` / `verify_delta_update` / `validate_rollback` now check the signature itself (covering package_id, versions, type, checksum) against the trust store's public key — previously *any* key present in the trust store was accepted regardless of signature. `re_sign_update` actually signs instead of only swapping the key_id. Delta signature verification now runs before patch parsing (a forged delta can't reach patch handling).
- **`tests/test_update_signing.py`** upgraded to a real Ed25519 trust-store fixture + new `test_verify_full_update_forged_signature` (a well-formed 64-byte forged signature is rejected as TAMPERED, not trusted).

#### Compositor (rust/compositor, ABI 0.1.0)
- **`nyrqis_compositor_process_input` implemented**: was a stub returning 0; now dispatches events onto per-surface bounded queues (256 events, oldest dropped) with a global dispatch counter.
- **`nyrqis_compositor_send_frame_callback` implemented**: records the delivery timestamp on the surface (`last_frame_time`).
- **`nyrqis_compositor_commit_surface` implemented**: bumps the surface's `commit_count` (wl_surface.commit) and delivers the frame callback on first commit.
- **New introspection FFI**: `input_queue_depth`, `total_input_dispatched`, `commit_count`, `last_frame_time`, all exposed through `ui/compositor_codec.py`.
- **Test-stability fix**: the compositor unit tests now serialize through a test lock — the parallel harness was interleaving multi-step FFI sequences against the shared global state (pre-existing flake, made probable by the new state-dependent tests). 12/12 clean runs.
- Crate tests 33 → **37**.

#### Live Installer
- **`nyrqis_live_install.py`**: fixed a dead-code bug — the completion summary only printed under an inverted condition (inner `if install_done` nested inside `if not install_done`), so the post-install summary never printed in interactive mode.
- **`ui/installer_gui.py`**: fixed a layout crash in `render_user_setup` at narrow render widths (side-panel width went negative → PIL `ValueError`); the panel is now clamped to fit.
- **New `tests/test_live_installer_smoke.py`** (6 tests): every InstallerStep has a registered, non-stub screen; text-mode install completes; auto-advance reaches 100%; GUI renders all 15 screens.

#### Tests
- 3 new tests in `TestAutoRemediation`: throttle without cgroups (failure reported honestly), throttle with a real temp-dir cgroup (weight 100 → 75), migrate (checkpoint record + TERMINATED state). Backend suite 2619 → **2622**; installer +17, integration +11, update-signing 10 → 11, live-installer +6.

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
