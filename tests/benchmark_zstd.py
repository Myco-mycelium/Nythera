#!/usr/bin/env python3
"""Zstd compression-level selection benchmark (BENCHMARK_PLAN §2).

Methodology (per tests/BENCHMARK_PLAN.md §2): a representative corpus of
application-text-like, media-like, and already-compressed (incompressible)
data; per candidate level we measure compressed-size ratio, compression
throughput, and decompression throughput. This first pass uses a
synthetic corpus (documented honestly); a real asset corpus is future
work. Output is the input for the default-level decision in NPS-005 §3.

Run: python3 tests/benchmark_zstd.py
"""

import os
import time
import zstandard as zstd

LEVELS = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 22]
CHUNK = 1 << 20  # 1 MiB measurement chunks
ROUNDS = 8


def build_corpus():
    """Synthetic representative corpus; honest about what it is not."""
    # Text-like: highly compressible prose (game dialogue/config-like).
    text = ("The quick brown fox jumps over the lazy dog. " * 4000).encode()
    # Binary/media-like: lower-entropy structured noise (asset-like).
    media = bytes((i * 7 + (i >> 3)) % 256 for i in range(1 << 20)) * 2
    # Already-compressed / incompressible: /dev/urandom-like stream.
    try:
        rnd = os.urandom(1 << 20)
    except Exception:
        rnd = bytes(i % 251 for i in range(1 << 20))
    corpus = {"text": text, "media": media, "incompressible": rnd}
    total = sum(len(v) for v in corpus.values())
    print(f"corpus: text={len(text)} media={len(media)} "
          f"incompressible={len(rnd)} (total {total} bytes)")
    return corpus


def throughput(fn, data, rounds=ROUNDS, size=None):
    """Return MB/s for fn(data) averaged over rounds.

    ``size`` is the byte count the work actually produces/consumes
    (defaults to ``len(data)``). For compression that is the input
    length; for decompression it must be the DECOMPRESSED length —
    measuring decompression throughput against the compressed input
    would understate it by the compression ratio (fixed 2026-08-12;
    earlier §2 numbers used the compressed size and are corrected in
    BENCHMARK_RESULTS.md §2).
    """
    if size is None:
        size = len(data)
    start = time.perf_counter()
    for _ in range(rounds):
        fn(data)
    elapsed = time.perf_counter() - start
    return (size * rounds) / elapsed / 1e6


def bench_level(level, corpus):
    cctx = zstd.ZstdCompressor(level=level)
    dctx = zstd.ZstdDecompressor()
    results = {}
    total_in = 0
    total_out = 0
    for name, data in corpus.items():
        cdata = cctx.compress(data)
        ratio = len(data) / len(cdata)
        total_in += len(data)
        total_out += len(cdata)
        # compression throughput on this slice
        c_speed = throughput(lambda d: cctx.compress(d), data, 4)
        # decompression throughput (measured against the decompressed
        # output size — see throughput())
        d_speed = throughput(lambda d: dctx.decompress(d), cdata, 4,
                             size=len(data))
        results[name] = (ratio, c_speed, d_speed)
    overall_ratio = total_in / total_out
    return results, overall_ratio


def main():
    corpus = build_corpus()
    header = (
        "| Level | text ratio | media ratio | incompressible ratio | "
        "overall ratio | compress MB/s | decompress MB/s |"
    )
    sep = "|-------|-----------:|-----------:|---------------------:|"
    sep += "-------------:|--------------:|----------------:|"
    print(header)
    print(sep)
    for level in LEVELS:
        results, overall = bench_level(level, corpus)
        t_r, t_c, t_d = results["text"]
        m_r, m_c, m_d = results["media"]
        i_r, i_c, i_d = results["incompressible"]
        avg_c = (t_c + m_c + i_c) / 3
        avg_d = (t_d + m_d + i_d) / 3
        print(
            f"| {level} | {t_r:.2f} | {m_r:.2f} | {i_r:.2f} | "
            f"{overall:.2f} | {avg_c:.0f} | {avg_d:.0f} |"
        )


if __name__ == "__main__":
    main()
