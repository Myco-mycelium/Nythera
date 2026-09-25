# Session Digest — Fri 2026-09-25

One-line summary: **the live-ISO stream shipped end to end — rootless CI
green, issue #3 implemented and released, two releases cut (v0.29.31
verified, v0.29.32 shipped), every thread either landed or parked on a
written-down trigger.**

## Shipped

| What | Where | Evidence |
|------|-------|----------|
| Rootless live-ISO CI green on a stock runner | `live-iso-rootless.yml` (amd64 push-gated) | acquire → build → ownership proof → BOTH boot smokes, zero sudo |
| Boot-smoke concurrency guard (issue #3, drivers) | `d2b2a81`, both `tests/boot_smoke*.py` | exit-2 BUSY refusal on a live PID marker; stale takeover; `os.kill(pid,0)`+EPERM — pgrep banned; self-healing rmtree |
| Liveness-guarded cleanup tooling (issue #3) | `3f85979`, `scripts/clean-smoke-tmp.sh` | exit-3 refusal on live markers (`/proc` checks); dry-run default; `--yes` gate; proven in two real sweeps |
| v0.29.31 | tag + release | both assets verified end-user-style + boot-smoked |
| v0.29.32 | tag + release, both ISOs attached | ALL FOUR boot paths PASS on the DOWNLOADED assets (amd64/arm64 × direct/menu) |

## Verified

- Backend sweep **9237 OK (skipped=4)** — includes the 19 new contract pins
  (`TestSmokeConcurrencyGuard` 12, `TestCleanupToolingContract` 7).
- CI green on **every pushed commit** (checked individually), incl. CI
  four-for-four on the guard commit and both tag-triggered ISO workflows.
- Dailies **in-band five consecutive days** (10:02–11:49 UTC band,
  checker exit 0).
- The guard caught its own launcher racing itself — serialization worked
  exactly as designed during the local PASS ×4 wave.

## Parked (triggers + exact next commands)

1. **Post-rotation drill** — owner rotates the fine-grained PAT
   (Contents/Workflows/Actions/Variables/PR RW + issue comments), then:
   `scripts/verify_pat_grants.sh` → expect `ALL GRANTS PRESENT` →
   dispatch `live-iso-rootless.yml` `with-arm64: true` → expect 204 →
   watch the arm64 rootless run → post the issue #3 comment → expect 201.
   State: `GRANTS MISSING` ×3 re-checks (identity OK; variables 403;
   actions 403). **No further re-checks until the owner reports rotation.**
2. **Monday dailies check** — sixth consecutive in-band day; verdict goes
   into NEXT_SESSION_PLAN's STANDING ITEM.

## Reading order for a cold open

`REPOSITORY_STATE.md` (newest paragraphs) → `NEXT_SESSION_PLAN.md`
v6.33.0 opening checklist → this digest.
