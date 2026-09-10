---
title: Per-Container Token-Bucket Rate Limiting for IPC
document_id: ADR-0009
version: 1.3.1
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-07-12
updated: 2026-09-10
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

## Benchmark Data — Sweep + Adversarial (2026-09-10)

Close-out measurements (`tests/benchmark_bucket.py`, results in
`tests/BENCHMARK_RESULTS.md` §32):

- **Methodology correction**: `IPCManager.call` consults the RECEIVER
  endpoint's bucket, and `create_endpoint` builds buckets from the
  manager defaults — which ship as **burst=200 / 500 tokens-per-second**
  (not the 100/50 the 2026-08-12 writeup documented; the code default
  had drifted upward).
- **Sweep** (receiver-side bucket, burst pre-drained, steady state):
  sustained throughput ≈ refill rate at every burst capacity (32/100/
  256/1024) — burst shapes spike absorption only; refill/s is the knob
  that decides throughput. The shipped default caps a path at ~4.5% of
  its unthrottled capacity (13.3k calls/s floor on the in-process path);
  refill ≥ ~20,000/s reaches the "not the bottleneck" regime.
- **Adversarial** (shared endpoint bucket 256/1,000): a full-speed flood
  still passes ~1,025 calls/s while a legitimate 250 Hz client sharing
  the bucket gets **9 calls/s through (96% throttled)** — a naive shared
  bucket converts the limiter into a starvation weapon.

**Parameter recommendation this data supports** (for review): refill
scaled to workload class — ~1,000/s for input/audio endpoints, ~20,000/s
(or unlimited-by-grant) for bulk paths; burst ≤256 for spike absorption;
**plus per-sender (or per-flow) fairness within the endpoint budget** —
a mechanism change in NPS-010 §7.1, not a parameter choice, and the
adversarial data above is the argument for it.

## Implementation Note (2026-09-10)

The per-sender fairness the benchmark section above calls for is now
**implemented in the Linux backend** (`source/nyhal-linux-backend/ipc/core.py`):

- `FairTokenBucket` keeps the endpoint's shared envelope (bucket_size /
  tokens_per_second, unchanged meaning) and additionally confines each
  distinct sender to a per-sender sub-bucket refilled at
  `tokens_per_second / fair_shares` with `sender_burst` burst — one
  sender's sustained intake can never exceed its share, so the §32b
  starvation mode (250 Hz client throttled 96% under flood) is closed
  by construction; regression tests pin the behavior.
- New endpoints get a fair bucket **by default**
  (`IPCManager(default_fair_shares=8, default_sender_burst=64)`;
  sizing rule: envelope ≥ expected senders × per-sender demand).
- The operator can inspect and retune the knobs on a live endpoint via
  the control plane (`configure_endpoint_rate_limit`,
  `get_endpoint_rate_limit`, `list_endpoint_rate_limits`).
- **Dynamic shares** are available as an opt-in
  (`FairTokenBucket(dynamic_shares=True)`): `fair_shares` then means
  "shares at full occupancy" and the effective per-sender refill is
  the envelope divided by the live sender count — a lone sender may
  use the whole envelope, the full-occupancy guarantee is unchanged.
  Not yet the default (pending adversarial re-benchmark; review
  package §5.1).

The NPS-010 §7.1 wording still needs to adopt this mechanism, and the
ADR itself remains `Proposed` pending Architecture Group review.

## Status
Proposed — sweep + adversarial data collected (2026-09-10, §32);
parameter recommendation above ready for Architecture Group review.
The fairness mechanism is implemented in the Linux backend
(Implementation Note above); spec-side adoption in NPS-010 §7.1 is the
remaining step.
