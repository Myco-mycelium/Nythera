---
title: ADR Index
document_id: NPC-005
version: 1.5.0
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

# NPC-005 — Architecture Decision Record Index

Architecture Decision Records (ADRs) capture a specific decision, the
alternatives considered, and the reasoning — per NPC-001 §6. Full ADR text
lives under `docs/reference/adr/ADR-XXXX-slug.md`. This index tracks status
only.

| ID | Title | Status | Date | Supersedes |
|----|-------|--------|------|------------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted | 2026-07-12 | — |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Proposed | 2026-07-12 | — |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Proposed | 2026-07-12 | — |
| ADR-0004 | Containerized execution model for all application classes | Proposed | 2026-07-12 | — |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Proposed | 2026-07-12 | — |
| ADR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Proposed | 2026-07-12 | — |
| ADR-0007 | Adopt Zstandard as the default compression codec | Proposed | 2026-07-12 | — |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Proposed | 2026-07-12 | — |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Proposed | 2026-07-12 | — |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Proposed | 2026-07-12 | — |

## ADR Statuses

- **Proposed** — open for discussion, not yet binding.
- **Accepted** — binding; implementation MUST conform.
- **Superseded** — replaced by a later ADR (see `Supersedes` column).
- **Rejected** — considered and declined; kept for historical record.

## Revision History

| Version | Date       | Change              |
|---------|------------|----------------------|
| 1.0.0   | 2026-07-12 | Initial index        |
| 1.1.0   | 2026-07-12 | Add ADR-0006 (kernel base selection) |
| 1.2.0   | 2026-07-12 | Add ADR-0007 (compression codec selection) |
| 1.3.0   | 2026-07-12 | Add ADR-0008 (Android runtime approach) |
| 1.4.0   | 2026-07-12 | Add ADR-0009 (IPC rate limiting) |
| 1.5.0   | 2026-07-12 | Add ADR-0010 (Vulkan graphics foundation) |

---
**End of Document**
