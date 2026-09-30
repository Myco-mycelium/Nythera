---
title: Journal Commit as the Default NyFS save() Mode
document_id: ADR-0019
version: 1.1.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-08-12
updated: 2026-09-30
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

## Status

**Accepted** — RATIFIED AS-IMPLEMENTED, Bundle D decided 2026-09-30
(owner direction via the recorded session, the E1/F1 same-day-landing
shape): the three ledgers from issue #1 resolved per
AG_BRIEF_ADR0019's recommendation — (1) the fixed 60 s watcher
cadence retained as a tuning knob, not a correctness property; (2)
`auto_compact=True` ratified as the shipped default (the ADR-0022
as-implemented shape — the mechanism has run as the de-facto default
since 2026-08-12 with the measured 60–70×/61× commit wins on record);
(3) the shutdown ordering confirmed as implemented (stop watcher →
dirty-gated final save → unmount). The watcher's resource profile on
real hardware is recorded as a tuning follow-up, NOT an acceptance
blocker (the compaction cost is bounded by `journal_compact_bytes`;
the dirty gate bounds what an interrupted pass can lose). Decision
record: AG_AGENDA decision-log row D1 (2026-09-30); the frontmatter
and every index read Accepted on this ruling. Issue #1 is resolved by
this decision (the close itself is a manual owner step — the PAT
cannot close issues). Open questions 2 and 3 resolve with the
ratification: 64 MiB stands as the measured, six-week-shipped
default; ~0.3% steady-state overhead is accepted as the known-cost
ledger (the ADR-0022 precedent). This ruling supersedes and sanctions
the governance defect it closes: the 2026-09-06 self-flip (reverted
2026-09-20 as unsanctioned) is hereby given the Group record it
lacked. (v1.1.0 — status Accepted; the staged draft paragraph below
is retained as the pre-stage record.)

**[RATIFY-VARIANT DRAFT — SUPERSEDED BY THE RULING ABOVE 2026-09-30.
Staged 2026-09-27 inside AG_BRIEF_ADR0019 v1.1.0 §6; landed with the
2026-09-30 ruling date per the E1/F1 same-day convention.]**

**RATIFIED AS-IMPLEMENTED** — Bundle D decided 2026-09-27: the
three ledgers from issue #1 resolved per AG_BRIEF_ADR0019's
recommendation (fixed 60 s cadence retained as a tuning knob;
`auto_compact=True` ratified as the shipped default — the ADR-0022
as-implemented shape; shutdown ordering confirmed). The watcher's
resource profile on real hardware is recorded as a tuning follow-up,
NOT an acceptance blocker (the cost is bounded by
`journal_compact_bytes`; the dirty gate bounds an interrupted pass).
Decision record: AG_AGENDA decision-log row D1 (2026-09-27); the
frontmatter and every index now read Accepted on this ruling. Issue #1
is resolved by this decision (the close itself is a manual owner step
— the PAT cannot close issues). Open questions 2 and 3 in this ADR
resolve with the ratification: 64 MiB stands as the measured,
six-week-shipped default; ~0.3% steady-state overhead is accepted as
the known-cost ledger (the ADR-0022 precedent). The "tuning pending AG
review" caveats are stripped from `DAEMON_LIFECYCLE.md` accordingly.

---

**Proposed** — implemented 2026-08-12, default flipped in `fuse/nyfs.py`.
Journal commit is now the default save mode. Benchmark evidence in
`tests/BENCHMARK_RESULTS.md` §7–§9, §12–§14. Architecture Group
review pending. (Corrected 2026-09-20: a 2026-09-06 frontmatter edit
flipped this to Accepted without any Group decision record — this
section's own governance line and the ADR index both read Proposed,
so the frontmatter is reverted to match. The ADR-0025/0026 statuses
carry the same 2026-09-06 flag and are recorded in the index.)
**Superseded 2026-09-30 by the Accepted ruling at the top of this
section** — retained as the historical record of the Proposed period
and the 09-06/09-20 governance correction.
