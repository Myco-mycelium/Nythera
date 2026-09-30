# Session Digest — Wed 2026-09-30

One-line summary: **the last open AG bundle closed — Bundle D
(ADR-0019, issue #1) was decided by owner direction through the
recorded session (the E1/F1 channel, asked explicitly, not assumed
from "proceed") and RATIFIED AS-IMPLEMENTED the same day per the
pre-staged AG_BRIEF_ADR0019 §6 Variant-1 plan; the watcher
resource-profile follow-up was converted from a named method into an
executable, CI-verified harness; every commit pushed today is
CI-verified green. NO OPEN AG BUNDLES REMAIN.**

## Shipped

| What | Where | Evidence |
|------|-------|----------|
| Bundle D DECIDED + the pre-staged disposition LANDED | `652ec45` | ADR-0019 frontmatter v1.1.0 → **Accepted** with the ratification paragraph (three issue-#1 ledgers resolved per the brief's recommendation: 60 s cadence retained as a tuning knob; `auto_compact=True` ratified — the ADR-0022 as-implemented shape; shutdown ordering confirmed; the watcher resource profile a tuning follow-up, NOT a blocker; the 09-06 unsanctioned self-flip given the Group record it lacked); ADR index 1.23.0 (row + revision; the pre-stage's "1.22.0" was stale), spec index 1.51.0 (ADR cell + AG-BRIEF-ADR0019 → Superseded + revision row), adr/README.md and tests/BENCHMARK_PLAN.md reconciled — two surfaces beyond §6's list caught by the sweep; DAEMON_LIFECYCLE §4 item 3 CLOSED / §5 bullet struck / header note rewritten; AG_BRIEF_ADR0019 v1.2.0 → Superseded; AG_AGENDA v2.4.0 (decision-log row D1, owner direction via the recorded session, 2026-09-30; Bundle D block DECIDED; frontmatter: NO OPEN BUNDLES REMAIN); REPOSITORY_STATE newest paragraph + the issue-line item → [x]; NEXT_SESSION_PLAN v6.49.0. Issue #1 is resolved BY the decision; the GitHub close probed 403 point-in-time (the PAT cannot close issues) — the close stays a manual owner step |
| The tuning follow-up first-measured | `b89f16e` | DAEMON_LIFECYCLE §3 gained a Resource-profile section: idle no-op wakeup **24.71 µs** over a 120,000-call sample (journal below the half-threshold) ≈ **35.6 ms CPU/day** at the 60 s cadence (~0.0004% duty), ~5 KiB traced loop allocations, the active pass held to §14's bounds, the stop path confirmed (join(5 s), never blocks exit); the real-hardware remainder named precisely (SSD-vs-HDD §14 re-run, a 24 h idle-RSS soak, wake jitter under load) with its method |
| The follow-up made EXECUTABLE | `c1bc675` | `tests/bench_watcher_profile.py` — the device-labeled harness for §3's named captures (§14 pass re-run, idle-RSS soak with the watcher thread live, wake jitter), one command on target hardware, `--quick` smoke mode; proven end-to-end on the dev VM before landing: first real pass **26.9 ms/block** (inside §14's ~27 ms anchor band — an independent reproduction of the ratification's cost ledger), RSS drift **4 KiB** over the 90 s soak, wake jitter p95 **+4.2 ms** (1 s-scaled cadence); the 22 compaction/watcher/shutdown contract tests re-run green (22 passed, zero regression) |
| Dailies verified + the drift question answered | `5f5f7f6`, `615eed3`, `735e92c`, `8774191` | Monday: the whole family fired ~1 h late (arm64 12:44:52Z, 8 min inside the 12:45 no-show deadline) — IN BAND WITH LATE FIRES; Tuesday: returned to band (11:30/11:36); Wednesday: held (11:19/11:24, checker exit 0) — the drift fully reverted, the post-Monday trajectory a steady early-ward reversion |

## Verified

- CI on `c1bc675` (the harness commit): **green 37/38, 0 failed,
  1 skipped/neutral**, watched to completion ~13:40 UTC — the commit
  added a repo file, so the docs-only waiver did not apply; the
  every-commit-verified property extends through today's entire push
  series (`652ec45`, `b89f16e`, `c1bc675`, `71edd77`).
- Gates at every landing: premises **58/58** (no pins fired — the
  needles target stable identifiers), cycles **0 across 90 docs**,
  mkdocs strict clean, version drift OK (pyproject 0.29.36 ==
  CHANGELOG newest release).
- Tracker reads: **nine consecutive identical** (issues #1/#2/#3 all
  open with pre-today `updated_at`, 0 comments, 0 open PRs) — the
  Group sitting never happened independently; today's ruling came
  through the owner-direction channel, asked for explicitly per the
  09-06 lesson.
- Issue #1 close probe: HTTP **403** point-in-time, "Resource not
  accessible by personal access token" — exactly as the records
  predicted since 09-25.
- Monday's arm64 run (in progress at its capture): completed/success
  — residual closed green.

## Parked (all externally gated or dated)

0. **Owner session (fires on the owner REPORTING the PAT rotation):**
   `scripts/verify_pat_grants.sh` → expect ALL GRANTS PRESENT →
   dispatch `live-iso-rootless.yml` `with-arm64: true` → expect 204 →
   watch the run → close **issue #1** referencing AG decision-log row
   D1 (2026-09-30) → post the issue #3 summary comment → expect 201.
1. **Target-hardware profile run:** `python3
   tests/bench_watcher_profile.py` (full 24 h soak; `--quick` for a
   smoke) on target-class storage → paste the labeled block into
   DAEMON_LIFECYCLE §3's Resource profile. Not a gate.
2. **Thursday dailies band read:** `bash scripts/check_scheduled_runs.sh`
   after the nominal band window (Thu 2026-10-01) → verdict to the
   NEXT_SESSION_PLAN STANDING ITEM. The c1bc675 CI check this read
   would have carried is already done (see Verified).

## Reading order for a cold open

`REPOSITORY_STATE.md` (newest paragraphs) → `NEXT_SESSION_PLAN.md`
v6.49.0 opening checklist → this digest → `docs/reference/adr/
ADR-0019-journal-commit-default.md` §Status (the ratification
paragraph) → `AG_AGENDA.md` decision-log row D1.
