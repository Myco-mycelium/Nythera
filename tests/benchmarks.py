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
- §4 (live mount, 2026-08-12): ``--nyfs-mount`` drives the same patterns
  through a REAL kernel FUSE mount (fusepy + /dev/fuse + fusermount;
  skipped when absent) vs native I/O, and reports how the kernel batches
  write requests to the daemon.
- §5 (persisted image, 2026-08-12): ``--nyfs-persist`` builds a
  deterministic mixed asset corpus, saves it to disk (durability,
  NPS-004 §7), reloads it, and measures the loaded-image read patterns
  plus the end-to-end storage compression ratio.

Usage:
  python3 tests/benchmarks.py --all       # everything (default)
  python3 tests/benchmarks.py --ipc       # §1 IPC round-trip
  python3 tests/benchmarks.py --bucket    # §3 token-bucket defaults
  python3 tests/benchmarks.py --zstd      # §2 Zstd level sweep
  python3 tests/benchmarks.py --nyfs      # §4 NyFS vs native proxy
  python3 tests/benchmarks.py --nyfs-mount  # §4 live-mount FUSE vs native
  python3 tests/benchmarks.py --nyfs-persist  # §5 persisted-image lifecycle

Honesty notes (NPC-002 §5.2):
- These are FIRST-PASS microbenchmarks on this host, not the full plan
  methodology (which requires two containers, load variants, and a real
  FUSE mount). The in-process IPC path excludes the deferred Unix-socket/
  shared-memory transport, so these numbers bound the control-plane cost,
  not the final IPC wire cost.
- Results belong in `tests/BENCHMARK_RESULTS.md`, not in this file.
- A full `--all` run takes roughly 1–3 minutes (the Zstd level sweep is
  the long pole at ~30 s, and `--nyfs-mount` adds ~15–20 s where the
  host supports a live FUSE mount); use the individual flags to re-run
  one section quickly.
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


def _fuse_mount_available() -> bool:
    """True when a live FUSE mount can be attempted on this host."""
    try:
        if not os.path.exists("/dev/fuse"):
            return False
        import shutil

        if shutil.which("fusermount3") is None and shutil.which("fusermount") is None:
            return False
        from fuse.nyfs import _import_fusepy

        return _import_fusepy() is not None
    except Exception:
        return False


class _CountingOps(NyFSOperations):
    """Wraps the ops layer to count what the kernel actually sends us."""

    def __init__(self, fs):
        super().__init__(fs)
        self.write_calls = 0
        self.max_write = 0

    def write(self, path, data, offset, fh=None):
        self.write_calls += 1
        self.max_write = max(self.max_write, len(data))
        return super().write(path, data, offset, fh)


def benchmark_nyfs_mount(total=16 * 1024 * 1024):
    """Through a REAL FUSE mount vs native I/O on the same tmp dir (§4).

    First-pass, environment-gated (skipped when fusepy, /dev/fuse, or
    fusermount is unavailable). Honesty caveats:
    - The native baseline is the same ``tempfile`` location as the
      backing store (ext4 on this host, ``/dev/sda2``) — a real
      disk-backed comparison; hot data lands in the page cache, as it
      would for any disk-backed filesystem.
    - Reads run with the kernel page cache + readahead active (real
      users get the same), which batches 4 KiB user reads into larger
      daemon requests.
    - The kernel's write batching to the daemon is reported explicitly.
      NyFS negotiates FUSE_CAP_BIG_WRITES + FUSE_CAP_WRITEBACK_CACHE +
      FUSE_CAP_MAX_PAGES in the INIT handshake (``NyFSMount``
      ``writeback_cache=True``, the default), so writes batch at 128 KiB
      instead of the 4 KiB pages a stock fusepy mount gets.
    """
    if not _fuse_mount_available():
        return {"skipped": "no fusepy / /dev/fuse / fusermount on this host"}
    from fuse.nyfs import NyFSMount

    def mbps(bytes_, seconds):
        return round(bytes_ / seconds / (1024 * 1024), 2) if seconds else float("inf")

    def bench_write(path, chunk, size):
        data = os.urandom(chunk)
        with open(path, "wb") as fh:
            t0 = time.perf_counter()
            off = 0
            while off < size:
                fh.write(data[:size - off])
                off += chunk
            fh.flush()
            return mbps(size, time.perf_counter() - t0)

    def bench_read(path, chunk, size):
        with open(path, "rb") as fh:
            t0 = time.perf_counter()
            read = 0
            while read < size:
                fh.read(chunk)
                read += chunk
            return mbps(size, time.perf_counter() - t0)

    base = tempfile.mkdtemp()
    mnt = os.path.join(tempfile.mkdtemp(), "mnt")
    native_dir = os.path.join(base, "native")
    os.makedirs(native_dir)
    fs = NyFSFilesystem(os.path.join(base, "fs"))
    ops = _CountingOps(fs)
    m = NyFSMount(fs, mnt)
    m.operations = ops
    # Watchdog: a hung kernel FUSE request must not hang the runner.
    threading.Timer(60.0, lambda: os._exit(99)).start()
    try:
        if not m.mount(foreground=True, blocking=False):
            return {"skipped": "mount could not be started"}
        if not m.wait_ready(timeout=5.0):
            return {"skipped": "mount never became live"}

        results = {}
        for chunk, tag in ((1024 * 1024, "1m"), (4096, "4k")):
            results[f"write_{tag}_fuse_mbps"] = bench_write(
                os.path.join(mnt, "b.bin"), chunk, total)
            results[f"write_{tag}_native_mbps"] = bench_write(
                os.path.join(native_dir, "b.bin"), chunk, total)
        for chunk, tag in ((1024 * 1024, "1m"), (4096, "4k")):
            results[f"read_{tag}_fuse_mbps"] = bench_read(
                os.path.join(mnt, "b.bin"), chunk, total)
            results[f"read_{tag}_native_mbps"] = bench_read(
                os.path.join(native_dir, "b.bin"), chunk, total)

        # Kernel write batching: how does the daemon see a 1 MiB write?
        ops.write_calls = ops.max_write = 0
        with open(os.path.join(mnt, "b.bin"), "wb") as fh:
            fh.write(os.urandom(1024 * 1024))
            fh.flush()
        results["write_requests_per_1m"] = ops.write_calls
        results["max_write_request_bytes"] = ops.max_write
        results["total_bytes"] = total
        return results
    finally:
        try:
            m.unmount()
        except Exception:
            pass
        try:
            import subprocess

            subprocess.run(["fusermount3", "-u", mnt],
                           capture_output=True, timeout=5)
        except Exception:
            pass


def benchmark_nyfs_persisted():
    """Persisted-image lifecycle: save/load, ratio, loaded-image reads.

    Builds a deterministic mixed asset corpus (compressible text-like,
    incompressible binary, and large streaming files — seed 11), writes
    it through the NyFS ops layer, ``save()``s it to disk (NPS-004 §7
    durability), reloads with ``load()``, and measures:
    - commit cost: save() time and on-disk block-store size;
    - end-to-end storage compression ratio (logical / on-disk bytes) —
      a first-pass data point for BENCHMARK_PLAN §2's "compression
      ratio > 30%" question, on a synthetic corpus (the plan's real
      asset corpus remains unmeasured);
    - loaded-image reads: sequential streaming of large files and
      small-file random access — the "installed once, read many times"
      shape that matters for gaming loads (NPS-006 §5).
    """
    import random

    with tempfile.TemporaryDirectory() as tmp:
        rng = random.Random(11)
        corpus = []
        # ~150 small text-like files (compressible).
        for i in range(150):
            n = rng.randint(10, 200)
            body = ("The quick brown fox jumps over the lazy dog. " * n).encode()
            corpus.append((f"/assets/text_{i}.txt", body))
        # 30 medium files: mixed compressibility.
        for i in range(30):
            size = rng.randint(64_000, 200_000)
            if i % 3 == 0:
                body = rng.randbytes(size)  # pseudo-random, incompressible
            elif i % 3 == 1:
                body = (b"level-data-v1;" * (size // 13 + 1))[:size]
            else:
                body = rng.randbytes(size)
            corpus.append((f"/assets/med_{i}.bin", body))
        # 5 large streaming files (~1-4 MiB, compressible).
        for i in range(5):
            n = rng.randint(1, 4)
            body = (b"stream-chunk;" * (1024 * 1024 // 13)) * n
            corpus.append((f"/assets/big_{i}.dat", body))

        total_logical = sum(len(b) for _, b in corpus)
        fs = NyFSFilesystem(os.path.join(tmp, "fs"))
        fs.mkdir("/assets")

        # Write the corpus.
        t0 = time.perf_counter()
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        write_s = time.perf_counter() - t0

        # Commit.
        t0 = time.perf_counter()
        fs.save()
        save_s = time.perf_counter() - t0
        # Re-save of an unchanged state (immutable-block skip path).
        t0 = time.perf_counter()
        fs.save()
        resave_s = time.perf_counter() - t0
        # End-to-end on-disk footprint: block store + inode tables + any
        # snapshot/metadata files under the state tree.
        state_dir = os.path.join(tmp, "fs", "state")
        on_disk = 0
        for root, _dirs, files in os.walk(state_dir):
            for name in files:
                on_disk += os.path.getsize(os.path.join(root, name))

        # Reload.
        t0 = time.perf_counter()
        fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
        load_s = time.perf_counter() - t0

        # Loaded-image reads: sequential streaming of the large files.
        big_total = sum(len(b) for p, b in corpus if len(b) >= 1024 * 1024)
        t0 = time.perf_counter()
        for path, body in corpus:
            if len(body) < 1024 * 1024:
                continue
            f = fs2.resolve(path)
            for off in range(0, len(body), 65536):
                fs2.read(f, 65536, off)
        stream_s = time.perf_counter() - t0

        # Loaded-image reads: small-file random access (asset catalog).
        small = [(p, b) for p, b in corpus if len(b) < 64_000]
        t0 = time.perf_counter()
        for _ in range(3):
            for path, body in small:
                f = fs2.resolve(path)
                fs2.read(f, min(4096, len(body)), 0)
        small_s = time.perf_counter() - t0

        def mbps(bytes_, seconds):
            return round(bytes_ / seconds / (1024 * 1024), 2) if seconds else float("inf")

        return {
            "files": len(corpus),
            "logical_bytes": total_logical,
            "on_disk_bytes": on_disk,
            "compression_ratio": round(total_logical / on_disk, 2),
            "write_corpus_mbps": mbps(total_logical, write_s),
            "save_seconds": round(save_s, 3),
            "resave_seconds": round(resave_s, 3),
            "load_seconds": round(load_s, 3),
            "loaded_stream_read_mbps": mbps(big_total, stream_s),
            "loaded_small_reads_per_sec": round(
                len(small) * 3 / small_s, 1),
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
    parser.add_argument("--nyfs-mount", action="store_true",
                        help="§4 live-mount FUSE vs native")
    parser.add_argument("--nyfs-persist", action="store_true",
                        help="§5 persisted-image lifecycle")
    args = parser.parse_args()

    selected = (args.ipc or args.bucket or args.zstd or args.nyfs
                or args.nyfs_mount or args.nyfs_persist)
    if not selected or args.all:
        args.ipc = args.bucket = args.zstd = args.nyfs = True
        args.nyfs_mount = args.nyfs_persist = True

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
    if args.nyfs_mount:
        _print_section("NyFS live FUSE mount vs native (§4):",
                       benchmark_nyfs_mount())
    if args.nyfs_persist:
        _print_section("NyFS persisted-image lifecycle (§5):",
                       benchmark_nyfs_persisted())


if __name__ == "__main__":
    main()
