# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nyrqis repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-08-15

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus an externally-contributed,
independently-verified Linux Backend implementation
(`source/nyhal-linux-backend/`, 64/64 tests passing). Milestone 12 — the
phased security threat model — is **complete**: Phases 1–7 are done
(`NPS-018` methodology, `NPS-019` attack surface enumeration, `NPS-020`
STRIDE analysis, `NPS-021` privilege/escalation analysis, `NPS-022`
container escape analysis, `NPS-023` secure boot, `NPS-024` AI,
`NPS-027` package trust). Phase 4
found the most severe issue in the threat model to date (capability
enforcement covers IPC only, not direct syscalls); Phase 6 found the
suggest-vs-act boundary NPC-001 §11.1 depends on has no requirement that
its confirmation UI actually be unspoofable — meaning even a
perfectly-implemented assistant following the spec as written wouldn't
have closed the gap. Across all seven phases, every finding recorded has
a disposition — no bare observations left dangling. Phase 7 (Package
Trust Model, `NPS-027`) landed 2026-08-12, completing the threat model
(see `docs/reference/security/README.md`). A docs-backlog pass
(2026-08-12)
started Milestone 11's gap categories: the Object Registry (NPS-025),
Public API (API-001), ABI (ABI-001), and Package Format (NPS-026,
including the digital-signature design closing `FIND-PACKAGE-001`) now
exist as `Draft` documents; first Tutorials and How-To guides are
published; and every stale category/reference index placeholder has been
replaced with a real index. Still remaining from Milestone 11's
prioritized backlog: governance expansion, build architecture docs,
performance budgets, and developer onboarding — see
`007-PROJECT_ROADMAP.md`.

## Governance Documents

- [x] NTM-000 The Nyrqis Manifest — Accepted
- [x] NPC-001 Project Constitution — Accepted
- [x] NPC-002 AI Collaboration Protocol — Accepted
- [x] NPC-003 Engineering Handbook — Accepted
- [x] NPC-004 Specification Index — Draft
- [x] NPC-005 ADR Index — Draft
- [x] NPC-006 Glossary — Draft
- [x] NPC-007 Project Roadmap — Draft
- [x] NPC-008 Subsystem Owners — Draft (all subsystems currently Unassigned)
- [x] NPC-009 Requirements Database — Draft (in response to external review feedback)

## Architecture Decision Records
11 accepted, 8 held (4 named benchmark blockers plus 4 decisions pending
Architecture Group sign-off, not benchmark-blocked), 1 rejected.

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Accepted
- [x] ADR-0003 Game disk images with overlay — Accepted
- [x] ADR-0004 Containerized execution model — Accepted
- [x] ADR-0005 Windows compatibility translation layer — Accepted
- [x] ADR-0006 Hybrid microkernel as kernel base — Accepted
- [ ] ADR-0007 Zstandard as default compression codec — **Proposed**, first-pass level-sweep data collected (2026-08-12, `tests/BENCHMARK_RESULTS.md` §2) plus an end-to-end NyFS compression ratio of 6.42 : 1 on a synthetic corpus (2026-08-12, §7, `--nyfs-persist`); default-level decision pending Architecture Group review
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Accepted
- [ ] ADR-0009 Per-container token-bucket IPC rate limiting — **Proposed**, first-pass bucket-parameter data collected (2026-08-12, `tests/BENCHMARK_RESULTS.md`); parameter sweep + Architecture Group review pending
- [x] ADR-0010 Vulkan as native graphics API foundation — Accepted
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Accepted
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Accepted
- [ ] ADR-0013 EEVDF-derived scheduler with real-time priority class — **Proposed**, algorithm family decided, tuning parameters blocked on benchmark data
- [ ] ADR-0014 UEFI Secure Boot with user-enrollable keys — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0015 Shared dynamic binary translation for ARM/x86 — **Proposed**, approach decided; performance validation blocked on benchmark data
- [ ] ADR-0016 NyFS Linux Backend as user-space FUSE filesystem — **Proposed**, initial strategy decided; kernel-module fallback blocked on FUSE-overhead benchmark data
- [x] ADR-0017 Reject domain-grouped NPS renumbering — **Rejected** (the project's first; considered and explicitly declined, not left unresolved)
- [ ] ADR-0018 Hash-chained append-only log for capability audit records — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0019 Journal commit as the default NyFS save() mode — **Proposed**, review package for the 2026-08-12 implementer default flip; daemon lifecycle design note (`source/nyhal-linux-backend/DAEMON_LIFECYCLE.md`) answers its open question 1, AG tuning review pending
- [x] ADR-0020 Implementation languages and the platform boundary — **Accepted** (v2.0.0, 2026-08-13), canonical language matrix (Rust/C++/C platform languages; NyHAL resolved Rust-first) + platform-boundary principle: platform-critical execution paths must not depend on the Python interpreter; supersedes v1 (Python + Rust, 2026-08-12); Architecture Group acceptance recorded in issue #2 (closing the issue itself is a manual step — the PAT cannot comment/close issues)
- [x] ADR-0021 NyRuntime direction — IPC serving loop behind the FFI boundary — **Accepted** (2026-08-15), close gate met (wire p50 82–95 µs vs <100 µs target, §22)
- [x] ADR-0022 NyVault — storage as a daemon-hosted service on the IPC transport — **Proposed** (2026-08-15) but IMPLEMENTED through the accounting increments (0.14.10–0.14.19: quotas, warnings, event ring, path-scoped grants, subtree quotas); Architecture Group review pending
- [x] ADR-0023 NyVault key manager — envelope encryption with Rust-held key custody — **Proposed** (2026-08-15) but IMPLEMENTED (at-rest encryption claimed: per-volume DEKs, KEK custody crate, AEAD block layer); Architecture Group review pending
- [ ] ADR-0024 Streaming data plane — chunked framing for large CALL payloads — **Proposed** (2026-08-16), drafted as the documented next step of ADR-0022's data plane (the 32 KiB per-call cap, §27 paging cost); implementation pending Architecture Group review + the §29 evidence run (`--vault-stream`)

## Specifications (NPS)
13 accepted, 14 held (4 named benchmark/dependency blockers, plus
NPS-018..NPS-024 and NPS-027 — threat-model phase documents, Draft
pending Architecture Group sign-off — and NPS-025, NPS-026 — Draft
documents from the 2026-08-12 Milestone 11 backlog pass).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted (v1.2.0: GPU command buffer validation + submission timeout added, closing threat model findings FIND-KERNEL-001/003)
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking); v1.1.0 added a shared-memory zeroing requirement, closing threat model finding FIND-CONTAINER-003; v1.2.0 recorded first-pass in-process latency (p50 92 µs vs <100 µs target — tail exceeds); the real Unix-domain datagram transport shipped 2026-08-14 and its over-transport measurement landed the same day (BENCHMARK_RESULTS.md §20: p50 188.79 µs / p95 295.23 µs / p99 373.51 µs) — the §6.1 gate is NOT met at the median, so NPS-003 stays Draft with the ADR-0020 Rust transport as the documented close path
- [x] NPS-004 NyFS Filesystem Core — Accepted
- [ ] NPS-005 Transparent Compression Policy — **Draft**, transitively blocked on ADR-0007 (defines default levels tied to the still-Proposed codec ADR)
- [x] NPS-006 Nyrqis Game/Application Image Format (.nygi) and Overlay — Accepted
- [x] NPS-007 Windows Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-008 Android Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-009 Adaptive UI Shell — Accepted (VR resolved: explicitly deferred to a future milestone, not an open mode definition)
- [ ] NPS-010 Container Runtime — **Draft**, transitively blocked on ADR-0009 (§7.1 normatively requires its still-Proposed rate-limiting mechanism); v1.1.0 added atomic grant-check (§4.2) and tamper-evident audit log requirement (§8.1, per new ADR-0018), closing threat model findings FIND-CAPABILITY-001/002
- [x] NPS-011 Capability Registry — Accepted (27 capabilities registered: 25 through Milestone 10, minus 1 split into 3 this pass — `CAP-MEDIA-LIBRARY` → `CAP-MEDIA-IMAGES`/`CAP-MEDIA-VIDEO`/`CAP-MEDIA-AUDIO`, closing threat model finding FIND-CAPABILITY-004; still intentionally incomplete by design)
- [x] NPS-012 Controller and Input Subsystem — Accepted (VR capability formally deferred, not left ambiguous — §5.1)
- [x] NPS-013 GPU Feature Support — Accepted (§7.3 documents current FSR/XeSS/FSR4 vendor SDK status, verified 2026-07-13)
- [x] NPS-014 Emulator Hub — Accepted
- [x] NPS-015 Local AI Assistant — Accepted (v1.1.0: four amendments closing threat model Phase 6 findings — unspoofable confirmation UI, corrected file-search capability, persistence-mechanism exclusion, suggestion audit log)
- [x] NPS-016 Optional Cloud Synchronization — Accepted
- [x] NPS-017 NyHAL — Kernel Abstraction Layer and Backend Contract — Accepted
- [x] NPS-018 Threat Model Methodology and Trust Boundaries — Draft (Threat Model Phase 1a)
- [x] NPS-019 Attack Surface Enumeration — Draft (Threat Model Phase 1b, 24 surfaces catalogued)
- [x] NPS-020 STRIDE Analysis per Trust Boundary — Draft (Threat Model Phase 2, 10 boundaries, 3 findings drove real spec amendments this pass)
- [x] NPS-021 Privilege Boundaries and Capability Escalation Analysis — Draft (Threat Model Phase 3, 5 findings — 4 resolved, 1 governance-level recorded not technically fixed)
- [x] NPS-022 Container Escape Analysis and Runtime Isolation — Draft (Threat Model Phase 4, grounded in the real Linux Backend code; found capability enforcement covers only IPC send/call, not direct syscalls — the most severe finding to date, flagged as the implementation's top priority)
- [x] NPS-023 Secure Boot Threat Model — Draft (Threat Model Phase 5, first full pass on TB-BOOT; found zero Secure Boot status visibility on the Linux Backend and unvalidated boot-phase transitions; a measured-boot/TPM gap logged as not fixable by amendment)
- [x] NPS-024 AI Threat Model — Draft (Threat Model Phase 6, first full pass on TB-AI, no implementation exists yet; found the suggest-vs-act boundary's confirmation UI isn't required to be unspoofable — the most conceptually significant finding since Phase 4's capability-enforcement gap)
- [x] NPS-025 Object Registry — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 2; 14 object types catalogued, Identity flagged pending its own NPS)
- [x] NPS-026 Package Format (.nypkg) — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 7 and FIND-PACKAGE-001; signed manifests + integrity trees proposed, concrete crypto scheme pending dedicated human review per NPC-002 §6.2)
- [x] NPS-027 Package Trust Model — Draft (Threat Model Phase 7, 2026-08-12, completing Milestone 12; disposition of FIND-PACKAGE-001 plus 4 new findings closed via NPS-006 §6 amendment and REQ-SEC-0003..0006)

## Requirements Database
NPC-009 (Draft) + seed ledger at `docs/reference/requirements/REQUIREMENTS.md`:
40 requirements across all 17 domain prefixes. Nearly all traced to
`Accepted` specs; two (`REQ-IPC-0003`, `REQ-IPC-0004`) trace to
still-`Draft` NPS-003, called out explicitly rather than silently
overstating coverage quality. One entry (`REQ-NYHAL-0003`) marked
`Implemented (partial)`, referencing the `nyctr` PoC with an explicit
caveat about what it doesn't cover. Not full coverage of NPS-001..021 by
design (NPC-009 §7.3) — expand incrementally, and going forward new
normative additions should cite a
REQ ID from the start (NPC-009 §7.2).

## ABI / API References
Draft: [`API-001`](../reference/api/API-001-public-api.md) (Public API —
areas, layering, naming/versioning conventions, error model; exact
signatures deferred to implementation) and [`ABI-001`](../reference/abi/ABI-001-binary-compatibility.md)
(Binary Compatibility — compatibility rules, IPC wire format per NPS-003
§9's deferral, symbol/plugin/driver/runtime/backend ABIs).

## Package Format
Draft: [`NPS-026`](../reference/package-format/NPS-026-package-format.md)
(.nypkg — signed manifest, integrity trees, compression, delta updates,
streaming install, rollback, dependencies). This is the package-format
NPS deferred by NPS-006 §2/§9, and the response to `FIND-PACKAGE-001`
(checksums alone don't establish publisher authenticity).

## Object Registry
Draft: [`NPS-025`](../reference/object-registry/NPS-025-object-registry.md)
— every object type (Workspace, Window, Application, Package,
Capability, Game, Mod, Controller, GPU, Notification, AI Conversation,
Device, Service; Identity flagged pending its own NPS) with fields,
lifecycle, permissions, serialization rules, and relationships.

## Source Code
Two things now, not one:

- `source/nyhal-linux-backend/poc-container/` (`nyctr.py`) — the original
  spike: proves the most basic container primitive (PID/mount/UTS/user
  namespace isolation + a cgroup memory/pid limit) works on stock Linux.
  Superseded in scope by the item below but kept as the minimal reference
  it was designed to be.

- `source/nyhal-linux-backend/` — a substantially fuller Linux Backend
  implementation (`backend/container.py`, `backend/capability.py`,
  `backend/seccomp.py`, `backend/launcher.py`, `ipc/core.py`,
  `fuse/nyfs.py`, `boot/lifecycle.py`), contributed
  externally (not authored in this session — merged from the remote after
  a `git push` conflict surfaced it) and **independently verified before
  being documented here**: `python3 test_backend.py` passes
  54/54. Real cgroup v1/v2 detection and namespace usage confirmed by
  reading the code, not assumed from its own claims.

  Its own `IMPLEMENTATION_STATUS.md` (`document_id: IMPL-001`, v0.2.0)
  self-rates as **"Experimental Backend — Core Implementation Complete,
  Performance/Integration Work Pending,"** explicitly **not yet
  conformant** to NPS-017 §5: data-plane enforcement exists via an
  in-container seccomp-BPF filter (default-allow deny model; a
  default-deny allowlist posture is the strictly-stronger follow-up),
  `openat2` write-intent is not flag-filterable from classic BPF
  (documented residual gap), LSM integration is deferred, and no IPC
  latency, FUSE overhead, or compression benchmarks exist. That
  self-assessment reads as accurate against the code, not inflated —
  consistent with this project's existing discipline.

  **Reconciled with Phases 4 and 5 of the threat model** (`NPS-022`,
  `NPS-023`; the findings were recorded this session, and the fixes
  landed this session too):
  - `FIND-BACKEND-002` (the most severe finding to date — capability
    enforcement covered only IPC `send`/`call`, leaving direct syscalls
    unmediated) is **closed** by `backend/seccomp.py` +
    `backend/launcher.py`: capability sets compile to a cBPF filter
    installed inside the container before its command runs. Verified
    end-to-end on this host — a read-only container's write-capable
    `openat` is refused with `EPERM` at the syscall level.
  - `FIND-BACKEND-003` (cgroup v1 `release_agent` exposure) is **closed**
    by `notify_on_release=0` on the container's v1 cgroups plus
    best-effort unmount of leaking cgroup mounts in the launcher.
  - `FIND-BACKEND-004` (shell interpolation of container-supplied
    strings) is **closed** by the shell-free launcher: hostnames and
    commands are argv entries, and `sethostname(2)` is called directly.
  - `FIND-BOOT-001` (zero Secure Boot status visibility) is **closed** by
    `boot/lifecycle.py`'s efivars + mokutil probing (`secure-boot-status`).
  - `FIND-BOOT-002` (unvalidated boot-phase transitions) is **closed** by
    legal-transition validation in `boot/lifecycle.py`.
  - `FIND-CAPABILITY-004` (capability granularity mismatch) was already
    closed at the spec level by splitting `CAP-MEDIA-LIBRARY` into
    images/video/audio; the backend's `Capability` enum now reflects it.
  - IPC `receive` now checks the receiver holds `CAP_IPC_RECEIVE`
    (control-plane enforcement widened beyond `send`/`call`).
  - FUSE is no longer structural-only: `fuse/nyfs.py` gained a path API,
    full operation handlers, and `fusepy` mount wiring (ADR-0016).

  Since 2026-08-12, `backend/seccomp.py` also carries the **ADR-0020 FFI
  loader** for the first Rust migration: it locates the Rust seccomp
  cdylib (`$NYRQIS_RUST_LIB` → crate `target/release/` →
  `LD_LIBRARY_PATH`), ABI-version checks it, and routes
  `build_program`/`validate_program`/`simulate` through the FFI, falling
  back to pure Python on any failure. `NYRQIS_RUST_FORCE=1` turns
  failures into errors — the conformance gate CI watches.

  **2026-08-16 (0.14.22): the NUI (.nstudio) runtime consumption lands
  (ADR-0025).** The Nyrqis side of the NyForge ↔ runtime pipeline:
  `ui/nstudio.py` (pure-Python reference floor — parse, contract
  validation, `$state:` substitution, layout render, text preview),
  `rust/nyui/` (the Rust import gate, ABI 1.0.0 — the UI layer's first
  compiled artifact, per ADR-0020), `ui/nstudio_codec.py` (the standard
  FFI loader), the four NyForge example designs as fixtures under
  `tests/fixtures/nstudio/` (including the 1440×900 `nyrqis-shell` UI
  draft), and `TestNstudioImport` + `TestNstudioCodecConformance` (32
  tests; differential messages byte-identical floor↔crate). CI gains
  `rust-nyui` + `rust-nyui-conformance` (required gate).

  **2026-08-16 (0.14.23): the import gate rides the control plane.**
  `NuiService` (`ui/service.py`) exposes `nui_validate` / `nui_load` /
  `nui_current` over the datagram control plane — operator-only
  (registered containers refused), per-call document budget, `nui_load`
  persists the design as the daemon's shell UI, `nui_current` surfaces
  what is loaded (re-imported through the gate on every call; stale
  persisted designs reported honestly as `valid: false`) — with
  `nyrqisctl nui validate|load|current` as the CLI (e2e verified
  against a live daemon with the Rust crate as the engine). The
  Security Center (`security-center.nstudio`) and Vault Workspace
  (`vault-workspace.nstudio`) screens — the second and third NyForge
  designs, 71 components / 4 behaviors / 1 binding each — join the
  fixtures with shape + `$state:` tests. `tests/benchmarks.py --nui`
  (§30) A/Bs the gate floor-vs-crate: crate ~2.1× faster at the median
  (242 µs vs 502 µs p50). Suite 524 → **538**.

  **2026-08-17 (0.14.24): the Nyrqis API Registry lands (one
  machine-readable contract, three consumers).** The NUI component
  vocabulary now lives in `ui/contracts/nui-api-v1.json` — the Python
  floor loads its tables from it at import time, the Rust crate embeds
  the same file (`include_str!` → `OnceLock<Registry>`), and Nyforge
  regenerates its C# tables from a vendored copy. `TestNstudioCodecConformance`
  passes unchanged (floor↔crate cannot diverge — same file). Full
  suite: **538** (unchanged — the migration is behavior-preserving).

  **2026-08-17 (0.14.25): the first real Shell component set.** The
  registry grows to 63 components across five new categories — Shell,
  Data, Form, Media, Developer — each with a real semantic contract
  (Taskbar position/alignment/autoHide/…, WindowFrame
  Minimize/Maximize/Restore/Close, …). All three consumers pick it up
  automatically; import-gate tests that used `Taskbar` as the unknown-type
  example now use `BogusWidget`. Suite stays 538.

  **2026-08-17 (0.14.26): the real desktop shell screen.**
  `desktop.nstudio` — a 1440×900 desktop (DesktopSurface/DesktopIcons,
  Taskbar, StartMenu, CommandPalette, NotificationCenter, QuickSettings,
  WorkspaceSwitcher) plus a `lock` screen (LockScreen) — 30 components, 8
  behaviors, 6 bindings, authored with the shell vocabulary and accepted
  by the floor, the Rust crate, and Nyforge's own serializer. Suite
  538 → **539**.

  **2026-08-17 (0.14.27): the window system + power UI.**
  `windows.nstudio` — WindowFrame/WindowControls driving
  component-targeted actions (Minimize/Maximize/Close), stacked
  windows, and a PowerMenu with Sleep/Restart/Shutdown — 21 components,
  8 behaviors, 1 binding across 2 screens; accepted by the floor, the
  crate, and Nyforge's serializer. Suite 539 → **540**.

  **2026-08-17 (0.14.28): widgets + OSD + login.** `WidgetHost`,
  `OSD`, `Login` join the registry (66 components); `widgets.nstudio`
  — WidgetHost cards, a volume OSD, a Login form  — 19 components, 5
  behaviors, 2 bindings across 3 screens; accepted by floor, crate,
  and Nyforge's serializer. Suite 540 → **541**.

  **2026-08-17 (0.14.29): typed property metadata in the registry.**
  `properties` become metadata objects (name/type/default/bindable/
  required + min/max/enumValues/units where meaningful); vocabulary
  unchanged. Floor parses names, the crate's serde structs carry the
  full PropertyDefinition, Nyforge regenerates ComponentContracts.cs
  + the new PropertyDefinitions.cs. Suite stays 541.

  **2026-08-17 (0.14.30): reusable component masters (NFS-006 §9).**
  `components[]` holds reusable masters; instances declare
  `componentRef` + `overrides` and omit `type` (both gates reject an
  instance with its own type; overrides must fit the master's contract)
  — enforced identically by the floor and the crate (differential
  tests). The `desktop.nstudio` taskbar is built from one
  `TaskbarButton` master with two instances; Nyforge materializes
  instances via `ReusableComponentResolver`. Suite 541 → **546**; Nyforge
  71/71.

  **2026-08-17 (0.14.31): responsive layout constraints (NUI-SCHEMA
  §4.1).** `layout` gains optional anchors (all default false), min/max
  bounds, and `aspectRatio`, validated identically by both gates
  (differential). `resolve_layout()` adapts any container size (stretch
  on both-horizontal anchors, bottom-dock, aspect derivation) and
  `text_preview()` shows adapted bounds. The desktop shell's taskbar
  stretches and docks itself; an icon carries `aspectRatio: 1.0`.
  Suite 546 → **562**; Nyforge 118/118.

  **2026-08-17 (0.14.32): localization (NUI-SCHEMA §8.1).** A document's
  `locales` section (`active` + per-locale string tables) resolves
  `$localize:key` references in component properties, reusable
  overrides, and behavior arguments; refs must exist in the active
  locale's table, enforced fail-closed by both gates with byte-identical
  messages. `resolve_text()` resolves them. The shell fixture's search
  label and DND message are localized (en/af).  Suite 562 → **573**;
  Nyforge 127/127.

  **2026-08-17 (0.14.33): resources — the managed asset catalog
  (NUI-SCHEMA §8.2).** A document's `resources` section (unique ids,
  allowed kinds, non-empty paths, optional 64-hex sha256) is validated
  by both gates; `$asset:id` references in properties and overrides
  must name a declared resource (fail-closed, byte-identical messages).
  The shell fixture's wallpaper is a declared image asset referenced
  via `$asset:wallpaper`. Suite 573 → **585**; Nyforge 135/135.

  **2026-08-17 (0.14.34): the NUI expression language (NUI-SCHEMA
  §7.2).** `ui/nexpr.py` is the deterministic expression language
  (`state.name` refs, comparisons, `&&`/`||`/`!`, and
  `if`/`min`/`max`/`contains`/`format`) with position-tagged syntax
  errors. `$expr:` values (properties, overrides, action arguments) and
  condition `expression` fields (superseding the legacy equality form)
  are validated fail-closed by **both gates** with byte-identical
  messages and evaluated at resolution time (`resolve_action` /
  `resolve_condition`). `rust/nyui/src/nexpr.rs` is the byte-for-byte
  Rust mirror (differential-tested; crate 9 → 13 unit tests). The shell
  fixture's DND condition is `state.doNotDisturb == true` and its
  notification title is `$expr:format(state.clockTime, "{0}")`; Nyforge
  mirrors the gate as ER-NUI-021 before Preview (one semantics across
  Nyforge / floor / crate). Suite 585 → **604**; Nyforge 163/163.

  **2026-08-17 (0.14.35): declarative animations (NUI-SCHEMA §8.3).**
  The document's `animations` section — unique ids, a target that must
  name an existing component, a non-empty property, and timing
  (duration/delay/repeat non-negative; easing linear|ease-in|ease-out|
  ease-in-out|steps; direction forward|reverse|alternate) — is
  validated identically by both gates. The registry gains the
  `Nyrqis.Animation.Play` system action; a behavior using it must
  reference a declared animation (byte-identical messages,
  differential). The desktop shell's Start menu fade plays on toggle;
  Nyforge mirrors the gate as ER-NUI-022 (contracts regenerated from
  the registry). Suite 604 → **619**; Nyforge 173/173. Keyframes are
  the documented follow-on.

## Build System
Started 2026-08-12. CI (`.github/workflows/ci.yml`) runs on every push/PR
and is the first place the Rust crate compiles (the dev host has no Rust
toolchain):

- `rust-seccomp` — builds and tests the ADR-0020 first-migration crate
  (`source/nyhal-linux-backend/rust/seccomp`, cargo build --release +
  cargo test).
- `rust-seccomp-conformance` — **non-blocking** gate that forces the
  full Python test suite through the Rust module via the FFI loader
  (`NYRQIS_RUST_FORCE=1`); it fails while the crate is scaffold-only and
  turns green automatically when the port lands.
- `backend` — the pure-Python test suite (`python3 -B test_backend.py`),
  the correctness floor.

`docs.yml` (docs site build + GitHub Pages deploy) remains separate.

## Documentation Site
Structure created; MkDocs Material configured with full nav (zero warnings
under `mkdocs build --strict`); CI workflow (`.github/workflows/docs.yml`)
builds and deploys to GitHub Pages on push to `main`. Version pinned via
`requirements-docs.txt` due to MkDocs Material's own public warning about
breaking, currently-unsuitable-for-production changes in MkDocs 2.0.

## Next Actions
Benchmark-gated (unblocks the 3 ADRs + 4 NPS documents held above).
First-pass data for four of the seven items landed 2026-08-12
(`tests/BENCHMARK_RESULTS.md`); the remaining items still have no
measurements:
1. ~~Benchmark IPC round-trip latency (unblocks NPS-003, transitively
   NPS-010's remaining path once ADR-0009 also clears).~~ **First-pass
   data collected 2026-08-12** — p50 92 µs / p95 157 µs / p99 213 µs,
   in-process only; the over-transport measurement landed 2026-08-14
   (BENCHMARK_RESULTS.md §20): p50 188.79 µs / p95 295.23 µs / p99
   373.51 µs vs 87.28 µs in-process — §6.1's <100 µs gate is NOT met
   at the median over the real transport. The Rust transport hot path
   (ADR-0020 migration #6, rust/transport) shipped the same day as the
   documented close path. **Delta measured 2026-08-14 (same-session
   A/B, BENCHMARK_RESULTS.md §20): the v1 FFI surface (per-recv
   malloc) was SLOWER than the floor (wire p50 ~426 µs Rust vs ~231 µs
   floor). FFI surface v2 (ABI 2.0.0, same day) removes the
   allocation — recv writes directly into the caller's reusable
   buffer, send is zero-copy — and measured wire p50 307–357 µs
   (~28% under v1, ~1.6× the ~200 µs floor) with the residual being
   the ctypes boundary tax, not a bug. The migration stands on the
   boundary rule + the byte-identical conformance gate; NPS-003 stays
   Draft, gate open (closing it needs the serving loop behind the
   boundary — the NyRuntime direction).**
2. ~~Benchmark default IPC token-bucket parameters (unblocks ADR-0009,
   then NPS-010 §7.1).~~ **First-pass data collected 2026-08-12** — the
   default bucket (100 burst, 50/s refill) sustains only ~99.5 calls/s
   on a client→endpoint path and throttles ~18.9k calls/s at full speed;
   the defaults are demonstrably too low for this workload shape.
   Parameter sweep, adversarial test, and Architecture Group review
   still pending.
3. ~~Benchmark Zstd compression levels, install size vs. load time
   (unblocks ADR-0007, then NPS-005).~~ **First-pass data collected
   2026-08-12** — level sweep on a synthetic corpus (overall ratio 2.54
   at levels 1–5 vs 3.17 at ≥7; compression 0.6–3.5 GB/s); a real asset
   corpus, the LZ4 fast-path comparison, and concurrent-load CPU
   measurement remain, and the default-level decision belongs to
   Architecture Group review.
4. Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning (unblocks ADR-0013 in full; algorithm family is already decided).
5. Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).
6. Benchmark FUSE overhead for NyFS's Linux Backend (ADR-0016;
   determines whether the FUSE decision holds or needs a kernel-module
   fallback). **Proxy data re-run 2026-08-12 after the per-block CoW
   rewrite** (`tests/BENCHMARK_RESULTS.md` §5): streaming 1 MiB-chunk
   writes ~162 MB/s (~4× the old whole-file 40.5 MB/s) vs 541–771 MB/s
   native; small 4 KiB ops are now dominated by per-call block compress
   + per-read SHA-256 verification (~3.6 MB/s write / ~2.8 MB/s read),
   with the checksum-verification read cost recorded as the key finding
   for Architecture Group review. **Live-mount first-pass data
   collected 2026-08-12** (`tests/BENCHMARK_RESULTS.md` §6): this host
   turned out to have fusepy + `/dev/fuse` all along, so the real
   kernel mount was measured — writes ~1.8–2.2 MB/s, bounded by the
   kernel's 4 KiB write batching × 64 KiB CoW blocks (256 requests per
   1 MiB write), reads ~25–37 MB/s (readahead-batched); durability and
   CoW snapshots verified end-to-end through the kernel path
   (`TestNyFSLiveMount`). **The write-batching limit was then fixed
   2026-08-12** by negotiating `FUSE_CAP_BIG_WRITES` +
   `FUSE_CAP_WRITEBACK_CACHE` + `FUSE_CAP_MAX_PAGES` in the INIT
   handshake (`NyFSMount` `writeback_cache=True`, default): writes now
   batch at 128 KiB and stream at ~40–46 MB/s (~25×). No gate declared
   met.
7. Benchmark hash-chain computation/verification overhead before ADR-0018 exits Proposed — expected to be negligible but not asserted as fact without a measurement, per NPC-002 §5.2.

Genuinely still open, not fabricable:
8. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) — requires actual contributors, not something to invent.
9. Choose a real license (`LICENSE` is still the Milestone 1 placeholder — "no rights granted... until a formal license is adopted"). This is a legal/business decision for the repository owner, not one to pick unilaterally on their behalf.
10. ~~Enable GitHub Pages with source "GitHub Actions" (Settings → Pages)
    so `.github/workflows/docs.yml`'s deploy step has somewhere to publish
    to — the workflow runs regardless, but won't be visibly served until
    this is set.~~ **Done 2026-08-12** — Pages is enabled with source
    `GitHub Actions` on `main`; the site is served at
    `https://myco-mycelium.github.io/Nythera/` (the URL will move to
    `.../Nyrqis` when the repository is renamed per `REBRAND_NOTICE.md`).
    The first deploy that ran before Pages was enabled failed only at the
    `actions/deploy-pages` step; the push carrying this status update
    re-triggers the workflow, which should deploy cleanly.
11. Revisit `NPC-008`'s "claim an Unassigned slot without a vote" design once the project has more than one active contributor — `FIND-CAPABILITY-005` (NPS-021 §5.4) flagged this as a soft privilege path, recorded against the governance document rather than given a runtime fix that wouldn't be the right tool for it.
12. Design a measured-boot/TPM attestation story once a concrete need justifies it (`FIND-BOOT-003`, NPS-023 §4) — not fixable by a quick amendment, same category as the package-signing gap.

Implementation now needs to catch up to what the threat model has already
decided at the spec level — none of these are documentation tasks:
- ~~Implement data-plane capability enforcement (seccomp/LSM) in
  `source/nyhal-linux-backend/backend/capability.py` — `FIND-BACKEND-002`
  (NPS-022 §4) found capability tracking exists but enforcement covers
  only IPC send/call, leaving direct syscalls completely unmediated.~~
  **Done this session** — `backend/seccomp.py` + `backend/launcher.py`
  install an in-container cBPF filter; verified end-to-end. Follow-ups:
  default-deny allowlist posture, LSM integration, and the documented
  `openat2` flag-inspection gap.
- ~~Wire the cgroup v1 `release_agent` hardening and shell-interpolation
  hygiene fixes (`NPS-017` §4.1) into `backend/container.py`.~~ **Done
  this session** (`notify_on_release=0`, launcher-level unmount,
  shell-free `sethostname`).
- ~~Implement Secure Boot status reporting (`REQ-BOOT-0004`, `NPS-017`
  §4.5) and boot-phase transition validation (`FIND-BOOT-002`, `NPS-001`
  §5) in `boot/lifecycle.py`.~~ **Done this session**
  (`secure-boot-status` CLI; legal-transition validation).
- ~~Once 13–15 land, correct `IMPLEMENTATION_STATUS.md`'s own conformance
  claims to reflect them rather than leaving it describing the
  pre-fix state.~~ **Done** — `IMPL-001` v0.2.0 (2026-08-12) now
  describes the enforced state and its residual gaps.

Process and tooling:
17. Wire `tools/check_depends_on_cycles.py` into `.github/workflows/docs.yml`
    as a CI step. It found 4 real circular dependencies this pass
    (NPS-001↔ADR-0012, NPS-001↔ADR-0013, NPS-001↔ADR-0014,
    NPS-007/008↔ADR-0015 — each individually reasonable when added, only
    circular together) that had been sitting in already-committed,
    already-pushed documents undetected. Running it by hand caught them
    this time; it should run automatically going forward.
18. Elevate priority on Milestone 11's package-format gap category
    (specifically digital signatures) — Phase 2's `FIND-PACKAGE-001`
    found that `.nygi` integrity currently relies on checksums alone,
    which don't establish publisher authenticity; an attacker can tamper
    with an image and simply recompute a valid checksum. Not fixable by a
    quick amendment; needs a real package-signing/PKI specification.
19. Continue Milestone 11's remaining prioritized backlog
    (`007-PROJECT_ROADMAP.md`) — diagrams, API reference, ABI
    specification, object registry, and package format are now `Draft`
    (2026-08-12 pass); governance expansion, build architecture docs,
    performance budgets, and developer onboarding remain. Each is
    roughly the size of a prior milestone on its own.
20. Continue the threat model (Milestone 12, `docs/reference/security/`):
    Phase 7 (Package Trust Model, extending NPS-006, already well-motivated
    by `FIND-PACKAGE-001`) is the last planned phase.
21. Once an AI assistant implementation begins, build it against the
    amended `NPS-015` from the start — a protected confirmation UI
    (`REQ-AI-0003`), suggestion audit logging via `ADR-0018`'s mechanism
    (`REQ-AI-0004`), and the corrected file-search capability scoping —
    rather than building the naive version and retrofitting these later.

Resolved earlier this session, kept here for a complete record:
- ~~Resolve shared ARM instruction-translation approach~~ — ADR-0015 (shared dynamic binary translation, JIT + hot-path cache).
- ~~Scope VR integration~~ — explicitly deferred to a future milestone (NPS-012 §5.1).
- ~~Evaluate vendor-neutral upscaling integration point~~ — NPS-013 §7.3, grounded in vendor SDK research.
- ~~Decide NyFS's Linux Backend implementation strategy~~ — ADR-0016 (FUSE first, kernel-module fallback open pending benchmark #6).
- ~~Decide secure boot key management~~ — ADR-0014 (UEFI Secure Boot, shim-equivalent chain, user-enrollable keys).
- ~~Configure CI build for the MkDocs Material site~~ — `.github/workflows/docs.yml`, verified locally with `mkdocs build --strict` before committing.
- Expand NPS-011 Android permission mapping — 8 new capabilities added; still intentionally incomplete per NPS-011 §6.

Documentation hygiene, fixed earlier this session:
- A prior review pass (Milestone 9) left several documents with a Markdown
  table-formatting bug: the row recording that milestone's own review
  had gotten separated from its revision-history table by a blank line.
  Affected all 13 `Accepted` NPS documents from that review; fixed and
  verified via `mkdocs build --strict` and a repo-wide grep, now clean.
- `mkdocs.yml`'s `repo_url` was still the bootstrap placeholder; corrected
  to the canonical GitHub repository `Myco-mycelium/Nythera` (the
  repository name is intentionally unchanged by the 2026-08-12 rebrand —
  see `REBRAND_NOTICE.md`).

## Documentation Hygiene Notes *(ongoing)*
- 2026-08-13 (**ADR-0020 migrations #1 and #2 implemented**): the
  `rust/seccomp` crate (policy compiler / validator / simulator) and the
  `rust/syscalls` crate (`sethostname`/`prctl`/`unshare` wrappers;
  `clone` deferred pending the direct-syscall child-entry-point design)
  are implemented, CI-built and unit-tested on every push, and held
  equal to the pure-Python implementations by golden byte-identical
  tests plus a seeded differential test
  (`test_rust_and_python_agree_differentially`). The forced-mode seccomp
  conformance gate (`NYRQIS_RUST_FORCE=1`, CI
  `rust-seccomp-conformance`) is **green and now a required job**;
  `backend/rust_syscalls.py` (the shared syscalls loader with ctypes
  fallback and force mode) is wired into `launcher.set_hostname`. The
  dev host still has no Rust toolchain — CI is the compiler. Test
  suite: 125/125 (113 + 12 new).
- 2026-08-13 (**direct-syscall launcher landed, ADR-0020 priority #2**):
  `container.py` now launches containers by default via direct
  `unshare(2)`/`fork(2)` syscalls (`_spawn_direct`): the manager forks a
  namespace-setup child that performs `unshare(CLONE_NEWUSER)` + root
  uid/gid maps (ids captured BEFORE the unshare — the classic 65534
  map-write failure), `unshare(NEWNS|NEWUTS|NEWIPC)`, `unshare(CLONE_NEWPID)`,
  then forks the container's PID-1 which mounts a hardened procfs via
  the new no-arg `mount_proc` FFI (post-fork, pre-exec — zero Python
  allocation) and execs the launcher. The setup child relays the
  container PID through a pipe and exits with its status (or dies by
  its signal), so `wait()` keeps Popen-compatible semantics. `rust/syscalls`
  is now ABI 1.1.0 (`sethostname`/`prctl`/`unshare`/`mount`/`mount_proc`);
  `unshare(1)` remains an opt-in legacy path (`use_direct_syscalls=False`).
  CI gains the required `rust-syscalls-conformance` gate (syscalls-facing
  test classes forced through the FFI). Verified on this host: hostile
  hostname `evil; rm -rf /` passed verbatim (FIND-BACKEND-004), PID-1 in
  the new PID namespace, seccomp filter active, suspend/resume/terminate
  lifecycle, legacy path still works. Test suite: **185/185 (169 run + 16
  skipped without the Rust crates: the seccomp differential and the
  syscalls/NyFS/IPC conformance classes — all RUN in CI where the
  crates are built)**.
- 2026-08-13 (**ADR-0020 migration #3: NyFS block codec in Rust**):
  `rust/nyfs/` (ABI 1.0.0) ships the storage hot paths — SHA-256
  per-block checksum (NPS-004 §4) and Zstandard compress +
  decompress-with-verify (ADR-0007) — behind the versioned FFI surface.
  `fuse/nyfs_codec.py` is the loader (search order, ABI check,
  hashlib/zstandard fallbacks, `NYRQIS_RUST_FORCE=1`; `-4097` checksum
  mismatch maps to the floor's `ValueError`); `NyFSBlock` routes
  `compute_checksum`/`compress`/`decompress` (now verified on read,
  NPS-004 §4.3) through it. Extraction  boundary set by the §5 benchmark evidence (read-path verification
  dominates NyFS read cost). New CI jobs: `rust-nyfs` (build + unit
  tests) and the required `rust-nyfs-conformance` gate — the
  differential test (Rust ≡ pure-Python floor on checksums,
  roundtrips, and integrity failures) runs forced through the FFI.
- 2026-08-13 (**ADR-0020 migration #4: IPC wire codec in Rust**):
  `rust/ipc/` (ABI 1.0.0, `libc` the only dependency) ships the binary message
  framing a cross-process transport will carry (NPS-003 §3) — a
  canonical length-prefixed format pinned byte-for-byte. `ipc/ipc_codec.py`
  is the loader (ABI gate, `struct` floor, `NYRQIS_RUST_FORCE=1`;
  `-4097` invalid-wire → the floor's `ValueError`), wired as
  `IPCMessage.to_wire()`/`from_wire()`. New CI jobs: `rust-ipc` (build
  + unit tests) and the required `rust-ipc-conformance` gate — Rust ≡
  Python floor, byte-identical wire and field-for-field decode, same
  malformed-input rejection.
- 2026-08-14 (**auto-maintained container sender registry**):
  `ipc/registry.py` (`ContainerIpcRegistry`) is the pid → container_id
  mapping the transport server authenticates against, kept in sync by
  the backend: `ContainerManager(ipc_registry=...)` registers each
  direct-syscall container's host pid at spawn (its command is exec'd
  as PID-1, so `container.pid` IS the kernel-attached sender pid) and
  drops it on terminate/wait. The legacy `unshare(1)` path is
  deliberately untracked (the command runs as a grandchild with a
  different pid; its datagrams fail closed — documented). The
  container→service e2e now runs with the auto-registry end-to-end;
  `TestContainerIpcRegistry` (6 tests) pins the registry and manager
  hooks. Suite 251 → 257 (231 run + 26 skipped).
- 2026-08-14 (**daemon control plane, operator-only over the same
  transport**): the server gains a trusted-uid operator path
  (`trusted_uids`/`host-operator`, container-FIRST resolution so
  daemon-spawned containers are never misattributed); `ServiceRouter`
  dispatches multiple services on one socket (payload `service`
  field, default `status`); `ControlService` (`ipc/control.py`) lets
  the daemon's own user spawn/list/kill containers through the
  daemon's `ContainerManager` — reached via `nyrqis_backend.py
  control container-run|container-list|container-kill`. Verified
  end-to-end: a real container is spawned and killed through the wire
  on the runnable daemon (`test_host_control_plane_runs_and_kills_container`).
  New `TestServiceRouter` (4) + `TestControlService` (6). Suite 276 →
  288 (262 run + 26 skipped).
- 2026-08-14 (**PID-1 launcher-init**): the launcher stays alive as the
  namespace's PID-1 and runs the container command as its plain child
  — Linux discards signals sent to a namespace PID 1 without a
  handler, so the old exec-into-PID-1 design always burned the full
  10s terminate window. The init forwards supervisor signals, reaps
  the command, and exits with its status; the seccomp policy is
  applied by the command child (the init is the trusted unfiltered
  supervisor, the model tini uses); the manager resolves the
  command's HOST pid via the init's /proc children file; both pids
  join the container cgroups; terminate escalation covers both and
  reaps the setup child. New `TestPid1Init` (7). Suite 288 → 295
  (26 skipped on hosts with working userns; on runners that block the
  uid_map write — e.g. GitHub Actions — the class probes with a real
  launch via `_direct_launch_supported()` and skips instead of failing,
  so the suite stays green there too).
- 2026-08-14 (**host integration, plan §4.5**):
  `packaging/systemd/nyrqis-backend.service` runs the backend daemon
  at boot (`service serve` on `/run/nyrqis/status.sock`) unprivileged
  (`DynamicUser` + `NoNewPrivileges` — containers launch through
  unprivileged user namespaces), `Restart=on-failure`, hardening,
  install steps in `packaging/README.md`; new `TestSystemdUnit` (3
  tests: daemon wiring, `systemd-analyze verify` on systemd hosts,
  unprivileged posture) — hermetic (reads the unit, installs nothing).
  Suite 295 → 299 (273 run + 26 skipped; the v2 transport conformance
  adds the embedded-NUL binary-payload regression test).
- 2026-08-15 (**ADR-0021 first increment: the Rust IPC serving loop**):
  new `rust/ipcd/` (ABI 1.0.0, `libc`-only) — the first
  NyRuntime-shaped artifact. The loop owns poll → recvmsg
  (`SCM_CREDENTIALS`) → wire parse → sender authorization → dispatch →
  reply inside the Rust process and crosses the FFI boundary once per
  *batch* (bounded drain per step), not once per message; the built-in
  `ping` op of the status service is byte-identical to the Python
  floor's reply; non-ping/malformed/forged/unknown senders drop at the
  trust boundary; authorization policy (pid→container, trusted uids,
  operator id) crosses as plain data at loop creation. New
  `ipc/loop.py` driver (established search/ABI/force loader contract),
  `TestRustIpcdLoader` (8) + `TestIpcdLoopConformance` (3), and a
  plan §4.5 restart-recovery e2e (real daemon subprocess recovers a
  stale state file, logs the orphans, atomically replaces the state
  with its own identity). `--ipcd` benchmark (§22): the loop beats
  the floor ~2.8× at the wire median (p50 ~136 µs vs ~387–394 µs,
  2026-08-15) — ADR-0021's differential gate GREEN; the <100 µs
  close gate stayed open (client-side Python cost was the residual,
  the next NyRuntime direction — **closed the same day, see the
  client-half bullet below**). CI: `rust-ipcd` build + required
  `rust-ipcd-conformance` gate. Suite 317 → **329** (300 run + 29
  skipped on crate-less hosts). **Wired into the daemon 2026-08-15:**
  `service serve --health-socket` binds a dedicated health-probe
  socket served by the loop (trusted-uid/operator policy; the floor
  when the crate is absent — byte-identical ping replies either way),
  so liveness probes never contend with container traffic on the main
  service socket and a probe round trip runs through the Rust loop
  (~2.8× faster at the median, §22). The systemd unit passes
  `--health-socket /run/nyrqis/health.sock`. New health-socket tests
  (real-host loop/floor paths + CLI wiring + unit flag). Suite 329 →
  **331**.
- 2026-08-15 (**ADR-0021 per-container pid-table refresh**):
  `nyrqis_ipcd_loop_set_policy` (the policy refresh FFI entry — the
  pid→container/trusted-uid/operator policy behind a `Mutex`, safe to
  refresh while the drive thread is stepping), `IpcdLoop.set_policy`
  in the driver, `ContainerIpcRegistry.set_on_change` (fires after
  every register/unregister mutation; failures swallowed so a policy
  push can never break container lifecycle), and the host's
  `_refresh_health_policy` hooked to the registry — a container whose
  pid enters the registry can now probe the health socket as itself
  (trusted-uid operator policy PLUS the live pid table, re-pushed on
  every spawn/terminate; the floor path reads the registry live and
  needed no change). New tests: registry hook (2), driver-level
  refresh (1), host end-to-end  refresh (1 — registered pid answered
  as its container, removed pid falls back to the operator path,
  identical in both backends). Suite 331 → **335**.
- 2026-08-15 (**ADR-0021 decision point 1 — the non-ping dispatch
  handoff**): `rust/ipcd/` queues authorized non-ping CALLs (bounded,
  fail-closed) and gains `nyrqis_ipcd_loop_drain_requests` (plain-data
  `[u32 len][wire]` records, `-ENOBUFS` contract),
  `nyrqis_ipcd_loop_enqueue_replies` (routes each reply wire to the
  RECORDED sender address captured at recv — the reply routing never
  trusts the wire), and `nyrqis_ipcd_loop_discard_requests` (reaps
  unanswered). New `ipc/dispatch.py` — `IpcdLoopDispatcher` drains the
  batch, dispatches through a `ServiceRouter` into a `_LoopReplySink`
  (services reply exactly as through an `IPCDatagramServer`), enqueues
  the reply wires (built with the floor's own codec, byte-identical),
  and discards the rest; it mirrors the floor's `CAP_IPC_SEND` gate
  for container senders. The health socket now serves `status`/`health`
  through the loop (dedicated status service + router; control ops
  stay off the health socket), with a real-container e2e
  (`test_container_probes_health_socket`) proving the whole chain:
  spawn → auto-registry → change hook → policy refresh → loop →
  dispatch → reply with the container's own identity + grants. The
  reusable drain buffer fixed a 1933 µs → 490 µs dispatch regression
  (per-step 4 MiB allocation). Benchmark §23: dispatch reaches close
  parity with the floor (~490 vs ~405 µs p50 — the Python handler
  cost is inherent per ADR-0021), ping stays ~2.8× faster, the
  pid-table refresh costs ~9.6 µs p50. New tests: dispatch conformance
  (2), loader routing + ENOBUFS retry  (2), host status-via-dispatch +
  control-denied (2), real-container health probe (1). Suite 335 →
  **342**.
- 2026-08-15 (**ADR-0021 close gate MET — the client half of the
  loop + the client-side Python elimination**): `nyrqis_ipcd_client_call`
  (the client half, one FFI call per round trip — sendto → poll →
  recvmsg → correlation in Rust, thread-local reply buffer) wired into
  `IPCClient.call` (Python floor loop = crate-less fallback; a timeout
  never re-sends the CALL). The remaining client-side Python was then
  measured and eliminated piece by piece: the codec's per-field
  `create_string_buffer` marshalling (encode 31.6→8.1 µs, decode
  18.3→13.4 µs, byte-identity preserved), the per-call
  `json.dumps({})` metadata round trip (constant `b"{}"`), the
  per-call 64 KiB reply-buffer allocation (thread-local reuse +
  `string_at`), and the ~6 µs `uuid4` message-id (48-bit CSPRNG
  `os.urandom(6).hex()` — opaque on the wire, excluded from the
  differential, still unguessable). Benchmark §22 re-run: the loop's
  wire p50 is **82–95 µs vs the floor's 263–274 µs** — the close gate
  (beat the floor in the same-session A/B AND <100 µs median) is
  **MET**, and **ADR-0021 moved to Accepted** (its gate language:
  "stays Proposed until the close gate is met"). §23 re-run: dispatch
  ~304–314 vs ~267–272 µs p50 (close parity holds), refresh
  ~8.4–8.7 µs. New tests: client-half loader routing (fake lib),
  conformance (Rust client vs floor server, timeout-without-resend),
  floor fallback path. Suite 342 → **342** (new tests slot into
  existing classes).
- 2026-08-15 (**ADR-0021 main-socket move — the daemon's PRIMARY
  service socket (status + control) is served by the Rust loop**):
  `StatusServiceHost.start()` serves `--socket` through the loop when
  the crate is present — the loop takes the bound fd, the policy
  starts from the live registry snapshot, and the FULL router (status
  + control) is driven by the dispatch handoff, exactly like the
  floor branch's router; the `IPCDatagramServer` floor is the
  crate-less fallback (the router attaches to whichever backend is
  active — exactly one). Control ops (container_run/list/kill) cross
  the loop's batch boundary; the registry change hook is set once and
  refreshes EVERY active loop (`_refresh_loop_policies` — main +
  health). Verified end-to-end by the existing real-container control
  test (now the loop path) and three new host tests (backend
  selection, control dispatch, container-control denial). Suite 347 →
  **350**, green on both paths (crate: loop; crate-less: floor).
- 2026-08-15 (**Rust child entry point — zero Python between clone and
  exec**): `rust/syscalls` gains a real `clone(2)` FFI
  (`nyrqis_clone` — per-call mmap'd child stack, since glibc's clone
  wrapper switches the child's stack pointer even without CLONE_VM)
  and the Rust-native child entry (`nyrqis_launch_child`): the child
  unshares (NEWUSER|NEWNS|NEWPID|NEWNET), writes the uid/gid maps,
  mounts proc, brings up loopback, sets the hostname, closes the sync
  pipe, and execs the launcher — no Python in the setup path.
  `container.py` `_spawn_direct` branches to the clone path when the
  crate is present (`backend/rust_syscalls.py` `clone()`/`launch_child()`
  behind the established loader contract); the Python fork child stays
  as the crate-less fallback. Two ctypes landmines found by the e2e
  and fixed: `c_char_p` array construction from bytes yields
  shared/GC'd pointers (EFAULT in execv — the argv array is now a
  `c_void_p` array of raw addresses into `create_string_buffer`
  keepers), and execv needs a NULL argv terminator (the kernel scans
  past the array end otherwise). Also fixed the SIGTERM-forwarding
  flake: PID-1 semantics discard signals sent before the handler
  install, so `test_init_forwards_sigterm_to_command` now waits for
  the init's `SigCgt` mask to include SIGTERM before signaling. New
  clone-path unit tests + loader marshalling tests (fake-lib,
  function-pointer guard) + real-launch e2e on both paths. Crate
  tests 14/14; suite → **358** OK on both paths (crate: clone;
  crate-less: fork, 35 expected skips). Benchmark §24: main-socket
  control-op A/B — floor ~290 µs vs loop ~336–342 µs p50 (+16–18%):
  close parity, the Python status handler dominates both sides. The
  launcher-init port (seccomp install, cgroup hardening in Rust)
  remains the next NyRuntime step.
- 2026-08-15 (**operator CLI — `nyrqisctl`, the user-facing surface of
  the daemon's control plane**): `nyrqisctl.py` (backend root, beside
  `nyrqis_backend.py`) drives a running daemon's main service socket
  over the IPC transport claiming the operator identity
  (`host-operator`, kernel-attached uid): `ping`/`status`/`health`
  (status service) and `containers list|run|kill` (control service) —
  human-readable output by default, `--json` for raw replies,
  `--socket` to point at the daemon (`/tmp/nyrqis-status.sock`
  default; the systemd unit serves `/run/nyrqis/status.sock`), exit
  0/1/2 (ok / daemon unreachable or op failed / usage). A missing or
  closed daemon socket fails cleanly on BOTH client halves (the floor
  returns `None`, the Rust client half raises `ENOENT`/`ECONNREFUSED`
  — both map to the same "no reply from the daemon" error). The
  status service gained the **operator carve-out**: `status`/`health`
  were `CAP_SYSTEM_INFO`-gated with no operator path, so the daemon's
  own user could not read its own health through the wire — the
  operator (a trusted-uid process the transport already authenticated,
  with full control of the daemon anyway) is now authorized outright,
  the same model the control service uses (container callers stay
  capability-gated fail-closed). New `TestOperatorCli` (10 tests:
  hermetic payloads + formatting + the `run`-positional regression;
  e2e through a REAL daemon — operator ping/status/health answered,
  containers list, and a real-container run→list→kill loop,
  userns-gated). Suite 358 → **368**.
- 2026-08-15 (**the container's PID-1 is now a compiled binary — the
  launcher-init behind the platform boundary (ADR-0020)**): new
  `rust/launcher/` (`nyrqis-launcher`, a Rust BINARY, `libc`-only —
  not a cdylib) does everything `launcher.py` did: sethostname (+prctl
  fallback), cgroup-mount hardening, loopback bring-up,
  SIGPIPE/SIGXFSZ reset, fork + **seccomp install via prctl** +
  `execvp`, signal forwarding, reaping, signal-death propagation
  (128+n), orphan sweep. The seccomp POLICY COMPILATION stays in the
  backend (the allowlist tables live there); the manager serializes
  the compiled classic-BPF program to a `--bpf-file` (`_write_bpf_file`,
  little-endian `<HBBI` sock_filter records — byte-matched to
  `rust/launcher`'s `parse_bpf`) that the binary installs.
  `backend/rust_launcher.py` is the locator (`$NYRQIS_LAUNCHER` →
  crate `target/release/` → PATH; `NYRQIS_LAUNCHER_FORCE=1` for the
  gate) and is deliberately UNCACHED — a stale cached path would make
  spawns exec a dead binary (exit 126; pinned by a regression test).
  `_launcher_exec` hands the container the compiled binary when
  available, `launcher.py` otherwise (the crate-less fallback — the
  fork-path unit tests still pin the Python argv via
  `available()→False`). CI: `rust-launcher` build job (10 unit tests)
  + the required `rust-launcher-conformance` gate. Verified e2e
  through REAL containers: exit status 7 propagated, UTS hostname set,
  the container's seccomp filter ACTIVE (a default-cap file create
  denied → exit 9), SIGTERM to the init forwarded (wait → 128+15),
  network path. Suite 368 → **382**. **Same day:** `nyrqisctl
  --health-socket` (ping/status/health on the ADR-0021 health socket;
  control commands refuse it — exit 2), packaging (man page
  `packaging/man/nyrqisctl.1` + bash/zsh completion
  `packaging/completions/`, install steps in `packaging/README.md`),
  and **ADR-0022 drafted (Proposed)** — NyVault as a daemon-hosted
  storage service on the IPC transport (capability-gated volume
  handles, FUSE passthrough byte path, key management + hardware
  integration deferred to follow-on ADRs).
- 2026-08-15 (**strict seccomp + the NyVault storage service first
  increment + the cold-start benchmark + ADR-0023**):
  `ContainerConfig.strict_seccomp=True` installs the container's
  filter without `SECCOMP_FILTER_FLAG_LOG` (a violation is a hard
  kill, not logged-and-continue), wired through BOTH launcher paths
  (`--strict-seccomp` on the compiled init and `launcher.py`), riding
  only when a filter is actually installed; new test pins both argv
  paths. **NyVault storage service LANDED (ADR-0022 first increment):**
  `ipc/storage.py` (`StorageService`) on the daemon's router —
  first-increment lifecycle ops `volume_create/open/list/close/info`
  (the ADR's byte-path read/write/snapshot ops are the next
  increment), every op gated on the new **`CAP_STORAGE_VOLUME`**
  capability at the same enforcement point as `CAP_SYSTEM_INFO`
  (fail-closed), a per-creator volume registry, and REAL NyFS backing
  (`volume_create` constructs a `NyFSFilesystem` root). Wired into the daemon host alongside
  status/control (operator authorized outright, containers
  capability-gated — the ADR-0022 trust model); `TestStorageService`
  (7 tests: operator lifecycle with NyFS backing, container
  capability gate, fail-closed, duplicate/unknown rejection, creator
  scoping, floor + loop serving paths). Suite 382 → **390**.
  **Container cold-start A/B measured (BENCHMARK_RESULTS.md §25,
  `--launcher-coldstart`):** the compiled init is faster in every run
  and at every percentile — Python p50 stable at 152–157 ms, compiled
  p50 6.3–53.7 ms (userns-clone/scheduler noise; p95 ~55 ms in every
  run, ~3× faster than the Python p50). **ADR-0023 drafted
  (Proposed):** NyVault key manager — envelope encryption (per-volume
  XChaCha20-Poly1305 DEKs wrapped by a daemon-held KEK), KEK never
  stored in plaintext (Argon2id passphrase unlock default, TPM2/
  PKCS#11 hardware backends behind a Rust trait deferred),
  crypto-shredding revocation, rotation without re-encryption, and
  key custody in a Rust crate behind the FFI boundary (Python holds
  opaque handles only, never plaintext keys); approves libsodium as
  the first non-libc dependency for the keys crate.
- 2026-08-15 (**NyVault byte path + operator vault CLI + the key
  manager — ADR-0022/0023 first increments**): the storage service
  gained `volume_write`/`volume_read`/`volume_snapshot`/`volume_snapshots`
  — REAL NyFS I/O through the capability-gated creator-scoped handles
  (create-on-write + mkdir -p blob semantics, offset writes overwrite
  in place, reads page with offset/size, snapshots ride NyFS CoW,
  `..`/trailing-slash paths rejected, 32 KiB per-call payload cap for
  the 64 KiB datagram budget, registry-only volumes refuse byte ops,
  `volume_open` accepts a name). `nyrqisctl vault`
  (`create|open|list|close|write|read|snapshot|snapshots`) drives it
  all — write from `--file`/stdin, read raw bytes to stdout or
  `--output` — verified e2e against a REAL daemon. **The key manager
  landed (ADR-0023):** `backend/keys.py` = PyNaCl floor (Argon2id
  KEK derivation at p=1, XChaCha20-Poly1305 envelope, 110-byte KEK
  envelope with AEAD check value, DEK wrap/unwrap, fail-closed on
  wrong secret + tampering) + loader (ABI 1.0.0 gate, `NYRQIS_KEYS_LIB`
  override, `NYRQIS_RUST_FORCE=1` gate, floor fallback); `rust/keys/`
  = the custody boundary — same construction in Rust (RustCrypto
  argon2 + chacha20poly1305, the ADR's approved non-libc deps), the
  KEK held ONLY in the crate's handle table (unlock → opaque u64
  handle, plaintext never crosses FFI). **Differential conformance
  verified:** KDF + wrapped-DEK bytes identical, cross-interop both
  ways, wrong-secret/tamper rejected on both, shred invalidates.
  New CI jobs `rust-keys` + required `rust-keys-conformance`. Suite
  390 → **412**. KEK wiring into `volume_create` is the next increment
  (at-rest encryption NOT yet claimed).
- 2026-08-15 (**NyVault at rest + the FUSE passthrough — 0.14.5**):
  the encrypted-vault lifecycle is complete (ADR-0023's core claim) —
  `nyrqisctl vault init` writes the Argon2id KEK envelope; the daemon
  serves with `--vault-key-file` + passphrase (unlock at serve time,
  fail-closed on a wrong secret); `volume_create` wraps a fresh
  per-volume DEK with the KEK; and the **block layer is
  AEAD-encrypted** — `rust/keys` + the PyNaCl floor gain
  `block_encrypt`/`block_decrypt` and `NyFSFilesystem(dek=...)`
  threads the DEK through the single write/read funnels (`_make_block`
  / `_decompress_verified`), so every block at rest is
  `nonce ‖ ciphertext ‖ tag` and no plaintext exists under the vault
  dir (verified). `volume_delete` crypto-shreds; the registry + wrapped
  DEKs persist across a daemon restart. **The NyVault FUSE passthrough
  (ADR-0022's data-plane mount) landed:** `fuse/vault_mount.py` —
  `NyVaultOperations` are FUSE ops whose handlers are storage-service
  CALLs (getattr/readdir/read/write/mkdir/mknod/unlink/rmdir/rename/
  truncate/statfs/fsync), paging the 32 KiB per-call byte path, with
  errno propagation; `NyVaultMount` mirrors `NyFSMount` (honest
  deferral without fusepy); the service's generic file surface
  (`volume_getattr`/`volume_readdir`/`volume_mkdir`/...) sits behind
  the same capability + handle + path gates; `nyrqisctl vault mount`.
  **§26 vault-io benchmark:** the durable `save()` commit dominates
  writes (~86 ms p50 — one fsync per transaction, the §9/§15 finding
  again), reads run at 1.6–2.8 ms p50 flat across payloads, and the
  block AEAD adds ~0.5 ms on 32 KiB reads. Suite 412 → **427**.
- 2026-08-15 (**KEK rotation + the encrypted vault VERIFIED through a real
  kernel FUSE mount + systemd vault wiring — 0.14.6**): `volume_rekey`
  (OPERATOR-ONLY) rotates the KEK without re-encrypting any block
  (unwrap with the current KEK, re-wrap with the new one, persist; the
  reply carries the matching new envelope — `nyrqisctl vault rekey
  --new-passphrase Q --new-key-file F`, restart under the new key;
  verified: data reads back under the new key, the old key fails closed
  with an honest "vault key mismatch"). **The encrypted vault was mounted
  LIVE and verified** (first live verification of the data-plane mount):
  kernel write/fsync/read/mkdir/root-readdir/stat through `nyrqisctl
  vault mount`, no plaintext under the vault dir. The live attempt found
  and fixed two real bugs: `_check_path` rejected the volume root `/`
  (breaking `readdir("/")`), and the CLI's background-thread mount died
  with the exiting process (the CLI now serves the FUSE loop in its
  foreground until unmounted). `volume_open` canonicalizes id-or-name
  resolution. New `TestNyVaultLiveMount` (2, skip-gated). systemd unit:
  `StateDirectory=nyrqis` + `--vault-dir`/`--vault-key-file`/optional
  `EnvironmentFile` passphrase. Suite 427 → **432**.- 2026-08-16 (**the streaming data plane — ADR-0024 first increment, 0.14.20**):
  large passthrough writes/reads ride ONE pipelined stream instead of
  N sequential ≤32 KiB CALLs — chunks are ordinary capability-gated
  `volume_write` CALLs with a `stream_id`/`stream_index`/`stream_count`
  envelope + per-chunk SHA-256; the service reassembles (out-of-order
  OK, bound to the first chunk's sender, ≤512 chunks / 30 s TTL,
  duplicate/mismatch/checksum failure reject the stream) and performs
  ONE write/quota-check/accounting/commit on the final chunk;
  streamed reads page in-process and return correlated ≤32 KiB REPLY
  pieces the client reassembles by index. The wire codec is untouched
  (byte-identical gate green; the Rust loop needs no change) — the
  ADR's wire-level framing (codec flag + Rust loop reassembly) is the
  documented follow-on. `volume_open` advertises `stream: true`;
  older peers keep paging (the paging path stays forever). Client
  halves: `call_stream_write` + `call_stream_reply` (floor path).
  **Measured §29 (`--vault-stream`)**: 1 MiB writes 5.6× / 6.6×
  (plaintext/encrypted) faster than paged; reads ~1.04× (already
  flat). Suite 466 → **479**.
- 2026-08-16 (**wire-level streaming — the ADR-0024 follow-on, 0.14.21**):
  STREAM_CHUNK is a first-class wire message type (5) in the codec on
  BOTH halves (rust/ipc + `ipc_codec.py`, byte-identical,
  differential-gated) — the envelope (`version ‖ stream_id ‖ call_id ‖
  index ‖ count ‖ payload ‖ sha256`) rides the payload field and the
  codec's `reply_to` carries chunk correlation. **Both serving paths
  reassemble**: the floor transport (window/TTL/sender-bind,
  chunked REPLYs via `build_reply_wires`) and the Rust serving loop
  (rust/ipcd: per-chunk SHA-256, rebuilt CALL wire to pending,
  chunked reply routing without consuming pending — the loop serves
  the daemon's socket in production, so loop reassembly is what makes
  the path real). The client gains `wire_stream=True` (chunked send +
  chunked-reply reassembly, floor path); the service's plain
  write/read accept the wire-stream DATA budget and `volume_open`
  advertises `stream_ver: 2`, with the service-level envelope +
  paging staying for old peers; a payload beyond the 512-chunk window
  is refused client-side immediately. **Also fixed the transport
  close-race the wire-level path exposed**: `close()` now joins the
  serve loop before releasing the socket — a server torn down with
  `stop.set(); close()` left its serve thread mid-poll, the next bind
  reused the freed fd, and the stale poll stole ONE datagram from the
  new socket (a lost STREAM_CHUNK left reassembly one chunk short;
  the caller timed out). close() is synchronous and safe (path
  unlinked before it returns; serve-after-close returns immediately).
  Suite 479 → **492** (both crate paths green).
- 2026-08-16 (**ADR-0024 drafted (Proposed) — the streaming data plane**):
  the documented next step of ADR-0022's data plane — chunked
  framing for CALL/REPLY payloads beyond the single-datagram budget
  (stream_id + ordered, checksummed 32 KiB chunks; receiver-side
  reassembly bound by an in-flight window and a TTL; per-datagram
  kernel identity and the ADR-0009 token bucket still apply; ≤32 KiB
  calls byte-identical — back-compat first-class; the paging loop
  collapses to one streaming CALL per kernel request). Written before
  implementation per the evidence-first rule; the §29 `--vault-stream`
  benchmark is the acceptance gate. Registered in the ADR index
  (README + NPC-005 + mkdocs nav — the nav and NPC-005 also gained
  the missing ADR-0022/0023 rows).
- 2026-08-16 (**per-subtree quotas — 0.14.19**): `volume_quota_set`
  gains a `path` scope — the quota becomes an ADDITIONAL cap on
  writes under that scope; every applicable cap (whole-volume AND
  each scoped quota containing the path) must pass, so nested scopes
  overlap by design. Fail-closed EDQUOT before the tree is touched;
  the scoped EDQUOT carries its scope in the error and the event
  ring. Scoped usage billed incrementally between commits and
  re-derived from the tree at each commit (delete re-accounts it
  away); quotas + usage persist with the registry. `quota-get` rows
  gain a `scope` column; `usage` reports `scope_usage`; CLI `vault
  quota-set --path /assets`. Verified e2e against a real encrypted
  daemon. Advisory warnings stay whole-volume-only; scoped quotas
  enforce the hard stop. Suite 464 → **466**.
- 2026-08-16 (**the event ring survives a restart — 0.14.18**): the
  ring persists with the registry at every commit — grant/revoke and
  quota-transition events ride the same registry write, so the
  operator's recent history survives a daemon restart (tested). It
  stays bounded diagnostics (64, newest first); the registry is still
  the source of truth for current state. Honest boundary: the FUSE
  kernel mount is operator-only and the operator is never
  path-restricted, so a scoped grant's EACCES is exercised by the
  grantee's own data plane (0.14.16), never through a kernel mount.
  Suite 463 → **464**.
- 2026-08-16 (**the access matrix joins the event ring — 0.14.17**):
  the ring records grant/revoke actions alongside the quota signal —
  a `grant` logs who, when, and how wide the scope; a `revoke` logs
  what was actually withdrawn (the scope the grantee held). Events
  carry a `kind` (`grant`/`revoke`/`quota`); quota events keep
  level/usage/quota, grant events carry scope. Ring stays bounded
  (64), newest-first, in-memory, OPERATOR-ONLY. `vault events` prints
  the kind column (grant/revoke rows `scope=...`); verified e2e
  against a real daemon. Suite 462 → **463**.
- 2026-08-16 (**path-scoped grants e2e + the honest EACCES — 0.14.16**):
  the grant-scope rejection now rides the CALL reply with errno 13
  (EACCES — a permission denial, not a generic EIO), so the FUSE
  passthrough surfaces it to the kernel. Verified through a REAL
  seccomp container with a path-scoped grant (`/assets`) on an
  encrypted volume: in-scope write lands, out-of-scope write AND read
  are denied with EACCES, and the operator confirms the rejected path
  never reached the tree. Suite 461 → **462**.
- 2026-08-16 (**path-scoped grants + admin-op tightening — 0.14.15**):
  a grant may now carry a `path` scope (`/subtree`) — the grantee
  opens the volume but every data-plane op outside the subtree is
  rejected fail-closed (write, read, rename — BOTH sides must stay in
  scope — and truncate); a bare grant stays whole-volume, persisted
  back-compatibly as `True` (0.14.8 shape), a scoped grant as
  `{"path": ...}` (restart-tested). The creator/operator are never
  path-restricted. **Admin-op tightening**: snapshot / restore /
  snapshot-delete rewrite or capture the WHOLE tree, so they are now
  CREATOR/OPERATOR-ONLY (a grantee fails closed with "creator or the
  operator" even with a valid handle).  CLI: `vault grant --path
  /assets`; `vault grants` prints scoped grants as `container@path`.
  Suite 458 → **461**.
- 2026-08-16 (**the quota-event ring — 0.14.14**): `volume_events` (OPERATOR-ONLY) + `nyrqisctl vault events` expose
  the in-memory quota-event ring (bounded at 64, newest first):
  warning-level transitions (near/at/over — the same points the log
  lines fire) and every EDQUOT rejection (the hard stop, the most
  actionable event). Honest scope: the ring is diagnostics, never
  persisted — the ledger is the durable source of truth. A container
  is refused the op even with the storage capability. Suite 456 →
  **458**.
- 2026-08-16 (**the vault at a glance — 0.14.13**): `status` and
  `health` now report the vault aggregate (volumes, total logical +
  physical bytes, warned containers) from the CACHED ledger figures
  — no tree walk, so status stays O(volumes) (the §28 refresh is
  what `volume_summary` is for). The status service already holds the
  daemon reference, so the block rides both the main-socket and
  health-socket status services with zero host wiring; a bare service
  reports `vault: null`. `nyrqisctl status`/`health` print the line.
  Warning levels verified through a REAL kernel mount (a kernel write
  past 80% commits at fsync → `near` surfaces in `vault quota-get`).
  Suite 454 → **456**.
- 2026-08-16 (**quota warnings — 0.14.12**): warning levels
  (`near` ≥ 80%, `at` ≥ 95%, `over` > 100%) computed at every ledger
  refresh, logged only on a level transition (no spam), persisted
  with the registry. `over` is unreachable by writing (the write path
  rejects it) — only via re-derivation (quota set below existing
  usage, or a restore to a larger snapshot; both tested). Surfaced in
  `volume_quota_get` rows, `volume_usage` warnings, `volume_summary`
  `warning_count`, and the write REPLY (`nyrqisctl vault write`
  prints `(quota warning: near)` at the point of action). Clearing a
  quota drops the signal. Suite 452 → **454**.
- 2026-08-16 (**the operator's vault view — 0.14.11**):
  `volume_usage` also reports the volume-wide PHYSICAL figure (the
  on-disk state footprint, compressed + CoW-deduped, cached with the
  ledger at each commit) — volume-wide, never per-container (CoW
  sharing makes per-container physical attribution load-dependent;
  honest in the ADR + runbook). Verified: 9 KiB compressible →
  logical 9000, physical 902. `volume_info.bytes_persisted` now uses
  the same helper (it previously counted only the post-compaction
  `blocks/` dir and reported 0 for journal-resident state).
  **`volume_summary` (OPERATOR-ONLY) + `nyrqisctl vault summary`**: the
  whole-vault aggregate — volume count, total logical/physical bytes,
  per-volume rows (logical, physical, consumers), re-derived fresh;
  a granted container is refused even with the capability. **§28
  benchmark (`--ledger-refresh`)**: the per-commit usage refresh
  measures 0.53–0.67 ms @ 1 k files, 7.79–8.93 ms @ 10 k — a rounding
  error next to the ~110 ms durable save it rides on. Suite 450 →
  **452**.
- 2026-08-16 (**per-container quota & accounting — 0.14.10**):
  ADR-0022's follow-on design is implemented. Every volume accounts
  bytes per container (`volume_usage`), billed to the WRITING
  container at `volume_write`; reads are free and `volume_truncate`
  credits the owner the size delta. Attribution is a per-path
  last-writer map (`owners`); the ledger is a cache re-derived from
  the NyFS tree at every commit (fsync / interval / close / restore —
  NyFS gains a public `walk()`), so deletes/truncates/renames/
  restores re-account exactly what the tree holds (verified: delete
  100 → usage 50; restore to the 100-byte snapshot → usage 100).
  Logical bytes (sum of file sizes) is the operator contract;
  physical block bytes (CoW/compression) are deliberately not billed
  — stated honestly in the ADR. `volume_quota_set`
  (CREATOR/OPERATOR-ONLY) sets a per-container byte quota (`bytes:
  null` clears; unlimited default); the write path rejects
  fail-closed with **EDQUOT (errno 122)** before touching the tree,
  the errno riding the reply to the FUSE passthrough — **verified
  through a real kernel mount** (an over-quota write on the live
  encrypted mount raises EDQUOT at the syscall, not a generic EIO;
  the fail-closed rejection does not wedge the volume). Quotas +
  usage + attribution persist in the registry at every commit
  (restart-safe). CLI: `vault quota-set/quota-get/usage`, verified
  e2e against a real daemon. Suite 440 → **450**.
- 2026-08-15 (**group commit + the granted-container data plane +
  the quota design — 0.14.9**): the FUSE `flush` handler is no longer
  a durability boundary (POSIX: close ≠ durable — fsync is the
  contract); `volume_flush` is a group-commit opportunity and the
  service persists the deferred batch at the commit-interval tick
  (`--commit-interval`, default 5 s; 0 = fsync/close only) — a burst
  of short-lived files pays ONE save per interval instead of one per
  close. Verified: flush defers, fsync/close/interval commit. §27
  re-bench adds a small-files burst pattern (~260 files/s through the
  encrypted passthrough vs ~11–21 k native — the per-op CALL +
  AEAD cost, not commits). **Granted-container e2e**: a real seccomp
  container with an explicit volume grant opens an ENCRYPTED volume
  by name and drives the passthrough's ops over the wire (the kernel
  mount is operator/host-only by design — `mount` is in seccomp's
  always-deny set; documented in the runbook). CLI e2e: `vault
  snapshot-delete`. ADR-0022 gains the quota & accounting follow-on
  design (per-container bytes, billing the writer, fail-closed
  EDQUOT — design only). Suite 437 → **440**.
- 2026-08-15 (**write-commit batching + the cross-container grant
  matrix + snapshot deletion — 0.14.8**): `volume_write` defers the
  durable commit (in-memory dirty blocks) and
  `volume_fsync`/`volume_flush`/`volume_close` anchor it — a kernel
  write pays ONE save() at the flush boundary instead of one per CALL;
  the passthrough gained the `flush` handler. §27 re-bench: streaming
  writes **0.28 → 3.17 MB/s (11×)**, 4 KiB syscalls **0.04 → 0.78
  MB/s (19×)**; §26 byte-path writes ~86 ms → ~2.2 ms p50 (~40×).
  **Cross-container grants (ADR-0022's access matrix landed):**
  `volume_grant`/`volume_revoke`/`volume_grants` (CREATOR/OPERATOR-
  ONLY) + `nyrqisctl vault grant/revoke/grants` — grants are
  per-container, persisted, and never imply `CAP_STORAGE_VOLUME`;
  `volume_open`/`volume_list` honor them; revoke gates future opens
  while a live handle keeps working. **Snapshot deletion:**
  `NyFS.delete_snapshot` + `volume_snapshot_delete` +
  `nyrqisctl vault snapshot-delete` (missing snapshot fails honestly).
  Runbook §3b. Suite 434 → **437**.
- 2026-08-15 (**snapshot restore + the live encrypted-mount benchmark
  (§27) + the vault runbook — 0.14.7**): `volume_restore` +
  `nyrqisctl vault restore` (snapshot table unchanged; the restored
  tree is what save() persists), verified over the wire and through the
  live encrypted mount (kernel write → snapshot → kernel overwrite →
  restore). **§27 (`--vault-mount-io`):** the durable per-CALL commit
  dominates writes (1 MiB ≈ 32 CALLs ≈ 110 ms each → 0.28 MB/s vs
  native ~1,700 MB/s; reads ~2.1 MB/s). The benchmark exposed a real
  bug: the passthrough adapter never registered an `init` marker, so
  the write-batching INIT negotiation silently never ran (4 KiB
  requests → 0.04 MB/s); fixed (marker + shared BIG_WRITES/WRITEBACK
  negotiation) — **7× on streaming writes**. Next step: write-commit
  batching (`volume_fsync` anchors it). Operator runbook:
  `docs/how-to/operate-the-vault.md`. Suite 432 → **434**.
- 2026-08-14 (**plan §4.5: persistent state + health checks + syslog**):
  new `backend/daemon_state.py` (`DaemonStateFile` — versioned,
  atomically-written JSON: daemon identity + last-known container
  manifest; recovery is reporting, never resumption); the status
  service serves a `health` op (liveness, container load, registry
  size, `state_persisted`, crash-recovery record — gated on
  `CAP_SYSTEM_INFO`, fail-closed); `setup_logging(syslog=True)`
  mirrors to the journal via `/dev/log`; `service serve` gains
  `--syslog --state-file` and the systemd unit passes both (state in
  the `RuntimeDirectory`); mutating control ops refresh the manifest
  via a `state_saver` hook. New `TestDaemonState` (11) +
  `TestLoggingConfig` (3) + health-op tests + control saver test;
  `TestSystemdUnit` asserts the new flags. Suite 299 → 317 (291 run +
  26 skipped).
  (269 run + 26 skipped).
- 2026-08-14 (**runnable status-service daemon + auto capability
  lifecycle**): `nyrqis_backend.py service serve` runs a
  `StatusServiceHost` daemon — the container manager, transport sender
  registry, capability manager, server, and service share state, so a
  container spawned against the daemon is automatically registered AND
  granted (defaults at spawn, revoked on terminate — NPS-010 §5;
  `ContainerManager` gains `capability_manager=` mirroring the
  ipc-registry hooks) and can call the status service with zero manual
  bookkeeping. SIGINT/SIGTERM shut it down cleanly. The status e2e
  now proves the whole chain automatically; new
  `TestContainerCapabilityLifecycle` (6) + `TestStatusServiceHost` (5,
  incl. a real CLI subprocess that binds 0700 and exits 0 on SIGTERM
  and a REAL container spawned through the daemon's own manager that
  completes the status CALL against it — the operator flow
  end-to-end). Suite 265 → 276 (250 run + 26 skipped).
- 2026-08-14 (**first real backend service on the transport**):
  `ipc/service.py` (`BackendStatusService`) is a container-facing
  CALL/REPLY service attached to an `IPCDatagramServer`: `ping`
  verifies the whole chain (transport + kernel identity + reply
  path), and `status` — capability-gated on `CAP_SYSTEM_INFO` (a
  default grant), denied fail-closed without a `CapabilityManager` —
  reports the backend version, uptime, and the caller's own container
  id and capability set. The server's CALL dispatch now swallows
  handler exceptions (a service bug replies "internal error", never
  kills the serving thread). New `TestBackendStatusService` (7 tests)
  and `test_container_calls_status_service` — a REAL container
  completes a `status` CALL through the auto-registry + capability
  enforcement. Suite 257 → 265 (239 run + 26 skipped).
- 2026-08-14 (**ADR-0020 migration #6: IPC transport hot path**):
  `rust/transport/` (ABI 1.0.0, `libc` the only dependency) ships the
  per-message syscall half of the Unix-domain datagram transport —
  sendto, poll+recvmsg with MSG_DONTWAIT, and the SCM_CREDENTIALS
  parse yielding the sender's global (pid, uid, gid) and bound path —
  behind the versioned FFI surface. `ipc/transport_codec.py` is the
  loader (search order, ABI gate, BackendUnavailable → Python-floor
  fallback, NYRQIS_RUST_FORCE=1) wired into
  `UnixDatagramEndpoint.send`/`receive`; binding/0700/SO_PASSCRED
  stays on the floor. New CI jobs: `rust-transport` (build + unit
  tests) and the required `rust-transport-conformance` gate (transport
  loader + differential classes forced through the FFI; raw-wire only,
  so the separate ipc-codec loader's force check stays honest). Suite
  239 → 251 (225 run + 26 skipped without the Rust crates). The crate
  is the documented close path for the NPS-003 §6.1 latency gate.
  **FFI surface v2 (ABI 2.0.0, 2026-08-14): caller-supplied buffers —
  recv recvmsgs directly into the caller's reusable wire buffer (zero
  malloc/free, `nyrqis_transport_free` gone), send is zero-copy;
  measured wire p50 307–357 µs (~28% under v1's ~426 µs, ~1.6× the
  ~200 µs floor) with the residual the ctypes boundary tax. Gate
  stays open; the migration stands on the boundary rule +
  byte-identical conformance.**
- 2026-08-13 (**ADR-0020 migration #5: container launch-plan
  primitives in Rust**): `rust/container/` (ABI 1.0.0, `libc` the only
  dependency) ships the pure launch-plan computations the manager
  makes per launch — the launcher argv (FIND-BACKEND-004), the cgroup
  v1/v2 resource plan (FIND-BACKEND-003: `notify_on_release=0`), the
  `--map-root-user` uid/gid maps, and the NPS-010 §4 state machine.
  `backend/container_codec.py` is the loader (search order, ABI gate,
  byte-identical `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097`
  malformed flat → the floor's `ValueError`, `-4098` invalid
  transition → `False`), wired into `transition_to`,
  `_launcher_args`, `_cgroup_v1_plan`, `_setup_cgroups_v2`, and the
  direct-syscall child's root maps. New CI jobs: `rust-container`
  (build + unit tests) and the required `rust-container-conformance`
  gate — the container-facing classes, including the end-to-end
  launch tests that route through the codec, forced through the FFI.
  Test suite: **257/257 (231 run + 26 skipped without the Rust
  crates)**.
- 2026-08-13 (**ADR-0020 Accepted + syscalls scaffold + CI test fix + session §17**):
  ADR-0020 v2.0.0 **Accepted** by Architecture Group (issue #2; the
  acceptance text is recorded in the ADR's Status section — the PAT can
  create issues but not comment/close them, so closing #2 is a manual
  step). **Migration priority #2 scaffolded**: `rust/syscalls/`
  (clone/unshare/sethostname/prctl FFI wrappers behind ABI-001, stub
  entry points returning ERR_INTERNAL, conformance plan; CI builds it).
  **Real CI bug fixed**: the `backend` job was red since 2026-08-12 —
  `test_auto_compact_is_the_mount_default` and
  `test_auto_compact_failed_mount_leaves_no_watcher` mocked `_build_fuse`
  but not `attach()`, so they failed on CI (no fusepy) while passing on
  this host (fusepy present); both now mock `attach()`, verified by
  re-running the suite under a no-fusepy pre-import patch — 113/113,
  4 live-mount tests correctly skipped. The rust-seccomp job (crate
  build + tests) has been green in CI all along — the crate compiles.
  **Consolidated session §17** (2026-08-13): every recorded finding
  reproduced for a second session.
- 2026-08-13 (**ADR-0020 governance + terminology + plan reconciliation**):
  AG review opened as **issue #2** (mirroring #1 for ADR-0019): ADR-0020
  remains `Proposed` until Architecture Group acceptance per NPC-001 §6.4.
  Glossary (NPC-006) v1.2.0 gained **Platform Boundary** and
  **Platform-Critical Execution Path** entries. `docs/implementation_plan.md`
  reconciled: the two stale language signals — Python `ctypes` syscall
  wrappers and the `fusepy` FUSE path — are now marked Rust-first
  platform-critical paths behind ABI-001, with ADR-0020 added to its
  citations.
- 2026-08-13 (**ADR-0020 v2.0.0 — canonical language matrix + platform-boundary principle**):
  the recorded language strategy moved from "Python + Rust by component
  class" to the canonical matrix — Rust, C++, and C as the platform
  languages (NyHAL **Rust-first**; C++ primary for NyUI/NyShell/NyGame;
  C where hardware requires), Python unrestricted **above the platform
  boundary** (tooling, SDK bindings, tests, CI, research) and barred as
  an execution language for platform-critical paths. New normative
  rule: **platform-critical execution paths must not depend on the
  Python interpreter** — the all-Python Linux backend is now explicitly
  the *reference implementation* whose platform-critical modules
  (seccomp enforcement, FUSE ops, container launch, IPC core) carry a
  migration obligation to the rust/seccomp queue. Docs reconciled in
  the same pass: how-to language guide (boundary-first), sdk/README
  (Rust + C++ core, Python SDK binding), rust/README, IMPLEMENTATION_STATUS
  (113/113), NPC-005 v1.14.0, mkdocs nav.
  the ADR-0020 seccomp wire format is implemented and verified
  (`SeccompPolicy.to_json()` / `policy_from_json()`, round-trip tests)
  and the round-trip **found and fixed two real aarch64 syscall-table
  bugs** (`readlink: 76` → splice alias; `faccessat: 49` → should be
  48; verified against `/usr/include/asm-generic/unistd.h`), with a
  unique-numbers guard test. The Rust implementation itself stays
  blocked: rustup's toolchain download does not complete on this host
  (three attempts, 2026-08-12) — documented in `rust/README.md` and
  `rust/seccomp/README.md`. **Daemon lifecycle (DAEMON_LIFECYCLE.md)
  partially implemented:** dirty-flag tracking, `NyFSMount.shutdown()`
  (dirty-gated final commit → unmount), SIGINT/SIGTERM handlers in
  blocking mode, and `auto_compact` is now the mount default — items 1–2
  of the spec's §4 gate done; AG tuning review (item 3) pending.
  Tests 103 → **108**.
- 2026-08-12 (**first ADR-0020 migration scaffold + daemon lifecycle + consolidated session**):
  the seccomp policy compiler is scaffolded at
  `source/nyhal-linux-backend/rust/seccomp/` — FFI boundary contract,
  Cargo manifest, and conformance test plan; **unbuilt** (no Rust
  toolchain on the dev host; the Python implementation remains the
  only shipped one until the conformance suite passes through the
  FFI). New `docs/how-to/choose-an-implementation-language.md`
  (component → language map) and an SDK language-strategy section;
  `source/nyhal-linux-backend/DAEMON_LIFECYCLE.md` (design note)
  answers ADR-0019's open question 1: `auto_compact` SHOULD become
  the mount default once signal handling, a final-commit shutdown
  contract, and AG tuning review land. The consolidated session
  (BENCHMARK_RESULTS §16) reproduced every recorded finding and
  surfaced a runner bug — the `--nyfs-mount` 60 s watchdog was never
  cancelled (would kill any full `--all` run with exit 99) — fixed.
- 2026-08-12 (**implementation-language strategy; ADR-0020**): the
  first recorded language decision — Python for the user-space backend
  and tooling, Rust for kernel-adjacent/hot-path/security-critical
  components, with a versioned FFI boundary (ABI rule) and an
  evidence-gated migration rule (no rewrite without measured
  performance or a security finding). Proposed pending Architecture
  Group review; index NPC-005 v1.13.0. Also closed the last §9
  benchmark gap: journal × block-size interplay (§15) — block size is
  an interleaved-mode lever only (save time flat 0.18–0.25 s under
  journal; ratio 6.38 → 6.50).
- 2026-08-12 (**compaction API + background watcher; ADR-0019**):
  journal compaction is exposed to daemons as `journal_bytes()` /
  `maybe_compact()` / `compact_journal()`, and `NyFSMount` gained an
  opt-in `auto_compact` watcher (background thread, lower threshold)
  so the materialize pass runs during idle intervals instead of
  stalling a transaction. Tests 99 → **103** (public compaction API,
  crash-mid-materialize leaves the journal intact, live-mount watcher
  trims the journal below the save-time threshold, failed mounts leave
  no watcher running). Benchmarks:
  §13 mixed write/read/commit loop — journal commits ~3.7–4× faster
  (131 vs 504 ms) at identical write throughput; §14 compaction cost —
  the deferred pass is an interleaved save of referenced blocks
  (~27 ms/block; 11.2 s per 417-block / 2.5 MB journal). The default
  flip's governance review package is **ADR-0019** (Proposed).
- 2026-08-12 (**journal commit is now the default commit mode**):
  `save()` defaults to `use_journal=True` (one fsync per transaction;
  interleaved path kept as `use_journal=False`). The full suite passes
  99/99 with the default flipped, including the live FUSE mount
  durability test (the fsync handler now commits via the journal, and
  the test asserts a non-empty journal). Benchmark sections that
  document the interleaved durability baseline (§7 persist, §10 dedup,
  §12 real corpus) pin `use_journal=False` explicitly so their recorded
  numbers stay reproducible. Implementer decision 2026-08-12;
  Architecture Group review is the formal governance step.
- 2026-08-12 (**journal commit + benchmark followups**): `save()` gained
  `use_journal=True` — an append-only journal (`state/journal.bin`)
  fsynced once per transaction before the atomic metadata swap, with
  `load()` journal fallback, torn-tail tolerance, and compaction past
  `journal_compact_bytes`. Benchmark (`BENCHMARK_RESULTS.md` §9, §12):
  **~60–70× faster commit** (0.20 s vs 11–15 s on 17.1 MB/417 blocks;
  2.0 s vs 123 s on a 3,855-file real corpus) at ~0.3% on-disk
  overhead — the decisive commit-cost lever; whether it becomes the
  default is an Architecture Group decision. Three more benchmark
  sections: cross-snapshot dedup (§10 — CoW sharing costs ~2% of an
  independent copy for a 20%-churn snapshot, ~49×), zstd-3 vs zlib-6
  codec comparison (§11 — LZ4 approximated with zlib, python-lz4
  unavailable; zlib wins ratio 3.13 vs 2.54, zstd wins speed ~23× on
  incompressible data), and real-corpus ratio (§12 — **1.29 : 1** on a
  real /usr/share sample vs 6.42 : 1 synthetic, the honest §2 data
  point). Fixed a decompress-throughput measurement bug in
  `benchmark_zstd.py` (was measured against compressed input; §4 table
  re-run). Test suite 91 → 99.
- 2026-08-12 (**NyFS FUSE write-batching fixed**): `NyFSMount` now
  negotiates `FUSE_CAP_BIG_WRITES` + `FUSE_CAP_WRITEBACK_CACHE` +
  `FUSE_CAP_MAX_PAGES` in the FUSE INIT handshake (`writeback_cache=True`,
  default) — fusepy never registers `init` and drops the connection
  pointer, so stock mounts got page-granular 4 KiB write requests. Fix:
  expose `init` on the operations adapter and override fusepy's `FUSE`
  class to set `fuse_conn_info.want`/`max_pages` (ctypes, libfuse 2.9
  layout). Kernel writes now batch at 128 KiB (8 requests per 1 MiB vs
  256); streaming writes ~40–46 MB/s vs ~1.8 MB/s (~25×), recorded in
  `BENCHMARK_RESULTS.md` §6. Correctness under writeback caching pinned
  by `test_random_overwrites_through_mount_with_writeback_cache` and
  `test_truncate_and_write_ordering_under_writeback_cache`; test suite
  89 → 91.
- 2026-08-12 (**NyFS snapshot diffing**): `fuse/nyfs.py` gained
  `diff_snapshots(a, b)` / `diff_live(snap)` — path-level added/removed/
  modified with before/after sizes, compared via per-block checksums (no
  decompression), so identical content is never reported as modified.
  9 new tests (`TestNyFSSnapshotDiff`); test suite 80 → 89.
- 2026-08-12 (**NyFS live FUSE mount verified**): `fuse/nyfs.py`'s mount
  wiring was fixed against the installed fusepy's actual API (operations
  must be callable — dispatch is `operations(op, path, *args)` — and
  `FUSE.__init__` runs the event loop itself, so there is no `main()`;
  non-blocking mounts run the constructor in a daemon thread). A real
  kernel mount now works end-to-end: multi-block write through the
  mount, `fsync(2)` → `save()` durability, CoW snapshot + overwrite +
  commit, unmount, reload from disk with snapshot restore, re-mount and
  read-back — all verified (`TestNyFSLiveMount`, 80/80 tests; skipped
  where fusepy//dev/fuse/fusermount are absent). Live-mount first-pass
  benchmark data recorded in `BENCHMARK_RESULTS.md` §6 (key finding: the
  kernel splits writes into 4 KiB requests, each rebuilding a 64 KiB CoW
  block). `benchmarks.py` gained `--nyfs-mount`; `NyFSMount.mount()`
  now forwards FUSE options (`max_write`, …). Temp diagnostic scripts
  removed.
- 2026-08-12 (**NyFS durability**): `fuse/nyfs.py` gained explicit
  `save()`/`load()` persistence (NPS-004 §7) — inode tree + snapshots in
  `state/metadata.json`, immutable block files in `state/blocks/`,
  write-blocks-then-atomic-metadata-swap ordering so a crash leaves the
  last committed state (verified by a crash-mid-save test), corrupt
  metadata/tampered blocks surface errors instead of silent corruption,
  `gc_blocks()` reclaims CoW-orphaned block files and stale temp files.
  Hardened after code review: both containing directories are fsynced so
  the metadata swap is a durable commit point, already-present block
  files are skipped (immutable ⇒ re-save is idempotent, verified by
  test), and the FUSE `fsync` handler maps to `save()`. Test suite
  71 → 79.
- 2026-08-12 (**consolidated benchmark runner + NyFS re-run**):
  `tests/benchmarks.py` now runs all runnable plan sections in one
  reproducible script (`--all`, `--ipc`, `--bucket`, `--zstd`, `--nyfs`,
  importing the Zstd sweep). Proxy numbers re-run after the per-block
  CoW rewrite: streaming writes ~162 MB/s (~4× old path), small-op
  pattern dominated by per-call compress + per-read checksum verify
  (recorded in `BENCHMARK_RESULTS.md` §5; no gate met).
- 2026-08-12 (**threat model complete + benchmark data**): Phase 7
  Package Trust Model (`NPS-027`) landed, closing Milestone 12; NPS-006
  §6 amended (v1.1.0 — signature verification per NPS-026 §6, overlay/
  base provenance, package-event audit); REQ-SEC-0003..0006 added (ledger
  now 40 requirements); first-pass Zstd level-sweep data recorded in
  `tests/BENCHMARK_RESULTS.md` §2 (ratio/compute knee at levels 3–7).
- 2026-08-12 (**rebrand**): project name changed from **Nythera** to
  **Nyrqis** everywhere (docs, code identifiers, `NYTHERA_LOG_LEVEL` →
  `NYRQIS_LOG_LEVEL`, `nythera-policy-` → `nyrqis-policy-`, LICENSE
  placeholder, `nythera_backend.py` → `nyrqis_backend.py`,
  `000-THE_NYTHERA_MANIFEST.md` → `000-THE_NYRQIS_MANIFEST.md`). The `Ny`
  prefix is retained (now denotes Nyrqis: `nyhal`, `nyctr`, `nyfs`,
  `nygi`, `nypkg`). The repository directory and GitHub URL
  (`Myco-mycelium/Nythera`) keep the old name for now — the three URL
  references are the only remaining occurrences, documented in
  `REBRAND_NOTICE.md` (CR-0035). New commits are authored `Nyrqis
  Bootstrap <bootstrap@nyrqis.local>`. Name review (2026-08-12, recorded
  in `REBRAND_NOTICE.md`): `Nyrqis` shows no collisions (OS, software,
  trademark, domain) beyond a minor fictional location; the `Ny` prefix
  is low-risk as an internal architecture convention. The local directory
  is renamed to `Nyrqis/`; the GitHub repository rename
  (`Myco-mycelium/Nythera` → `Nyrqis`) is the one remaining manual step
  (needs a maintainer — no `gh`/credentials here); the three URL
  references are deliberately left pointing at the current repo name
  because GitHub redirects renamed repositories automatically.
  Registry-level trademark check (2026-08-12): no registered/pending/
  dead trademark for `Nyrqis` or variants (`Nyrquis`/`Nyrqys`) in any
  class across USPTO, EUIPO/TMView, and WIPO — recorded in
  `REBRAND_NOTICE.md`.
- 2026-08-12 (backend hardening): Linux Backend data-plane capability
  enforcement (seccomp-BPF installed in-container — `FIND-BACKEND-002`),
  shell-free launcher (`FIND-BACKEND-004`), cgroup v1 release_agent
  hardening (`FIND-BACKEND-003`), boot transition validation +
  Secure Boot status reporting (`FIND-BOOT-001/002`), real FUSE
  operations + fusepy wiring (ADR-0016), receive-side IPC capability
  check, and the `CAP-MEDIA-*` enum split. 54/54 tests pass; verified
  end-to-end container runs on this host (hostile hostname passed
  verbatim; read-only container's write-open refused with `EPERM`).
  Docs updated in the same pass: `IMPLEMENTATION_STATUS.md` v0.2.0,
  `README_IMPLEMENTATION.md`, `requirements.txt`, this file.
- 2026-08-12: replaced all remaining "Diátaxis category placeholder" and
  "will be added as specifications are accepted" READMEs with real
  indexes (tutorials, how-to, diagrams, assets, adr, nps, npc, abi, api,
  package-format, object-registry, all seven explanation subsystems);
  updated `source/README.md` (full Linux Backend) and `tests/README.md`
  (BENCHMARK_PLAN link); added Mermaid rendering to `mkdocs.yml` for the
  new diagrams.
- 2026-07-13: `tools/check_depends_on_cycles.py` added and run for the
  first time, surfacing 4 real cycles across documents committed in
  earlier sessions. All fixed by removing the back-reference that closed
  each loop, following the same rule documented in the script's own
  docstring: a document may cite something that depends on it in prose,
  but must not list it back in its own `depends_on` front-matter.
