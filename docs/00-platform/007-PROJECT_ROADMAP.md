---
title: Project Roadmap
document_id: NPC-007
version: 1.0.0
status: Draft
classification: Informative
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: Quarterly
depends_on: [NTM-000, NPC-003]
---

# NPC-007 — Project Roadmap

This document is **informative**. It reflects current planning intent and
does not create binding requirements; the milestone definitions in
NPC-003 §7 are the source of truth for what each milestone contains.

## Current Milestone

**M8 — AI Subsystem** complete. All M1–M8 milestones now have draft-level
content. A cross-cutting NyHAL pluggable-backend layer (ADR-0012, NPS-017)
has also been added on top of the original roadmap scope — see the new
"Cross-Cutting" section below.

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
- [ ] Architecture Group review of ADR-0012/NPS-017 — flagged cross-cutting per NPC-001 §3.1, touches every backend-facing contract since M3
- [ ] Begin Linux Backend implementation work (NPS-017 §6)
- [ ] Decide NyFS's Linux Backend implementation strategy (NPS-017 §8)

## Revision History

| Version | Date       | Change            |
|---------|------------|--------------------|
| 1.0.0   | 2026-07-12 | Initial roadmap at bootstrap |

---
**End of Document**
