---
title: AG Brief — ADR-0019 Journal-Commit Default and the Daemon Lifecycle (issue #1, Bundle D)
document_id: AG-BRIEF-ADR0019
version: 1.0.0
status: Proposed
classification: Internal
owners:
  - Nyrqis Engineering
created: 2026-09-27
updated: 2026-09-27
ai_assisted: true
review_cycle: One sitting
depends_on: [ADR-0019, NPS-004, ADR-0016, NPC-001]
---

# AG Brief — ADR-0019: the auto_compact Tuning Review (issue #1)

## 1. Status of This Document

This is the decision package for Bundle D — the ADR-0019 tuning review
(issue #1, open since 2026-08-12), staged on AG_AGENDA 2026-09-25. It
follows the D3/D4/duality/DBG-PhaseB/CRY001/UPD001 brief format:
verified evidence, per-decision ledgers, one recommendation,
overridable. The brief proposes; the Group decides. Bundle D differs
from E and F in one respect: the code has been shipped and running as
the de-facto default since 2026-08-12 — the sitting adjudicates a
governance record, not a proposal.

One cautionary precedent is part of the evidence base: ADR-0019's
2026-09-06 self-flip to `Accepted` (no Group record) was reverted
2026-09-20 as unsanctioned — per the ADR's own governance text and the
index. What this sitting produces IS the missing record.

## 2. The Verified Evidence (tree-verified 2026-09-25, re-verified 2026-09-27)

**The measured case for the default (ADR-0019, review data):**
`save()` under per-block fsyncs is fsync-bound — 11–15 s for the
17.1 MB / 417-block corpus, 123 s for the 3,855-file corpus
(`tests/BENCHMARK_RESULTS.md` §7/§12). The append-only commit journal
with ONE fsync per transaction commits the same corpora in **0.20 s
(~60–70×)** and **2.0 s (~61×)** at ~0.3% on-disk overhead (§9); a
mixed read/write/commit loop holds the win at ~3.7–4× lower per-commit
latency (§13). The cost is deferred, not removed: compaction past
`journal_compact_bytes` (64 MiB default) is an interleaved save of the
referenced blocks (~27 ms/block, §14) — roughly an 11 s background
pass per ~2.5 MB of new blocks. Batched fsync measured as noise on a
single disk (§8); larger blocks cut commit time 40–60% at small-write
amplification cost.

**The as-built mechanism (all claims pinned re-runnably in the premise
registry — `agenda-b1-autocompact-pin`, `agenda-b1-autocompact-test-pin`,
`agenda-b1-defaults-pin`):**

| Claim | Where | Status |
|---|---|---|
| `auto_compact: bool = True` at the mount | `fuse/nyfs.py:1864` | shipped; resurfaced once in `backend/container.py` |
| Dedicated default pin | `test_backend.py::test_auto_compact_is_the_mount_default` | exists, exactly once |
| Dirty gate | `NyFSFilesystem._dirty` set by every mutation, cleared by `save()`; `shutdown()` commits only when dirty | implemented per `DAEMON_LIFECYCLE.md` §2 |
| Shutdown ordering | stop watcher → dirty-gated final save → unmount; `SIGINT`/`SIGTERM` handlers via `mount(handle_signals=True)` (the blocking-mode default) | implemented; `test_shutdown_commits_dirty_state`, `test_dirty_flag_tracking` |
| Crash atomicity unchanged | journal fsynced BEFORE the metadata swap — new metadata never references un-durable entries; `load()` falls back to the journal and tolerates torn tails | NPS-004 §7.1 contract preserved |

**The governance record:** the flip landed 2026-08-12 (`40cb4e8`) with
the full suite green under the flipped default; issue #1 has tracked
the review ever since. The mechanism has run as the de-facto default
ever since — the defect was the unrecorded flip, never the code.

## 3. The Regulatory Frame

Per NPC-001, an `Accepted` ADR requires a Group record — the 09-06
self-flip incident is the standing caution, and this sitting is the
sanctioned path. Per NPS-004 §7/§7.1, the durability and crash-
atomicity contracts must hold: the journal-before-swap ordering is the
mechanism by which ADR-0019 preserves them (verified as implemented).
Per ADR-0016 (NyFS FUSE-first, Accepted), the daemon lifecycle this
ADR's shutdown contract sits on is accepted substrate. The closest
governance analog is ADR-0022's as-implemented ratification
(2026-09-19): the Group CONFIRMED a shipped default with the
performance record as the known-cost ledger — the same shape as
decision 2 below.

## 4. The Three Decision Ledgers (from issue #1)

### Decision 1 — Compaction cadence

- **Keep the fixed 60 s watcher interval** (as shipped): simple,
  audited, and the compaction cost is bounded by
  `journal_compact_bytes`, not by the timer.
  - Pros: zero churn; the interval is a tuning knob, not a correctness
    property. Cons: idle daemons wake on a timer; a bursty writer can
    still hit the byte threshold first.
- **Derive from journal growth / a multiple of the fsync period:**
  adaptive, fewer wasted wakes.
  - Cons: new mechanism + new test surface for a tuning gain; no
    benchmark data currently distinguishes the postures.

### Decision 2 — Default posture

- **Ratify `auto_compact=True` as shipped** (as-implemented, the
  ADR-0022 shape): the mechanism has run as the default for six weeks,
  the suite is green, the measured win is 60–70×, and demotion would
  churn a shipped default without new data.
  - Cons accepted knowingly: the watcher's resource profile is not yet
    documented on real hardware (see the open question).
- **Demote to opt-in** pending the resource-profile documentation.
  - Pros: conservative. Cons: reverses a measured, pinned, six-week
    de-facto default on a documented-feeling concern rather than a
    measured failure; `shutdown()`'s dirty gate means the risk surface
    is the background pass, not data loss.

### Decision 3 — Shutdown ordering

- **Confirm as implemented** (stop watcher → dirty-gated final save →
  unmount, signal-safe best-effort): matches `DAEMON_LIFECYCLE.md` §2
  and the contract tests exactly.
  - Cons: none recorded; amendment would need a concrete failure the
    record does not contain.
- **Amend:** only on evidence the record lacks.

## 5. Recommendation

**Ratify as-implemented in all three ledgers** (fixed cadence
retained, `auto_compact=True` ratified, shutdown ordering confirmed),
with **the watcher's resource profile on real hardware flagged as the
one open mechanism question** — recorded as a tuning follow-up, not an
acceptance blocker (the same defer-the-tuning move the D1 static-
default decision made). Ratification is safe to grant before the
resource profile exists because the compaction cost is already bounded
by `journal_compact_bytes` and the dirty gate already bounds what an
interrupted pass can lose. If the Group prefers demotion, the scoped
revision is: document the watcher profile first, re-stage, decide.

**What a decision unlocks:** stripping the "tuning pending AG review"
caveats from ADR-0019 and `DAEMON_LIFECYCLE.md`; recording the outcome
as an ADR-0019 amendment (or follow-up ADR); a clean acceptance (or a
scoped revision) closes issue #1 — the close itself is a manual owner
step (the PAT cannot close issues). DECISION-READY — purely judgment.
