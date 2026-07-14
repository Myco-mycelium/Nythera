---
title: Requirements Database
document_id: NPC-009
version: 1.0.0
status: Draft
classification: Normative
owners:
  - Nythera Architecture
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPC-003]
---

# NPC-009 — Requirements Database

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines the requirement identifier scheme, required fields, and
traceability rules for `docs/reference/requirements/`. The requirements
themselves live in `REQUIREMENTS.md` in that directory; this document
governs the *scheme*, not the content.

## 2. Purpose *(Informative)*

Every NPS document to date states requirements in prose, using RFC 2119
language ("the runtime MUST..."). That's sufficient for a human reading
one document, but it doesn't let anyone answer, at a glance: which
requirement does this test verify? Which requirements does this code
satisfy? Which requirements are still purely aspirational versus actually
implemented? A requirements database answers those questions by giving
every individually-testable requirement a stable ID that specifications,
code, and tests can all point at.

## 3. Identifier Scheme

3.1. Every requirement **MUST** have an ID of the form `REQ-<DOMAIN>-<NNNN>`,
where `<DOMAIN>` is one of the registered domain prefixes (§4) and
`<NNNN>` is a zero-padded four-digit sequence number, unique within that
domain.

3.2. Requirement IDs **MUST NOT** be reused or renumbered once assigned,
for the same reason document IDs aren't renumbered (see ADR-0017) — a test
or a piece of code may already cite it.

3.3. A requirement that is superseded or found incorrect **MUST** be
marked `Superseded` or `Rejected` (§6) rather than deleted or renumbered;
the ID stays reserved and historical.

## 4. Domain Prefixes

Domain prefixes **MUST** map to an existing NPS `subsystem` value or a
clearly-scoped cross-cutting concern. The initial set:

| Prefix | Scope | Maps to subsystem(s) |
|--------|-------|------------------------|
| `BOOT` | Boot sequence, firmware handoff | core-architecture (NPS-001) |
| `KERNEL` | Process/thread model, scheduler | core-architecture (NPS-002) |
| `IPC` | Inter-process communication | core-architecture (NPS-003) |
| `STORAGE` | NyFS core guarantees | storage (NPS-004) |
| `COMPRESS` | Compression policy | storage (NPS-005) |
| `IMAGE` | Game/application image format | storage (NPS-006) |
| `WINCOMPAT` | Windows compatibility runtime | runtime (NPS-007) |
| `ANDROIDCOMPAT` | Android compatibility runtime | runtime (NPS-008) |
| `UI` | Adaptive UI shell | runtime (NPS-009) |
| `SEC` | Container runtime, general security | security (NPS-010) |
| `CAP` | Capability registry | security (NPS-011) |
| `INPUT` | Controller/input subsystem | gaming (NPS-012) |
| `GPU` | GPU feature support | gaming (NPS-013) |
| `EMU` | Emulator hub | gaming (NPS-014) |
| `AI` | Local AI assistant | ai (NPS-015) |
| `SYNC` | Cloud synchronization | ai (NPS-016) |
| `NYHAL` | Backend abstraction contract | core-architecture (NPS-017) |

New prefixes **MAY** be added via the normal change process (NPC-001 §6)
as new subsystems are introduced.

## 5. Required Fields

Every requirement entry **MUST** record:

| Field | Meaning |
|-------|---------|
| ID | `REQ-<DOMAIN>-<NNNN>` |
| Statement | A single, testable SHALL/MUST statement — one requirement per ID, not a bundle |
| Source | The specification section it's traced from (e.g. `NPS-001 §6.1`) |
| Status | `Draft`, `Verified`, `Implemented`, `Tested`, `Superseded`, or `Rejected` (§6) |
| Verified By | Test ID(s) once one exists, else empty |
| Implemented By | Source file/module path once one exists, else empty |

## 6. Status Lifecycle

```
Draft → Verified → Implemented → Tested
              ↘ Superseded / Rejected (from any state)
```

- **Draft** — extracted from a specification, not yet cross-checked
  against the specification's exact wording by a second pass.
- **Verified** — confirmed to accurately restate its source specification's
  normative language (no drift in meaning).
- **Implemented** — code exists that is intended to satisfy it (the
  "Implemented By" field is populated). Implementation existing does
  **NOT** imply correctness — that's `Tested`.
- **Tested** — a test (`Verified By`) exists and passes, exercising this
  specific requirement.
- **Superseded** — replaced by a newer requirement (which **MUST** be
  cross-referenced).
- **Rejected** — considered and explicitly not adopted; kept for record,
  mirroring ADR status semantics (NPC-001 §5).

## 7. Traceability Rules

7.1. A requirement's **Statement MUST** be traceable to specific
normative language (a MUST/SHALL sentence) in its **Source** document —
it MUST NOT introduce a new obligation the source specification doesn't
already state. The requirements database restates and IDs existing
obligations; it does not create new ones. New obligations belong in the
specification first (NPC-001 §6), then get a requirement ID.

7.2. Going forward, **new** normative content added to any NPS or NPC
document **SHOULD** cite the requirement ID it corresponds to (creating
one first if needed), so that specification and requirement stay in sync
from the moment of authorship rather than needing a later extraction pass.

7.3. Retroactively adding requirement IDs to the full existing body of
NPS-001 through NPS-017 is **NOT** required by this document and is
explicitly out of scope for its initial version — `REQUIREMENTS.md` seeds
a representative set per domain (§4) rather than claiming full coverage.
Full retroactive extraction, if undertaken, **MUST** be tracked as its own
change (NPC-001 §6), not silently assumed complete.

7.4. Test suites (NPC-003 §5.3), once they exist, **SHOULD** reference the
requirement ID(s) they exercise in test names or metadata, populating
`REQUIREMENTS.md`'s `Verified By` field.

## 8. Non-Goals *(Informative)*

This is not a project-management ticket tracker, and requirement status
is not a proxy for implementation priority or scheduling. A `Draft`
requirement in a Milestone 8 subsystem isn't "more overdue" than a
`Draft` requirement in Milestone 3 — status here tracks specification
fidelity and implementation/test existence, nothing else.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft, in response to external review feedback |

---
**End of Document**
