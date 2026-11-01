#!/usr/bin/env bash
#
# update_ceremony_refusals_test.sh -- proves the revocation UPDATE
# ceremony's checks bite, rather than merely existing.
#
# tools/sign_dbx_update_ceremony.sh stands between an operator and a
# revocation package that firmware refuses, or one that bricks the
# fleet. A check that silently stopped firing would look exactly like a
# passing ceremony, so this test exercises each refusal PATH:
#
#   positive control  a real ceremony run PASSES (so the negatives below
#                     are specific, not "this script always fails")
#   1. missing input  an input that does not exist must fail with the
#                     ceremony's own message
#   2. loose custody  a KEK key with group bits must fail with the
#                     custody message, not sign anything
#   3. anchor target  revoking the trust anchor must fail: it would
#                     refuse every carrier, including replacements
#   4. wrong signer   a KEK that is not the enrolled KEK must fail
#                     before a package is produced
#   5. stale stamp    a stamp not newer than --previous-stamp must fail
#                     (firmware rejects non-increasing stamps)
#
# Every case asserts BOTH the exit status and the specific named FAIL
# line, so an unrelated crash cannot satisfy a case. No QEMU, no network,
# no writes outside a temp directory; the dev chain is only READ (the
# custody case copies the key to the temp dir to chmod it).
#
# Usage: ./tools/update_ceremony_refusals_test.sh
# Requires the dev chain to exist (build.sh -> make_carrier.sh owns it).

set -uo pipefail
cd "$(dirname "$0")/.."

CEREMONY=./tools/sign_dbx_update_ceremony.sh
KEK_CERT=uefi/tools/trust/carrier-dev-cert.pem
KEK_KEY=uefi/tools/trust/carrier-dev-key.pem
LEAF=uefi/tools/trust/carrier-dev-leaf-cert.pem
STRANGER_CERT=uefi/tools/trust/mok-dev-cert.pem
STRANGER_KEY=uefi/tools/trust/mok-dev-key.pem

for f in "$CEREMONY" "$KEK_CERT" "$KEK_KEY" "$LEAF" "$STRANGER_CERT" "$STRANGER_KEY"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: prerequisite missing: $f (run build.sh first -- it generates the dev chain)"
        exit 1
    fi
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# expect_case <label> <expected-exit:ok|fail> <expected-substring> <cmd...>
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

echo "==> revocation update ceremony refusal tests"

# --- positive control: a real run passes, so the negatives mean something ---
expect_case positive ok "PASS: signing/distribution ceremony complete" \
    "$CEREMONY" --revoke "$LEAF" --out "$TMP/positive"

# --- 1. missing input: the ceremony will not invent the target ---------
expect_case missing-input fail "FAIL: ceremony input missing or empty" \
    "$CEREMONY" --revoke "$TMP/does-not-exist.pem" --out "$TMP/missing"

# --- 2. custody: a group-readable KEK must stop the ceremony -----------
cp "$KEK_KEY" "$TMP/loose-key.pem"
chmod 640 "$TMP/loose-key.pem"
expect_case loose-custody fail "group/world bits set" \
    "$CEREMONY" --revoke "$LEAF" --kek-key "$TMP/loose-key.pem" --out "$TMP/loose"

# --- 3. anchor target: the one certificate this must never revoke ------
expect_case anchor-target fail "the revocation target IS the trust anchor" \
    "$CEREMONY" --revoke "$KEK_CERT" --out "$TMP/anchor"

# --- 4. signer identity: not the enrolled KEK --------------------------
expect_case wrong-signer fail "the signing certificate is not the enrolled KEK" \
    "$CEREMONY" --revoke "$LEAF" --kek-cert "$STRANGER_CERT" --kek-key "$STRANGER_KEY" \
    --enrolled-kek "$KEK_CERT" --out "$TMP/wrong-signer"

# --- 5. stale stamp: firmware rejects a non-increasing stamp -----------
expect_case stale-stamp fail "is not newer than the previous stamp" \
    "$CEREMONY" --revoke "$LEAF" --stamp-year 2026 --previous-stamp 2026-06-01 \
    --out "$TMP/stale"

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS ceremony refusal case(s) did not behave as required -- a check above is not biting"
    exit 1
fi
echo "PASS: revocation update ceremony refusal paths verified -- the positive run passes, and a missing input, loose custody, an anchor target, a non-enrolled signer and a stale stamp each fail with their own named message"
