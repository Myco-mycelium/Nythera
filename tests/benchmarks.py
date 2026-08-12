#!/usr/bin/env python3
"""First-pass benchmarks for the Nyrqis Linux Backend.

Implements the available subset of `tests/BENCHMARK_PLAN.md`:

- §1 IPC round-trip latency (NPS-003 §3): the `call` primitive, p50/p95/p99.
- §4 FUSE overhead (ADR-0016): NyFS operation-handler throughput vs native
  file I/O on the same disk, as a proxy for the real FUSE-vs-ext4
  comparison (which requires a live FUSE mount).

Honesty notes (NPC-002 §5.2):
- These are FIRST-PASS microbenchmarks on this host, not the full plan
  methodology (which requires two containers, load variants, and a real
  FUSE mount). The in-process IPC path excludes the deferred Unix-socket/
  shared-memory transport, so these numbers bound the control-plane cost,
  not the final IPC wire cost.
- Run: `python3 tests/benchmarks.py`. Results belong in
  `tests/BENCHMARK_RESULTS.md`, not in this file.
"""

import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "source" / "nyhal-linux-backend"))

from ipc.core import IPCManager, TokenBucket  # noqa: E402
from fuse.nyfs import NyFSFilesystem, NyFSOperations  # noqa: E402

IPC_ITERATIONS = 20000
FS_TOTAL_BYTES = 8 * 1024 * 1024
FS_CHUNK = 4096
SMALL_FILES = 1000


def percentile(sorted_values, pct):
    idx = int(len(sorted_values) * pct)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _spawn_responder(mgr, endpoint, payload_size, stop):
    def responder():
        while not stop.is_set():
            msg = mgr.receive(endpoint.endpoint_id, timeout_s=0.1)
            if msg is not None and msg.message_type.value == "call":
                mgr.reply(msg.message_id, b"r" * payload_size)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    return thread


def benchmark_ipc_roundtrip(n=IPC_ITERATIONS, payload_size=64):
    """p50/p95/p99 of the `call` primitive (BENCHMARK_PLAN §1).

    The endpoints are given a deliberately high token budget so the
    measured distribution is the primitive's control-plane latency, not
    the default rate limiter (whose throttle behaviour is measured
    separately by ``benchmark_default_bucket``).
    """
    mgr = IPCManager()
    roomy = TokenBucket(bucket_size=1_000_000, tokens_per_second=1_000_000.0)
    svc = mgr.create_endpoint("container-svc", "ep-svc")
    cli = mgr.create_endpoint("container-cli", "ep-cli")
    svc.rate_limit = roomy  # measure the primitive, not the limiter
    cli.rate_limit = roomy
    payload = b"x" * payload_size
    stop = threading.Event()
    thread = _spawn_responder(mgr, svc, payload_size, stop)
    try:
        for _ in range(200):  # Warmup.
            mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=5.0)
        latencies = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=5.0)
            latencies.append((time.perf_counter_ns() - t0) / 1000.0)  # microseconds
    finally:
        stop.set()
        thread.join(timeout=1.0)
    latencies.sort()
    return {
        "iterations": n,
        "payload_bytes": payload_size,
        "p50_us": round(percentile(latencies, 0.50), 2),
        "p95_us": round(percentile(latencies, 0.95), 2),
        "p99_us": round(percentile(latencies, 0.99), 2),
        "mean_us": round(statistics.mean(latencies), 2),
        "max_us": round(latencies[-1], 2),
    }


def benchmark_default_bucket(duration_s=2.0, payload_size=64):
    """Sustained round-trips under the DEFAULT token bucket (ADR-0009 §3).

    ``create_endpoint`` defaults to ``TokenBucket(100, 50/s)``. This
    measures how many call round-trips per second that actually sustains
    (and how many are throttled) — the raw data point ADR-0009's default
    parameter decision needs.
    """
    mgr = IPCManager()
    svc = mgr.create_endpoint("container-svc", "ep-svc")  # default bucket
    cli = mgr.create_endpoint("container-cli", "ep-cli")  # default bucket
    payload = b"x" * payload_size
    stop = threading.Event()
    thread = _spawn_responder(mgr, svc, payload_size, stop)
    succeeded = 0
    throttled = 0
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        # A throttled call returns None (send_message refused a token) —
        # it does not raise, so None must be counted as throttled.
        if mgr.call("container-cli", svc.endpoint_id, payload, timeout_s=0.5) is None:
            throttled += 1
        else:
            succeeded += 1
    stop.set()
    thread.join(timeout=1.0)
    return {
        "duration_s": duration_s,
        "succeeded": succeeded,
        "throttled": throttled,
        "sustained_calls_per_sec": round(succeeded / duration_s, 1),
        "throttled_per_sec": round(throttled / duration_s, 1),
    }


def benchmark_nyfs_vs_native():
    """NyFS operations-layer throughput vs native file I/O (§4 proxy)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Native baseline: sequential write + read on the same disk.
        native_path = os.path.join(tmp, "native.bin")
        data = os.urandom(FS_CHUNK)
        t0 = time.perf_counter()
        with open(native_path, "wb") as fh:
            for _ in range(FS_TOTAL_BYTES // FS_CHUNK):
                fh.write(data)
        native_write_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        with open(native_path, "rb") as fh:
            while fh.read(FS_CHUNK):
                pass
        native_read_s = time.perf_counter() - t0

        # NyFS through the FUSE operation handlers (same disk, CoW +
        # Zstd compression active). NOTE: write()/read() are whole-file
        # operations in this implementation (one merged block per inode),
        # so each 4 KiB op compresses/decompresses the full 8 MiB buffer —
        # the numbers below measure that path, not per-block I/O.
        fs = NyFSFilesystem(os.path.join(tmp, "nyfs"))
        ops = NyFSOperations(fs)
        ops.mknod("/bench.bin", 0o644, 0)
        fh = ops.open("/bench.bin", os.O_WRONLY)
        t0 = time.perf_counter()
        for _ in range(FS_TOTAL_BYTES // FS_CHUNK):
            ops.write("/bench.bin", data, FS_CHUNK, fh)
        nyfs_write_s = time.perf_counter() - t0
        ops.release("/bench.bin", fh)
        t0 = time.perf_counter()
        for i in range(FS_TOTAL_BYTES // FS_CHUNK):
            ops.read("/bench.bin", FS_CHUNK, (i * FS_CHUNK) % FS_TOTAL_BYTES)
        nyfs_read_s = time.perf_counter() - t0

        # Small-file creation (many game assets, NPS-006 §5).
        t0 = time.perf_counter()
        for i in range(SMALL_FILES):
            ops.mknod(f"/asset_{i}.dat", 0o644, 0)
        small_create_s = time.perf_counter() - t0

    def mbps(total, seconds):
        return round(total / seconds / (1024 * 1024), 2) if seconds else float("inf")

    return {
        "total_bytes": FS_TOTAL_BYTES,
        "small_files": SMALL_FILES,
        "native_write_mbps": mbps(FS_TOTAL_BYTES, native_write_s),
        "native_read_mbps": mbps(FS_TOTAL_BYTES, native_read_s),
        "nyfs_write_mbps": mbps(FS_TOTAL_BYTES, nyfs_write_s),
        "nyfs_read_mbps": mbps(FS_TOTAL_BYTES, nyfs_read_s),
        "write_overhead_pct": round(
            100 * (nyfs_write_s / native_write_s - 1), 1
        ) if native_write_s else None,
        "read_overhead_pct": round(
            100 * (nyfs_read_s / native_read_s - 1), 1
        ) if native_read_s else None,
        "small_create_per_sec": round(SMALL_FILES / small_create_s, 1),
    }


def main():
    print("Nyrqis Linux Backend — first-pass benchmarks")
    print("=" * 60)
    print("IPC round-trip, raised token budget (BENCHMARK_PLAN §1):")
    ipc = benchmark_ipc_roundtrip()
    for k, v in ipc.items():
        print(f"  {k}: {v}")
    print("Default token bucket sustained rate (BENCHMARK_PLAN §3 data point):")
    bucket = benchmark_default_bucket()
    for k, v in bucket.items():
        print(f"  {k}: {v}")
    print("NyFS vs native (BENCHMARK_PLAN §4 proxy; whole-file CoW/compress path):")
    fsb = benchmark_nyfs_vs_native()
    for k, v in fsb.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
