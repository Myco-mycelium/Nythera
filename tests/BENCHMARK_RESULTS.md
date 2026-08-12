# Nyrqis Benchmark Results — First Pass

**Date:** 2026-08-12
**Environment:** Linux 6.14.0-37-generic, x86_64, glibc 2.39, Python 3.12.3
**CPU:** Intel Core i3-2120 @ 3.30 GHz (4 cores)
**Methodology:** `python3 tests/benchmarks.py` (source in this directory)

Per NPC-002 §5.2, these are **real measurements** with their methodology
and environment recorded alongside. They are **first-pass
microbenchmarks**, not the full plan methodology from
`tests/BENCHMARK_PLAN.md` — each row below says exactly what was and was
not measured. No number here is asserted beyond its own measurement.

## 1. IPC Round-Trip Latency (BENCHMARK_PLAN §1)

The `call` primitive (NPS-003 §3), client thread → service endpoint →
reply, in-process `IPCManager`. Endpoints were given a deliberately high
token budget so the distribution measures the **control-plane primitive
latency**, not the rate limiter (which is measured separately in §3
below). 20,000 iterations, 64-byte payloads, warmup 200 calls.

| Metric | Value |
|--------|-------|
| p50 | 92.03 µs |
| p95 | 156.64 µs |
| p99 | 212.58 µs |
| mean | 103.91 µs |
| max | 5,019 µs |

**What this does NOT measure (honest scope):** the plan calls for two
containers under load variants on two hardware classes. The backend's
transport layer (Unix-domain socket / shared memory) is deferred, so
these numbers bound the in-process control-plane cost only — the final
wire cost will be higher, and the `NPS-003` §6.1 target (< 100 µs) can
only be judged against the real transport once it exists.

## 2. IPC Token-Bucket Defaults (BENCHMARK_PLAN §3 data point)

A single client thread calling a default-budget endpoint for 2 seconds,
counting `None` returns as throttled (`send_message` refuses a token and
`call` returns `None`; it does not raise).

| Metric | Value |
|--------|-------|
| Successful round-trips (2 s) | 199 |
| Throttled calls (2 s) | 37,750 |
| Sustained rate | ~99.5 calls/s |
| Throttle rate at full speed | ~18,875 calls/s |

This is a genuine finding for ADR-0009: the default
`TokenBucket(bucket_size=100, tokens_per_second=50)` caps a single
client→endpoint call path at ~50 calls/s steady state (100 burst then
refill), which would throttle legitimate high-frequency traffic (input
delivery, audio, NPS-012 §6) by orders of magnitude. The plan's
legitimate-traffic baseline and adversarial flooding test are still
needed, but the default parameters are already demonstrably too low for
this workload shape.

## 3. NyFS Operation-Throughput vs Native (BENCHMARK_PLAN §4 proxy)

8 MiB written and read through the FUSE operation handlers
(`NyFSOperations`, CoW + Zstd active) vs native file I/O on the same
disk/tmpdir. Small-file creation: 1,000 files via `mknod`.

| Metric | Value |
|--------|-------|
| Native write | 884 MB/s |
| Native read | 2,095 MB/s |
| NyFS write | 40.5 MB/s (+2,085%) |
| NyFS read | 242 MB/s (+766%) |
| Small-file create | ~29,400 files/s |

**Structural note (why the overhead is what it is):** this implementation
stores each inode's content as a single merged block, so every 4 KiB
write merges + recompresses the full 8 MiB buffer and every read
decompresses it. The overhead measured here is therefore dominated by the
whole-file CoW/compress path, not by FUSE context switches. The plan's
actual question — FUSE-vs-ext4 with a live kernel mount — is **still
pending**: it requires `fusepy` + `/dev/fuse`, and the per-block CoW
rewrite is prerequisite work before those numbers mean anything for
gaming workloads (NPS-006 §5).

## 4. Zstd Compression-Level Sweep (BENCHMARK_PLAN §2)

`python3 tests/benchmark_zstd.py`, synthetic corpus (text-like 180 KB,
structured media-like 2 MiB, incompressible 1 MiB), levels 1–22, 1 MiB
measurement chunks, throughput averaged over 4–8 rounds. Environment as
in the header above.

| Level | Overall ratio | Compress MB/s | Decompress MB/s |
|-------|--------------:|--------------:|----------------:|
| 1     | 2.54 | 3,199 | 3,692 |
| 3     | 2.54 | 3,479 | 3,313 |
| 5     | 2.54 | 2,700 | 3,137 |
| 7     | 3.17 | 2,095 | 2,135 |
| 9     | 3.17 | 1,435 | 2,849 |
| 11    | 3.17 | 2,009 | 3,024 |
| 13    | 3.17 | 810  | 2,223 |
| 15    | 3.17 | 864  | 2,700 |
| 17    | 3.17 | 675  | 2,688 |
| 19    | 3.17 | 621  | 2,696 |
| 22    | 3.17 | 621  | 2,768 |

**Honest caveats:** (1) the "media" slice is a repeating structured
pattern that only higher levels' larger search windows fully exploit
(reaching ~4,000× on that slice at level 17+), so the absolute ratios are
synthetic and not a prediction of real texture compression — the useful
signal is the *shape*: overall ratio flatlines above level ~7 while
compression cost keeps rising. (2) Incompressible data passes through at
ratio 1.00 at every level, as expected for the NPS-004 §4.5 pass-through
path. (3) The LZ4 fast-path comparison (ADR-0007) is not yet run —
`python-lz4` is unavailable in this environment.

**Observation for ADR-0007 / NPS-005 §3 (informative, not a decision):**
on this corpus the ratio/compute knee sits around levels 3–7: level 3
compresses at ~3.5 GB/s with the same ratio as level 1, and levels ≥ 7
buy ~25% more ratio at 40–80% of the compression throughput. The actual
default-level decision remains NPS-005 §3's table, pending Architecture
Group review.

## Status vs BENCHMARK_PLAN

| Plan section | Status |
|--------------|--------|
| §1 IPC round-trip latency | First-pass data collected (in-process only; real transport + load variants pending) |
| §2 Zstd level selection | First-pass data collected (synthetic corpus; real asset corpus, LZ4 comparison, and concurrent-load CPU measurement pending) |
| §3 Token-bucket parameters | First-pass data collected (defaults shown to throttle this workload shape); sweep + adversarial test pending |
| §4 FUSE overhead | Proxy data collected (ops-layer vs native); live-mount comparison pending `/dev/fuse` + per-block CoW work |

Nothing in `BENCHMARK_PLAN.md`'s gates has been declared met on the
strength of this first pass; these numbers exist to inform the next
implementation steps, not to close the benchmarks.

## Where these numbers landed (2026-08-12)

- **NPS-003 §6.1** (v1.2.0): benchmark note added — the <100 µs target is
  met at the median (p50 92 µs) and exceeded at the tail (p95/p99); the
  document remains `Draft` because the real transport is pending.
- **ADR-0009** (v1.1.0): benchmark-data section added — default bucket
  parameters shown to throttle this workload shape; status stays
  `Proposed` pending the parameter sweep and Architecture Group review.
- **NPS-010 §9** (v1.2.0): status note updated with the ADR-0009 data.
- **NPC-004 / NPC-007 / REPOSITORY_STATE.md**: statuses and checklist
  items reconciled to "first-pass data collected" — no gate declared met.
