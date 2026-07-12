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
review_cycle: Quarterly
depends_on: [NTM-000, NPC-003]
---

# NPC-007 — Project Roadmap

This document is **informative**. It reflects current planning intent and
does not create binding requirements; the milestone definitions in
NPC-003 §7 are the source of truth for what each milestone contains.

## Current Milestone

**M4 — Storage** (in progress). M1 and M3 complete.

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
- [ ] Move NPC-001/002/003 from Draft to Accepted after Architecture Group review
- [ ] Establish subsystem ownership list

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
- [ ] Windows compatibility subsystem NPS (per ADR-0005)
- [ ] Android runtime subsystem NPS
- [ ] Adaptive UI shell NPS (desktop / touch / console / handheld modes)

### M6 — Security
- [ ] Capability registry population (per ADR-0004)
- [ ] Container runtime NPS

### M7 — Gaming Subsystem
- [ ] `.nygi` image format NPS (per ADR-0003)
- [ ] Controller, GPU (VRR/HDR/ray tracing/upscaling), emulator hub NPS

### M8 — AI Subsystem
- [ ] Local AI assistant boundary NPS (per NTM-000 §9)
- [ ] Optional cloud sync NPS (opt-in, per NPC-001 §10.2)

## Revision History

| Version | Date       | Change            |
|---------|------------|--------------------|
| 1.0.0   | 2026-07-12 | Initial roadmap at bootstrap |

---
**End of Document**
