# Session Digest — Sun 2026-09-27

One-line summary: **the CRY-001 thread matured from draft to
decision-ready — its false "zero egress" null finding was caught and
corrected (v0.1.1), the corrected design note was staged as AG_AGENDA
Bundle E with a full pre-read brief, and every record commit is
CI-verified green; the afternoon added the next Phase 4 design note
(UPD-001, automatic updates with rollback) whose surface audit found
the signed-update machinery shipped-but-unwired.**

## Shipped

| What | Where | Evidence |
|------|-------|----------|
| CI tail closed (afternoon) | `3b2e783` | the morning's cold-close records commit `f6157f9` verified green 35/35 via the authenticated check-runs API — the every-commit-verified property extends through the morning session's final push |
| UPD-001 v0.1.0 — the automatic-updates/rollback design note (afternoon) | `ecafd3d`, `docs/00-platform/UPDATE_ROLLBACK_SPEC.md` | per the DBG-001/CRY-001 design-note-first discipline: surface audit (§2.1) finds the signed-update machinery — `update_signing.py` verify + `validate_rollback`, `delta_update.py` signed generate/apply — shipped and tested but consumed ONLY by its own tests; no IPC op or CLI wires fetch→verify→apply→audit; `PackageManager.update_package` verifies without applying; honest gap = WIRING and POLICY, not cryptography. Three options (A compose-first operator-invoked, recommended; B A + opt-in automaticity via the NPS-019/NPS-020 pass; C close), five open questions (rollback trigger/health contract = the load-bearing one) |
| UPD-001 registered | `ecafd3d` | spec index v1.43.0 row; mkdocs nav; premise `upd001-design-note` (49/49); roadmap M14 Phase 4 note corrected; REPOSITORY_STATE paragraph in the same commit; gates: cycles 0 across 88 docs, mkdocs strict clean; CI on `ecafd3d` green 35/35 watched to completion |
| CRY-001 v0.1.0 null finding caught FALSE | `75d6492`, CRY-001 §2.1 | "ZERO outbound HTTP clients in non-test backend code" returns four hits, all predating the draft: `_send_webhook` (2026-08-28, `5585532`), registry pull/push/catalog (2026-08-30, `56de456`; pull wired to IPC + CLI), loopback-only health-check HTTP type |
| CRY-001 v0.1.1 corrigendum | `75d6492` | §1 reframed (first TELEMETRY-class egress, not first egress path), §2.1 records the four-site table; corrected finding: NO implicit/telemetry egress — every existing site operator-configured or loopback |
| Staged for the Group | `75d6492`, AG_AGENDA v2.3.0 Bundle E1 | tree-verified pre-flight, §6's four decisions (option choice; spool default; schema floor; retention/purge); DECISION-READY; roadmap stays `[ ]` |
| Pre-read brief | this session, `AG_BRIEF_CRY001.md` (AG-BRIEF-CRY001 v1.0.0) | the corrected audit restated, regulatory frame (A needs no new NPS-011 capability; B triggers the NPS-019/NPS-020 pass), three options with ledgers, recommends **Option A** with the spool default flagged as the open mechanism question |
| Downstream records reconciled | `75d6492` + this session | roadmap M14 Phase 4 note corrected; spec index 1.39.0 correction marker + 1.40.0/1.41.0 rows; premise registry `cry001-design-note` updated to v0.1.1 and `cry001-brief-registered` added |

## Verified

- CI on the CRY-001 draft commit `46dbc46`: **green, 35/35** (live-iso
  correctly path-gated off for a docs-only push).
- CI on the corrigendum/staging commit `75d6492`: **green, 35/35**,
  polled to completion (30 success, 0 failed, 0 in progress).
- Checkers at record time: **premises 48/48 OK** (47 + the new
  brief pin), **version drift OK** (pyproject 0.29.35 == newest
  CHANGELOG), **mkdocs strict exit 0**.
- Scheduled-runs checker exit 0 point-in-time (2026-09-27 ~07:36 and
  ~08:00 UTC); no PAT-grant probes spent — the rotation trigger stays
  owner-reported-only per the standing rule.

## Parked (triggers + exact next commands)

0. **AG governance — THREE decision-ready threads now**: Bundle D
   (ADR-0019, issue #1), Bundle E (CRY-001, briefed), and UPD-001
   (this afternoon's note, not yet bundled — stage it as a Bundle F
   row on AG_AGENDA when the Group's next session is called). Land the
   Group's disposition when it rules; issue #2 stays open only because
   the PAT cannot close issues.
1. **Post-rotation drill** — unchanged; fire ONLY on the owner
   REPORTING the rotation: `scripts/verify_pat_grants.sh` → expect
   `ALL GRANTS PRESENT` → dispatch `live-iso-rootless.yml`
   `with-arm64: true` → expect 204 → watch the run → post the issue #3
   comment → expect 201.
2. **Dailies band check — CAPTURED ~10:58 UTC: PASS, seventh
   consecutive in-band day.** Both Sunday fires landed in-band
   (pat-expiry-watch 10:41:23Z, scheduled-runs-watch 10:51:17Z, both
   completed success at tip `c258f93`) and the checker exited 0 at
   ~10:53 UTC. Full verdict recorded in the NEXT_SESSION_PLAN STANDING
   ITEM; the next verification point is Monday 2026-09-28's fires.
3. **AG governance** — two bundles now decision-ready on
   `AG_AGENDA.md` v2.3.1: Bundle D (ADR-0019 auto_compact, issue #1)
   and Bundle E (CRY-001 crash reporting/telemetry, briefed by
   AG-BRIEF-CRY001 v1.0.0). Land the Group's disposition when it
   rules. Issue #2 stays open only because the PAT cannot close
   issues — close manually at the next owner session.

## Reading order for a cold open

`REPOSITORY_STATE.md` (newest paragraphs) → `NEXT_SESSION_PLAN.md`
v6.40.0 opening checklist → this digest → `AG_BRIEF_CRY001.md`.
