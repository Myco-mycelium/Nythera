---
title: Per-Container Token-Bucket Rate Limiting for IPC
document_id: ADR-0009
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0006, NPS-002, NPS-003]
---

# ADR-0009 — Per-Container Token-Bucket Rate Limiting for IPC

## Context
NPS-003 §8.2 left endpoint-flooding denial-of-service mitigation as an open
question, deferred to Milestone M6. Because the kernel is the sole arbiter
of IPC (NPS-003 §5.4) and every meaningful platform operation flows through
it (NPS-001 §4), an unbounded container could starve others by flooding
`send`/`call` traffic even without holding any capability beyond
"communicate with an endpoint it has legitimate access to."

## Decision (Proposed)
Enforce IPC rate limiting via a **token bucket per container**, checked by
the kernel's capability/IPC primitives (NPS-001 §3) at `send`/`call` time.
Each container is assigned a bucket with a refill rate and burst capacity;
exceeding it causes the offending `send`/`call` to block or fail with a
defined backpressure error rather than succeeding unbounded.

- Buckets are scoped **per container**, not per process, consistent with
  containers being the trust/resource boundary (NPS-002 §4).
- System-service containers (NPS-001 §5 Stage 5) **MAY** be assigned a
  higher default bucket than application containers, but this exception
  **MUST** be explicit and auditable, not implicit by virtue of being a
  system process — consistent with NPC-001 §9.2's "no implicit elevated
  privileges" rule extended to resource limits.
- Bucket parameters **MUST** be adjustable per-container by the capability
  registry (NPS-011) if a specific capability class is later shown to
  need higher-throughput IPC (e.g. bulk shared-memory setup), rather than
  raising the global default.

## Alternatives Considered
- **No rate limiting (status quo, i.e. leave §8.2 unresolved)** — rejected;
  leaves an unmitigated denial-of-service path across every container,
  directly conflicting with NTM-000 §4 ("Security is created through
  architecture").
- **Global system-wide rate limit rather than per-container** — rejected;
  a single noisy container could still degrade every other container,
  which defeats the purpose of container isolation established in
  ADR-0004.
- **Reactive throttling (detect flooding after the fact, then penalize)**
  — rejected as the primary mechanism; reactive detection is inherently a
  race condition during the window before detection, whereas a token
  bucket bounds the problem structurally from the start. Reactive
  monitoring MAY still be layered on top in a future revision.

## Consequences
- NPS-010 (Container Runtime) MUST define where bucket parameters are
  configured and their interaction with container creation.
- Legitimate high-throughput use cases (e.g. bulk asset streaming during
  game load, per NPS-006 §5) MUST route through the shared-memory bulk
  transfer path (NPS-003 §3.1), not through high-frequency small messages,
  so they are not artificially throttled by this mechanism.
- Exact default refill rate and burst capacity require benchmarking before
  this ADR can move past Proposed, per NPC-002 §5.2.

## Status
Proposed — pending benchmark data and Architecture Group review.
