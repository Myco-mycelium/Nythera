#!/usr/bin/env bash
#
# ceremony_refusals_test.sh -- proves the rotation ceremony's checks
# BITE, rather than merely existing.
#
# tools/rotate_leaf_ceremony.sh's named checks ARE the gate that stands
# between a rotation and shipping a revocation that kills the
# replacement leaf (ADR-0029's scope decision) or a signing key with
# shared permissions (ADR-0028's offline-root posture). A check that
# silently stopped firing would look exactly like a passing ceremony, so
# this test exercises each refusal PATH:
#
#   positive control  a real ceremony run PASSES (so the negatives below
#                     are specific, not "this script always fails")
#   1. missing root   an explicit --root-key that does not exist must
#                     fail with the ceremony's own input message
#   2. loose custody  a root key with group bits must fail with the
#                     custody message, not proceed
#   3. serial reuse   a replacement leaf forced onto the DEAD leaf's
#                     serialNumber must fail on the serial check -- the
#                     whole-certificate dbx form's scope rule
#
# Every case asserts BOTH the exit status and the specific named FAIL
# line, so an unrelated crash cannot satisfy a case. No QEMU, no network,
# no writes outside a temp directory; the enrolled dev chain is only
# READ (the custody case copies the root key to the temp dir to chmod it,
# never touching the original).
#
# Usage: ./tools/ceremony_refusals_test.sh
# Requires the dev chain to exist (build.sh -> make_carrier.sh owns it).

set -uo pipefail
cd "$(dirname "$0")/.."

CEREMONY=./tools/rotate_leaf_ceremony.sh
ROOT_KEY=uefi/tools/trust/carrier-dev-key.pem
ROOT_CERT=uefi/tools/trust/carrier-dev-cert.pem
DEAD_LEAF=uefi/tools/trust/carrier-dev-leaf-cert.pem

for f in "$CEREMONY" "$ROOT_KEY" "$ROOT_CERT" "$DEAD_LEAF"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: prerequisite missing: $f (run build.sh first -- it generates the dev chain)"
        exit 1
    fi
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

ok()   { echo "  OK: $*"; }
bad()  { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# expect_case <label> <expected-exit:ok|fail> <expected-substring> <cmd...>
# Runs the command, captures its output, and requires the exit class AND
# the named line -- the substring is what makes the case specific.
expect_case() {
    local label="$1" want="$2" needle="$3"
    shift 3
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
    if ! grep -qF "$needle" "$out"; then
        bad "$label: exit class '$want' matched but the named line is absent: '$needle'"
        sed 's/^/        /' "$out" | tail -20
        return
    fi
    ok "$label: $want, named line present (exit $s)"
}

echo "==> ceremony refusal tests"

# --- positive control: a real run passes, so the negatives mean something ---
expect_case positive ok "PASS: rotation ceremony complete" \
    "$CEREMONY" --out "$TMP/positive"

# --- 1. missing root pair: the ceremony will not invent an anchor ---
expect_case missing-root fail "FAIL: ceremony input missing or empty" \
    "$CEREMONY" --out "$TMP/missing-root" --root-key "$TMP/does-not-exist.pem"

# --- 2. custody: a group-readable root key must stop the ceremony ---
cp "$ROOT_KEY" "$TMP/loose-key.pem"
chmod 640 "$TMP/loose-key.pem"
expect_case loose-custody fail "group/world bits set" \
    "$CEREMONY" --out "$TMP/loose" --root-key "$TMP/loose-key.pem" --root-cert "$ROOT_CERT"

# --- 3. serial reuse: ADR-0029's whole-certificate scope rule ---
DEAD_SERIAL="$(openssl x509 -in "$DEAD_LEAF" -noout -serial | sed 's/^serial=//')"
echo "  (dead leaf serialNumber: $DEAD_SERIAL)"
expect_case serial-reuse fail "reuses the dead leaf's serialNumber" \
    "$CEREMONY" --out "$TMP/serial-reuse" --serial "$DEAD_SERIAL"

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS ceremony refusal case(s) did not behave as required -- a check above is not biting"
    exit 1
fi
echo "PASS: rotation ceremony refusal paths verified -- positive run passes, and missing inputs, loose custody and serial reuse each fail with their own named message"
