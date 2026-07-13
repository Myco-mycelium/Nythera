---
title: Container Runtime
document_id: NPS-010
version: 1.0.1
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0006, ADR-0009, NPS-002, NPS-003]
---

# NPS-010 — Container Runtime

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
formalizes the container primitive that ADR-0004 established conceptually
and that NPS-002 through NPS-009 have all assumed exists: creation,
capability assignment, resource limits, and teardown.

## 2. Scope

This specification covers the container lifecycle, how capabilities
(defined per-class in NPS-011) are assigned to a container at creation, and
how resource limits — including the IPC rate limiting decided in ADR-0009 —
are attached to and enforced against a container. It does not define
individual capability classes (NPS-011) or the scheduler itself (NPS-001
§3, NPS-002 §6).

## 3. Definitions

- **Container** — as defined in ADR-0004 and NPS-002 §3: the
  capability-scoped execution boundary hosting one or more processes.
- **Container Manifest** — the declarative description of a container's
  requested capability set and resource limits, evaluated at creation time.
- **Grant** — the kernel's act of attaching a specific capability instance
  to a container, recorded so it can be audited and revoked.

## 4. Container Lifecycle

Containers **MUST** move through the following states:

```
REQUESTED → EVALUATING → ACTIVE → SUSPENDED ⇄ ACTIVE → TERMINATING → TERMINATED
```

4.1. **REQUESTED** — a container manifest is submitted (by the package
installer, the service manager at boot per NPS-001 §5, or an
already-running privileged container spawning a helper).

4.2. **EVALUATING** — the requested capability set is checked against what
the requester is itself permitted to grant (NPS-002 §7.1: a process cannot
grant what its own container doesn't hold) and against the capability
registry (NPS-011) for validity. A manifest requesting an undefined
capability **MUST** be rejected, per NPC-001 §9.3.

4.3. **ACTIVE** — the container exists with at least one process (NPS-002
§5); its granted capabilities are enforceable by the kernel (NPS-003 §5.4).

4.4. **SUSPENDED** — all processes within the container are suspended
without termination, supporting the Gaming Mode / Handheld Mode
suspend/resume behavior described in NPS-009 §5.2 and §5.5. Granted
capabilities **MUST** be retained across suspension, not re-evaluated.

4.5. **TERMINATING** — teardown has begun; all processes move toward
NPS-002 §5.5; all capability grants begin revocation (§6).

4.6. **TERMINATED** — all processes terminated (NPS-002 §5.6), all
capability grants revoked, resource-limit bookkeeping (§7) released.

## 5. Capability Assignment

5.1. A container's capability set **MUST** be fixed at the end of the
EVALUATING state for its initial grant; capabilities **MUST NOT** be added
after ACTIVE except through the explicit, auditable capability-registry
request path referenced in NPC-001 §9.3 and NPS-002 §7.3 — never silently.

5.2. A container **MAY** voluntarily narrow its own capability set at any
time (NPS-002 §7.3); narrowing **MUST** be irreversible for the lifetime of
that container instance — a container cannot re-grant itself a capability
it dropped.

5.3. The Windows-compat and Android-compat runtimes (NPS-007, NPS-008)
**MUST** submit container manifests through this same evaluation path;
runtime class **MUST NOT** bypass §4.2 evaluation.

## 6. Revocation

6.1. Revoking a capability from an active container **MUST** take effect
for all future operations immediately (consistent with NPS-003 §4.3's
endpoint revocation model) but **MUST NOT** retroactively invalidate
already-completed operations.

6.2. A user-initiated permission change (e.g. revoking camera access
through system settings) **MUST** result in capability revocation on the
affected container within a bounded, defined time window — not merely on
the container's next restart.

## 7. Resource Limits

7.1. Every container **MUST** have an IPC rate limit assigned at creation,
per the token-bucket mechanism decided in ADR-0009. A manifest **MAY**
request non-default bucket parameters, but any increase above the platform
default **MUST** be justified by a specific capability grant that legitimately
requires it (e.g. a bulk-transfer-heavy capability), evaluated in §4.2.

7.2. Containers **SHOULD** also be assignable CPU-time and memory limits,
enforced by the scheduler and memory manager (NPS-001 §3), to prevent a
single container from starving others — this extends the same "container
as resource boundary" principle already applied to IPC in ADR-0009.

7.3. Resource-limit values **MUST NOT** be treated as security boundaries
on their own; they are a reliability/fairness mechanism (NTM-000 §4,
"Reliability") layered on top of, not a substitute for, capability-based
access control.

## 8. Auditability

8.1. Every grant and revocation **MUST** be recorded in a form a user can
inspect, per NTM-000 §4 ("Transparency") and NPC-001 §9.1's requirement
that permission sets be user-visible.

8.2. The audit record **SHOULD** be queryable per-container ("what can this
app do right now") and per-capability ("what currently holds camera
access"), since both views are needed for different user and
administrator questions.

## 9. Open Questions *(Informative)*

- **Status note (Milestone 9 review):** §7.1 of this document normatively
  requires the ADR-0009 token-bucket mechanism, which remains `Proposed`
  pending its own benchmark data. The container lifecycle, capability
  assignment, and revocation sections (§4–§6, §8) are not themselves
  blocked, but this document is kept `Draft` as a whole rather than
  partially accepted, consistent with NPC-001 §5's rule that acceptance
  applies to a document, not a subset of its sections. Expected to move to
  `Accepted` alongside ADR-0009.
- Exact default CPU/memory limit values (§7.2) require benchmarking across
  representative workloads and are deferred pending that data, per NPC-002
  §5.2.
- Whether SUSPENDED containers should count against active resource
  budgets or a separate reduced accounting is undecided.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.0.1   | 2026-07-13 | Clarify Draft status is a transitive dependency on ADR-0009 §7.1, not an issue in this document's own content (Milestone 9 review) |

---
**End of Document**
