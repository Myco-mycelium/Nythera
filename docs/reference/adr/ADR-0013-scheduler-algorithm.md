---
title: Adopt an EEVDF-Derived Scheduler with a Real-Time Priority Class
document_id: ADR-0013
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, NPS-001, NPS-002]
---

# ADR-0013 — EEVDF-Derived Scheduler with a Real-Time Priority Class

## Context
NPS-001 §7 and NPS-002 §6.2 both left the exact scheduler algorithm
unspecified, deferring it pending "usage-pattern data." The algorithm
choice itself, however, doesn't require hardware benchmarking to decide in
principle — only its tuning parameters do (default time slices, weight
curves), which remain correctly deferred. This ADR closes the algorithmic
choice; parameter tuning stays an open, benchmark-gated item (tracked in
`REPOSITORY_STATE.md`).

## Decision (Proposed)
Adopt an **EEVDF-derived** (Earliest Eligible Virtual Deadline First)
algorithm as the default time-sharing scheduling class, with a separate,
higher-priority **real-time class** for latency-sensitive work (input
delivery per NPS-012 §6.1, audio, and frame pacing per NPS-002 §6.2).

- EEVDF-style scheduling is chosen over a pure CFS (Completely Fair
  Scheduler)-style vruntime model because it directly models a request's
  *deadline eligibility*, not just historical fairness — a better match
  for NTM-000's "gaming is first-class" goal, where frame-pacing
  consistency matters at least as much as long-run fairness.
- The real-time class **MUST** be usable only by containers holding the
  appropriate resource-limit grant (NPS-010 §7.2), consistent with the
  "no implicit elevated resource priority" principle already established
  for capabilities (NPC-001 §9.2) — a container doesn't get real-time
  scheduling merely by claiming to need it.
- Exact time-slice length, weight curve, and real-time class admission
  limits remain unspecified pending benchmarking (per NPC-002 §5.2); this
  ADR fixes the algorithmic family, not its tuning.

## Alternatives Considered
- **Plain CFS-style vruntime fairness, no explicit real-time class** —
  rejected; without a distinct latency-sensitive class, input and audio
  work compete on equal footing with background compute, which risks
  exactly the frame-pacing inconsistency NTM-000's gaming goals are meant
  to avoid.
- **A fully custom scheduler design** — rejected as unjustified complexity
  (NTM-000 §4, "Simplicity"); EEVDF is a well-understood, published
  algorithm with known properties, avoiding the risk of inventing and
  then having to debug a novel scheduling theory from scratch.
- **Priority-only (no fairness guarantee) scheduling** — rejected; a
  purely priority-based scheduler risks starving low-priority containers
  indefinitely, conflicting with NTM-000 §4 ("Reliability").

## Consequences
- NPS-002 §6.2 and NPS-001 §3's scheduler component are now specified at
  the algorithm-family level; NPS-001 §7's open question is narrowed from
  "which algorithm" to "which tuning parameters," pending benchmark data.
- The real-time class's admission control depends on NPS-010's
  resource-limit grant mechanism, which itself is only partially specified
  (NPS-010 §9 already flags default CPU/memory limits as open).
- This decision MUST be revisited if benchmarking reveals EEVDF's
  properties don't hold up under Nythera's actual container/IPC load
  patterns (NPS-003's IPC-heavy design is somewhat different from a
  typical Linux workload EEVDF was tuned against).

## Status
Proposed — algorithm family decided; tuning parameters remain pending
benchmark data per NPC-002 §5.2.
