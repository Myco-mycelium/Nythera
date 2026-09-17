#!/usr/bin/env bash
# Fake gh for release-race testing: release state lives in $FAKE_GH_STATE.
#
# Semantics match real gh under concurrency:
#   release view   X  → exit 0 iff the release marker exists
#   release create X  → exit 1 ("already exists") iff the marker exists,
#                       else create it and exit 0
#   release upload X f → copy f into $FAKE_GH_STATE/assets/ (clobber)
#
# Supported (for the harness): release view|create|upload. Anything else
# exits 2 loudly.
set -u
STATE="${FAKE_GH_STATE:?FAKE_GH_STATE must point at a state directory}"
mkdir -p "$STATE"
MARKER="$STATE/release-exists"
cmd="${1:-}"; [ $# -gt 0 ] && shift

case "$cmd" in
  release)
    sub="${1:-}"; shift
    case "$sub" in
      view)
        [ -f "$MARKER" ] && exit 0
        exit 1
        ;;
      create)
        if [ -f "$MARKER" ]; then
          echo "release already exists" >&2
          exit 1
        fi
        touch "$MARKER"
        exit 0
        ;;
      upload)
        asset=""
        for a in "$@"; do
          case "$a" in
            --repo|--clobber|-"*") ;;
            *) [ -f "$a" ] && asset="$a" ;;
          esac
        done
        if [ -z "$asset" ]; then
          echo "fake-gh upload: no asset file found in: $*" >&2
          exit 1
        fi
        mkdir -p "$STATE/assets"
        cp "$asset" "$STATE/assets/$(basename "$asset")"
        exit 0
        ;;
    esac
    ;;
esac
echo "fake-gh: unhandled command: ${cmd:-} $*" >&2
exit 2
