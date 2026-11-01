#!/usr/bin/env bash
#
# firmware_matrix_test.sh -- proves tools/firmware_matrix.sh's VERDICT LOGIC
# bites, without QEMU and without a single real boot.
#
# The matrix driver's job is to turn N gate runs into one artifact whose
# every row is true: this build, this sha256, this expectation, this
# measurement. A driver that silently passes a mislabelled row is worse than
# no matrix, because the artifact would then certify firmware coverage that
# was never measured. So the driver is exercised against a STUB gate (a few
# lines that echo a fixed transcript and exit with a fixed status), which
# makes every interesting path cheap and exactly reproducible:
#
#   two legs, both pass            -> exit 0, both rows OK
#   refusal expected and measured  -> exit 0 (refuse-auth is a real verdict)
#   pass expected, refusal measured-> exit 1, named "no time-based-auth surface"
#   refusal expected, gate passed  -> exit 1, named "DOES enforce"
#   a gate that neither passes nor
#   refuses in the measured shape  -> exit 1, measured other-failure
#   the same firmware listed twice -> exit 1, named as one-build-measured-once
#   a missing firmware image       -> exit 1, named
#   a transcript that does not name
#   the image it claims            -> exit 1, named (the anti-stale-log pin)
#   an inherited cross-build
#   control                        -> cleared per leg, matrix still passes
#   unknown expectation / no entry -> exit 2 (usage)
#
# The stub is the ONLY gate involved: nothing here boots, so a broken driver
# cannot be hidden behind a slow green QEMU run, and a broken QEMU gate
# cannot make the driver look wrong. No network, no writes outside a temp
# directory.
#
# Usage: ./tools/firmware_matrix_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

DRIVER=./tools/firmware_matrix.sh
for f in "$DRIVER"; do
    if [[ ! -x "$f" ]]; then
        echo "FAIL: prerequisite missing or not executable: $f"
        exit 1
    fi
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# expect_case <label> <ok|fail> <expected-exit-code> <needle> <cmd...>
expect_case() {
    local label="$1" want="$2" want_status="$3" needle="$4"
    shift 4
    local out="$TMP/$label.log"
    "$@" >"$out" 2>&1
    local s=$?
    local got="ok"
    [[ $s -ne 0 ]] && got="fail"
    if [[ "$got" != "$want" ]]; then
        bad "$label: expected exit class '$want', got '$got' (status $s); see below"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    if [[ "$want_status" != "any" && "$s" != "$want_status" ]]; then
        bad "$label: expected status $want_status, got $s"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    if ! grep -qF "$needle" "$out"; then
        bad "$label: exit class matched but the named line is absent: '$needle'"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    ok "$label: $want (status $s), named line present"
}

# --- the stub gate --------------------------------------------------------
# Its behaviour comes from sidecar files next to the image it is handed, so
# one stub serves every case and each leg's transcript is a file to inspect.
STUB="$TMP/stub-gate.sh"
cat >"$STUB" <<'STUB_EOF'
#!/usr/bin/env bash
# A few lines standing in for tools/qemu_uefi_ms_secboot_test.sh.
img="$OVMF_CODE_OVERRIDE"
sha="$(sha256sum "$img" | cut -d' ' -f1)"
nopin="$(dirname "$img")/nopin"
if [[ ! -f "$nopin" ]]; then
    echo "==> firmware under test: $img (sha256 $sha)"
fi
if [[ -n "${SECBOOT_CROSS_BUILD_CODE:-}" ]]; then
    echo "FAIL: stub gate: the driver inherited a cross-build control ($SECBOOT_CROSS_BUILD_CODE)"
    exit 1
fi
mode="$(cat "$img.mode" 2>/dev/null || echo other)"
case "$mode" in
    pass)
        echo "PASS: ms OVMF under enforcement accepts the Authenticode-signed stub"
        exit 0 ;;
    refuse)
        echo "-- dbx-update: non-KeK-signed package = 0x8000000000000002 -- REFUSED by the firmware"
        echo "-- dbx-update: KeK-signed package = 0x8000000000000002 -- REFUSED by the firmware"
        echo "FAIL: ms OVMF secboot test failed (positive marker_ok=0, ...)"
        exit 1 ;;
    accepted-anyway)
        echo "PASS: ms OVMF under enforcement accepts the Authenticode-signed stub"
        echo "   dbx-update: SetVariable(dbx) ACCEPTED (1206 bytes) -- EFI_SUCCESS"
        exit 0 ;;
    *)
        echo "something happened that is neither a pass nor the measured refusal"
        exit 1 ;;
esac
STUB_EOF
chmod +x "$STUB"

# mk_fw <name> <mode>: a distinct firmware image (distinct bytes per name) plus
# its stub behaviour.
mk_fw() {
    printf 'firmware-build-%s\n' "$1" >"$TMP/$1.fd"
    printf '%s' "$2" >"$TMP/$1.fd.mode"
}
mk_fw fw-enforcing pass
mk_fw fw-enforcing-2 pass
mk_fw fw-noauth refuse
mk_fw fw-other other
printf 'vars\n' >"$TMP/vars.fd"

echo "==> firmware matrix driver tests"

# 1. two distinct enforcing builds -> matrix holds
expect_case matrix-pass ok 0 "PASS: firmware matrix held on all 2 distinct firmware build(s)" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-pass" \
    "$TMP/fw-enforcing.fd:pass" "$TMP/fw-enforcing-2.fd:pass"

# ... and the table names each build with its own sha256 and verdict.
if grep -qF "fw-enforcing.fd" "$TMP/out-pass/matrix-summary.txt" \
   && grep -qF "fw-enforcing-2.fd" "$TMP/out-pass/matrix-summary.txt" \
   && grep -qE "pass[[:space:]]+pass[[:space:]]+OK" "$TMP/out-pass/matrix-summary.txt"; then
    ok "matrix-pass: the artifact table carries one row per build (image, sha256, expected, measured)"
else
    bad "matrix-pass: the artifact table does not carry a row per build"
    sed 's/^/        /' "$TMP/out-pass/matrix-summary.txt" | tail -10
fi

# ... and the machine-readable artifact agrees with the table.
if python3 - "$TMP/out-pass/matrix-summary.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["firmware_count"] == 2, doc
assert doc["unmet"] == 0, doc
assert [l["measured"] for l in doc["legs"]] == ["pass", "pass"], doc
PY
then
    ok "matrix-pass: matrix-summary.json parses and reports 2 firmware(s), 0 unmet"
else
    bad "matrix-pass: matrix-summary.json does not describe the run"
fi

# 2. a build without the auth surface, EXPECTED to refuse -> a real verdict
expect_case matrix-refuse ok 0 "measured: refuse-auth" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-refuse" \
    "$TMP/fw-enforcing.fd:pass" "$TMP/fw-noauth.fd:refuse-auth"

# 3. refusal measured where a pass was expected -> the mislabel is caught
expect_case mislabel-pass fail 1 "has no time-based-auth surface and belongs in the matrix as refuse-auth" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-mislabel1" \
    "$TMP/fw-noauth.fd:pass"

# 4. the inverse: a build that enforces listed as a refusal-only build
expect_case mislabel-refuse fail 1 "DOES enforce authenticated updates, so it belongs in the matrix as pass" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-mislabel2" \
    "$TMP/fw-enforcing.fd:refuse-auth"

# 5. neither shape: an unclassifiable failure is never a satisfied expectation
expect_case other-failure fail 1 "measured 'other-failure'" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-other" \
    "$TMP/fw-other.fd:pass"

# 6. one firmware listed twice: two rows for one build is a false matrix
expect_case duplicate fail 1 "a matrix that lists one firmware twice measures it once" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-dup" \
    "$TMP/fw-enforcing.fd:pass" "$TMP/fw-enforcing.fd:pass"

# 7. a firmware that is not there
expect_case missing fail 1 "firmware image missing or empty" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-missing" \
    "$TMP/nope.fd:pass"

# 8. the anti-stale-log pin: a transcript that does not name the image
mkdir -p "$TMP/pinned"
cp "$TMP/fw-enforcing.fd" "$TMP/pinned/fw-nopin.fd"
cp "$TMP/fw-enforcing.fd.mode" "$TMP/pinned/fw-nopin.fd.mode"
: >"$TMP/pinned/nopin"
expect_case unpinned fail 1 "the gate transcript does not name this image and sha256" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-unpinned" \
    "$TMP/pinned/fw-nopin.fd:pass"

# 9. an inherited cross-build control must not leak into the legs
SECBOOT_CROSS_BUILD_CODE="$TMP/fw-other.fd" \
expect_case cross-inherited ok 0 "PASS: firmware matrix held" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-cross" \
    "$TMP/fw-enforcing.fd:pass"

# 10. usage: unknown expectation, and an empty matrix
expect_case unknown-expectation fail 1 "unknown expectation 'maybe'" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-bad" "$TMP/fw-enforcing.fd:maybe"

expect_case no-entries fail 2 "no firmware images given" \
    "$DRIVER" --gate "$STUB" --out "$TMP/out-none"

# 11. a gate that is not executable is a usage error, not a measured verdict
expect_case missing-gate fail 2 "gate not found or not executable" \
    "$DRIVER" --gate "$TMP/no-such-gate.sh" --out "$TMP/out-nogate" \
    "$TMP/fw-enforcing.fd:pass"

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS firmware matrix case(s) did not behave as required -- the driver's verdict logic is not biting"
    exit 1
fi
echo "PASS: firmware matrix driver verified -- both verdicts pass through, a mislabelled expectation, an unclassifiable failure, a duplicated build, a missing image, an unpinned transcript, an inherited cross-build control and both usage errors each fail with their own named message"
