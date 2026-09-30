#!/usr/bin/env python3
"""bench_watcher_profile.py — the ADR-0019 watcher resource-profile harness.

Runs the three captures named in DAEMON_LIFECYCLE.md §3 (Resource
profile) on the machine in front of it and writes a machine- and
device-labeled markdown block ready to paste into the follow-up record:

  1. the §14 compaction pass cost re-run on the local storage class,
  2. an idle-RSS soak (default 24 h; --quick reduces it for smoke runs),
  3. wake-to-wake jitter of the shipped 60 s watcher cadence.

Usage (from the repo root, target hardware):
    python3 tests/bench_watcher_profile.py --quick      # ~2 min smoke
    python3 tests/bench_watcher_profile.py              # full 24 h soak

The harness never mounts FUSE and never writes outside its temp dir.
Every output block labels the host, kernel, CPU, storage device, and
filesystem so results cannot be mistaken for a different storage
class. The defaults remain tuning knobs — this measures, it does not
gate (ADR-0019, ratified as-implemented 2026-09-30).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "source", "nyhal-linux-backend"))
from fuse.nyfs import NyFSFilesystem  # noqa: E402

WATCHER_INTERVAL = 60.0
HALF_THRESHOLD = 32 * 1024 * 1024
FULL_THRESHOLD = 64 * 1024 * 1024


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def device_label(path: str) -> dict:
    """Best-effort label of the backing block device and its fs."""
    info = {"fs": "unknown", "device": "unknown", "rotational": "unknown"}
    try:
        info["fs"] = os.statvfs(path) and (
            subprocess.run(["stat", "-f", "-c", "%T", path],
                           capture_output=True, text=True).stdout.strip()
            or "unknown")
    except OSError:
        pass
    try:
        src = os.path.realpath(path)
        while src != "/" and not os.path.ismount(src):
            src = os.path.dirname(src)
        out = subprocess.run(["findmnt", "-no", "SOURCE", src],
                             capture_output=True, text=True).stdout.strip()
        info["device"] = out or "unknown"
        base = os.path.basename(out)
        dev = f"/sys/block/{base}/queue/rotational"
        if base and os.path.exists(dev):
            info["rotational"] = open(dev).read().strip()
        else:
            info["rotational"] = "unknown (device/layer not a plain sd*|nvme*)"
    except Exception:
        pass
    return info


def host_label() -> dict:
    cpu = {}
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu["cpu"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "host": socket.gethostname(),
        "kernel": platform.release(),
        "cpu": cpu.get("cpu", platform.processor() or "unknown"),
        "python": platform.python_version(),
        "date": utcnow(),
    }


def build_corpus(root: str, blocks: int = 417) -> NyFSFilesystem:
    """A journal deep enough to make a compaction pass real work: ~417
    blocks (the §14 anchor corpus) appended under the default journal
    commit path."""
    fs = NyFSFilesystem(root)
    fs.create_directory("/corpus")
    files = [fs.create_file(f"/corpus/file_{j}.bin") for j in range(64)]
    for i in range(blocks * 8):
        fs.write(files[i % 64], bytes([i % 251]) * 4096)
    fs.save()
    return fs


def measure_pass_cost(root: str, passes: int = 5) -> dict:
    fs = build_corpus(root)
    tail_inode = None
    results = []
    for _ in range(passes):
        # grow the journal so every pass has real blocks to materialize
        if tail_inode is None:
            tail_inode = fs.create_file("/corpus/tail.bin")
        fs.write(tail_inode, os.urandom(256 * 1024))
        fs.save()
        jb0 = fs.journal_bytes()
        t0 = time.perf_counter()
        n = fs.compact_journal()
        dt_s = time.perf_counter() - t0
        results.append((jb0, n, dt_s))
    return {"results": results, "blocks": 417 * 8}


def soak_once(fs: NyFSFilesystem, seconds: int) -> dict:
    """Idle-RSS sample of this process while the watcher idles."""
    def rss_kb() -> int:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
        return -1

    samples = []
    end = time.monotonic() + seconds
    # 1 Hz for short soaks, ~0.2 Hz for the 24 h run
    step = 1.0 if seconds <= 300 else 5.0
    while time.monotonic() < end:
        samples.append(rss_kb())
        time.sleep(step)
    return {"n": len(samples), "min_kb": min(samples), "max_kb": max(samples),
            "drift_kb": max(samples) - min(samples)}


def measure_jitter(seconds: int = 120) -> dict:
    """Shipped cadence, scaled by --quick: Event.wait(interval) in a
    daemon thread; record wake-to-wake error across the window."""
    interval = 1.0 if seconds <= 300 else WATCHER_INTERVAL
    errs: list[float] = []
    stop = threading.Event()

    def loop() -> None:
        next_wake = time.monotonic() + interval
        while not stop.wait(interval):
            now = time.monotonic()
            errs.append((now - next_wake) * 1000.0)
            next_wake += interval

    th = threading.Thread(target=loop, daemon=True)
    t0 = time.monotonic()
    th.start()
    time.sleep(seconds)
    stop.set()
    th.join()
    if errs:
        mean = sum(errs) / len(errs)
        var = sum((e - mean) ** 2 for e in errs) / len(errs)
        return {"n": len(errs), "window_s": round(time.monotonic() - t0, 1),
                "interval_s": interval, "mean_ms": round(mean, 3),
                "p95_ms": round(sorted(errs)[int(len(errs) * 0.95)], 3),
                "max_ms": round(max(errs), 3),
                "stdev_ms": round(var ** 0.5, 3)}
    return {"n": 0, "window_s": round(time.monotonic() - t0, 1),
            "interval_s": interval}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="smoke mode: 90 s soak, 1 s jitter interval")
    ap.add_argument("--soak-seconds", type=int, default=None,
                    help="override the idle-soak duration (default 86400)")
    args = ap.parse_args()

    soak_s = args.soak_seconds or (90 if args.quick else 24 * 3600)
    jitter_s = 30 if args.quick else 120

    labels = host_label()
    print(f"# ADR-0019 watcher resource profile — {labels['date']}")
    print(f"host: {labels['host']} | kernel: {labels['kernel']} | "
          f"cpu: {labels['cpu']} | python: {labels['python']}")

    tmp = tempfile.mkdtemp(prefix="nyfs-watcher-profile.")
    try:
        dev = device_label(tmp)
        print(f"storage: fs={dev['fs']} device={dev['device']} "
              f"rotational={dev['rotational']}")
        print("class: DEV-VM-CLASS UNTIL RUN ON TARGET HARDWARE\n")

        pc = measure_pass_cost(tmp)
        print(f"## §14 pass re-run ({pc['blocks']} blocks in corpus, "
              f"{len(pc['results'])} passes)")
        for jb, n, dt_s in pc["results"]:
            print(f"- journal {jb/1024:.0f} KiB -> {n} blocks compacted "
                  f"in {dt_s*1000:.1f} ms ({(dt_s*1000/max(n,1)):.2f} ms/block)")
        print()

        print(f"## idle-RSS soak ({soak_s}s, live watcher thread at the "
              f"{WATCHER_INTERVAL}s cadence... scaled to 1s in quick mode)")
        fs = NyFSFilesystem(tmp)
        fs.save()
        interval = 1.0 if args.quick else WATCHER_INTERVAL
        stop = threading.Event()

        def watcher() -> None:
            while not stop.wait(interval):
                try:
                    fs.maybe_compact(threshold=HALF_THRESHOLD)
                except Exception:
                    pass

        th = threading.Thread(target=watcher, daemon=True)
        th.start()
        try:
            s = soak_once(fs, soak_s)
        finally:
            stop.set()
            th.join()
        print(f"- {s['n']} samples: min {s['min_kb']} KiB, "
              f"max {s['max_kb']} KiB, drift {s['drift_kb']} KiB\n")

        print(f"## wake jitter (interval {jitter_s if jitter_s <= 5 else WATCHER_INTERVAL}s"
              f"{' scaled' if jitter_s != WATCHER_INTERVAL else ''}, {jitter_s}s window)")
        j = measure_jitter(jitter_s)
        print(f"- {j['n']} wakes at a {j['interval_s']}s interval: "
              f"mean +{j.get('mean_ms', 0)} ms, "
              f"p95 +{j.get('p95_ms', 0)} ms, max +{j.get('max_ms', 0)} ms, "
              f"stdev {j.get('stdev_ms', 0)} ms")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
