#!/bin/bash
# Liveness-guarded cleanup of smoke tmp dirs (issue #3).
#
# The 2026-09-25 false FAIL: a concurrent cleanup deleted a LIVE smoke's
# tmpdir (tmp/nyrqis-boot-smoke-*); the driver then polled a nonexistent
# serial path and failed a byte-identical ISO. This tool never repeats
# that: a tmp dir is only removed when the smokes that own the namespace
# prove DEAD by PID file. Never by age, never by pgrep — pattern-matching
# pgrep self-matches the checking shell's own cmdline and can fool or
# kill the very launcher it inspects.
#
# Liveness sources (all PID-file based):
#   1. The drivers' own markers: nyrqis-boot-smoke.pid and
#      nyrqis-boot-smoke-menu.pid at the temp root (held while a smoke
#      runs, per the d2b2a81 guard).
#   2. $CLEAN_SMOKE_PIDS (default ~/nyrqis-work/smoke.pids) — the local
#      wrapper's own PID file.
# A marker naming a DEAD pid is stale debris: this tool removes the
# marker itself along with the dirs (after --yes).
#
# Liveness test: [[ -d /proc/$pid ]] — existence on Linux, immune to the
# EPERM blind spot of kill -0 on a process you do not own (same
# semantics as the drivers' os.kill(pid, 0) with EPERM counted alive).
# A reused pid can only cause a false REFUSAL — the safe direction.
#
# Modes: default is DRY-RUN (lists what would be removed, still refuses
# while anything is live); --yes performs the removal; --dry-run is
# explicit; anything else is a usage error (exit 2). Refusal exits 3 and
# removes NOTHING. Environment overrides for testing: CLEAN_TMP_ROOT
# (default ${TMPDIR:-/tmp}), CLEAN_SMOKE_PIDS.
set -u

ROOT="${CLEAN_TMP_ROOT:-${TMPDIR:-/tmp}}"
PIDS_FILE="${CLEAN_SMOKE_PIDS:-$HOME/nyrqis-work/smoke.pids}"
DO_IT=0

for arg in "$@"; do
  case "$arg" in
    --yes) DO_IT=1 ;;
    --dry-run) DO_IT=0 ;;
    *)
      echo "usage: $0 [--yes | --dry-run]" >&2
      echo "  default: dry-run listing; --yes: actually remove" >&2
      exit 2
      ;;
  esac
done

alive() { [[ -n "${1:-}" && -d "/proc/$1" ]]; }

any_live=0
for m in "$ROOT/nyrqis-boot-smoke.pid" "$ROOT/nyrqis-boot-smoke-menu.pid"; do
  [[ -f "$m" ]] || continue
  p="$(cat "$m" 2>/dev/null || true)"
  if alive "$p"; then
    echo "ABORT: marker $m held by LIVE pid $p ($(ps -p "$p" -o args= 2>/dev/null | cut -c1-80)) — refusing to clean (issue #3)"
    any_live=1
  else
    echo "stale marker $m (pid ${p:-?} dead) — will remove the marker itself"
  fi
done

if [[ -f "$PIDS_FILE" ]]; then
  p="$(cat "$PIDS_FILE" 2>/dev/null || true)"
  if alive "$p"; then
    echo "ABORT: wrapper PID file $PIDS_FILE names LIVE pid $p — refusing to clean (issue #3)"
    any_live=1
  else
    echo "stale wrapper PID file $PIDS_FILE (pid ${p:-?} dead)"
  fi
fi

if (( any_live )); then
  echo "CLEANUP-VERDICT=REFUSED (live smoke detected — nothing was removed)"
  exit 3
fi

mapfile -t dirs < <(find "$ROOT" -maxdepth 1 -type d \
  -name 'nyrqis-boot-smoke-*' 2>/dev/null | sort)

if (( ${#dirs[@]} == 0 )); then
  echo "CLEANUP-VERDICT=CLEAN (no smoke tmp dirs under $ROOT)"
else
  for d in "${dirs[@]}"; do
    if (( DO_IT )); then
      rm -rf -- "$d"
      echo "REMOVED $d"
    else
      echo "WOULD-REMOVE $d (dry-run; pass --yes to remove)"
    fi
  done
  if (( DO_IT )); then
    rm -f -- "$ROOT/nyrqis-boot-smoke.pid" "$ROOT/nyrqis-boot-smoke-menu.pid" "$PIDS_FILE" 2>/dev/null
    echo "CLEANUP-VERDICT=DONE"
  else
    echo "CLEANUP-VERDICT=DRY-RUN (nothing was removed)"
  fi
fi
exit 0
