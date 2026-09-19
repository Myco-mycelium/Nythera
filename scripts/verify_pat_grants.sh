#!/usr/bin/env bash
# verify_pat_grants.sh — verify a GitHub PAT's EFFECTIVE grants for this
# repo, and optionally run the full workflow_dispatch drill.
#
# Usage:
#   scripts/verify_pat_grants.sh            # probe only (report; exit 0
#                                           # unless a probe itself errors)
#   scripts/verify_pat_grants.sh --drill    # + dispatch live-iso.yml at
#                                           # ref=v0.29.27, poll it, and
#                                           # compare the re-shipped
#                                           # asset digest to the pinned
#                                           # one (exit 1 on mismatch)
#
# Token sources (first hit wins): $PAT_TOKEN, the credential store
# (0600 ~/.git-credentials-nyrqis), or a hidden stdin prompt. The token
# NEVER enters process argv: API calls go through a 0600 temp netrc.
#
# Probes are non-destructive:
#   identity   GET /user
#   variables  PATCH PAT_EXPIRES_AT with its own value when it exists;
#              otherwise create+delete a probe value (cleanup failure
#              warns loudly — a stale probe variable would pacify
#              pat-expiry-watch.yml)
#   actions    POST a workflow_dispatch of pat-expiry-watch.yml (a
#              harmless self-check run) — the exact permission the
#              failed-jobs rerun and the release drill need
set -euo pipefail

REPO="Myco-mycelium/Nythera"
PINNED_AMD64_DIGEST="cf6618b8eaf6b46a1108070f8485e0753e9698c585ded3b20cf95e049d7306a7"
DRILL=0
[ "${1:-}" = "--drill" ] && DRILL=1

TOKEN="${PAT_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  TOKEN="$(grep -o 'github_pat_[^@]*' \
    "${GIT_CREDS_FILE:-$HOME/.git-credentials-nyrqis}" 2>/dev/null | head -1 || true)"
fi
if [ -z "$TOKEN" ]; then
  printf 'Paste the PAT to verify (hidden): '
  IFS= read -rs TOKEN; echo
fi
[ -n "$TOKEN" ] || { echo "no token found (PAT_TOKEN, store, or stdin)"; exit 1; }

NETRC="$(mktemp)"
trap 'rm -f "$NETRC" "$NETRC.run" 2>/dev/null' EXIT
chmod 600 "$NETRC"
cat > "$NETRC" <<NETRCEOF
machine github.com login x-access-token password $TOKEN
machine api.github.com login x-access-token password $TOKEN
NETRCEOF
api() { curl -s --netrc-file "$NETRC" -o "$2" -w '%{http_code}' "$@" >/dev/null \
  2>/dev/null || true; }

MISSING=0

# --- identity ---------------------------------------------------------
CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/vp-user.json -w '%{http_code}' \
  https://api.github.com/user || true)"
if [ "$CODE" = "200" ]; then
  echo "identity:        OK ($(python3 -c "import json;print(json.load(open('/tmp/vp-user.json'))['login'])"))"
else
  echo "identity:        FAILED (HTTP $CODE) — token invalid or expired"
  exit 1
fi
rm -f /tmp/vp-user.json

# --- variables write --------------------------------------------------
CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/vp-var.json -w '%{http_code}' \
  "https://api.github.com/repos/$REPO/actions/variables/PAT_EXPIRES_AT" || true)"
if [ "$CODE" = "200" ]; then
  CUR="$(python3 -c "import json;print(json.load(open('/tmp/vp-var.json'))['value'])")"
  CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X PATCH \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"PAT_EXPIRES_AT\",\"value\":\"$CUR\"}" \
    "https://api.github.com/repos/$REPO/actions/variables/PAT_EXPIRES_AT" || true)"
  [ "$CODE" = "204" ] && echo "variables write: OK (idempotent PATCH of PAT_EXPIRES_AT)" \
    || { echo "variables write: MISSING (PATCH HTTP $CODE)"; MISSING=1; }
elif [ "$CODE" = "404" ]; then
  CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X POST \
    -H "Content-Type: application/json" \
    -d '{"name":"PAT_EXPIRES_AT","value":"2099-01-01"}' \
    "https://api.github.com/repos/$REPO/actions/variables" || true)"
  if [ "$CODE" = "201" ]; then
    DCODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X DELETE \
      "https://api.github.com/repos/$REPO/actions/variables/PAT_EXPIRES_AT" || true)"
    if [ "$DCODE" = "204" ]; then
      echo "variables write: OK (probe variable created + removed)"
    else
      echo "variables write: OK, but probe cleanup FAILED (HTTP $DCODE) —"
      echo "  DELETE PAT_EXPIRES_AT manually or pat-expiry-watch.yml reads a bogus date"
      MISSING=1
    fi
  else
    echo "variables write: MISSING (create HTTP $CODE)"
    MISSING=1
  fi
else
  echo "variables write: MISSING (read HTTP $CODE)"
  MISSING=1
fi
rm -f /tmp/vp-var.json

# --- actions write ----------------------------------------------------
CODE="$(curl -s --netrc-file "$NETRC" -o /dev/null -w '%{http_code}' -X POST \
  -H "Accept: application/vnd.github+json" -d '{}' \
  "https://api.github.com/repos/$REPO/actions/workflows/pat-expiry-watch.yml/dispatches" \
  || true)"
if [ "$CODE" = "204" ]; then
  echo "actions write:   OK (dispatched pat-expiry-watch.yml — harmless self-check)"
else
  echo "actions write:   MISSING (dispatch HTTP $CODE) — cannot re-run failed jobs or run the drill"
  MISSING=1
fi

# --- optional: the full dispatch drill --------------------------------
if [ "$DRILL" = 1 ]; then
  echo
  echo "=== DRILL: dispatch live-iso.yml at ref=v0.29.27, release-tag=v0.29.27 ==="
  CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/vp-d.json -w '%{http_code}' -X POST \
    -H "Accept: application/vnd.github+json" \
    -d '{"ref":"v0.29.27","inputs":{"release-tag":"v0.29.27"}}' \
    "https://api.github.com/repos/$REPO/actions/workflows/live-iso.yml/dispatches" || true)"
  [ "$CODE" = "204" ] || { echo "::error::drill dispatch failed (HTTP $CODE)"; exit 1; }
  # The tag is annotated: dereference to the commit the run reports.
  TAGSHA="$(curl -s --netrc-file "$NETRC" \
    "https://api.github.com/repos/$REPO/git/ref/tags/v0.29.27" \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['object']['sha'])")"
  COMMIT="$(curl -s --netrc-file "$NETRC" \
    "https://api.github.com/repos/$REPO/git/tags/$TAGSHA" \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['object']['sha'])")"
  echo "polling run for commit ${COMMIT:0:7} (up to 45 min)…"
  for i in $(seq 1 45); do
    sleep 60
    STATUS="$(curl -s --netrc-file "$NETRC" \
      "https://api.github.com/repos/$REPO/actions/workflows/live-iso.yml/runs?head_sha=$COMMIT&per_page=1" \
      | python3 -c "import json,sys;r=json.load(sys.stdin)['workflow_runs'];print(f\"{r[0]['status']}/{r[0]['conclusion']}\" if r else 'none')")"
    echo "  [$i min] $STATUS"
    case "$STATUS" in
      completed/*) break ;;
    esac
  done
  case "$STATUS" in
    completed/success) ;;
    *) echo "::error::drill run ended $STATUS"; exit 1 ;;
  esac
  DIGEST="$(curl -s --max-time 30 "https://api.github.com/repos/$REPO/releases/tags/v0.29.27" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('assets',[]):
    if a['name']=='nyrqis-live.iso': print(a['digest'].split(':')[-1])")"
  echo "re-shipped amd64 digest: $DIGEST"
  if [ "$DIGEST" = "$PINNED_AMD64_DIGEST" ]; then
    echo "DRILL PASS — digest identical to the pinned original"
  else
    echo "::warning::digest DIFFERS from the pinned original (rebuild inputs are not byte-reproducible). The release now carries the freshly built+smoked tag ISO."
    exit 1
  fi
fi

echo
if [ "$MISSING" = 0 ]; then
  echo "ALL GRANTS PRESENT."
else
  echo "GRANTS MISSING (see above) — mint the replacement with Contents RW, Workflows RW, Actions RW, Variables RW."
fi
