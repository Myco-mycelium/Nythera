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
- §5 (save() commit-cost levers, 2026-08-12): ``--save-levers`` measures
  the knobs the fsync-bound finding named — block size (64 KiB / 256 KiB /
  1 MiB), ``save(batched_fsync=True)`` group-commit, and the new
  ``save(use_journal=True)`` append-only journal (one fsync per
  transaction) — on the same corpus, each verified by a save -> load ->
  read round-trip.
- §5 (cross-snapshot dedup, 2026-08-12): ``--snapshot-dedup`` measures
  how much block-store space a snapshot chain really costs when 20% of
  the corpus changes between snapshots (CoW block sharing).
- §2 (codec comparison, 2026-08-12): ``--codec`` compares zstd level 3
  (NyFS default) against zlib level 6 (stdlib; python-lz4 is not
  installed on this host) on the ``benchmark_zstd`` corpus.
- §2 (real-corpus ratio, 2026-08-12): ``--real-corpus`` runs the
  end-to-end compression-ratio measurement on a deterministic sample of
  real files from ``/usr/share`` (fonts, locale, man, mime, zoneinfo,
  applications).

Usage:
  python3 tests/benchmarks.py --all       # everything (default)
  python3 tests/benchmarks.py --ipc       # §1 IPC round-trip
  python3 tests/benchmarks.py --bucket    # §3 token-bucket defaults
  python3 tests/benchmarks.py --zstd      # §2 Zstd level sweep
  python3 tests/benchmarks.py --nyfs      # §4 NyFS vs native proxy
  python3 tests/benchmarks.py --nyfs-mount  # §4 live-mount FUSE vs native
  python3 tests/benchmarks.py --nyfs-persist  # §5 persisted-image lifecycle
  python3 tests/benchmarks.py --save-levers   # §5 save() commit-cost levers
  python3 tests/benchmarks.py --snapshot-dedup  # §5 cross-snapshot dedup
  python3 tests/benchmarks.py --codec        # §2 zstd vs zlib codec compare
  python3 tests/benchmarks.py --real-corpus  # §2 real-corpus ratio

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


def _state_tree_bytes(state_dir) -> int:
    """Total bytes under the NyFS state tree (blocks + journal + inode
    tables + metadata) — the end-to-end on-disk footprint."""
    total = 0
    for root, _dirs, files in os.walk(state_dir):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


def _build_persist_corpus(seed: int = 11):
    """Deterministic mixed asset corpus shared by the persisted-image
    benchmarks: ~150 small compressible text-like files, 30 medium files
    of mixed compressibility, and 5 large streaming files.

    Seeded (no ``os.urandom``), so the exact byte image reproduces
    across runs. Returns ``(corpus, total_logical)``.
    """
    import random

    rng = random.Random(seed)
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
    return corpus, total_logical


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
    with tempfile.TemporaryDirectory() as tmp:
        corpus, total_logical = _build_persist_corpus()
        fs = NyFSFilesystem(os.path.join(tmp, "fs"))
        fs.mkdir("/assets")

        # Write the corpus.
        t0 = time.perf_counter()
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        write_s = time.perf_counter() - t0

        # Commit. Pinned to the interleaved path (use_journal=False) so
        # this section keeps measuring the fsync-per-block durability
        # contract baseline documented in §7; journal commit (the
        # default) is measured separately in §9.
        t0 = time.perf_counter()
        fs.save(use_journal=False)
        save_s = time.perf_counter() - t0
        # Re-save of an unchanged state (immutable-block skip path).
        t0 = time.perf_counter()
        fs.save(use_journal=False)
        resave_s = time.perf_counter() - t0
        # End-to-end on-disk footprint: block store + inode tables + any
        # snapshot/metadata files under the state tree.
        state_dir = os.path.join(tmp, "fs", "state")
        on_disk = _state_tree_bytes(state_dir)

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


def benchmark_save_levers():
    """save() commit-cost levers (BENCHMARK_RESULTS.md §8).

    The §7 fsync-bound finding named three design questions for commit
    cost; the first two are measured here on the same deterministic
    corpus (``_build_persist_corpus``):
    - larger blocks (fewer block files -> fewer per-file fsyncs, at the
      cost of padding waste for small files): 64 KiB (baseline),
      256 KiB, and 1 MiB;
    - batched fsync (``save(batched_fsync=True)``: all temps written,
      then all fsynced, then all renamed) vs the default interleaved
      path, at the default 64 KiB block size;
    - journal commit (``save(use_journal=True)``: one fsync for the
      whole transaction's block payloads, then the metadata swap) at
      the default 64 KiB block size.
    Every config verifies a full save -> load -> read round-trip before
    reporting (``roundtrip_ok``), so a lever that broke durability would
    fail loudly here. Each config repeats twice and reports the minimum
    save time: fsync-bound timings swing ±30% run to run on this host,
    so single-run comparisons would be noise.
    """
    corpus, total_logical = _build_persist_corpus()

    def run(block_size, batched, use_journal=False, repeats=2):
        # fsync-bound timings are noisy on this host (observed ±30% run
        # to run), so each config is repeated and the minimum (least
        # noise-inflated) save time is reported; the other metrics come
        # from the best run.
        best = None
        for _ in range(repeats):
            with tempfile.TemporaryDirectory() as tmp:
                fs = NyFSFilesystem(os.path.join(tmp, "fs"),
                                    block_size=block_size)
                fs.mkdir("/assets")
                for path, body in corpus:
                    fs.write(fs.create_file(path), body)
                t0 = time.perf_counter()
                fs.save(batched_fsync=batched, use_journal=use_journal)
                save_s = time.perf_counter() - t0
                state_dir = os.path.join(tmp, "fs", "state")
                on_disk = _state_tree_bytes(state_dir)
                blocks_dir = os.path.join(state_dir, "blocks")
                n_blocks = (len([n for n in os.listdir(blocks_dir)
                                 if n.endswith(".bin")])
                            if os.path.isdir(blocks_dir) else 0)
                journal = os.path.join(state_dir, "journal.bin")
                j_bytes = (os.path.getsize(journal)
                           if os.path.exists(journal) else 0)
                fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
                ok = all(
                    fs2.read(fs2.resolve(p)) == body
                    for p, body in corpus
                )
                row = {
                    "save_s": round(save_s, 3),
                    "block_files": n_blocks,
                    "journal_bytes": j_bytes,
                    "on_disk_bytes": on_disk,
                    "ratio": round(total_logical / on_disk, 2),
                    "roundtrip_ok": ok,
                }
                if best is None or save_s < best["save_s"]:
                    best = row
        return best

    rows = {
        "64k_interleaved": run(65536, False),
        "64k_batched": run(65536, True),
        "64k_journal": run(65536, False, use_journal=True),
        "256k_interleaved": run(262144, False),
        "1m_interleaved": run(1048576, False),
    }
    out = {"logical_bytes": total_logical}
    for name, row in rows.items():
        out.update({f"{name}_{k}": v for k, v in row.items()})
    return out


def benchmark_snapshot_dedup():
    """Cross-snapshot deduplication measured on disk (BENCHMARK_RESULTS
    §10).

    NyFS dedups by CoW sharing: snapshots reference the same immutable
    blocks, so a save stores each distinct block once. This measures how
    much block-store space a snapshot chain actually costs when 20% of
    the corpus changes between snapshots — vs the naive cost of an
    independent full copy.
    """
    corpus, total_logical = _build_persist_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        fs = NyFSFilesystem(os.path.join(tmp, "fs"))
        fs.mkdir("/assets")
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        snap1 = fs.create_snapshot()
        # Pinned to interleaved so the block-store growth metric is the
        # .bin-file delta documented in §10 (journal mode holds payloads
        # in the journal until compaction).
        fs.save(use_journal=False)
        state_dir = os.path.join(tmp, "fs", "state")
        after_snap1 = _state_tree_bytes(state_dir)

        # Modify ~20% of the corpus: rewrite 30 text files with new
        # content and flip the first 4 KiB of 5 medium files.
        for i in range(30):
            fs.write(fs.resolve(f"/assets/text_{i}.txt"),
                     f"changed-v2;{i}".encode() * 80)
        for i in range(5):
            med = fs.resolve(f"/assets/med_{i}.bin")
            head = fs.read(med, 4096, 0)
            fs.write(med, bytes(b ^ 0xFF for b in head), 0)
        snap2 = fs.create_snapshot()
        fs.save(use_journal=False)
        after_snap2 = _state_tree_bytes(state_dir)
        bins = [n for n in os.listdir(os.path.join(state_dir, "blocks"))
                if n.endswith(".bin")]
        new_bytes = after_snap2 - after_snap1
        return {
            "logical_bytes": total_logical,
            "on_disk_after_snap1": after_snap1,
            "on_disk_after_snap2": after_snap2,
            "new_block_bytes_for_snap2": new_bytes,
            "naive_full_copy_bytes": total_logical,
            "dedup_factor": (round(total_logical / new_bytes, 2)
                              if new_bytes else None),
            "block_files_after_snap2": len(bins),
            "snapshots": 2,
        }


def benchmark_codec_compare():
    """zstd (NyFS default, level 3) vs zlib (stdlib, level 6) on the
    benchmark_zstd corpus — the non-zstd codec comparison that
    BENCHMARK_PLAN §2 lists as pending (BENCHMARK_RESULTS §11).

    python-lz4 is NOT installed on this host, so the plan's LZ4
    comparison is approximated with zlib, a broadly-comparable
    general-purpose codec (installing ``lz4`` via pip would add a true
    LZ4 row).
    """
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
    except ImportError as e:
        return {"error": f"zstandard unavailable: {e}"}

    import zlib
    import zstandard as zstd

    out = {}
    total_in = sum(len(v) for v in corpus.values())
    for name, data in corpus.items():
        zc = zstd.ZstdCompressor(level=3)
        zd = zstd.ZstdDecompressor()
        z3 = zc.compress(data)
        out[f"zstd3_{name}_ratio"] = round(len(data) / len(z3), 2)
        out[f"zstd3_{name}_compress_mbps"] = round(
            mod.throughput(lambda d: zc.compress(d), data, 4))
        out[f"zstd3_{name}_decompress_mbps"] = round(
            mod.throughput(lambda d: zd.decompress(d), z3, 4,
                           size=len(data)))
        zb = zlib.compress(data, 6)
        out[f"zlib6_{name}_ratio"] = round(len(data) / len(zb), 2)
        out[f"zlib6_{name}_compress_mbps"] = round(
            mod.throughput(lambda d: zlib.compress(d, 6), data, 4))
        out[f"zlib6_{name}_decompress_mbps"] = round(
            mod.throughput(lambda d: zlib.decompress(d), zb, 4,
                           size=len(data)))
    zstd_out = sum(len(zstd.ZstdCompressor(level=3).compress(v))
                   for v in corpus.values())
    zlib_out = sum(len(zlib.compress(v, 6)) for v in corpus.values())
    out["zstd3_overall_ratio"] = round(total_in / zstd_out, 2)
    out["zlib6_overall_ratio"] = round(total_in / zlib_out, 2)
    return out


def _build_real_corpus(target_bytes: int = 16 * 1024 * 1024):
    """Deterministic sample of REAL files from the system (/usr/share).

    Subdirectories chosen for variety: zoneinfo (binary timezone data),
    applications (.desktop text), mime (XML/globs), man (compressed
    text), locale (compiled message catalogs), fonts (.ttf binaries).
    Files are taken in sorted-path order per directory until the target
    size is reached, so the selection is deterministic for a given
    system image. Returns ([(path, bytes)], total).
    """
    roots = ["zoneinfo", "applications", "mime", "man", "locale", "fonts"]
    selected = []
    total = 0
    for sub in roots:
        root = os.path.join("/usr/share", sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()  # deterministic traversal for a given image
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(path)
                    if size < 256 or size > 4 * 1024 * 1024:
                        continue
                    if total + size > target_bytes:
                        continue
                    data = open(path, "rb").read()
                except OSError:
                    continue
                selected.append((path, data))
                total += size
                if total >= target_bytes:
                    return selected, total
    return selected, total


def benchmark_real_corpus(target_bytes: int = 16 * 1024 * 1024):
    """End-to-end NyFS compression ratio on a REAL mixed corpus
    (BENCHMARK_RESULTS §12).

    The synthetic §7 corpus is text-heavy (6.42 : 1). Real
    game-adjacent data — already-compressed fonts, locale catalogs,
    compressed man pages — is the honest second data point for
    BENCHMARK_PLAN §2.
    """
    try:
        files, total = _build_real_corpus(target_bytes)
    except Exception as e:
        return {"error": str(e)}
    out = {
        "source": "/usr/share (zoneinfo, applications, mime, man,"
                  " locale, fonts)",
        "files": len(files),
        "logical_bytes": total,
    }

    def pass_write_and_save(use_journal):
        with tempfile.TemporaryDirectory() as tmp:
            fs = NyFSFilesystem(os.path.join(tmp, "fs"))
            fs.mkdir("/assets")
            t0 = time.perf_counter()
            for i, (_path, data) in enumerate(files):
                fs.write(fs.create_file(f"/assets/real_{i}.bin"), data)
            write_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            fs.save(use_journal=use_journal)
            save_s = time.perf_counter() - t0
            state_dir = os.path.join(tmp, "fs", "state")
            on_disk = _state_tree_bytes(state_dir)
            blocks_dir = os.path.join(state_dir, "blocks")
            n_blocks = (len([n for n in os.listdir(blocks_dir)
                             if n.endswith(".bin")])
                        if os.path.isdir(blocks_dir) else 0)
            fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
            ok = all(fs2.read(fs2.resolve(f"/assets/real_{i}.bin")) == data
                     for i, (_p, data) in enumerate(files))
            return {
                "on_disk_bytes": on_disk,
                "compression_ratio": round(total / on_disk, 2),
                "write_mbps": (round(total / write_s / 1e6, 2)
                               if write_s else None),
                "save_seconds": round(save_s, 3),
                "block_files": n_blocks,
                "roundtrip_ok": ok,
            }

    out["interleaved"] = pass_write_and_save(False)
    out["journal"] = pass_write_and_save(True)
    return out


def benchmark_mixed_workload():
    """Mixed read/write/commit loop under journal vs interleaved commit
    (BENCHMARK_RESULTS §13).

    §9 measured a single cold transaction. Real daemons commit
    repeatedly while serving reads and writes, so this section drives a
    deterministic loop: N files, R rounds, each round updating every
    file (CoW), reading it back, and fsync()-committing once. Reports
    end-to-end time, per-commit latency (avg + max), and I/O throughput
    for both commit modes; every run reloads and compares the full
    content before reporting (roundtrip_ok). Each mode builds its own
    workload from the same seed so the I/O is byte-identical across
    modes.
    """
    n_files, rounds, chunk = 16, 6, 16 * 1024
    file_bytes = 64 * 1024

    def run(use_journal):
        rng = __import__("random").Random(13)
        with tempfile.TemporaryDirectory() as tmp:
            fs = NyFSFilesystem(os.path.join(tmp, "fs"))
            paths = []
            t0 = time.perf_counter()
            for i in range(n_files):
                p = f"/mix_{i}.bin"
                fs.write(fs.create_file(p),
                         bytes(rng.randrange(256) for _ in range(file_bytes)))
                paths.append(p)
            write_s = time.perf_counter() - t0

            t0 = time.perf_counter()
            commits = []
            for r in range(rounds):
                for i, p in enumerate(paths):
                    off = (r * chunk) % (file_bytes - chunk + 1)
                    data = bytes(rng.randrange(256) for _ in range(chunk))
                    fs.write(fs.resolve(p), data, offset=off)
                    fs.read(fs.resolve(p), chunk, off)
                c0 = time.perf_counter()
                fs.save(use_journal=use_journal)
                commits.append(time.perf_counter() - c0)
            loop_s = time.perf_counter() - t0

            live = {p: fs.read(fs.resolve(p)) for p in paths}
            fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
            ok = all(fs2.read(fs2.resolve(p)) == live[p] for p in paths)
            total_io = n_files * rounds * chunk
            return {
                "write_mbps": round(n_files * file_bytes / write_s / 1e6, 2),
                "loop_seconds": round(loop_s, 3),
                "commits": rounds,
                "commit_ms_avg": round(sum(commits) / len(commits) * 1000, 2),
                "commit_ms_max": round(max(commits) * 1000, 2),
                "io_mbps": round(total_io / loop_s / 1e6, 2),
                "roundtrip_ok": ok,
            }

    return {"interleaved": run(False), "journal": run(True)}


def benchmark_compaction_cost():
    """The journal compaction pass measured in isolation
    (BENCHMARK_RESULTS §14).

    Journal commits are cheap (~60–70× vs fsync-per-block, §9), but the
    materialize pass they postpone — move referenced blocks into
    ``state/blocks/``, truncate the journal — is a real cost a daemon
    pays somewhere. This section measures it on the same §7 corpus:
    build a journal without ever triggering save()-time compaction (1
    GiB threshold), then time ``compact_journal()`` and report the
    per-block materialize cost alongside the commit time it buys.
    """
    corpus, total_logical = _build_persist_corpus()
    with tempfile.TemporaryDirectory() as tmp:
        fs = NyFSFilesystem(os.path.join(tmp, "fs"),
                            journal_compact_bytes=1 << 30)
        fs.mkdir("/assets")
        for path, body in corpus:
            fs.write(fs.create_file(path), body)
        t0 = time.perf_counter()
        fs.save(use_journal=True)
        save_s = time.perf_counter() - t0
        journal_before = fs.journal_bytes()

        t0 = time.perf_counter()
        moved = fs.compact_journal()
        compact_s = time.perf_counter() - t0

        blocks_dir = os.path.join(tmp, "fs", "state", "blocks")
        n_bins = len([n for n in os.listdir(blocks_dir)
                      if n.endswith(".bin")])
        fs2 = NyFSFilesystem.load(os.path.join(tmp, "fs"))
        ok = all(fs2.read(fs2.resolve(p)) == body for p, body in corpus)
        return {
            "logical_bytes": total_logical,
            "journal_commit_s": round(save_s, 3),
            "journal_bytes_before": journal_before,
            "blocks_moved": moved,
            "compaction_s": round(compact_s, 3),
            "per_block_ms": (round(compact_s / moved * 1000, 2)
                              if moved else None),
            "bin_files_after": n_bins,
            "journal_bytes_after": fs.journal_bytes(),
            "roundtrip_ok": ok,
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
    parser.add_argument("--save-levers", action="store_true",
                        help="§5 save() commit-cost levers (block size, "
                             "batched fsync, journal)")
    parser.add_argument("--snapshot-dedup", action="store_true",
                        help="§5 cross-snapshot dedup measurement")
    parser.add_argument("--codec", action="store_true",
                        help="§2 zstd vs zlib codec comparison")
    parser.add_argument("--real-corpus", action="store_true",
                        help="§2 end-to-end ratio on a real /usr/share corpus")
    parser.add_argument("--mixed-workload", action="store_true",
                        help="§5 mixed read/write/commit loop, journal vs interleaved")
    parser.add_argument("--compaction-cost", action="store_true",
                        help="§5 journal compaction pass cost")
    args = parser.parse_args()

    selected = (args.ipc or args.bucket or args.zstd or args.nyfs
                or args.nyfs_mount or args.nyfs_persist or args.save_levers
                or args.snapshot_dedup or args.codec or args.real_corpus
                or args.mixed_workload or args.compaction_cost)
    if not selected or args.all:
        args.ipc = args.bucket = args.zstd = args.nyfs = True
        args.nyfs_mount = args.nyfs_persist = args.save_levers = True
        args.snapshot_dedup = args.codec = args.real_corpus = True
        args.mixed_workload = args.compaction_cost = True

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
    if args.save_levers:
        _print_section("NyFS save() commit-cost levers (§5):",
                       benchmark_save_levers())
    if args.snapshot_dedup:
        _print_section("NyFS cross-snapshot dedup (§5):",
                       benchmark_snapshot_dedup())
    if args.codec:
        _print_section("zstd-3 vs zlib-6 codec compare (§2):",
                       benchmark_codec_compare())
    if args.real_corpus:
        _print_section("NyFS real-corpus compression ratio (§2):",
                       benchmark_real_corpus())
    if args.mixed_workload:
        _print_section("NyFS mixed read/write/commit loop (§13):",
                       benchmark_mixed_workload())
    if args.compaction_cost:
        _print_section("NyFS journal compaction pass cost (§14):",
                       benchmark_compaction_cost())


if __name__ == "__main__":
    main()
