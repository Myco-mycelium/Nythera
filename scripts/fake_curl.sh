#!/usr/bin/env bash
# Fake curl for release-race testing — models the ONE invocation shape
# scripts/attach_release_asset.sh uses:
#   curl --fail --silent --show-error --max-time N \
#     --header "Authorization: Bearer X" \
#     --header "Content-Type: application/x-iso9660-image" \
#     --upload-file FILE "URL?name=NAME"
# Semantics: store FILE into $FAKE_GH_STATE/assets/NAME (clobber) — the
# real uploads endpoint registers the asset under the ?name= parameter,
# not the file's basename.
set -u
STATE="${FAKE_GH_STATE:?FAKE_GH_STATE must point at a state directory}"
args=("$@")
url="${args[${#args[@]}-1]}"
name="${url##*\?name=}"
file=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --upload-file) file="${args[$((i + 1))]}" ;;
  esac
done
if [ -z "$file" ] || [ ! -f "$file" ] || [ -z "$name" ]; then
  echo "fake-curl: unhandled invocation: $*" >&2
  exit 2
fi
mkdir -p "$STATE/assets"
cp "$file" "$STATE/assets/$name"
exit 0
