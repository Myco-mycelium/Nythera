#!/usr/bin/env python3
"""ADR-0013 EEVDF tuning data: time-slice, weight curve, RT admission.

Why this exists: ADR-0013 fixes the algorithm family (EEVDF-derived with
a real-time class) but leaves its tuning parameters unspecified pending
benchmark data (NPC-002 §5.2, REPOSITORY_STATE Next Actions #4). This
script produces that data with a faithful discrete-time EEVDF model
(same quantities Linux 6.6 uses: eligibility via lag, earliest eligible
virtual deadline first, virtual deadline = virtual eligible time +
request length / weight).

What this measures (three tuning questions):

1. BASE SLICE — default request slice 0.75/1.5/3/6/12 ms with one
   interactive task (small periodic requests, the input/frame shape)
   among N background tasks issuing large back-to-back requests. The
   interactive task's latency p50/p95/max is the cost the default slice
   imposes on exactly the workloads NTM-000's gaming goal protects.

2. WEIGHT CURVE — nice→weight mapping. Compares the CFS-legacy
   exponential curve (1024/1.25^nice), a linear curve, and the Linux
   6.6 weights table for latency isolation (weight -5 vs weight 0 vs
   weight +5 interactive tasks under background load) and for share
   accuracy (two competing CPU hogs at weights 10:1 — measured share
   vs nominal 10:1).

3. RT ADMISSION — the real-time class at a fixed bandwidth reserve.
   RT tasks with a known period/computation demand run at increasing
   total utilization up to and beyond the reserve; we record RT
   deadline misses and the fair-class latency degradation. This gives
   the admission numbers: how much RT load the reserve absorbs
   miss-free, and what the fair class pays at each RT load level.

Honesty notes (NPC-002 §5.2):
- This is a SIMULATION of the scheduling model, not on-target hardware
  numbers. It is the right instrument for RELATIVE parameter choice
  (slice A vs slice B under identical load), not for absolute latency
  prediction; the ADR's revisit clause (real container/IPC load
  patterns) still applies to any eventual default chosen from this.
- The model omits cache effects, wake-up preemption grace, and
  multi-core run queues (single run queue = the conservative case for
  interactive latency).
- Results belong in tests/BENCHMARK_RESULTS.md.

Run:
    python3 tests/benchmark_adr0013.py            # all three sections
    python3 tests/benchmark_adr0013.py --slice
    python3 tests/benchmark_adr0013.py --weights
    python3 tests/benchmark_adr0013.py --rt
"""

import argparse
import heapq
import time

US = 1  # all times in microseconds

# Linux 6.6 weight table (nice -20..19), abbreviated to the ones used.
LINUX_WEIGHTS = {
    -5: 3358, -4: 2724, -3: 2210, -2: 1792, -1: 1454,
    0: 1024, 1: 820, 2: 655, 3: 526, 4: 423, 5: 335,
}


def cfs_weight(nice: int) -> int:
    """CFS-legacy exponential curve: 1024 / 1.25^nice."""
    return max(15, int(round(1024 / (1.25 ** nice))))


def linear_weight(nice: int) -> int:
    """Linear curve, normalized to 1024 at nice 0."""
    return max(15, 1024 // (1 + nice) if nice >= 0 else 1024 * (1 - nice))


def weight_fn(name: str):
    return {"cfs": cfs_weight, "linear": linear_weight,
            "linux": lambda n: LINUX_WEIGHTS.get(n, 1024)}[name]


class Task:
    __slots__ = ("name", "kind", "weight", "period", "req_len", "vruntime",
                 "arrivals", "completions", "next_arrival", "remaining",
                 "overruns")

    def __init__(self, name, kind, weight, period, req_len):
        self.name = name
        self.kind = kind            # "interactive" | "background" | "rt"
        self.weight = weight
        self.period = period        # inter-arrival period (interactive/rt)
        self.req_len = req_len      # request service length in us
        self.vruntime = 0           # virtual runtime accumulated
        self.arrivals = []          # arrival timestamps
        self.completions = []       # completion timestamps
        self.next_arrival = 0
        self.remaining = 0          # remaining service of current request
        self.overruns = 0           # periods that arrived before completion


class EEVDFSim:
    """Single-CPU discrete-event EEVDF + RT-priority simulation.

    Fair-class requests run to completion (or to the next arrival,
    which re-picks — EEVDF's preempt-on-earlier-deadline rule at request
    granularity). Background hogs re-arm immediately on completion
    (continuous work). Interactive/RT tasks re-arm on their period via
    an event heap; a request still in flight at the next period counts
    as an overrun (a missed frame — the realistic input/frame-pacing
    behavior) and is NOT queued again.
    """

    _seq = 0

    def __init__(self, duration_us=2_000_000):
        self.duration = duration_us
        self.tasks = []
        self.now = 0

    def add(self, task):
        self.tasks.append(task)
        return task

    def _vdeadline(self, t):
        return t.vruntime + (t.remaining * 1024) // max(t.weight, 1)

    def _pick(self):
        eligible = [t for t in self.tasks if t.remaining > 0]
        if not eligible:
            return None
        rt = [t for t in eligible if t.kind == "rt"]
        if rt:
            return min(rt, key=self._vdeadline)
        return min(eligible, key=self._vdeadline)

    def run(self):
        heap = []
        seq = 0
        for t in self.tasks:
            if t.kind == "background":
                t.remaining = t.req_len  # continuous work starts now
            else:
                heapq.heappush(heap, (t.next_arrival, seq, t))
                seq += 1
        self.now = 0
        end = self.duration
        while self.now < end:
            next_arrival = heap[0][0] if heap else end
            cur = self._pick()
            if cur is None:
                if next_arrival >= end:
                    break
                self.now = next_arrival
                while heap and heap[0][0] <= self.now:
                    _, _, t = heapq.heappop(heap)
                    self._arrive(t, self.now, heap, seq)
                    seq += 1
                continue
            run_to = min(next_arrival, end, self.now + cur.remaining)
            step = run_to - self.now
            if step <= 0:
                # arrival at this instant; process it and re-pick
                while heap and heap[0][0] <= self.now:
                    _, _, t = heapq.heappop(heap)
                    self._arrive(t, self.now, heap, seq)
                    seq += 1
                continue
            cur.remaining -= step
            cur.vruntime += (step * 1024) // max(cur.weight, 1)
            self.now = run_to
            if cur.remaining <= 0:
                cur.completions.append(self.now)
                if cur.kind == "background":
                    cur.remaining = cur.req_len  # continuous work re-arms
            while heap and heap[0][0] <= self.now:
                _, _, t = heapq.heappop(heap)
                self._arrive(t, self.now, heap, seq)
                seq += 1
        return self

    def _arrive(self, t, when, heap, seq):
        if t.kind == "background":
            return  # hogs re-arm inline on completion, not via events
        if t.remaining > 0:
            t.overruns += 1  # missed frame: previous request still pending
        else:
            t.remaining = t.req_len
            t.arrivals.append(when)
        heapq.heappush(heap, (when + t.period, seq, t))

    def latencies(self, task):
        """Response latency per request: completion - arrival (paired)."""
        return [c - a for a, c in zip(task.arrivals, task.completions)]


def _percentiles(values):
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "n": 0}
    s = sorted(values)
    return {
        "p50": s[len(s) // 2],
        "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
        "max": s[-1],
        "n": len(s),
    }


def section_slice():
    """Interactive latency vs ITS OWN request size (the EEVDF slice
    quantity) under equal-weight background load. In EEVDF a request's
    virtual deadline is vruntime + len/weight, and a pending request is
    preempted only by an earlier deadline — so the latency an
    interactive task experiences is dominated by its own request length
    (plus at most one in-flight background request's tail). The tuning
    insight this section produces: interactive classes must keep
    requests small; background throughput is protected by weight, not
    by giving interactive tasks long slices."""
    print("## 1. Interactive request-size sweep (1 interactive + 3 background hogs, 2 s sim)")
    print()
    print("| request | interactive p50 | p95 | max | overruns | requests |")
    print("|--------:|----------------:|----:|----:|---------:|---------:|")
    for slice_us in (750, 1500, 3000, 6000, 12000):
        sim = EEVDFSim(duration_us=2_000_000)
        inter = sim.add(Task("input", "interactive", 1024, 10_000, slice_us))
        for i in range(3):
            sim.add(Task(f"bg{i}", "background", 1024, 0, 20_000))
        sim.run()
        st = _percentiles(sim.latencies(inter))
        print(f"| {slice_us/1000:g} ms | {st['p50']} | {st['p95']} | "
              f"{st['max']} | {inter.overruns} | {st['n']} |")


def section_weights():
    """Isolation and share accuracy across weight curves."""
    print("## 2. Weight-curve comparison")
    print()
    print("### 2a. Interactive latency isolation (nice -5/0/+5 among 3 hogs)")
    print()
    print("| curve | nice | p50 | p95 | max |")
    print("|-------|-----:|----:|----:|----:|")
    for curve in ("cfs", "linear", "linux"):
        wf = weight_fn(curve)
        for nice in (-5, 0, 5):
            sim = EEVDFSim(duration_us=1_000_000)
            inter = sim.add(Task("input", "interactive", wf(nice),
                                 10_000, 1_500))
            for i in range(3):
                sim.add(Task(f"bg{i}", "background", 1024, 0, 20_000))
            sim.run()
            st = _percentiles(sim.latencies(inter))
            print(f"| {curve} | {nice:+d} | {st['p50']} | {st['p95']} | "
                  f"{st['max']} |")
    print()
    print("### 2b. Share accuracy: two hogs, nominal 10:1 (measured share)")
    print()
    print("| curve | w_hi share | expected |")
    print("|-------|-----------:|---------:|")
    for curve in ("cfs", "linear", "linux"):
        wf = weight_fn(curve)
        sim = EEVDFSim(duration_us=1_000_000)
        a = sim.add(Task("hi", "background", wf(-5), 0, 20_000))
        b = sim.add(Task("lo", "background", wf(5), 0, 20_000))
        sim.run()
        hi, lo = len(a.completions), len(b.completions)
        share = hi / (hi + lo) if hi + lo else 0.0
        expected = wf(-5) / (wf(-5) + wf(5))
        print(f"| {curve} | {share:.3f} | {expected:.3f} |")


def section_rt():
    """RT admission: what unbounded RT load costs the fair class.

    The simulation deliberately implements NO admission control — RT
    always preempts — so the table IS the argument for the reserve: it
    shows the RT load level where the fair class degrades and where RT
    itself starts missing, which is the data the admission limit must
    be chosen from (the reserve value itself is an Architecture Group
    decision, NPS-010 §7.2)."""
    print("## 3. RT load vs fair-class cost (NO admission control — the data the reserve must be chosen from)")
    print()
    print("| RT util | RT misses | fair p50 | fair p95 | fair max |")
    print("|--------:|----------:|---------:|---------:|---------:|")
    rt_period = 20_000   # 20 ms period per RT task
    rt_comp = 4_000      # 4 ms compute per period -> 20% each
    for n_rt in (0, 1, 2, 3, 4, 5):  # 0%..100% utilization
        sim = EEVDFSim(duration_us=1_000_000)
        rt_tasks = []
        for i in range(n_rt):
            t = sim.add(Task(f"rt{i}", "rt", 0, rt_period, rt_comp))
            t.next_arrival = i * 1000  # phase-offset
            rt_tasks.append(t)
        fair = sim.add(Task("input", "interactive", 1024, 10_000, 1_500))
        for i in range(2):
            sim.add(Task(f"bg{i}", "background", 1024, 0, 20_000))
        sim.run()
        misses = sum(t.overruns for t in rt_tasks) + sum(
            1
            for t in rt_tasks
            for a, c in zip(t.arrivals, t.completions)
            if c - a > t.period  # deadline = next period boundary
        )
        st = _percentiles(sim.latencies(fair))
        util = n_rt * rt_comp / rt_period
        p50 = st['p50'] if st['n'] else "STARVED"
        p95 = st['p95'] if st['n'] else "-"
        mx = st['max'] if st['n'] else "-"
        print(f"| {util:.0%} | {misses} | {p50} | {p95} | "
              f"{mx} |")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slice", action="store_true")
    parser.add_argument("--weights", action="store_true")
    parser.add_argument("--rt", action="store_true")
    args = parser.parse_args()
    none = not (args.slice or args.weights or args.rt)
    t0 = time.perf_counter()
    if args.slice or none:
        section_slice()
        print()
    if args.weights or none:
        section_weights()
        print()
    if args.rt or none:
        section_rt()
        print()
    print(f"(sim wall time: {time.perf_counter() - t0:.1f}s)")


if __name__ == "__main__":
    main()
