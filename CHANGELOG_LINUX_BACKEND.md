---
title: Nyrqis Linux Backend Implementation Changelog
document_id: CHANGELOG-IMPL-001
version: 0.1.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-08-15
ai_assisted: true
---

# Nyrqis Linux Backend Implementation Changelog

> **Naming note (2026-08-12):** this changelog was originally kept under the
> project name *Nythera*. On 2026-08-12 the project was renamed to *Nyrqis*
> (CR-0035 — see `docs/00-platform/REBRAND_NOTICE.md`). Entries below dated
> before that date refer to the same project under its former name.

## [0.14.36] — 2026-08-17

### State scopes (NUI-SCHEMA §8.4)

- **`stateScopes` section.** A document may carry the five named state
  tables — `global` / `screen` / `component` / `session` /
  `persistent` — referenced as dotted `scope.key` names in
  expressions, conditions, bindings, and `$expr:` arguments. `global`
  is the named form of the flat `states` section: a bare reference
  resolves against `states` first, then `global`.
- **Scope-aware resolution.** `resolve_state`/`resolve_states` on the
  floor resolve dotted references through the declared tables (the
  flattened view: flat keys plus every scope's entries under their
  dotted names, flat wins on collision); `_state_known` gates
  conditions and bindings; expression validation is scope-aware
  (`_scoped_state_keys`).
- **Fail-closed at both import gates.** Unknown scope names and
  non-object tables are rejected (`stateScopes: unknown scope
  'bogus'` / `stateScopes: scope 'persistent' must be an object`),
  and dotted references to undeclared scoped keys are unknown-state
  errors — byte-identical messages between the floor and the Rust
  crate (differential-tested); Nyforge mirrors the section check as
  ER-NUI-023 and threads scope-awareness through its expression,
  condition, and binding checks before Preview.
- **Proven with the shell:** `desktop.nstudio` declares `persistent`
  (theme) and `session` (clockTime) scopes; the theme toggle is an
  `if` expression over the persistent theme and the DND title formats
  the session clock.
- New `TestStateScopes` (13) + 2 differential conformance cases. Suite
  619 → **635**.

## [0.14.35] — 2026-08-17

### Declarative animations (NUI-SCHEMA §8.3)

- **`animations` section.** A document may carry a list of declarative
  animations — unique `id`s, a `target` that must name an existing
  component (optional — defaults to the triggering component), a
  non-empty `property`, and validated timing: `duration`/`delay`/`repeat`
  non-negative integers, `easing` one of linear / ease-in / ease-out /
  ease-in-out / steps, `direction` one of forward / reverse / alternate
  (defaults: 300 / 0 / 0 / ease-in-out / forward).
- **`Nyrqis.Animation.Play`** — a new Nyrqis API Registry system action
  (its only argument is `animation`): a behavior triggers a declared
  animation by id, and the reference is validated fail-closed at both
  import gates (byte-identical messages, differential-tested). The
  Rust crate mirrors the section validation and reference check;
  Nyforge mirrors it as ER-NUI-022 before Preview.
- **Proven with the shell:** `desktop.nstudio` declares a
  `start_menu_fade` animation (opacity, 200 ms, ease-out) and
  `behavior_start_toggle` plays it via `Nyrqis.Animation.Play`.
- New `TestAnimations` (10) + 4 differential conformance cases. Suite
  604 → **619**.

## [0.14.34] — 2026-08-17

### The NUI expression language (NUI-SCHEMA §7.2)

- **`ui/nexpr.py`** — the deterministic NUI expression language: a
  recursive-descent parser for `state.name` references, comparisons,
  `&&`/`||`/`!`, and the `if`/`min`/`max`/`contains`/`format` functions,
  with position-tagged syntax errors (byte offsets, so messages are
  stable across implementations).
- **`$expr:` values** in component properties, reusable overrides, and
  action arguments — and condition `expression` fields (which supersede
  the legacy `state`/`operator`/`value` equality form) — are validated
  fail-closed at **both import gates** and evaluated against document
  state at resolution time (`resolve_action` / `resolve_condition`).
- **Rust mirror** — `rust/nyui/src/nexpr.rs` is a byte-for-byte
  behavioral mirror of the floor's parser (same grammar, same error
  messages); the conformance gate differential-tests the two and reports
  the same first failure. The crate now enforces expressions in
  conditions, arguments, properties, and overrides.
- **Proven with the shell:** `desktop.nstudio`'s DND condition is now
  `state.doNotDisturb == true` and its notification title is
  `$expr:format(state.clockTime, "{0}")` (resolves to the clock time).
  NyForge mirrors the gate as `ER-NUI-021` (before Preview).
- New `TestExpressions` (13) + 5 differential conformance cases. Suite
  585 → **604**; crate 9 → 13 unit tests.

## [0.14.33] — 2026-08-17

### Resources — the managed asset catalog (NUI-SCHEMA §8.2)

- **`resources` section.** A document may carry `{"assets": [{id,
  kind, path, sha256?}]}`; ids are unique, `kind` must be one of
  image/svg/icon/font/audio/video/material/animation, `path` is
  non-empty, and `sha256` (optional) must be a 64-char hex string —
  validated by both gates.
- **`$asset:id` references** in component properties and reusable
  overrides must name a declared resource — fail-closed at both import
  gates with byte-identical messages (new differential tests).
- **Shell fixture proves it:** `desktop.nstudio` declares a
  `wallpaper` image asset and the DesktopSurface's `wallpaper`
  property references it via `$asset:wallpaper`. Suite 573 → **585**
  (new `TestResources`, 8 tests + 4 conformance).

## [0.14.32] — 2026-08-17

### Localization — `$localize:key` through the document's locales (NUI-SCHEMA §8.1)

- **`locales` section.** A document may carry
  `{"active": "en", "tables": {"en": {"key": "text"}, ...}}`; the
  active locale must have a table, and tables must map string keys to
  string values (validated by both gates).
- **`$localize:key` references** in component properties, reusable
  overrides, and behavior action arguments must exist in the ACTIVE
  locale's table — fail-closed at both import gates with byte-identical
  messages (new differential tests). `resolve_text()` resolves them
  (missing keys stay literal — fail-soft at resolution; the gate
  already rejected them).
- **Shell fixture proves it:** `desktop.nstudio` carries en/af tables;
  the search button's label and the DND notification message are
  `$localize:` references, verified resolving to "Search" and
  "Notifications paused until disabled" (and to the af strings when the
  active locale switches). Suite 562 → **573** (new `TestLocalization`,
  7 tests + 4 conformance).

## [0.14.31] — 2026-08-17

### Responsive layout constraints (NUI-SCHEMA §4.1)

- **Constraint fields on `layout`.** `anchorLeft/Right/Top/Bottom`
  (booleans, all default false), `minWidth`/`maxWidth`/`minHeight`/
  `maxHeight` (non-negative ints, `min* <= max*`), and `aspectRatio`
  (positive number). Both import gates validate them with byte-identical
  messages (new differential tests).
- **`resolve_layout()`** applies them — the runtime-facing adaptation:
  both horizontal anchors stretch the width (`container_w - 2*x`,
  clamped); a single `anchorBottom` docks from the bottom (`y` is the
  bottom inset); `aspectRatio` derives the non-stretched axis.
  `text_preview()` (the stand-in renderer) now shows adapted bounds.
- **Shell fixture proves it:** the `desktop.nstudio` taskbar stretches
  full-width and docks to the bottom with min/max bounds; a desktop
  icon carries `aspectRatio: 1.0`. Suite 546 → **562** (new
  `TestResponsiveLayout`, 12 tests + 4 conformance).

## [0.14.30] — 2026-08-17

### Reusable component masters — `components[]` is no longer reserved (NFS-006 §9)

- **Masters + instances.** A document's `components[]` now holds reusable
  masters (validated like any component); a node referencing one
  declares `componentRef` + `overrides` and **omits `type`** (both gates
  reject an instance that declares its own type — a type conflicting
  with the master is a bug). Overrides must be properties the master's
  type contract declares.
- **Both import gates enforce it.** The Python floor resolves the
  instance's contract from the master and validates refs/overrides;
  the Rust crate does the same (serde + pass), with byte-identical
  error messages (differential-tested). The `desktop.nstudio` shell
  fixture builds its taskbar from one `TaskbarButton` master with two
  `componentRef` instances.
- **Nyforge side:** `NuiComponent` serializes `componentRef`/`overrides`;
  `ReusableComponentResolver` materializes an instance as the master's
  clone + overrides + instance children. FEATURE_STATUS `ComponentReuse`
  → implemented (suite 541 → **546**; Nyforge 71/71).

## [0.14.29] — 2026-08-17

### Typed property metadata in the registry (NFS-006's reserved fields)

- **`properties` become metadata objects.** Every property in
  `nui-api-v1.json` now carries `name`/`type`/`default`/`bindable`/
  `required`, plus `min`/`max`/`enumValues`/`units` where meaningful
  (Slider value 0–100, Taskbar position enum, MediaPlayer position is a
  number not an enum — component-specific typing preserved). The
  vocabulary (names) is unchanged.
- **All three consumers parse the richer shape.** The Python floor
  extracts names for validation; the Rust crate's serde structs carry
  the full `PropertyDefinition` (name + typed metadata); Nyforge
  regenerates both `ComponentContracts.cs` (names) and a new
  `PropertyDefinitions.cs` (typed metadata for the Inspector). The
  conformance gate (floor vs crate, byte-identical) passes unchanged.

## [0.14.28] — 2026-08-17

### Widgets + OSD + login — third reference shell screen; registry 63 → 66

- **`WidgetHost`, `OSD`, `Login` join the registry** — three more Shell
  components (66 total) for the remaining Phase F pieces, each with a
  real semantic contract (`AddWidget`/`RemoveWidget` on WidgetHost,
  `Open`/`Close`/`Dismiss` on OSD, `Submit`/`Cancel` on Login). All
  three consumers regenerated: floor at import, crate at compile, Nyforge
  C# from the vendored copy.
- **`widgets.nstudio` joins the fixtures** — the widgets screen
  (WidgetHost holding Clock + System Monitor cards), the OSD screen
  (volume OSD with a `$state:`-substituted message + slider), and the
  login screen (Login with username/password inputs and submit/cancel
  wiring). 19 components, 5 behaviors, 2 bindings across 3 screens.
- Validated by floor + crate (differential) and opens in Nyforge itself.
  Suite 540 → **541**.

## [0.14.27] — 2026-08-17

### The window system + power UI — second reference shell screen

- **`windows.nstudio` joins the fixtures** — the window-system and
  power-UI shell screens: WindowFrame + WindowControls drive
  component-targeted actions (`Minimize`/`Maximize`/`Close` on the
  frame), stacked windows (Vault behind Files) with a toolbar and lists,
  and a PowerMenu with Sleep/Restart/Shutdown wired to system
  notifications. 21 components, 8 behaviors, 1 binding across 2
  screens.
- Validated by floor + crate (differential) and opens in Nyforge itself
  (serializer test). Suite 539 → **540**.

## [0.14.26] — 2026-08-17

### The real desktop shell screen — authored with the shell vocabulary

- **`desktop.nstudio` joins the fixtures** — the first reference shell
  screen built from the 0.14.25 vocabulary: a 1440×900 desktop with
  DesktopSurface + DesktopIcons, a Taskbar (Start/Search buttons, pinned
  apps List, WorkspaceSwitcher, clock, SystemTray), StartMenu, Search,
  CommandPalette, NotificationCenter, QuickSettings (Wi-Fi/DND toggles,
  volume slider, Eclipse/Solar theme buttons) — plus a second `lock`
  screen with a LockScreen component. 30 components, 8 behaviors, 6
  bindings across 2 screens.
- Behaviors exercise the shell actions (`StartMenu.Toggle`,
  `CommandPalette.Open`, `DesktopIcon.Launch`) and the conditional DND
  notification, all through the import gate.
- `desktop.nstudio` is validated by the floor and the Rust crate
  (differential, byte-identical) and opens in Nyforge itself
  (serializer round-trip test on the editor side). Suite 538 → **539**.

## [0.14.25] — 2026-08-17

### The first real Shell component set lands in the registry

- **63 components (was 29).** The Nyrqis API Registry
  (`ui/contracts/nui-api-v1.json`) grows by 34 components across five
  new categories — **Shell** (DesktopSurface, DesktopIcon, Taskbar,
  StartMenu, SystemTray, NotificationCenter, QuickSettings,
  WorkspaceSwitcher, WindowFrame, WindowControls, ContextMenu,
  CommandPalette, Launcher, Search, PowerMenu, LockScreen,
  Application), **Data** (List, ListItem, DataTable, TreeView, Menu,
  MenuItem), **Form** (Form, DatePicker, TimePicker, FilePicker,
  SettingsPanel), **Media** (Video, Audio, MediaPlayer) and
  **Developer** (Terminal, CodeEditor, LogViewer).
- Each carries a real semantic contract — e.g. `Taskbar` declares
  `position`/`alignment`/`autoHide`/`pinnedApps`/`runningApps`/
  `showClock`/`showTray` and `WindowFrame` declares
  `Minimize`/`Maximize`/`Restore`/`Close` actions — so Nyforge's palette
  and Behaviors dropdowns know what the shell actually supports.
- All three consumers pick it up automatically: the Python floor loads
  the file at import, the Rust crate embeds it at compile time (a
  vocabulary change that isn't compiled in is a build failure), and
  Nyforge regenerates its C# tables. The import-gate tests that used
  `Taskbar` as their "unknown type" example were switched to
  `BogusWidget` — the old example became real. Suite stays **538**.

## [0.14.24] — 2026-08-17

### The Nyrqis API Registry — one machine-readable contract, three consumers

- **`ui/contracts/nui-api-v1.json` — the registry lands.** The NUI
  component vocabulary (29 components, 6 system actions) now lives in a
  single machine-readable file instead of three hand-maintained tables.
  The Python floor (`ui/nstudio.py`), the Rust crate (`rust/nyui`), and
  Nyforge's C# tables all derive from it; the crate embeds the same file
  via `include_str!`, so a registry change that isn't compiled in is a
  build failure, not silent drift.
- **Python floor migrated.** `COMPONENT_CONTRACTS` / `SYSTEM_ACTIONS` are
  loaded from the registry at import time (missing/malformed registry is
  a hard import error — never silently empty tables).
- **Rust crate migrated.** `serde` derive + `OnceLock<Registry>` parse the
  embedded registry on first use; the const `CONTRACTS`/`SYSTEM_ACTIONS`
  tables are gone. 9/9 unit tests pass; release build clean.
- **Conformance intact.** `TestNstudioCodecConformance` (floor vs crate,
  byte-identical messages) passes unchanged — the two consumers read the
  same file, so behavior cannot diverge.
- **Nyforge derives from the same registry.** The editor's
  `ComponentContracts.cs`/`NuiSystemActions.cs` are regenerated from a
  vendored copy by `tools/generate_contracts.py`, with a CI drift gate.

## [0.14.23] — 2026-08-16

### NUI follow-on — the import gate over the control plane, a second screen, and §30

- **`ui/service.py` — `NuiService`, the operator NUI import gate over the
  IPC control plane.** `nui_validate` runs the gate and returns a summary
  (schema version, engine — `rust` or `python` — screens, component /
  behavior / binding counts); `nui_load` validates AND persists the
  design as the daemon's shell UI (`<state-dir>/ui/shell.nstudio`),
  re-importable on the next load (round trip). Both ops are
  operator-only — a registered container is refused — with a per-call
  document budget (`NUI_DOCUMENT_MAX_BYTES`, ~60 KiB datagram budget
  minus envelope) and unknown-op rejection. Wired into the daemon's
  `ServiceRouter` under `service: nui`.
- **`nui_current` — the loaded-design surface.** `NuiService` gains the
  read op `nui_current` so the daemon can answer "what shell UI is
  loaded?": `loaded: false` before any design is persisted (honest, not
  an error), or the persisted design's summary re-imported through the
  gate on every call, plus its path. A persisted design that no longer
  re-imports cleanly is reported as `loaded: true, valid: false` with
  the validation message — the operator sees the stale design instead
  of a silent failure.
- **`nyrqisctl nui`** — `nui validate <file>` (gate only),
  `nui load <file>` (gate + persist), and `nui current` (loaded-design
  query) with the usual `--socket`/`--timeout` flags and human-readable
  summary output; a real end-to-end CLI run drives a live daemon with
  the **Rust crate as the engine** — `nui current` before any load
  reports "no shell design loaded", and after `nui load` reports the
  persisted design's summary.
- **Second NyForge screen — Security Center.** `security-center.nstudio`
  joins `tests/fixtures/nstudio/` (71 components, 4 behaviors, 1
  binding — lockdown Toggle bound to state, conditional lockdown
  behavior, posture `$state:` substitution) and the fixture lists in
  `TestNstudioImport` + `TestNstudioCodecConformance`.
- **Third NyForge screen — Vault Workspace.** `vault-workspace.nstudio`
  joins the fixtures (71 components, 4 behaviors, 1 binding —
  auto-snapshot Toggle bound to state, conditional pause behavior,
  sync `$state:` substitution, volume list with quota indicators) with
  shape + `$state:` resolution tests.
- **§30 benchmark — the NUI import gate A/B.** `--nui` measures the
  parse+validate gate on the security-center fixture, Python floor vs
  Rust crate in the same process: crate **~2.1× faster at the median**
  (242 µs vs 502 µs p50) with ~1/3 the variance — evidence for
  ADR-0025's ADR-0020 performance claim. Results in §30 of
  `tests/BENCHMARK_RESULTS.md`.
- **Tests.** `TestNuiService` (12 tests: operator validate/load happy
  paths, bad design, wrong version, oversized document, load without a
  state dir, container refusal, unknown op, and the four `nui_current`
  cases) plus the security-center and vault-workspace shape +
  `$state:` resolution tests — suite 524 → **538**.

## [0.14.22] — 2026-08-16

### NUI (.nstudio) runtime consumption — the UI import gate (ADR-0025)

The Nyrqis side of the NyForge ↔ runtime pipeline lands: the runtime can
now import, validate, and render the `.nstudio` documents NyForge
produces (NFS-001). ADR-0025 documents the decision; the first increment
is implemented and gated the same day.

- **`ui/nstudio.py` — the pure-Python reference floor.** Parses and
  validates `.nstudio` documents against the NUI contract tables
  (component vocabulary NFS-001 §4, per-type property/event contracts and
  system actions §5, behavior/binding references §7–§8), with the strict
  schema-version gate of §9 (`NstudioVersionError` on an unsupported
  version). Also provides `$state:` argument substitution (§7.1),
  `resolve_action()`, a layout `render()` (absolute coordinates), and a
  deterministic `text_preview()` stand-in renderer.
- **`rust/nyui/` — the Rust import gate (ABI 1.0.0).** Parse/validate in
  Rust behind a versioned FFI (`nyrqis_nyui_validate` / `_version` /
  `_last_error`; caller-supplied input, zero Rust-side allocation),
  mirroring the seccomp/transport/ipcd migration pattern. Deps: serde_json
  only. 9 crate unit tests.
- **`ui/nstudio_codec.py` — the FFI loader.** Same contract as the other
  crate loaders: `$NYRQIS_RUST_LIB` → crate `target/release/` →
  `LD_LIBRARY_PATH`, ABI check, `NYRQIS_RUST_FORCE=1` semantics, and
  error-class mapping back to the floor's exception hierarchy.
- **Fixtures + tests.** The four NyForge example designs (forge-home,
  settings-app, vault-dashboard, **nyrqis-shell** — the 1440×900 shell UI
  draft) are fixtures under `tests/fixtures/nstudio/`. `TestNstudioImport`
  (floor: parse, gates, substitution, render, preview) and
  `TestNstudioCodecConformance` (differential: the crate rejects exactly
  what the floor rejects, error messages byte-identical on single-issue
  documents) — 32 new tests, all green on the crate path.
- **CI.** `rust-nyui` builds/tests the crate; `rust-nyui-conformance`
  forces the two classes through the FFI (required gate), matching the
  established per-crate gate pattern.

## [0.14.21] — 2026-08-16

### Wire-level streaming — the ADR-0024 follow-on lands

- **STREAM_CHUNK is now a first-class wire message type (5) in the
  codec** on BOTH halves (rust/ipc + `ipc_codec.py`, byte-identical,
  differential-gated) — the envelope rides the payload field:
  `version(1) ‖ stream_id(6B) ‖ call_id ‖ index(u32) ‖ count(u32) ‖
  payload ‖ sha256(payload)`; the codec's `reply_to` field carries the
  correlation on every chunk, so the existing correlation machinery is
  untouched.
- **The floor transport reassembles STREAM_CHUNK CALLs** with the
  ADR-0024 bounds (≤512 chunks / 16 MiB, 30 s TTL sweep, per-chunk
  SHA-256 verified before dispatch, stream bound to its first chunk's
  sender — a chunk from another sender fails even with a matching id)
  and **chunks large REPLYs** (`build_reply_wires` — the framing
  boundary is the single-datagram budget, not the chunk size, so
  service-level stream pieces of ≤32 KiB of data (~44 KiB of JSON)
  still ride ONE datagram and old peers are not broken). The client
  gains wire-level streaming (`wire_stream=True`: chunked send +
  chunked-reply reassembly) on the floor path.
- **The Rust serving loop (rust/ipcd) reassembles too** — the crux:
  the daemon's service socket is loop-served in production, so a
  floor-only version would pass tests but silently not stream in
  production. The loop accepts type 5, verifies each chunk's SHA-256
  (sha2, the precedent the nyfs crate established), rebuilds the CALL
  wire into pending, and routes chunked reply wires without consuming
  the pending entry (only the final REPLY reaps it). 24 crate unit
  tests incl. envelope parse + per-sender slotting.
- **The service accepts the wire-stream budget**: the plain write/read
  paths take payloads up to the wire-stream DATA budget (the 32 KiB
  per-call cap is now a config bound on the stream path, not a
  protocol one) and `volume_open` advertises `stream_ver: 2`; the
  service-level envelope stays for old peers, and a passthrough that
  never sees the advertisement keeps paging. A client payload beyond
  the 512-chunk reassembly window is refused immediately (fail fast
  instead of pipelining a stream the receiver is bound to drop).
- **Measured (§29, unchanged conclusions)**: the wire-level path
  carries the same 5.6×/6.6× write speedup end-to-end through a real
  server on both floor and loop paths.
- New `TestWireLevelStreaming` (8 tests incl. wire-streamed
  write/read through a real server and the budget rejection),
  `test_streamed_storage_call_through_rust_loop` (loop e2e), and the
  differential + Rust unit coverage. Suite 479 → **492**.

### Transport close-race fix (the flake the wire-level path exposed)

- **`IPCDatagramServer.close()` now joins the serve loop before
  releasing the socket.** Tearing a server down with
  `stop.set(); close()` used to leave the serve thread mid-`poll`;
  the next socket bind could reuse the freed fd number, and the stale
  poll would steal ONE datagram from the new socket. That bit the
  wire-level streaming path directly: one lost STREAM_CHUNK left the
  live server's reassembly one chunk short, so a large write never
  completed and the caller timed out (intermittent — reproduced at
  ~50% in the harness, zero warnings fired). `close()` now sets the
  closed flag, the loop notices within one poll window, exits, and
  closes the endpoint with no poll in flight — the socket path is
  unlinked before `close()` returns (a new server can rebind the same
  path immediately). The serve loop also returns immediately when
  called on an already-closed server instead of spinning. New
  regression tests: close joins the loop before releasing the socket,
  rebinding the same path succeeds, serve-after-close returns, and a
  wire-streamed write completes after a sibling server is torn down
  the exact way the suite does it.

## [0.14.20] — 2026-08-16

### The streaming data plane — ADR-0024 first increment

- **A large write/read rides ONE pipelined stream instead of N
  sequential CALLs** (ADR-0024 first increment). A logical write
  larger than the 32 KiB per-call cap is split client-side into
  ≤32 KiB chunks, each an ORDINARY capability-gated `volume_write`
  CALL carrying a `stream_id`/`stream_index`/`stream_count` envelope
  and a per-chunk SHA-256 `checksum`; the service reassembles (chunks
  may arrive out of order) and performs **ONE write, ONE quota check,
  ONE accounting charge, and ONE commit** when the final chunk
  arrives, replying once. Reads: one `volume_read` CALL with
  `stream=True` returns a sequence of correlated ≤32 KiB REPLYs that
  the client reassembles by index.
- **The wire codec is untouched** — chunks are ordinary CALLs, so the
  byte-identical differential gate stays green and the Rust serving
  loop needs no change (chunks dispatch on either loop path). The
  ADR's wire-level framing (a codec flag + Rust loop reassembly for
  ALL services) is the documented follow-on increment.
- **Bounds mirror the ADR's window + TTL**: at most 512 chunks (16 MiB)
  per stream, a 30 s reassembly TTL (incomplete streams are swept),
  duplicate/mismatched chunks reject the whole stream fail-closed, and
  a stream is bound to the first chunk's sender (a chunk from another
  container fails even with a matching id).
- **Mixed-version degradation is first-class**: `volume_open` now
  advertises `stream: true`; a passthrough that never sees the flag
  (an older daemon) keeps paging in ≤32 KiB CALLs, and the paging
  paths stay implemented forever as the fallback (also on a partial/
  timed-out stream).
- **Client halves**: `IPCClient.call_stream_write` (pipelined chunk
  sends, one final reply) and `IPCClient.call_stream_reply` (collect
  correlated pieces by index) — both the Python floor path by design
  (the Rust client half is single-round-trip; its streaming is the
  follow-on).
- **Measured (§29, `--vault-stream`)**: 1 MiB writes **5.6× faster
  plaintext / 6.6× encrypted** vs the paged path (355.9 → 64.1 ms;
  511.4 → 77.9 ms); reads ~1.02–1.08× (their cost was already flat —
  AEAD block decode dominates, and each piece still rides its own
  REPLY datagram). The evidence Architecture Group reviews before
  accepting ADR-0024.
- New `TestStorageStreaming` (13 tests: out-of-order reassembly,
  duplicate/cross-sender/checksum/count-bound rejection, TTL sweep,
  single-write enforcement on the FULL payload (scoped-EDQUOT on the
  assembled bytes), streamed-read pieces + EOF, the open
  advertisement, plain-path back-compat, and two real-server e2e
  round trips incl. a quota-rejected stream). Suite 466 → **479**.

## [0.14.19] — 2026-08-16

### Per-subtree quotas — budget each scope of a shared volume

- **`volume_quota_set` gains a `path` scope**: the quota becomes
  PER-SUBTREE — an ADDITIONAL cap on writes under that scope. Every
  applicable cap must pass (the whole-volume quota AND each scoped
  quota whose scope contains the path), so a path under nested scopes
  is capped by each — the scoped figures read "bytes under this
  scope", so nested caps overlap by design (stated in the ADR + the
  runbook). Cleared the same way (`bytes: null` removes that scope's
  cap; `--unlimited --path /x` clears it).
- **Enforcement**: fail-closed EDQUOT (errno 122) before the write
  touches the tree; the scoped EDQUOT carries its scope in the error
  AND in the event ring (a whole-volume EDQUOT stays scope-less, so
  the operator can tell where it hit). Scoped usage is billed
  incrementally between commits (like the whole-volume ledger) and
  re-derived from the tree at every commit — a delete under the scope
  re-accounts it away (tested). Quotas + scoped usage persist with
  the registry (restart-tested).
- **Surface**: `quota-get` rows gain a `scope` column (whole-volume
  rows print `/`, scoped rows their path + scoped usage); `usage`
  reports `scope_usage` (bytes under each scope, per container).
  CLI: `nyrqisctl vault quota-set <vol> <container> --path /assets
  --bytes 500`, `quota-get`, `usage`. Verified end-to-end against a
  real encrypted daemon (scoped quota → in-scope write lands →
  over-scope write rejects with EDQUOT + "under scope /assets" →
  quota-get shows both rows). Advisory warning levels remain
  whole-volume-only for now — scoped quotas enforce the hard stop.
- Suite 464 → **466** (enforcement incl. whole-volume interplay +
  nested overlap, persistence + re-derive; the CLI quota payload test
  gained `--path`; quota-get row shape updated for `scope`).

## [0.14.18] — 2026-08-16

### The event ring survives a restart

- **Durable history**: the ring is now persisted with the registry at
  every commit — a grant/revoke or quota transition recorded today is
  still there after a daemon restart (the grant and revoke ops record
  the event BEFORE the persist so it rides the same registry write;
  quota events ride the commit path's save). The ring stays **bounded
  diagnostics** (64, newest first) — the registry remains the source
  of truth for the current state; this is durability for the
  operator's recent history, not a log file.
- **Honest boundary (kernel-mount EACCES)**: the FUSE kernel mount is
  operator/host-only by design and the operator is never
  path-restricted — so a kernel mount can never hold a scoped grant.
  A scoped grant's EACCES is exercised by the grantee's own data
  plane (its CALLs), verified end-to-end in 0.14.16; the runbook now
  says so.
- Suite 463 → **464** (restart-persistence test: a scoped grant's
  event survives a fresh StorageService over the same registry).

## [0.14.17] — 2026-08-16

### The access matrix joins the event ring

- **Grant/revoke events**: the event ring now records the access
  matrix, not just the quota signal — a `grant` records who, when, and
  **how wide the scope**; a `revoke` records **what was actually
  withdrawn** (the scope the grantee held, `/` for whole-volume).
  Events carry a `kind` (`grant` / `revoke` / `quota`); quota events
  keep their `level`/`usage`/`quota` fields, grant events carry
  `scope`. The ring stays bounded (64), newest-first, in-memory
  diagnostics (never persisted — the registry is the durable source
  of truth), and OPERATOR-ONLY.
- **CLI**: `vault events` prints the kind column — quota rows as
  before (`time\tvolume\tcontainer\tlevel\tusage/quota`), grant/
  revoke rows as `time\tvolume\tcontainer\tgrant|revoke\tscope=...`;
  the header is now `time\tvolume\tcontainer\tkind\tdetail`.
  Verified end-to-end against a real daemon: create → scoped grant →
  revoke → the ring shows `revoke container-b scope=/assets` then
  `grant container-b scope=/assets`, newest first.
- Suite 462 → **463** (the grant-events ring test: whole + scoped
  grants, revoke-with-scope, no-event revoke, operator-only gate;
  the quota-event and CLI tests updated for the `kind` field).

## [0.14.16] — 2026-08-16

### Path-scoped grants verified end-to-end + the honest EACCES

- **EACCES on scope violations**: the grant-scope rejection now rides
  the CALL reply with `errno` 13 (`EACCES`) — a scope violation is a
  permission denial, so the FUSE passthrough surfaces the honest
  errno to the kernel instead of a generic `EIO`. Asserted in the
  hermetic handler tests (write + rename) and in the e2e below.
- **Verified through a REAL seccomp container** (0.14.15's feature,
  this round's proof): a container holding a **path-scoped grant**
  (`/assets`) on an ENCRYPTED volume drives the passthrough ops over
  the wire — the write inside the scope lands, the write AND read
  outside the scope are denied with `EACCES` riding the reply, the
  in-scope write reads back, and the operator confirms the rejected
  path **never reached the tree** (reads as no-such-file). The full
  chain: kernel-scoped grant → container CALL → fail-closed scope
  check → honest errno → operator verification.
- Suite 461 → **462** (the container e2e; the hermetic scope tests
  gained the errno assertions).

## [0.14.15] — 2026-08-16

### Path-scoped grants + admin-op tightening

- **Path-scoped grants**: `volume_grant` may now carry a `path` scope
  (`/subtree`) — the grantee can open the volume, but every data-plane
  op on a path outside the subtree is rejected **fail-closed**: write,
  read, rename (**BOTH sides** of a rename must stay inside the scope;
  a move cannot escape it either way), and truncate. A bare grant
  stays a whole-volume grant, persisted back-compatibly as `True`
  (the 0.14.8 shape); a scoped grant persists as `{"path": ...}` —
  both survive a daemon restart (tested). The creator and operator
  are never path-restricted.
- **Admin-op tightening (the finding this round)**: snapshot /
  restore / snapshot-delete capture or rewrite the **WHOLE** volume
  tree — a granted container (even with a whole-volume grant) could
  snapshot data outside any scope or clobber the entire volume with a
  restore — so these are now CREATOR/OPERATOR-ONLY, exactly like
  grants themselves (a grantee's attempt fails closed with "creator
  or the operator", even with a valid handle — tested).
- **CLI**: `nyrqisctl vault grant --name assets container-b --path
  /assets`; `vault grants` prints scoped grants as `container@path`
  (whole-volume grants bare); the grant reply echoes the scope.
- Suite 458 → **461** (scope enforcement across write/read/rename-both-
  sides/truncate, persistence + `True` back-compat, admin-op refusal
  for grantees; the grant-matrix and CLI payload tests updated for the
  scope-aware shapes).

## [0.14.14] — 2026-08-16

### The quota-event ring — the operator's actionable history

- **`volume_events` (OPERATOR-ONLY) + `nyrqisctl vault events`**: the
  in-memory quota-event ring (bounded at 64, newest first) recording
  warning-level TRANSITIONS (`near`/`at`/`over` — the same points the
  log lines fire) and every **EDQUOT rejection** (the hard stop, the
  most actionable event an operator can see). Honest scope: the ring
  is diagnostics, never persisted — the ledger is the durable source
  of truth (the runbook says so). A container is refused the op even
  with the storage capability (it reveals per-container accounting).
- Format: `time\tvolume\tcontainer\tlevel\tusage/quota`, e.g.
  `edquot` at `95/100`.
- Suite 456 → **458** (transition + EDQUOT event test, ring-bound
  test; the CLI quota payload test gained the events payload/format).

## [0.14.13] — 2026-08-16

### The vault at a glance: status/health carry the aggregate

- **`status` and `health` now report the vault aggregate** — volumes,
  total LOGICAL + PHYSICAL bytes, and warned containers — read from
  the CACHED ledger figures (no tree walk: status stays O(volumes)
  instead of paying the §28 refresh, which is what `volume_summary`
  is for). The status service already holds the daemon reference, so
  the block rides both the main-socket and health-socket status
  services with zero host wiring; a bare service (no daemon/storage)
  reports `vault: null`. `nyrqisctl status`/`health` print the line
  when present.
- **Warning levels verified through a REAL kernel mount**: a kernel
  write past 80% of a quota on the live encrypted mount commits at
  fsync, the refresh computes `near`, and `vault quota-get` reports
  it — the same end-to-end path as the EDQUOT verification.
- Suite 454 → **456** (status-vault-aggregate test + the live-mount
  warning test + CLI status/health vault formatting).

## [0.14.12] — 2026-08-16

### Quota warnings — the operational signal on top of the hard EDQUOT stop

- **Warning levels** (`near` ≥ 80%, `at` ≥ 95%, `over` > 100%), computed
  at every ledger refresh (commit) from the quota ledger, logged only
  on a level TRANSITION (a volume parked near its quota does not
  spam), and persisted with the registry. `over` is not reachable by
  writing — the write path rejects it — only by re-derivation: a
  quota set below existing usage or a restore to a larger snapshot
  (both tested).
- **Surfaced everywhere the operator looks**: `volume_quota_get` rows
  carry the level, `volume_usage` carries per-container warnings,
  `volume_summary` rows carry a `warning_count`, and the WRITE reply
  carries the writer's post-billing level so `nyrqisctl vault write`
  prints `(quota warning: near)` at the point of action. Clearing a
  quota drops the signal at the next refresh.
- Suite 452 → **454** (warning-level + persistence/clearing tests; the
  CLI quota payload test gained the warning columns and the write
  warning).

## [0.14.11] — 2026-08-16

### The operator's vault view: physical-byte figure, whole-vault summary, ledger-refresh cost

- **PHYSICAL bytes, honestly scoped.** `volume_usage` now also reports
  the volume-wide PHYSICAL figure: the on-disk state footprint
  (journal + metadata + block store — compressed + CoW-deduped),
  cached with the ledger at each commit (one `stat` pass over the
  state dir). It is volume-wide, never per-container — with CoW
  sharing, per-container physical attribution is load-dependent, so
  only the whole-volume figure is honest (stated in the ADR and the
  runbook). Verified: 9 KiB of compressible data → logical 9000,
  physical 902 (compression is visible). `volume_info`'s
  `bytes_persisted` now uses the same helper — it previously only
  counted the post-compaction `blocks/` dir and reported 0 for
  journal-resident state.
- **`volume_summary` (OPERATOR-ONLY)** + `nyrqisctl vault summary`:
  the whole-vault aggregate — volume count, total logical + physical
  bytes, and a per-volume row (logical, physical, consumer count),
  re-derived fresh on demand (a granted container is refused even
  with the storage capability — the summary reveals volumes the
  caller may not open).
- **§28 ledger-refresh benchmark (`--ledger-refresh`)**: the ADR-0022
  per-commit usage refresh (tree walk + attribution + physical stat)
  measures **0.53–0.67 ms at 1 k files and 7.79–8.93 ms at 10 k** —
  a rounding error next to the ~110 ms durable save it rides on, so
  the accounting increment added no measurable commit cost.
- Suite 450 → **452** (usage-physical + summary tests; the CLI quota
  payload test gained the summary/usage-physical assertions).

## [0.14.10] — 2026-08-16

### Per-container quota & accounting — ADR-0022's follow-on design is implemented

- **The ledger.** Every volume now accounts bytes per container
  (`volume_usage`), billed to the WRITING container at `volume_write`
  (the handle's binding container — the grant matrix of 0.14.8 made
  this possible: a shared volume bills each consumer). Reads are free;
  `volume_truncate` credits the owner the size delta immediately, so a
  container that shrinks its files can write again before the next
  commit refresh. Attribution is a per-path last-writer map
  (`owners`), and the ledger itself is a **cache re-derived from the
  NyFS tree at every commit** (fsync / interval tick / close / restore
  — NyFS gains a public `walk()`): a delete, truncate, rename or
  restore re-accounts exactly what the tree holds, so the ledger can
  never drift from what a restore actually frees. Sum of file sizes =
  LOGICAL bytes (the operator contract); physical block-storage bytes
  (CoW sharing, compression) are deliberately NOT billed — stated
  honestly in the ADR.
- **Enforcement.** `volume_quota_set` (CREATOR/OPERATOR-ONLY — quota
  is administration, like grants) sets a per-container byte quota
  (`bytes: null` clears it; unlimited by default). The write path
  rejects **fail-closed with EDQUOT (errno 122) BEFORE touching the
  tree** when `accounted + write > quota`; the errno rides the reply so
  the FUSE passthrough surfaces the real `EDQUOT` to the kernel.
- **Persistence.** Quotas, usage and attribution persist in the volume
  registry (`volumes.json`) at every commit — accounting survives a
  daemon restart, and the tree re-derives it anyway on the first
  commit.
- **CLI.** `vault quota-set <vol|--name> <container> [--bytes N |
  --unlimited]`, `vault quota-get <vol|--name>` (quota + usage rows),
  `vault usage <vol|--name>` (per-container usage, any opener).
  Verified e2e against a real daemon: quota set → over-quota write
  fails with "quota exceeded" (exit 1) → quota-get/usage show the
  billed figure → `--unlimited` clears.
- **EDQUOT verified through a REAL kernel mount**: an over-quota write
  on the live ENCRYPTED mount surfaces as `EDQUOT` at the syscall
  (the errno rides the CALL reply → `VaultMountError` → `FuseOSError`
  → kernel), not a generic EIO — and the fail-closed rejection does
  not wedge the volume (the next within-quota kernel write lands).
- Suite 440 → **450** (7 storage-service accounting tests + the CLI
  quota payload tests + the lifecycle-e2e quota round trip + the
  passthrough and live-mount EDQUOT tests).

## [0.14.9] — 2026-08-15

### Group commit (interval-based), the granted-container data plane, and the quota design

- **Group commit (§27)**: the FUSE `flush` handler (close-of-last-fd) is
  no longer a durability boundary — POSIX: close does not promise
  durability, fsync does. `flush` is now a group-commit OPPORTUNITY
  (`volume_flush`): the service persists the deferred batch at the
  commit-interval tick (`--commit-interval`, default 5 s; 0 = fsync/
  close only), so a burst of short-lived files pays ONE save per
  interval instead of one per close. `volume_fsync` (POSIX contract),
  `volume_close` (unmount), and reads-in-memory are unchanged.
  Verified semantics: flush defers (journal untouched), fsync commits,
  the interval tick commits the batch, close commits. Re-bench §27:
  writes hold ~3.2 MB/s (1 MiB) / ~0.8 MB/s (4 KiB) — already
  single-commit patterns — and the new **small-files burst pattern**
  (100×4 KiB open/write/close) runs at **~260 files/s through the
  encrypted passthrough vs ~11–21 k native**, dominated by the per-op
  CALL round-trip + AEAD (the ADR-0022 data-plane cost), not commits.
- **Granted container drives the mount's ops (verified e2e)**: a real
  seccomp container holding an explicit volume grant opens an
  ENCRYPTED volume by NAME and runs the passthrough's operations over
  the wire (`NyVaultOperations` — the exact ops a kernel mount
  issues). Honest finding: the kernel mount itself is operator/host-
  only by design (`mount`/`umount2` are in seccomp's always-deny set),
  so a seccomp container's data plane is these CALLs — documented in
  the runbook. CLI e2e: `vault snapshot-delete` verified against a
  real daemon (v2 survives, v1 dropped, restore-to-v1 fails honestly).
- **Quota & accounting design**: ADR-0022 gains the follow-on design
  (per-container byte quota, billing the WRITING container on shared
  volumes, fail-closed EDQUOT at write, tree-derived ledger refreshed
  at commit) — a design, not yet implemented, so the doc's "follow-on"
  status stays honest.
- Suite 437 → **440** (interval/flush semantics + the granted-container
  e2e + the flush/fsync passthrough test; the CLI lifecycle e2e gained
  the snapshot-delete steps).

## [0.14.8] — 2026-08-15

### Write-commit batching, the cross-container grant matrix, and snapshot deletion

- **Write-commit batching (§27 re-bench)**: `volume_write` now defers the
  durable commit (in-memory dirty blocks — the §26 byte-path behavior)
  and `volume_fsync`/`volume_flush`/`volume_close` anchor it, so a
  kernel write pays ONE `save()` at the fsync/flush boundary instead of
  one per CALL. The passthrough gained the `flush` FUSE handler. Re-bench
  `--vault-mount-io`: streaming writes **0.28 → 3.17 MB/s (11×)**, 4 KiB
  syscalls **0.04 → 0.78 MB/s (19×)**; §26 byte-path writes dropped from
  ~86 ms to ~2.2 ms p50 (~40×) — deferred data is visible in memory
  immediately and lost on a daemon crash before commit, exactly POSIX
  fsync semantics (spelled out in the runbook).
- **Cross-container volume grants (ADR-0022's access matrix is no longer
  future work)**: `volume_grant`/`volume_revoke`/`volume_grants`
  (CREATOR/OPERATOR-ONLY — a granted container administers nothing, it
  can only open) let the creator share a volume with another container;
  grants are per-container, persisted with the registry (survive a
  restart), and never imply `CAP_STORAGE_VOLUME`. `volume_open`/
  `volume_list` honor them; revoke gates future opens while a live
  handle keeps working (open-file semantics). `nyrqisctl vault
  grant/revoke/grants` by id or `--name`; the runbook gained §3b.
- **Snapshot deletion**: `NyFS.delete_snapshot` + `volume_snapshot_delete`
  over the wire + `nyrqisctl vault snapshot-delete` — the snapshot
  table is a lifecycle, not just a read path; a missing snapshot fails
  honestly.
- Suite 434 → **437** (grant matrix + snapshot delete + the CLI payload
  tests).

## [0.14.7] — 2026-08-15

### Snapshot restore, the live encrypted-mount benchmark (§27), and the vault runbook

- **`volume_restore` + `nyrqisctl vault restore`**: restore a volume's tree to a CoW snapshot over the wire (the snapshot table itself is unchanged; the restored table is what `save()` persists). Verified through the CLI (snapshot → overwrite → restore → original bytes back) AND through the **live encrypted mount** (`TestNyVaultLiveMount`: kernel write → snapshot → kernel overwrite → restore → content verified on the same CALL path the kernel uses). `NyVaultOperations` gains `snapshot`/`restore`/`list_snapshots` for mount owners.
- **Live ENCRYPTED NyVault mount benchmark (§27, `--vault-mount-io`)**: a real kernel FUSE mount over an ADR-0023-encrypted volume vs native — writes are dominated by the **durable per-CALL commit** (1 MiB ≈ 32 sequential CALLs ≈ 110 ms each → 0.28 MB/s vs native ~1,700 MB/s); reads need no commit and run at ~2.1 MB/s. The benchmark exposed a REAL bug and fixed it: the passthrough mount's adapter never registered an `init` marker, so fusepy never wired the C callback and the write-batching INIT negotiation **silently never ran** (4 KiB write requests → 256 commits per 1 MiB, 0.04 MB/s). The adapter now has the `init` marker and the mount shares `NyFSMount`'s FUSE_CAP_BIG_WRITES + WRITEBACK_CACHE + MAX_PAGES negotiation — **7× on streaming writes** (0.04 → 0.28 MB/s). The documented next step is write-commit batching (aggregate `save()` at the fsync/interval boundary; `volume_fsync` already exists to anchor it).
- **The vault operator runbook landed** (`docs/how-to/operate-the-vault.md`, in the nav): init → serve → byte path → mount → rekey → backup/restore → security notes, with the honest fail-closed behaviors spelled out.
- Suite 432 → **434** (restore over the wire + restore through the live encrypted mount).

## [0.14.6] — 2026-08-15

### KEK rotation, a verified LIVE encrypted mount, and the systemd vault wiring

- **KEK rotation landed (ADR-0023 "rotation without re-encryption")**: the
  storage service's `volume_rekey` op (OPERATOR-ONLY — a container never
  holds the master passphrase) unwraps every volume's DEK with the current
  KEK and re-wraps it with the new one, derived daemon-side and held in
  the key handle table for the duration; **no block is re-encrypted** (the
  DEK, hence all ciphertext, is untouched). The reply carries the NEW KEK
  envelope (its salt is the one the DEKs were wrapped with — a locally
  generated envelope would NOT match), so `nyrqisctl vault rekey
  --new-passphrase Q --new-key-file F` persists it and the operator
  restarts the daemon under the new key. Verified end-to-end: data reads
  back after a restart under the NEW key, and the OLD key file can no
  longer open the volume (fail-closed with an honest "vault key mismatch"
  — the generic handler now surfaces `StorageLockedError` messages instead
  of "internal error").
- **The encrypted vault was taken through a REAL kernel FUSE mount and
  verified live (ADR-0022's data-plane mount, first live verification)**: a
  `StatusServiceHost` serving an encrypted vault + `nyrqisctl vault mount`
  (and the in-process `NyVaultMount`) — kernel writes ride the passthrough
  CALLs into the AEAD block layer and **no plaintext lands under the vault
  dir**; write/fsync/read/mkdir/root-readdir/stat all work. Two real bugs
  found and fixed by the live attempt: (1) `_check_path` rejected the
  volume ROOT (`/`), breaking `readdir("/")`/`getattr("/")` — the root is
  now a valid path; (2) the CLI's `--background` mount died with the
  exiting CLI process (the FUSE loop lives in a daemon thread), so
  `vault mount` now serves in the foreground of the CLI process (prints
  confirmation, blocks on the loop until unmounted) — removed the
  misleading flag. `volume_open` also canonicalizes id-or-name resolution
  (the passthrough hands a name string; the handle now binds the real
  volume id). New `TestNyVaultLiveMount` (2 tests, skip-gated on fusepy +
  /dev/fuse + fusermount like `TestNyFSLiveMount`).
- **systemd unit vault wiring**: `StateDirectory=nyrqis` (persistent
  `/var/lib/nyrqis`, chowned to the service user), `--vault-dir
  /var/lib/nyrqis/vault` + `--vault-key-file /var/lib/nyrqis/vault.key`,
  and `EnvironmentFile=-/etc/nyrqis/backend.env` for the unlock passphrase
  (the `-` prefix keeps the unit valid — without the file the vault serves
  plaintext). `TestSystemdUnit` asserts the flags.
- Suite 427 → **432** (rekey + live mount).

## [0.14.5] — 2026-08-15

### NyVault at rest: KEK wiring + block AEAD + the FUSE passthrough

- **The vault is now encrypted AT REST end-to-end (ADR-0023's core claim)**: `nyrqisctl vault init` writes the Argon2id-derived KEK envelope; the daemon serves with `--vault-key-file` + passphrase (unlock at serve time, fail-closed on a wrong secret); `volume_create` gives every volume its own random DEK wrapped with the KEK (`ad = volume id`); and the **block layer is AEAD-encrypted**: `rust/keys` + the PyNaCl floor gain `block_encrypt`/`block_decrypt` (24-byte nonce, XChaCha20-Poly1305, checksum over ciphertext per ADR-0023), and `NyFSFilesystem(dek=...)` threads the DEK through `_make_block`/`_decompress_verified` — the single write/read funnels — so every block at rest is `nonce ‖ ciphertext ‖ tag`, verified on read. Verified: write → read → save → load round-trips, and **no plaintext anywhere under the vault dir**. Differential byte-identity for the block ops matches the KEK/wrap conformance.
- **Volume lifecycle completes**: `volume_delete` crypto-shreds (drop handles + wrapped DEK + backing image + registry entry — the ciphertext may remain, but no key path survives), and the registry + wrapped DEKs **persist across a daemon restart** (`volumes.json` in the vault dir; DEKs re-unwrapped from the KEK on open).
- **The NyVault FUSE passthrough LANDED (ADR-0022's data-plane mount)**: `fuse/vault_mount.py` — `NyVaultOperations` are FUSE ops whose handlers are **storage-service CALLs** (getattr/readdir/read/write/mkdir/mknod/unlink/rmdir/rename/truncate/statfs/fsync), paging through the 32 KiB per-call byte path for kernel-sized requests, with errno propagation (ENOENT → FileNotFoundError, etc.); `NyVaultMount` mirrors `NyFSMount` (honest deferral without fusepy). The service's generic file surface (`volume_getattr`/`volume_readdir`/`volume_mkdir`/...) sits behind the same capability + handle + path gates as the byte path. CLI: `nyrqisctl vault mount <volume> <mountpoint>` (foreground/`--background`).
- **§26 vault-io benchmark**: write/read p50 through the full loop — the durable `save()` commit dominates writes (~86 ms, one fsync per transaction — the §9/§15 finding again), reads run at **1.6–2.8 ms p50** flat across payloads, and the block AEAD adds ~0.5 ms on 32 KiB reads (nothing measurable on writes).
- Suite 412 → **427** (block-AEAD differential + encrypted-vault lifecycle + generic file surface + `TestNyVaultOperations` + CLI mount).

## [0.14.4] — 2026-08-15

### NyVault byte path + operator vault CLI + the key manager (ADR-0022/0023)

- **The NyVault byte path LANDED (ADR-0022's "the daemon holds the data plane")**: `ipc/storage.py` gains `volume_write` / `volume_read` / `volume_snapshot` / `volume_snapshots` — real NyFS I/O through the capability-gated, creator-scoped handles. Writes are create-on-write with mkdir -p semantics (a blob store), offset writes overwrite in place (CoW), reads page with offset/size, and snapshots ride NyFS's CoW snapshots (the snapshot keeps old data after an overwrite — verified). Paths are validated (`..` and trailing-slash rejected); per-call payloads are capped at 32 KiB (the 64 KiB datagram budget — streaming is the FUSE-passthrough increment's job); registry-only volumes (no `--vault-dir`) refuse the byte ops cleanly. `volume_open` now also opens by NAME (`vault open --name assets`).
- **`nyrqisctl vault` subcommands LANDED**: `vault create|open|list|close|write|read|snapshot|snapshots` — write takes `--file` or stdin, read emits raw bytes to stdout (or `--output FILE`), and the health socket refuses vault ops like control ops. Verified end-to-end against a REAL daemon: create → open → write → read (byte-identical) → offset write → snapshot → snapshots → close, all through the CLI.
- **The NyVault key manager LANDED (ADR-0023 first increment)**: `backend/keys.py` is the pure PyNaCl floor — Argon2id KEK derivation (p=1, matching libsodium's argon2id KDF), XChaCha20-Poly1305 envelope encryption (24-byte nonce, 16-byte tag, caller-supplied AD), a deterministic 110-byte KEK envelope (magic + version + KDF params + salt + AEAD check value — the only thing persisted), per-volume DEK wrap/unwrap, and fail-closed verification (wrong unlock secret and tampering both rejected). **`rust/keys/` is the custody boundary**: the same construction in Rust (RustCrypto argon2 + chacha20poly1305 — ADR-0023's approved first non-libc dependencies; the crate is the seam, not the vendor), with the KEK held ONLY in the crate's handle table — `unlock` returns an opaque u64 handle and the plaintext KEK never crosses FFI (the platform-boundary rule on the most sensitive path). `backend/keys.py` doubles as the loader (search order, ABI gate 1.0.0, `NYRQIS_KEYS_LIB` override, `NYRQIS_RUST_FORCE=1` gate, PyNaCl floor fallback; the availability cache is a deleted-attribute scan so env changes take effect). **Differential conformance verified**: Argon2id bytes identical, wrapped-DEK bytes identical, cross-implementation blob interop both ways, wrong-secret/tamper rejection on both, handle shred invalidates. New CI jobs `rust-keys` (build + 6 unit tests) and the required `rust-keys-conformance` gate. Suite 390 → **412**. Wiring the KEK into `volume_create` (per-volume wrapped DEKs) is the next increment — at-rest encryption is not yet claimed.

## [0.14.3] — 2026-08-15

### Strict seccomp + NyVault storage service (ADR-0022) + cold-start benchmark + ADR-0023

- **`strict_seccomp` on `ContainerConfig`**: when set, the container's seccomp filter installs with `SECCOMP_FILTER_FLAG_LOG` removed (and, where available, the filter is installed for the exec'd child rather than the intermediate fork) so a policy violation is a hard kill, not a logged-and-continue. Wired through BOTH launcher paths — `--strict-seccomp` on the Rust `nyrqis-launcher` (ADR-0020) and the matching flag on `launcher.py` — and only rides when a filter is actually being installed (`seccomp=True`). New tests cover both paths' argv wiring and the flag's absence when no filter is installed.
- **NyVault storage service lands as a real backend service (ADR-0022, first increment)**: `ipc/storage.py` — a `StorageService` on the IPC router with the first-increment lifecycle ops `volume_create` / `volume_open` / `volume_list` / `volume_close` / `volume_info` (the ADR's byte-path ops — read/write/snapshot — are the next increment), each gated on the new **`CAP_STORAGE_VOLUME`** capability (fail-closed, same enforcement point as `CAP_SYSTEM_INFO`), with a volume registry keyed by container + volume name and **real NyFS backing**: `volume_create` genuinely constructs a `NyFSFilesystem` root for the volume. Wired into the daemon host (`nyrqis_backend.py`) alongside status/control; operator calls are authorized outright, container calls capability-gated — the ADR-0022 trust model. New `TestStorageService` (7 tests: create/open/write/read round-trip through the service, capability denial for ungranted containers, operator authorization, snapshot listing).
- **Container cold-start benchmark (§25)**: `--launcher-coldstart` measures real spawn→wait latency for a trivial command, compiled `nyrqis-launcher` vs Python `launcher.py` A/B in the same session (8 iterations/side, userns-gated). Measured on the build host: **the compiled init is faster in every run and at every percentile** — Python p50 stable at 152–157 ms; compiled p50 6.3–53.7 ms across runs (scheduler/userns-clone noise; p95 ~55 ms in every run, ~3× faster than the Python p50). Recorded in `tests/BENCHMARK_RESULTS.md` §25.
- **ADR-0023 (Proposed)**: NyVault key manager — envelope encryption (per-volume XChaCha20-Poly1305 DEKs wrapped by a daemon-held KEK), KEK never stored in plaintext (Argon2id passphrase unlock default, hardware-bound TPM2/PKCS#11 backends behind a Rust trait deferred), crypto-shredding revocation, rotation without re-encryption, and **key custody in a Rust crate behind the FFI boundary** — Python interacts through opaque handles and never holds plaintext keys (ADR-0020's rule on the most sensitive path). Approves libsodium as the first non-libc dependency for the keys crate. Added to the ADR index.

## [0.14.2] — 2026-08-15

### Compiled launcher-init + operator-CLI polish + NyVault ADR (ADR-0020, ADR-0022, ADR-0021)

- **The container's PID-1 is now a compiled binary (`rust/launcher/`, ADR-0020)**: the launcher-init moves behind the platform boundary — zero Python between clone and exec. `nyrqis-launcher` (a Rust BINARY, `libc`-only) does everything `launcher.py` did: sethostname (with the prctl fallback), cgroup-mount hardening (`umount2`), loopback bring-up (`SIOCSIFFLAGS`), SIGPIPE/SIGXFSZ reset, fork + **seccomp install via prctl** + `execvp`, signal forwarding (7 signals, async-signal-safe atomic handler), reaping, signal-death propagation (128+n), and the orphan sweep. The seccomp POLICY COMPILATION stays in the backend (the syscall allowlist tables live there); the manager serializes the compiled classic-BPF program to a `--bpf-file` the binary installs. `backend/rust_launcher.py` is the locator (`$NYRQIS_LAUNCHER` override → crate `target/release/` → PATH; `NYRQIS_LAUNCHER_FORCE=1` for the conformance gate) and is deliberately UNCACHED — a stale cached path would exec a dead binary (126). `container.py` `_launcher_exec` hands the container the compiled binary when available, launcher.py otherwise; the Python launcher stays as the crate-less fallback. New CI jobs: `rust-launcher` (build + 10 unit tests) and the required `rust-launcher-conformance` gate (the loader + wiring classes forced through the binary). Verified end-to-end: real containers through the compiled init — exit status 7 propagated, UTS hostname set, **the container's seccomp filter ACTIVE** (a default-cap file create denied → exit 9), SIGTERM to the init forwarded and wait() reported 128+15. Suite 368 → **382**.
- **`nyrqisctl --health-socket` (ADR-0021)**: `ping`/`status`/`health` route to the daemon's dedicated health-probe socket (no contention with container traffic on the main socket); control commands refuse it (exit 2, clear message). New `test_cli_health_socket_routes_status_ops`.
- **Packaging polish**: `packaging/man/nyrqisctl.1` (roff man page: commands, options, exit status, examples) + `packaging/completions/nyrqisctl.bash`/`.zsh` (tab-completion), with install steps in `packaging/README.md` (which also documents nyrqisctl as the preferred operator surface).
- **ADR-0022 drafted (Proposed)**: NyVault — storage as a daemon-hosted service on the IPC transport. A container obtains a NyFS-backed volume by CALLing a `storage` service (registered on ADR-0021's router), gets a capability-gated volume handle, and its in-container byte path is a FUSE passthrough whose ops are the same authenticated CALLs; the daemon holds the data plane. Deliberately NOT a database, NOT a key store (the vault key-manager ADR is deferred before at-rest encryption is claimed), NOT kernel-level storage. Added to the ADR index.

## [0.14.1] — 2026-08-15

### The operator CLI (`nyrqisctl`) — the user-facing surface of the daemon's control plane

- **`nyrqisctl.py` (NEW)** — a standalone operator CLI that drives a running daemon's main service socket over the IPC transport, claiming the operator identity (`host-operator`, authenticated by the kernel-attached uid): `ping` / `status` / `health` (status service) and `containers list|run|kill` (control service). Human-readable output by default, `--json` for the raw reply, `--socket` to point at the daemon (`/tmp/nyrqis-status.sock` default; the systemd unit serves `/run/nyrqis/status.sock`), exit 0/1/2 (ok / daemon unreachable or op failed / usage). A missing or closed daemon socket fails cleanly on both client halves (the floor returns `None`, the Rust client half raises `ENOENT`/`ECONNREFUSED` — both map to the same "no reply from the daemon" error).
- **Operator carve-out in `ipc/service.py`** — `status`/`health` were gated on `CAP_SYSTEM_INFO` with no operator path, so the daemon's own user could not read its own health through the wire. The status service now authorizes `DEFAULT_OPERATOR_ID` outright: the transport has already authenticated it by the kernel-attached uid (trusted-uid path), and such a process has full control of the daemon anyway — the same model the control service already uses (the container capability model deliberately does not apply to the operator). Container callers are unchanged (still capability-gated, fail-closed).
- **`test_backend.py` — new `TestOperatorCli` (10 tests)**: hermetic payload construction (status + control ops), human-format rendering (status/health/table/run), the `run` positional-vs-subcommand regression (`run_command` dest so the subcommand survives), missing-socket → `None`; end-to-end through a REAL daemon: operator `ping`/`status`/`health` answered (carve-out verified), `containers list`, the full `run`→`list`→`kill` loop on a REAL container (userns-gated like the other netns e2e), clean no-daemon failure (no traceback), and `--json`. Suite 358 → **368**.

## [0.14.0] — 2026-08-15

### The Rust-native child entry point: the container's PID-1 is created by ONE `clone(2)` FFI call — no Python between fork and exec (ADR-0020 migration #2 completion, implementation_plan.md §4.1)

- **`rust/syscalls/` (ABI 1.2.0)** — `nyrqis_syscalls_clone` (real `clone(2)` via libc, SIGCHLD semantics preserved so `waitpid` sees the child; the caller passes a per-call mmap'd child stack — glibc's x86_64 `clone` switches the child's stack pointer to the passed argument even WITHOUT `CLONE_VM`, so a dummy stack overflows the child) and `nyrqis_syscalls_launch_child` (the Rust-native child entry: writes the root uid/gid maps captured by the manager, sets PDEATHSIG, mounts the hardened procfs, closes the error-report pipe, and `execv`s the launcher — zero Python between fork and exec). The crate's own unit tests pin the clone contract (child runs the entry, exits with its status, `waitpid` sees SIGCHLD; the ABI floor test updated for 1.2.0).
- **`backend/rust_syscalls.py`** — `clone(flags, LaunchArgs)` marshals `LaunchArgs` (write-fd, uid, gid, argv) through a `c_void_p` argv array of RAW ADDRESSES with the `argc+1` NULL terminator: a `c_char_p` array re-copies each string into a temporary-owned buffer that dies with the temporary, leaving the clone child's `execv` reading freed memory (EFAULT, exit 126 — the ctypes array-construction trap; pinned by the loader tests). The entry-point address is resolved from the loaded library (`nyrqis_syscalls_launch_child`), never a Python callback, with a `_CFuncPtr` guard so a malformed library fails cleanly instead of segfaulting.
- **`backend/container.py`** — `_spawn_direct_clone`: ONE `clone(2)` FFI call creates the container's PID-1 directly in ALL its namespaces (user/mount/UTS/IPC/pid + net) when the crate is present; the existing Python fork-setup child (`_spawn_direct_fork`) is the crate-less fallback with the same observable outcome (init pid, command pid resolution through `/proc` children, wait semantics). Verified end-to-end: real container launches through the clone child (hostname set inside, exit status propagated, network netns path).
- **`test_backend.py`** — clone-path unit tests (fork fallback pinned via `available()→False`; the c_void_p argv marshalling + NULL terminator; `_rust_clone` guard against non-`_CFuncPtr` entry) + loader tests for the clone marshalling. Suite 350 → **358**. Also fixed a real test race exposed by the faster clone path: `test_init_forwards_sigterm_to_command` now waits for the init's `/proc/<pid>/status` `SigCgt` mask to include SIGTERM before signaling — kernel PID-1 semantics DISCARD a signal sent before a handler is installed, so signaling in the init's fork→install window silently dropped the SIGTERM (flake: 3 of 4 runs). The fixed test is deterministic (5/5 clean, and faster).
- **`tests/benchmarks.py` + BENCHMARK_RESULTS.md §24** — the main-socket control-op A/B (`--ipcd-control`): a REAL control op (`{"op": "status"}` → full CAP_SYSTEM_INFO authorization + handler) over the real transport, floor vs loop — the dispatch harness is parameterized by op (wire payload passed base64 to the client subprocess). Measured: floor p50 ~290 µs vs loop p50 **336–342 µs (+16–18%)** — close parity, matching the synthetic dispatch A/B (§23): the status handler runs in Python on both sides, so the loop's batch boundary costs the operator essentially nothing on control ops while keeping the platform-critical serving path off the interpreter.

## [0.13.9] — 2026-08-15

### ADR-0021 main-socket move: the daemon's PRIMARY service socket (status + control) is served by the Rust loop

- `nyrqis_backend.py`: `StatusServiceHost.start()` serves the main service
  socket (`--socket`) through the Rust serving loop when the crate is
  present — the loop takes the bound fd, the policy starts from the live
  registry snapshot (refreshed by the registry change hook on every
  spawn/terminate), and the FULL router (status + control) is driven by
  the dispatch handoff (`IpcdLoopDispatcher`), exactly like the floor
  branch's router. The `IPCDatagramServer` floor remains the crate-less
  fallback (the router attaches to whichever backend is active — exactly
  one). Control ops (container_run/list/kill) now cross the loop's batch
  boundary; verified end-to-end by the existing real-container control
  test, which now exercises the loop path.
- The registry change hook is set once by the main loop's startup and
  refreshes EVERY active loop (`_refresh_loop_policies` — main + health),
  so a container whose pid enters the registry is authorized on both
  sockets at once; the health socket's duplicate hook registration is gone.
- Tests: suite 347 → **350** — `test_host_main_socket_served_by_loop_when_crate_present`
  (backend selection + backend-agnostic status call), `test_host_main_socket_serves_control_ops`
  (container_list through the loop's dispatch), `test_host_main_socket_denies_container_control`
  (operator-only reply through the loop). Both paths verified: 350 OK with
  the crate (loop path) and 350 OK crate-less (floor path, CI's Python
  job).

## [0.13.8] — 2026-08-15

### ADR-0021 close gate MET: the client half of the loop + the client-side Python elimination

#### Added

- **`rust/ipcd/`** — `nyrqis_ipcd_client_call` (the client half of the loop, ABI-001): one FFI call per CALL round trip — `sendto` → `poll` → `recvmsg` → correlation (reply must match the call's `message_id`, parsed from the request wire; non-matching datagrams dropped in Rust, exactly the floor's correlation loop) → copy into the caller's reply buffer; `-ETIMEDOUT` on expiry, `-ENOBUFS` on an oversized reply. The receive buffer is a module-level locked array (zeroed once, not per poll iteration). Crate suite 18 → **22** (round trip against the serving loop in-process, timeout, correlation-amid-noise, invalid args, symbol surface).
- **`ipc/loop.py`** — `client_call` driver with a **thread-local reusable reply buffer** (no per-call 64 KiB allocation) and `string_at` for the reply copy; `BackendUnavailable` fallback contract (a timeout must NOT re-send the CALL — that would duplicate it).
- **`ipc/transport.py`** — `IPCClient.call` now routes the whole round trip through `client_call` when the crate is present; the Python floor loop (send + correlated receive) is the crate-less fallback with identical semantics, so a caller cannot tell which half served the call.
- **`ipc/ipc_codec.py`** — the Rust FFI encode/decode now pass `bytes` directly through the `c_void_p` argtypes (buffer protocol, no per-field `create_string_buffer` copy): encode 31.6 → 8.1 µs, decode 18.3 → 13.4 µs, byte-identity preserved (verified).
- **`ipc/core.py`** — `to_wire` emits the constant `b"{}"` for empty metadata (byte-identical to `json.dumps({}, sort_keys=True)`); the `message_id` generator is now `os.urandom(6).hex()` — 48-bit CSPRNG, opaque on the wire, excluded from the conformance differential, still unguessable, and ~6 µs cheaper than `uuid4` per call.
- **`test_backend.py`** — client-half loader routing with a fake lib (arg marshalling + `BackendUnavailable`), conformance (Rust client vs floor server round trip; timeout returns None without re-sending), and the floor fallback path. Suite 342 → **342** (the new tests slot into existing classes; all green).
- **`tests/benchmarks.py`** — §22/§23 re-run with the client half active: **the ADR-0021 close gate is MET** — the loop's wire p50 is **82–95 µs** across runs (UNDER the NPS-003 §6.1 <100 µs median) vs the floor's 263–274 µs (~3× faster), satisfying both close-gate criteria (beats the floor in the same-session A/B AND <100 µs median). Recorded in BENCHMARK_RESULTS.md §22/§23. **ADR-0021 moved to Accepted** (its own gate language: "stays Proposed until the close gate is met").


## [0.13.7] — 2026-08-15

### ADR-0021 decision point 1: the non-ping dispatch handoff — the health socket serves status/health

#### Added

- **`rust/ipcd/`** — the loop now QUEUES authorized non-ping CALLs (bounded by `MAX_PENDING`, fail-closed like the floor) instead of dropping them, and gains the dispatch-handoff FFI surface: `nyrqis_ipcd_loop_drain_requests` (plain-data `[u32 len][wire]` records; `-ENOBUFS` when the first record does not fit), `nyrqis_ipcd_loop_enqueue_replies` (routes each reply wire to the RECORDED sender address captured at recv — matched by the reply's `reply_to`; unknown ids skipped), and `nyrqis_ipcd_loop_discard_requests` (reaps unanswered requests). The reply routing never trusts the wire. New crate tests: full queue→drain→enqueue→send cycle with correlation, unknown-reply_to skip, discard reaping, `-ENOBUFS` on a tiny buffer. Crate suite 15 → **18**.
- **`ipc/loop.py`** — `IpcdLoop.drain_requests` / `enqueue_replies` / `discard_requests` (the FFI driver), with a REUSABLE drain buffer — the first version allocated a ~4 MiB buffer per step and the dispatch benchmark showed p50 1933 µs; reusing the buffer + copying only the written bytes dropped it to ~490 µs (close parity with the floor).
- **`ipc/dispatch.py`** (NEW) — `IpcdLoopDispatcher`: drives the handoff — after each step it drains the queued batch, dispatches each request through a `ServiceRouter` whose services reply into a `_LoopReplySink` (a server-shaped collector; the loop owns the routing), enqueues the collected reply wires for the loop to send, and discards the rest. Mirrors the floor's `CAP_IPC_SEND` gate for container senders before dispatch (the operator path needs no capability); reply wires are built with the SAME codec the floor's `reply()` uses, so a reply is byte-identical whichever backend served it.
- **`nyrqis_backend.py`** — the health loop is now driven by a `IpcdLoopDispatcher` wired to a dedicated status service + router (control ops stay off the health socket, matching the floor branch): `status`/`health` over the health socket go through the loop's queue when the crate is present.
- **`test_backend.py`** — dispatch conformance (unknown-op reply byte-identical to the floor; `status` semantic fields; a sender without `CAP_IPC_SEND` is dropped exactly like the floor), loader routing with a fake lib + the `-ENOBUFS` retry (crate-less host coverage), host end-to-end (`status` served on the health socket; a `control` request gets `unknown service` on the health router), and `test_container_probes_health_socket` — a REAL container spawned through the host's own manager calls `status` on the health socket and gets its own identity + granted capabilities back (auto-registry → change hook → policy refresh → loop → dispatch). Suite 335 → **342**.
- **`tests/benchmarks.py`** — `--ipcd-dispatch` (the non-ping handoff A/B) and `--ipcd-refresh` (isolated `set_policy` cost), recorded as **§23** in BENCHMARK_RESULTS.md: dispatch reaches close parity with the floor (~490 vs ~405 µs p50, +21% — the Python handler cost is inherent per ADR-0021; ping stays ~2.8× faster), and the pid-table refresh costs ~9.6 µs p50 (a cheap plain-data policy push on the lifecycle path).


## [0.13.6] — 2026-08-15

### ADR-0021 per-container pid-table refresh: containers can probe the health socket

#### Added

- **`rust/ipcd/`** — `nyrqis_ipcd_loop_set_policy` (the policy refresh FFI entry): replaces the loop's sender-authorization policy in place — pid→container table, trusted uids, operator id — same marshalling as `loop_new`, safe to call from another thread while the drive thread is stepping (the policy moved behind a `Mutex`; the FFI surface still exposes no shared state). New crate test `set_policy_refreshes_pid_table` (drop before refresh → answered after; invalid args → `ERR_INVALID_ARGS`). Crate suite 14 → **15**.
- **`ipc/registry.py`** — `ContainerIpcRegistry` gains `set_on_change()`: a callback fired after every register/unregister mutation (idempotent unregister of a never-mapped pid does NOT fire — nothing changed), with failures swallowed and logged so a policy push can never break container lifecycle.
- **`ipc/loop.py`** — `IpcdLoop.set_policy()`: the Python driver for the new FFI entry (snapshot marshalling + error mapping), safe while the drive thread is stepping.
- **`nyrqis_backend.py`** — `StatusServiceHost` now creates the health loop with the **live registry snapshot** and hooks `self.ipc_registry.set_on_change(self._refresh_health_policy)`, which re-pushes the snapshot on every container spawn/terminate. A container whose pid is in the registry can now probe the health socket as itself (operator/trusted-uid policy PLUS the pid table); the floor path needed no change (it reads the registry live).
- **`test_backend.py`** — registry change-hook tests (fires after each mutation with a current snapshot; failures swallowed), `test_set_policy_refreshes_pid_table` (driver-level: authorization granted/revoked/re-granted via `set_policy` without recreating the loop), and `test_host_health_socket_refreshes_container_policy` (end-to-end through the real host: a pid registered AFTER the health socket starts is answered as its container; after unregister, a container-id ping is dropped — the caller falls back to the trusted-uid operator path; identical behavior in both backends). Suite 331 → **335**.


## [0.13.5] — 2026-08-15

### ADR-0021 wired into the daemon: the health-probe socket

#### Added

- **`nyrqis_backend.py`** — `StatusServiceHost` gains `health_socket_path=` and `service serve` gains `--health-socket`: a **dedicated health-probe socket** served by the Rust serving loop when the crate is present (trusted-uid/operator policy, the loop's first-increment scope) and by the floor's status service otherwise — both answer the operator's ping with byte-identical replies, so a probe cannot tell which backend answered. The health path never contends with container traffic on the main service socket; containers keep using the main socket (the loop's per-container pid-table refresh is a later increment). `start()`/`stop()` own the health thread + endpoint lifecycle (the loop does not close the fd; the endpoint unlinks on stop).
- **`packaging/systemd/nyrqis-backend.service`** — ExecStart now passes `--health-socket /run/nyrqis/health.sock` (a systemd `HealthCheckCommand` can probe liveness once systemd ≥ 253); `packaging/README.md` documents the health socket.
- **`test_backend.py`** — `test_host_health_socket_serves_ping` (real host: operator ping on the health socket gets the byte-identical reply via the loop when the crate is present / the floor otherwise — asserted — and the MAIN socket still serves status, plus socket unlink on stop), `test_cli_service_serve_wires_health_socket` (CLI wiring); `TestSystemdUnit` asserts the new flag. Suite 329 → **331**.

## [0.13.4] — 2026-08-15

### ADR-0021 first increment: the Rust IPC serving loop (`rust/ipcd/`)

#### Added

- **`rust/ipcd/` (NEW)** — the first NyRuntime-shaped artifact (ADR-0021): a Rust serving loop that owns the whole dispatch cycle for the daemon's service socket — `poll` → `recvmsg` (`SCM_CREDENTIALS`) → wire parse → sender authorization → service dispatch → `sendto` reply — inside the Rust process, crossing the FFI boundary once per *batch* (a bounded drain per step) instead of once per message. ABI 1.0.0, `libc` the only dependency, following the migration-crate conventions. First-increment scope (honest, the ADR's gate-on-data rule): the built-in `ping` op of the status service with byte-identical reply semantics to the Python floor; anything else (non-CALL, non-ping, malformed wire, unknown or forged sender) is dropped at the trust boundary — the non-ping dispatch handoff is the next increment. Sender-authorization policy (pid→container table, trusted uids, operator id) crosses the boundary as plain data at loop creation (ABI-001: no pointers into Python objects). 14 crate unit tests (parse contract, ping detection, reply payload byte-identity, registered/operator/forged/drop paths, batch drain, clean timeout, error codes).
- **`ipc/loop.py` (NEW)** — the FFI driver for the loop: the established search/ABI/force loader contract (`$NYRQIS_RUST_LIB` → crate `target/release` → bare name; `NYRQIS_RUST_FORCE=1` turns misses into errors) plus the `IpcdLoop` wrapper (`new(fd, batch_max, pids, trusted_uids, operator_id)`, `step(timeout_ms)`, `close()`). The caller (the Python floor) keeps owning the socket lifecycle — the loop does NOT close the fd.
- **`test_backend.py`** — `TestRustIpcdLoader` (8: loader contract, error mapping, fake-lib FFI routing) and `TestIpcdLoopConformance` (3: ping reply semantics ≡ floor (reply_to correlation, empty sender/receiver, metadata `{}`, byte-identical payload), batch drain of 5 pings in one step, non-ping + forged-sender drops). Plus `TestStatusServiceHost.test_daemon_restart_recovers_stale_state` — the plan §4.5 recovery path END-TO-END through a real daemon subprocess (stale state pre-seeded with a dead pid + orphan manifest → the daemon logs the recovery and atomically replaces the state with its own identity, carrying the recovery summary forward). Suite 317 → **329** (300 run + 29 skipped on crate-less hosts; locally all run).
- **`tests/benchmarks.py`** — `--ipcd` (§21 ADR-0021 A/B): the same `{"op": "ping"}` request served by the Python floor (`BackendStatusService`) and by the Rust loop, client in a separate process, wire p50 each. Measured on the build host 2026-08-15 (BENCHMARK_RESULTS.md §22): **floor p50 ~387–394 µs vs loop p50 ~136 µs — the loop beats the floor ~2.8× at the wire median**, so ADR-0021's differential gate is GREEN; the close gate (NPS-003 §6.1 <100 µs median) stays OPEN — the residual is the client-side Python per-call cost (its own codec + transport + correlation loop), which is exactly the next NyRuntime direction.
- **`.github/workflows/ci.yml`** — `rust-ipcd` (build + tests + cdylib present) and `rust-ipcd-conformance` (required gate: `TestRustIpcdLoader` + `TestIpcdLoopConformance` forced through the FFI).

## [0.13.3] — 2026-08-14

### Phase 5 (plan §4.5): persistent state, health checks, syslog logging

#### Added

- **`backend/daemon_state.py` (NEW)** — `DaemonStateFile`: a versioned, atomically-written (tmp + `os.replace`, the NyFS/ADR-0019 discipline) JSON record of the daemon identity (pid, backend version, socket) and a last-known container manifest. Recovery is reporting, never resumption (NPS-010 §4 has no resume-from-pid transition): a stale previous-daemon record is detected at start, the orphan ids are logged, and the health op reports a recovery *summary* (previous pid + orphan count; the full manifest stays in the state file for operator review) — orphaned processes are NOT auto-killed.
- **`ipc/service.py`** — new `health` op on `BackendStatusService` (gated on `CAP_SYSTEM_INFO` like `status`, fail-closed): serve-loop liveness, container load (known/running), IPC registry size, state-persistence status, crash-recovery record. The service takes an optional `daemon=` reference to read that shared state.
- **`ipc/control.py`** — `ControlService` gains a `state_saver=` hook called (best effort, failure never breaks the reply) after the mutating `container_run`/`container_kill` ops, so the daemon's manifest stays current.
- **`nyrqis_backend.py`** — `setup_logging(verbose, syslog=True)` mirrors records to the journal via `/dev/log` (UDP-514 fallback, best-effort); `StatusServiceHost` wires the state file end-to-end (recover-on-start, save-on-start/stop, saver hook to the control service); `service serve` gains `--syslog` and `--state-file` (default `/run/nyrqis/daemon-state.json`, `--state-file ''` disables).
- **`packaging/systemd/nyrqis-backend.service`** — ExecStart now passes `--syslog --state-file /run/nyrqis/daemon-state.json` (journald owns `/dev/log`; the state file lives in the `RuntimeDirectory` systemd creates for the service user). `packaging/README.md` documents logging + state operation.
- **`test_backend.py`** — new `TestDaemonState` (11: round-trip, atomicity under `os.replace` failure, corrupt/schema/missing handling, pid-staleness, host recovery + persistence), `TestLoggingConfig` (3: `/dev/log` attach, UDP fallback, graceful degrade), health-op tests (real-socket `health`, fail-closed denial, `state_persisted`), control state-saver test; `TestSystemdUnit` now asserts the unit passes `--syslog --state-file`. Suite **299 → 317** (291 run + 26 skipped).

#### Fixed

- **Docs** — `IMPLEMENTATION_STATUS` (§5 outstanding items closed), `implementation_plan.md` §4.5, `REPOSITORY_STATE`.

## [0.13.2] — 2026-08-14

### Rust IPC transport FFI surface v2 (ABI 2.0.0) — caller-supplied buffers

#### Changed

- **`rust/transport` (ABI 2.0.0)** — `nyrqis_transport_recv` now `recvmsg`s DIRECTLY into the caller's reusable wire buffer (the `iovec` points at it — zero intermediate copy, zero malloc, zero free) and writes the sender path into the caller's path buffer; `nyrqis_transport_send` passes the immutable wire bytes by pointer (`c_char_p` — no per-call `create_string_buffer` copy). The v1 `nyrqis_transport_free` ownership contract is removed (the symbol is gone).
- **`ipc/transport_codec.py`** — loader updated to ABI 2.0.0: `recv(fd, timeout_ms, wire_buf, path_buf)` takes the caller's buffers (scratch buffers allocated only when omitted); `send` is zero-copy. `RECV_WIRE_SIZE`/`RECV_PATH_SIZE` exported (64 KiB / 108).
- **`ipc/transport.py`** — `UnixDatagramEndpoint` owns one reusable buffer pair per socket (created at bind, reused for the endpoint's lifetime), so the hot path does zero allocations.
- **`test_backend.py`** — the fake-lib recv routing test updated to the v2 signature (no free call; caller buffers); the crate's unit tests rewritten for the caller-buffer contract (round-trip, timeout, invalid-args).

#### Measured (documented honestly, NPC-002 §5.2)

- **The allocation removal was the right lever**: isolated same-process round trip p50 32.50 → 24.33 µs (floor 9.1–9.5 µs); wire-level p50 ~426 → 307–357 µs across four runs (floor 195–231 µs same-session), a ~28% improvement. **The NPS-003 §6.1 gate is still NOT met** — v2 remains ~1.6× the floor at the wire median; the residual is the ctypes boundary tax (eleven marshalled args per recv call, per-send path encode, the unavoidable copy into immutable Python bytes), the honest floor of any compiled transport driven from Python. NPS-003 stays Draft; closing the gate needs the serving loop itself behind the boundary (the NyRuntime direction), documented in BENCHMARK_RESULTS.md §20.

#### Fixed

- **Docs** — `IMPLEMENTATION_STATUS`, `BENCHMARK_RESULTS.md` §20, `REPOSITORY_STATE`.

## [0.13.1] — 2026-08-14

### Host Integration (plan §4.5) + First Rust-Transport Benchmark Data

#### Added

- **`packaging/systemd/nyrqis-backend.service`** — runs the backend daemon at boot (`nyrqis_backend.py service serve --socket /run/nyrqis/status.sock`): unprivileged by design (`DynamicUser=true` + `NoNewPrivileges=true` — the daemon launches containers through unprivileged user namespaces and must not run as root), `Restart=on-failure`, `PrivateTmp`/`ProtectHome`/`ProtectSystem` hardening, install steps in `packaging/README.md`.
- **`test_backend.py`** — `TestSystemdUnit` (3 tests): the unit wires the actual daemon subcommand, passes `systemd-analyze verify` when systemd is present (skipped otherwise), and runs unprivileged. Hermetic — reads the unit file, installs nothing on the host. Suite **295 → 298** (272 run + 26 skipped).
- **`test_backend.py`** (0.13.2) — the transport conformance class adds `test_binary_payload_with_embedded_nul_bytes` (the zero-copy `c_char_p` send must pass embedded `\x00` bytes in real binary wire frames); the fake-lib recv test asserts the v2 no-free contract directly. Suite **298 → 299** (273 run + 26 skipped).

#### Measured (documented honestly, NPC-002 §5.2 — no fabricated numbers)

- **First Rust-transport benchmark data point** (BENCHMARK_RESULTS.md §20, measured 2026-08-14 on the build host): a same-session A/B with the crate active shows the current Rust FFI surface is **slower** than the Python floor at the median (over the wire p50 ~426 µs Rust vs ~231 µs floor; isolated same-process round trip p50 32.50 µs vs 9.06 µs). Cause: the surface mallocs the output wire buffer AND a sender-path C string on every receive and copies the wire on both send and receive — the per-message allocation/copy overhead the migration exists to remove. The migration itself stands on ADR-0020's platform-boundary rule and the byte-identical conformance gate; the §6.1 performance close is a **second-pass FFI surface** (caller-supplied/pooled output buffers, no per-call malloc). NPS-003 stays Draft with the gate open until that lands.

#### Fixed

- **Docs** — `IMPLEMENTATION_STATUS` (§5), `implementation_plan.md` §4.5, `REPOSITORY_STATE`.

## [0.13.0] — 2026-08-14

### PID-1 Launcher-Init (Graceful Termination of Container Commands)

#### Added

- **`backend/launcher.py`** — the launcher no longer `execve`s the container command; it becomes the namespace's **PID-1 init**: forks the command as a plain child, forwards supervisor signals (SIGHUP/INT/QUIT/TERM/USR1/USR2/WINCH), reaps it, and exits with its status (or dies by its signal, preserving Popen-compatible `wait()` semantics: exit code or `-signum`). Also resets Python's SIG_IGN SIGPIPE/SIGXFSZ before the fork (SIG_IGN survives fork AND exec — the old launcher leaked an ignored SIGPIPE into the command).
- **`backend/container.py`** — the manager resolves the command's **HOST pid** itself (a pid reported from inside the namespace is ns-local) by polling the init's `/proc/<pid>/task/<pid>/children` — the init's only direct child (`_resolve_command_pid`; the manager's /proc is host-scoped, the container's procfs lives in its own mount namespace). `Container` gains `_init_pid` (the PID-1 init); `_attach_to_cgroups` moves BOTH pids into the container cgroups (the init's memory can no longer escape accounting); `terminate()` escalation SIGKILLs both and best-effort reaps the setup child (no zombie left behind).
- **`test_backend.py`** — `TestPid1Init` (7 tests): the command is a plain child (pid 2 in-namespace) of the PID-1 init, SIGTERM terminates a container in <3s (was: full 10s window), signals to the INIT forward to the command, exit statuses propagate through the init, a fast-exit command spawns and reports its status with no zombie, the host-pid relay never touches the process environment, and the legacy `unshare(1)` path runs the command through the init. Suite **288 → 295** (269 run + 26 skipped).
- **`ipc/transport.py`, `ipc/control.py`, `ipc/service.py`** — reviewer-driven cleanup: `DEFAULT_OPERATOR_ID` now lives in the transport (the auth boundary) with `ControlService` syncing its operator identity from the server on attach (single source of truth); the `ServiceRouter` wires services through their `attach()` method when present (falling back to the minimal `_server` contract).

#### Fixed

- **PID-1 signal semantics**: Linux discards signals (other than SIGKILL/SIGSTOP) sent to a namespace PID 1 that has no handler installed — a container command running AS PID 1 could never be terminated gracefully, so `terminate()` always burned the full 10s SIGTERM window before the SIGKILL escalation (probe-verified: a SIGTERM to a PID-1 `sleep` was discarded; Docker's `--init` is the same problem). With the init, SIGTERM reaches the command directly and a kill completes in milliseconds.
- **The init runs unfiltered by design** (the model tini uses): the seccomp policy is applied by the command child before its exec, so a container without `CAP_PROCESS_SPAWN` cannot EPERM the init's own fork (the seccomp filter would otherwise have blocked it — the data-plane guarantee for the command and its descendants is unchanged).
- **Docs** — `IMPLEMENTATION_STATUS` (v0.22.0), `implementation_plan.md` §4.1, `REPOSITORY_STATE`. Note: the legacy `unshare(1)` path's kill semantics remain their long-standing limitation (SIGTERM to the `unshare(1)` wrapper orphans the init+command); the DIRECT path is where termination is now prompt.

## [0.12.0] — 2026-08-14

### Daemon Control Plane (Operator-Only, Over the Same Transport)

#### Added

- **`ipc/transport.py`** — `IPCDatagramServer` gains a second identity path: `trusted_uids` (default the daemon's uid via the host) resolves an unknown pid to the `host-operator` identity when the kernel-attached uid is trusted AND the wire claims the operator id. Container resolution stays **pid-FIRST**, so a daemon-spawned container (which runs as the same user) is never misattributed to the operator. The operator path deliberately bypasses the container capability model — a process running as the daemon's user already has full control of it.
- **`ipc/service.py`** — `ServiceRouter`: dispatches the server's CALL handler across registered services on the payload's `service` field (default `status`, back-compatible); unknown services and service bugs become error REPLies, never kill the serve loop.
- **`ipc/control.py`** — `ControlService`: the operator control plane — `container_run` (spawns through the daemon's `ContainerManager`, auto-registered + auto-granted), `container_list`, `container_kill`. Operator-only: any container sender gets `forbidden` even with CAP_IPC_SEND.
- **`nyrqis_backend.py`** — the host now serves status + control on one socket (`router` wiring, `trusted_uids={os.getuid()}`); new `control` command (`container-run`, `container-list`, `container-kill`) claims the operator identity and prints the daemon's JSON reply.
- **`test_backend.py`** — `TestServiceRouter` (4), `TestControlService` (6, incl. container-cannot-drive-control and untrusted-uid-drop), `test_host_control_plane_runs_and_kills_container` (a REAL container spawned and killed through the wire on the runnable daemon), and `test_cli_control_wires_operator_client`. Suite **276 → 288** (262 run + 26 skipped).
- **Docs** — `IMPLEMENTATION_STATUS` v0.21.0; `implementation_plan.md` §4.3; `REPOSITORY_STATE`.

## [0.11.0] — 2026-08-14

### Runnable Status-Service Daemon + Control-Plane Capability Lifecycle

#### Added

- **`nyrqis_backend.py`** — `service serve` subcommand and `StatusServiceHost`: a runnable daemon that owns the shared state the trust chain needs — `ContainerIpcRegistry`, `CapabilityManager`, `ContainerManager`, the `IPCDatagramServer`, and the `BackendStatusService` — binds the socket (default `/tmp/nyrqis-status.sock`), and serves until SIGINT/SIGTERM (clean stop: the loop exits before the socket is released).
- **`backend/container.py`** — `ContainerManager(capability_manager=...)`: the control-plane capability lifecycle (NPS-010 §5) now mirrors the ipc-registry hooks — each spawned container is initialized with its default grants (CAP_IPC_SEND for the transport check, CAP_SYSTEM_INFO for the status check) and the grants are revoked on every terminate/wait path (idempotent). Keyed by container id, not pid, so both launch paths initialize; `None` keeps every existing flow byte-identical.
- **`test_backend.py`** — `TestContainerCapabilityLifecycle` (6 tests: spawn init, spawn-failure revoke, terminate revoke, wait legacy + direct revoke, no-manager no-op) and `TestStatusServiceHost` (5 tests: host serves status over a real socket, host manager auto-grants, CLI wiring via a mocked host, a REAL `service serve` subprocess that binds 0700 and exits 0 on SIGTERM; `test_host_container_completes_status_call` spawns a REAL container through the daemon's own manager and it completes the status CALL against the daemon's own server — the operator flow end-to-end). The status e2e now proves the whole chain automatically — the container is registered AND granted by its manager, no manual `initialize_container`. Suite **265 → 276** (250 run + 26 skipped).
- **Docs** — `IMPLEMENTATION_STATUS` v0.20.0; `implementation_plan.md` §4.3; `REPOSITORY_STATE`.

## [0.10.0] — 2026-08-14

### First Real Backend Service on the Transport

#### Added

- **`ipc/service.py`** — `BackendStatusService`, the first container-facing service on the transport (plan §4.3). Attaches to a bound `IPCDatagramServer` as its CALL handler — the server has already authenticated the sender (kernel `SCM_CREDENTIALS` pid → container via the auto-registry) and enforced `CAP_IPC_SEND`, so the service enforces its own per-operation capability on top:
  - `{"op": "ping"}` — requires nothing beyond the server's checks; verifies the whole chain (transport + identity + reply path) with a pong.
  - `{"op": "status"}` — requires `CAP_SYSTEM_INFO` (a default grant, NPS-011) and is **denied fail-closed** when no `CapabilityManager` is attached or the caller lacks the grant; reports the backend version, service uptime, and the caller's own container id and capability set.
  - A service bug becomes an `internal error` REPLY — the handler never raises into the serve loop.
- **`ipc/transport.py`** — `serve_once` now swallows `on_call` handler exceptions (logged; the datagram is consumed, the loop continues) — consistent with the documented "one bad datagram must not kill the serving thread" guarantee.
- **`test_backend.py`** — `TestBackendStatusService` (7 tests: ping round-trip with authenticated identity, status identity/capabilities/version, denial without `CAP_SYSTEM_INFO`, fail-closed without a manager, unknown op, malformed request, internal-error-then-recover) and `test_container_calls_status_service` — a **REAL container** completes a `status` CALL through the auto-registry + server capability enforcement + service capability enforcement (`TestNetworkNamespaceIsolation`). Suite **257 → 265** (239 run + 26 skipped).
- **Docs** — `IMPLEMENTATION_STATUS` v0.19.0; `implementation_plan.md` §4.3; `REPOSITORY_STATE`.

## [0.9.0] — 2026-08-14

### Auto-Maintained Container Sender Registry

#### Added

- **`ipc/registry.py`** — `ContainerIpcRegistry`: the pid → container_id mapping the `IPCDatagramServer` authenticates against (callable, slots directly into `pid_registry`). `register`/`unregister`/`resolve`/`__call__`/`__len__`/`__contains__`, with the exactness contract documented: the mapping is exact for the direct-syscall path (the command is exec'd as PID-1, so `container.pid` IS the kernel-attached sender pid); the legacy `unshare(1)` path is not tracked and its datagrams fail closed.
- **`backend/container.py`** — `ContainerManager(ipc_registry=...)`: registers each direct-syscall container's pid at spawn as early as possible after the pid is known (the e2e's ready-marker handshake guarantees no TOCTOU there; a datagram arriving before registration fails closed, never misattributed) and unregisters on terminate/wait paths.
- **`test_backend.py`** — `TestContainerIpcRegistry` (6 tests: registry semantics incl. the callable, server resolution, spawn-register/terminate-unregister, legacy-path-not-tracked, wait-unregister) and the container→service e2e (`test_container_ipc_call_service`) now uses the **auto-registry** end-to-end — no manual `pid_registry` bookkeeping. Suite **251 → 257** (231 run + 26 skipped).

## [0.8.0] — 2026-08-14

### Rust IPC Transport Hot Path (ADR-0020 migration #6)

#### Added

- **`rust/transport/`** — the sixth-migration crate (ABI 1.0.0, `libc` the only dependency): the per-message syscall half of the Unix-domain datagram transport — `nyrqis_transport_send` (one `sendto`), `nyrqis_transport_recv` (`poll` + `recvmsg` with `MSG_DONTWAIT`, so it never blocks past the timeout and is safe on blocking and non-blocking fds; returns the frame, the kernel-attached global `(pid, uid, gid)` from `SCM_CREDENTIALS`, and the sender's bound path), and `nyrqis_transport_free` — with the seccomp/nyfs/ipc ownership and `-errno`/`ERR_INTERNAL` error contracts. Crate unit tests cover sun_path packing bounds, invalid args, a real round-trip (frame bytes + creds == `getpid/getuid/getgid` + sender path), and the timeout path.
- **`ipc/transport_codec.py`** — the FFI loader: search order (`$NYRQIS_RUST_LIB`, crate `target/release/`, bare name), ABI-version gate, `BackendUnavailable` → Python-floor fallback, `NYRQIS_RUST_FORCE=1` (routing failures become errors), `-errno → OSError` / `-4096 → RuntimeError` mapping. Wired into `UnixDatagramEndpoint.send`/`receive` (raw frames — the wire codec, migration #4, still owns framing); binding/0700/`SO_PASSCRED` stays on the floor.
- **`test_backend.py`** — `TestTransportRustLoader` (9 tests: candidates, error mapping, absent-backend fallback + floor round-trip, force-mode errors, FFI routing with a fake lib for send and recv including the byref-output writes and buffer frees) and `TestTransportConformance` (3 differential tests, skip-gated on the crate: endpoint round-trip with kernel creds + sender path, timeout → None, missing-peer error surfacing). Suite **239 → 251** (225 run + 26 skipped).
- **CI** — `rust-transport` (build + tests + cdylib artifact check) and the required `rust-transport-conformance` gate (transport classes forced through the FFI; raw-wire only, so the separate ipc-codec loader's force check stays honest).

#### Changed

- **`ipc/transport.py`** — `UnixDatagramEndpoint` routes `send`/`receive` through the Rust hot path when the crate is loaded, falling back to the Python floor otherwise (and failing loudly under `NYRQIS_RUST_FORCE=1`).

## [0.7.0] — 2026-08-14

### Over-Transport IPC Latency Benchmark (NPS-003 §6.1 gate data)

#### Added

- **`tests/benchmarks.py`** — `--ipc-transport` (§20): the `call` primitive over the REAL cross-process Unix-domain datagram transport (`ipc/transport.py`) — client and server in separate processes, wire-codec framing, kernel `SO_PASSCRED` identity, 20,000 iterations / 64 B payloads / 200 warmup, raised token budget, ready-marker handshake so the registry never drops a datagram. The in-process honesty note now points at §20 for the wire cost.
- **`tests/BENCHMARK_RESULTS.md` §20** — the gate data point: p50 188.79 µs / p95 295.23 µs / p99 373.51 µs over the transport vs 87.28 µs in-process (same session). **NPS-003 §6.1's <100 µs gate is NOT met at the median** — NPS-003 stays Draft; the ADR-0020 Rust transport is the documented close path.

## [0.6.0] — 2026-08-14

### IPC Transport Hardened + Container-to-Service End-to-End

#### Changed

- **`ipc/transport.py`** — sender identity is now **purely receiver-side**: the sender attaches nothing, and `SO_PASSCRED` makes the kernel attach the real `SCM_CREDENTIALS` `(pid, uid, gid)` to every inbound datagram (verified: even a bare `sendto` carries the kernel-attached credentials). The sender can no longer influence or forge its identity at all, and the explicit-credentials path — whose namespace-scoped pid/uid would be wrong inside a container — is gone. All `TestIPCTransport` security behavior (forgery drop, unknown-sender drop, `CAP_IPC_SEND` enforcement, CALL/REPLY correlation) is unchanged and green.

#### Added

- **`test_backend.py`** — `test_container_ipc_call_service` (`TestNetworkNamespaceIsolation`): a real `network=True` container granted `CAP_NETWORK_SOCKET`/`CAP_NETWORK_BIND`/`CAP_FILESYSTEM_WRITE` runs an `IPCClient` under the active seccomp filter and completes a kernel-authenticated CALL/REPLY with a host-side service over the Unix-domain datagram transport. Suite **238 → 239** (216 run + 23 skipped)

## [0.5.0] — 2026-08-14

### Unix-Domain Datagram IPC Transport

#### Added

- **`ipc/transport.py`** — the inter-process channel for NPS-017 §4.3 (plan §4.3), activating the ADR-0020 migration #4 wire codec as a real transport
  - `UnixDatagramEndpoint` — one `AF_UNIX SOCK_DGRAM` socket bound to a path (0700, path-length guarded), with `SO_PASSCRED` on the receiver and the sender's real `SCM_CREDENTIALS` attached on send
  - `IPCDatagramServer` — serves one endpoint path: parses the wire (malformed datagrams dropped at the trust boundary), authenticates the sender via the kernel-attached `(pid, uid, gid)` mapped to a container, **drops forged `sender_id`s, unknown pids, and senders lacking `CAP_IPC_SEND`** before delivery, then enqueues through the endpoint's token bucket (ADR-0009) or dispatches `CALL`s to an `on_call` handler with direct reply
  - `IPCClient` — the caller side: `send`/`notify`/`call`/`receive` over the socket; `CALL` carries the reply path in `metadata['reply_path']`, replies are correlated by `reply_to` (the client-side trust anchor)
  - `SO_PEERCRED` does NOT work on datagram sockets (returns `(0,-1,-1)` — verified on this host), so `SCM_CREDENTIALS` is the mechanism; an unprivileged sender cannot forge credentials (the kernel refuses a non-matching `ucred` with EPERM)
- **`test_backend.py`** — `TestIPCTransport` (9 tests): authenticated same-process send/receive, unknown-sender drop, forged-sender drop, malformed-wire drop, `CAP_IPC_SEND` denial, in-process CALL/REPLY, **a real cross-process CALL/REPLY with kernel-pid authentication**, inbound ADR-0009 rate limiting, and the socket-path guard. Suite **229 → 238**

#### Changed

- `IMPLEMENTATION_STATUS` 0.14.0; `implementation_plan.md` §4.3 records the landed transport (shared-memory remains deferred)

## [0.4.0] — 2026-08-14

### Loopback Up in Network Containers

#### Added

- **`backend/launcher.py`** — `bring_loopback_up()` (step 2b, before the seccomp install): best-effort `SIOCSIFFLAGS` sets `lo` up so a `network=True` container has a usable 127.0.0.1. It succeeds because the container's netns is owned by its user namespace (where the launcher is root, so CAP_NET_ADMIN applies); sharing the host netns (default) it EPERMs harmlessly — the host's `lo` is already up. Never fatal; runs before the filter so it is backend setup, not container behavior. Covers both launch paths (the launcher runs inside the container either way).
- **`test_backend.py`** — 4 unit tests for `bring_loopback_up` (sets IFF_UP, already-up no-op, EPERM graceful, no-socket graceful) plus an end-to-end bind test: a netns container granted `CAP_NETWORK_SOCKET`/`CAP_NETWORK_BIND`/`CAP_FILESYSTEM_WRITE` binds 127.0.0.1 through the real launch path *with the seccomp filter active* and writes a marker to the shared rootfs (the seccomp data plane correctly EPERMs the marker write without the filesystem grant — caught live). Suite **224 → 229**

#### Changed

- `IMPLEMENTATION_STATUS` 0.13.0; `implementation_plan.md` §4.1 records the usable-localhost posture (veth/bridge remains future work, requires host root)

## [0.3.0] — 2026-08-14

### Network Namespace Support

#### Added

- **`backend/container.py`** — opt-in per-container network namespace isolation (implementation_plan.md §4.1)
  - `ContainerConfig.network` (default `False`): when enabled, the container gets its own network namespace and sees only loopback — a pure isolation boundary; no host interfaces are visible
  - Direct-syscall path: `CLONE_NEWNET` is added to the mount/UTS/IPC unshare in the namespace-setup child (`_direct_launch_child(..., network=...)`)
  - Legacy `unshare(1)` path: `--net` flag added to the launch command
  - Outbound connectivity (veth/bridge) is deliberate future work — the netns is an isolation boundary, not a network pipe
- **`backend/rust_syscalls.py`** — `CLONE_NEWNET` constant exported (the existing `unshare(flags)` FFI passes flags through raw, so no crate change was needed)
- **`test_backend.py`** — `TestNetworkNamespaceIsolation` (2 real-launch tests, skip-gated on an honest host probe that actually launches a netns container): `network=True` container's netns inode differs from the host's and its own procfs lists only `lo`; the default container's netns equals the host's. Plus 5 unit tests (config default, legacy `--net` presence/absence, direct-child `CLONE_NEWNET` flags, manager→child flag forwarding). Suite **217 → 224**

#### Changed

- `IMPLEMENTATION_STATUS` 0.12.0; `implementation_plan.md` §4.1 documents the netns posture (loopback-only, veth future work)

## [0.2.0] — 2026-08-14

### Cgroup Freezer for Suspension

#### Added

- **`backend/container.py`** — cgroup v2 freezer integration for suspension (implementation_plan.md §4.1)
  - `suspend()` now freezes the container's **whole cgroup** via `cgroup.freeze` (write `1`, best-effort confirmation through `cgroup.events`' `frozen 1`) when attached to a v2 cgroup — descendants and future forks cannot outrun the suspension (SIGSTOP alone only stopped PID-1)
  - `resume()` thaws via `cgroup.freeze` (write `0`)
  - `terminate()` thaws a frozen container first so SIGTERM gets its graceful window (a frozen cgroup defers non-SIGKILL signals)
  - SIGSTOP/SIGCONT remains the fallback for v1 hosts (no unified freezer provisioned), failed cgroup setup, and failed freeze writes; a **failed thaw raises** instead — a frozen cgroup defers every signal except SIGKILL, so a SIGCONT fallback would report RUNNING for a process the kernel still holds frozen (the caller retries or escalates to `terminate()`, whose SIGKILL still applies)
  - `_freeze_control()` computes the control file (testable without touching `/sys/fs/cgroup`); `_wait_frozen()` confirms the freeze best-effort
- **`test_backend.py`** — `TestContainerFreezer` (12 tests): control-file decision (v2/v1/no-cgroup), freeze/thaw writes, the raise-on-thaw-failure contract, every fallback path, terminate-thaw ordering, and an end-to-end real-process signal suspend/resume; suite **205 → 217**

#### Changed

- `suspend`/`resume`/`terminate` use `signal.SIGSTOP`/`SIGCONT`/`SIGTERM`/`SIGKILL` constants instead of numeric literals

## [0.1.0] — 2026-07-15

### Initial Implementation

This release provides a complete, structurally-sound implementation of the NyHAL Linux Backend, implementing all five core requirements from NPS-017 §4. The implementation is **Experimental** and requires performance benchmarking and FUSE integration before conformance.

#### Added

##### Core Infrastructure
- **`backend/container.py`** — Container primitives (NPS-017 §4.1, NPS-010)
  - `Container` class with lifecycle state machine (CREATED → RUNNING → SUSPENDED → TERMINATED)
  - `ContainerManager` for managing multiple containers
  - `ContainerConfig` for container configuration
  - `ResourceLimits` for memory, CPU, and process limits
  - Namespace isolation (user, PID, mount, UTS, IPC)
  - Cgroups v2 support with v1 fallback
  - Process suspension/resumption via SIGSTOP/SIGCONT
  - Graceful shutdown with SIGTERM → SIGKILL escalation

- **`backend/capability.py`** — Capability enforcement (NPS-017 §4.2, NPS-011)
  - `Capability` enum with 23 capabilities from NPS-011 registry
  - `CapabilityManager` as sole arbiter of capability validity
  - Capability grant/revoke/validate operations
  - Capability attenuation per NPS-003 §5
  - Audit trail for all capability operations
  - Default capability set for new containers
  - Prevention of self-issued or forged capabilities

- **`ipc/core.py`** — IPC semantics (NPS-017 §4.3, NPS-003)
  - `IPCMessage` with payload, capabilities, and metadata
  - `IPCEndpoint` for receiving messages
  - `IPCManager` for routing and managing endpoints
  - Four primitives: `send`, `receive`, `call`, `notify`
  - Token-bucket rate limiting per ADR-0009
  - Capability transfer and attenuation
  - Synchronous call-reply pattern
  - Async message send
  - Lightweight notifications

- **`fuse/nyfs.py`** — Storage guarantees (NPS-017 §4.4, NPS-004, ADR-0016)
  - `NyFSFilesystem` core with inode management
  - `NyFSBlock` with compression and checksumming
  - Copy-on-Write (CoW) file/directory operations
  - Snapshots: create, restore, list
  - SHA256 checksumming for data integrity
  - Zstandard compression (with fallback if unavailable)
  - `NyFSMount` FUSE wrapper (structural placeholder)

- **`boot/lifecycle.py`** — Boot and lifecycle (NPS-017 §4.5, NPS-001 §5)
  - `BootSequence` with four-phase boot per NPS-001 §5
  - Phase 1: Hardware/Host Initialization
  - Phase 2: Trusted First Process
  - Phase 3: Service Bring-up
  - Phase 4: Usable Session
  - Milestone recording and audit trail
  - Signal handlers for graceful shutdown
  - Boot report generation

##### CLI and Tools
- **`nyrqis_backend.py`** — Command-line interface
  - `boot` command: Start the Nyrqis system
  - `container create/run` commands: Manage containers
  - `capability list/grant` commands: Manage capabilities
  - `ipc endpoint create` command: Create IPC endpoints
  - `filesystem create/snapshot` commands: Manage NyFS

##### Testing
- **`test_backend.py`** — Comprehensive test suite
  - 20 unit tests covering all five core requirements
  - Tests for container primitives and state machine
  - Tests for capability grant/revoke/validate
  - Tests for IPC send/receive/call/notify
  - Tests for storage write/read/snapshot
  - Tests for boot sequence phases
  - Conformance verification tests

##### Documentation
- **`IMPLEMENTATION_STATUS.md`** — Detailed implementation status
  - Requirement-by-requirement breakdown
  - Implementation status for each module
  - Outstanding work and deferred items
  - Conformance assessment
  - Next steps and roadmap

- **`README_IMPLEMENTATION.md`** — Implementation guide
  - Architecture overview
  - Quick start guide
  - Detailed module documentation with examples
  - File structure
  - CLI reference
  - Testing instructions
  - Conformance status
  - References to specifications

- **`requirements.txt`** — Python dependencies
  - zstandard (compression)
  - pytest (testing)
  - sphinx (documentation)

- **`docs/implementation_plan.md`** — Design and implementation plan
  - Overview of Nyrqis vision and principles
  - NyHAL backend requirements
  - Implementation strategy for each requirement
  - Key dependencies and challenges
  - High-level implementation roadmap

#### Changed

- Extended `source/nyhal-linux-backend/README.md` with status of implementation work

#### Notes

##### Architectural Decisions

1. **Container Primitives**: Uses `unshare(1)` for the PoC; production implementation should use direct `clone()`/`unshare()` syscalls for finer control.

2. **Capability Enforcement**: Capability registry and validation logic are complete; LSM/seccomp enforcement is deferred pending integration work.

3. **IPC Semantics**: All four primitives are implemented with token-bucket rate limiting; transport layer (Unix domain sockets or shared memory) is deferred.

4. **Storage Guarantees**: Core NyFS logic is complete; FUSE daemon integration is deferred pending pyfuse3 or fusepy integration.

5. **Boot and Lifecycle**: Four-phase boot sequence is implemented; systemd integration is deferred.

##### Conformance Status

Per NPS-017 §5.1, the Linux Backend is **NOT YET conformant** but provides all five core requirements in some form:

- ✓ Container Primitives: Fully implemented
- ⚠ Capability Enforcement: Registry complete; enforcement deferred
- ✓ IPC Semantics: Fully implemented
- ⚠ Storage Guarantees: Core logic; FUSE integration deferred
- ✓ Boot and Lifecycle: Fully implemented

##### Performance Benchmarks

The following benchmarks are required before conformance:
- IPC Round-trip Latency: < 100µs (NPS-003 §6.1)
- FUSE I/O Overhead: < 20% (ADR-0016)
- Token-Bucket Parameters: TBD (ADR-0009)
- Compression Ratio: > 30% (ADR-0007)

See `tests/BENCHMARK_PLAN.md` for methodology.

##### Next Steps

**Immediate (Phase 1):**
- Refactor container primitives to use direct syscalls
- ~~Implement cgroup freezer for suspension~~ — landed 2026-08-14
- ~~Add network namespace support~~ — landed 2026-08-14
- Run IPC latency benchmarks

**Short-term (Phase 2):**
- Integrate pyfuse3 or fusepy for FUSE daemon
- Implement FUSE operation handlers
- Test CoW and snapshot functionality
- Benchmark FUSE overhead

**Medium-term (Phase 3):**
- Research and integrate LSM (AppArmor or SELinux)
- Implement seccomp-bpf profile generation
- Map capabilities to syscalls
- Test enforcement with real containers

**Long-term (Phase 4):**
- Systemd integration
- Persistent state management
- Health checks and recovery
- Performance optimization
- Full conformance assessment

---

## Revision History

| Version | Date       | Status      | Notes |
|---------|------------|-------------|-------|
| 0.1.0   | 2026-07-15 | In Progress | Initial implementation complete |

---

## References

### Nyrqis Specifications
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- NPS-001: Kernel Architecture and Boot (NyKernel Backend)
- NPS-010: Container Runtime
- NPS-011: Capability Registry
- NPS-003: Inter-Process Communication and Capability Passing
- NPS-004: NyFS Filesystem Core

### Architecture Decision Records
- ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0009: Per-container token-bucket rate limiting for IPC
- ADR-0007: Adopt Zstandard as the default compression codec
- ADR-0006: Adopt a hybrid microkernel as the Nyrqis kernel base

### Other Resources
- NTM-000: The Nyrqis Manifest
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
- REPOSITORY_STATE.md: Project status tracking

---

**End of Document**
