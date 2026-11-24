#!/usr/bin/env bash
#
# messages_selfcheck.sh — prove the live-ISO route messages name the producer
# that actually ran.
#
# WHY THIS EXISTS. The BIOS-only fallback in build-live-iso.sh used to log
# "xorriso or grub bins missing" whenever the hybrid path lost, even when
# xorriso WAS installed and only /usr/lib/grub was absent: a claim about a tool
# that had not failed, and no mention of genisoimage, the tool the branch then
# runs. The arm64 refusal tested grub-mkrescue first but listed only
# grub-efi-arm64-bin and xorriso. Both lines were corrected by hand, and a
# later edit could restore either one with nothing else noticing. This script
# is the notice.
#
# WHAT IT PROVES, AND HOW.
#   * It EXTRACTS the shipped text from build-live-iso.sh (awk between the
#     ISO_GAP anchor and the refusal) instead of restating it here, so what is
#     asserted is what runs. An extraction that finds nothing would let every
#     assertion below pass vacuously, so a failed extraction is a FAIL.
#   * It runs that block under four PATH shapes and compares the logged gap
#     list against the set of things this script independently observes
#     missing on each shape. Exit codes are asserted with it: the block must
#     refuse exactly when genisoimage -- the fallback's own tool -- is absent.
#     A message that hardcodes a name (the old bug) cannot match the observed
#     set in all four shapes.
#   * It checks the arm64 refusal against the condition it belongs to: every
#     thing the condition tests (grub-mkrescue, the arm64 module tree, xorriso)
#     must be named in the refusal, and the condition itself is re-read so a
#     rewording that drops a test is caught rather than silently unchecked.
#   * Both checks carry a MUTATION CONTROL: the old, wrong text is fed to the
#     same assertion functions and must be rejected. A checker that accepts the
#     broken message proves nothing about the fixed one.
#
# WHAT IT DOES NOT PROVE. No ISO is built. The hybrid decision ($HYBRID), the
# isolinux staging, genisoimage's output and whether a BIOS-only image boots
# are all untested here; they need root or --skip-chroot plus mksquashfs,
# debootstrap and a rootfs, and running them rewrites a real ISO. The grub-tree
# probe is also only exercised in its "absent" state wherever /usr/lib/grub
# does not exist. This script itself needs only bash and coreutils.
#
# Usage: ./packaging/live/messages_selfcheck.sh
# Exit:  0 every assertion held; 1 anything did not (each is printed).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER="$SCRIPT_DIR/build-live-iso.sh"
# $BASH is this shell's own absolute path (bash sets it), taken before any
# PATH is narrowed: the block runs under a test PATH and the shell must still
# be findable.
BASH_BIN="$BASH"

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }
say() { echo; echo "==> $*"; }

# --- extraction -------------------------------------------------------------
# Anchors are the shipped lines, not line numbers: a reordering still extracts
# the block, a rename stops the extraction and therefore fails this check.
extract_fallback() {
    awk '
        /^    ISO_GAP=""$/ { emitting = 1 }
        emitting { print }
        emitting && index($0, "die \"the BIOS-only fallback") { exit }
    ' "$BUILDER"
}

arm64_condition() {
    awk '
        /^    if grub-mkrescue --version/ { emitting = 1 }
        emitting { print }
        emitting && /command -v xorriso/ { exit }
    ' "$BUILDER"
}

arm64_refusal() {
    awk '/die "arm64 image needs/ { print; exit }' "$BUILDER"
}

# --- what is actually present on a given PATH -------------------------------
grub_trees_present() {
    [[ -d /usr/lib/grub/i386-pc || -d /usr/lib/grub/x86_64-efi ]]
}

observed_gap() {   # $1 directory to search for the three tools
    local dir="$1" gap=""
    PATH="$dir" command -v grub-mkrescue >/dev/null 2>&1 || gap="$gap grub-mkrescue"
    grub_trees_present || gap="$gap grub-bins(/usr/lib/grub)"
    PATH="$dir" command -v xorriso >/dev/null 2>&1 || gap="$gap xorriso"
    PATH="$dir" command -v genisoimage >/dev/null 2>&1 || gap="$gap genisoimage"
    printf '%s' "$gap"
}

# --- run the extracted block ------------------------------------------------
# The block calls log/die, so those are supplied here; sourcing the extracted
# file keeps its quoting exactly as shipped.
run_block() {      # $1 block file, $2 PATH dir -> stdout, block's exit status
    PATH="$2" "$BASH_BIN" -c '
        log() { printf "LOG: %s\n" "$*"; }
        die() { printf "DIE: %s\n" "$*"; exit 1; }
        . "$1"
    ' _ "$1"
}

verify_fallback() {   # $1 label, $2 block file, $3 PATH dir; 0 all held
    local label="$1" file="$2" dir="$3" gap expected out rc
    gap="$(observed_gap "$dir")"
    expected="LOG: hybrid path unavailable (missing:$gap); building BIOS-only isolinux ISO with genisoimage"
    out="$(run_block "$file" "$dir")"
    rc=$?
    echo "  [$label] rc=$rc"
    printf '%s\n' "$out" | sed 's/^/        /'
    if ! printf '%s\n' "$out" | grep -qxF -- "$expected"; then
        bad "$label: the logged gap list is not the set actually missing (wanted: $expected)"
        return 1
    fi
    ok "$label: the logged gap list matches what is actually missing"
    if ! PATH="$dir" command -v genisoimage >/dev/null 2>&1; then
        if [[ "$rc" -eq 1 ]] \
           && printf '%s\n' "$out" | grep -qF -- "DIE: the BIOS-only fallback runs genisoimage, and it is not on PATH"; then
            ok "$label: refused with exit 1, naming genisoimage"
            return 0
        fi
        bad "$label: expected a genisoimage refusal with exit 1 (rc=$rc)"
        return 1
    fi
    if [[ "$rc" -eq 0 ]] && ! printf '%s\n' "$out" | grep -qF -- "DIE:"; then
        ok "$label: no refusal while genisoimage is present (exit 0)"
        return 0
    fi
    bad "$label: refused although genisoimage is present (rc=$rc)"
    return 1
}

# --- arm64 refusal vs the condition it belongs to ---------------------------
refusal_covers_condition() {   # $1 condition text, $2 refusal text; 0 covers
    local cond="$1" msg="$2" pair token phrase
    for pair in grub-mkrescue:grub-mkrescue arm64-efi:grub-efi-arm64-bin xorriso:xorriso; do
        token="${pair%%:*}"
        phrase="${pair#*:}"
        if ! grep -qF -- "$token" <<<"$cond"; then
            echo "        the arm64 condition no longer tests '$token' -- update this check"
            return 1
        fi
        if ! grep -qF -- "$phrase" <<<"$msg"; then
            echo "        the refusal does not name '$phrase', which the condition tests"
            return 1
        fi
    done
    return 0
}

# --- harness ----------------------------------------------------------------
say "preconditions"
if [[ ! -f "$BUILDER" ]]; then
    echo "FAIL: $BUILDER is missing -- there is nothing to check"
    exit 1
fi
if [[ -z "${BASH_BIN:-}" || ! -x "$BASH_BIN" ]]; then
    echo "FAIL: no bash on PATH to run the extracted block with"
    exit 1
fi
ok "$(basename "$BUILDER") and $BASH_BIN"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

stub() { printf '#!/bin/sh\nexit 0\n' > "$1"; chmod +x "$1"; }

say "extract the fallback block from $(basename "$BUILDER")"
FALLBACK="$TMP/fallback-block.sh"
extract_fallback > "$FALLBACK"
if grep -qF 'ISO_GAP=""' "$FALLBACK" && grep -qF 'die "the BIOS-only fallback' "$FALLBACK"; then
    ok "extracted $(wc -l < "$FALLBACK") lines between the ISO_GAP anchor and the refusal"
else
    echo "FAIL: the extraction found no block (anchors renamed?) -- every assertion below would be vacuous"
    exit 1
fi

say "the block under different PATH shapes"
EMPTY="$TMP/nothing";    mkdir -p "$EMPTY"
HYBRID_OK="$TMP/hybrid-without-genisoimage"; mkdir -p "$HYBRID_OK"
stub "$HYBRID_OK/grub-mkrescue"; stub "$HYBRID_OK/xorriso"
ALL_TOOLS="$TMP/all-tools"; mkdir -p "$ALL_TOOLS"
stub "$ALL_TOOLS/grub-mkrescue"; stub "$ALL_TOOLS/xorriso"; stub "$ALL_TOOLS/genisoimage"
ONLY_FALLBACK="$TMP/only-genisoimage"; mkdir -p "$ONLY_FALLBACK"
stub "$ONLY_FALLBACK/genisoimage"

verify_fallback "nothing installed"            "$FALLBACK" "$EMPTY"        || true
verify_fallback "hybrid tools, no genisoimage" "$FALLBACK" "$HYBRID_OK"    || true
verify_fallback "every tool present"           "$FALLBACK" "$ALL_TOOLS"    || true
verify_fallback "only the fallback's tool"     "$FALLBACK" "$ONLY_FALLBACK" || true

say "mutation control: the OLD fallback line must be rejected"
MUTANT="$TMP/fallback-old-text.sh"
sed 's|^    log "hybrid path unavailable.*|    log "xorriso or grub bins missing; building BIOS-only isolinux ISO"|' \
    "$FALLBACK" > "$MUTANT"
if cmp -s "$FALLBACK" "$MUTANT"; then
    bad "the mutation changed nothing -- this control cannot prove anything"
else
    ok "mutant differs from the shipped block"
    # Subshell: the mutant's expected failures must not reach this run's tally.
    if ( verify_fallback "old text" "$MUTANT" "$HYBRID_OK" ) > "$TMP/mutant.log" 2>&1; then
        bad "the old wording was ACCEPTED -- the assertion does not depend on the message"
        sed 's/^/        /' "$TMP/mutant.log"
    else
        ok "the old wording was rejected, as it must be"
        grep '^  FAIL' "$TMP/mutant.log" | sed 's/^/      /'
    fi
fi

say "the arm64 refusal covers the condition it tests"
COND="$(arm64_condition)"
REFUSAL="$(arm64_refusal)"
if [[ -z "$COND" || -z "$REFUSAL" ]]; then
    bad "could not extract the arm64 condition or its refusal (line reworded?)"
else
    echo "  condition: $(printf '%s' "$COND" | tr '\n' ' ' | sed 's/  */ /g')"
    echo "  refusal:   $REFUSAL"
    if refusal_covers_condition "$COND" "$REFUSAL"; then
        ok "the refusal names grub-mkrescue, grub-efi-arm64-bin and xorriso"
    else
        bad "the refusal does not cover every part of its condition"
    fi
    OLD_REFUSAL='        die "arm64 image needs grub-efi-arm64-bin + xorriso (apt-get install grub-efi-arm64-bin xorriso mtools)"'
    if refusal_covers_condition "$COND" "$OLD_REFUSAL"; then
        bad "the OLD arm64 refusal was ACCEPTED -- the check does not depend on the text"
    else
        ok "the old arm64 refusal was rejected, as it must be"
    fi
fi

echo
if [[ "$FAILS" -eq 0 ]]; then
    echo "MESSAGES SELFCHECK: PASS (fallback messages and arm64 refusal name what they tested)"
    exit 0
fi
echo "MESSAGES SELFCHECK: FAIL -- $FAILS assertion(s) did not hold (above)"
exit 1
