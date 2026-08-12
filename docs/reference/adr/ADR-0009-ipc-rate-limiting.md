---
title: Per-Container Token-Bucket Rate Limiting for IPC
document_id: ADR-0009
version: 1.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-07-12
updated: 2026-08-12
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

## Benchmark Data (2026-08-12)

First-pass measurements from `tests/BENCHMARK_RESULTS.md` (Linux 6.14,
x86_64, Python 3.12; methodology in `tests/benchmarks.py`):

- The default `TokenBucket(bucket_size=100, tokens_per_second=50)`
  sustains only **~99.5 calls/s** on a single client→endpoint call path
  (199 successful round-trips in 2 s) and throttles **~18,875 calls/s**
  when the client sends at full speed (throttled `call`s return `None`;
  they do not block or raise).
- Steady-state refill therefore caps legitimate traffic at ~50 calls/s —
  orders of magnitude below what input delivery, audio, and controller
  paths (NPS-012 §6) would need.

This confirms the flooding concern the mechanism exists to address, and
also demonstrates the default parameters are **too low** for this
workload shape. The plan's legitimate-traffic baseline and
adversarial-flooding sweep are still needed before concrete defaults are
proposed; this ADR stays `Proposed` pending that sweep and Architecture
Group review.

## Status
Proposed — first-pass benchmark data collected (2026-08-12); parameter
sweep and Architecture Group review pending.
