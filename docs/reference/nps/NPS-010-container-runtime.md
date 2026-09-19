---
title: Container Runtime
document_id: NPS-010
version: 1.6.0
status: Accepted
classification: Normative
subsystem: security
owners:
  - Nyrqis Architecture
created: 2026-07-12
updated: 2026-09-19
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
capability **MUST** be rejected, per NPC-001 §9.3. This validity check and
the grant recorded at the end of this state (§5.1) **MUST** be a single
atomic operation against one consistent read of the capability registry —
not two separate reads — so that a capability deprecated between check
and grant cannot still be issued (per the threat model, `FIND-CAPABILITY-001`,
NPS-021 §5.1).

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

7.1.1. The endpoint's token bucket **MUST** enforce **per-sender
fairness**: a sender's sustained intake **MUST NOT** exceed its share of
the endpoint's envelope — in the Linux backend, `FairTokenBucket`
confines each sender to `tokens_per_second / fair_shares` (plus a
bounded `sender_burst`) under the shared envelope, and endpoints are
fair **by default** (ADR-0009 §32b: a shared-only bucket starved a
legitimate 250 Hz client to ~9 admitted/s under a full-speed flood).
The bucket **MUST** still cap total intake at the shared envelope.
Endpoint limiter parameters (including `fair_shares`/`sender_burst`)
**MAY** be retuned by the operator at runtime through the control
plane.

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
that permission sets be user-visible. This record **MUST** be
tamper-evident, implemented as the hash-chained, append-only log decided
in ADR-0018, so that a compromised container or service with write access
to the log cannot silently falsify its own capability history (per the
threat model, `FIND-CAPABILITY-002`, NPS-021 §5.2).

8.2. The audit record **SHOULD** be queryable per-container ("what can this
app do right now") and per-capability ("what currently holds camera
access"), since both views are needed for different user and
administrator questions.

## 9. Open Questions *(Informative)*

- **Status note (Milestone 9 review; benchmark status updated
  2026-08-12; §7.1.1 fairness adopted 2026-09-10):** §7.1 of this
  document normatively requires the ADR-0009 token-bucket mechanism.
  First-pass benchmark data now exists (`tests/BENCHMARK_RESULTS.md`):
  the default bucket sustains only ~99.5 calls/s on a client→endpoint
  path and throttles ~18.9k calls/s at full speed, so the default
  parameters are demonstrably too low for high-frequency legitimate
  traffic — a finding recorded in ADR-0009, which remains `Proposed`
  pending the parameter sweep and Architecture Group review. The
  §32b adversarial finding (shared-bucket starvation) is closed at the
  mechanism level: §7.1.1 now requires per-sender fairness, implemented
  in the Linux backend as `FairTokenBucket` with fair-by-default
  endpoints. **Status (2026-09-19): ADR-0009 was Accepted by the
  Architecture Group (static `fair_shares` default, dynamic opt-in),
  removing this document's last transitive blocker — NPS-010 is now
  `Accepted` as a whole.**
- Exact default CPU/memory limit values (§7.2) require benchmarking across
  representative workloads and are deferred pending that data, per NPC-002
  §5.2. **Status update 2026-09-18: the deferred data now exists**
  (`tests/BENCHMARK_RESULTS.md` §35, methodology in BENCHMARK_PLAN §7 —
  real cgroup-v2 enforcement): representative workload shapes peak at
  3.2–9.0 MB (the 256 MB default is 28–80× that floor); quota throttling
  is a tail phenomenon (a bursty shape at 2.5× under-provisioned quota
  keeps its exact p50 while p95 grows ~8× — monitor p95/`nr_throttled`,
  not mean usage);  the 64-PID default sits just 1.5× above a modest
  supervisor shape's peak (fork-fail below it is clean). **Status
  (2026-09-19): the default VALUES were adopted by the Architecture
  Group** — keep 256 MB / 64 PIDs / unlimited quota, with the standing
  rules below now normative (§7.2).
- Whether SUSPENDED containers should count against active resource
  budgets or a separate reduced accounting is undecided. **Status
  update 2026-09-18: measured** (§35d): a frozen container consumes 0%
  CPU but retains 100% of its memory, and the kernel can still reclaim
  from a frozen cgroup via the `memory.high` pressure path. The
  accounting model consistent with actual enforcement is therefore:
  **full memory accounting, zero CPU accounting** for SUSPENDED
  containers. **Status (2026-09-19): adopted as normative** by the
  Architecture Group decision recorded in `AG_AGENDA.md` — see §7.2's
  amendment and the table below.

**Adopted defaults (Architecture Group, 2026-09-19; data §35):**

| limit | shipped default | §35 data | proposal |
|---|---|---|---|
| `memory_mb` | 256 | representative shapes peak 3.2–9.0 MB (28–80× headroom at the floor); real Nyrqis app stack unmeasured | **keep 256** — nothing of the measured class is throttled by it; revisit when the real app stack (NyRuntime + compositor + shell) is measured |
| `pid_limit` | 64 | a modest supervisor shape (shell + 40 children) peaks at 41 tasks; fork-fail below is a clean refusal | **keep 64 (NORMATIVE)** for app containers; supervisor-shaped containers MUST raise it explicitly via §7.2's assignability (1.5× headroom is too thin to be silent about) |
| `cpu_quota_us` | None (unlimited) | quota throttling is a TAIL phenomenon: at 2.5× under-provisioning, p50 is unchanged while p95 grows ~8× (bimodal stutter, invisible to mean-usage monitoring) | **keep unlimited by default**; when quotas are assigned, size them ≥ ~2.5× the workload's average demand and monitor p95 latency + `nr_throttled`, never mean usage |
| SUSPENDED accounting | **normative (2026-09-19)** | frozen: 0% CPU, 100% memory retained, kernel-reclaimable via `memory.high` | ADOPTED: SUSPENDED containers count FULLY against memory budgets and NOT AT ALL against CPU budgets; budget checks MUST NOT treat suspension as memory relief |

Operator guidance the data earns (candidate for the ops how-to): the
failure signature of an under-sized quota is a *bimodal* latency
distribution with a clean median — alerts keyed on p50/mean CPU will
not fire; alert on p95 burst-completion latency and
`cpu.stat`'s `nr_throttled` instead.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.0.1   | 2026-07-13 | Clarify Draft status is a transitive dependency on ADR-0009 §7.1, not an issue in this document's own content (Milestone 9 review) |
| 1.1.0   | 2026-07-13 | §4.2: require atomic validity-check-and-grant, closing FIND-CAPABILITY-001. §8.1: require tamper-evident (hash-chained) audit log per new ADR-0018, closing FIND-CAPABILITY-002. Both from threat model Phase 3 (NPS-021). |
| 1.2.0   | 2026-08-12 | §9 status note: record first-pass ADR-0009 benchmark data (tests/BENCHMARK_RESULTS.md); default bucket shown to throttle this workload shape; ADR-0009 remains Proposed |
| 1.3.0   | 2026-09-10 | §7.1.1 (new): normatively require per-sender fairness in the endpoint bucket (ADR-0009 §32b mechanism, implemented as FairTokenBucket with fair-by-default endpoints); §9 status note refreshed |
| 1.4.0   | 2026-09-18 | §9: both open-question deferrals now have data — §35 of tests/BENCHMARK_RESULTS.md (real cgroup-v2 enforcement) covers the default CPU/memory limit question (footprint floor 28–80× under the 256 MB default; quota throttling is tail-shaped; 64-PID default 1.5× above a modest supervisor) and answers the SUSPENDED-accounting question (full memory, zero CPU; frozen cgroups stay kernel-reclaimable). Default values remain an Architecture Group decision |
| 1.5.0   | 2026-09-18 | §9: proposed-defaults table added from the §35 data (keep 256 MB / 64 PIDs / unlimited quota, with the raise-it-explicitly rule for supervisor shapes and the ≥2.5× sizing + p95/`nr_throttled` monitoring rule for assigned quotas); SUSPENDED-accounting proposal made normative-candidate (full memory, zero CPU; suspension is not budget relief) — all pending Architecture Group decision |
| 1.6.0   | 2026-09-19 | **Document → Accepted**: ADR-0009's acceptance removed the last transitive blocker. §9 defaults ADOPTED by the Architecture Group (256 MB / 64 PIDs / unlimited quota + standing rules + SUSPENDED accounting = full memory, zero CPU, normative) — decision recorded in `AG_AGENDA.md`'s decision log |

---
**End of Document**
