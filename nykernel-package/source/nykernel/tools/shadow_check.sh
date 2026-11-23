#!/usr/bin/env bash
#
# shadow_check.sh -- fail when a local shadowing a previous local appears
# anywhere the baseline does not already account for.
#
#   ./tools/shadow_check.sh                 # check (exit 1 on NEW shadowing)
#   ./tools/shadow_check.sh --update        # rewrite the baseline from the
#                                           # current tree (review the diff!)
#   ./tools/shadow_check.sh --update --allow-grow
#                                           # ...even if that baselines more
#                                           # than SHADOW_GROW_MAX new sites
#   ./tools/shadow_check.sh --list          # print every site, no verdict
#
# ENVIRONMENT
#
#   SHADOW_CHECK_REQUIRED=1   a skip (no cargo/clippy) is a FAILURE, not a
#                             pass. CI sets this: a job that silently skipped
#                             the check is a job that never ran it.
#   SHADOW_GROW_MAX=N         how many NEW sites one --update may add
#                             (default 2). --update refuses past it; this is
#                             what stops "just re-baseline it" from erasing
#                             the allowance in a single command. --allow-grow
#                             overrides for a reviewed, one-off shift.
#
# WHY THIS EXISTS
#
# In the 0x84 verdict function two locals were called `info`, and two were
# called `info_ok`. Rust's shadowing made the block-device geometry leg print
# the PLATFORM_INFO record under the BLOCK-INFO label, and made the chain that
# decides the proof's verdict read the platform flag twice instead of the
# block one -- so a claim that was failing could never fail. The gate caught
# the wrong numbers; nothing caught the cause. This check does, on every run,
# and for the whole crate rather than the one file where it bit.
#
# Clippy has the lints for it (`shadow_unrelated`, `shadow_same`), but both are
# in the `restriction` group, i.e. off by default -- silence there means
# nothing. Enabling them without a baseline would drown the check in the
# crate's ~27 existing sites (loop counters, `matches!` pattern binders,
# locals reused in separate match arms), all of which are benign and none of
# which can be read after the rebound name enters scope. So the known sites
# live in tools/shadow_baseline.txt as `path<TAB>name<TAB>count`, and this
# script fails only on shadowing that is NOT in it. A count is used instead
# of a line number so ordinary edits above a site do not churn the baseline;
# a second `m` in a file that already has one is still new.
#
# The baseline is keyed on (file, name), not on which function, because that
# is what a reviewer needs to see in a diff: "another `m` in caps.rs".
#
# OPTIONAL BY DESIGN
#
# Clippy is a separate rustup component (`rustup component add clippy`). If
# it is not installed this script says so and exits 0 -- the boot gates must
# keep working on a machine that only has rustc.
#
# WHERE IT RUNS
#
# `build.sh` calls it on every build (so all four boot gates inherit it),
# and SKIP_SHADOW_CHECK=1 there opts out for one build. CI sets
# SHADOW_CHECK_REQUIRED=1 and installs clippy explicitly, so a runner that
# lost the component fails there instead of quietly passing without the
# check -- and the workflow also greps the gate transcript for the OK line,
# so "the gate ran" is asserted rather than inferred.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
BASELINE="tools/shadow_baseline.txt"
MODE=""
ALLOW_GROW=0
for arg in "$@"; do
    case "$arg" in
        --update)     MODE="--update" ;;
        --list)       MODE="--list" ;;
        --allow-grow) ALLOW_GROW=1 ;;
        *)
            echo "usage: $0 [--update [--allow-grow]|--list]"
            exit 2
            ;;
    esac
done
REQUIRE="${SHADOW_CHECK_REQUIRED:-0}"
GROW_MAX="${SHADOW_GROW_MAX:-2}"

# ---- 1. is clippy here? -------------------------------------------------
# A skip is honest locally (a rustc-only machine must still build) and a
# hole where the check is meant to be a guarantee, so REQUIRE turns it into
# the failure it would otherwise pretend not to be.
skip() {
    if [[ "$REQUIRE" == "1" ]]; then
        echo "shadow check: FAILED -- $1"
        echo "              SHADOW_CHECK_REQUIRED=1, so a skip is a failure here."
        echo "              install it with: rustup component add clippy"
        exit 1
    fi
    echo "shadow check: SKIPPED -- $1"
    echo "              (optional check; SHADOW_CHECK_REQUIRED=1 makes this fatal)"
    exit 0
}

if ! command -v cargo >/dev/null 2>&1; then
    skip "cargo is not on PATH"
fi
if ! cargo clippy --version >/dev/null 2>&1; then
    skip "clippy is not installed for this toolchain"
fi

# ---- 2. run clippy on the real AST -------------------------------------
# The lints are opted into explicitly: they are `restriction` lints and are
# allowed by default. -Zjson-target-spec mirrors build.sh (see its comment).
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
RUSTC_BOOTSTRAP=1 cargo clippy -Zjson-target-spec -- \
    -W clippy::shadow_unrelated -W clippy::shadow_same >"$LOG" 2>&1
CLIPPY_RC=$?

# A compile error is not a shadowing verdict; say which it is.
if [[ $CLIPPY_RC -ne 0 ]] && ! grep -q "shadows a previous\|is shadowed" "$LOG" 2>/dev/null; then
    if grep -q "^error" "$LOG"; then
        echo "shadow check: FAILED -- clippy could not analyse the crate:"
        grep -A3 "^error" "$LOG" | head -12
        exit 1
    fi
fi

# Every warning block looks like:
#   warning: `owner` shadows a previous, unrelated binding
#       --> src/experimental/caps.rs:1036:13
#   warning: `mut r` is shadowed by itself in `r`
#       --> src/foo.rs:12:9
# so the name is the first backticked token and the location is the next
# line that starts with `-->`. Vendored historical trees under src/porting
# are excluded: they are not this crate's code and their style is not ours
# to police.
SITES="$(mktemp)"
trap 'rm -f "$LOG" "$SITES"' EXIT
awk '
    /warning: `/ && (/shadows a previous/ || /is shadowed/) {
        line = $0
        name = ""
        if (match(line, /`[^`]*`/)) {
            name = substr(line, RSTART + 1, RLENGTH - 2)
            sub(/^mut /, "", name)
        }
        if ((getline nxt) > 0 && match(nxt, /--> [^:]+:[0-9]+/)) {
            loc = substr(nxt, RSTART + 4, RLENGTH - 4)
            split(loc, parts, ":")
            # rustc prints the source path as the HOST spells it: forward
            # slashes on Linux, backslashes on Windows. The baseline below is
            # keyed on '/' and the vendored-tree exclusion below greps for
            # '/porting/', so an unnormalised Windows path would both miss the
            # exclusion and fail to match baseline lines recorded elsewhere.
            # One separator in the key, whatever the host.
            gsub(/\\/, "/", parts[1])
            if (name != "") print parts[1] "\t" name
        }
    }
' "$LOG" | grep -v '/porting/' | sort | uniq -c \
  | awk '{ printf "%s\t%s\t%s\n", $2, $3, $1 }' \
  | sort > "$SITES"

COUNT=$(wc -l < "$SITES" | tr -d ' ')

if [[ "$MODE" == "--list" ]]; then
    if [[ "$COUNT" -eq 0 ]]; then
        echo "shadow check: no shadowing at all"
    else
        echo "shadow check: $COUNT site(s) (path, name, occurrences)"
        column -t -s "$(printf '\t')" "$SITES" 2>/dev/null || cat "$SITES"
    fi
    exit 0
fi

if [[ "$MODE" == "--update" ]]; then
    # Growth guard: re-baselining is how a systemic regression becomes
    # invisible, so widening the allowance is a reviewed act. A shrink (a
    # rename that paid off) or a same-size reshuffle is always allowed.
    OLD="$(mktemp)"
    trap 'rm -f "$LOG" "$SITES" "$OLD"' EXIT
    if [[ -f "$BASELINE" ]]; then
        grep -v '^#' "$BASELINE" | grep -v '^$' | sort > "$OLD"
    else
        : > "$OLD"
    fi
    ADDED="$(comm -23 "$SITES" "$OLD")"
    ADDED_N=$(printf '%s\n' "$ADDED" | grep -c . || true)
    OLD_N=$(grep -c . "$OLD" || true)
    if [[ "$ADDED_N" -gt "$GROW_MAX" && "$ALLOW_GROW" != "1" ]]; then
        echo "shadow check: REFUSED -- --update would add $ADDED_N new site(s)"
        echo "              (baseline has $OLD_N; SHADOW_GROW_MAX=$GROW_MAX)."
        echo "              That is a code change, not a baseline change: rename them,"
        echo "              or pass --allow-grow if the growth is reviewed."
        printf '%s\n' "$ADDED" | while IFS="$(printf '\t')" read -r path name n; do
            [[ -n "$path" ]] && echo "    + $path: \`$name\` ($n place(s))"
        done
        exit 1
    fi
    {
        echo "# Known, reviewed shadowing sites: <path> <name> <occurrences>."
        echo "# Maintained by tools/shadow_check.sh --update. Each entry is a"
        echo "# local reused in a scope where the earlier binding is already"
        echo "# dead (loop counters, match-arm binders, separate blocks)."
        echo "# ADDING A LINE HERE IS A REVIEW DECISION: if the shadow can be"
        echo "# read after the rebound name enters scope, fix the code instead."
        cat "$SITES"
    } > "$BASELINE"
    echo "shadow check: baseline rewritten with $COUNT site(s) -> $BASELINE"
    if [[ "$ADDED_N" -gt 0 ]]; then
        echo "              new allowance (+$ADDED_N, budget $GROW_MAX) -- review these:"
        printf '%s\n' "$ADDED" | while IFS="$(printf '\t')" read -r path name n; do
            [[ -n "$path" ]] && echo "    + $path: \`$name\` ($n place(s))"
        done
    fi
    exit 0
fi

# ---- 3. compare against the baseline -----------------------------------
if [[ ! -f "$BASELINE" ]]; then
    echo "shadow check: FAILED -- $BASELINE is missing."
    echo "              Run: $0 --update   (and review the result)"
    exit 1
fi
KNOWN="$(mktemp)"
trap 'rm -f "$LOG" "$SITES" "$KNOWN"' EXIT
grep -v '^#' "$BASELINE" | grep -v '^$' | sort > "$KNOWN"

NEW="$(comm -23 "$SITES" "$KNOWN")"
GONE="$(comm -13 "$SITES" "$KNOWN")"

if [[ -n "$GONE" ]]; then
    # Fewer sites than the baseline is not a regression, but a stale baseline
    # quietly widens the allowance, so say so and point at --update.
    echo "shadow check: note -- the baseline lists sites this tree no longer has:"
    printf '%s\n' "$GONE" | sed 's/^/    /'
    echo "              (run $0 --update to tighten it)"
fi

if [[ -z "$NEW" ]]; then
    echo "shadow check: OK -- $COUNT site(s), all of them in $BASELINE"
    exit 0
fi

echo "shadow check: FAILED -- NEW shadowing:"
printf '%s\n' "$NEW" | while IFS="$(printf '\t')" read -r path name n; do
    echo "    $path: \`$name\` shadowed ($n place(s)), not in the baseline"
done
cat <<'EOF'

A local now re-binds a name that is already bound in the same scope chain.
That is how the block-info verdict printed the platform record and why a
failing claim could not fail: the later binding swallowed the earlier one's
uses. Either give the new local its own name, or -- if the earlier binding is
genuinely dead by that point -- record it:

    ./tools/shadow_check.sh --update && git diff tools/shadow_baseline.txt

and justify the added line in the commit message.
EOF
exit 1
