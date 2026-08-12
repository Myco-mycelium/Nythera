# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nyrqis repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-08-12

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus an externally-contributed,
independently-verified Linux Backend implementation
(`source/nyhal-linux-backend/`, 54/54 tests passing). Milestone 12 — the
phased security threat model — is in progress: Phases 1–6 are done
(`NPS-018` methodology, `NPS-019` attack surface enumeration, `NPS-020`
STRIDE analysis, `NPS-021` privilege/escalation analysis, `NPS-022`
container escape analysis, `NPS-023` secure boot, `NPS-024` AI). Phase 4
found the most severe issue in the threat model to date (capability
enforcement covers IPC only, not direct syscalls); Phase 6 found the
suggest-vs-act boundary NPC-001 §11.1 depends on has no requirement that
its confirmation UI actually be unspoofable — meaning even a
perfectly-implemented assistant following the spec as written wouldn't
have closed the gap. Across Phases 2–6, 29 unique findings have surfaced
and every one has a disposition — no bare observations left dangling.
Phase 7 (Package Trust Model) is the last planned phase — see
`docs/reference/security/README.md`. A docs-backlog pass (2026-08-12)
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
10 accepted, 7 held (named blockers below; 4 are new decisions pending
Architecture Group sign-off, not benchmark-blocked), 1 rejected.

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Accepted
- [x] ADR-0003 Game disk images with overlay — Accepted
- [x] ADR-0004 Containerized execution model — Accepted
- [x] ADR-0005 Windows compatibility translation layer — Accepted
- [x] ADR-0006 Hybrid microkernel as kernel base — Accepted
- [ ] ADR-0007 Zstandard as default compression codec — **Proposed**, blocked on compression-level benchmark data
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Accepted
- [ ] ADR-0009 Per-container token-bucket IPC rate limiting — **Proposed**, blocked on default bucket-parameter benchmark data
- [x] ADR-0010 Vulkan as native graphics API foundation — Accepted
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Accepted
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Accepted
- [ ] ADR-0013 EEVDF-derived scheduler with real-time priority class — **Proposed**, algorithm family decided, tuning parameters blocked on benchmark data
- [ ] ADR-0014 UEFI Secure Boot with user-enrollable keys — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0015 Shared dynamic binary translation for ARM/x86 — **Proposed**, approach decided; performance validation blocked on benchmark data
- [ ] ADR-0016 NyFS Linux Backend as user-space FUSE filesystem — **Proposed**, initial strategy decided; kernel-module fallback blocked on FUSE-overhead benchmark data
- [x] ADR-0017 Reject domain-grouped NPS renumbering — **Rejected** (the project's first; considered and explicitly declined, not left unresolved)
- [ ] ADR-0018 Hash-chained append-only log for capability audit records — **Proposed**, pending Architecture Group review (not benchmark-blocked)

## Specifications (NPS)
13 accepted, 13 held (4 named benchmark/dependency blockers, plus
NPS-018..NPS-024 — new Draft documents pending Architecture Group
sign-off, not benchmark-blocked — and NPS-025, NPS-026 — new Draft
documents from the 2026-08-12 Milestone 11 backlog pass).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted (v1.2.0: GPU command buffer validation + submission timeout added, closing threat model findings FIND-KERNEL-001/003)
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking); v1.1.0 added a shared-memory zeroing requirement, closing threat model finding FIND-CONTAINER-003
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

## Requirements Database
NPC-009 (Draft) + seed ledger at `docs/reference/requirements/REQUIREMENTS.md`:
32 requirements across all 17 domain prefixes. Nearly all traced to
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

## Build System
Not started.

## Documentation Site
Structure created; MkDocs Material configured with full nav (zero warnings
under `mkdocs build --strict`); CI workflow (`.github/workflows/docs.yml`)
builds and deploys to GitHub Pages on push to `main`. Version pinned via
`requirements-docs.txt` due to MkDocs Material's own public warning about
breaking, currently-unsuitable-for-production changes in MkDocs 2.0.

## Next Actions
Benchmark-gated (unblocks the 3 ADRs + 4 NPS documents held above; no
numbers exist yet — see `tests/BENCHMARK_PLAN.md` for methodology):
1. Benchmark IPC round-trip latency (unblocks NPS-003, transitively NPS-010's remaining path once ADR-0009 also clears).
2. Benchmark default IPC token-bucket parameters (unblocks ADR-0009, then NPS-010 §7.1).
3. Benchmark Zstd compression levels, install size vs. load time (unblocks ADR-0007, then NPS-005).
4. Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning (unblocks ADR-0013 in full; algorithm family is already decided).
5. Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).
6. Benchmark FUSE overhead for NyFS's Linux Backend (ADR-0016; determines whether the FUSE decision holds or needs a kernel-module fallback).
7. Benchmark hash-chain computation/verification overhead before ADR-0018 exits Proposed — expected to be negligible but not asserted as fact without a measurement, per NPC-002 §5.2.

Genuinely still open, not fabricable:
8. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) — requires actual contributors, not something to invent.
9. Choose a real license (`LICENSE` is still the Milestone 1 placeholder — "no rights granted... until a formal license is adopted"). This is a legal/business decision for the repository owner, not one to pick unilaterally on their behalf.
10. Enable GitHub Pages with source "GitHub Actions" (Settings → Pages) so `.github/workflows/docs.yml`'s deploy step has somewhere to publish to — the workflow runs regardless, but won't be visibly served until this is set.
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
