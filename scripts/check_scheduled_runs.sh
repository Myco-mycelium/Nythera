#!/usr/bin/env bash
# check_scheduled_runs.sh — report the scheduled (cron) run status of
# the release-pipeline workflows, so the first arm64 cron fire
# (Mon 2026-09-21 06:00 UTC) and every later one are caught even when
# nobody is watching. Also verifies the PAT-expiry watcher's scheduled
# fires (its whole job is to go red BEFORE the PAT dies, so a missed
# fire is itself a finding).
#
# Usage:
#   scripts/check_scheduled_runs.sh            # human summary
#   scripts/check_scheduled_runs.sh --watch    # poll up to 2h for the
#                                              # LATEST scheduled run of
#                                              # each workflow to finish
#                                              # (exit 1 on any failure)
#
# Token: same sources as verify_pat_grants.sh (PAT_TOKEN, the 0600
# credential store, stdin) — anonymous works too for a public repo.
set -u

REPO="Myco-mycelium/Nythera"
WORKFLOWS="live-iso.yml live-iso-arm64.yml"
WATCHER="pat-expiry-watch.yml"
WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1

NETRC="$(mktemp)"
trap 'rm -f "$NETRC"' EXIT
chmod 600 "$NETRC"
TOKEN="${PAT_TOKEN:-}"
[ -n "$TOKEN" ] || TOKEN="$(grep -o 'github_pat_[^@]*' \
  "${GIT_CREDS_FILE:-$HOME/.git-credentials-nyrqis}" 2>/dev/null | head -1 || true)"
if [ -n "$TOKEN" ]; then
  cat > "$NETRC" <<NETRCEOF
machine github.com login x-access-token password $TOKEN
machine api.github.com login x-access-token password $TOKEN
NETRCEOF
  NRC=(--netrc-file "$NETRC")
else
  echo "(no token available — querying anonymously)"
  NRC=()
fi

FAIL=0
check_workflow() { # workflow-file
  local wf="$1" json
  json="$(curl -s "${NRC[@]}" \
    "https://api.github.com/repos/$REPO/actions/workflows/$wf/runs?event=schedule&per_page=1")"
  echo "$json" | python3 -c "
import json,sys
runs=json.load(sys.stdin)['workflow_runs']
if not runs:
    print(f'$wf schedule: NO RUN YET (cron not fired since the workflow landed)')
else:
    r=runs[0]
    print(f\"$wf schedule: {r['status']}/{r['conclusion']} at {r['created_at']} ({r['head_sha'][:7]})\")"
  echo "$json" | grep -q '"conclusion": *"failure"' && FAIL=1
  return 0
}

check_watcher() { # the expiry watcher: absence of fires is a finding
  local json n last
  json="$(curl -s "${NRC[@]}" \
    "https://api.github.com/repos/$REPO/actions/workflows/$WATCHER/runs?event=schedule&per_page=1")"
  n="$(echo "$json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["workflow_runs"]))')"
  if [ "$n" = 0 ]; then
    echo "$WATCHER schedule: NO RUN YET (daily 05:37 UTC — expected at least one fire per day once landed)"
    return 0
  fi
  last="$(echo "$json" | python3 -c "
import json,sys
r=json.load(sys.stdin)['workflow_runs'][0]
print(f\"{r['status']}/{r['conclusion']} at {r['created_at']}\")")"
  echo "$WATCHER schedule: $last"
  # A watcher that exists but has not fired in >48h is silently dead.
  local created
  created="$(echo "$json" | python3 -c "
import json,sys,datetime
r=json.load(sys.stdin)['workflow_runs'][0]
print(datetime.datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp())")"
  local now age
  now="$(date -u +%s)"
  age=$(( (now - created) / 86400 ))
  if [ "$age" -gt 2 ]; then
    echo "::error::$WATCHER last fired ${age}d ago — the daily schedule is not firing (workflow disabled? cron broken?)"
    FAIL=1
  fi
  return 0
}

if [ "$WATCH" = 0 ]; then
  for wf in $WORKFLOWS; do check_workflow "$wf"; done
  check_watcher
  [ "$FAIL" = 0 ] && echo "SCHEDULED RUNS: OK" || echo "SCHEDULED RUNS: FAILURE SEEN"
  exit "$FAIL"
fi

# --watch: wait for the LATEST scheduled run of each workflow to finish.
for wf in $WORKFLOWS; do
  echo "=== watching $wf scheduled runs (up to 120 min) ==="
  for i in $(seq 1 24); do
    sleep 300
    STATE="$(curl -s "${NRC[@]}" \
      "https://api.github.com/repos/$REPO/actions/workflows/$wf/runs?event=schedule&per_page=1" \
      | python3 -c "
import json,sys
runs=json.load(sys.stdin)['workflow_runs']
print(f\"{runs[0]['status']}/{runs[0]['conclusion']}\" if runs else 'none')")"
    echo "  [$((i * 5)) min] $STATE"
    case "$STATE" in
      completed/*) break ;;
    esac
  done
  case "$STATE" in
    completed/success) ;;
    completed/*) echo "::error::$wf scheduled run ended $STATE"; FAIL=1 ;;
    *) echo "::error::$wf scheduled run still $STATE after the watch window"; FAIL=1 ;;
  esac
done
check_watcher
[ "$FAIL" = 0 ] && echo "SCHEDULED RUNS: ALL PASS" || echo "SCHEDULED RUNS: FAILURES"
exit "$FAIL"
