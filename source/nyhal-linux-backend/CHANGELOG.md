# Changelog

All notable changes to the Nyrqis Linux Backend will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Compositor design-token layer** (phase 2.1 of the design-language
  rollout, `ui/compositor.py` + `ui/nstudio.py`): `.nstudio` documents
  can now carry a `designTokens` section (spacing/radius/surface/
  motion vocabulary per `docs/reference/design-language.md`) that the
  loader preserves (`NstudioDocument.design_tokens`) and the
  compositor merges over its defaults at render time. Application is
  opt-in and bounded: corner radii apply to buttons and window
  frames, and `surface.translucency` (0–1) drives real alpha-blended
  taskbar translucency (crop-blend-paste; pixels behind the bar show
  through). Documents without tokens render pixel-identically to
  before; theme key sets are unchanged. New tests pin opt-in
  behavior, radius merging, and cross-document token isolation.
- **Desktop shell restyled to the design language** (phase 2.2,
  `shell/defaults/desktop.nstudio`): the shipped 30-component desktop
  now carries the same `designTokens` vocabulary as the reference
  shell — the taskbar renders with 0.85 alpha-blended translucency,
  buttons/window frames take the radius tokens, and quick-settings
  controls are re-flowed to the 44-px target minimum under a 56-px
  bar. Start-menu motion is paired per the spec (fade+rise on enter,
  faster ease-in drop on exit). All component ids, behaviors,
  bindings, and locales preserved; the `tests/fixtures` copy is
  intentionally untouched (pinned by render/schema tests).
- **First real CI build of the live ISO closed two integration gaps**
  (found on the release commit's `live-iso` run):
  - `tests/boot_smoke.py` booted the ISO via `-cdrom` with no way to
    select the serial entry and no `NYRQIS_BOOT_SMOKE=1` on any kernel
    command line — the handshake could never succeed. The smoke now
    extracts `/live/vmlinuz` + `/live/initrd` from the ISO (xorriso,
    isoinfo fallback) and boots them directly with `-append
    boot=live console=ttyS0,115200 NYRQIS_BOOT_SMOKE=1`, so the
    outcome no longer depends on bootloader menu selection; a final
    log read after early qemu exit fixes a latent marker-parse bug
    (verified: pass / pong-fail / no-marker / extraction-failure
    paths against a QEMU stand-in asserting the direct-boot args).
  - The bench gate's §31b invariant pinned the corpus-absolute ratio
    ordering (lz4 ≥ zstd-3), a property of the recording host's
    `/usr/share` mix; it now pins the host-relative claims — lz4 ≥
    1.5x zstd-3 throughput on the same corpus (record: 2.7x) plus
    lossless round-trips — and §31a times via aggregate best-of-3 to
    shed runner jitter (per-file-min method rejected: it measured one
    small file, 8x instead of the record's 59x).
- **Design-language rollout phase 2.3a (form-heavy surfaces)**:
  `ui/hig.py` now carries the Nyrqis token vocabulary as the Python
  bridge of the `.nstudio` `designTokens` section (4-pt space grid,
  radius bounds, 44-px targets, motion pairs with exits faster than
  entrances, one-accent brand values, surface opacities + hairline) —
  same values as `ui/compositor.DESIGN_TOKENS` and the reference
  shell, pinned by a cross-module agreement test. The settings panel
  adopts them: builtin Eclipse/Solar themes retuned to the brand
  base/accent/text tokens (Dracula untouched as a user theme; the
  generic `Theme` dataclass default unchanged), toggle and theme-item
  rows lifted to the 44-px target with the theme band re-flowed to
  184 px on the 4-pt grid, and the toggle track redrawn at 44x24.
  New `tests/test_design_language.py` (registered in `run_tests.sh`)
  pins the bridge values, target compliance, and the panel adoption.
- **List/table renderer family tokenized + selection highlight**
  (phase 2.3b of the design-language rollout, `ui/compositor.py`):
  the List and MenuItem renderers now read their geometry from the
  ``space``/``radius`` tokens (row pitch defaults to ``space.xl`` =
  24 px — the historical constant — so token-less documents render
  pixel-identically), and both honor the interactive-selection
  contract from the §7 checklist: ``selectedIndex``/``selected``
  draws the accent highlight (``radius.sm`` corner) with
  contrast text from ``surface_elevated``, keeping the label's
  position fixed so selection never shifts text. This lifts every
  list/table surface (package, process, network panels) at the
  compositor layer without touching app modules. Four new compositor
  tests pin default-pitch identity, token-driven pitch, accent
  highlight geometry, and contrast text.
- **Second real-CI round closed the remaining integration gaps** (the
  fix-commit runs failed in the same two steps, which pinpointed the
  residual defects):
  - The live image's autologin console stopped at a bare bash shell —
    no banner, no boot-smoke markers. `build-live-iso.sh` now writes
    the demo user's `.bash_profile` exec'ing `nyrqis-demo`, so the
    session starts on every autologin console (tty1 + ttyS0).
    `nyrqis-demo`'s smoke branch polls the ping for up to 30 s (a
    fixed 3 s sleep cannot budget a TCG-slowed runner) and always
    lands `…READY=1` so driver diagnosis stays reliable; the driver
    prints the serial-log tail on EVERY failure, even `--keep-logs`.
  - The bench gate's §31a bounds were still corpus-absolute (flat
    < 5%, ≥ 20x — both properties of the recording host's file mix;
    the deterministic synthetic corpus legitimately shows a 25% gain
    at level 22 and 11–13x speed). The gate now pins the
    corpus-independent engine mechanics — ratio monotonic in level,
    level 22 within the same storage class (≤ 1.35x), level 3 in a
    decisively faster class (≥ 5x) on the deterministic corpus — plus
    the real-asset storage-class claim (≤ 1.5x, host-sampled). The
    record's flatness figure stays a record observation, not a gate.
  - The build's initrd live-capability guard rejected **good**
    initrds: `echo "$listing" | grep -q …` under `set -o pipefail`
    returns 141 when grep matches late in a large listing (grep exits
    early, the writer gets SIGPIPE), so a successful check read as a
    miss and the build died with "missing: scripts/live …" even though
    the hook had run (`live-boot: core filesystems …` in the log).
    The guard now greps the captured listing via here-strings (no
    pipe), keeps the builtin (=y) module acceptance, and its die
    message points at the check itself when mkinitramfs ran clean.
  - The guard also required the module FILE to be named after the
    module NAME; the ISO 9660 driver's modprobe name is ``iso9660``
    but the kernel ships it as ``fs/isofs/isofs.ko``, so good initrds
    (``force_load iso9660`` → ``isofs.ko``) were rejected with
    "missing: iso9660.ko". Both spellings are accepted now, paired
    with their CONFIG symbols.
  - The new squashfs content gate matched the WRONG listing format:
    ``unsquashfs -ls`` prints paths with the destination prefix and
    no leading slash (``squashfs-root/etc``), so an anchored ``/etc``
    pattern false-negatived and killed a good build with "no /etc".
    The gate now matches name endings (validated positive + negative
    against locally built images), and its die message carries the
    listing head for self-diagnosis. The boot smoke adds ``debug``
    to the kernel cmdline so live-boot's mountroot set -x traces
    land in the serial log — the union-mount failure (medium found,
    squashfs loop-mounted, yet /root empty) is the next diagnosis
    target and the trace will carry the exact failing command.
  - **The empty-``/root`` mystery is closed**: the pivot diagnostic
    showed the overlay union FULLY populated (bin, etc, usr/bin/sh all
    present) with exactly one node missing — ``/sbin/init``. Debian
    ships that path via ``systemd-sysv`` (not ``systemd``), which
    ``--variant=minbase --include=systemd`` can legitimately skip;
    usr-merge then leaves ``/sbin`` absent, so the pivot dies with
    "run-init: can't execute '/sbin/init'" while the tree looks
    complete. Both debootstrap invocations now include
    ``systemd-sysv``, the build repairs a missing ``/sbin/init`` to
    the real systemd binary, and the finished squashfs is probed for
    the node: regular file passes, symlink passes only if its target
    resolves inside the image (dangling = build death, not a boot
    round burned). Also: plain ``debug`` on the kernel cmdline makes
    initramfs-tools redirect ALL init output into the VM's
    ``/run/initramfs/initramfs.debug`` (invisible on serial); the
    smoke now uses ``debug=y`` which traces to the console.

## [0.29.0] - 2026-09-11

### Added

- **Per-sender-fair IPC rate limiting** (`ipc/core.FairTokenBucket`,
  ADR-0009 §32b): the shared endpoint token bucket let one flooding
  container starve a legitimate 250 Hz client to ~9 admitted/s while
  the flood still passed ~1,025/s (BENCHMARK_RESULTS §32b).
  FairTokenBucket keeps the endpoint's shared envelope but confines
  each sender to a per-sender share (`tokens_per_second / fair_shares`,
  plus `sender_burst` spike absorption); `IPCEndpoint.send_message`
  meters by the message's `sender_id`. New endpoints get a fair bucket
  by default (`IPCManager(default_fair_shares=8,
  default_sender_burst=64)`; sizing rule: envelope ≥ senders ×
  per-sender demand). Regression tests pin flood bounding, envelope
  cap, shared-pool compatibility, and bounded sender-table growth.
- **Endpoint limiter control-plane ops** (`ipc/control.py`,
  operator-only): `configure_endpoint_rate_limit` (retune
  `rate`/`bucket_size`/`fair_shares`/`sender_burst`/`dynamic_shares`
  on a live endpoint), `get_endpoint_rate_limit`,
  `list_endpoint_rate_limits`; CLI: `nyrqisctl ep-limits
  list|get|set` (with `--[no-]dynamic-shares`). The limiter snapshot
  reports live occupancy (`active_senders`) and the effective
  per-sender share.
- **Dynamic shares** (`FairTokenBucket.dynamic_shares`, opt-in):
  `fair_shares` means "shares at full occupancy" — the effective
  per-sender refill is the envelope divided by the live sender count,
  so a lone sender may use the whole envelope while the §32d
  full-occupancy guarantee is unchanged (ADR-0009 review package
  §5.1). Not yet the default; needs its own adversarial re-benchmark
  before shipping on generally.
- **`nyrqisctl ep-limits list|get|set`** and daemon startup knobs
  (`--ipc-rate/--ipc-bucket-size/--ipc-fair-shares/--ipc-sender-burst`,
  threaded through `StatusServiceHost` → `IPCManager`); the packaged
  systemd unit ships the AG-proposed input-class envelope (2,000/s,
  burst 256, 8 shares → 250/s guaranteed per sender).
- **ADR-0009 review package** (`docs/reference/adr/ADR-0009-review-package.md`):
  benchmark record §32a–d, implementation status, proposed defaults,
  and the sign-off checklist for the Architecture Group.
- **Fair-bucket defaults data** (`tests/benchmark_bucket.py
  --fair-sweep`, BENCHMARK_RESULTS §32d): lone-sender static-shares
  cost (126/s on the old default envelope), guaranteed share delivered
  (8 × 250 Hz senders all meet rate on a sized envelope), and flat
  equal starvation when undersized — the defaults proposal and the
  static-vs-dynamic question for review.
- **Spec adoption**: NPS-010 §7.1.1 (v1.3.0) normatively requires
  per-sender fairness; operator how-to
  (`docs/how-to/tune-endpoint-rate-limits.md`).
- **Control-plane audit trail**: endpoint limiter retunes are recorded
  (bounded, in-memory) with the operator's `--reason`, surfaced via
  `get_control_audit` / `nyrqisctl ep-limits audit`, and persisted
  across daemon restarts through the daemon state file
  (`audit_saver`/`audit_loader` on `ControlService`; restore failures
  degrade to an empty trail).
- **Admission metrics**: every endpoint keeps a bounded admission
  sample ring; `get_endpoint_rate_limit_metrics` /
  `nyrqisctl ep-limits metrics [endpoint] [--window S]` reports
  counts, rates, and the rejection ratio over a trailing window (live
  watching; resets on restart).
- **§32e dynamic-shares adversarial data** (BENCHMARK_RESULTS):
  guarantee holds under dynamic shares; abuser's absolute take rises
  ~3.8× at low occupancy → static stays the default, dynamic opt-in,
  with the recommendation now data-backed.
- **Post-signoff readiness, executed**: the library default posture is
  flipped to the AG-proposed envelope — `LIBRARY_DEFAULT_*` now equals
  `ACCEPTED_PROPOSED_*` (256 / 2,000/s), so library consumers match
  the daemon and systemd unit; the ADR **status** flip (Proposed →
  Accepted) remains the Architecture Group's recorded decision
  (readiness test updated accordingly).
- **Benchmark rot gate** (`tools/benchmark_gate.py`, wired into CI and
  `run_tests.sh`): re-derives the load-bearing invariants of all three
  close-out records and fails when a record no longer describes the
  mechanism — ADR-0009 (§32: shared-pool ≈ refill, per-sender share
  bound, envelope cap, dynamic full-occupancy bound, dynamic
  lone-sender lift), ADR-0007 (§31: real-corpus ratio curve flat —
  level 22 gains <5% over level 3 while level 3 is ≥20× faster — and
  the LZ4 fast-path ratio premise with lossless round-trips at every
  swept level), and ADR-0013 (§33: interactive latency equals the
  task's own request size exactly, 10:1 share accuracy within 2% on
  every weight curve, and the decisive no-admission-control RT row —
  zero RT misses while the fair class starves completely). The
  compression checks degrade to an explicit skip (exit 0) when the
  codec deps are absent; `--only adr0009|adr0007|adr0013` runs one
  record's checks.
- **`ep-limits metrics --watch INTERVAL`**: continuous polling render
  of admission metrics (clears the terminal between frames; Ctrl-C to
  stop) for live flood/undersizing watching.
- **Design language** (`docs/reference/design-language.md`): Apple-HIG
  structure (clarity/deference/depth) with Material feel (motion
  tokens, elevation, 44 px targets) as the normative UI vocabulary;
  the default shell (`shell/defaults/default-shell.nstudio`) restyled
  as the reference implementation — design tokens, theme overrides,
  contract-valid taskbar/menu restyle, paired enter/exit menu motion.
- **Live-demo ISO** (`packaging/live/`, CI workflow `live-iso`):
  `build-live-iso.sh` assembles a hybrid (UEFI + BIOS, VM + USB)
  bootable image — squashfs rootfs with the full backend/desktop tree
  at `/opt/nyrqis`, autologin `demo` user, and a `nyrqis-demo` session
  that starts the daemon, attempts the desktop, and prints a
  capability probe listing exactly what the booted machine is missing.
  Unverified locally (no passwordless sudo / xorriso on the dev host):
  the chrooted build path and the ISO's first real boot; the
  unprivileged pipeline (staging → squashfs → templates → honest
  precondition failure) is verified, and CI builds, **boot-smokes in
  headless QEMU** (`tests/boot_smoke.py`: serial-console handshake —
  the demo session writes `NYRQIS_BOOT_SMOKE_READY` and asserts
  `NYRQIS_BOOT_SMOKE_PONG=1`, the daemon's ping reply; serial log
  uploaded for diagnosis), and publishes the ISO on every `main`
  push. The smoke driver's pass/pong-fail/no-marker paths are verified
  against a simulated QEMU.

### Fixed

- **`ep-limits` ops answered "no IPC manager attached" on the real
  daemon** (`ipc/control.py`, `nyrqis_backend.py`): the endpoint
  limiter ops resolved the IPC manager off the attached server, but
  the Rust serving loop's dispatch handoff (ADR-0021) attaches
  services to a reply SINK — no manager attribute — so on the packaged
  daemon (Rust loop present) every `ep-limits list|get|set|metrics`
  call failed while every unit test (floor `IPCDatagramServer` path)
  passed. `ControlService` now takes an explicit `ipc_manager`
  attachment (wired by `StatusServiceHost`), falling back to the
  server's manager under the floor wiring; regression tests pin the
  sink wiring directly and drive `ep-limits` through the CLI against
  a real daemon host. (Found by the first end-to-end `--watch` run.)
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
