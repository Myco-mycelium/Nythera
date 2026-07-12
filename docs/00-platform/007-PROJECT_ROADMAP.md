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

**M1 — Repository Bootstrap** (in progress)

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
- [ ] MkDocs Material site configuration and CI build
- [ ] CONTRIBUTING.md and LICENSE

### M2 — Platform Constitution
- [ ] Move NPC-001/002/003 from Draft to Accepted after Architecture Group review
- [ ] Establish subsystem ownership list

### M3 — Core Architecture
- [ ] Kernel base selection ADR
- [ ] Initial NPS set for boot, process model, IPC

### M4 — Storage
- [ ] Finalize ADR-0002 (filesystem) into Accepted NPS specifications
- [ ] Compression codec selection (Zstd/LZ4 defaults)

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
