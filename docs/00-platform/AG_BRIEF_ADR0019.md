---
title: AG Brief — ADR-0019 Journal-Commit Default and the Daemon Lifecycle (issue #1, Bundle D)
document_id: AG-BRIEF-ADR0019
version: 1.1.0
status: Proposed (decision-ready; §6 pre-stages the ratify variant's landing edits)
classification: Internal
owners:
  - Nyrqis Engineering
created: 2026-09-27
updated: 2026-09-27 (v1.1.0 — §6 added)
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

## 6. Pre-staged Disposition (v1.1.0, 2026-09-27 — the CRY-001/UPD-001
§7.1 precedent: acceptance converts to landed work without
re-planning)

The 2026-09-27 session landed Bundles E and F the same day their
Option A rulings arrived, with the gates (sweeps, pins, indexes)
reconciled in the same commit. Bundle D gets the same pre-stage: the
three variants' landing edits are drafted here so the sitting's
ruling converts directly. NOTHING below is landed — the ADR and
indexes still read Proposed as this brief records; the drafted block
sits inside the ADR's Status section clearly marked as a draft pending
this ruling.

**Variant 1 — RATIFY-AS-IMPLEMENTED (the recommendation; drafted in
full):**

- `docs/reference/adr/ADR-0019-journal-commit-default.md`: frontmatter
  `status: Proposed` → `Accepted`; the §Status section gains the
  ratification paragraph (the drafted block is already in place,
  marked as pending this ruling) — three ledgers resolved per §5, the
  resource profile recorded as a tuning follow-up and NOT a blocker,
  open questions 2/3 resolved with the ratification (64 MiB stands;
  ~0.3% accepted as the known-cost ledger), issue #1 resolved by the
  decision.
- `docs/00-platform/005-ADR_INDEX.md`: the ADR-0019 row `Proposed` →
  `Accepted` with the 2026-09-27 date and the decision note; a new
  index revision row (1.22.0) records the sanctioned flip.
- `docs/00-platform/004-SPECIFICATION_INDEX.md`: the ADR-0019 row
  Proposed → Accepted; a new revision row (1.51.0) records the ruling
  + the as-implemented ratification.
- `docs/00-platform/REPOSITORY_STATE.md`: the item-24 status line and
  the 2026-09-27 sitting paragraph.
- `source/nyhal-linux-backend/DAEMON_LIFECYCLE.md`: §4 item 3's
  "reviewed by Architecture Group ... still open" caveat and §5's
  "Architecture Group tuning review" open item are struck, with the
  decision note (defaults ratified as tuning knobs; the watcher
  resource profile remains a documented follow-up, tracked in
  AG_BRIEF_ADR0019 §5).
- `docs/00-platform/AG_AGENDA.md`: decision-log row D1 (2026-09-27,
  owner direction via the recorded session); the status frontmatter
  and Bundle D block marked DECIDED, registered text retained.
- Downstream: the roadmap/spec-index/cycle gates re-run; the AG_BRIEF
  frontmatter flips to Superseded (the decision it staged has
  landed); issue #1's close stays the manual owner step.

**Variant 2 — DEMOTE-TO-OPT-IN:** same surfaces, opposite direction:
`fuse/nyfs.py` default flip reverted (`auto_compact: bool = False`) +
the dedicated default pin inverted + DAEMON_LIFECYCLE §4/§5 updated to
the demoted posture + the indexes record the scoped revision; ADR-0019
stays Proposed until the watcher resource profile is documented and
the review re-sits. Sized: one-line default + one pin + the records.

**Variant 3 — SCOPED REVISION (amend before deciding):** the Group
names an amendment (e.g., the adaptive-cadence mechanism from ledger
1); AG_BRIEF_ADR0019 v1.2.0 would carry the drafted amendment text
for the next sitting. No file edits are pre-staged for this variant —
it re-opens drafting by definition.

The pre-stage exists so the ruling lands the SAME session it is given,
with the gates green and the records reconciled — the anti-pattern
being the 09-06 unsanctioned flip this sitting exists to supersede.
