---
title: Optional Cloud Synchronization
document_id: NPS-016
version: 1.0.1
status: Accepted
classification: Normative
subsystem: ai
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPS-006, NPS-010, NPS-011]
---

# NPS-016 — Optional Cloud Synchronization

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements NPC-001 §10.2 ("Cloud synchronization features... MUST be
opt-in and MUST NOT be required for core functionality") and the Cloud
Features goal from the original design discussion.

Despite being grouped under the AI subsystem in the project roadmap for
scheduling purposes, cloud sync has no dependency on the AI assistant
(NPS-015) and **MUST** function independently of it.

## 2. Scope

This specification covers what data classes may be synchronized, the
opt-in mechanism, conflict handling, and offline-first guarantees. It does
not cover the sync transport protocol or backend implementation, which are
deferred to a future ABI/API specification once a concrete backend is
chosen.

## 3. Syncable Data Classes

Per the original design discussion, the following data classes **MAY** be
offered for synchronization, each independently toggleable:

| Class | Description |
|-------|--------------|
| Save games | The writable overlay data defined in NPS-006 §4 |
| Installed applications | The list of installed `.nygi` images (not the image content itself, which SHOULD be re-fetched from its original source on a new device) |
| Settings | User preferences, including adaptive UI shell mode overrides (NPS-009 §4.2) |
| Themes | Visual customization data |
| Drivers | Device driver configuration, where portable across hardware |

## 4. Opt-In Requirement

4.1. No data class in §3 **MAY** be synchronized without the user
explicitly enabling it, per `CAP-CLOUD-SYNC` (NPS-011) being denied by
default.

4.2. Enabling sync for one data class **MUST NOT** implicitly enable sync
for any other data class; each is an independent, auditable grant (NPS-010
§8), consistent with NPC-001 §10.1's requirement that data remain under
user control.

4.3. The system **MUST** remain fully functional with all sync disabled,
per NPC-001 §10.2 and NTM-000's "The OS would remain fully functional
offline" goal.

## 5. Data Ownership and Portability

5.1. Synchronized data **MUST** remain exportable in an open format
without requiring the cloud connection to be active, per NPC-001 §10.1 —
sync is a convenience path, not the canonical storage location of the
user's data. The canonical copy is always the local NyFS-backed data
(NPS-004, NPS-006).

5.2. A user **MUST** be able to delete their synchronized data from the
cloud endpoint independently of deleting local data, and vice versa.

## 6. Conflict Handling

6.1. When the same data class has diverged between two devices (e.g. save
games modified offline on both), the system **MUST** surface the conflict
to the user rather than silently choosing one version, since silently
discarding save progress would violate NPC-001 §10 (User Ownership).

6.2. Automatic conflict resolution **MAY** be offered as a default
suggestion (e.g. "most recently modified"), but **MUST** remain
overridable by the user per §6.1.

## 7. Security

7.1. Data in transit to a sync endpoint **MUST** be encrypted.

7.2. The sync container **MUST** hold only `CAP-CLOUD-SYNC` and the
specific read capabilities needed for the data classes the user has
enabled (e.g. `CAP-FILESYSTEM-NYFS` scoped to overlay data for save sync),
per NPS-010 §4.2 — it **MUST NOT** receive broad system access to perform
its function, consistent with the pattern established across every
subsystem since ADR-0004.

## 8. Third-Party Sync Backends

8.1. Nythera **SHOULD NOT** hard-depend on a single, Nythera-operated
cloud service for sync to function, consistent with NTM-000 §5's
rejection of vendor lock-in; the sync mechanism **SHOULD** be
backend-agnostic where practical (e.g. user-configurable endpoint), though
a default first-party option **MAY** be offered.

## 9. Open Questions *(Informative)*

- The exact transport protocol and backend architecture are deferred to a
  future specification once a concrete implementation path is chosen.
- Whether driver configuration sync (§3) is portable across meaningfully
  different hardware, or should be scoped to same-device-class sync only,
  is undecided.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.0.1   | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
