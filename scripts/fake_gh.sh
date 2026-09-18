#!/usr/bin/env bash
# Fake gh for release-race testing: release state lives in $FAKE_GH_STATE.
#
# Models the exact gh surface of scripts/attach_release_asset.sh:
#   release view X                → exit 0 iff the release marker exists
#   release view X --json uploadUrl -q .uploadUrl
#                                 → print the fake uploads URL
#   release view X --json assets -q '.assets[].name'
#                                 → print asset filenames, one per line
#   release create X ...          → exit 1 ("already exists") iff the
#                                   marker exists, else create + exit 0
#   release edit X --draft=false  → no-op success iff the release exists
#   api repos/R/releases/tags/T --jq '...select(.name=="N")... | .id'
#                                 → print (assigning if needed) the
#                                   asset id of N, iff N exists
#   api -X DELETE repos/R/releases/assets/ID
#                                 → remove that asset
#
# Concurrency modeling: with FAKE_GH_CREATE_RACE_LOSERS=1, every create
# sleeps 1s first — on a shared state dir the first job's create wins
# and the second's fails with "already exists", deterministically
# exercising the script's re-view fallback path.
#
# Anything unhandled exits 2 loudly (fail-closed).
set -u
STATE="${FAKE_GH_STATE:?FAKE_GH_STATE must point at a state directory}"
mkdir -p "$STATE"
MARKER="$STATE/release-exists"
cmd="${1:-}"; [ $# -gt 0 ] && shift

asset_id_for() { # name  (echo the id; assign 1001 + first-free on demand)
  local name="$1" idf="$STATE/assets/.id-$name" n=1001
  [ -f "$idf" ] && { cat "$idf"; return 0; }
  [ -f "$STATE/assets/$name" ] || return 0
  while [ -e "$STATE/assets/.id-$n" ]; do n=$((n + 1)); done
  echo "$n" > "$idf"
  echo "$n"
}

case "$cmd" in
  release)
    sub="${1:-}"; shift
    case "$sub" in
      view)
        # Distinguish the three call shapes by their --json value.
        # (prev-variable parsing: reading $2 inside the loop would
        # yield the flag itself, not its value.)
        json=""; prev=""
        for a in "$@"; do
          case "$prev" in --json) json="$a" ;; esac
          prev="$a"
        done
        case "${json:-}" in
          uploadUrl)
            [ -f "$MARKER" ] || exit 1
            echo "https://uploads.fake.test/repos/$STATE/releases/1/assets{\?name,label}"
            exit 0 ;;
          assets)
            [ -f "$MARKER" ] || exit 1
            for f in "$STATE/assets"/*; do
              [ -f "$f" ] || continue
              case "$(basename "$f")" in .id-*) continue ;; esac
              basename "$f"
            done
            exit 0 ;;
          *)
            [ -f "$MARKER" ] && exit 0
            exit 1 ;;
        esac
        ;;
      create)
        [ -n "${FAKE_GH_CREATE_RACE_LOSERS:-}" ] && sleep 1
        if [ -f "$MARKER" ]; then
          echo "release already exists" >&2
          exit 1
        fi
        touch "$MARKER"
        exit 0
        ;;
      edit)
        [ -f "$MARKER" ] || exit 1
        exit 0
        ;;
      upload)
        # Legacy shape (pre-curl); kept so nothing silently degrades.
        asset=""
        for a in "$@"; do
          case "$a" in
            --repo|--clobber|-*) ;;
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
  api)
    # api -X DELETE repos/R/releases/assets/ID
    if [ "${1:-}" = "-X" ] && [ "${2:-}" = "DELETE" ]; then
      id="${4##*/}"
      for idf in "$STATE"/assets/.id-*; do
        [ -f "$idf" ] || continue
        if [ "$(cat "$idf")" = "$id" ]; then
          name="${idf##*/.id-}"
          rm -f "$STATE/assets/$name" "$idf"
          exit 0
        fi
      done
      exit 0
    fi
    # api repos/R/releases/tags/T --jq '...select(.name=="N")... | .id'
    jq=""; prev=""
    for a in "$@"; do
      case "$prev" in --jq) jq="$a" ;; esac
      prev="$a"
    done
    if [ -n "$jq" ]; then
      name="$(printf '%s' "$jq" | sed -n 's/.*select(\.name=="\([^"]*\)").*/\1/p')"
      asset_id_for "$name"
      exit 0
    fi
    ;;
esac
echo "fake-gh: unhandled command: ${cmd:-} $*" >&2
exit 2
