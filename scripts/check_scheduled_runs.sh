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
# Missed-fire detection (added 2026-09-21, after the arm64 cron's first
# fire at Mon 06:00 UTC produced no run and this script printed only
# "NO RUN YET" and exited 0 — silence exactly where a finding was):
# each workflow's declared cron schedule is parsed and a fire overdue
# past the grace window FAILS the check. The window is anchored on the
# EXPECTED fire time computed from the cron expression, not on the last
# run's created_at — a weekly cadence would otherwise let every late
# run push its own alarm out by a week.
#
# Token: same sources as verify_pat_grants.sh (PAT_TOKEN, the 0600
# credential store, stdin) — anonymous works too for a public repo.
set -u

REPO="Myco-mycelium/Nythera"
WORKFLOWS="live-iso.yml live-iso-arm64.yml"
WATCHER="pat-expiry-watch.yml"
WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1

# Weekly-cron grace. GitHub schedule delays are documented (30–90 min
# under load) but measured reality on this repo runs worse: the amd64
# cron due Mon 2026-09-21 03:00 UTC actually fired 08:45 UTC — 5 h 45 m
# late. 8 h covers that envelope with headroom while still alerting the
# same day; the daily scheduled-runs-watch CI job (05:52 UTC) turns any
# longer miss into a red run within a day regardless of this value.
# Daily watchers keep the historical 48 h silence threshold.
WEEKLY_GRACE_HOURS=8
DAILY_MAX_AGE_DAYS=2

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

# cron_epoch <cron-expr> <back|fwd> -> epoch of the most recent fire at
# or before now (back), or of the first fire after now (fwd); 0 if none
# within 8 days of walking. 5-field cron: minute hour day-of-month
# month day-of-week (0=Sun), supporting * , - /. Pure-minute walking is
# deliberately used over a cron library: no dependency beyond python3,
# and the repo's crons are trivial shapes.
cron_epoch() {
  python3 - "$1" "$2" <<'PYEOF'
import sys
from datetime import datetime, timedelta, timezone

def parse_field(expr, lo, hi):
    vals = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, step = part.split("/")
            step = int(step)
        if part == "*" or part.startswith("*/"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        vals.update(v for v in range(start, end + 1) if (v - start) % step == 0)
    return vals

f = sys.argv[1].split()
minutes, hours = parse_field(f[0], 0, 59), parse_field(f[1], 0, 23)
doms, months, dows = parse_field(f[2], 1, 31), parse_field(f[3], 1, 12), parse_field(f[4], 0, 6)
dom_restricted, dow_restricted = f[2] != "*", f[4] != "*"
dir_back = sys.argv[2] != "fwd"

now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
step = -1 if dir_back else 1
t = now
for _ in range(8 * 24 * 60):  # 8 days of walking covers any weekly cron
    if t.minute in minutes and t.hour in hours and t.month in months:
        dom_ok = t.day in doms
        dow_ok = ((t.weekday() + 1) % 7) in dows  # python: 0=Mon; cron: 0=Sun
        if dom_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok  # standard cron OR semantics
        else:
            day_ok = dom_ok and dow_ok
        if day_ok and (t < now if dir_back else t > now):
            print(int(t.timestamp()))
            break
    t += timedelta(minutes=step)
else:
    print(0)
PYEOF
}

# crons_of <workflow-file> -> the cron expressions declared in on:.
# Bounded to the on: block (awk gate) so prose comments mentioning
# "cron:" cannot leak into the schedule.
crons_of() {
  awk '/^on:/{on=1; next} /^(jobs|permissions|env|defaults|concurrency):/{on=0} on && /cron:/' \
    ".github/workflows/$1" \
    | sed 's/.*cron:[[:space:]]*//; s/["'"'"']//g; s/[[:space:]]*#.*//'
}

check_workflow() { # workflow-file
  local wf="$1" json last status
  json="$(curl -s --max-time 30 "${NRC[@]}" \
    "https://api.github.com/repos/$REPO/actions/workflows/$wf/runs?event=schedule&per_page=1")"
  if ! echo "$json" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    echo "::error::$wf: runs API query failed (invalid JSON) — network? token?"
    FAIL=1
    return 0
  fi

  # Latest expected fire across all declared crons, plus the earliest
  # NEXT fire (for imminent-fire suppression below). Read line-by-line
  # via process substitution: a cron expression contains '*' fields and
  # MUST NEVER be word-split (glob expansion once turned every repo
  # root filename into a "cron expression" here).
  local expected=0 next_due=0 c e
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    e="$(cron_epoch "$c" back)"
    if [ -n "$e" ] && [ "$e" -gt "$expected" ]; then expected="$e"; fi
    e="$(cron_epoch "$c" fwd)"
    if [ -n "$e" ] && [ "$e" -gt 0 ] && { [ "$next_due" -eq 0 ] || [ "$e" -lt "$next_due" ]; }; then next_due="$e"; fi
  done < <(crons_of "$wf")
  if [ "$expected" -eq 0 ]; then
    echo "::error::$wf declares no parseable cron in its on: block — schedule lost?"
    FAIL=1
    return 0
  fi

  last="$(echo "$json" | python3 -c "
import json,sys
runs=json.load(sys.stdin)['workflow_runs']
print(runs[0]['created_at'] if runs else '')")"

  if [ -z "$last" ]; then
    echo "$wf schedule: NO RUN YET (no scheduled run since the workflow landed)"
    if [ "$expected" -gt 0 ]; then
      local now_s overdue
      now_s="$(date -u +%s)"
      # Imminent-fire suppression: a legitimate fire within the grace
      # window AHEAD of now (e.g. the daily CI job running at 05:52 UTC
      # on a Monday, minutes before a 06:00 fire) is not a miss — and
      # the backward-looking expected fire is then last week's, which
      # for a workflow that did not exist yet would false-alarm.
      if [ "$next_due" -gt "$now_s" ] && [ $((next_due - now_s)) -le $((WEEKLY_GRACE_HOURS * 3600)) ]; then
        echo "  (next cron fire $(date -u -d "@$next_due" '+%H:%M UTC') is imminent — not counted as a miss)"
        return 0
      fi
      overdue=$(( (now_s - expected) / 3600 ))
      if [ "$overdue" -ge "$WEEKLY_GRACE_HOURS" ]; then
        echo "::error::$wf cron was due $(date -u -d "@$expected" '+%Y-%m-%d %H:%M UTC') — no scheduled run exists ${overdue}h later (grace ${WEEKLY_GRACE_HOURS}h): the schedule is not firing"
        FAIL=1
      else
        echo "  (cron due $(date -u -d "@$expected" '+%H:%M UTC') — within the ${WEEKLY_GRACE_HOURS}h schedule-delay window)"
      fi
    fi
    return 0
  fi

  status="$(echo "$json" | python3 -c "
import json,sys
r=json.load(sys.stdin)['workflow_runs'][0]
print(f\"{r['status']}/{r['conclusion']} at {r['created_at']} ({r['head_sha'][:7]})\")")"
  echo "$wf schedule: $status"
  echo "$json" | grep -q '"conclusion": *"failure"' && FAIL=1
  return 0
}

check_watcher() { # the expiry watcher: absence of fires is a finding
  local json n last
  json="$(curl -s --max-time 30 "${NRC[@]}" \
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
# int(): timestamp() prints a float and bash arithmetic rejects '1789810887.0'
# (the >48h check crashed on every invocation until this cast existed).
print(int(datetime.datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp()))")"
  local now age
  now="$(date -u +%s)"
  age=$(( (now - created) / 86400 ))
  if [ "$age" -gt "$DAILY_MAX_AGE_DAYS" ]; then
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
    STATE="$(curl -s --max-time 30 "${NRC[@]}" \
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
