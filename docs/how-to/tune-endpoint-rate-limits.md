---
title: Tune Endpoint IPC Rate Limits
document_id: HOWTO-IPC-LIMITS
status: Active
owners: [Nyrqis Architecture]
created: 2026-09-10
ai_assisted: true
---

# How-To: Tune Endpoint IPC Rate Limits

Every IPC endpoint's rate limiter is a **fair** token bucket
(ADR-0009 §32b, NPS-010 §7.1.1): a shared envelope plus a per-sender
share, so one flooding container cannot starve well-behaved ones. This
guide covers inspecting and sizing those limiters on a running daemon.

## The model, in one paragraph

A limiter has a **shared envelope** — `rate` (tokens/s refill) and
`bucket_size` (burst capacity) — that caps the endpoint's total intake,
and a **per-sender share** — `tokens_per_second / fair_shares`, plus
`sender_burst` spike absorption — that caps what any ONE sender can
take from it. Both apply: a sender can never exceed its share, and all
senders together can never exceed the envelope.

**The sizing rule** (this is the whole job):

```
rate >= expected_senders x per-sender demand
```

Example: an input endpoint serving 8 clients at 250 Hz needs
`rate >= 8 x 250 = 2,000/s`. With `fair_shares=8`, every client is
then *guaranteed* 250/s even under a flood (measured: 250.5/s each
under contention — BENCHMARK_RESULTS §32d). With the envelope
undersized, fairness still holds — nobody starves anybody — but
*everyone* is throttled equally (§32d.3). Fairness equalizes; the
envelope decides whether there is enough to go around.

## Inspect current limiters

```bash
# Every endpoint: kind, rate, burst, shares, per-sender share
nyrqisctl ep-limits list

# One endpoint in detail (adds sender_burst and message count)
nyrqisctl ep-limits get ep-svc
```

## Retune a live endpoint

```bash
# Size for 8 clients x 250 Hz (the input-class example above)
nyrqisctl ep-limits set ep-svc --rate 2000 --bucket-size 256

# Narrow the shares (e.g. 16 known clients → 125/s guaranteed each)
nyrqisctl ep-limits set ep-svc --fair-shares 16

# Give each sender more spike absorption
nyrqisctl ep-limits set ep-svc --sender-burst 128
```

Omitted fields stay unchanged, so a fairness-only tweak does not touch
the envelope and vice versa. The change lands on the live endpoint
immediately; no daemon restart. These are operator-only control-plane
ops — a container cannot call them.

Defaults on a fresh daemon: `rate=500, bucket_size=200,
fair_shares=8, sender_burst=64` (per-sender share 62.5/s). The
proposed review defaults for input-class endpoints are 2,000/s with 8
shares (ADR-0009 review package §4).

## Reading `list` output

```
endpoint   container      kind             rate    burst  shares  per-sender
ep-svc     container-svc  FairTokenBucket  2000/s  256    8       250/s
```

- `per-sender` is `rate / fair_shares` — the guaranteed per-sender
  rate under contention.
- `kind` is `FairTokenBucket` on every default endpoint; a bare
  `TokenBucket` (shared pool only) can only appear if it was installed
  explicitly, and `ep-limits set` on it requires `--rate` or
  `--bucket-size` to replace it.

## When to resize

- A container logs `IPC rate limit exceeded` under normal load → the
  envelope is undersized for the sender count; apply the sizing rule.
- Suspected flooding: check `ep-limits get` — a fair bucket already
  confines the flooder to its share; raise the envelope only if
  legitimate senders are ALSO being throttled.
- Long-running lone-client endpoints (e.g. one game engine per
  endpoint): either size `rate` to that client's real demand, or opt
  in to **dynamic shares** — see below.

## Dynamic shares (opt-in)

With static shares (the default), a lone sender gets
`sender_burst + rate/fair_shares`, not the whole envelope (§32d.1).
`dynamic_shares=true` makes `fair_shares` mean "shares at full
occupancy": the effective per-sender refill is the envelope divided by
the **live sender count**, so a lone sender can use the whole envelope
while N coexisting senders (N ≥ fair_shares) keep the exact static
guarantee.

```bash
# Opt in on one endpoint
nyrqisctl ep-limits set ep-svc --dynamic-shares
# ... and back off again
nyrqisctl ep-limits set ep-svc --no-dynamic-shares
```

`ep-limits list/get` shows live occupancy (`active_senders`) and the
current effective `per-sender` share. Dynamic shares are **not** the
default: §32e measured that the guarantee holds but an abuser's
absolute take rises ~3.8× at low occupancy — static stays default
until the Architecture Group decides otherwise (review package §5.1).

## Watch admission metrics

Every endpoint keeps a bounded ring of admission samples; the metrics
op aggregates the trailing window:

```bash
nyrqisctl ep-limits metrics                    # all endpoints, 60 s
nyrqisctl ep-limits metrics ep-svc --window 300  # one endpoint, 5 min
nyrqisctl ep-limits metrics --watch 1          # poll every second
```

```
ep-svc: admitted 2000/2012 (33.3/s, rejected 0.2/s, rejection 0.60%) over 60.0s
```

- `rejection` creeping up under normal load → the envelope is
  undersized for the sender count; apply the sizing rule.
- `rejection` high for ONE endpoint while others are quiet → look at
  who is sending (the fair bucket already confines any flooder to its
  share).
- Samples are in-memory only (last ~4096 admissions) and reset on
  daemon restart — the metrics are for live watching, the audit trail
  for history.
- `--watch INTERVAL` re-renders continuously (clears a terminal
  between frames; safe to pipe; Ctrl-C stops). The trailing window is
  independent of the poll interval — poll fast, window wide.

## References

- ADR-0009 v1.3.0 (Implementation Note + benchmark data §32)
- NPS-010 §7.1.1 (normative fairness requirement)
- `tests/BENCHMARK_RESULTS.md` §32b–§32d (the numbers behind the rule)
- `docs/reference/adr/ADR-0009-review-package.md` (review status,
  open questions)
