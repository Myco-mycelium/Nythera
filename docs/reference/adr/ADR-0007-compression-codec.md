---
title: Adopt Zstandard as the Default Compression Codec
document_id: ADR-0007
version: 1.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-07-12
updated: 2026-09-10
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0002]
---

# ADR-0007 — Zstandard as the Default Compression Codec

## Context
ADR-0002 commits Nyrqis to a filesystem with built-in, transparent
compression, but does not select a codec. The original design discussion
raised LZ4, Zstandard, and Oodle as candidates. A codec decision is a
prerequisite for both NPS-004 (filesystem core) and NPS-006 (game image
format), since both need a concrete default to specify against.

The three candidates trade off differently:

- **LZ4** — extremely fast decompression, modest compression ratio.
- **Zstandard (Zstd)** — tunable levels spanning LZ4-like speed at low
  levels to much stronger ratios at high levels; open source, widely
  supported, no licensing cost.
- **Oodle** — strong ratio and speed, but proprietary and licensed, which
  conflicts with keeping the platform's core compression path
  unencumbered and auditable.

## Decision (Proposed)
Adopt **Zstandard** as the default codec for both NyFS (ADR-0002) and game
images (ADR-0003), used at a low compression level (favoring decompression
speed) for actively-read data such as installed applications and games, and
a higher compression level for cold/archival data, per the "unused files
can be recompressed more aggressively" goal from the original design
discussion.

**LZ4** remains available as an explicit per-region override for
latency-critical paths (e.g. the storage I/O fast path defined in kernel
space by NPS-001 §3) where Zstd's decompression cost, even at low levels,
is measurable.

Oodle is rejected as the default: a proprietary, licensed codec on the
default read/write path for every application and game would conflict with
NTM-000 §7 ("Community") and §4 ("Transparency") by making a core platform
behavior dependent on a closed, licensed component. It **MAY** still be
supported as an optional, user-installed codec for specific titles that
ship Oodle-compressed assets, without becoming the platform default.

## Alternatives Considered
- **LZ4 as the sole default** — rejected; ratio is too low to meaningfully
  reduce install sizes, which was an explicit goal of the compression
  feature.
- **Oodle as the default** — rejected per above; licensing cost and closed
  source conflict with platform principles.
- **Per-file-type codec selection at install time** — deferred, not
  rejected; NPS-004/NPS-006 MAY allow this as a future refinement once a
  single default is implemented and measured.

## Consequences
- NPS-004 and NPS-006 MUST specify Zstd as the default codec and MUST
  define the LZ4 fast-path override mechanism.
- Compression level defaults MUST be benchmarked (install size vs. load
  time) before this ADR can move to Accepted, per NPC-002 §5.2.
- Hardware decompression acceleration, mentioned in the original design
  discussion, is out of scope for this ADR and deferred to a future
  hardware-support NPS once specific hardware targets are chosen.

## Benchmark Data (2026-09-10, close-out)

The three remaining benchmark blockers are collected
(`tests/benchmark_adr0007.py`, results in `tests/BENCHMARK_RESULTS.md`
§31; joins the 2026-08-12 synthetic sweep §2, the end-to-end NyFS
ratios §7/§12, and the zstd-vs-zlib codec comparison §11):

- **Real-asset level sweep** (265 files / 8.4 MB deterministic
  `/usr/share` sample): overall ratio is flat (~1.07–1.09) at every
  level 1–22 — on already-compressed real data, levels ≥7 buy ≤2% ratio
  for 60× less compression throughput (408 → 6 MB/s). Combined with §2's
  synthetic knee (ratio flatlines above level ~7), the ratio argument
  for any level above 3 is closed.
- **Real LZ4 fast path** (`lz4.frame`, now available — the §11 zlib
  approximation is retired): ~2.7× zstd-1's compression throughput at
  equal ratio (1.06 vs 1.07) on the real corpus, and ~12–16× on the
  synthetic text-like corpus (where LZ4's parser is also ~20% better;
  that shape flatters LZ4 — honest caveat). The fast-path override the
  decision names is worth having; the Zstd default keeps the mid-range
  robustness §2 documented.
- **Concurrent compression** (zstd-3, real corpus, 1/2/4/8 threads):
  aggregate throughput scales to ~2.2× at 8 threads — zstandard
  partially releases the GIL; concurrent daemon commits get real but
  sub-linear parallel compression.

The level-choice data is now complete. The default level (data supports
a low default, 1–3) remains an Architecture Group decision per NPS-005
§3.

## Status
Proposed — benchmark data complete (2026-08-12 first pass + 2026-09-10
close-out); pending Architecture Group review for the default-level
choice.
