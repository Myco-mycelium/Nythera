---
title: Process and Thread Model
document_id: NPS-002
version: 1.0.0
status: Draft
classification: Normative
subsystem: core-architecture
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0006, NPS-001]
---

# NPS-002 — Process and Thread Model

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines how processes, threads, and containers relate to one another in
Nythera, and how this maps onto the containerized execution model of
ADR-0004.

## 2. Scope

This specification covers process and thread lifecycle, the relationship
between a **container** (ADR-0004) and one or more processes, and the
capability inheritance rules at process creation. It does not cover IPC
message contents (NPS-003) or specific capability classes (deferred to the
Milestone M6 capability-registry work).

## 3. Definitions

- **Process** — an isolated address space with one or more threads,
  scheduled by the kernel (NPS-001 §3).
- **Thread** — a unit of scheduling within a process, sharing its address
  space.
- **Container** — the capability-scoped execution boundary defined in
  ADR-0004. A container **MUST** consist of one or more processes and
  **MUST** have an explicit, enumerable capability set.
- **Runtime Class** — the origin of a process: `native`, `windows-compat`,
  or `android-compat` (per ADR-0005).

## 4. Container-to-Process Relationship

4.1. Every process **MUST** belong to exactly one container at creation
time. A process **MUST NOT** exist outside a container, including
system-service processes, per NTM-000 §4 ("Security is created through
architecture").

4.2. A container **MAY** host multiple processes (e.g. a Windows-compat
container hosting the translated application plus its helper processes),
provided all processes in the container share the same capability set.

4.3. A process **MUST NOT** hold a capability its container does not hold.
Capability sets **MUST** be enforced at the kernel boundary (NPS-001 §3,
Capability/IPC Primitives), not merely by user-space convention.

## 5. Process Lifecycle

Processes **MUST** move through the following states:

```
CREATED → READY → RUNNING → (BLOCKED ⇄ RUNNING) → TERMINATING → TERMINATED
```

5.1. **CREATED** — address space allocated, capability set assigned from
the parent container; no threads yet scheduled.

5.2. **READY** — at least one thread is schedulable.

5.3. **RUNNING** — at least one thread is executing on a core.

5.4. **BLOCKED** — all threads are waiting (I/O, IPC receive, timer); the
process **MUST** yield the core, per NTM-000 "Performance."

5.5. **TERMINATING** — the process (or its container) has requested exit,
or has been terminated by the scheduler/service manager; all held
capabilities **MUST** begin revocation.

5.6. **TERMINATED** — resources reclaimed; process ID **MAY** be reused
only after a grace period sufficient to avoid stale-reference bugs in
IPC (see NPS-003).

## 6. Thread Model

6.1. Threads within a process **MUST** share the process's address space
and capability set; Nythera **MUST NOT** support per-thread capability
subsets in v1 — this **MAY** be revisited in a future MAJOR revision if a
concrete need is demonstrated.

6.2. The scheduler (NPS-001 §3) **MUST** support at minimum: a default
time-sharing class for general applications, and a real-time-priority class
for latency-sensitive workloads (audio, input, and gaming frame pacing),
consistent with NTM-000's gaming-as-first-class-capability goal.

6.3. Thread creation and destruction **MUST NOT** require kernel-space
filesystem or driver access, per the user-space isolation defined in
NPS-001 §4.

## 7. Capability Inheritance at Creation

7.1. A newly created process **MUST** receive a capability set that is a
subset of its container's capability set; it **MUST NOT** receive
capabilities broader than its container.

7.2. Compatibility-runtime processes (`windows-compat`, `android-compat`)
**MUST** follow the same inheritance rule as `native` processes — runtime
class **MUST NOT** itself grant additional implicit capabilities, per
ADR-0004.

7.3. A process **MAY** voluntarily narrow its own capability set (e.g. drop
network access after startup) but **MUST NOT** be able to widen it without
an explicit, auditable request through the capability registry (Milestone
M6, per NPC-001 §9.3).

## 8. Failure and Crash Isolation

8.1. A crashing process **MUST NOT** affect other containers, consistent
with the hybrid microkernel boundary in ADR-0006.

8.2. A crashing process **MAY** affect other processes within the same
container, since they share a capability set and are considered part of
the same trust boundary (§4.2).

8.3. The service manager (introduced during boot, NPS-001 §5 Stage 5)
**SHOULD** support automatic restart policies for system-service
containers, and **MUST** surface application-container crashes to the user
per NTM-000 "Transparency."

## 9. Open Questions *(Informative)*

- Exact real-time scheduling guarantees for gaming workloads (§6.2) require
  benchmark-backed numbers before this section can move past Draft, per
  NPC-002 §5.2.
- Process migration/suspend-to-disk semantics for the "Gaming Mode"
  suspend/resume feature described in NTM-000 are deferred to a future NPS.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

---
**End of Document**
