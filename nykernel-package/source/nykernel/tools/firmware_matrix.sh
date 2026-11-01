#!/usr/bin/env bash
#
# firmware_matrix.sh -- run the ms secure-boot gate against SEVERAL firmware
# builds in one invocation and report one verdict per build.
#
# Why this exists: tools/qemu_uefi_ms_secboot_test.sh takes ONE firmware
# image per run (OVMF_CODE_OVERRIDE), so the cross-build dimension was
# whatever a single shell session happened to remember. "Acceptance holds
# on the firmware we tested" is a weaker claim than "acceptance holds on
# every build in this matrix", and the difference was invisible in the
# transcript -- two runs, two sessions, no single artifact saying which
# builds were covered. This driver produces that artifact: a table of
# firmware sha256 -> expected verdict -> measured verdict, written to disk
# next to the logs.
#
# It does NOT re-implement or relax any assertion. Each leg is the gate,
# unmodified, with OVMF_CODE_OVERRIDE set; a leg's verdict is what the gate
# printed and what it exited with. What the driver adds is:
#
#   * one sha256 per image, so "build A" cannot be confused with "build B"
#     (and two entries with the same sha256 are REFUSED: a matrix that
#     lists one firmware twice measures it once and claims two);
#   * a per-leg pin that the gate reported the SAME image this entry named
#     (the gate prints "firmware under test: <path> (sha256 <sha>)", so a
#     stale or shared log cannot satisfy a leg);
#   * an EXPECTATION per entry, because not every firmware in a matrix is
#     expected to enforce. A build without the time-based-auth variable
#     surface answers EFI_INVALID_PARAMETER to a correctly signed package
#     (measured 2026-10-31 on OVMF_CODE_4M.fd), which is a real, useful
#     verdict -- recorded as `refuse-auth`, not hidden as a failure or
#     wrongly counted as a pass.
#
# Usage:
#   ./tools/firmware_matrix.sh [--out DIR] [--gate SCRIPT] \
#       <image>[:<expect>[:<vars-template>]] ...
#
#   <expect>   pass         the full gate must PASS on this firmware
#              refuse-auth  the gate must FAIL, and the failure must be the
#                           measured unsupported-surface refusal
#                           (EFI_INVALID_PARAMETER, 0x8000000000000002) with
#                           the KeK package NOT accepted
#   <vars>     variable-store template for that firmware (default:
#              uefi/tools/OVMF_VARS_4M.fd)
#
# Examples:
#   # every build in the tree: two that enforce, two that refuse the surface
#   ./tools/firmware_matrix.sh \
#       uefi/tools/OVMF_CODE_4M.ms.fd:pass \
#       uefi/tools/OVMF_CODE_4M.fd:refuse-auth
#
# Writes <out>/matrix-summary.txt and <out>/matrix-summary.json plus one
# log per leg. Exit 0 only when EVERY entry's measured verdict equals its
# expectation.
#
# Cost: one full gate run per entry (several boots each). Not a CI-on-push
# job; the driver's own verdict logic is tested hermetically by
# tools/firmware_matrix_test.sh, and the matrix itself is a measurement you
# schedule.

set -uo pipefail
cd "$(dirname "$0")/.."

GATE=./tools/qemu_uefi_ms_secboot_test.sh
OUT=target/firmware-matrix
ENTRIES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)  OUT="$2"; shift 2 ;;
        --gate) GATE="$2"; shift 2 ;;
        -h|--help) sed -n '3,72p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --) shift; break ;;
        -*) echo "FAIL: unknown option: $1"; exit 2 ;;
        *)  ENTRIES+=("$1"); shift ;;
    esac
done
[[ $# -gt 0 ]] && ENTRIES+=("$@")

if [[ ${#ENTRIES[@]} -eq 0 ]]; then
    echo "FAIL: no firmware images given (see --help)"
    exit 2
fi
if [[ ! -x "$GATE" ]]; then
    echo "FAIL: gate not found or not executable: $GATE"
    exit 2
fi

mkdir -p "$OUT"
FAILS=0
declare -a ROW_IMAGE=() ROW_SHA=() ROW_EXPECT=() ROW_MEASURED=() ROW_RESULT=()

bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

echo "==> firmware matrix: ${#ENTRIES[@]} entr(ies), gate $GATE"
echo "    expectation vocabulary: pass | refuse-auth"
echo

# --- parse + validate before running anything -----------------------------
declare -a IMAGE=() EXPECT=() VARS=()
declare -A SEEN_SHA=()
for entry in "${ENTRIES[@]}"; do
    IFS=: read -r img expect vars <<<"$entry"
    expect="${expect:-pass}"
    vars="${vars:-uefi/tools/OVMF_VARS_4M.fd}"
    if [[ -z "$img" ]]; then
        bad "empty matrix entry '$entry'"
        continue
    fi
    case "$expect" in
        pass|refuse-auth) ;;
        *) bad "$img: unknown expectation '$expect' (pass | refuse-auth)"; continue ;;
    esac
    if [[ ! -s "$img" ]]; then
        bad "$img: firmware image missing or empty"
        continue
    fi
    if [[ ! -s "$vars" ]]; then
        bad "$img: vars template missing or empty: $vars"
        continue
    fi
    sha="$(sha256sum "$img" | cut -d' ' -f1)"
    if [[ -n "${SEEN_SHA[$sha]:-}" ]]; then
        # Two paths, one firmware: the matrix would report two verdicts for
        # one build and imply a covered build that is not in it.
        bad "$img: byte-identical to ${SEEN_SHA[$sha]} (sha256 $sha) -- a matrix that lists one firmware twice measures it once"
        continue
    fi
    SEEN_SHA[$sha]="$img"
    IMAGE+=("$img"); EXPECT+=("$expect"); VARS+=("$vars")
done

if [[ ${#IMAGE[@]} -eq 0 ]]; then
    echo "FAIL: no usable firmware entry in the matrix"
    exit 1
fi

# --- one gate run per firmware -------------------------------------------
leg=0
for i in "${!IMAGE[@]}"; do
    img="${IMAGE[$i]}"; expect="${EXPECT[$i]}"; vars="${VARS[$i]}"
    leg=$((leg + 1))
    sha="$(sha256sum "$img" | cut -d' ' -f1)"
    slug="$(echo "$img" | sed 's#.*/##; s#[^A-Za-z0-9._-]#_#g')"
    log="$OUT/${leg}-${slug}.log"

    echo "==> leg $leg/${#IMAGE[@]}: $img"
    echo "    sha256 $sha, expected: $expect"
    # The cross-build control is a single-firmware device; inside a matrix
    # the matrix IS the cross dimension, so it is cleared per leg rather
    # than inherited from the caller's environment (an inherited control
    # would re-run one build inside every leg and report it as coverage).
    env OVMF_CODE_OVERRIDE="$img" \
        OVMF_VARS_OVERRIDE="$vars" \
        SECBOOT_CROSS_BUILD_CODE= \
        bash "$GATE" >"$log" 2>&1
    status=$?

    # --- what the gate actually did, in the expectation vocabulary ---
    # pass: the gate's own final PASS line, exit 0.
    # refuse-auth: the measured unsupported-surface refusal -- the stub got
    #   EFI_INVALID_PARAMETER for the package, and nothing was accepted.
    # other-failure: neither, which is never satisfiable by an expectation.
    measured=other-failure
    if [[ $status -eq 0 ]] && grep -qaF "PASS: ms OVMF under enforcement" "$log"; then
        measured=pass
    elif grep -qaF "= 0x8000000000000002 -- REFUSED by the firmware" "$log" \
         && ! grep -qaF "SetVariable(dbx) ACCEPTED" "$log"; then
        measured=refuse-auth
    fi

    # --- the pin: this log describes THIS firmware ---
    pin_ok=1
    if ! grep -qaF "firmware under test: $img (sha256 $sha)" "$log"; then
        pin_ok=0
        bad "$img: the gate transcript does not name this image and sha256 -- the leg did not run the firmware this entry claims"
    fi

    result=OK
    if [[ "$expect" == pass && "$measured" == refuse-auth ]]; then
        bad "$img: expected the full gate to PASS; it refused the KeK package with EFI_INVALID_PARAMETER -- this build has no time-based-auth surface and belongs in the matrix as refuse-auth, not pass"
        result=EXPECTATION-MISMATCH
    elif [[ "$expect" == refuse-auth && "$measured" == pass ]]; then
        bad "$img: expected refusal with EFI_INVALID_PARAMETER; the gate PASSED -- this build DOES enforce authenticated updates, so it belongs in the matrix as pass"
        result=EXPECTATION-MISMATCH
    elif [[ "$measured" != "$expect" ]]; then
        bad "$img: expected '$expect'; measured '$measured' (gate exit $status, see $log)"
        result=EXPECTATION-MISMATCH
    fi
    [[ "$pin_ok" -eq 0 ]] && result=UNPINNED

    echo "    measured: $measured (gate exit $status) -- $result"
    echo

    ROW_IMAGE+=("$img"); ROW_SHA+=("$sha"); ROW_EXPECT+=("$expect")
    ROW_MEASURED+=("$measured"); ROW_RESULT+=("$result")
done

# --- the artifact: one table naming build, expectation and measurement ---
SUMMARY="$OUT/matrix-summary.txt"
{
    printf '%-58s %-12s %-12s %-20s %s\n' "FIRMWARE" "SHA256" "EXPECTED" "MEASURED" "RESULT"
    for i in "${!ROW_IMAGE[@]}"; do
        printf '%-58s %-12s %-12s %-20s %s\n' \
            "${ROW_IMAGE[$i]}" "${ROW_SHA[$i]:0:12}" \
            "${ROW_EXPECT[$i]}" "${ROW_MEASURED[$i]}" "${ROW_RESULT[$i]}"
    done
} | tee "$SUMMARY"
echo

# JSON alongside the table, written from the same rows so the two cannot
# describe different runs.
{
    echo "{\"legs\": ["
    first=1
    for i in "${!ROW_IMAGE[@]}"; do
        [[ $first -eq 1 ]] && first=0 || echo ","
        printf ' {"image": %s, "sha256": %s, "expected": %s, "measured": %s, "result": %s}' \
            "\"${ROW_IMAGE[$i]}\"" "\"${ROW_SHA[$i]}\"" "\"${ROW_EXPECT[$i]}\"" \
            "\"${ROW_MEASURED[$i]}\"" "\"${ROW_RESULT[$i]}\""
    done
    echo
    echo "],"
    echo " \"firmware_count\": ${#ROW_IMAGE[@]}, \"unmet\": $FAILS}"
} >"$OUT/matrix-summary.json"

if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: firmware matrix did not hold -- $FAILS entr(ies) did not match their expectation (table above; logs in $OUT)"
    exit 1
fi
echo "PASS: firmware matrix held on all ${#ROW_IMAGE[@]} distinct firmware build(s) -- each leg's measured verdict equals its expectation, and each leg's transcript names the firmware sha256 it ran (table in $SUMMARY)"
exit 0
