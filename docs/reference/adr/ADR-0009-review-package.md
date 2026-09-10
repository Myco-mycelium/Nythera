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
     snapshot). NOT the default: the §32e adversarial re-check shows
     the guarantee HOLDS under dynamic shares (legit client fully
     protected), but an abuser's absolute take rises ~3.8× while
     occupancy is low (~1,022/s vs 271/s on the sized envelope) —
     so making dynamic the default is a policy decision, not a
     benchmark one. Recommended posture: static default, dynamic
     opt-in per endpoint for known-good, bursty, low-occupancy
     workloads (what is shipped).

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

## 7. Draft sign-off request (for the Architecture Group agenda)

> **Subject: Review request — ADR-0009 (IPC rate limiting) close-out;
> NPS-010 §7.1.1 adoption; default parameters proposal**
>
> ADR-0009's review package is complete
> (`docs/reference/adr/ADR-0009-review-package.md`, this document).
> The benchmark record is closed (BENCHMARK_RESULTS §2, §32a–e) and
> the mechanism is implemented, regression-tested, and live in the
> Linux backend. Three decisions are requested:
>
> **1. Mechanism (§3).** Endpoint token buckets with per-sender
> fairness — adopted normatively as NPS-010 §7.1.1 (v1.3.0),
> implemented as `FairTokenBucket` with fair-by-default endpoints.
> The §32b starvation mode (shared bucket throttled a legitimate
> 250 Hz client 96% under flood) is closed by construction and by
> measurement (§32c: legit client 250.0/s, 0 throttled, flood
> confined).
>
> **2. Default parameters (§4).** Per workload class, per the ADR's
> own "refill scaled to workload class": input/audio 2,000/s envelope,
> 8 shares, burst 256 (→ 250/s guaranteed per sender — §32d.2 shows
> the guarantee delivered under contention); bulk stays on the
> shared-memory path; everything else keeps the 500/s envelope. The
> packaged daemon and systemd unit already ship the input-class
> envelope; the `IPCManager` library default stays 200/500 pending
> your acceptance. The sizing rule (envelope ≥ senders × per-sender
> demand) is mandatory under static shares (§32d.1 lone-sender cap).
>
> **3. Static vs. dynamic shares (§5.1, §32e).** Recommended: static
> default (adversarial-optimal — an abuser's absolute take is
> minimized), dynamic as an opt-in per endpoint for known-good
> low-occupancy workloads. §32e shows the fairness guarantee holds
> under dynamic shares but an abuser's absolute take rises ~3.8× at
> low occupancy — making dynamic the default is a policy call, not a
> benchmark one.
>
> On acceptance: ADR-0009 → `Accepted`, NPS-010 → `Accepted` (the
> transitive §7.1 block clears), `IPCManager` defaults align with the
> accepted table, and NPS-011's `CAP-IPC-HIGH-THROUGHPUT` wording is
> reconciled. Instruments are re-runnable:
> `tests/benchmark_bucket.py --sweep | --adversarial | --fair-sweep`.
