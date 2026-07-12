---
title: Specification Index
document_id: NPC-004
version: 1.1.0
status: Draft
classification: Reference
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
review_cycle: Continuous
depends_on: [NPC-001]
---

# NPC-004 — Specification Index

This document is the master index of every canonical document in the
Nythera repository. It **MUST** be updated in the same commit that adds,
accepts, deprecates, or rejects any normative document (NPC-001 §6.5).

## 00 — Platform (Foundational)

| ID | Title | Status | Version |
|----|-------|--------|---------|
| NTM-000 | The Nythera Manifest | Accepted | 1.0.0 |
| NPC-001 | Project Constitution | Draft | 1.0.0 |
| NPC-002 | AI Collaboration Protocol | Draft | 1.0.0 |
| NPC-003 | Engineering Handbook | Draft | 1.0.0 |
| NPC-004 | Specification Index (this document) | Draft | 1.0.0 |
| NPC-005 | ADR Index | Draft | 1.0.0 |
| NPC-006 | Glossary | Draft | 1.0.0 |
| NPC-007 | Project Roadmap | Draft | 1.0.0 |

## Architecture Decision Records

| ID | Title | Status |
|----|-------|--------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Proposed |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Proposed |
| ADR-0004 | Containerized execution model for all application classes | Proposed |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Proposed |
| ADR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Proposed |

See `docs/reference/adr/` for full records and NPC-005 for the governing
index.

## Nythera Proposals for Specification (NPS)

| ID | Title | Subsystem | Status |
|----|-------|-----------|--------|
| NPS-001 | Kernel Architecture and Boot | core-architecture | Draft |
| NPS-002 | Process and Thread Model | core-architecture | Draft |
| NPS-003 | Inter-Process Communication and Capability Passing | core-architecture | Draft |

Storage, runtime, security, and gaming specifications remain to be drafted
(Milestones M4–M8, see NPC-003 §7).

## ABI / API References

| ID | Title | Status |
|----|-------|--------|
| — | — | Not started |

## Package Format

| ID | Title | Status |
|----|-------|--------|
| — | Nythera Package Format (.nypkg) | Not started |

---

## Revision History

| Version | Date       | Change                          |
|---------|------------|-----------------------------------|
| 1.0.0   | 2026-07-12 | Initial index at repository bootstrap |
| 1.1.0   | 2026-07-12 | Add ADR-0006 and NPS-001..003 (Core Architecture, M3) |

---
**End of Document**
