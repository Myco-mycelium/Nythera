---
title: Project Roadmap
document_id: NPC-007
version: 1.3.0
status: Draft
classification: Informative
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: Quarterly
depends_on: [NTM-000, NPC-003]
---

# NPC-007 — Project Roadmap

This document is **informative**. It reflects current planning intent and
does not create binding requirements; the milestone definitions in
NPC-003 §7 are the source of truth for what each milestone contains.

## Current Milestone

**M12 — Threat Model (phased)**, Phase 1 complete. Building the security
threat model requested by the Milestone 11 external review in explicit
phases rather than one pass — see "Milestone 12" section below and
[`docs/reference/security/README.md`](../reference/security/README.md)
for the full phase table. M9, M10, and M11's structural items remain
complete underneath it.

## Roadmap

### M1 — Repository Bootstrap
- [x] NTM-000 The Nythera Manifest
- [x] NPC-001 Project Constitution (Draft)
- [x] NPC-002 AI Collaboration Protocol (Draft)
- [x] NPC-003 Engineering Handbook (Draft)
- [x] NPC-004 Specification Index
- [x] NPC-005 ADR Index (+ ADR-0001..0005 drafted)
- [x] NPC-006 Glossary
- [x] NPC-007 Project Roadmap (this document)
- [x] REPOSITORY_STATE.md, CHANGE_REQUEST_LOG.md
- [x] MkDocs Material site configuration
- [ ] CI build for the documentation site
- [x] CONTRIBUTING.md and LICENSE

### M2 — Platform Constitution
- [x] Move NPC-001/002/003 from Draft to Accepted after Architecture Group review
- [x] Establish subsystem ownership list (NPC-008 `SUBSYSTEM_OWNERS.md` — all subsystems currently Unassigned, pending contributors)
- [x] Retroactively add `ai_assisted: true` to all existing normative documents (NPC-002 §3.1 compliance)

### M3 — Core Architecture
- [x] Kernel base selection ADR (ADR-0006 — hybrid microkernel, Proposed)
- [x] NPS-001 Kernel Architecture and Boot (Draft)
- [x] NPS-002 Process and Thread Model (Draft)
- [x] NPS-003 Inter-Process Communication and Capability Passing (Draft)
- [ ] Architecture Group review to move ADR-0006 and NPS-001..003 to Accepted
- [ ] Scheduler algorithm decision (NPS-001 §7 open question)
- [ ] IPC round-trip latency benchmark (NPS-003 §6.1)

### M4 — Storage
- [x] Finalize ADR-0002 (filesystem) into NPS specifications (NPS-004, Draft)
- [x] Compression codec selection (ADR-0007 — Zstd default, LZ4 fast-path override)
- [x] NPS-005 Transparent Compression Policy (Draft)
- [x] NPS-006 Game/Application Image Format (.nygi) and Overlay (Draft, implements ADR-0003)
- [ ] Benchmark Zstd compression levels (install size vs. load time) before ADR-0007 exits Proposed
- [ ] Architecture Group review to move ADR-0002/0003/0007 and NPS-004..006 to Accepted

### M5 — Runtime
- [x] Windows compatibility subsystem NPS (NPS-007, Draft, implements ADR-0005)
- [x] Android runtime approach decision (ADR-0008 — AOSP-based container runtime)
- [x] Android runtime subsystem NPS (NPS-008, Draft, implements ADR-0008)
- [x] Adaptive UI shell NPS (NPS-009, Draft — desktop/gaming/phone/tablet/handheld/server modes)
- [ ] Resolve shared ARM instruction-translation approach (NPS-007 §7 / NPS-008 §7)
- [ ] Architecture Group review to move ADR-0005/0008 and NPS-007..009 to Accepted

### M6 — Security
- [x] Container runtime NPS (NPS-010, Draft — lifecycle, capability assignment, revocation, resource limits)
- [x] IPC rate-limiting mechanism decision (ADR-0009 — per-container token buckets)
- [x] Capability registry populated (NPS-011, Draft — 14 capabilities registered, closing the gap NPC-001 §9.3 has referenced since Milestone 1)
- [ ] Benchmark default IPC token-bucket parameters before ADR-0009 exits Proposed
- [ ] Expand Android permission mapping (NPS-011 §6) incrementally
- [ ] Architecture Group review to move ADR-0009 and NPS-010/011 to Accepted

### M7 — Gaming Subsystem
- [x] Native graphics API foundation decision (ADR-0010 — Vulkan)
- [x] Controller and input subsystem NPS (NPS-012, Draft — native controllers, Steam Input, VR deferred)
- [x] GPU feature support NPS (NPS-013, Draft — HDR, VRR, ray tracing, upscaling)
- [x] Emulator hub NPS (NPS-014, Draft — user-supplied ROM/BIOS only, no bundled content)
- [ ] Scope VR integration and define its capability class (NPS-012 §5.1)
- [ ] Evaluate vendor-neutral upscaling integration point (NPS-013 §9)
- [ ] Architecture Group review to move ADR-0010 and NPS-012..014 to Accepted

### M8 — AI Subsystem
- [x] AI assistant containerization decision (ADR-0011 — ordinary capability-scoped container, no implicit elevated access)
- [x] Local AI assistant boundary NPS (NPS-015, Draft — functional scope, suggest-vs-act boundary, offline operation, disableability)
- [x] Optional cloud sync NPS (NPS-016, Draft — opt-in per data class, offline-first, conflict handling)
- [x] New capability registry entries: `CAP-AI-DIAGNOSTICS-READ`, `CAP-AI-SUGGEST-ACTION`, `CAP-CLOUD-SYNC`
- [ ] Architecture Group review to move ADR-0011 and NPS-015/016 to Accepted

### Cross-Cutting — NyHAL Pluggable Kernel Backend
Added after the original M1–M8 scope was fully drafted, in response to a
practical concern: shipping something runnable while the NyKernel (ADR-0006)
matures.

- [x] ADR-0012: adopt NyHAL as a pluggable kernel abstraction layer (Linux Backend, Experimental Backend, NyKernel Backend)
- [x] NPS-017: NyHAL backend contract (containers, capabilities, IPC, storage, boot — all as portable guarantees)
- [x] NPS-001 amended to scope itself to the NyKernel Backend specifically, without changing its normative content
- [x] Architecture Group review of ADR-0012/NPS-017 — completed as part of Milestone 9
- [x] Begin Linux Backend implementation work (NPS-017 §6) — first spike done in Milestone 10 (`source/nyhal-linux-backend/poc-container/nyctr.py`); container-primitive isolation only, §4.2–§4.5 remain unstarted (full status tracked in Milestone 10's section below)
- [x] Decide NyFS's Linux Backend implementation strategy (NPS-017 §8) — ADR-0016: FUSE first, kernel-module fallback open pending benchmark

### M9 — Architecture Group Review
A full review pass across every M1–M8 document, resolving what could
honestly be resolved and naming what couldn't, per NPC-001 §5's rule that
`Draft` → `Accepted` requires a recorded review.

- [x] ADR-0013: adopt an EEVDF-derived scheduler with a real-time priority class, resolving NPS-001 §7's open scheduler-algorithm question (tuning parameters remain benchmark-gated)
- [x] Resolve VR scoping ambiguity (NPS-012 §5.1, NPS-009 §7): explicitly deferred to a future milestone rather than left as an open question indefinitely
- [x] Verify NPS-013 §7.3's FSR/XeSS/FSR4 vendor SDK licensing claims via web search before accepting (per NPC-002 §5.1) — confirmed accurate
- [x] Accept ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0006, ADR-0008, ADR-0010, ADR-0011, ADR-0012 (9 of 13 ADRs)
- [x] Accept NPS-001, NPS-004, NPS-006, NPS-007, NPS-008, NPS-009, NPS-011, NPS-012, NPS-013, NPS-014, NPS-015, NPS-016, NPS-017 (13 of 17 NPS)
- [x] Keep ADR-0007, ADR-0009, ADR-0013 at `Proposed` — each explicitly benchmark/tuning-blocked in its own text, not a review oversight
- [x] Keep NPS-002, NPS-003 at `Draft` — each self-blocks on benchmark data in its own normative text (§9, §6.1 respectively)
- [x] Keep NPS-005, NPS-010 at `Draft` — each transitively blocked on an ADR (0007, 0009) that isn't Accepted yet, with an explicit status note added explaining why
- [ ] Re-review ADR-0007, ADR-0009, ADR-0013, NPS-002, NPS-003, NPS-005, NPS-010 once their respective benchmarks land

### M10 — Backlog Closure Pass
A follow-up pass working through Milestone 9's remaining `Next Actions`
list, closing everything that could honestly be closed without fabricated
data, and naming what genuinely still requires either real hardware
benchmarking or real contributors.

- [x] ADR-0014: adopt UEFI Secure Boot with user-enrollable keys, resolving NPS-001 §7's secure-boot open question
- [x] ADR-0015: adopt a shared dynamic binary translation approach for ARM/x86, resolving NPS-007 §7 / NPS-008 §7's shared deferral
- [x] ADR-0016: implement NyFS's Linux Backend as a user-space FUSE filesystem, resolving NPS-017 §8's storage-strategy question
- [x] Expand NPS-011 Android permission mapping: `CAP-CONTACTS`, `CAP-CALENDAR`, `CAP-TELEPHONY`, `CAP-SMS`, `CAP-SENSORS`, `CAP-MEDIA-LIBRARY`, `CAP-NEAR-FIELD`, `CAP-BIOMETRIC`
- [x] `tests/BENCHMARK_PLAN.md`: methodology for every pending benchmark (IPC latency, compression levels, token-bucket tuning, FUSE overhead) — no fabricated numbers, per NPC-002 §5.2
- [x] `.github/workflows/docs.yml`: CI build + GitHub Pages deploy for the MkDocs site, verified locally with `mkdocs build --strict` (zero warnings) before committing
- [x] `requirements-docs.txt`: pin `mkdocs-material` to the 9.x line given the Material team's own public warning about breaking, production-unready changes in MkDocs 2.0
- [ ] Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` — requires actual contributors, intentionally not fabricated
- [x] Begin Linux Backend implementation work (NPS-017 §6) — see the Cross-Cutting NyHAL section above; first spike done, §4.2–§4.5 remain unstarted
- [ ] Run the four benchmarks defined in `tests/BENCHMARK_PLAN.md` once something exists to measure

### M11 — Response to External Repository Review
An external review of the repository (2026-07-13) rated it 8.6/10 and
proposed 10 gap categories plus two structural recommendations. Both
structural recommendations were acted on immediately; the gap categories
are logged here in the review's own stated priority order, to be worked
through incrementally rather than all at once — each is roughly the size
of a prior milestone by itself.

**Structural recommendations — resolved:**
- [x] Requirements Database — the review's own top priority. NPC-009 +
  seed ledger (`docs/reference/requirements/REQUIREMENTS.md`), 29
  requirements across all 17 domain prefixes, traced to existing
  `Accepted` specification sections, not fabricated new obligations.
- [x] NPS domain-grouped renumbering (NPS-100/200/...) — considered and
  **rejected** via ADR-0017: breaks every existing cross-reference across
  17 already-cited documents for a benefit (domain grouping) the existing
  `subsystem` front-matter field and `SPECIFICATION_INDEX.md` already
  provide at zero citation-breakage cost.

**Gap categories — prioritized per the review's own "what I would focus
on next" ordering, not yet built:**
1. [~] Security architecture and threat model (`docs/reference/security/`) — **in progress as Milestone 12**, phased; see below
2. [ ] Complete Object Registry (`docs/reference/object-registry/`) — every object type (Workspace, Window, Application, Package, Capability, Identity, Game, Mod, Controller, GPU, Notification, AI Conversation, Device, Service) with fields, lifecycle, permissions, serialization, relationships
3. [ ] Capability Registry — ongoing by design (NPS-011 §5), not a discrete milestone item; already at 25 entries
4. [ ] Public API specification (`docs/reference/api/`) — NyHAL, NyCore, Runtime, Package, Filesystem, Window, AI, Gaming, Plugin APIs
5. [ ] ABI specification (`docs/reference/abi/`) — calling conventions, binary compatibility, symbol versioning, plugin ABI, driver ABI, runtime ABI
6. [ ] Architecture diagrams (`docs/diagrams/`) — boot sequence, NyHAL architecture, NyCore, object graph, capability graph, package lifecycle, runtime lifecycle, scheduler, memory manager, game package layering, AI subsystem, identity subsystem, update pipeline
7. [ ] Package format specification, split per the review's suggestion — manifest, digital signatures, compression, delta updates, integrity tree, streaming install, rollback, dependency resolution
8. [ ] Governance expansion — RFC process, release process, deprecation policy, versioning policy, branching strategy, commit conventions, ADR workflow (some of this already exists in NPC-001/003; the review's ask is to make it a dedicated, fuller treatment)
9. [ ] Build architecture (`docs/reference/build/`) — toolchain, build graph, cross-compilation, reproducible builds, CI stages, artifact signing
10. [ ] Performance engineering budgets — startup targets, memory budgets, IPC latency, filesystem performance, gaming targets, AI inference targets (methodology already exists in `tests/BENCHMARK_PLAN.md`; the review's ask is target *numbers*, which still require real hardware and are not fabricated ahead of that)
11. [ ] Developer onboarding — install prerequisites, build docs, coding standards, repository tour, first-contribution guide, debugging, testing, documentation style

An "Identity subsystem" and "Update pipeline" were mentioned by the review
under diagrams/object-registry but have no corresponding NPS document yet
— they surface a genuine gap in the specification set itself, not just in
diagrams, and should get their own NPS before being diagrammed.

### M12 — Threat Model (phased)
Building the complete security threat model in explicit phases, per
gap category 1 of Milestone 11. Phase order follows dependency: later
phases apply earlier ones to specific trust boundaries rather than
starting from scratch. Full phase table and links:
[`docs/reference/security/README.md`](../reference/security/README.md).

- [x] Phase 1a — Threat Model Methodology & Trust Boundaries (`NPS-018`): STRIDE framework, 10 trust boundaries derived from existing decisions, 6 attacker profiles, a deliberately simple (non-CVSS) severity model
- [x] Phase 1b — Attack Surface Enumeration (`NPS-019`): 24 concrete surfaces catalogued across all 10 trust boundaries, each citing its governing spec
- [ ] Phase 2 — STRIDE Analysis per Trust Boundary: apply NPS-018 §3 to every surface in NPS-019 §3
- [ ] Phase 3 — Privilege Boundaries & Capability Escalation Analysis
- [ ] Phase 4 — Container Escape Analysis & Runtime Isolation
- [ ] Phase 5 — Secure Boot Threat Model (extends ADR-0014)
- [ ] Phase 6 — AI Threat Model (extends NPS-015)
- [ ] Phase 7 — Package Trust Model (extends NPS-006)

## Revision History

| Version | Date       | Change            |
|---------|------------|--------------------|
| 1.0.0   | 2026-07-12 | Initial roadmap at bootstrap |
| 1.1.0   | 2026-07-13 | Add M9 (Architecture Group Review) and M10 (Backlog Closure Pass) sections |
| 1.2.0   | 2026-07-13 | Add M11 (Response to External Repository Review): Requirements Database delivered, NPS renumbering rejected, remaining 10 gap categories logged in priority order |
| 1.3.0   | 2026-07-13 | Add M12 (Threat Model, phased): Phase 1 (methodology + attack surface) complete, Phases 2–7 planned and sequenced |

---
**End of Document**
