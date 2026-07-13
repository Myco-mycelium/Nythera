# Nythera Benchmark Plan

This document defines **methodology**, not results. Per NPC-002 §5.2,
performance and compatibility claims MUST NOT be published in `Accepted`
documents until backed by a reproducible test referenced here. No numbers
in this file are real measurements — they are pending, and every entry
below is currently unstarted.

This file exists so that when implementation begins, "how do we benchmark
this" is already answered, rather than being designed under time pressure
alongside the code it's meant to validate.

## 1. IPC Round-Trip Latency (blocks NPS-003 exiting Draft)

**Question:** What is the `call` primitive's (NPS-003 §3) round-trip
latency under representative load?

**Method:**
- Microbenchmark: two containers, one issuing `call` in a tight loop to
  the other, measuring p50/p95/p99 latency.
- Load variants: idle system, system under IPC load from N other
  containers (N = 1, 10, 100), system under concurrent NyFS I/O load.
- Run on at least two hardware classes once available (a representative
  desktop/gaming target and a representative handheld/phone target), since
  NTM-000 targets both.

**Pass/fail gate:** No fixed target exists yet. A reasonable first-pass
gate — input-to-frame latency budget allows only a small fraction for IPC
overhead — should be derived once NPS-012 §6.1's input-delivery latency
requirements are made concrete, not asserted here in advance of that work.

## 2. Zstd Compression Level Selection (blocks ADR-0007 exiting Proposed)

**Question:** What Zstd compression level(s) give the best install-size
vs. load-time tradeoff for NyFS (NPS-004 §4.5) and game images (NPS-006)?

**Method:**
- Corpus: a representative mix of application binaries, textures/media
  assets, and already-compressed formats (to validate the incompressible
  pass-through path, NPS-004 §4.5).
- Measure, per candidate level: compressed size ratio, decompression
  throughput (MB/s), and CPU cost during decompression under concurrent
  load (simulating game-load-time contention with other system activity).
- Compare against the LZ4 fast-path override (ADR-0007) on the same
  corpus, to validate the "explicit per-region override" design actually
  earns its complexity.

**Pass/fail gate:** None yet — this benchmark's output *produces* the
default level decision in NPS-005 §3's table, it doesn't validate a
pre-committed number.

## 3. IPC Token-Bucket Parameters (blocks ADR-0009 exiting Proposed)

**Question:** What default refill rate and burst capacity (ADR-0009)
prevent endpoint-flooding denial-of-service without throttling legitimate
high-frequency traffic (e.g. input delivery, NPS-012 §6.2)?

**Method:**
- Establish a legitimate-traffic baseline: measure real IPC call rates
  from input delivery, audio, and typical application chatter under normal
  use (requires the IPC latency benchmark in §1 as a prerequisite, since
  both need the same instrumented IPC path).
- Adversarial test: a deliberately flooding container attempting to
  degrade a target container's IPC throughput; measure whether candidate
  bucket parameters bound the impact without needing reactive detection.
- Sweep candidate (refill rate, burst capacity) pairs against both the
  baseline and adversarial tests to find the range that passes both.

**Pass/fail gate:** Legitimate traffic from §1's baseline MUST NOT be
throttled under normal operation; adversarial flooding MUST be bounded to
a defined maximum impact on other containers' latency (exact bound TBD
from this benchmark's own findings).

## 4. FUSE Overhead for NyFS Linux Backend (referenced by ADR-0016)

**Question:** Is FUSE's per-operation overhead acceptable for gaming
workloads, or does it require falling back to a kernel module (ADR-0016
§"Alternatives Considered")?

**Method:**
- Compare NyFS-over-FUSE against a native ext4/Btrfs baseline on identical
  hardware, for both random small-file access (many game assets) and large
  sequential reads (asset streaming, NPS-006 §5).
- Measure with and without the transparent compression path (§2's Zstd
  work) active, since decompression + FUSE context-switch overhead compound.

**Pass/fail gate:** None yet — ADR-0016 already commits to trying FUSE
first; this benchmark determines whether that decision holds or needs
revisiting, not whether to attempt it at all.

## Status

All four benchmarks are **Not Started** — they require a working
implementation of the subsystem being measured, which does not yet exist
(see `REPOSITORY_STATE.md`: source code is "Not started"). This document
should be revisited once Linux Backend implementation work (NPS-017 §6)
produces something runnable to actually measure.
