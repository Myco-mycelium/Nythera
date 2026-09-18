#!/usr/bin/env bash
# rotate_push_pat.sh — one-pass rotation of the workstation's GitHub
# push credential (the fine-grained PAT that replaced the inline-URL
# token from .git/config during the 2026-09-18 credential-hygiene fix).
#
# Run from the repo root AFTER minting a new fine-grained PAT in the
# browser (Settings → Developer settings → Fine-grained tokens):
#
#   scripts/rotate_push_pat.sh
#   # paste the new token when prompted (hidden input)
#
# Recommended grants for the new token (repo Myco-mycelium/Nythera):
#   Contents:      read/write   (push + release asset upload)
#   Workflows:     read/write   (push workflow-file changes)
#   Actions:       read/write   (re-run failed jobs, dispatch workflows)
#   Variables:     read/write   (set the PAT_EXPIRES_AT repo variable)
#
# Safety ordering (the v1 lesson — a negative probe left a FAKE token
# in the live store because v1 swapped the store before validating):
#   1. validate the new token FIRST (identity + auth probe, both via
#      0600/0700 temp files so the token never enters process argv),
#   2. only then swap the credential store,
#   3. then set the expiry variable (API if granted, UI text if not).
# The script never echoes the token and shreds its temp files.
set -euo pipefail

REPO="Myco-mycelium/Nythera"
HOST="github.com"
CREDS="${GIT_CREDS_FILE:-$HOME/.git-credentials-nyrqis}"

printf 'Paste the NEW fine-grained PAT (hidden): '
IFS= read -rs NEW_TOKEN
echo
[ -n "$NEW_TOKEN" ] || { echo "empty token — aborting"; exit 1; }
case "$NEW_TOKEN" in
  github_pat_*|ghp_*) ;;
  *) echo "warning: token lacks a known GitHub PAT prefix — continuing" ;;
esac

TMP="$(mktemp)"; NETRC="$(mktemp)"; ASKPASS="$(mktemp)"
trap 'rm -f "$TMP" "$NETRC" "$ASKPASS"' EXIT
chmod 600 "$TMP" "$NETRC"; chmod 700 "$ASKPASS"
# netrc + askpass: the token stays out of process argv entirely.
cat > "$NETRC" <<NETRCEOF
machine github.com login x-access-token password $NEW_TOKEN
machine api.github.com login x-access-token password $NEW_TOKEN
NETRCEOF
cat > "$ASKPASS" <<ASKEOF
#!/bin/sh
case "\$1" in
  Username*) echo x-access-token ;;
  *) echo '$NEW_TOKEN' ;;
esac
ASKEOF

# 1. VALIDATE FIRST — identity probe (API, via netrc).
CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/pat-user.json -w '%{http_code}' \
  https://api.github.com/user)"
[ "$CODE" = "200" ] || {
  echo "::error::new token rejected by /user (HTTP $CODE) — the live credential store was NOT touched"
  exit 1
}
LOGIN="$(python3 -c "import json;print(json.load(open('/tmp/pat-user.json'))['login'])")"
echo "new token identity: $LOGIN"
rm -f /tmp/pat-user.json

# 2. Auth probe — ls-remote exercises the credential read-only, via
#    askpass (token in a 0700 temp script, not in argv).
GIT_TERMINAL_PROMPT=0 GIT_ASKPASS="$ASKPASS" git -c credential.helper= \
  ls-remote "https://$HOST/$REPO.git" HEAD >/dev/null 2>&1 \
  || { echo "::error::credential does not authenticate to $REPO — store NOT touched"; exit 1; }
echo "credential authenticates (ls-remote OK)"

# 3. NOW swap the credential store entry (0600, only file that holds it).
OLD_TOKEN="$(grep -o 'github_pat_[^@]*' "$CREDS" 2>/dev/null | head -1 || true)"
if [ -n "$OLD_TOKEN" ]; then
  grep -v "x-access-token:$OLD_TOKEN@$HOST" "$CREDS" > "$TMP" || true
else
  cp "$CREDS" "$TMP"
fi
echo "https://x-access-token:$NEW_TOKEN@$HOST" >> "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$CREDS"
echo "credential store updated ($CREDS, mode $(stat -c %a "$CREDS"))"

# 4. Flush any cached copy of the old token.
printf 'protocol=https\nhost=%s\n' "$HOST" | git credential-cache exit >/dev/null 2>&1 || true

# 5. Set the expiry variable for pat-expiry-watch.yml (API when the
#    token has Variables write, UI instructions otherwise).
printf 'Expiry date of the NEW token, YYYY-MM-DD: '
IFS= read -r EXPIRES
if [ -n "$EXPIRES" ] && date -u -d "$EXPIRES" +%s >/dev/null 2>&1; then
  CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/pat-var.json -w '%{http_code}' -X PATCH \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"PAT_EXPIRES_AT\",\"value\":\"$EXPIRES\"}" \
    "https://api.github.com/repos/$REPO/actions/variables/PAT_EXPIRES_AT")"
  if [ "$CODE" = "204" ]; then
    echo "PAT_EXPIRES_AT repo variable set to $EXPIRES"
  elif [ "$CODE" = "404" ]; then
    CODE="$(curl -s --netrc-file "$NETRC" -o /tmp/pat-var.json -w '%{http_code}' -X POST \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"PAT_EXPIRES_AT\",\"value\":\"$EXPIRES\"}" \
      "https://api.github.com/repos/$REPO/actions/variables")"
    [ "$CODE" = "201" ] && echo "PAT_EXPIRES_AT repo variable created ($EXPIRES)" \
      || echo "variable create failed (HTTP $CODE): set it in Settings → Secrets and variables → Actions → Variables"
  else
    echo "variable upsert failed (HTTP $CODE): set PAT_EXPIRES_AT=$EXPIRES in Settings → Secrets and variables → Actions → Variables"
  fi
  rm -f /tmp/pat-var.json
else
  echo "no valid date given: set PAT_EXPIRES_AT in Settings → Secrets and variables → Actions → Variables (pat-expiry-watch.yml needs it)"
fi

echo
echo "ROTATION COMPLETE. Old token (if any) is dead once you delete it:"
echo "  Settings → Developer settings → Fine-grained tokens → delete the old entry"
echo "Then verify a real push from this repo: git push --dry-run origin main"
