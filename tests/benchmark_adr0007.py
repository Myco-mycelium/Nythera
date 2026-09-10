#!/usr/bin/env python3
"""ADR-0007 close-out data: real-asset zstd level sweep, LZ4 fast path,
and concurrent-compression scaling.

Why this exists: BENCHMARK_RESULTS §2/§4 measured a SYNTHETIC corpus and
approximated the LZ4 fast path with zlib level 6 (python-lz4 was then
unavailable). The remaining ADR-0007 blockers were exactly: a real asset
corpus, the LZ4 fast-path comparison, and concurrent-load CPU
measurement. This script closes all three:

1. REAL SWEEP — the §12 real /usr/share corpus (deterministic sample of
   fonts, locale catalogs, man pages, mime, zoneinfo, applications —
   predominantly already-compressed data) swept across zstd levels 1–22,
   with per-slice ratios (games/texture-like corpora differ; here the
   honest expectation is LOW ratios) plus the synthetic corpus for
   shape continuity with §2/§4.

2. LZ4 FAST PATH — lz4.frame (now installed) vs zstd at levels 1 and 3
   on the same corpora: ratio and both throughputs. ADR-0007's premise
   is "Zstd default, LZ4 fast-path override"; this quantifies what the
   override buys and what it costs in ratio.

3. CONCURRENT LOAD — 1/2/4/8 threads compressing independent chunks:
   per-thread and aggregate throughput. Nyrqis commits run inside a
   daemon while other work continues; the single-thread numbers in §2
   overstate commit interference if compression scales, understate it
   if the GIL serializes (zstandard releases the GIL for large inputs —
   this measures which).

Honesty notes (NPC-002 §5.2):
- "Real asset corpus" here is system files, not actual game assets
  (textures/meshes/audio are mostly already-compressed container
  formats; /usr/share skews similarly). It is the honest corpus we can
  ship deterministically; game-asset data remains future work.
- Throughput is host-load-dependent; ratios are exact and reproducible.
- Results belong in tests/BENCHMARK_RESULTS.md.

Run:
    python3 tests/benchmark_adr0007.py            # everything
    python3 tests/benchmark_adr0007.py --real     # real-corpus sweep
    python3 tests/benchmark_adr0007.py --lz4      # LZ4 vs zstd
    python3 tests/benchmark_adr0007.py --concurrent
"""

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import lz4.frame
import zstandard as zstd

LEVELS = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 22]
TARGET_BYTES = 8 * 1024 * 1024  # real-corpus sample size

REAL_ROOTS = ["fonts", "locale", "man", "mime", "zoneinfo", "applications"]


def build_real_corpus(target_bytes: int = TARGET_BYTES):
    """Deterministic sample of real files from /usr/share (§12 method)."""
    selected = []
    total = 0
    for sub in REAL_ROOTS:
        root = os.path.join("/usr/share", sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
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
                selected.append(data)
                total += size
                if total >= target_bytes:
                    return selected, total
    return selected, total


def build_synthetic_corpus():
    """Same shape as §2/§4 (text-like, media-like, incompressible)."""
    text = ("The quick brown fox jumps over the lazy dog. " * 4000).encode()
    media = bytes((i * 7 + (i >> 3)) % 256 for i in range(1 << 20)) * 2
    incompressible = os.urandom(1 << 20)
    return {"text": text, "media": media, "incompressible": incompressible}


def throughput_mb_s(fn, data, rounds=3, size=None):
    """MB/s over the bytes the work actually consumes/produces."""
    size = size if size is not None else len(data)
    best = 0.0
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(data)
        dt = time.perf_counter() - t0
        best = max(best, size / dt / 1e6)
    return best


def sweep_real():
    files, total = build_real_corpus()
    if not files:
        print("no real corpus available on this host; skipping")
        return
    print(f"real corpus: {len(files)} files, {total:,} bytes from /usr/share\n")
    print("| Level | overall ratio | compress MB/s | decompress MB/s |")
    print("|-------|--------------:|---------------:|----------------:|")
    for level in LEVELS:
        cctx = zstd.ZstdCompressor(level=level)
        dctx = zstd.ZstdDecompressor()
        cdatas = [cctx.compress(d) for d in files]
        ctotal = sum(len(c) for c in cdatas)
        ratio = total / ctotal
        c_speed = throughput_mb_s(lambda _: [cctx.compress(d) for d in files],
                                  b"", rounds=2, size=total)
        d_speed = throughput_mb_s(lambda _: [dctx.decompress(c) for c in cdatas],
                                  b"", rounds=2, size=total)
        print(f"| {level} | {ratio:.2f} | {c_speed:.0f} | {d_speed:.0f} |")


def lz4_compare():
    corpora = {"real /usr/share": build_real_corpus()[0],
               "synthetic §2": list(build_synthetic_corpus().values())}
    zstd_levels = [1, 3]
    lz4_levels = {"fast (0)": 0, "default (2)": 2, "max (16)": 16}
    print("| corpus | codec | level | ratio | compress MB/s | decompress MB/s |")
    print("|--------|-------|------:|------:|--------------:|----------------:|")
    for cname, files in corpora.items():
        if not files:
            continue
        total = sum(len(d) for d in files)
        for level in zstd_levels:
            cctx = zstd.ZstdCompressor(level=level)
            dctx = zstd.ZstdDecompressor()
            cdatas = [cctx.compress(d) for d in files]
            ratio = total / sum(len(c) for c in cdatas)
            c_speed = throughput_mb_s(lambda _: [cctx.compress(d) for d in files],
                                      b"", rounds=2, size=total)
            d_speed = throughput_mb_s(lambda _: [dctx.decompress(c) for c in cdatas],
                                      b"", rounds=2, size=total)
            print(f"| {cname} | zstd | {level} | {ratio:.2f} | {c_speed:.0f} | {d_speed:.0f} |")
        for name, level in lz4_levels.items():
            cdatas = [lz4.frame.compress(d, compression_level=level) for d in files]
            ratio = total / sum(len(c) for c in cdatas)
            c_speed = throughput_mb_s(
                lambda _: [lz4.frame.compress(d, compression_level=level) for d in files],
                b"", rounds=2, size=total)
            d_speed = throughput_mb_s(lambda _: [lz4.frame.decompress(c) for c in cdatas],
                                      b"", rounds=2, size=total)
            print(f"| {cname} | lz4 | {name} | {ratio:.2f} | {c_speed:.0f} | {d_speed:.0f} |")


def _compress_worker(files, results, idx, level=3):
    cctx = zstd.ZstdCompressor(level=level)
    t0 = time.perf_counter()
    n = 0
    for d in files:
        cctx.compress(d)
        n += len(d)
    results[idx] = n / (time.perf_counter() - t0) / 1e6


def concurrent():
    files, total = build_real_corpus(TARGET_BYTES // 2)
    if not files:
        print("no real corpus available on this host; skipping")
        return
    print(f"corpus: {len(files)} files, {total:,} bytes; zstd level 3\n")
    print("| threads | aggregate MB/s | per-thread MB/s | scaling vs 1 |")
    print("|--------:|---------------:|----------------:|-------------:|")
    baseline = None
    for threads in (1, 2, 4, 8):
        chunks = [files[i::threads] for i in range(threads)]
        results = [0.0] * threads
        workers = [threading.Thread(target=_compress_worker,
                                    args=(chunks[i], results, i))
                   for i in range(threads)]
        wall0 = time.perf_counter()
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        wall = time.perf_counter() - wall0
        agg = total / wall / 1e6
        per = sum(results) / threads
        if threads == 1:
            baseline = agg
        print(f"| {threads} | {agg:.0f} | {per:.0f} | "
              f"{(agg / baseline if baseline else 0):.2f}x |")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--lz4", action="store_true")
    parser.add_argument("--concurrent", action="store_true")
    args = parser.parse_args()
    none = not (args.real or args.lz4 or args.concurrent)
    if args.real or none:
        print("=== real-asset corpus zstd level sweep ===\n")
        sweep_real()
    if args.lz4 or none:
        print("\n=== LZ4 fast path vs zstd ===\n")
        lz4_compare()
    if args.concurrent or none:
        print("\n=== concurrent compression scaling ===\n")
        concurrent()


if __name__ == "__main__":
    main()
