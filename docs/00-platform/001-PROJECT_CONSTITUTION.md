---
title: Project Constitution
document_id: NPC-001
version: 1.1.0
status: Accepted
classification: Normative
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: Annual
depends_on: [NTM-000]
---

# NPC-001 — Project Constitution

## 1. Status of This Document

This document is **normative**. The key words **MUST**, **MUST NOT**,
**SHOULD**, **SHOULD NOT**, and **MAY** are to be interpreted as described in
RFC 2119. Sections marked *(Informative)* are explanatory and impose no
requirements.

This Constitution translates the principles of NTM-000 (The Nythera Manifest)
into enforceable engineering rules. Where this document and the Manifest
appear to conflict, the Manifest's philosophy governs and this document
**MUST** be revised.

---

## 2. Purpose *(Informative)*

The Constitution exists so that a project with many contributors, spanning
many years, produces a coherent system rather than a loose federation of
components. It defines how decisions are made, what every specification must
contain, and what guarantees the platform makes to its users and developers.

---

## 3. Governing Bodies

### 3.1 Architecture Group
The Architecture Group **MUST** approve any Nythera Platform Change (NPC) or
Architecture Decision Record (ADR) that alters a cross-cutting subsystem
(kernel, filesystem, security model, package format, UI shell).

### 3.2 Subsystem Owners
Each subsystem enumerated in `docs/00-platform/SUBSYSTEM_OWNERS.md` **MUST**
have at least one named owner responsible for its specifications and
reference implementation. `SUBSYSTEM_OWNERS.md` **MUST** be updated
whenever a new subsystem is introduced (e.g. a new top-level `subsystem`
value used in an NPS front-matter block).

### 3.3 Contributors
Any contributor **MAY** propose a Nythera Proposal for Specification (NPS) or
an ADR. Proposals **MUST** follow the process in Section 6.

---

## 4. Document Classes

| Class | Prefix | Purpose | Normative? |
|-------|--------|---------|------------|
| Manifest | NTM | Timeless philosophy | No |
| Platform Constitution | NPC | Enforceable governance rules | Yes |
| Proposal / Specification | NPS | Technical specifications | Yes |
| Architecture Decision Record | ADR | Records of a specific decision and its rationale | Yes (historical) |
| Application Binary Interface | ABI | Binary compatibility contracts | Yes |
| API Reference | API | Public interfaces | Yes |
| Requirement | REQ | Individually-testable, traceable obligation restating a specification's normative language | Yes |

All normative documents **MUST** include a YAML front-matter block containing
at minimum: `title`, `document_id`, `version`, `status`, `owners`, `created`,
`updated`, and `depends_on`.

---

## 5. Document Lifecycle

Every normative document **MUST** carry one of the following statuses:

- `Draft` — under active discussion, not binding.
- `Review` — feature-complete, undergoing formal review.
- `Accepted` — binding on all new work.
- `Deprecated` — superseded but retained for historical reference.
- `Rejected` — proposed and formally declined; retained for record.

A document **MUST NOT** move from `Draft` to `Accepted` without at least one
Architecture Group review recorded in its revision history.

---

## 6. Change Process

1. A contributor opens an NPS or ADR describing the problem, the proposed
   change, and the alternatives considered.
2. The proposal **MUST** state which Manifest principles it advances and
   confirm it does not violate Section 5 of NTM-000 ("What Nythera Will
   Never Become").
3. Subsystem owners affected by the change **MUST** be tagged for review.
4. The Architecture Group records a decision of `Accepted`, `Rejected`, or
   `Deferred`.
5. Accepted changes **MUST** be reflected in `SPECIFICATION_INDEX.md`,
   `REPOSITORY_STATE.md`, and `CHANGE_REQUEST_LOG.md` in the same change set.

Emergency security fixes **MAY** bypass steps 1–4 but **MUST** produce a
retroactive ADR within 14 days.

---

## 7. Versioning

All normative documents **MUST** use semantic versioning (`MAJOR.MINOR.PATCH`):

- **MAJOR** — breaking change to the contract the document defines.
- **MINOR** — backward-compatible addition.
- **PATCH** — clarification, typo fix, or non-normative correction.

Public interfaces (ABI, API, package format) **MUST NOT** introduce a
breaking change without a corresponding MAJOR version increment and a
migration guide under `docs/how-to/`.

---

## 8. Compatibility Guarantees

8.1. Once an ABI document reaches `Accepted` status, its MAJOR version
**MUST NOT** change without a deprecation period of no less than one platform
release cycle.

8.2. The package format (`docs/reference/package-format/`) **MUST** remain
readable by all future Nythera releases within the same MAJOR version.

8.3. Windows (.exe/.msi) and Android (.apk) compatibility layers **MAY**
evolve independently of the native ABI, provided native application
guarantees in 8.1 and 8.2 are unaffected.

---

## 9. Security Baseline

9.1. All first-party and third-party applications **MUST** execute inside a
container with an explicit, user-visible permission set, per NTM-000 §4
("Security").

9.2. The compatibility subsystems (Windows, Android) **MUST** run within the
same containerization model as native applications; they **MUST NOT** be
granted implicit elevated privileges.

9.3. Any subsystem requesting a new capability class **MUST** document it in
`docs/reference/capability-registry/` before it may be used by any
application.

---

## 10. Data Ownership

10.1. User data **MUST** remain readable and exportable in an open format
without requiring a network connection.

10.2. Cloud synchronization features defined in NTM-000 §"Cloud Features"
**MUST** be opt-in and **MUST NOT** be required for core functionality.

---

## 11. Artificial Intelligence Boundaries

11.1. AI subsystems **MUST NOT** possess authority to alter system
configuration, install software, or change security policy without an
explicit, auditable user action.

11.2. AI subsystems **MUST** be disable-able without loss of core OS
functionality, per NTM-000 §9.

---

## 12. Amendment Process

This Constitution **MAY** be amended by an Architecture Group decision
following the process in Section 6. Amendments **MUST NOT** contradict
NTM-000. If a proposed amendment requires contradicting the Manifest, the
Manifest **MUST** be amended first through a separate, explicitly-labeled
proposal, which **SHOULD** be rare.

---

## Revision History

| Version | Date       | Change                     |
|---------|------------|-----------------------------|
| 1.0.0   | 2026-07-12 | Initial draft for review    |
| 1.0.1   | 2026-07-12 | Clarify §3.2 to reference `SUBSYSTEM_OWNERS.md` as the canonical ownership list |
| 1.0.2   | 2026-07-12 | Architecture Group review completed. Status: Draft → Accepted (Milestone 2). |
| 1.1.0   | 2026-07-13 | Add `REQ` (Requirement) document class to §4, per NPC-009 Requirements Database |

---
**End of Document**
