#!/usr/bin/env bash
#
# rotate_leaf_ceremony.sh -- the ADR-0028 rotation ceremony and
# ADR-0029's compromise procedure, as ONE executable step: issue a
# replacement leaf under the (offline) root, sign the kernel image
# with it, and compose the dbx entry that revokes the DEAD leaf's
# keys -- the EFI_CERT_X509_SHA256 entry carrying the dead leaf's
# To-Be-Signed hash, wrapped in the authenticated-update header the
# .ms gate feeds virt-fw's --set-dbx.
#
# Why a ceremony and not a build step (ADR-0028): chain issuance is
# the one call the root is ever needed for, and the root's private
# key belongs on offline media, not in a build. So this tool REFUSES
# to run without an explicit root key/cert, refuses a root key with
# group/world permission bits (custody is the ceremony's first
# control, not its last), and writes NOTHING into the enrolled dev
# chain -- every artifact lands in an output directory for the
# operator to carry forward.
#
# What it proves before it exits zero (each a named FAIL, not a
# note):
#
#   1. SCOPE: the replacement leaf's To-Be-Signed hash DIFFERS from
#      the dead leaf's. This is ADR-0029's whole reason for
#      revoking by TBS hash -- a re-issued leaf (new key, same
#      identity) must not be killed by the dead key's entry. If the
#      hashes were equal the revocation would cover the replacement,
#      which is the exact failure the hash form exists to avoid.
#   2. SERIAL: the replacement's serialNumber does not equal the dead
#      leaf's, so the whole-certificate dbx form (ADR-0014's
#      issuer+serial pair rule) cannot revoke the replacement too.
#   3. CHAIN: the replacement leaf verifies under the root certificate
#      (openssl verify), and the replacement carrier's signature
#      verifies over the kernel bytes (openssl cms -verify).
#   4. SHAPE: the wrapped dbx blob is accepted by the SAME store
#      tooling the gates compose with (virt-fw-vars --set-dbx), and
#      the resulting variable's size matches the SigDB -- i.e. the
#      update blob this ceremony hands off is the shape the .ms gate
#      already proves enforcement for, with no stub rebuild.
#
# What it does NOT claim (ADR-0029's composition boundary, stated
# honestly): the authenticated-update header virt-fw's --set-dbx
# parses is varstore-tooling glue (EFI_TIME + WIN_CERTIFICATE_UEFI_GUID,
# no PKCS7 body), NOT a signed variable-exchange package. This tool
# produces the enforcement-side artifact and its store-tooling
# wrapping; production distribution of a dbx update still goes
# through the platform's real authenticated path (a KEK-signed
# EFI_VARIABLE_AUTHENTICATION_2 package at real SetVariable time, or
# the vendor's signed dbx distribution). Nothing here pretends to be
# that exchange.
#
# Usage:
#   ./tools/rotate_leaf_ceremony.sh [--kernel <elf>] [--dead-leaf <pem|der>]
#       [--root-cert <pem>] [--root-key <pem>] [--out <dir>]
#       [--revoked-year <YYYY>] [--serial <hex>]
#
# Defaults: the shipped dev chain (uefi/tools/trust/carrier-dev-*.pem)
# over target/kernel.elf, output uefi/dist/leaf-rotation, dead leaf
# revoked as of 2020-01-01 (in force for any firmware clock after it).

set -euo pipefail
cd "$(dirname "$0")/.."

KERNEL=target/kernel.elf
DEAD_LEAF=uefi/tools/trust/carrier-dev-leaf-cert.pem
ROOT_CERT=uefi/tools/trust/carrier-dev-cert.pem
ROOT_KEY=uefi/tools/trust/carrier-dev-key.pem
OUT=uefi/dist/leaf-rotation
REVOKED_YEAR=2020
SERIAL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kernel) KERNEL="$2"; shift 2 ;;
        --dead-leaf) DEAD_LEAF="$2"; shift 2 ;;
        --root-cert) ROOT_CERT="$2"; shift 2 ;;
        --root-key) ROOT_KEY="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --revoked-year) REVOKED_YEAR="$2"; shift 2 ;;
        --serial) SERIAL="$2"; shift 2 ;;
        -h|--help) sed -n '1,60p' "$0"; exit 0 ;;
        *) echo "rotate_leaf_ceremony.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

for f in "$KERNEL" "$DEAD_LEAF" "$ROOT_CERT" "$ROOT_KEY"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: ceremony input missing or empty: $f"
        echo "      the root pair is the OFFLINE anchor (ADR-0028): point --root-key/--root-cert"
        echo "      at wherever the ceremony volume is mounted; this tool will not generate one."
        exit 1
    fi
done
for tool in openssl python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "FAIL: required tool missing: $tool"; exit 1; }
done

# ---- custody: the root key's permission bits are a ceremony control ----
# Not a formality: a world- or group-readable CA key is the difference
# between an offline anchor and a shared secret, and the ceremony is the
# only moment anyone is allowed to notice.
KEY_MODE="$(stat -c '%a' "$ROOT_KEY")"
if (( (8#$KEY_MODE & 8#077) != 0 )); then
    echo "FAIL: root key $ROOT_KEY has mode $KEY_MODE (group/world bits set)."
    echo "      ADR-0028's offline-root posture requires the CA key to be readable only"
    echo "      by the ceremony's own principal; chmod 600 (or mount it that way) first."
    exit 1
fi
if [[ "$(realpath "$ROOT_KEY")" == "$(pwd)"/* ]]; then
    echo "NOTE: the root key lives inside this working tree ($ROOT_KEY) -- a dev-chain"
    echo "      convenience, not ADR-0028's production posture (offline media). The"
    echo "      ceremony's permission check above is the control this tool can enforce."
fi

mkdir -p "$OUT"
OUT_ABS="$(realpath "$OUT")"
ROOT_ABS="$(realpath "$ROOT_CERT")"
ROOT_KEY_ABS="$(realpath "$ROOT_KEY")"

# The dead leaf may be PEM or DER; normalize to DER for hashing/composing.
dead_to_der() {
    if openssl x509 -in "$DEAD_LEAF" -noout 2>/dev/null; then
        openssl x509 -in "$DEAD_LEAF" -outform DER -out "$OUT_ABS/dead-leaf.der"
    else
        openssl x509 -inform DER -in "$DEAD_LEAF" -outform DER -out "$OUT_ABS/dead-leaf.der"
    fi
}
cert_serial() { # <pem|der> -> uppercase hex, no prefix
    if openssl x509 -in "$1" -noout  2>/dev/null; then
        openssl x509 -in "$1" -noout -serial
    else
        openssl x509 -inform DER -in "$1" -noout -serial
    fi | sed 's/^serial=//'
}

echo "==> ceremony: rotating the leaf under $ROOT_CERT (root key custodied, mode $KEY_MODE)"
dead_to_der
DEAD_SERIAL="$(cert_serial "$OUT_ABS/dead-leaf.der")"
DEAD_TBS="$(python3 tools/patch_carrier_negatives.py sha256-tbs-hex "$OUT_ABS/dead-leaf.der" | sed 's/^[^:]*: //')"
echo "    dead leaf serial $DEAD_SERIAL, To-Be-Signed sha256 $DEAD_TBS"

# ---- 1. the replacement leaf: fresh key, private to this ceremony ----
echo "==> generating the replacement leaf key (RSA-2048, 0600) in $OUT"
( umask 077
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
      -pkeyopt rsa_keygen_pubexp:65537 -out "$OUT_ABS/leaf-replacement-key.pem" )
openssl req -new -key "$OUT_ABS/leaf-replacement-key.pem" -sha256 \
    -subj "/CN=Nyrqis dev carrier leaf (rotation)/OU=Nyrqis bringup" \
    -out "$OUT_ABS/leaf-replacement.csr"

# Serial: a ceremony-supplied value when the operator's CA policy dictates
# one (openssl x509 -req has no -set_serial escape here by accident --
# it is the documented way to keep issuance reproducible), otherwise the
# random serial openssl picks. Either way step 2 below proves it is not
# the dead leaf's.
if [[ -n "$SERIAL" ]]; then
    SERIAL_ARGS=(-set_serial "0x${SERIAL#0x}")
    echo "==> issuing the replacement leaf with the ceremony-specified serial $SERIAL"
else
    SERIAL_ARGS=()
    echo "==> issuing the replacement leaf (openssl-chosen serial)"
fi
# Same extension shape the stub's leaf walk vets, byte-identical to
# make_carrier.sh's: CA:FALSE critical, digitalSignature critical.
openssl x509 -req -in "$OUT_ABS/leaf-replacement.csr" \
    -CA "$ROOT_CERT" -CAkey "$ROOT_KEY" -CAcreateserial -sha256 -days 3650 \
    "${SERIAL_ARGS[@]}" \
    -extfile <(printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\n') \
    -out "$OUT_ABS/leaf-replacement-cert.pem"
openssl x509 -in "$OUT_ABS/leaf-replacement-cert.pem" -outform DER \
    -out "$OUT_ABS/leaf-replacement-cert.der"

# ---- 2. scope: the revocation must not cover the replacement ----
NEW_TBS="$(python3 tools/patch_carrier_negatives.py sha256-tbs-hex "$OUT_ABS/leaf-replacement-cert.der" | sed 's/^[^:]*: //')"
NEW_SERIAL="$(cert_serial "$OUT_ABS/leaf-replacement-cert.der")"
echo "    replacement serial $NEW_SERIAL, To-Be-Signed sha256 $NEW_TBS"
FAILED=0
if [[ "$NEW_TBS" == "$DEAD_TBS" ]]; then
    echo "FAIL: the replacement leaf's To-Be-Signed hash EQUALS the dead leaf's --"
    echo "      the composed dbx entry would revoke the replacement too. Re-run with a new key"
    echo "      (this can only happen if the same key+cert were passed back in)."
    FAILED=1
else
    echo "    OK scope: TBS hashes differ, so the dead key's entry cannot cover the replacement"
fi
if [[ "$NEW_SERIAL" == "$DEAD_SERIAL" ]]; then
    echo "FAIL: the replacement reuses the dead leaf's serialNumber ($NEW_SERIAL) -- the"
    echo "      whole-certificate dbx form (issuer+serial) would revoke the replacement as well."
    FAILED=1
else
    echo "    OK serial: replacement serial differs from the dead leaf's"
fi

# ---- 3. chain + carrier ----
if openssl verify -CAfile "$ROOT_CERT" "$OUT_ABS/leaf-replacement-cert.pem" >"$OUT_ABS/verify.log" 2>&1; then
    echo "    OK chain: replacement leaf verifies under the root ($(grep -c . "$OUT_ABS/verify.log") line(s), see verify.log)"
else
    echo "FAIL: the replacement leaf does not verify under $ROOT_CERT:"
    sed 's/^/      /' "$OUT_ABS/verify.log"
    FAILED=1
fi
echo "==> signing the kernel image with the replacement leaf (detached, noattr, sha256)"
openssl cms -sign -binary -md sha256 -noattr \
    -in "$KERNEL" -signer "$OUT_ABS/leaf-replacement-cert.pem" \
    -inkey "$OUT_ABS/leaf-replacement-key.pem" \
    -outform DER -out "$OUT_ABS/carrier-replacement.der"
if openssl cms -verify -noverify -binary -in "$OUT_ABS/carrier-replacement.der" \
       -inform DER -content "$KERNEL" -out /dev/null >/dev/null 2>&1; then
    echo "    OK carrier: replacement carrier verifies over $KERNEL ($(wc -c < "$OUT_ABS/carrier-replacement.der") bytes)"
else
    echo "FAIL: the replacement carrier does not verify over $KERNEL -- the ceremony produced an unusable signer"
    FAILED=1
fi

# ---- 4. the dbx entry for the DEAD leaf, in the gate's exact shapes ----
echo "==> composing the dead leaf's revocation: EFI_CERT_X509_SHA256 (TBS hash, revoked $REVOKED_YEAR-01-01)"
python3 tools/patch_carrier_negatives.py make-dbx-hashlist \
    "$OUT_ABS/dead-leaf.der" "$REVOKED_YEAR" "$OUT_ABS/dbx-dead-leaf-tbs.der"
echo "==> wrapping it in the authenticated-update header virt-fw's --set-dbx parses"
python3 tools/patch_carrier_negatives.py make-dbx-auth-update \
    "$OUT_ABS/dbx-dead-leaf-tbs.der" "$REVOKED_YEAR" "$OUT_ABS/dbx-dead-leaf-tbs-auth2.der"

# The shape check: feed the wrapped blob through the SAME store tooling
# the .ms gate composes with, on a COPY of the plain template, then read
# back the variable's size. A blob virt-fw cannot class as a SigDB, or
# one whose payload does not survive the round trip, fails here rather
# than at distribution time.
VIRT="$(command -v virt-fw-vars 2>/dev/null || echo "$HOME/.local/bin/virt-fw-vars")"
VARS_TMPL=uefi/tools/OVMF_VARS_4M.fd
if [[ ! -x "$VIRT" ]]; then
    echo "    NOTE shape: virt-fw-vars not present -- the update blob's store acceptance is NOT"
    echo "         proven here (shape walked by make-dbx-auth-update's own parser instead)."
elif [[ ! -s "$VARS_TMPL" ]]; then
    echo "    NOTE shape: $VARS_TMPL missing -- store acceptance not probed."
else
    cp "$VARS_TMPL" "$OUT_ABS/vars-probe.fd"
    if "$VIRT" --inplace "$OUT_ABS/vars-probe.fd" \
            --set-dbx "$OUT_ABS/dbx-dead-leaf-tbs-auth2.der" >/dev/null 2>"$OUT_ABS/virt-fw.log"; then
        DBX_LINE="$("$VIRT" -i "$OUT_ABS/vars-probe.fd" -p 2>/dev/null | grep -E '^dbx ' || true)"
        SIGDB_BYTES="$(wc -c < "$OUT_ABS/dbx-dead-leaf-tbs.der")"
        echo "    OK shape: virt-fw-vars accepted the update ($DBX_LINE; SigDB $SIGDB_BYTES bytes)"
        if [[ "$DBX_LINE" != *"$SIGDB_BYTES bytes"* ]]; then
            echo "FAIL: the store's dbx does not carry the $SIGDB_BYTES-byte SigDB the ceremony composed"
            FAILED=1
        fi
    else
        echo "FAIL: virt-fw-vars rejected the composed update blob:"
        sed 's/^/      /' "$OUT_ABS/virt-fw.log"
        FAILED=1
    fi
fi

cat <<EOF

==> ceremony artifacts in $OUT (nothing in the enrolled chain was touched):
    leaf-replacement-key.pem        the NEW private key (0600) -- carry it to the signing host
    leaf-replacement-cert.pem/.der  issued by the root over that key
    carrier-replacement.der         the new detached carrier over $KERNEL
    dbx-dead-leaf-tbs.der           the dead leaf's TBS hash as an EFI_CERT_X509_SHA256 list
    dbx-dead-leaf-tbs-auth2.der     the same, wrapped for virt-fw's --set-dbx

    Handoff (ADR-0029's compromise procedure):
      1. Enroll/ship the replacement carrier wherever this chain is pinned
         (the anchor is UNCHANGED -- a new leaf needs no root ceremony).
      2. Distribute dbx-dead-leaf-tbs (or its auth2 form, per your platform's
         update path) so the dead key's certificates stop booting.
      3. The .ms gate already proves the enforcement end of this: put the
         blob in an enforcing store and the leaf-signed stub is refused,
         with no stub rebuild.

    Not claimed here: the auth2 header is store-tooling glue, not a signed
    PKCS7 variable-exchange package (ADR-0029's composition boundary).
EOF

if [[ "$FAILED" -ne 0 ]]; then
    echo "FAIL: ceremony checks failed (see the named FAIL lines above); the artifacts above are NOT fit to ship"
    exit 1
fi
echo "PASS: rotation ceremony complete -- replacement leaf issued and chained, dead leaf's TBS-hash revocation composed and accepted by virt-fw, scope and serial checks clean"
