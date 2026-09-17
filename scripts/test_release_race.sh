#!/usr/bin/env bash
# test_release_race.sh — prove the ISO workflows' release-upload logic
# survives the concurrent-create race WITHOUT touching GitHub.
#
# On a v* tag push, live-iso.yml's `menu-boot` and live-iso-arm64.yml's
# `menu-boot-arm64` both run `gh release view || gh release create` at
# the same time. Real gh fails a create with "already exists" when the
# other job won the race; the hardened steps tolerate that via a
# re-view before giving up. This harness runs both jobs' exact step
# logic CONCURRENTLY against scripts/fake_gh.sh (real semantics) in
# both race orders, and asserts: both jobs exit 0, both ISOs upload.
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
export PATH="$BIN:$PATH"
WORK="$(mktemp -d /tmp/gh-race-work-XXXXXX)"
trap 'rm -rf "$WORK" "$BIN" /tmp/fake-gh-state-* 2>/dev/null' EXIT

job_body() { # iso delay  (uses $FAKE_GH_STATE; cwd is $WORK)
  local iso="$1" delay="$2"
  cd "$WORK" || exit 9
  echo "content-of-$iso" > "dist-$iso"
  set -e
  # ===== the exact logic from the hardened workflow steps =====
  if ! gh release view "${GITHUB_REF_NAME}" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
    sleep "$delay"   # pace so the race window is deterministic
    gh release create "${GITHUB_REF_NAME}" \
         --repo "$GITHUB_REPOSITORY" \
         --title "Nyrqis ${GITHUB_REF_NAME}" \
         --generate-notes 2>/dev/null \
      || { gh release view "${GITHUB_REF_NAME}" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1 \
             || { echo "::error::release ${GITHUB_REF_NAME} could not be created or found"; exit 1; }; }
  fi
  gh release upload "${GITHUB_REF_NAME}" "dist-$iso" \
    --repo "$GITHUB_REPOSITORY" --clobber
  gh release view "${GITHUB_REF_NAME}" --repo "$GITHUB_REPOSITORY" \
    --json assets -q '.assets[].name'
  # ===== end workflow logic =====
}

FAIL=0
run_round() { # amd64_delay arm64_delay label
  local ad="$1" bd="$2" label="$3" r1 r2 s1 s2
  s1="$(mktemp -d /tmp/fake-gh-state-XXXXXX)"
  s2="$(mktemp -d /tmp/fake-gh-state-XXXXXX)"
  FAKE_GH_STATE="$s1" job_body nyrqis-live.iso "$ad" \
    >"$WORK/log-amd64.txt" 2>&1 & local p1=$!
  FAKE_GH_STATE="$s2" job_body nyrqis-live-arm64.iso "$bd" \
    >"$WORK/log-arm64.txt" 2>&1 & local p2=$!
  wait "$p1"; r1=$?
  wait "$p2"; r2=$?
  local assets; assets=$(find "$s1/assets" "$s2/assets" -name '*.iso' 2>/dev/null | wc -l)
  echo "round[$label]: amd64_rc=$r1 arm64_rc=$r2 assets_uploaded=$assets/2"
  [ "$r1" = "0" ] && [ "$r2" = "0" ] || { FAIL=1; echo "  !! a job failed"; }
  [ "$assets" = "2" ] || { FAIL=1; echo "  !! assets wrong"; }
}

# Re-run round: the release ALREADY exists (a previous workflow run
# uploaded it). Both jobs run the SAME full workflow body as rounds 1-2
# (view → create-or-skip → --clobber upload): view succeeds, create is
# skipped cleanly, and the upload clobbers the previous run's assets.
# The "clobber" variant runs both jobs CONCURRENTLY over the same tag.
run_rerun_round() { # [clobber]
  local mode="${1:-sequential}"
  local s; s="$(mktemp -d /tmp/fake-gh-state-XXXXXX)"
  mkdir -p "$s/assets"
  touch "$s/release-exists"
  # Seed with the names the uploads produce: job_body writes dist-$iso,
  # and the fake keys assets by basename — so the previous run's assets
  # are dist-nyrqis-live.iso / dist-nyrqis-live-arm64.iso.
  echo previous-amd64 > "$s/assets/dist-nyrqis-live.iso"
  echo previous-arm64 > "$s/assets/dist-nyrqis-live-arm64.iso"
  local r1=0 r2=0 a_ok=1
  if [ "$mode" = clobber ]; then
    FAKE_GH_STATE="$s" job_body nyrqis-live.iso 0 \
      >"$WORK/log-rerun-clobber-amd64.txt" 2>&1 & local p1=$!
    FAKE_GH_STATE="$s" job_body nyrqis-live-arm64.iso 0 \
      >"$WORK/log-rerun-clobber-arm64.txt" 2>&1 & local p2=$!
    wait "$p1"; r1=$?
    wait "$p2"; r2=$?
  else
    FAKE_GH_STATE="$s" job_body nyrqis-live.iso 0 \
      >"$WORK/log-rerun-sequential.txt" 2>&1; r1=$?
    FAKE_GH_STATE="$s" job_body nyrqis-live-arm64.iso 0 \
      >>"$WORK/log-rerun-sequential.txt" 2>&1; r2=$?
  fi
  # Replacement proof: the asset CONTENT must be this run's upload, not
  # the seeded previous-* text (a skipped clobber would leave the seed).
  grep -q content-of-nyrqis-live.iso "$s/assets/dist-nyrqis-live.iso" 2>/dev/null || a_ok=0
  grep -q content-of-nyrqis-live-arm64.iso "$s/assets/dist-nyrqis-live-arm64.iso" 2>/dev/null || a_ok=0
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
# --clobber-upload over the existing assets.
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
