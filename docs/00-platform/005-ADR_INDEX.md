---
title: ADR Index
document_id: NPC-005
version: 1.11.0
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
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Accepted | 2026-07-13 | — |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Accepted | 2026-07-13 | — |
| ADR-0004 | Containerized execution model for all application classes | Accepted | 2026-07-13 | — |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Accepted | 2026-07-13 | — |
| ADR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Accepted | 2026-07-13 | — |
| ADR-0007 | Adopt Zstandard as the default compression codec | Proposed | 2026-07-12 | — |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Accepted | 2026-07-13 | — |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Proposed | 2026-07-12 | — |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Accepted | 2026-07-13 | — |
| ADR-0011 | AI assistant runs as an ordinary capability-scoped container | Accepted | 2026-07-13 | — |
| ADR-0012 | Adopt NyHAL as a pluggable kernel abstraction layer | Accepted | 2026-07-13 | — |
| ADR-0013 | Adopt an EEVDF-derived scheduler with a real-time priority class | Proposed | 2026-07-13 | — |
| ADR-0014 | Adopt UEFI Secure Boot with user-enrollable keys | Proposed | 2026-07-13 | — |
| ADR-0015 | Shared dynamic binary translation approach for ARM/x86 compatibility | Proposed | 2026-07-13 | — |
| ADR-0016 | NyFS Linux Backend implemented as a user-space FUSE filesystem | Proposed | 2026-07-13 | — |
| ADR-0017 | Reject domain-grouped NPS renumbering | **Rejected** | 2026-07-13 | — |
| ADR-0018 | Hash-chained append-only log for capability audit records | Proposed | 2026-07-13 | — |

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
| 1.6.0   | 2026-07-12 | Add ADR-0011 (AI assistant containerization) |
| 1.7.0   | 2026-07-12 | Add ADR-0012 (NyHAL pluggable kernel backend) |
| 1.8.0   | 2026-07-13 | Milestone 9 review: accept ADR-0002/0003/0004/0005/0006/0008/0010/0011/0012; add ADR-0013 (scheduler algorithm, Proposed — tuning-blocked). ADR-0007 and ADR-0009 remain Proposed pending benchmark data. |
| 1.9.0   | 2026-07-13 | Add ADR-0014 (secure boot), ADR-0015 (shared ARM translation), ADR-0016 (NyFS Linux Backend FUSE strategy) — closing three of the backlog's open architecture-decision items |
| 1.10.0  | 2026-07-13 | Add ADR-0017 — the project's first Rejected ADR, declining a proposed NPS domain-renumbering scheme |
| 1.11.0  | 2026-07-13 | Add ADR-0018 (hash-chained audit log), from threat model Phase 3 |

---
**End of Document**
