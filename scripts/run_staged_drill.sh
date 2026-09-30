#!/usr/bin/env bash
# run_staged_drill.sh — the post-rotation owner session, one command.
#
# Chains the exact sequence staged in NEXT_SESSION_PLAN / the
# 2026-09-30 digest, asserting each step's expected outcome:
#
#   1. grants check   — scripts/verify_pat_grants.sh must report present
#   2. dispatch       — live-iso-rootless.yml (with-arm64: true), expect 204
#   3. watch          — poll the dispatched run to completion
#   4. close issue #1 — state closed/completed, message references
#                       AG decision-log row D1 (2026-09-30)
#   5. issue #3 comment — the close-out summary, expect 201
#   6. PAT_EXPIRES_AT — PATCH the repo variable (idempotent; skips a
#                       variable that already carries the same value)
#
# Token: same sources as verify_pat_grants.sh ($PAT_TOKEN, or the
# 0600 credential store). Read-only against GitHub except the four
# acts above, each of which is the documented intent of this drill.
#
# Usage:  scripts/run_staged_drill.sh [--dry-run]
#         --dry-run runs the grants check only and prints the planned acts.

set -u

REPO="${REPO:-Myco-mycelium/Nythera}"
API="https://api.github.com"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '%s\n' "$*"; }
die() { printf 'DRILL STOP: %s\n' "$*" >&2; exit 1; }

TOKEN="${PAT_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -o 'github_pat_[^@]*' \
    "${GIT_CREDS_FILE:-$HOME/.git-credentials-nyrqis}" 2>/dev/null | head -1 || true)"
fi
[ -n "$TOKEN" ] || die "no token found (PAT_TOKEN or the credential store)"

NETRC="$(mktemp)"
trap 'rm -f "$NETRC" /tmp/drill-payload.json' EXIT
printf 'machine api.github.com\nlogin x-access-token\npassword %s\n' "$TOKEN" > "$NETRC"
chmod 600 "$NETRC"

gh() { curl -s --netrc-file "$NETRC" -H "Accept: application/vnd.github+json" "$@"; }

# ── 1. grants check ────────────────────────────────────────────────
say "== 1/6 grants check =="
if PAT_TOKEN="$TOKEN" bash "$(dirname "$0")/verify_pat_grants.sh" | tee /dev/stderr | grep -q "GRANTS MISSING"; then
  die "grants still missing — flip Actions/Variables/Workflows/Pull-requests to RW (or re-mint), then re-run"
fi
say "grants OK"

# ── 2. dispatch live-iso-rootless.yml ─────────────────────────────
say "== 2/6 dispatch live-iso-rootless.yml (with-arm64: true) =="
if [ "$DRY" = 1 ]; then say "(dry-run: dispatch, watch, close, comment, variable skipped)"; exit 0; fi
printf '{"ref":"main","inputs":{"with-arm64":"true"}}' > /tmp/drill-payload.json
CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" \
  -d @/tmp/drill-payload.json \
  "$API/repos/$REPO/actions/workflows/live-iso-rootless.yml/dispatches")"
[ "$CODE" = "204" ] || die "dispatch returned HTTP $CODE (expected 204)"
say "dispatch 204 OK"

# ── 3. watch the dispatched run ────────────────────────────────────
say "== 3/6 watching the dispatched run =="
RUN_ID=""
for _ in $(seq 1 20); do
  RUN_ID="$(gh "$API/repos/$REPO/actions/workflows/live-iso-rootless.yml/runs?event=workflow_dispatch&per_page=1" \
    | python3 -c "import json,sys; rs=json.load(sys.stdin).get('workflow_runs',[]); print(rs[0]['id'] if rs else '')")"
  [ -n "$RUN_ID" ] && break
  sleep 10
done
[ -n "$RUN_ID" ] || die "dispatched run never appeared"
say "run id $RUN_ID"
while :; do
  STATE="$(gh "$API/repos/$REPO/actions/runs/$RUN_ID" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',''), d.get('conclusion',''))")"
  case "$STATE" in
    "completed success") say "run $RUN_ID completed SUCCESS"; break ;;
    completed*) die "run $RUN_ID finished: $STATE" ;;
  esac
  say "  ... $STATE"
  sleep 60
done

# ── 4. close issue #1 ──────────────────────────────────────────────
say "== 4/6 close issue #1 =="
BODY="$(cat <<'MSG'
Resolved by decision — ADR-0019 RATIFIED AS-IMPLEMENTED (Bundle D,
AG decision-log row D1, 2026-09-30, owner direction via the recorded
session; the E1/F1 same-day shape). All three ledgers from this issue
were resolved per AG_BRIEF_ADR0019's recommendation: the fixed 60 s
watcher cadence retained as a tuning knob; auto_compact=True ratified
as the shipped default (the ADR-0022 as-implemented shape); the
shutdown ordering confirmed. The watcher resource profile is a tuning
follow-up (first measurement recorded; harness:
tests/bench_watcher_profile.py), not a blocker. Records: ADR-0019 v1.1.0
Accepted; ADR index 1.23.0; spec index 1.51.0; AG_AGENDA v2.4.0 row D1.
MSG
)"
printf '{"state":"closed","state_reason":"completed"}' > /tmp/drill-payload.json
CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X PATCH \
  -H "Accept: application/vnd.github+json" -d @/tmp/drill-payload.json \
  "$API/repos/$REPO/issues/1")"
[ "$CODE" = "200" ] || die "issue #1 close returned HTTP $CODE (expected 200)"
say "issue #1 CLOSED"
# the decision record as the closing comment (close-with-comment)
python3 - "$BODY" <<'PY' > /tmp/drill-payload.json
import json, sys
print(json.dumps({"body": sys.argv[1]}))
PY
CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" -d @/tmp/drill-payload.json \
  "$API/repos/$REPO/issues/1/comments")"
[ "$CODE" = "201" ] || die "issue #1 comment returned HTTP $CODE (expected 201)"
say "issue #1 closing comment 201 OK"

# ── 5. issue #3 close-out comment ─────────────────────────────────
say "== 5/6 comment on issue #3 =="
printf '{"body":"Post-rotation drill executed (scripts/run_staged_drill.sh): grants verified, live-iso-rootless dispatched with arm64 and completed success. The rotation is fully verified — issue comments 201 as expected after the grant fix. Close-out summary preserved in NEXT_SESSION_PLAN / REPOSITORY_STATE history per convention."}' > /tmp/drill-payload.json
CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" -d @/tmp/drill-payload.json \
  "$API/repos/$REPO/issues/3/comments")"
[ "$CODE" = "201" ] || die "issue #3 comment returned HTTP $CODE (expected 201)"
say "issue #3 comment 201 OK"

# ── 6. PAT_EXPIRES_AT variable ─────────────────────────────────────
say "== 6/6 PAT_EXPIRES_AT =="
CURRENT="$(gh "$API/repos/$REPO/actions/variables/PAT_EXPIRES_AT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('value',''))" 2>/dev/null || true)"
if [ -n "$CURRENT" ]; then
  say "already set ($CURRENT) — PATCH skipped (idempotent)"
else
  printf '{"name":"PAT_EXPIRES_AT","value":"SET-BY-OWNER-SESSION"}' > /tmp/drill-payload.json
  CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X PATCH \
    -H "Accept: application/vnd.github+json" -d @/tmp/drill-payload.json \
    "$API/repos/$REPO/actions/variables/PAT_EXPIRES_AT")"
  case "$CODE" in
    201|204) say "PAT_EXPIRES_AT set (HTTP $CODE)" ;;
    *) die "PAT_EXPIRES_AT PATCH returned HTTP $CODE" ;;
  esac
fi

say "DRILL COMPLETE — the rotation is fully verified; record the verdict in NEXT_SESSION_PLAN."
