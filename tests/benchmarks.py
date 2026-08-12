#!/usr/bin/env python3
"""Consolidated benchmarks for the Nyrqis Linux Backend.

Runs every benchmark in `tests/BENCHMARK_PLAN.md` that is runnable on
this host in one reproducible script:

- §1 IPC round-trip latency (NPS-003 §3): the `call` primitive, p50/p95/p99.
- §3 IPC token-bucket defaults (ADR-0009): sustained rate under the
  default bucket.
- §2 Zstd compression levels (ADR-0007): level sweep (imports the
  standalone `benchmark_zstd.py`).
- §4 FUSE overhead (ADR-0016): NyFS operation-handler throughput vs native
  file I/O on the same disk, as a proxy for the real FUSE-vs-ext4
  comparison (which requires a live FUSE mount). With per-block CoW
  (2026-08-12) this reports two access patterns — 4 KiB sequential writes
  (per-call overhead dominates) and 1 MiB-chunk streaming (per-block CoW
  win) — plus a block-size sweep on the 4 KiB pattern.

Usage:
  python3 tests/benchmarks.py --all      # everything (default)
  python3 tests/benchmarks.py --ipc      # §1 IPC round-trip
  python3 tests/benchmarks.py --bucket   # §3 token-bucket defaults
  python3 tests/benchmarks.py --zstd     # §2 Zstd level sweep
  python3 tests/benchmarks.py --nyfs     # §4 NyFS vs native proxy

Honesty notes (NPC-002 §5.2):
- These are FIRST-PASS microbenchmarks on this host, not the full plan
  methodology (which requires two containers, load variants, and a real
  FUSE mount). The in-process IPC path excludes the deferred Unix-socket/
  shared-memory transport, so these numbers bound the control-plane cost,
  not the final IPC wire cost.
- Results belong in `tests/BENCHMARK_RESULTS.md`, not in this file.
- A full `--all` run takes roughly 1–2 minutes (the Zstd level sweep is
  the long pole at ~30 s); use the individual flags to re-run one
  section quickly.
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


def _nyfs_throughput(fs, total, chunk, random_offsets=False):
    """Write ``total`` bytes in ``chunk`` pieces; return MB/s."""
    f = fs.create_file("/bench.bin")
    data = os.urandom(4096)
    t0 = time.perf_counter()
    if random_offsets:
        # 4 KiB scatter across a 16 MiB address space (asset-like).
        # Fixed seed (7) so runs are reproducible.
        import random

        rng = random.Random(7)
        off = 0
        written = 0
        while written < total:
            off = rng.randrange(0, 16 * 1024 * 1024, 4096)
            fs.write(f, data, off)
            written += len(data)
    else:
        off = 0
        for _ in range(total // chunk):
            fs.write(f, data, off)
            off += chunk
    elapsed = time.perf_counter() - t0
    return round(total / elapsed / (1024 * 1024), 2) if elapsed else float("inf")


def benchmark_nyfs_vs_native():
    """NyFS operations-layer throughput vs native file I/O (§4 proxy).

    With per-block CoW (2026-08-12) the access pattern matters, so this
    reports two: 4 KiB sequential writes (per-call overhead dominates —
    each write compresses/checksums a full ``block_size`` block) and
    1 MiB-chunk streaming (per-block CoW's write-amplification win).
    """
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

        nyfs_root = os.path.join(tmp, "nyfs")

        # Access pattern A: 4 KiB sequential writes (old benchmark shape).
        fs_a = NyFSFilesystem(os.path.join(nyfs_root, "a"))
        small_write_mbps = _nyfs_throughput(fs_a, FS_TOTAL_BYTES, FS_CHUNK)

        # Access pattern B: 1 MiB-chunk streaming writes.
        fs_b = NyFSFilesystem(os.path.join(nyfs_root, "b"))
        stream_write_mbps = _nyfs_throughput(
            fs_b, FS_TOTAL_BYTES, 1024 * 1024)

        # Access pattern C: 4 KiB scattered writes (random offsets).
        fs_c = NyFSFilesystem(os.path.join(nyfs_root, "c"))
        scatter_write_mbps = _nyfs_throughput(
            fs_c, FS_TOTAL_BYTES, FS_CHUNK, random_offsets=True)

        # Sequential 8 MiB read through the operation handlers. The file
        # is written in 1 MiB chunks (single pass per block) so the read
        # timing measures reads, not the write path.
        fs_d = NyFSFilesystem(os.path.join(nyfs_root, "d"))
        f = fs_d.create_file("/bench.bin")
        off = 0
        for _ in range(FS_TOTAL_BYTES // (1024 * 1024)):
            fs_d.write(f, os.urandom(1024 * 1024), off)
            off += 1024 * 1024
        t0 = time.perf_counter()
        for i in range(FS_TOTAL_BYTES // FS_CHUNK):
            fs_d.read(f, FS_CHUNK, (i * FS_CHUNK) % FS_TOTAL_BYTES)
        nyfs_read_s = time.perf_counter() - t0

        # Block-size sweep on the 4 KiB-write pattern (tuning data for
        # the block_size default decision, not a decision itself).
        sweep = {}
        for bs in (4096, 16384, 65536, 262144):
            fs_s = NyFSFilesystem(os.path.join(nyfs_root, f"s{bs}"),
                                  block_size=bs)
            sweep[bs] = _nyfs_throughput(fs_s, FS_TOTAL_BYTES, FS_CHUNK)

        # Small-file creation (many game assets, NPS-006 §5).
        ops = NyFSOperations(fs_a)
        t0 = time.perf_counter()
        for i in range(SMALL_FILES):
            ops.mknod(f"/asset_{i}.dat", 0o644, 0)
        small_create_s = time.perf_counter() - t0

    def mbps(total, seconds):
        return round(total / seconds / (1024 * 1024), 2) if seconds else float("inf")

    return {
        "total_bytes": FS_TOTAL_BYTES,
        "small_files": SMALL_FILES,
        "block_size": NyFSFilesystem.BLOCK_SIZE,
        "native_write_mbps": mbps(FS_TOTAL_BYTES, native_write_s),
        "native_read_mbps": mbps(FS_TOTAL_BYTES, native_read_s),
        "nyfs_write_4k_mbps": small_write_mbps,
        "nyfs_write_1m_mbps": stream_write_mbps,
        "nyfs_write_scatter_mbps": scatter_write_mbps,
        "nyfs_read_mbps": mbps(FS_TOTAL_BYTES, nyfs_read_s),
        "small_create_per_sec": round(SMALL_FILES / small_create_s, 1),
        "block_size_sweep_4k_mbps": sweep,
    }


def benchmark_zstd_levels():
    """Zstd level sweep (BENCHMARK_PLAN §2) via benchmark_zstd.py."""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "benchmark_zstd",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "benchmark_zstd.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        corpus = mod.build_corpus()
        rows = {}
        for level in mod.LEVELS:
            results, overall = mod.bench_level(level, corpus)
            rows[level] = {
                "overall_ratio": round(overall, 2),
                "text_ratio": round(results["text"][0], 2),
                "media_ratio": round(results["media"][0], 2),
                "compress_mbps": round(
                    (results["text"][1] + results["media"][1]
                     + results["incompressible"][1]) / 3),
                "decompress_mbps": round(
                    (results["text"][2] + results["media"][2]
                     + results["incompressible"][2]) / 3),
            }
        return rows
    except ImportError as e:
        return {"error": f"zstandard unavailable: {e}"}


def _print_section(title, data):
    print(title)
    for k, v in data.items():
        print(f"  {k}: {v}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Nyrqis Linux Backend consolidated benchmarks")
    parser.add_argument("--all", action="store_true", help="run everything (default)")
    parser.add_argument("--ipc", action="store_true", help="§1 IPC round-trip latency")
    parser.add_argument("--bucket", action="store_true", help="§3 token-bucket defaults")
    parser.add_argument("--zstd", action="store_true", help="§2 Zstd level sweep")
    parser.add_argument("--nyfs", action="store_true", help="§4 NyFS vs native proxy")
    args = parser.parse_args()

    selected = args.ipc or args.bucket or args.zstd or args.nyfs
    if not selected or args.all:
        args.ipc = args.bucket = args.zstd = args.nyfs = True

    print("Nyrqis Linux Backend — consolidated first-pass benchmarks")
    print("=" * 60)
    if args.ipc:
        _print_section("IPC round-trip, raised token budget (§1):",
                       benchmark_ipc_roundtrip())
    if args.bucket:
        _print_section("Default token bucket sustained rate (§3):",
                       benchmark_default_bucket())
    if args.zstd:
        _print_section("Zstd level sweep (§2):", benchmark_zstd_levels())
    if args.nyfs:
        _print_section("NyFS vs native (§4 proxy; per-block CoW):",
                       benchmark_nyfs_vs_native())


if __name__ == "__main__":
    main()
