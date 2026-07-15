# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-15

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus a first tested code spike
(`nyctr` container primitive). Milestone 12 — the phased security threat
model — is in progress: Phases 1–3 are done (`NPS-018` methodology,
`NPS-019` attack surface enumeration, `NPS-020` STRIDE analysis,
`NPS-021` privilege boundaries and capability escalation). Between
Phases 2 and 3, 8 findings surfaced and every one has a disposition — no
bare observations left dangling: `NPS-001`, `NPS-003`, `NPS-010`, and
`NPS-011` were amended directly; a new `ADR-0018` (hash-chained audit
log) and 4 new `REQ-*` entries were added; the package-signing gap
strengthened Milestone 11's package-format priority; and one governance
risk was recorded against `NPC-008` rather than forced into a runtime
fix that wouldn't actually address it. Phases 4–7 remain planned and
sequenced but not started — see
`docs/reference/security/README.md`. Milestone 11's remaining 9 gap
categories (diagrams, API reference, ABI specification, full object
registry, package format split, governance expansion, build architecture
docs, performance budgets, developer onboarding) are still logged as a
prioritized backlog in `007-PROJECT_ROADMAP.md` — not built yet.

## Governance Documents

- [x] NTM-000 The Nythera Manifest — Accepted
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
13 accepted, 8 held (4 named benchmark/dependency blockers, plus NPS-018,
NPS-019, NPS-020, and NPS-021 — new Draft documents pending Architecture
Group sign-off, not benchmark-blocked).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted (v1.2.0: GPU command buffer validation + submission timeout added, closing threat model findings FIND-KERNEL-001/003)
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking); v1.1.0 added a shared-memory zeroing requirement, closing threat model finding FIND-CONTAINER-003
- [x] NPS-004 NyFS Filesystem Core — Accepted
- [ ] NPS-005 Transparent Compression Policy — **Draft**, transitively blocked on ADR-0007 (defines default levels tied to the still-Proposed codec ADR)
- [x] NPS-006 Nythera Game/Application Image Format (.nygi) and Overlay — Accepted
- [x] NPS-007 Windows Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-008 Android Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-009 Adaptive UI Shell — Accepted (VR resolved: explicitly deferred to a future milestone, not an open mode definition)
- [ ] NPS-010 Container Runtime — **Draft**, transitively blocked on ADR-0009 (§7.1 normatively requires its still-Proposed rate-limiting mechanism); v1.1.0 added atomic grant-check (§4.2) and tamper-evident audit log requirement (§8.1, per new ADR-0018), closing threat model findings FIND-CAPABILITY-001/002
- [x] NPS-011 Capability Registry — Accepted (27 capabilities registered: 25 through Milestone 10, minus 1 split into 3 this pass — `CAP-MEDIA-LIBRARY` → `CAP-MEDIA-IMAGES`/`CAP-MEDIA-VIDEO`/`CAP-MEDIA-AUDIO`, closing threat model finding FIND-CAPABILITY-004; still intentionally incomplete by design)
- [x] NPS-012 Controller and Input Subsystem — Accepted (VR capability formally deferred, not left ambiguous — §5.1)
- [x] NPS-013 GPU Feature Support — Accepted (§7.3 documents current FSR/XeSS/FSR4 vendor SDK status, verified 2026-07-13)
- [x] NPS-014 Emulator Hub — Accepted
- [x] NPS-015 Local AI Assistant — Accepted
- [x] NPS-016 Optional Cloud Synchronization — Accepted
- [x] NPS-017 NyHAL — Kernel Abstraction Layer and Backend Contract — Accepted
- [x] NPS-018 Threat Model Methodology and Trust Boundaries — Draft (Threat Model Phase 1a)
- [x] NPS-019 Attack Surface Enumeration — Draft (Threat Model Phase 1b, 24 surfaces catalogued)
- [x] NPS-020 STRIDE Analysis per Trust Boundary — Draft (Threat Model Phase 2, 10 boundaries, 3 findings drove real spec amendments this pass)
- [x] NPS-021 Privilege Boundaries and Capability Escalation Analysis — Draft (Threat Model Phase 3, 5 findings — 4 resolved, 1 governance-level recorded not technically fixed)

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
Not started.

## Package Format
Not started.

## Source Code
Two things now, not one:

- `source/nyhal-linux-backend/poc-container/` (`nyctr.py`) — the original
  spike: proves the most basic container primitive (PID/mount/UTS/user
  namespace isolation + a cgroup memory/pid limit) works on stock Linux.
  Superseded in scope by the item below but kept as the minimal reference
  it was designed to be.

- `source/nyhal-linux-backend/` — a substantially fuller Linux Backend
  implementation (`backend/container.py`, `backend/capability.py`,
  `ipc/core.py`, `fuse/nyfs.py`, `boot/lifecycle.py`), contributed
  externally (not authored in this session — merged from the remote after
  a `git push` conflict surfaced it) and **independently verified before
  being documented here**: `python3 -m pytest test_backend.py` passes
  20/20. Real cgroup v1/v2 detection and namespace usage confirmed by
  reading the code, not assumed from its own claims.

  Its own `IMPLEMENTATION_STATUS.md` (`document_id: IMPL-001`) self-rates
  as **"Experimental Backend — Core Implementation Complete,
  Performance/Integration Work Pending,"** explicitly **not yet
  conformant** to NPS-017 §5: capability enforcement exists as tracked
  state (grant/revoke/attenuate/audit, all tested) but has no seccomp/LSM
  enforcement wired in yet; the NyFS FUSE integration is structural only
  (no `pyfuse3`/`fusepy` daemon yet); no IPC latency, FUSE overhead, or
  compression benchmarks exist. That self-assessment reads as accurate
  against the code, not inflated — consistent with this project's
  existing discipline.

  **Not yet reconciled with this session's threat model work**: Phase 4
  (Container Escape Analysis, not yet started) should specifically
  re-examine `FIND-BACKEND-001` against this implementation — it's a
  meaningfully different situation than the bare `nyctr` PoC (capability
  *tracking* now exists, even though OS-level *enforcement* still
  doesn't), and the finding's severity/status should be reassessed with
  that distinction in mind rather than left describing the old PoC.

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

Not benchmark-gated, resolved this pass:
- ~~Resolve shared ARM instruction-translation approach~~ — ADR-0015 (shared dynamic binary translation, JIT + hot-path cache).
- ~~Scope VR integration~~ — explicitly deferred to a future milestone (NPS-012 §5.1), not designed further; this was the decision, not a placeholder.
- ~~Evaluate vendor-neutral upscaling integration point~~ — NPS-013 §7.3, grounded in current (2026-07-13) FSR/XeSS/FSR4 vendor SDK status.
- ~~Decide NyFS's Linux Backend implementation strategy~~ — ADR-0016 (FUSE first, kernel-module fallback open pending benchmark #6 above).
- ~~Decide secure boot key management~~ — ADR-0014 (UEFI Secure Boot, shim-equivalent chain, user-enrollable keys).
- ~~Configure CI build for the MkDocs Material site~~ — `.github/workflows/docs.yml`, verified locally with `mkdocs build --strict` (zero warnings) before committing.
- Expand NPS-011 Android permission mapping — 8 new capabilities added (contacts, calendar, telephony, SMS, sensors, media library, NFC, biometric); still intentionally incomplete per NPS-011 §6, expand further as gaps are found.

Genuinely still open, not fabricable:
7. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) — requires actual contributors, not something to invent.
8. Begin implementation work on the Linux Backend (NPS-017 §6) — the container-primitive spike (`source/nyhal-linux-backend/poc-container/`) is done and passing; next up is capability enforcement (seccomp/LSM, NPS-017 §4.2), which needs the capability registry (NPS-011) mapped onto concrete Linux mechanisms, not just documented in the abstract.
9. Choose a real license (`LICENSE` is still the Milestone 1 placeholder — "no rights granted... until a formal license is adopted"). This is a legal/business decision for the repository owner, not one to pick unilaterally on their behalf.
10. Enable GitHub Pages with source "GitHub Actions" (Settings → Pages) so `.github/workflows/docs.yml`'s deploy step has somewhere to publish to — the workflow runs regardless, but won't be visibly served until this is set.

Documentation hygiene, fixed this pass:
- A prior review pass (Milestone 9) left several documents with a Markdown
  table-formatting bug: the row recording that milestone's own review
  (e.g. "Architecture Group review completed... Status: Draft → Accepted")
  had gotten separated from its revision-history table by a blank line,
  rendering as an orphaned single-row table instead of part of the
  sequence. This affected all 13 `Accepted` NPS documents (every one that
  went through the Milestone 9 review): NPS-001, 004, 006, 007, 008, 009,
  011, 012, 013, 014, 015, 016, 017 — fixed across the M10 commit and this
  pass; verified via `mkdocs build --strict` and a repo-wide grep for the
  pattern, which is now clean.
- `mkdocs.yml`'s `repo_url` was still the bootstrap placeholder
  (`your-org/Nythera`) despite the real repository existing since
  Milestone 1's first push; corrected to `Myco-mycelium/Nythera`.

External review response (2026-07-13), not fabricable/decided instantly:
9. Work through Milestone 11's remaining prioritized backlog
   (`007-PROJECT_ROADMAP.md`) — architecture diagrams, API reference, ABI
   specification, full object registry, package format split, governance
   expansion, build architecture docs, performance budgets, developer
   onboarding. Each is roughly the size of a prior milestone on its own;
   not attempted in a single pass.
10. Continue the threat model (Milestone 12, `docs/reference/security/`):
    Phase 4 (Container Escape Analysis & Runtime Isolation) is next,
    deepening `TB-CONTAINER`/`TB-BACKEND` — including a fuller look at
    `FIND-BACKEND-001`, the `nyctr` PoC's complete lack of capability
    enforcement, which Phase 3 explicitly left for Phase 4 rather than
    duplicating. Phases 5–7 (secure boot, AI, package trust) follow.
11. Elevate priority on Milestone 11's package-format gap category
    (specifically digital signatures) — Phase 2's `FIND-PACKAGE-001`
    found that `.nygi` integrity currently relies on checksums alone,
    which don't establish publisher authenticity; an attacker can tamper
    with an image and simply recompute a valid checksum. Not fixable by a
    quick amendment like the other two Phase 2 findings; needs a real
    package-signing/PKI specification.
12. Benchmark hash-chain computation/verification overhead before
    ADR-0018 exits Proposed — expected to be negligible but not asserted
    as fact without a measurement, per NPC-002 §5.2.
13. Revisit `NPC-008`'s "claim an Unassigned slot without a vote" design
    once the project has more than one active contributor —
    `FIND-CAPABILITY-005` (NPS-021 §5.4) flagged this as a soft privilege
    path, recorded against the governance document rather than given a
    runtime fix that wouldn't be the right tool for it.
14. Wire `tools/check_depends_on_cycles.py` into `.github/workflows/docs.yml`
    as a CI step. It found 4 real circular dependencies this pass
    (NPS-001↔ADR-0012, NPS-001↔ADR-0013, NPS-001↔ADR-0014,
    NPS-007/008↔ADR-0015 — each individually reasonable when added, only
    circular together) that had been sitting in already-committed,
    already-pushed documents undetected. Running it by hand caught them
    this time; it should run automatically going forward.

## Documentation Hygiene Notes *(ongoing)*
- 2026-07-13: `tools/check_depends_on_cycles.py` added and run for the
  first time, surfacing 4 real cycles across documents committed in
  earlier sessions. All fixed by removing the back-reference that closed
  each loop, following the same rule documented in the script's own
  docstring: a document may cite something that depends on it in prose,
  but must not list it back in its own `depends_on` front-matter.
