# How to Operate the IPC Rate Limits

*Applies to: retuning and watching the endpoint token buckets on the
Linux Backend daemon (`source/nyhal-linux-backend/`, ADR-0009,
NPS-010 §7.1). Every command talks to the daemon's control plane over
the kernel-authenticated transport, so the daemon must be running, and
the limiter ops are operator-only.*

## When you need this

You run the daemon and you want to know why calls are being throttled
on an endpoint, decide whether that endpoint should use static or
dynamic per-sender shares, retune the bucket without restarting, or
leave an audit trail of why you did. The limiter is a reliability
mechanism, not a security boundary (NPS-010 §7.3) — capability
enforcement is separate.

## Concepts (30 seconds)

- **Envelope** — the endpoint's shared budget: `bucket_size` burst,
  `tokens_per_second` refill. Total intake never exceeds it.
- **Per-sender fairness** (`FairTokenBucket`, fair-by-default) — each
  distinct sender is additionally confined to a sub-bucket refilled at
  `tokens_per_second / fair_shares` with `sender_burst` burst, so one
  sender's sustained intake can never exceed its share (ADR-0009 §32b:
  a shared-only bucket starved a legitimate 250 Hz client to ~16
  admitted/s under a full-speed flood).
- **Static vs dynamic shares** — under **static** shares the refill is
  always `envelope / fair_shares`, whether or not that many senders
  exist (a lone sender is capped at `sender_burst + envelope/shares`;
  measured 313/s on a 2,000/s envelope). Under **dynamic** shares the
  refill follows the live sender count — a lone sender may draw the
  whole envelope (measured 2,063/s), and the trade is a larger
  low-occupancy abuser take (~3.8× under flood). At full occupancy the
  modes are indistinguishable (§32e/§32f).
- **The guarantee** (NPS-010 §7.1.1, mode-independent): total intake is
  capped at the envelope, and a sender's sustained intake never exceeds
  its share.

## Steps

### 1. See what exists

```bash
nyrqisctl ep-limits-list                 # every endpoint's limiter summary
nyrqisctl ep-limits-get --endpoint-id ep-svc
```

The snapshot shows `kind`, `bucket_size`, `tokens_per_second`,
`fair_shares`, `sender_burst`, `per_sender_share`, `active_senders`,
and `dynamic_shares`. On a dynamic endpoint `per_sender_share` is the
**current** occupancy-dependent refill (envelope ÷ live senders), not
a fixed number.

### 2. Read the admission metrics

```bash
nyrqisctl ep-limits-metrics --endpoint-id ep-svc --window 60
nyrqisctl ep-limits-metrics --window 300        # all endpoints
```

You get `total` / `admitted` / `rejected` counts, `admitted_per_s`,
`rejected_per_s`, and `rejection_ratio` over the trailing window, from
the endpoint's bounded sample ring (`retained_s` tells you how much of
the window the ring actually covers — this is a sample ring, not a
time-series database).

Reading them:

- **`rejection_ratio` ~0, `admitted_per_s` ≈ requested** — healthy;
  leave it alone.
- **Legitimate senders throttled while a few senders dominate** — the
  sizing rule is violated (below) or the envelope is too small. Fix the
  envelope first; the fairness mechanism already protects the victim.
- **A lone sender seeing `rejection_ratio` ≈ 1 on a STATIC endpoint** —
  that is the share cap doing its job. If the sender is known-good and
  legitimately needs more, this is the dynamic-shares use case (step 3).

### 3. Choose the posture

| Situation | Posture |
|---|---|
| Untrusted / mixed sender population; minimize any abuser's absolute take | **static** (the shipped default) |
| Known-good, low-occupancy, bursty workload (e.g. one game engine that intermittently needs the full envelope) | **dynamic** for that endpoint |
| `admitted_per_s` summed over senders far below the envelope while senders are share-capped | raise `fair_shares`' effective width via the envelope, or go dynamic for that endpoint |

### 4. Retune (no restart)

```bash
# Envelope + fairness knobs (a partial envelope update re-creates the
# limiter; a fairness-only update retunes in place):
nyrqisctl ep-limits-set --endpoint-id ep-svc \
    --rate 2000 --bucket-size 256 --fair-shares 8 --sender-burst 64 \
    --reason "sized to 8 input senders x 250 Hz (NPS-010 7.1.1)"

# Switch one endpoint to dynamic shares (and back):
nyrqisctl ep-limits-set --endpoint-id ep-svc --dynamic-shares \
    --reason "known-good engine, intermittently needs the envelope"
nyrqisctl ep-limits-set --endpoint-id ep-svc --no-dynamic-shares \
    --reason "back to platform default after the burst window"
```

Sizing rule under static shares (ADR-0009 §32d.1, mandatory): **the
envelope must be ≥ expected senders × per-sender demand**, or every
sender is starved equally. Under dynamic shares the rule relaxes (the
share follows occupancy), but the envelope remains the hard ceiling.

### 5. Leave a trail; verify

Every retune lands in the control-plane audit trail with the operator's
`--reason` — this is the "who changed the shared knobs, when, and why"
record (distinct from the per-container ADR-0018 capability audit):

```bash
nyrqisctl ep-limits-audit
```

Verify the switch took effect and watch the occupancy:

```bash
nyrqisctl ep-limits-get --endpoint-id ep-svc   # dynamic_shares: true
watch -n5 'nyrqisctl ep-limits-get --endpoint-id ep-svc'
```

## What to watch on a dynamic-mode endpoint

- **`active_senders`** — the share denominator. As it grows toward
  `fair_shares`, dynamic behavior converges to static; when it is low,
  each sender can draw a large slice.
- **`per_sender_share`** — under dynamic this moves with occupancy; a
  sudden drop means new senders arrived (or a sender is churning the
  table).
- **`admitted_per_s` of the loudest sender** — under dynamic, a lone
  full-speed sender drawing ~the envelope is **by design**, not a
  fault. If that sender is not known-good, switch the endpoint back to
  static (step 4) — that is the trade you accepted, quantified in
  BENCHMARK_RESULTS §32e/§32f.
- **`rejection_ratio` on legitimate senders** — must stay ~0 in both
  modes. Non-zero means the envelope itself is too small (raise
  `rate`), not that shares are wrong.

## Scope notes (honest)

- **With a state file** (`--state-file`, the systemd unit sets one),
  operator retunes persist: every retune records the endpoint's
  posture in the state file (`endpoint_limit_overrides`) and a
  restarted daemon re-applies it before serving — the retune survives
  the restart. Without a state file the configuration is daemon
  process state: a restart recreates the endpoints at their
  code/manifest defaults, so re-apply your retunes by hand (they are
  visible in `ep-limits-audit` for exactly this).
- The admission metrics come from a bounded in-memory sample ring;
  there is no historical export. If you need history, sample
  `ep-limits-metrics` on a cron.
- All limiter ops are operator-only (trusted-uid control plane); a
  container cannot retune its own or another endpoint's bucket.
