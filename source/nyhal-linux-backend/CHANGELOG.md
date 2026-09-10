# Changelog

All notable changes to the Nyrqis Linux Backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Vulkan FFI slot leak** (`rust/vulkan`): the three destroy functions
  (`nyrqis_vulkan_destroy_instance`/`_device`/`_swapchain`) marked slots
  `active = false` but left `Some(..)` in the table, while `alloc_slot`
  only reuses `None` slots — after `MAX_INSTANCES` (4) create/destroy
  cycles in any long-lived process, every subsequent `create_instance`
  failed with "too many instances". (Found by the new vendor-conformance
  suite in the full-suite run: `test_boot_integration`'s boot-render
  import chain exhausted the table.) Destroy now `take()`s the slot;
  crate tests 12 green; verified 6 full create/destroy cycles reuse all
  4 slots.

### Added

- **DRM driver identification** (`ui/drm_backend.query_driver`):
  `DRM_IOCTL_VERSION` with the native 64-byte `drm_version` layout —
  identifies the driver under test (e.g. `i915 1.6.0 "Intel
  Graphics"`); the vendor-identification primitive the M15 Phase 2
  hardware matrix is built on.
- **Vendor-agnostic GPU conformance suite**
  (`tests/test_gpu_vendor_conformance.py`, 9 tests): the full
  GBM/EGL/Vulkan/DRM pipeline exercised through the shipped codecs with
  identical assertions regardless of which vendor's driver answers, plus
  a driver-matrix row report — the instrument M15 Phase 2 runs unchanged
  on AMD/NVIDIA hosts.
- **Benchmark close-out scripts**: `tests/benchmark_adr0007.py` (real-
  asset zstd sweep, real LZ4 fast path, concurrent compression),
  `tests/benchmark_bucket.py` (token-bucket sweep +
  adversarial interference), `tests/benchmark_adr0013.py` (EEVDF tuning
  simulation) — data and findings in `tests/BENCHMARK_RESULTS.md`
  §31–33; review packages for ADR-0007/0009/0013.

## [0.28.0] - 2026-09-09

### Added

#### Compositor presentation (M14 Priority 9 — surfaces to display)
- **`ui/compositor_presentation.py`**: the presentation half of the compositor. Committed client surfaces (SHM pixel buffers shared over the Wayland socket via memfd + SCM_RIGHTS) are mapped and alpha-blended onto an output-sized framebuffer in z-order (integer math, no PIL dependency), then presented through `DRMBackend` (DRM/KMS `DRM_IOCTL_MODE_SETCRTC`) when a DRM device is available; otherwise the frame is stored for inspection (headless/software fallback). Fail-closed: nothing presents a frame the compositor never produced.
- **wl_shm in the wire event loop (rust/compositor, ABI 0x0000_0200 → 0x0000_0300)**: `wl_shm.create_pool`, `wl_shm_pool.create_buffer`, and `wl_shm_pool.destroy` are parsed and dispatched — pool/buffer objects join the object table and buffers bind to surfaces through `wl_surface.attach`. Crate tests 48 → **49** (a wire-level test joins registry → bind → create_pool → create_buffer → create_surface → attach).
- **SCM_RIGHTS fd receipt (`ui/wayland_socket.py`)**: the socket now reads with `recvmsg` so out-of-band file descriptors — how Wayland clients share wl_shm pools — are received alongside bytes; a zero-byte read with ancillary fds is correctly not treated as a disconnect, and received fds are delivered to the host half via a `set_fd_callback` hook (`ui/compositor_host.py`).
- **Wired into `NyrqisCompositor`** (`ui/nyrqis_compositor.py`): host half + presentation start automatically; `get_stats()` reports presentation counters (frames composited, presented, fallback-stored).
- **New `tests/test_compositor_presentation.py`** (32 tests): SHM capture, z-order alpha blending, DRM present vs. software fallback, fd plumbing, fail-closed paths.

#### Package repository (NPS-026 §7)
- **`backend/package_repo.py`**: a package repository is a directory of published `.nypkg` payloads, delta payloads, and a single **signed index** that a client verifies *before* downloading anything. The index entry binds package id, version, checksum, and publisher key; the trust model is fail-closed and shared with package_signing/delta_update — verification requires a trusted key AND a valid Ed25519 signature over the canonical index bytes; a tampered or untrusted index is an error, never a warning. Entry tamper checks bind content checksums.
- **`nyrqisctl_repo.py`**: operator CLI — `publish-package`, `publish-delta`, `list`, `find`, `find-delta`, `verify-entry`, `remove`.
- **New `tests/test_package_repo.py`** (21 tests): publish/list/find/verify flows, tamper rejection, trust-model fail-closed paths.

#### UI applications completed to spec
- Sixteen desktop-app modules were finished to their tracked spec suites — **250 previously-failing tests now green; the full pytest suite stands at 6133 passing, 35 skips**: `db_client` (real CSV/JSON/Markdown export, FK lookup, table stats), `vm_manager` + `notes_app` + `process_manager` (CRUD, folders/tags/search, history, kill semantics), `packet_analyzer` + `network_monitor` (capture lifecycle, protocol stats, per-interface sparklines), `virtual_keyboard` (multi-layout keymaps, modifiers, history), `calendar_app` (view modes, event CRUD), `markdown_editor` (block parser, DocumentStats, tags), `password_manager` (generator kwargs, strength scoring, autofill), `screen_recorder` (recording lifecycle, presets), `disk_health` (SMART scoring; `health_status` is a str-subclass enum), plus `audio_mixer`/`font_manager` fixes (missing `@property`, pixel-buffer `render` + `render_lines` compat).
- Cross-suite spec conflicts resolved by adopting the newest convention (packet direction icons with VS16, FontManager pixel-buffer `render`) and updating the two stale assertions in older test files.

### Fixed

- **Compositor restart leaked resource slots (rust/compositor)**: `nyrqis_compositor_start` reset only the wire event loop's object table, not the crate's client/surface/output/input-queue tables. In a long-lived process (the test suite is exactly that — every `CDLL` handle shares one global state) outputs accumulated across start/stop cycles until `MAX_OUTPUTS` (16) was exhausted and `add_output` failed. `start` now tears down all previous-session resources, matching the documented "fresh protocol session" semantics.
- **`ui/audio_mixer.py`**: `AudioDevice.volume_bar` was defined without `@property` (returned a bound method instead of the rendered bar).
- **`ui/drm_backend.py` spoke no real DRM UAPI** (found by on-host hardware verification, not by the suite): the mode-query ioctls passed immutable `bytes` the kernel cannot write counts into (EFAULT — `detect_connectors()` silently returned `[]` on every real machine), GETCRTC/SETCRTC/GETCONNECTOR ioctl numbers encoded wrong struct sizes, and `set_mode(..., fb_id=0)` would have *disabled* scanout on a real master (fb 0 means "off"), never presented. Rewritten to the real UAPI per `include/uapi/drm/drm_mode.h`: correct ioctl numbers (GETRESOURCES 0xc04064a0, GETCRTC/SETCRTC 0xc06864a1/a2, GETCONNECTOR 0xc05064a7, ADDFB2 0xc06864b8, dumb-buffer ioctls, PAGE_FLIP 0xc01864b0), the two-call query protocol with user-space pointer arrays (`ctypes` arrays packed into the struct's `*_ptr` fields), full `drm_mode_modeinfo` parsing (preferred-mode detection), and a present path that allocates a dumb buffer → maps it → ADDFB2 (ARGB8888) → SETCRTC, refusing to send a scanout-disabling SETCRTC when no framebuffer can be created. Without DRM master every modeset fails honestly (kernel EPERM) and callers fall back.

### Verified on real hardware (0.28.0)

`verify_presentation.py` runs the presentation checks on the live machine: a read-only DRM probe (never modesets), real memfd SHM surfaces composited through the pipeline and checked byte-for-byte against the blend math (opaque copy + rounded over-operator), and the DRM-path posture check. Results on the Intel dev host (`/dev/dri/card1`): 2 CRTCs, 3 connectors, 3 encoders detected; connector 64 (CRTC 47, 5 modes) enumerated with real mode lists; composite output byte-exact; SETCRTC without DRM master refused by the kernel (EPERM) and honestly reported with software fallback — taking master from the live desktop session was deliberately not attempted. Wired into `run_tests.sh --gpu`.

### Changed

- `run_tests.sh`: compositor modes now include `tests.test_compositor_presentation`; full mode runs `tests.test_package_repo`.

## [0.27.0] - 2026-09-09

### Added

#### Compositor socket host half (M14 follow-on — the documented Priority 9 gap)
- **`ui/compositor_host.py`** (`CompositorHost`): the host side of the Wayland wire event loop, closing the "socket/epoll host half stays on the host side" follow-on recorded since the 0.2.0 wire loop landed. The transport (`WaylandSocketServer`: accept/read/write over a real Unix domain socket) now feeds every client's bytes through the Rust crate's wire-format event loop (`compositor_codec.handle_client_data`) and drains the crate's response events (`next_event`) back onto the socket — protocol parsing, the object table, and surface dispatch all run in the crate; Python owns only the socket.
- **Partial-message reassembly**: bytes are fed to the crate only on message boundaries (the host parses the 8-byte header's size field and holds back incomplete tails), so `recv()` fragmentation never reaches the parser as a truncated request.
- **Single-dispatch ownership**: when the wire loop is wired, the legacy Python dispatch inside `WaylandSocketServer` is skipped — it previously ran in parallel and double-responded (its hand-built registry globals are not wire-correct). With no crate, the Python path remains the fallback (fail-closed; the host fabricates no events in stub mode).
- **Session-scoped protocol state (Rust)**: `nyrqis_compositor_start`/`stop` now reset the event loop's object table and outbound queues. Previously the table persisted across sessions, so a compositor restart (or a second test) collided with stale object ids — a re-connecting client's `get_registry(new_id=2)` failed with "already in use". Also added `wl_display.sync` (opcode 0 → one-shot `wl_callback.done`), which every real client sends at startup. Crate tests 47 → **48**.
- **Wired into `NyrqisCompositor`**: the integrated compositor starts a crate session and bridges the socket automatically (engine "rust"); `get_stats()` reports host-half counters (engine, bytes in/out, events drained, protocol errors). The crate cdylib ships prebuilt for dev hosts; the engine degrades honestly to "stub" when absent.
- **New `tests/test_compositor_host.py`** (8 tests): full client handshake over a real socket (get_registry → bind → create_surface → frame → commit ⇒ 5 globals + frame-done on the wire), partial-message reassembly across sends, per-client isolation, disconnect cleanup, stub-mode fail-closed behavior, flush retention on writer failure. New CI job `compositor-host` (required gate) builds the crate and runs the suite; new CI job `rust-compositor` builds + unit-tests the crate, which **CI had never compiled** — the crate's 48 tests ran only on dev hosts until now.

#### Delta update generation (M14 Priority 9 — NPS-026 §6 generation half)
- **`backend/delta_update.py`**: `update_signing.py` verified full/delta/rollback updates but nothing produced them. New module: `diff_packages` (add/modify/remove ops between two payload directories, `.nypkg` layout normalized, deterministic order), `create_delta_update` (document with canonical checksum; optional Ed25519 signing with the same `package_id:version_from:version_to:delta:checksum` payload `UpdateVerifier._signature_payload` verifies), `delta_payload_bytes`/`save_delta_update`/`load_delta_update`, and `apply_delta_update` (signature verified BEFORE any filesystem mutation; path-traversal guard on every op; unsigned deltas refused when a trust store is supplied — fail-closed, no forgeable stubs).
- **Cross-verified against the shipped verifier**: `tests/test_delta_update.py` (18 tests) includes `TestDeltaPassesShippedVerifier` — a generated delta passes `UpdateVerifier.verify_delta_update` unmodified, and a tampered op list fails it. The two halves of NPS-026 §6 now close on each other, not just on their own fixtures.

### Changed

- `run_tests.sh` `--compositor` and full modes now include `tests.test_compositor_host`.

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
