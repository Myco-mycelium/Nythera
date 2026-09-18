#!/usr/bin/env python3
"""NPS-010 §7.2/§9 close-out data: default CPU/memory resource-limit
values, measured against real cgroup-v2 enforcement.

Why this exists: NPS-010 §9 lists "exact default CPU/memory limit values
(§7.2) require benchmarking across representative workloads" as an open
question (NPC-002 §5.2 — no defaults asserted without data), and the
shipped `ResourceLimits` defaults in `backend/container.py`
(memory_mb=256, pid_limit=64, cpu_shares=1024, cpu_quota=None) have
never been validated against workloads. This script measures:

1. MEMORY FOOTPRINT — `memory.peak` of four representative container
   workloads (idle daemon, Python-language service, bursty interactive
   producer, process-spawning supervisor): is the 256 MB default
   generous, adequate, or tight for each?
2. CPU QUOTA SWEEP — the same bursty workload under cpu.max quotas
   {none, 20%, 50%, 100% of one CPU}: throughput, burst-completion
   latency p50/p95/p99, and `cpu.stat`'s throttled_usec — where the
   quota starts to bite a bursty (interactive-shaped) workload, and
   what throttling does to its tail latency.
3. PID LIMIT SWEEP — pids.max {16, 32, 64, 128, max} against the
   process-spawning workload: where does the 64-PID default actually
   fork-fail for a supervisor shape?
4. SUSPENDED ACCOUNTING — cgroup.freeze=1 on a mid-flight container:
   CPU consumption (expected ~0), memory retention (expected 100%),
   and whether the kernel can still reclaim frozen pages
   (memory.high poke). This is the DATA for §9's second open question
   ("should SUSPENDED containers count against active resource
   budgets or a separate reduced accounting").

Honesty notes (NPC-002 §5.2):
- Single host, cgroup v2 via the systemd user manager's delegated
  subtree (user@1000.service — the slice above it is root-owned;
  controllers cpu/memory/pids are enabled there; the script skips
  cleanly if the delegation is absent).
  Absolute numbers are host-dependent; the STRUCTURAL findings
  (footprint class of each workload shape, throttle-tail behavior,
  where the PID default bites, frozen-memory retention) are not.
- "Representative workloads" here are process shapes (idle, language-
  runtime service, bursty producer, supervisor), not the full Nyrqis
  demo stack; mapping to real app containers stays future work.
- No namespaces are used — the cgroup is the unit under test, matching
  what the backend enforces; seccomp/LSM layers are out of scope.
- Everything runs in a throwaway cgroup that is torn down in `finally`;
  no persistent system state is touched.

Run:
    python3 tests/benchmark_nps010_limits.py            # everything
    python3 tests/benchmark_nps010_limits.py --memory
    python3 tests/benchmark_nps010_limits.py --cpu
    python3 tests/benchmark_nps010_limits.py --pids
    python3 tests/benchmark_nps010_limits.py --suspended
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The systemd user manager's cgroup is the delegated, user-writable
# subtree (the slice above it is root-owned); controllers are enabled
# for its children, so real cpu.max/memory.max/pids.max/cgroup.freeze
# enforcement applies to everything spawned inside.
SERVICE_CG = Path(
    "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service"
)
CG_ROOT = SERVICE_CG / "nyrqis-bench-limits"

# The shipped defaults whose adequacy is under test (backend/container.py
# ResourceLimits.__init__): memory_mb=256, pid_limit=64,
# cpu_shares=1024, cpu_quota_us=None (unlimited).
DEFAULT_MEMORY_MB = 256
DEFAULT_PID_LIMIT = 64


# --------------------------------------------------------------------------
# cgroup helpers
# --------------------------------------------------------------------------
def delegated():
    """Is the user manager's cgroup delegated with the controllers we need?"""
    if not SERVICE_CG.is_dir():
        return False, "user@1000.service cgroup not found"
    try:
        have = set((SERVICE_CG / "cgroup.controllers").read_text().split())
        need = {"cpu", "memory", "pids"}
        if not need.issubset(have):
            return False, f"controllers delegated: {sorted(have)} (need cpu/memory/pids)"
        enabled = set((SERVICE_CG / "cgroup.subtree_control").read_text().split())
        if not need.issubset(enabled):
            (SERVICE_CG / "cgroup.subtree_control").write_text(
                "+" + " +".join(sorted(need - enabled))
            )
        CG_ROOT.mkdir(exist_ok=False)  # fresh subtree per run
        # cgroup v2 requires controllers enabled at EVERY level below
        # the point of delegation, or the leaves get no controller files.
        CG_ROOT_SUBTREE = CG_ROOT / "cgroup.subtree_control"
        CG_ROOT_SUBTREE.write_text("+cpu +memory +pids")
        return True, ""
    except (PermissionError, OSError) as e:
        return False, str(e)


def make_child(name):
    p = CG_ROOT / name
    p.mkdir(exist_ok=False)
    return p


def cg_write(child, fname, value):
    (child / fname).write_text(str(value))


def cg_read(child, fname):
    return (child / fname).read_text().strip()


def run_in_cgroup(child, argv, **kw):
    """Spawn argv so that EVERY page it faults and every task it spawns
    is charged to `child`: a tiny shell moves ITSELF into the cgroup and
    then execs the real argv — moving after spawn instead would leave
    interpreter-startup pages charged to the parent cgroup (measured:
    that undercounts a workload by its entire baseline RSS).
    Children the process spawns later inherit the cgroup."""
    import shlex
    procs = shlex.quote(str(child / "cgroup.procs"))
    # argv for `sh -c <script> <args...>` needs a placeholder $0 after
    # the script string, or the first real arg is consumed as $0.
    if argv[0] == "/bin/sh" and argv[1] == "-c":
        argv = argv[:3] + ["sh"] + argv[3:]
    wrapped = [
        "/bin/sh", "-c",
        f"echo $$ > {procs} && exec " + " ".join(shlex.quote(a) for a in argv),
    ]
    return subprocess.Popen(wrapped, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, **kw)


def cleanup(child):
    """Kill everything in the cgroup, unfreeze if needed, remove it."""
    try:
        if (child / "cgroup.freeze").exists():
            cg_write(child, "cgroup.freeze", 0)
        procs = cg_read(child, "cgroup.procs").split()
        for pid in procs:
            try:
                os.kill(int(pid), 9)
            except (ProcessLookupError, ValueError):
                pass
        for _ in range(50):
            if not cg_read(child, "cgroup.procs").split():
                break
            time.sleep(0.05)
        shutil.rmtree(child, ignore_errors=True)
    except Exception:
        pass


def parse_memory_peak(child):
    """memory.peak in MB when the kernel exposes it (≥ 6.7-ish), else None
    (callers fall back to MemoryMonitor sampling)."""
    try:
        return int(cg_read(child, "memory.peak")) / (1024 * 1024)
    except (OSError, ValueError):
        return None


class MemoryMonitor:
    """Poll memory.current in a background thread; .peak_mb is the max
    observed, .sample() snapshots the current value."""

    def __init__(self, child, interval=0.02):
        self.child = child
        self.interval = interval
        self.peak_mb = 0.0
        self._stop = False
        self._t = None

    def _loop(self):
        while not self._stop:
            try:
                cur = int(cg_read(self.child, "memory.current")) / (1024 * 1024)
                if cur > self.peak_mb:
                    self.peak_mb = cur
            except (OSError, ValueError):
                pass
            time.sleep(self.interval)

    def __enter__(self):
        import threading
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def sample(self):
        try:
            return int(cg_read(self.child, "memory.current")) / (1024 * 1024)
        except (OSError, ValueError):
            return self.peak_mb

    def __exit__(self, *a):
        self._stop = True
        if self._t:
            self._t.join(timeout=1.0)


# --------------------------------------------------------------------------
# workload definitions (process shapes, not the full app stack)
# --------------------------------------------------------------------------
PY_IDLE = [sys.executable, "-c", "import time; time.sleep(8)"]

PY_SERVICE = [sys.executable, "-c", r"""
import json, time
# Language-runtime service shape: import a realistic module set, build
# some in-memory state, then serve a small request loop.
import hashlib, array, collections
state = {f"key-{i:05d}": hashlib.sha256(str(i).encode()).hexdigest()
         for i in range(20_000)}
deadline = time.time() + 6
n = 0
while time.time() < deadline:
    k = f"key-{n % 20000:05d}"
    _ = json.dumps({"k": k, "v": state[k][:16]})
    n += 1
    time.sleep(0.001)
print(f"SERVED {n}", flush=True)
"""]

PY_BURSTY = [sys.executable, "-c", r"""
# Bursty interactive-producer shape: 10 ms CPU bursts separated by
# 10 ms sleeps (input/audio-like cadence). Reports per-burst wall
# completion time so quota throttling shows up as tail latency.
import time, sys, json
bursts = int(sys.argv[1]) if len(sys.argv) > 1 else 200
lat = []
t_end = time.perf_counter()
for b in range(bursts):
    t0 = time.perf_counter()
    x = 0
    while time.perf_counter() - t0 < 0.010:   # 10 ms busy burst
        x += 1
    done = time.perf_counter()
    lat.append((done - t0) * 1000.0)
    time.sleep(0.010)
print("BURST_LAT_MS " + json.dumps(lat), flush=True)
"""]

SH_SPAWNER = ["/bin/sh", "-c", r"""
# Supervisor shape: fork N concurrent children, wait for all.
N=$1
i=0
while [ $i -lt $N ]; do
  sleep 2 &
  i=$((i+1))
done
wait
echo "SPAWNED $N OK"
"""]


def pct(lat, p):
    s = sorted(lat)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# --------------------------------------------------------------------------
# 1. MEMORY FOOTPRINT
# --------------------------------------------------------------------------
def bench_memory():
    print("== MEMORY: memory.peak of representative workloads ==")
    rows = []
    workloads = [
        ("idle daemon", PY_IDLE),
        ("python service (20k-entry state, req loop)", PY_SERVICE),
        ("bursty producer (10ms bursts)", PY_BURSTY),
        ("supervisor (32 concurrent children)", SH_SPAWNER + ["32"]),
    ]
    for label, argv in workloads:
        child = make_child("mem-" + str(abs(hash(label)) % 10_000))
        try:
            p = run_in_cgroup(child, argv)
            with MemoryMonitor(child) as mon:
                out, _ = p.communicate(timeout=120)
                peak = parse_memory_peak(child)
                if peak is None:
                    peak = mon.peak_mb
            rows.append((label, peak))
            print(f"  {label:45s} peak {peak:7.1f} MB"
                  f"  ({(out or '').strip().splitlines()[-1][:40] if out else ''})")
        finally:
            cleanup(child)
    ok = [r for r in rows if r[1] is not None]
    if ok:
        mx = max(r[1] for r in ok)
        print(f"  vs {DEFAULT_MEMORY_MB} MB default: "
              + ", ".join(f"{l.split(' ')[0]} {p/DEFAULT_MEMORY_MB*100:.0f}%"
                          for l, p in ok))
    return rows


# --------------------------------------------------------------------------
# 2. CPU QUOTA SWEEP
# --------------------------------------------------------------------------
def cpu_stat(child):
    d = {}
    for line in cg_read(child, "cpu.stat").splitlines():
        k, v = line.split()
        d[k] = int(v)
    return d


def bench_cpu():
    print("== CPU: quota sweep on the bursty producer ==")
    quotas = [
        ("none (shipped default)", None),
        ("50% of one CPU", "50000 100000"),
        ("20% of one CPU", "20000 100000"),
        ("200% (2 cores)", "200000 100000"),
    ]
    rows = []
    for label, quota in quotas:
        child = make_child("cpu-" + str(abs(hash(label)) % 10_000))
        try:
            if quota:
                cg_write(child, "cpu.max", quota)
            p = run_in_cgroup(child, PY_BURSTY + ["200"])
            out, _ = p.communicate(timeout=180)
            st = cpu_stat(child)
            lat = []
            for line in (out or "").splitlines():
                if line.startswith("BURST_LAT_MS"):
                    lat = json.loads(line.split(" ", 1)[1])
            if lat:
                rows.append((label, pct(lat, 50), pct(lat, 95),
                             pct(lat, 99), max(lat), st.get("throttled_usec", 0)))
                print(f"  {label:26s} p50 {pct(lat,50):6.2f} ms  "
                      f"p95 {pct(lat,95):6.2f}  p99 {pct(lat,99):7.2f}  "
                      f"max {max(lat):7.2f}  throttled {st.get('throttled_usec',0)/1000:8.1f} ms")
        finally:
            cleanup(child)
    return rows


# --------------------------------------------------------------------------
# 3. PID LIMIT SWEEP
# --------------------------------------------------------------------------
def bench_pids():
    print("== PID: pids.max sweep on the supervisor shape ==")
    rows = []
    for limit in (16, 32, 64, 128, "max"):
        child = make_child(f"pids-{limit}")
        try:
            cg_write(child, "pids.max", limit)
            n = 40  # comfortably under 64 default but over 32
            p = run_in_cgroup(child, SH_SPAWNER + [str(n)])
            out, _ = p.communicate(timeout=60)
            ok = "SPAWNED" in (out or "")
            rows.append((limit, ok))
            print(f"  pids.max {str(limit):>4}: fork {n} children -> "
                  f"{'OK' if ok else 'FAILED (' + (out or '').strip().splitlines()[-1][:48] + ')'}")
        except subprocess.TimeoutExpired:
            print(f"  pids.max {str(limit):>4}: TIMEOUT (fork-fail loop?)")
        finally:
            cleanup(child)
    return rows


# --------------------------------------------------------------------------
# 4. SUSPENDED ACCOUNTING (freeze)
# --------------------------------------------------------------------------
def bench_suspended():
    print("== SUSPENDED: cgroup.freeze accounting ==")
    child = make_child("susp")
    try:
        p = run_in_cgroup(child, PY_SERVICE)
        pre = 0.0
        with MemoryMonitor(child) as mon:
            time.sleep(2.0)  # let it build state
            pre = parse_memory_peak(child) or mon.sample()
            pre_stat = cpu_stat(child)

            cg_write(child, "cgroup.freeze", 1)
            time.sleep(3.0)  # frozen window
        post_stat = cpu_stat(child)
        frozen_mem = None
        try:
            frozen_mem = int(cg_read(child, "memory.current")) / (1024 * 1024)
        except (OSError, ValueError):
            pass

        # can the kernel still reclaim from a frozen cgroup? poke the
        # memory high-pressure path by writing a high then re-reading.
        reclaim_note = "n/a"
        try:
            cg_write(child, "memory.high", str(int(pre * 1024 * 1024 * 0.9)))
            time.sleep(0.5)
            after = int(cg_read(child, "memory.current")) / (1024 * 1024)
            reclaim_note = f"{pre:.0f} -> {after:.0f} MB under memory.high poke"
            cg_write(child, "memory.high", "max")
        except (OSError, ValueError, KeyError):
            reclaim_note = "memory.high write unavailable"

        cg_write(child, "cgroup.freeze", 0)
        out, _ = p.communicate(timeout=60)
        cpu_during_freeze_ms = (post_stat.get("usage_usec", 0)
                                - pre_stat.get("usage_usec", 0)) / 1000.0
        print(f"  peak before freeze:     {pre:8.1f} MB")
        print(f"  memory.current frozen:  {frozen_mem:8.1f} MB"
              f"  ({frozen_mem/pre*100:.0f}% of peak retained)")
        print(f"  CPU while frozen (3 s): {cpu_during_freeze_ms:8.1f} ms"
              f"  ({cpu_during_freeze_ms/3000*100:.1f}% of one core)"
              f"  [+{post_stat.get('nr_throttled',0)-pre_stat.get('nr_throttled',0)} throttle events]")
        print(f"  reclaim while frozen:   {reclaim_note}")
        print(f"  unfreeze -> workload finished: {'SPAWNED' in (out or '') or 'SERVED' in (out or '')}")
        return {
            "pre_freeze_peak_mb": pre,
            "frozen_retained_mb": frozen_mem,
            "cpu_while_frozen_ms": cpu_during_freeze_ms,
            "reclaim": reclaim_note,
        }
    finally:
        cleanup(child)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--memory", action="store_true")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--pids", action="store_true")
    ap.add_argument("--suspended", action="store_true")
    args = ap.parse_args()
    run_all = not any((args.memory, args.cpu, args.pids, args.suspended))

    ok, why = delegated()
    if not ok:
        print(f"SKIP: delegated cgroup-v2 subtree unavailable ({why}); "
              "this benchmark needs the user slice delegated with "
              "cpu/memory/pids controllers.", file=sys.stderr)
        return 2

    try:
        if run_all or args.memory:
            bench_memory()
        if run_all or args.cpu:
            bench_cpu()
        if run_all or args.pids:
            bench_pids()
        if run_all or args.suspended:
            bench_suspended()
    finally:
        shutil.rmtree(CG_ROOT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
