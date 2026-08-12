---
title: Journal Commit as the Default NyFS save() Mode
document_id: ADR-0019
version: 1.0.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
depends_on: [NPS-004, ADR-0002, ADR-0007, ADR-0016]
---

# ADR-0019 — Journal Commit as the Default NyFS `save()` Mode

## Context

NPS-004 §7 defines NyFS durability: `save()` persists the filesystem
atomically, with per-block fsyncs followed by the atomic metadata swap
(write temp + fsync + rename), which is the commit point. First-pass
benchmarking found `save()` fsync-bound at ~27 ms/block
(`tests/BENCHMARK_RESULTS.md` §7): committing the 17.1 MB / 417-block
deterministic corpus takes **11–15 s**, and a real 3,855-file corpus
takes **123 s** (§12).

Two commit-cost levers were measured (§8): larger blocks cut commit
time ~40–60% (at small-write amplification cost — a 4 KiB random write
rebuilds a whole 1 MiB CoW block), and batched fsync (group all temps,
fsync all, rename all) is **noise on a single disk** — its win flips
sign run to run. The third candidate — an append-only commit journal —
was then implemented and measured (§9): appending every new block
payload to `state/journal.bin` with **one fsync per transaction**, then
the metadata swap, commits the same corpus in **0.20 s (~60–70×
faster)** at ~0.3% on-disk overhead, and the small-file corpus in
**2.0 s vs 123 s (~61×)**. A mixed read/write/commit loop (§13)
confirms the win holds under repeated transactions: ~3.7–4× lower
per-commit latency. The cost is deferred, not removed: the journal
compacts (materialize referenced blocks, truncate) past
`journal_compact_bytes` (default 64 MiB), and the compaction pass is
exactly an interleaved save of the referenced blocks (~27 ms/block,
§14) — a ~11 s background pass per ~2.5 MB of new blocks.

On 2026-08-12 the implementer flipped the default to journal commit,
with the full suite (99/99 at the flip, now 103/103) green under the
flipped default, and recorded that **Architecture Group review is the
formal governance step**. This ADR is that review package.

## Decision (Proposed)

**`save()` defaults to journal commit** (`use_journal=True`):

- Every new block payload is appended to the append-only
  `state/journal.bin` and the whole transaction is fsynced **once**.
- The atomic metadata swap remains the commit point, and the journal is
  fsynced *before* it — new metadata never references un-durable
  entries, so the NPS-004 §7.1 crash-atomicity contract is unchanged.
- `load()` falls back to the journal for blocks without `.bin` files
  and tolerates torn tails (a crash mid-append leaves at most garbage
  after the last valid record, which the scan stops at).
- Compaction (materialize referenced blocks into `state/blocks/`,
  truncate) triggers past `journal_compact_bytes` (64 MiB default) and
  is crash-safe: renames happen before the truncate, so a crash
  mid-compaction leaves the journal intact and the state loadable.
- Daemons get `maybe_compact()` / `compact_journal()` hooks and an
  opt-in `NyFSMount(auto_compact=...)` background watcher that trims
  the journal during idle intervals, so the materialize pass runs
  outside the transaction path.
- The interleaved path (`use_journal=False`, with `batched_fsync`)
  stays available for compatibility and benchmarking.

Rationale: commit latency is a first-class gaming-load metric
(checkpointing, save-scumming, quick-resume); the journal moves NyFS
commit cost from "per-block fsync on the transaction hot path" to
"amortized, background-able compaction" for ~0.3% on-disk overhead.

## Alternatives Considered

- **Keep fsync-per-block interleaved as the default** — rejected;
  measured 60–70× slower commits, with per-block fsync on the
  transaction hot path (the primary workload for a gaming OS).
- **Batched fsync as the default** — rejected; measured at noise level
  on a single disk (§8), with no contract or latency benefit.
- **Journal kept opt-in (interleaved stays default)** — rejected;
  leaves the slow path as the default for the primary workload, hiding
  the cost instead of paying it.
- **Grouped commit (several transactions sharing one fsync)** —
  deferred; changes the durability contract (a crash could lose more
  than one acknowledged transaction) and needs NPS-004 semantics work.
  The journal already delivers the latency win without a contract
  change.
- **io_uring / async writeback in the storage layer** — deferred;
  a kernel-side optimization, orthogonal to the user-space commit path.
  Revisit with the native NyKernel backend (ADR-0012).

## Consequences

Positive:
- ~60–70× faster commit on the primary corpus; small-file-heavy
  workloads transformed (123 s → 2.0 s, §12); mixed loops ~3.7–4×
  lower per-commit latency (§13); ~0.3% on-disk overhead (§9).

Negative:
- The journal grows between compactions, bounded at 64 MiB by
  save-time compaction; a daemon should run `auto_compact` or periodic
  `maybe_compact()` so a transaction is rarely the one that stalls on
  the materialize pass (§14 measures that pass at ~27 ms/block).
- `gc_blocks()` does not reclaim journal space (compaction does).
- `load()` reads from the journal fallback until compaction.

Governance: this ADR stays **Proposed** until Architecture Group
acceptance. The flip is reversible without migration
(`save(use_journal=False)`), so acceptance can land incrementally.

## Open Questions for the Architecture Group

1. Should `auto_compact` be the default in the FUSE daemon, or stay
   opt-in until daemon lifecycle (signals, shutdown ordering) is
   specified?
2. Is 64 MiB the right default `journal_compact_bytes` for the target
   game-image and checkpoint sizes, and should it scale with block
   size?
3. Is the ~0.3% steady-state on-disk overhead acceptable, or should
   the journal be compressed or moved to a separate device?

## References

- Evidence: `tests/BENCHMARK_RESULTS.md` §7–§9 (levers + journal), §12
  (real corpus), §13 (mixed workload), §14 (compaction cost).
- Implementation: `source/nyhal-linux-backend/fuse/nyfs.py`
  (`save`, `_journal_append_new`, `_scan_journal`,
  `_materialize_journal`, `journal_bytes`, `maybe_compact`,
  `compact_journal`, `NyFSMount(auto_compact=...)`); tests in
  `source/nyhal-linux-backend/test_backend.py`.
