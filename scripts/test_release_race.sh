#!/usr/bin/env bash
# test_release_race.sh — prove the ISO workflows' release-upload logic
# survives the concurrent-create race WITHOUT touching GitHub.
#
# On a v* tag push, live-iso.yml's `menu-boot` and live-iso-arm64.yml's
# `menu-boot-arm64` both run scripts/attach_release_asset.sh at the same
# time. Real gh fails a create with "already exists" when the other job
# won the race; the script tolerates that via a re-view before giving
# up. This harness runs the REAL shared script CONCURRENTLY against
# scripts/fake_gh.sh + scripts/fake_curl.sh (real semantics) in both
# race orders, and asserts: both jobs exit 0, both ISOs upload under
# their real asset names, and re-runs replace prior assets.
#
# Usage: scripts/test_release_race.sh
# Exit 0 = race-safe; 1 = a round failed (see logs).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export GITHUB_REF_NAME="${GH_TEST_REF:-v9.9.9-test}"
export GITHUB_REPOSITORY="${GH_TEST_REPO:-acme/test-repo}"
export GH_TOKEN=x
BIN="$(mktemp -d /tmp/fake-gh-bin-XXXXXX)"
ln -s "$HERE/fake_gh.sh" "$BIN/gh"
ln -s "$HERE/fake_curl.sh" "$BIN/curl"
export PATH="$BIN:$PATH"
WORK="$(mktemp -d /tmp/gh-race-work-XXXXXX)"
trap 'rm -rf "$WORK" "$BIN" /tmp/fake-gh-state-* 2>/dev/null' EXIT

run_job() { # iso asset delay  (runs the REAL shared script; cwd $WORK)
  local iso="$1" asset="$2" delay="$3"
  cd "$WORK" || exit 9
  echo "content-of-$iso" > "$iso"
  sleep "$delay"   # pace so the race window is deterministic
  ISO_PATH="$WORK/$iso" ISO_NAME="$asset" \
    "$HERE/attach_release_asset.sh"
}

FAIL=0
run_round() { # amd64_delay arm64_delay label
  # ONE shared release state: on a real tag push both jobs race over
  # the SAME release — separate states (the old harness's shape) can
  # never reproduce that. FAKE_GH_CREATE_RACE_LOSERS=1 makes every
  # create sleep 1s, so the later-starting job's create deterministically
  # loses and must take the re-view fallback.
  local ad="$1" bd="$2" label="$3" r1 r2 s
  s="$(mktemp -d /tmp/fake-gh-state-XXXXXX)"
  FAKE_GH_STATE="$s" FAKE_GH_CREATE_RACE_LOSERS=1 \
    run_job nyrqis-live.iso nyrqis-live.iso "$ad" \
    >"$WORK/log-amd64.txt" 2>&1 & local p1=$!
  FAKE_GH_STATE="$s" FAKE_GH_CREATE_RACE_LOSERS=1 \
    run_job nyrqis-live-arm64.iso nyrqis-live-arm64.iso "$bd" \
    >"$WORK/log-arm64.txt" 2>&1 & local p2=$!
  wait "$p1"; r1=$?
  wait "$p2"; r2=$?
  # Assets must land under their REAL names (the fake curl keys by
  # ?name= — the same way the real uploads endpoint does).
  local assets; assets="$(find "$s/assets" \
    \( -name 'nyrqis-live.iso' -o -name 'nyrqis-live-arm64.iso' \) \
    2>/dev/null | wc -l)"
  echo "round[$label]: amd64_rc=$r1 arm64_rc=$r2 assets_uploaded=$assets/2"
  [ "$r1" = "0" ] && [ "$r2" = "0" ] || { FAIL=1; echo "  !! a job failed"; }
  [ "$assets" = "2" ] || { FAIL=1; echo "  !! assets wrong"; }
}

# Re-run round: the release ALREADY exists (a previous workflow run
# uploaded it). Both jobs run the REAL shared script (view → skip
# create → delete-before-upload → curl upload → draft self-heal) over
# the seeded release; the "clobber" variant runs both CONCURRENTLY.
run_rerun_round() { # [clobber]
  local mode="${1:-sequential}"
  local s; s="$(mktemp -d /tmp/fake-gh-state-XXXXXX)"
  mkdir -p "$s/assets"
  touch "$s/release-exists"
  echo previous-amd64 > "$s/assets/nyrqis-live.iso"
  echo previous-arm64 > "$s/assets/nyrqis-live-arm64.iso"
  local r1=0 r2=0 a_ok=1
  if [ "$mode" = clobber ]; then
    FAKE_GH_STATE="$s" run_job nyrqis-live.iso nyrqis-live.iso 0 \
      >"$WORK/log-rerun-clobber-amd64.txt" 2>&1 & local p1=$!
    FAKE_GH_STATE="$s" run_job nyrqis-live-arm64.iso nyrqis-live-arm64.iso 0 \
      >"$WORK/log-rerun-clobber-arm64.txt" 2>&1 & local p2=$!
    wait "$p1"; r1=$?
    wait "$p2"; r2=$?
  else
    FAKE_GH_STATE="$s" run_job nyrqis-live.iso nyrqis-live.iso 0 \
      >"$WORK/log-rerun-sequential.txt" 2>&1; r1=$?
    FAKE_GH_STATE="$s" run_job nyrqis-live-arm64.iso nyrqis-live-arm64.iso 0 \
      >>"$WORK/log-rerun-sequential.txt" 2>&1; r2=$?
  fi
  # Replacement proof: the asset CONTENT must be this run's upload, not
  # the seeded previous-* text (a skipped clobber would leave the seed).
  grep -q content-of-nyrqis-live.iso "$s/assets/nyrqis-live.iso" 2>/dev/null || a_ok=0
  grep -q content-of-nyrqis-live-arm64.iso "$s/assets/nyrqis-live-arm64.iso" 2>/dev/null || a_ok=0
  echo "round[rerun-$mode]: amd64_rc=$r1 arm64_rc=$r2 assets_replaced=$a_ok"
  [ "$r1" = "0" ] && [ "$r2" = "0" ] || { FAIL=1; echo "  !! a job failed"; }
  [ "$a_ok" = "1" ] || { FAIL=1; echo "  !! assets not replaced"; }
  rm -rf "$s"
}

# Round 1: amd64 paces (loses), arm64 creates immediately (wins).
run_round 0.5 0 "amd64-loses"
# Round 2: the mirror.
run_round 0 0.5 "arm64-loses"
# Round 3: re-run — the release ALREADY exists (a previous workflow run
# uploaded it). Both jobs must skip create cleanly (view succeeds) and
# replace the existing assets (delete-before-upload + curl).
run_rerun_round
# Round 4: re-run WITH concurrent clobber — both jobs upload the same
# tag simultaneously over existing assets; neither may fail.
run_rerun_round "clobber"

echo "--- job logs ---"; tail -n +1 "$WORK"/log-*.txt
if [ "$FAIL" = "0" ]; then
  echo "RACE-HARNESS: ALL PASS (release upload is race-safe)"
else
  echo "RACE-HARNESS: FAILURES"
  exit 1
fi
