#!/usr/bin/env python3
"""ADR-0009 token-bucket parameter sweep + adversarial interference test.

Why this exists: BENCHMARK_RESULTS §2 (2026-08-12) showed the DEFAULT
bucket (bucket_size=100, tokens_per_second was 50 at measurement time;
the code default is now 10.0 — both are measured here) caps a single
client→endpoint call path far below what input/audio traffic (NPS-012
§6) needs. ADR-0009 cannot exit Proposed until default parameters are
chosen from sweep data, not guessed.

What this measures (BENCHMARK_PLAN §3, completed data collection):

1. SWEEP — a (bucket_size × tokens_per_second) grid on the RECEIVER
   endpoint's bucket (that is the bucket `IPCManager.call` consults, via
   `endpoint.send_message`). The client drives full speed; the burst is
   PRE-DRAINED before timing so the measured steady state isolates the
   refill rate (with the burst included, a 1 s window reports
   ~refill + burst/window — spike absorption, not sustained rate). The
   floor row (unthrottled bucket) gives the path's ceiling, and a
   "manager default" row pins down what `IPCManager` actually ships
   today (burst=200, 500/s — which had drifted from the 100/50 the
   §2 writeup documented).

2. ADVERSARIAL — one flooding client (no sleep, full-speed) shares an
   endpoint's rate limiter with a legitimate client paced at a realistic
   rate. We record: how many flood calls are throttled (the limiter must
   reject the flood) and the legitimate client's call-completion rate
   (the limiter must not starve it). A limiter that passes the flood but
   starves the legitimate client is worse than no limiter; a limiter
   that throttles the flood AND keeps the legitimate client at its
   requested rate is the desired behavior.

Honesty notes (NPC-002 §5.2):
- In-process path (IPCManager.call), single host — this is the same path
  shape as BENCHMARK_RESULTS §1/§2, not a multi-container deployment.
- Windows are short (1.0 s per config) so the whole sweep runs in ~30 s;
  per-config numbers carry ~±5% run-to-run variance (§5 re-runs differ
  by that much).
- Results belong in tests/BENCHMARK_RESULTS.md, not in this file.

Run:
    python3 tests/benchmark_bucket.py           # sweep + adversarial
    python3 tests/benchmark_bucket.py --sweep   # sweep only
    python3 tests/benchmark_bucket.py --adversarial
"""

import argparse
import threading
import time

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "source" / "nyhal-linux-backend"))

from ipc.core import IPCManager, TokenBucket  # noqa: E402

WINDOW_S = 1.0
PAYLOAD = 64

# Grid chosen around the default and the workload shapes ADR-0009 names:
# input delivery (125–250 Hz per device), audio (per-period callbacks,
# ~48 kHz/period-size ≈ hundreds of calls/s), and background chatter.
BURSTS = [32, 100, 256, 1024]
RATES = [50, 100, 500, 1000, 5000, 20000]


def _drive_roundtrips(duration_s: float) -> dict:
    """Sustained round-trips against endpoints with their DEFAULT buckets.

    Returns (succeeded, throttled) counts. Throttled calls return None
    from ``mgr.call`` — they do not raise (documented in §2).
    """
    mgr = IPCManager()
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    cli = mgr.create_endpoint("container-cli", "ep-cli")
    payload = b"x" * PAYLOAD
    stop = threading.Event()

    def responder():
        while not stop.is_set():
            msg = mgr.receive(svc.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * PAYLOAD)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    succeeded = throttled = 0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        if mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=0.5) is None:
            throttled += 1
        else:
            succeeded += 1
    stop.set()
    thread.join(timeout=1.0)
    return {"succeeded": succeeded, "throttled": throttled, "seconds": duration_s}


def _drive_against_bucket(bucket, window_s: float, pre_drain: bool = True) -> dict:
    """Drive full-speed round-trips against a service endpoint whose
    rate limiter is ``bucket``; return sustained/throttled per second.

    With ``pre_drain`` the burst is consumed (and a bounded wait lets
    the bucket settle) before the timed window, so the result is the
    refill-limited steady state, not burst + refill.
    """
    mgr = IPCManager()
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    cli = mgr.create_endpoint("container-cli", "ep-cli")
    svc.rate_limit = bucket  # the bucket `call` consults (receiver side)
    if pre_drain:
        drained = 0
        while bucket.try_consume():
            drained += 1
        # Let the bucket settle to steady state before timing (bounded).
        time.sleep(min(1.0, drained / max(bucket.tokens_per_second, 1e-9)))
    payload = b"x" * PAYLOAD
    stop = threading.Event()

    def responder():
        while not stop.is_set():
            msg = mgr.receive(svc.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * PAYLOAD)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    ok = thr = 0
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        if mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=0.5) is None:
            thr += 1
        else:
            ok += 1
    stop.set()
    thread.join(timeout=1.0)
    return {"sustained": ok / window_s, "throttled": thr / window_s}


def sweep() -> list:
    """Steady-state sustained rate for every (burst, rate) pair, the
    manager's real defaults, and the unthrottled floor."""
    rows = []

    # Floor: unthrottled bucket on the receiver endpoint.
    floor_result = _drive_against_bucket(
        TokenBucket(bucket_size=1_000_000, tokens_per_second=1_000_000.0),
        WINDOW_S, pre_drain=False,
    )
    floor = floor_result["sustained"]
    rows.append(("floor (unthrottled)", 0, floor, floor_result["throttled"]))

    # Manager-default row: NO bucket replacement — measure the endpoint
    # exactly as create_endpoint builds it (burst=200, 500/s today).
    mgr = IPCManager()
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    cli = mgr.create_endpoint("container-cli", "ep-cli")
    payload = b"x" * PAYLOAD
    stop = threading.Event()

    def responder():
        while not stop.is_set():
            msg = mgr.receive(svc.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * PAYLOAD)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    ok = thr = 0
    deadline = time.monotonic() + WINDOW_S
    while time.monotonic() < deadline:
        if mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=0.5) is None:
            thr += 1
        else:
            ok += 1
    stop.set()
    thread.join(timeout=1.0)
    rows.append(("manager default (200, 500/s)", 500, ok / WINDOW_S, thr / WINDOW_S))

    for burst in BURSTS:
        for rate in RATES:
            bucket = TokenBucket(bucket_size=burst, tokens_per_second=float(rate))
            r = _drive_against_bucket(bucket, WINDOW_S)
            rows.append((f"burst={burst}", rate, r["sustained"], r["throttled"]))
    return rows


def adversarial(duration_s: float = 3.0, legit_rate_hz: float = 250.0) -> dict:
    """Flood vs legitimate client sharing ONE limiter on the endpoint.

    The limiter protects the SERVICE endpoint (as NPS-010 §7.1 intends:
    the endpoint's bucket limits what senders can push at it). The flood
    thread calls full-speed; the legitimate thread paces itself at
    ``legit_rate_hz`` (input-delivery shape). We count:
      - flood admitted / throttled per second,
      - legitimate admitted / throttled per second.
    Desired outcome: flood heavily throttled, legitimate client at ~its
    requested rate (its calls should NOT be throttled once the flood has
    drained the burst — a fair limiter reserves refill for whoever asks;
    a naive shared bucket will NOT do that, and quantifying the
    interference is exactly the data ADR-0009 needs to decide between
    shared buckets and per-sender fairness).
    """
    mgr = IPCManager()
    bucket = TokenBucket(bucket_size=256, tokens_per_second=1000.0)
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    svc.rate_limit = bucket
    flood_ep = mgr.create_endpoint("container-flood", "ep-flood")
    legit_ep = mgr.create_endpoint("container-legit", "ep-legit")
    payload = b"x" * PAYLOAD
    stop = threading.Event()

    def responder():
        while not stop.is_set():
            msg = mgr.receive(svc.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * PAYLOAD)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()

    counts = {"flood_ok": 0, "flood_thr": 0, "legit_ok": 0, "legit_thr": 0}

    def flood():
        payload_l = payload
        while not stop.is_set():
            if mgr.call("container-flood", svc.endpoint_id, payload_l, timeout_s=0.05) is None:
                counts["flood_thr"] += 1
            else:
                counts["flood_ok"] += 1

    def legit():
        payload_l = payload
        interval = 1.0 / legit_rate_hz
        next_t = time.monotonic()
        while not stop.is_set():
            next_t += interval
            if mgr.call("container-legit", svc.endpoint_id, payload_l, timeout_s=0.5) is None:
                counts["legit_thr"] += 1
            else:
                counts["legit_ok"] += 1
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    t1 = threading.Thread(target=flood, daemon=True)
    t2 = threading.Thread(target=legit, daemon=True)
    t1.start()
    t2.start()
    time.sleep(duration_s)
    stop.set()
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)
    stop.set()
    thread.join(timeout=1.0)

    return {
        "duration_s": duration_s,
        "legit_requested_hz": legit_rate_hz,
        "bucket": {"size": 256, "rate": 1000},
        "flood_admitted_per_s": round(counts["flood_ok"] / duration_s, 1),
        "flood_throttled_per_s": round(counts["flood_thr"] / duration_s, 1),
        "legit_admitted_per_s": round(counts["legit_ok"] / duration_s, 1),
        "legit_throttled_per_s": round(counts["legit_thr"] / duration_s, 1),
        "legit_meets_request": counts["legit_thr"] / max(1, counts["legit_ok"] + counts["legit_thr"]) < 0.01,
    }


def _print_sweep(rows):
    print("| burst | refill/s | sustained calls/s | throttled/s | % of floor |")
    print("|-------|---------:|------------------:|------------:|-----------:|")
    floor = rows[0][2]
    for burst, rate, ok, thr in rows[1:]:
        pct = 100.0 * ok / floor if floor else 0.0
        print(f"| {burst} | {rate} | {ok:.1f} | {thr:.1f} | {pct:.1f}% |")
    print(f"\nfloor: {floor:.1f} calls/s (unthrottled, same path)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sweep", action="store_true", help="parameter sweep only")
    parser.add_argument("--adversarial", action="store_true", help="interference test only")
    args = parser.parse_args()
    both = not args.sweep and not args.adversarial

    if args.sweep or both:
        rows = sweep()
        _print_sweep(rows)
    if args.adversarial or both:
        print("\nadversarial interference (shared endpoint bucket):")
        result = adversarial()
        for k, v in result.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
