#!/usr/bin/env python3
"""ADR-0018 close-out data: hash-chained audit-log overhead, measured
against the REAL implementation (backend/container.py's
`initialize_audit_integrity` / `append_audit_event` /
`verify_audit_integrity` — the exact code the IPC control plane calls
per capability grant/revoke, ipc/control.py:8553).

Why this exists: ADR-0018's Consequences say the per-event hash cost is
"expected to be negligible ... but per NPC-002 §5.2 this is not asserted
as a claim without a benchmark", and its Status line reads "Benchmark
pending". This script collects the data so the ADR can exit Proposed
with the expectation either confirmed or refuted.

Sections (each runnable standalone):

1. APPEND  — per-op latency distribution (p50/p95/p99/max) and sustained
   throughput of `append_audit_event` over a 100 k-event chain, GC
   disabled during timing (microbenchmark hygiene), plus the same with
   GC left on (the daemon runs with GC on; if the chain allocations
   trigger collections, the steady-state number lies).

2. VERIFY  — full-chain verification cost at chain lengths
   1 k / 10 k / 50 k / 100 k: total ms, per-event µs, and the linearity
   check (verify is O(n) by construction — the question is whether the
   per-event constant is stable as the chain grows, i.e. no accidental
   O(n²) in the chain lookup).

3. FLOOR   — decomposition: raw hashlib.sha256 of the same ~110-byte
   content string, the f-string + float-formatting alone, and a
   no-op method call. Shows how much of the measured append cost is
   the hash itself vs Python machinery (dict appends, attribute
   lookups, the duplicate _audit_trail write).

4. CONTEXT — overhead fraction against the recorded IPC numbers this
   audit path actually accompanies (BENCHMARK_RESULTS §20: in-process
   call p50 92 µs, wire p50 307–357 µs ABI 2.0.0). Cited, not
   re-measured — the point is the RATIO, which is what "negligible"
   has to mean operationally: if appending an audit event to a
   grant/revoke costs a small fraction of the operation it audits,
   the chain never becomes the bottleneck.

5. TAMPER SCOPE — an empirically demonstrated property of the current
   hashing scheme, discovered while writing this benchmark: the hashed
   content is `salt + prev_hash + op + timestamp` — the `details`
   payload is NOT hashed and NOT covered by verification. This section
   demonstrates the consequence on the real code (mutate a stored
   event's details → verify still reports valid=True) so the scope
   limitation is on record for the Architecture Group rather than
   discovered by an adversary. It is a property of the shipped scheme,
   not a performance number; it goes in the results notes, and fixing
   it is a spec decision (ADR-0018 §"Consequences" wording), not
   something this benchmark does unilaterally.

Honesty notes (NPC-002 §5.2):
- Single host, CPython on this machine's CPU; absolute numbers are
  host-dependent. The STRUCTURAL findings (O(n) verify with a stable
  constant, append cost ~µs-class, hash is not the dominant term,
  details not hashed) are host-independent code properties.
- The benchmark calls the real methods on a real ContainerManager and
  a real Container created via `mgr.create()` — no mocks, no stubs,
  no subprocess isolation. GC-off sections are marked as such.
- Tamper tests mutate only the in-memory chain of a throwaway
  container; nothing persists.

Run:
    python3 tests/benchmark_adr0018.py            # everything
    python3 tests/benchmark_adr0018.py --append
    python3 tests/benchmark_adr0018.py --verify
    python3 tests/benchmark_adr0018.py --floor
    python3 tests/benchmark_adr0018.py --context
    python3 tests/benchmark_adr0018.py --tamper-scope
"""

import argparse
import gc
import hashlib
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "source" / "nyhal-linux-backend"))

from backend.container import ContainerConfig, ContainerManager  # noqa: E402

# ---- ADR-0018 context: the events this log actually records -------------
# (ipc/control.py audit call sites: capability grants/revokes, container
# lifecycle ops). Representative op + a representative details payload.
REAL_OPS = ["CAP-GRANT", "CAP-REVOKE", "CONTAINER-START", "CONTAINER-STOP"]
REAL_DETAILS = {
    "capability": "CAP-IPC-CALL",
    "target": "nyctr-0ab1c2d3e4f5",
    "endpoint": "/com/nyrqis/Capability",
    "granted_by": "policy-engine",
}


def make_manager_and_container():
    mgr = ContainerManager()
    c = mgr.create(ContainerConfig(name="bench-adr0018"))
    mgr.initialize_audit_integrity(c)
    return mgr, c


def percentile(sorted_xs, p):
    if not sorted_xs:
        return 0.0
    k = max(0, min(len(sorted_xs) - 1, int(round(p / 100 * (len(sorted_xs) - 1)))))
    return sorted_xs[k]


def pcts(xs_us):
    s = sorted(xs_us)
    return {
        "p50": percentile(s, 50),
        "p95": percentile(s, 95),
        "p99": percentile(s, 99),
        "max": s[-1],
    }


# --------------------------------------------------------------------------
# 1. APPEND
# --------------------------------------------------------------------------
def bench_append(n_events=100_000):
    print(f"== APPEND: {n_events:,} events, one chain, real methods ==")
    mgr, c = make_manager_and_container()
    results = {}
    for label, disable_gc in (("gc-off", True), ("gc-on", False)):
        if disable_gc:
            gc.collect()
            gc.disable()
        else:
            gc.enable()
        lat = []
        t0 = time.perf_counter()
        try:
            for i in range(n_events):
                ta = time.perf_counter()
                mgr.append_audit_event(
                    c, REAL_OPS[i % len(REAL_OPS)], dict(REAL_DETAILS, seq=i)
                )
                lat.append((time.perf_counter() - ta) * 1e6)
        finally:
            total = time.perf_counter() - t0
            if disable_gc:
                gc.enable()
        p = pcts(lat)
        results[label] = {"per_op_us": p, "throughput": n_events / total}
        print(
            f"  {label:7s}: p50 {p['p50']:8.2f}  p95 {p['p95']:8.2f}  "
            f"p99 {p['p99']:8.2f}  max {p['max']:9.2f} µs/op   "
            f"throughput {results[label]['throughput']:>9,.0f} ops/s"
        )
    print(
        "  (per-op latency includes the timing call itself, ~50–100 ns; "
        "throughput is the honest steady-state number)"
    )
    return results


# --------------------------------------------------------------------------
# 2. VERIFY (scaling + linearity)
# --------------------------------------------------------------------------
def bench_verify(lengths=(1_000, 10_000, 50_000, 100_000)):
    print("== VERIFY: full-chain verification vs chain length ==")
    mgr, c = make_manager_and_container()
    rows = []
    for n in lengths:
        c._audit_hash_chain = []
        c._audit_trail = []
        for i in range(n):
            mgr.append_audit_event(
                c, REAL_OPS[i % len(REAL_OPS)], dict(REAL_DETAILS, seq=i)
            )
        # 3 verify passes, keep the best (first pass may be cold-cache)
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            r = mgr.verify_audit_integrity(c)
            dt = time.perf_counter() - t0
            assert r["valid"] and r["chain_length"] == n, r
            best = dt if best is None else min(best, dt)
        per_event = best / n * 1e6
        rows.append((n, best * 1e3, per_event))
        print(
            f"  {n:>7,} events: {best*1e3:>9.2f} ms total   "
            f"{per_event:>6.2f} µs/event"
        )
    consts = [r[2] for r in rows]
    spread = (max(consts) - min(consts)) / min(consts) * 100
    print(
        f"  per-event constant spread across sizes: {min(consts):.2f}–"
        f"{max(consts):.2f} µs ({spread:.0f}% spread) → "
        f"{'O(n) with stable constant' if spread < 60 else 'CHECK for superlinearity'}"
    )
    return rows


# --------------------------------------------------------------------------
# 3. FLOOR (decomposition)
# --------------------------------------------------------------------------
def bench_floor(n=200_000):
    print("== FLOOR: what inside append_audit_event costs what ==")
    salt = "a" * 16
    prev = "b" * 64
    op = "CAP-GRANT"
    ts = time.time()
    content = f"{salt}{prev}{op}{ts}"

    # (a) raw sha256 of the same content
    t0 = time.perf_counter()
    for _ in range(n):
        hashlib.sha256(content.encode()).hexdigest()
    sha_us = (time.perf_counter() - t0) / n * 1e6

    # (b) string construction alone (the content the scheme hashes)
    t0 = time.perf_counter()
    for i in range(n):
        f"{salt}{prev}{op}{time.time()}"
    fmt_us = (time.perf_counter() - t0) / n * 1e6

    # (c) full append on a prebuilt 10k chain (steady state, gc off)
    mgr, c = make_manager_and_container()
    for i in range(10_000):
        mgr.append_audit_event(c, op, dict(REAL_DETAILS))
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for i in range(n):
            mgr.append_audit_event(c, op, dict(REAL_DETAILS))
        full_us = (time.perf_counter() - t0) / n * 1e6
    finally:
        gc.enable()

    # (d) container._audit_trail double-write share: append with the
    # trail list pre-grown vs the same chain without trail writes is not
    # separable without editing the method — instead measure dict+list
    # append machinery directly as the residual sanity check.
    t0 = time.perf_counter()
    d = []
    for i in range(n):
        d.append({"op": op, "details": REAL_DETAILS, "time": time.time()})
    trail_us = (time.perf_counter() - t0) / n * 1e6

    print(f"  raw sha256(content):        {sha_us:7.2f} µs")
    print(f"  content f-string + time():  {fmt_us:7.2f} µs")
    print(f"  trail-entry dict+append:    {trail_us:7.2f} µs  (partial share)")
    print(f"  full append_audit_event:    {full_us:7.2f} µs  (10k chain)")
    print(
        f"  → hash itself is {sha_us/full_us*100:.0f}% of the append cost; "
        f"the rest is Python machinery (dicts, attribute lookups, the "
        f"duplicate _audit_trail write)"
    )
    return {
        "sha_us": sha_us,
        "fmt_us": fmt_us,
        "trail_us": trail_us,
        "full_us": full_us,
    }


# --------------------------------------------------------------------------
# 4. CONTEXT (overhead fraction vs the ops being audited — cited data)
# --------------------------------------------------------------------------
# BENCHMARK_RESULTS §20 (2026-08-14, ABI 2.0.0): wire call p50 307–357 µs;
# 2026-08-12 in-process call p50 92 µs. These are the operations each
# audit event accompanies.
def bench_context(append_us=None):
    print("== CONTEXT: audit overhead as a fraction of the audited op ==")
    if append_us is None:
        mgr, c = make_manager_and_container()
        for i in range(10_000):
            mgr.append_audit_event(c, "CAP-GRANT", dict(REAL_DETAILS))
        gc.collect()
        gc.disable()
        try:
            t0 = time.perf_counter()
            n = 100_000
            for i in range(n):
                mgr.append_audit_event(c, "CAP-GRANT", dict(REAL_DETAILS))
            append_us = (time.perf_counter() - t0) / n * 1e6
        finally:
            gc.enable()
    ipc = {
        "in-process call p50 (§20, 2026-08-12)": 92.0,
        "wire call p50 (§20, ABI 2.0.0 low)": 307.0,
        "wire call p50 (§20, ABI 2.0.0 high)": 357.0,
    }
    print(f"  measured append cost: {append_us:.2f} µs/event")
    for k, v in ipc.items():
        print(f"  vs {k}: {append_us/v*100:5.2f}% of the operation")
    print(
        "  (verify runs out-of-band, not per-call: it is a periodic or "
        "on-demand integrity sweep, so its O(n) cost does not sit on "
        "the grant/revoke hot path at all)"
    )
    return append_us


# --------------------------------------------------------------------------
# 5. TAMPER SCOPE (empirical property of the current hashing scheme)
# --------------------------------------------------------------------------
def bench_tamper_scope():
    print("== TAMPER SCOPE: what the chain hash actually covers ==")
    mgr, c = make_manager_and_container()
    for i in range(5):
        mgr.append_audit_event(c, "CAP-GRANT", {"capability": f"CAP-{i}"})
    v0 = mgr.verify_audit_integrity(c)
    print(f"  baseline: valid={v0['valid']} len={v0['chain_length']}")

    # (a) mutate DETAILS of a middle event IN THE CHAIN (the payload a
    # tamperer wants to change: which capability was granted, to whom)
    target = c._audit_hash_chain[2]
    orig_details = dict(target["details"])
    target["details"] = {"capability": "CAP-EVIL", "note": "history rewritten"}
    v1 = mgr.verify_audit_integrity(c)
    detected_details = not v1["valid"]
    # restore
    target["details"] = orig_details

    # (b) mutate OP (covered by the hash) — must be detected
    real_op = target["op"]
    target["op"] = "CAP-REVOKE"
    v2 = mgr.verify_audit_integrity(c)
    detected_op = not v2["valid"]
    target["op"] = real_op
    assert mgr.verify_audit_integrity(c)["valid"]

    # (c) mutate TIMESTAMP (covered) — must be detected
    real_ts = target["timestamp"]
    target["timestamp"] = real_ts + 0.001
    v3 = mgr.verify_audit_integrity(c)
    detected_ts = not v3["valid"]
    target["timestamp"] = real_ts

    # (d) strip the scheme marker — the marker is inside the hashed
    # content, so demoting a scheme-2 event to "legacy" is detected
    real_scheme = target.get("scheme")
    target.pop("scheme", None)
    v4 = mgr.verify_audit_integrity(c)
    detected_scheme_strip = not v4["valid"]
    target["scheme"] = real_scheme

    print(f"  mutate details  → detected: {detected_details}  (verify valid={v1['valid']})")
    print(f"  mutate op       → detected: {detected_op}")
    print(f"  mutate timestamp→ detected: {detected_ts}")
    print(f"  strip scheme    → detected: {detected_scheme_strip}")
    if detected_details:
        print(
            "  scheme 2 (ADR-0018 review §4.2): hashed content =\n"
            "  '2' + salt + prev_hash + op + timestamp + canonical-JSON\n"
            "  details — the details payload IS covered; stripping the\n"
            "  per-event scheme marker is itself detected. The _audit_trail\n"
            "  mirror stays an unhashed convenience copy (not part of the\n"
            "  tamper-evident record); the chain is the record.\n"
            "  (The §34e demonstration mutated the MIRROR — outside the\n"
            "  chain under BOTH schemes; the scheme-1 hole it recorded\n"
            "  was at the chain level and is closed by the scheme-2 fix.)"
        )
    else:
        print(
            "  hashed content = salt + prev_hash + op + timestamp; details are\n"
            "  stored alongside but NOT hashed — tampering with the details\n"
            "  payload is NOT detectable by verify_audit_integrity. Scope\n"
            "  limitation of the shipped scheme, recorded for the Architecture\n"
            "  Group (fix = hash the canonical details JSON too; spec decision,\n"
            "  not this benchmark's to make)."
        )
    return {
        "details_tamper_detected": detected_details,
        "op_tamper_detected": detected_op,
        "timestamp_tamper_detected": detected_ts,
        "scheme_strip_detected": detected_scheme_strip,
    }


# --------------------------------------------------------------------------
# 6. PERSISTENCE (the B1.4 snapshot requirement carries its number)
# --------------------------------------------------------------------------
def bench_persistence():
    print("== PERSISTENCE: append cost with per-append JSONL deltas ==")
    import tempfile
    d = tempfile.mkdtemp(prefix="nyrqis-audit-persist-bench-")
    os.environ["NYRQIS_AUDIT_SNAPSHOT_DIR"] = d
    try:
        n = 200
        mgr, c = make_manager_and_container()
        details = {"capability": "CAP-1", "target": "svc", "note": "x" * 32}
        mgr.append_audit_event(c, "WARMUP", details)
        t0 = time.perf_counter()
        for i in range(n):
            mgr.append_audit_event(
                c, "CAP-GRANT",
                {"capability": f"CAP-{i}", "target": "svc", "note": "x" * 32})
        total = (time.perf_counter() - t0) / n * 1e6
        path = os.path.join(d, mgr._audit_snapshot_filename(c.id))
        size = os.path.getsize(path)
        t1 = time.perf_counter()
        mgr.save_audit_snapshot(c)  # the compaction / full-rewrite path
        compact_us = (time.perf_counter() - t1) * 1e6
        print(f"  append + delta line: {total:.1f} µs/event (n={n})")
        print(f"  JSONL file at n={n + 1}: {size} bytes")
        print(f"  full compaction rewrite (once): {compact_us:.0f} µs")
        v = mgr.verify_audit_integrity(c)
        print(f"  post-snapshot verify: valid={v['valid']} len={v['chain_length']}")
        print(
            "  (per-append persistence appends one delta line — O(1),\n"
            "  file grows as the record grows; the whole-record rewrite\n"
            "  is compaction only. A first design rewrote the full file\n"
            "  per append and measured O(n) — 3800 µs at n=200 — which\n"
            "  is exactly why this section exists: the requirement\n"
            "  carries its number, and the number killed the bad design.)"
        )
        return {
            "append_with_delta_us": total,
            "jsonl_bytes": size,
            "compaction_us": compact_us,
        }
    finally:
        os.environ.pop("NYRQIS_AUDIT_SNAPSHOT_DIR", None)
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--floor", action="store_true")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--tamper-scope", action="store_true")
    ap.add_argument("--persistence", action="store_true")
    args = ap.parse_args()
    run_all = not any(
        (args.append, args.verify, args.floor, args.context,
         args.tamper_scope, args.persistence)
    )

    if run_all or args.tamper_scope:
        bench_tamper_scope()
    if run_all or args.append:
        bench_append()
    if run_all or args.verify:
        bench_verify()
    if run_all or args.floor:
        bench_floor()
    if run_all or args.context:
        bench_context()
    if run_all or args.persistence:
        bench_persistence()


if __name__ == "__main__":
    main()
