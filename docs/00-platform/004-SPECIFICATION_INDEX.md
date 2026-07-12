---
title: Specification Index
document_id: NPC-004
version: 1.6.0
status: Draft
classification: Reference
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
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
| NPC-001 | Project Constitution | Accepted | 1.0.1 |
| NPC-002 | AI Collaboration Protocol | Accepted | 1.0.1 |
| NPC-003 | Engineering Handbook | Accepted | 1.0.1 |
| NPC-004 | Specification Index (this document) | Draft | 1.3.0 |
| NPC-005 | ADR Index | Draft | 1.2.0 |
| NPC-006 | Glossary | Draft | 1.0.0 |
| NPC-007 | Project Roadmap | Draft | 1.0.0 |
| NPC-008 | Subsystem Owners | Draft | 1.0.0 |

## Architecture Decision Records

| ID | Title | Status |
|----|-------|--------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Proposed |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Proposed |
| ADR-0004 | Containerized execution model for all application classes | Proposed |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Proposed |
| ADR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Proposed |
| ADR-0007 | Adopt Zstandard as the default compression codec | Proposed |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Proposed |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Proposed |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Proposed |

See `docs/reference/adr/` for full records and NPC-005 for the governing
index.

## Nythera Proposals for Specification (NPS)

| ID | Title | Subsystem | Status |
|----|-------|-----------|--------|
| NPS-001 | Kernel Architecture and Boot | core-architecture | Draft |
| NPS-002 | Process and Thread Model | core-architecture | Draft |
| NPS-003 | Inter-Process Communication and Capability Passing | core-architecture | Draft |
| NPS-004 | NyFS Filesystem Core | storage | Draft |
| NPS-005 | Transparent Compression Policy | storage | Draft |
| NPS-006 | Nythera Game/Application Image Format (.nygi) and Overlay | storage | Draft |
| NPS-007 | Windows Compatibility Runtime | runtime | Draft |
| NPS-008 | Android Compatibility Runtime | runtime | Draft |
| NPS-009 | Adaptive UI Shell | runtime | Draft |
| NPS-010 | Container Runtime | security | Draft |
| NPS-011 | Capability Registry | security | Draft |
| NPS-012 | Controller and Input Subsystem | gaming | Draft |
| NPS-013 | GPU Feature Support | gaming | Draft |
| NPS-014 | Emulator Hub | gaming | Draft |

The AI Subsystem specification set remains to be drafted (Milestone M8, see
NPC-003 §7).

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
| 1.2.0   | 2026-07-12 | Add ADR-0007 and NPS-004..006 (Storage, M4) |
| 1.3.0   | 2026-07-12 | Accept NPC-001/002/003 (Draft → Accepted); add NPC-008 Subsystem Owners (Milestone 2) |
| 1.4.0   | 2026-07-12 | Add ADR-0008 and NPS-007..009 (Runtime, M5) |
| 1.5.0   | 2026-07-12 | Add ADR-0009 and NPS-010..011 (Security, M6) |
| 1.6.0   | 2026-07-12 | Add ADR-0010 and NPS-012..014 (Gaming Subsystem, M7) |

---
**End of Document**
