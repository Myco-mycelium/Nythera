#!/usr/bin/env bash
# attach_release_asset.sh — ship ONE ISO to the GitHub release of a tag.
#
# Single source of truth for the release-attach logic. Consumed by:
#   * live-iso.yml / live-iso-arm64.yml "Attach the ISO to the release"
#     steps (v* tag builds),
#   * the same workflows' "re-attach" workflow_dispatch jobs (manual
#     recovery when uploads.github.com has a bad day — no rebuild),
#   * scripts/test_release_race.sh, which runs THIS script against
#     scripts/fake_gh.sh + a fake curl so the race harness exercises the
#     real logic instead of a transcription that can drift.
#
# Behavior (hardened during v0.29.25 — see REPOSITORY_STATE.md):
#   1. RACE-SAFE create: view || create, tolerating "already exists"
#      from the concurrent arch job, re-viewing before giving up.
#   2. curl upload (NOT `gh release upload`, which stalled twice on a
#      ~250 MB asset): bounded --max-time per attempt, delete-before-
#      upload for idempotent re-runs, RETRY_ATTEMPTS with backoff.
#   3. DRAFT SELF-HEAL: deleting a tag converts its release to a draft
#      (invisible anonymously, visible to gh) — re-publish after the
#      upload gate passes, so the asset is never hidden.
#
# Required env:  GH_TOKEN, ISO_PATH
# Optional env:  ISO_NAME (default: basename of ISO_PATH), RELEASE_TAG
#                (default: GITHUB_REF_NAME), REPO (default:
#                GITHUB_REPOSITORY), RELEASE_NOTES, UPLOAD_MAX_SECS
#                (default 540), RETRY_ATTEMPTS (default 3)
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
ISO_PATH="${ISO_PATH:?ISO_PATH is required}"
[ -f "$ISO_PATH" ] || { echo "::error::ISO_PATH does not exist: $ISO_PATH"; exit 1; }
ISO_NAME="${ISO_NAME:-$(basename "$ISO_PATH")}"
RELEASE_TAG="${RELEASE_TAG:-${GITHUB_REF_NAME:?RELEASE_TAG or GITHUB_REF_NAME required}}"
REPO="${REPO:-${GITHUB_REPOSITORY:?REPO or GITHUB_REPOSITORY required}}"
RELEASE_NOTES="${RELEASE_NOTES:-Boot-smoked live ISOs for ${RELEASE_TAG} (amd64 + arm64). Both architectures passed the direct and menu-path QEMU boot smokes.}"
UPLOAD_MAX_SECS="${UPLOAD_MAX_SECS:-540}"
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-3}"

# The release page may not exist yet for a fresh tag — create it before
# uploading. RACE-SAFE: on a tag push, both architectures' jobs run
# concurrently and BOTH may see no release; the loser's create fails
# with "already_exists" — tolerate that (the winner created it) and
# re-check before giving up, so both ISOs always upload. --notes instead
# of --generate-notes: server-side notes generation is another stall
# point, and the notes are deterministic anyway.
if ! gh release view "$RELEASE_TAG" --repo "$REPO" >/dev/null 2>&1; then
  gh release create "$RELEASE_TAG" \
       --repo "$REPO" \
       --title "Nyrqis $RELEASE_TAG" \
       --notes "$RELEASE_NOTES" \
    || { sleep 10; \
         gh release view "$RELEASE_TAG" --repo "$REPO" >/dev/null 2>&1 \
           || { echo "::error::release $RELEASE_TAG could not be created or found"; exit 1; }; }
fi

# RETRY: `gh release upload` STALLED twice on the ~250 MB asset
# (v0.29.25 rounds 3-4; once even solo at 8.8 min it worked, twice it
# hung past 20 min). Upload via curl instead: bounded per attempt
# (--max-time), idempotent re-runs via delete-before-upload (the
# --clobber equivalent).
upload_url="$(gh release view "$RELEASE_TAG" --repo "$REPO" \
  --json uploadUrl -q .uploadUrl)"
upload_url="${upload_url%%\{*}"
asset_id="$(gh api "repos/$REPO/releases/tags/$RELEASE_TAG" \
  --jq ".assets[] | select(.name==\"$ISO_NAME\") | .id" 2>/dev/null || true)"
if [ -n "$asset_id" ]; then
  echo "deleting stale asset id=$asset_id (clobber)"
  gh api -X DELETE "repos/$REPO/releases/assets/$asset_id" >/dev/null 2>&1 || true
fi
up_ok=0
attempt=1
while [ "$attempt" -le "$RETRY_ATTEMPTS" ]; do
  if curl --fail --silent --show-error --max-time "$UPLOAD_MAX_SECS" \
       --header "Authorization: Bearer $GH_TOKEN" \
       --header "Content-Type: application/x-iso9660-image" \
       --upload-file "$ISO_PATH" \
       "${upload_url}?name=$ISO_NAME"; then
    up_ok=1; break
  fi
  echo "upload attempt $attempt failed or timed out (${UPLOAD_MAX_SECS}s cap); retrying in 30s"
  attempt=$((attempt + 1))
  if [ "$attempt" -le "$RETRY_ATTEMPTS" ]; then sleep 30; fi
done
[ "$up_ok" = 1 ] || { echo "::error::asset upload failed after $RETRY_ATTEMPTS bounded attempts"; exit 1; }

# DRAFT SELF-HEAL: deleting a tag (e.g. force-moving it) makes GitHub
# convert that tag's release to a DRAFT. Drafts are invisible to
# anonymous API/clients while authenticated `gh release view` still sees
# them — so the upload above can "succeed" into a release nobody can
# ever download. Re-publish unconditionally; this is a no-op for a live
# release. Strictly AFTER the upload gate: never publish a release
# whose asset upload failed.
gh release edit "$RELEASE_TAG" --repo "$REPO" --draft=false
gh release view "$RELEASE_TAG" --repo "$REPO" \
  --json assets -q '.assets[].name'
