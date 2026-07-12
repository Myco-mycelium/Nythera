---
title: Subsystem Owners
document_id: NPC-008
version: 1.0.0
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

# NPC-008 — Subsystem Owners

This document is the canonical ownership list required by NPC-001 §3.2. It
**MUST** be updated whenever a subsystem is added (a new `subsystem` value
appears in an NPS document's front-matter) or an owner changes.

An owner is the individual or group accountable for a subsystem's
specifications and reference implementation — the person(s) subsystem-level
reviews (NPC-003 §8.1) are routed to.

## Ownership Table

| Subsystem | Scope | Owner | Status |
|-----------|-------|-------|--------|
| `core-architecture` | Kernel, boot, process/thread model, IPC (NPS-001..003) | *Unassigned* | Needs owner |
| `storage` | NyFS, compression policy, game/application image format (NPS-004..006) | *Unassigned* | Needs owner |
| `runtime` | Windows compatibility layer, Android runtime, adaptive UI shell | *Unassigned* | Needs owner (Milestone M5) |
| `security` | Container runtime, capability registry | *Unassigned* | Needs owner (Milestone M6) |
| `gaming` | `.nygi` tooling, controller/GPU support, emulator hub | *Unassigned* | Needs owner (Milestone M7) |
| `ai` | Local AI assistant boundaries, optional cloud sync | *Unassigned* | Needs owner (Milestone M8) |
| `documentation` | Diátaxis structure, MkDocs site, governance docs (NPC series) | *Unassigned* | Needs owner |

## Process for Assigning an Owner

1. A contributor volunteers, or the Architecture Group assigns, an owner for
   an `Unassigned` subsystem.
2. Update the `Owner` column and set `Status` to `Assigned`.
3. Update this document's `updated` field and add a revision history entry.
4. No Architecture Group vote is required for an assignment into a
   currently-`Unassigned` slot; changing an *existing* owner **SHOULD** be
   discussed with the outgoing owner first.

## Notes *(Informative)*

Every subsystem is currently unassigned because the project has a single
active contributor plus AI assistance (per NPC-002) at time of writing. This
table exists now, ahead of team growth, so that onboarding a new contributor
is "claim a row" rather than "invent a governance process."

## Revision History

| Version | Date       | Change        |
|---------|------------|----------------|
| 1.0.0   | 2026-07-12 | Initial ownership table created as part of Milestone 2 (NPC-001 §3.2 compliance) |

---
**End of Document**
