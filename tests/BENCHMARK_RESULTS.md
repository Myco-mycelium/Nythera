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

**Structural note (why the overhead is what it is):** when these numbers
were collected, the implementation stored each inode's content as a
single merged block, so every 4 KiB write merged + recompressed the full
8 MiB buffer and every read decompressed it. That whole-file
CoW/compress path was therefore the dominant cost, not FUSE context
switches. **Per-block CoW landed 2026-08-12** (`fuse/nyfs.py` — fixed
64 KiB blocks; a write rebuilds only the blocks it overlaps), so these
proxy numbers are now **superseded and must be re-run** before they are
quoted again. The plan's actual question — FUSE-vs-ext4 with a live
kernel mount — is **still pending**: it requires `fusepy` + `/dev/fuse`
access (not available in this environment), and remains the prerequisite
for judging the FUSE decision for gaming workloads (NPS-006 §5).

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

## 5. Consolidated Run After Per-Block CoW (2026-08-12, re-run)

`python3 tests/benchmarks.py --all` now runs every runnable plan section
(§1 IPC, §3 token-bucket, §2 Zstd, §4 NyFS proxy) in one reproducible
script. This re-run happened **after** the per-block CoW rewrite
(`fuse/nyfs.py`), so the §4 numbers below replace the §3 proxy row.

### §1 / §3 (unchanged shape, re-confirmed)

| Metric | Value |
|--------|-------|
| IPC p50 / p95 / p99 | 88.12 / 123.65 / 215.27 µs |
| IPC mean / max | 96.67 / 2,633 µs |
| Default bucket sustained | ~99.5 calls/s (199 in 2 s) |
| Throttled at full speed | ~18,230 calls/s |

These are a re-run of §1/§2 above on the same in-process path — the
small differences (e.g. p95 156.64 µs first pass vs 123.65 µs here) are
run-to-run variance, not a methodology change.

### §4 NyFS vs native, per-block CoW — access-pattern dependent

| Access pattern | NyFS | Native | Note |
|----------------|-----:|-------:|------|
| 4 KiB sequential write | ~3.6 MB/s | 541–771 MB/s | per-call overhead dominates: every 4 KiB write decompresses + recompresses + SHA-256s a full 64 KiB block |
| 1 MiB-chunk streaming write | **~162–170 MB/s** | 541–771 MB/s | per-block CoW's write-amplification win — ~4× the old whole-file path (40.5 MB/s) |
| 4 KiB scattered write (16 MiB space, fixed seed 7) | ~1.0 MB/s | — | worst case: every write re-encodes a touched block, and sparse blocks must be materialized |
| 4 KiB sequential read | ~2.8 MB/s | 1,064–2,131 MB/s | **per-read SHA-256 verification dominates**: simulating the same loop without the verify step runs at ~557 MB/s vs ~15.5 MB/s with it (35×) |
| Small-file create | ~28,600–30,200 files/s | — | |

**Block-size sweep, 4 KiB-write pattern (tuning data, not a decision):**
block_size 4 KiB → 1.51 MB/s, 16 KiB → 3.6, 64 KiB → 4.1, 256 KiB → 1.6.
No block size rescues the 4 KiB-write pattern — the cost is per-call
(decompress + recompress + checksum of the touched block), not block
size per se. The 64 KiB default sits at the knee and wins for streaming;
per-block CoW's real benefit is bounded per-write amplification on
larger writes, not small scattered I/O.

**Findings for implementation (recorded, not gates met):**
1. Per-block CoW **delivers its design win** for streaming writes
   (~162 MB/s vs 40.5 MB/s whole-file), but the benchmark's original
   small-op shape (4 KiB writes/reads) is now dominated by per-call
   checksum + compression of the full block. These numbers replace the
   §3 proxy row; no FUSE-vs-ext4 judgment is implied (no live mount).
2. **Per-read SHA-256 verification is the single largest read cost**
   (NPS-004 §4.3 requires detection on read; caching the verification
   would trade that guarantee away and is NOT proposed). A cheaper
   integrity mechanism for the hot path (e.g. block-level checksum on
   metadata load + hashing only on explicit `fsck`-style verification)
   is a design question for Architecture Group review, not something
   this benchmark decided.

## Status vs BENCHMARK_PLAN

| Plan section | Status |
|--------------|--------|
| §1 IPC round-trip latency | First-pass data collected (in-process only; real transport + load variants pending) |
| §2 Zstd level selection | First-pass data collected (synthetic corpus; real asset corpus, LZ4 comparison, and concurrent-load CPU measurement pending) |
| §3 Token-bucket parameters | First-pass data collected (defaults shown to throttle this workload shape); sweep + adversarial test pending |
| §4 FUSE overhead | Proxy data **re-run after the per-block CoW rewrite (2026-08-12)** — streaming writes ~162 MB/s (4× the old path), small-op pattern dominated by per-call block compress + per-read checksum verify (see §5); live-mount comparison still pending `/dev/fuse` access |

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
