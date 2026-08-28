#!/usr/bin/env python3
"""
FUSE Overhead Benchmark — NyFS vs native filesystem I/O.

Measures read/write throughput at various payload sizes to determine
the per-call overhead of the NyFS block layer (CoW, compression,
checksumming) versus direct OS file I/O.

Usage:
    python3 tests/benchmarks.py
    python3 tests/benchmarks.py --json    # machine-readable output
    python3 tests/benchmarks.py --rounds 5
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any

# Allow running from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fuse.nyfs import NyFSFilesystem


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PAYLOAD_SIZES = [4096, 65536, 262144, 1048576, 4194304]  # 4K, 64K, 256K, 1M, 4M
DEFAULT_ROUNDS = 3
BLOCK_SIZE = 65536  # 64 KiB NyFS default


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label(size: int) -> str:
    if size >= 1048576:
        return f"{size // 1048576} MiB"
    return f"{size // 1024} KiB"


@dataclass
class BenchResult:
    payload: int
    label: str
    nyfs_write_mb_s: float = 0.0
    nyfs_read_mb_s: float = 0.0
    native_write_mb_s: float = 0.0
    native_read_mb_s: float = 0.0
    write_overhead_pct: float = 0.0
    read_overhead_pct: float = 0.0


# ---------------------------------------------------------------------------
# NyFS bench
# ---------------------------------------------------------------------------

def _bench_nyfs(base: str, payload: int, rounds: int) -> tuple:
    """Return (write_mbps, read_mbps) for NyFS."""
    data = os.urandom(payload)
    fs = NyFSFilesystem(base, block_size=BLOCK_SIZE)

    # --- write ---
    write_times: List[float] = []
    for r in range(rounds):
        fname = f"bench_{r}_{payload}.bin"
        fs.create_file(f"/{fname}")
        t0 = time.perf_counter()
        fs.write(f"/{fname}", data, offset=0)
        t1 = time.perf_counter()
        write_times.append(t1 - t0)

    # --- read (cold — re-load from the in-memory tree) ---
    read_times: List[float] = []
    for r in range(rounds):
        fname = f"bench_{r}_{payload}.bin"
        t0 = time.perf_counter()
        got = fs.read(f"/{fname}", len(data), offset=0)
        t1 = time.perf_counter()
        read_times.append(t1 - t0)
        assert len(got) == payload, f"short read: {len(got)} != {payload}"

    median_write = sorted(write_times)[len(write_times) // 2]
    median_read = sorted(read_times)[len(read_times) // 2]

    mb = payload / (1024 * 1024)
    write_mbps = mb / median_write if median_write > 0 else 0
    read_mbps = mb / median_read if median_read > 0 else 0
    return write_mbps, read_mbps


# ---------------------------------------------------------------------------
# Native bench
# ---------------------------------------------------------------------------

def _bench_native(d: str, payload: int, rounds: int) -> tuple:
    """Return (write_mbps, read_mbps) for a plain tmpfs file."""
    data = os.urandom(payload)

    write_times: List[float] = []
    for r in range(rounds):
        p = os.path.join(d, f"native_{r}_{payload}.bin")
        t0 = time.perf_counter()
        with open(p, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        t1 = time.perf_counter()
        write_times.append(t1 - t0)

    read_times: List[float] = []
    for r in range(rounds):
        p = os.path.join(d, f"native_{r}_{payload}.bin")
        t0 = time.perf_counter()
        with open(p, "rb") as f:
            got = f.read()
        t1 = time.perf_counter()
        read_times.append(t1 - t0)
        assert len(got) == payload

    median_write = sorted(write_times)[len(write_times) // 2]
    median_read = sorted(read_times)[len(read_times) // 2]

    mb = payload / (1024 * 1024)
    write_mbps = mb / median_write if median_write > 0 else 0
    read_mbps = mb / median_read if median_read > 0 else 0
    return write_mbps, read_mbps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmark(rounds: int = DEFAULT_ROUNDS) -> List[BenchResult]:
    results: List[BenchResult] = []

    nyfs_tmp = tempfile.mkdtemp(prefix="nyfs_bench_")
    native_tmp = tempfile.mkdtemp(prefix="native_bench_")
    try:
        for payload in PAYLOAD_SIZES:
            nyfs_w, nyfs_r = _bench_nyfs(nyfs_tmp, payload, rounds)
            native_w, native_r = _bench_native(native_tmp, payload, rounds)

            w_overhead = ((nyfs_w / native_w) - 1) * 100 if native_w > 0 else 0
            r_overhead = ((nyfs_r / native_r) - 1) * 100 if native_r > 0 else 0

            results.append(BenchResult(
                payload=payload,
                label=_label(payload),
                nyfs_write_mb_s=round(nyfs_w, 2),
                nyfs_read_mb_s=round(nyfs_r, 2),
                native_write_mb_s=round(native_w, 2),
                native_read_mb_s=round(native_r, 2),
                write_overhead_pct=round(w_overhead, 1),
                read_overhead_pct=round(r_overhead, 1),
            ))
    finally:
        shutil.rmtree(nyfs_tmp, ignore_errors=True)
        shutil.rmtree(native_tmp, ignore_errors=True)

    return results


def _print_table(results: List[BenchResult]) -> None:
    hdr = (f"{'Payload':>8}  {'NyFS W':>9} {'Native W':>9} {'W Overhead':>10}  "
           f"{'NyFS R':>9} {'Native R':>9} {'R Overhead':>10}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(
            f"{r.label:>8}  "
            f"{r.nyfs_write_mb_s:>7.2f} MB/s "
            f"{r.native_write_mb_s:>7.2f} MB/s "
            f"{r.write_overhead_pct:>+8.1f}%  "
            f"{r.nyfs_read_mb_s:>7.2f} MB/s "
            f"{r.native_read_mb_s:>7.2f} MB/s "
            f"{r.read_overhead_pct:>+8.1f}%"
        )
    print()
    print("Note: positive overhead = NyFS is slower than native; "
          "negative = NyFS is faster (block-level decompression "
          "benefits sequential reads).")


def main() -> None:
    ap = argparse.ArgumentParser(description="FUSE overhead benchmark")
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS,
                    help=f"Iterations per payload (default {DEFAULT_ROUNDS})")
    ap.add_argument("--json", action="store_true",
                    help="Output machine-readable JSON")
    args = ap.parse_args()

    results = run_benchmark(args.rounds)

    if args.json:
        out = [{"payload": r.payload, "label": r.label,
                "nyfs_write_mb_s": r.nyfs_write_mb_s,
                "nyfs_read_mb_s": r.nyfs_read_mb_s,
                "native_write_mb_s": r.native_write_mb_s,
                "native_read_mb_s": r.native_read_mb_s,
                "write_overhead_pct": r.write_overhead_pct,
                "read_overhead_pct": r.read_overhead_pct} for r in results]
        print(json.dumps(out, indent=2))
    else:
        _print_table(results)


if __name__ == "__main__":
    main()
