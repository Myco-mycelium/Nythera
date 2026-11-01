#!/usr/bin/env bash
#
# verify_dbx_store_test.sh -- proves the fleet-side verifier's verdicts
# BITE, in both directions.
#
# tools/verify_dbx_store.py answers one operational question -- will THIS
# store refuse THAT certificate -- and its verdicts differ in meaning:
# effective, scheduled-but-not-yet, present-in-a-class-we-ignore, absent.
# A verifier that answered "PASS" for all of them would be worse than no
# verifier at all, because a fleet audit would come back clean. So every
# path is exercised here with a store built for it:
#
#   positive (hash form)    target revoked by To-Be-Signed sha256 -> PASS
#   positive (cert form)    target revoked whole-certificate      -> PASS
#   absent                  nothing in dbx                        -> FAIL
#   scheduled               revoked from 2099                     -> FAIL,
#                           and PASS with --allow-pending
#   ignored class           hash present as EFI_CERT_SHA256 (an
#                           image-hash list, ADR-0030's report-only
#                           set)                                 -> FAIL
#   unreadable              a dbx payload that does not parse     -> FAIL
#   over-broad              the replacement is revoked too        -> FAIL
#                           on --absent replacement
#   fleet                   three machines with three different
#                           verdicts in ONE directory             -> FAIL,
#                           with the per-machine summary counting
#                           each refusal kind once; a clean fleet of
#                           two -> PASS; an EMPTY directory -> FAIL
#                           (auditing nothing is not a clean audit)
#   fleet JSON              the category behind each machine's
#                           verdict is machine-readable
#
# Each case asserts the exit class AND a named line, so an unrelated
# crash cannot satisfy it. Hermetic: two throwaway RSA-2048 certificates,
# a copy of the plain 4M varstore template, no network, no QEMU, and
# nothing written outside the temp directory. The TBS hash the verifier
# derives is cross-checked against tools/patch_carrier_negatives.py,
# which is a DIFFERENT implementation on purpose (the verifier must be
# able to disagree with the signing path).
#
# Usage: ./tools/verify_dbx_store_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

VERIFY=./tools/verify_dbx_store.py
HELPER=./tools/patch_carrier_negatives.py
TEMPLATE=uefi/tools/OVMF_VARS_4M.fd

for f in "$VERIFY" "$HELPER" "$TEMPLATE"; do
    if [[ ! -e "$f" ]]; then
        echo "FAIL: prerequisite missing: $f"
        exit 1
    fi
done
for tool in openssl python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "FAIL: required tool missing: $tool"; exit 1; }
done
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
if [[ ! -x "$VIRT" ]]; then
    echo "FAIL: virt-fw-vars missing (pip name: virt-firmware) -- a store cannot be composed or read"
    exit 1
fi

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

mk_cert() { # $1 CN, $2 basename
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
        -subj "/CN=Nyrqis fleet test $1" \
        -keyout "$TMP/$2.key" -out "$TMP/$2.pem" >/dev/null 2>&1
    openssl x509 -in "$TMP/$2.pem" -outform DER -out "$TMP/$2.der"
}

echo "==> fleet dbx verifier refusal tests"
mk_cert "dead leaf" dead
mk_cert "replacement leaf" replacement

# The signing path's idea of the identity, used as the cross-check below.
HELPER_TBS="$(python3 "$HELPER" sha256-tbs-hex "$TMP/dead.der" | sed 's/^sha256_tbs_hex: //')"
echo "  (dead leaf To-Be-Signed sha256 from patch_carrier_negatives: $HELPER_TBS)"

# --- fixtures: one store per verdict --------------------------------------
$VIRT -i "$TEMPLATE" --set-true SecureBootEnable -o "$TMP/base.fd" -l ERROR >/dev/null 2>&1 \
    || { echo "FAIL: could not build the baseline store from $TEMPLATE"; exit 1; }

mk_store() { # $1 out, $2... payload files (concatenated into one dbx)
    local out="$1"; shift
    cat "$@" >"$TMP/payload.der"
    python3 "$HELPER" make-dbx-auth-update "$TMP/payload.der" 2027 "$TMP/payload-auth2.der" >/dev/null
    cp "$TMP/base.fd" "$out"
    "$VIRT" --inplace "$out" --set-dbx "$TMP/payload-auth2.der" -l ERROR >/dev/null 2>&1
}

python3 "$HELPER" make-dbx-hashlist "$TMP/dead.der" 2020 "$TMP/dead-tbs.der" >/dev/null
mk_store "$TMP/store-tbs.fd" "$TMP/dead-tbs.der"

python3 "$HELPER" make-dbx-hashlist "$TMP/dead.der" 2099 "$TMP/dead-tbs-2099.der" >/dev/null
mk_store "$TMP/store-future.fd" "$TMP/dead-tbs-2099.der"

python3 "$HELPER" make-dbx-hashlist "$TMP/replacement.der" 2020 "$TMP/replacement-tbs.der" >/dev/null
mk_store "$TMP/store-overbroad.fd" "$TMP/dead-tbs.der" "$TMP/replacement-tbs.der"

# Whole-certificate form: virt-fw composes it from the PEM.
cp "$TMP/base.fd" "$TMP/store-cert.fd"
"$VIRT" --inplace "$TMP/store-cert.fd" --add-dbx-cert "d719b2cb-3d3a-4596-a3bc-dad00e67656f" \
    "$TMP/dead.pem" -l ERROR >/dev/null 2>&1

# The ignored class: the SAME identity placed in an EFI_CERT_SHA256 list,
# which is an image-hash list the revocation walk does not read (ADR-0030).
python3 - "$TMP/dead.der" "$TMP/dead-sha256-class.der" <<'PY'
import hashlib, struct, sys, uuid
sys.path.insert(0, "tools")
import verify_dbx_store as v

der = open(sys.argv[1], "rb").read()
tbs = hashlib.sha256(v.cert_tbs(der)).digest()
entry = bytes(16) + tbs                      # owner GUID + hash, NO EFI_TIME
list_size = 28 + len(entry)
# EFI_CERT_SHA256, the image-hash class the revocation walk does not read.
sha256_guid = uuid.UUID("c1c41626-504c-4092-aca9-41f936934328").bytes_le
blob = sha256_guid + struct.pack("<III", list_size, 0, len(entry)) + entry
open(sys.argv[2], "wb").write(blob)
PY
mk_store "$TMP/store-ignored.fd" "$TMP/dead-sha256-class.der"

printf 'not a signature list at all' >"$TMP/garbage.der"

# --- cases ----------------------------------------------------------------
expect_case positive-hash ok "PASS: 1 artifact(s) revoke" \
    "$VERIFY" --store "$TMP/store-tbs.fd" --cert "$TMP/dead.der"

# The derived identity must BE the other implementation's identity.
"$VERIFY" --store "$TMP/store-tbs.fd" --cert "$TMP/dead.der" >"$TMP/derive.log" 2>&1
if grep -qF "To-Be-Signed sha256 $HELPER_TBS" "$TMP/derive.log"; then
    ok "cross-check: the verifier's own DER walk agrees with patch_carrier_negatives ($HELPER_TBS)"
else
    bad "cross-check: the verifier derived a different To-Be-Signed hash than patch_carrier_negatives ($HELPER_TBS)"
    sed 's/^/        /' "$TMP/derive.log" | grep "To-Be-Signed" || true
fi

expect_case positive-whole-cert ok "match: EFI_CERT_X509" \
    "$VERIFY" --store "$TMP/store-cert.fd" --cert "$TMP/dead.pem"

expect_case absent fail "this store has no dbx variable at all" \
    "$VERIFY" --store "$TMP/base.fd" --cert "$TMP/dead.der"

expect_case scheduled fail "revoked only FROM 2099-01-01" \
    "$VERIFY" --store "$TMP/store-future.fd" --cert "$TMP/dead.der"

expect_case scheduled-allowed ok "pending: EFI_CERT_X509_SHA256 becomes effective 2099-01-01" \
    "$VERIFY" --store "$TMP/store-future.fd" --cert "$TMP/dead.der" --allow-pending

expect_case ignored-class fail "the kernel's revocation walk does not read" \
    "$VERIFY" --store "$TMP/store-ignored.fd" --cert "$TMP/dead.der"

expect_case unreadable fail "cannot read" \
    "$VERIFY" --dbx "$TMP/garbage.der" --cert "$TMP/dead.der"

expect_case overbroad fail "revokes it too" \
    "$VERIFY" --store "$TMP/store-overbroad.fd" --cert "$TMP/dead.der" \
    --absent "$TMP/replacement.der"

expect_case overbroad-clean ok "do not revoke the 1 certificate(s) named --absent" \
    "$VERIFY" --store "$TMP/store-tbs.fd" --cert "$TMP/dead.der" \
    --absent "$TMP/replacement.der"

# --- fleet mode: one call, one verdict per machine, one distribution ----
# The counts are the point: "3 machine(s): 1 enforcing, 1 ignored-class,
# 1 no-dbx-variable" is the report an operator needs after a rollout, and
# it must not be reachable by a run that silently audited nothing.
mkdir -p "$TMP/fleet" "$TMP/fleet-clean" "$TMP/fleet-empty"
cp "$TMP/store-tbs.fd" "$TMP/fleet/machine-a.fd"
cp "$TMP/base.fd" "$TMP/fleet/machine-b.fd"
cp "$TMP/store-ignored.fd" "$TMP/fleet/machine-c.fd"
printf 'not a store\n' >"$TMP/fleet/README.txt"   # must be ignored
cp "$TMP/store-tbs.fd" "$TMP/fleet-clean/machine-a.fd"
cp "$TMP/store-tbs.fd" "$TMP/fleet-clean/machine-b.fd"

expect_case fleet-mixed fail \
    "3 machine(s): 1 enforcing, 1 ignored-class, 1 no-dbx-variable" \
    "$VERIFY" --fleet "$TMP/fleet" --cert "$TMP/dead.der"

# ... and the per-machine lines must name the machine, not the blob.
"$VERIFY" --fleet "$TMP/fleet" --cert "$TMP/dead.der" >"$TMP/fleet-mixed.log" 2>&1
if grep -qF "FAIL  machine-c.fd" "$TMP/fleet-mixed.log" \
   && grep -qF "PASS  machine-a.fd" "$TMP/fleet-mixed.log" \
   && ! grep -qF "README.txt" "$TMP/fleet-mixed.log"; then
    ok "fleet-mixed: every machine named (non-store files ignored by --fleet-glob)"
else
    bad "fleet-mixed: the per-machine lines do not name each machine as required"
    sed 's/^/        /' "$TMP/fleet-mixed.log" | tail -12
fi

expect_case fleet-clean ok "2 machine(s): 2 enforcing" \
    "$VERIFY" --fleet "$TMP/fleet-clean" --cert "$TMP/dead.der"

expect_case fleet-empty fail "auditing an empty fleet proves nothing" \
    "$VERIFY" --fleet "$TMP/fleet-empty" --cert "$TMP/dead.der"

# The category is the part of a fleet verdict a report can be built on.
"$VERIFY" --fleet "$TMP/fleet" --cert "$TMP/dead.der" --json >"$TMP/fleet.json" 2>&1
if python3 - "$TMP/fleet.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
cats = {r["machine"]: [f["category"] for f in r["failures"]] for r in doc["results"]}
assert cats["machine-a.fd"] == [], cats
assert cats["machine-c.fd"] == ["ignored-class"], cats
assert cats["machine-b.fd"] == ["no-dbx-variable"], cats
PY
then
    ok "fleet-json: each machine's failure category is machine-readable"
else
    bad "fleet-json: the JSON verdict does not carry the categories the summary counts"
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS fleet verifier case(s) did not behave as required -- a verdict above is not biting"
    exit 1
fi
echo "PASS: fleet dbx verifier verdicts verified -- both enforced forms pass, absent/scheduled/ignored-class/unreadable stores and an over-broad revocation each fail with their own named message, and the derived To-Be-Signed hash agrees with the signing path's independent implementation"
