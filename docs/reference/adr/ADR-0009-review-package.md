---
title: ADR-0009 Review Package — Per-Container Token-Bucket IPC Rate Limiting
document_id: ADR-0009-REVIEW
version: 1.0.0
status: Informational
owners: [Nyrqis Architecture]
created: 2026-09-10
ai_assisted: true
depends_on: [ADR-0009, NPS-010, NPS-003]
---

# ADR-0009 Review Package — Architecture Group Sign-Off

Everything the Architecture Group needs to move ADR-0009 (and, with it,
NPS-010) out of `Proposed`/`Draft`, consolidated from the benchmark
record and the shipped implementation. All numbers cite their section
in `tests/BENCHMARK_RESULTS.md`; the instruments are re-runnable.

## 1. What is being asked

1. **Accept the mechanism**: a token bucket per endpoint, with
   **per-sender fairness** as a normative requirement (now §7.1.1 of
   NPS-010 v1.3.0).
2. **Accept the default parameters** (§2 below), or direct changes.
3. **Decide the one open mechanism question**: static vs. dynamic
   `fair_shares` (§5).

## 2. Benchmark record (complete)

| § | instrument | finding |
|---|---|---|
| §2, §32a | parameter sweep | steady-state throughput ≈ refill rate at every burst (32–1024); burst shapes spike absorption only; refill is the throughput knob |
| §32a | shipped default on the call path | 200 / 500/s caps a path at ~4.5% of its unthrottled floor (13.3k calls/s) |
| §32b | adversarial, shared bucket | a full-speed flood passes ~1,025/s while a legitimate 250 Hz client sharing the bucket gets **9/s (96% throttled)** — a shared-only bucket is a starvation weapon |
| §32c | adversarial, `FairTokenBucket` | same undersized envelope: flood confined (1,030 → 146 admits/s), both senders equal; envelope sized per the rule (2,000/s): legit client **250.0/s, 0 throttled**, flood 271/s |
| §32d | fair-bucket defaults data | lone sender = `sender_burst` + `envelope/shares` (static-shares cost, see §5); 8 × 250 Hz senders on a sized envelope all meet rate (250.5/s each); undersized envelope → flat, equal starvation instead of an asymmetric victim |

Re-run: `python3 tests/benchmark_bucket.py [--sweep | --adversarial |
--fair-sweep]` (Linux 6.14, x86_64, Python 3.12; in-process
CALL/REPLY path — the honesty notes in the benchmark docstring apply).

## 3. Implementation status (shipped, regression-tested)

- `ipc/core.FairTokenBucket` (subclass of `TokenBucket`): per-sender
  sub-buckets under the shared envelope; sender table bounded (idle
  eviction past 1024 entries). Regression tests in
  `source/nyhal-linux-backend/test_backend.py` (`TestFairTokenBucket`,
  7 tests) pin flood bounding, envelope cap, shared-pool
  compatibility, newcomer share, and table bounds.
- **Fair by default**: `IPCEndpoint` and `IPCManager.create_endpoint`
  build fair buckets; knobs `default_fair_shares` (8) and
  `default_sender_burst` (64).
- **Operator control plane** (operator-only): `configure_endpoint_rate_limit`,
  `get_endpoint_rate_limit`, `list_endpoint_rate_limits`; CLI:
  `nyrqisctl ep-limits list|get|set`.
- Spec adoption: NPS-010 §7.1.1 (normative), ADR-0009 v1.3.0
  Implementation Note.

## 4. Proposed defaults (§32d)

Per the ADR's own "refill scaled to workload class":

| endpoint class | envelope (burst / rate) | fair_shares | per-sender share |
|---|---|---|---|
| input/audio (NPS-012 §6: 8 clients × 250 Hz) | 256 / 2,000/s | 8 | 250/s |
| bulk paths (NPS-006 §5) | ≥20,000/s or unlimited-by-grant — bulk is REQUIRED to the shared-memory path (NPS-003 §3.1) by the ADR's Consequences | n/a | n/a |
| background chatter / default | 200 / 500/s (current ship) | 8 | 62.5/s |

Sizing rule (mandatory under static shares): **envelope ≥ expected
senders × per-sender demand**.

## 5. Open questions for the Group

1. **Static vs. dynamic `fair_shares`.** Static shares make a lone
   sender share-capped (~62.5/s on the default envelope — §32d.1).
   Options:
   - (a) Accept static shares + the sizing rule (the §32c/§32d
     guarantees are then exact).
   - (b) Dynamic shares: `fair_shares` means "shares at full
     occupancy"; the effective per-sender refill is the envelope
     divided by the live active-sender count, so a lone sender may use
     the whole envelope while the full-occupancy guarantee is
     unchanged. **Implemented as an opt-in**
     (`FairTokenBucket(dynamic_shares=True)`, retunable per endpoint
     via `configure_endpoint_rate_limit ... "dynamic_shares": true`;
     occupancy is reported as `active_senders` in the limiter
     snapshot). NOT yet the default: dynamic behavior needs its own
     adversarial re-benchmark before it can back the §32c guarantee —
     the Group decides whether it ships on, per class.

   Both options preserve the NPS-010 §7.1.1 fairness requirement at
   full occupancy; they differ only in under-occupied behavior.
2. **Input-class envelope**: confirm 2,000/s (8 × 250 Hz) against
   multi-seat console data when M15 hardware-matrix runs land.
3. **Bulk path parameters** are informational: bulk traffic is already
   REQUIRED to the shared-memory path; the ≥20,000/s figure only marks
   the "not the bottleneck" regime measured in §32a.

## 6. Sign-off checklist

- [ ] Mechanism accepted (token bucket + per-sender fairness, NPS-010 §7.1.1)
- [ ] Defaults accepted (§4) or amended
- [ ] Static/dynamic shares decided (§5.1)
- [ ] ADR-0009 → `Accepted`; NPS-010 → `Accepted` (transitive block clears)
- [ ] NPS-011 `CAP-IPC-HIGH-THROUGHPUT` wording reconciled with §7.1.1 if needed
