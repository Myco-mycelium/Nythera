#!/usr/bin/env bash
#
# make_carrier.sh -- the ADR-0014 chain's AUTHENTICATED half, signer
# side, with the one-hop RFC 5280 chain the stub's walk enforces:
#
#   ROOT  (self-signed anchor, CA:TRUE critical, keyCertSign critical)
#     -- issues once on first build, stable for the machine -->
#   LEAF  (key + cert SIGNED BY THE ROOT, CA:FALSE, digitalSignature
#          critical)
#     -- signs the kernel image into the carrier -->
#   CARRIER (detached, attrless PKCS#7 SignedData embedding the LEAF
#          cert; the ROOT cert rides in trusted-root.der inside the
#          stub)
#
# The stub's chain walk (uefi/src/trust.rs) verifies the carrier's
# embedded LEAF cert under the embedded ROOT's key, so a NEW leaf (or
# leaf key) validates after a stub rebuild with no anchor change --
# the anchor pins the issuer, not every signer.
#
# Outputs, all regenerated every run except the three key/cert files
# (root key+cert, leaf key; the leaf CERT is dependent state, repaired
# like the root's):
#
#     uefi/src/trusted-root.der  the ROOT anchor certificate as DER
#     uefi/src/carrier.der       openssl cms -sign -binary -noattr
#                                -outform DER over the kernel image,
#                                signed by the LEAF
#     uefi/src/carrier-neg-*.der FIVE negative carriers, one per
#                                refusal shape the stub must stay
#                                able to produce (below), each one
#                                main.rs must REFUSE at boot and
#                                tools/verify_carrier.sh must refuse
#                                on the host
#
# The negatives (names match main.rs's expected-refusal markers):
#   carrier-neg-digest.der    signed over a one-byte-different copy of
#                             the kernel image
#   carrier-neg-anchor.der    signed by a stray SELF-SIGNED key+cert
#                             pair generated here (unanchored: no
#                             chain from it to the root)
#   carrier-neg-sid.der       the GOOD carrier with ONE byte of the
#                             SignerInfo's issuerAndSerial serialNumber
#                             DER-precisely flipped
#                             (tools/patch_carrier_negatives.py)
#   carrier-neg-attrs.der     same leaf, but signed WITH signingTime
#                             attrs (no -noattr)
#   carrier-neg-detached.der  same leaf, but attached (-nodetach: the
#                             carrier embeds its own eContent)
#
# Usage: ./tools/make_carrier.sh <kernel-elf> <carrier.der-out> <trusted-root.der-out>
#
# The carrier itself stays deterministic for signer key + content
# (PKCS#1 v1.5 carries no randomness and -noattr removes signingTime),
# so include_bytes!-ed rebuilds track the kernel image exactly. A
# fresh checkout has no keys: the FIRST build generates root + leaf.

set -euo pipefail
# Anchor at the PACKAGED KERNEL ROOT from any caller (the same pattern
# tools/qemu_uefi_boot_test.sh uses): the artifact paths passed in are
# always kernel-root-relative (target/kernel.elf, uefi/src/carrier.der).
cd "$(dirname "$0")/.."

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <kernel-elf> <carrier.der-out> <trusted-root.der-out>"
    exit 1
fi
KERNEL="$1"; CARRIER_OUT="$2"; ROOT_OUT="$3"

if ! command -v openssl >/dev/null 2>&1; then
    echo "make_carrier.sh: no openssl on PATH (the signer side is openssl, the stub side is hand-rolled; ADR-0014 needs the real algorithm on at least one side)"
    exit 1
fi
if [[ ! -s "$KERNEL" ]]; then
    echo "make_carrier.sh: kernel image missing or empty: $KERNEL"
    exit 1
fi

mkdir -p uefi/tools/trust
ROOT_KEY=uefi/tools/trust/carrier-dev-key.pem
ROOT_CRT=uefi/tools/trust/carrier-dev-cert.pem
LEAF_KEY=uefi/tools/trust/carrier-dev-leaf-key.pem
LEAF_CSR=uefi/tools/trust/carrier-dev-leaf.csr
LEAF_CRT=uefi/tools/trust/carrier-dev-leaf-cert.pem
STRAY_KEY=uefi/tools/trust/carrier-neg-stray-key.pem
STRAY_CRT=uefi/tools/trust/carrier-neg-stray-cert.pem

mod_of_key() { openssl rsa -in "$1" -noout -modulus 2>/dev/null; }
mod_of_cert() { openssl x509 -in "$1" -noout -modulus 2>/dev/null; }

# ---- the ROOT anchor: generated once, stable for the machine ----
if [[ ! -s "$ROOT_KEY" ]]; then
    echo "==> generating the dev RSA-2048 ROOT key (uefi/tools/trust/carrier-dev-key.pem)"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -pkeyopt rsa_keygen_pubexp:65537 -out "$ROOT_KEY"
fi
# Dependent state, repaired whenever missing or key-mismatched
# (half-cleared tools dir).
if [[ ! -s "$ROOT_CRT" ]] || [[ "$(mod_of_cert "$ROOT_CRT")" != "$(mod_of_key "$ROOT_KEY")" ]]; then
    echo "==> generating the self-signed ROOT anchor certificate (uefi/tools/trust/carrier-dev-cert.pem)"
    openssl req -new -x509 -key "$ROOT_KEY" -sha256 -days 3650 \
        -subj "/CN=Nyrqis dev trust anchor/OU=Nyrqis bringup (not a real CA)" \
        -addext basicConstraints=critical,CA:TRUE \
        -addext keyUsage=critical,keyCertSign,cRLSign \
        -out "$ROOT_CRT"
fi

# ---- the LEAF signer: key + CSR once, cert repaired/issued by the ROOT ----
if [[ ! -s "$LEAF_KEY" ]]; then
    echo "==> generating the dev RSA-2048 LEAF key (uefi/tools/trust/carrier-dev-leaf-key.pem)"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -pkeyopt rsa_keygen_pubexp:65537 -out "$LEAF_KEY"
fi
if [[ ! -s "$LEAF_CSR" ]]; then
    echo "==> generating the LEAF CSR"
    openssl req -new -key "$LEAF_KEY" -sha256 \
        -subj "/CN=Nyrqis dev carrier leaf/OU=Nyrqis bringup" -out "$LEAF_CSR"
fi
# The leaf CERT is dependent state: missing or belonging to a
# different key than the one on disk -> re-issue FROM the root. Its
# extensions are the exact shape the stub's leaf walk vets
# (basicConstraints present-with-CA:FALSE, keyUsage critical
# digitalSignature); no pathLen, no CA contradiction.
if [[ ! -s "$LEAF_CRT" ]] || [[ "$(mod_of_cert "$LEAF_CRT")" != "$(mod_of_key "$LEAF_KEY")" ]]; then
    echo "==> issuing the LEAF certificate signed by the ROOT"
    openssl x509 -req -in "$LEAF_CSR" -CA "$ROOT_CRT" -CAkey "$ROOT_KEY" \
        -CAcreateserial -sha256 -days 3650 \
        -extfile <(printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nsubjectKeyIdentifier=hash\n') \
        -out "$LEAF_CRT"
fi

openssl x509 -in "$ROOT_CRT" -outform DER -out "$ROOT_OUT"

# ---- the GOOD carrier: leaf-signed, detached, attrless ----
echo "==> signing the GOOD carrier with the LEAF (detached, noattr, sha256) over $KERNEL"
openssl cms -sign -binary -md sha256 -noattr \
    -in "$KERNEL" -signer "$LEAF_CRT" -inkey "$LEAF_KEY" \
    -outform DER -out "$CARRIER_OUT"
openssl cms -verify -noverify -binary \
    -in "$CARRIER_OUT" -inform DER -content "$KERNEL" -out /dev/null >/dev/null

# ---- the negative carriers ----
# neg-anchor: a stray pair whose cert is a legal self-signed one --
# openssl signs it fine, the stub must refuse it for the CHAIN (no
# path from the stray to the embedded root).
if [[ ! -s "$STRAY_KEY" ]]; then
    echo "==> generating the NEGATIVE-ONLY stray key (uefi/tools/trust/carrier-neg-stray-key.pem)"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -pkeyopt rsa_keygen_pubexp:65537 -out "$STRAY_KEY"
fi
if [[ ! -s "$STRAY_CRT" ]] || [[ "$(mod_of_cert "$STRAY_CRT")" != "$(mod_of_key "$STRAY_KEY")" ]]; then
    echo "==> generating the stray NEGATIVE-ONLY self-signed certificate"
    openssl req -new -x509 -key "$STRAY_KEY" -sha256 -days 3650 \
        -subj "/CN=Nyrqis dev NEGATIVE-ONLY stray anchor (never trust)/OU=Nyrqis bringup" \
        -addext basicConstraints=critical,CA:TRUE \
        -addext keyUsage=critical,keyCertSign,cRLSign \
        -out "$STRAY_CRT"
fi
echo "==> signing the neg-anchor negative carrier with the STRAY pair"
openssl cms -sign -binary -md sha256 -noattr \
    -in "$KERNEL" -signer "$STRAY_CRT" -inkey "$STRAY_KEY" \
    -outform DER -out uefi/src/carrier-neg-anchor.der

# neg-digest: same leaf, but the content differs from the kernel by
# one bit -- the signature is honestly made over the WRONG bytes.
python3 tools/patch_carrier_negatives.py flip-content "$KERNEL" uefi/tools/trust/carrier-neg-flipcontent.bin
echo "==> signing the neg-digest negative carrier over the flipped content"
openssl cms -sign -binary -md sha256 -noattr \
    -in uefi/tools/trust/carrier-neg-flipcontent.bin \
    -signer "$LEAF_CRT" -inkey "$LEAF_KEY" \
    -outform DER -out uefi/src/carrier-neg-digest.der

# neg-sid: the GOOD carrier with one serialNumber byte flipped
# (byte surgery on the real carrier, DER-structurally located).
python3 tools/patch_carrier_negatives.py mutate-sid-serial "$CARRIER_OUT" uefi/src/carrier-neg-sid.der

# neg-attrs: signed WITH signingTime attrs -- a carrier shape the
# stub refuses before it ever reaches a key.
echo "==> signing the neg-attrs negative carrier (WITH signingTime attrs)"
openssl cms -sign -binary -md sha256 \
    -in "$KERNEL" -signer "$LEAF_CRT" -inkey "$LEAF_KEY" \
    -outform DER -out uefi/src/carrier-neg-attrs.der

# neg-detached: attached carrier -- the payload rides INSIDE the
# carrier's eContent, exactly the shape the stub refuses (the stub
# verifies its OWN copy of the bytes).
echo "==> signing the neg-detached negative carrier (attached, -nodetach)"
openssl cms -sign -binary -md sha256 -noattr -nodetach \
    -in "$KERNEL" -signer "$LEAF_CRT" -inkey "$LEAF_KEY" \
    -outform DER -out uefi/src/carrier-neg-detached.der

# ---- the VALIDITY-WINDOW negatives (the ADR-0014 expiry gate's
#      every-boot controls): two certificates SIGNED BY THE REAL ROOT
#      over the REAL leaf key, whose windows lie entirely outside any
#      plausible firmware clock. Their chains verify genuinely, so the
#      WINDOW gate is the only thing between each and a load:
#      neg-window-expired must refuse at its notAfter (1990..2020,
#      UTCTime), neg-window-future at its notBefore (2400..2500,
#      GeneralizedTime -- RFC 5280's beyond-2049 encoding). Each
#      certificate is issued over a FRESH, WIPED throwaway CA
#      database with explicit -startdate/-enddate, so the bytes are
#      deterministic run to run (today's date rides along in NOTHING
#      the stub parses; openssl cms itself adds no randomness under
#      -noattr, and the signature is bare PKCS#1 v1.5).
NEGWIN_DB=uefi/tools/trust/negwin-db
NEGWIN_CNF=uefi/tools/trust/negwin-ca.cnf
NEGWIN_EXT=uefi/tools/trust/negwin-ext.cnf
mkdir -p "$NEGWIN_DB"
cat > "$NEGWIN_CNF" <<EOF
[ca]
default_ca = CA_default
[CA_default]
certificate = $(pwd)/$ROOT_CRT
private_key = $(pwd)/$ROOT_KEY
new_certs_dir = $(pwd)/$NEGWIN_DB
database = $(pwd)/$NEGWIN_DB/index
serial = $(pwd)/$NEGWIN_DB/serial
default_md = sha256
default_days = 3650
policy = policy_any
unique_subject = no
copy_extensions = none
[policy_any]
commonName = supplied
EOF
cat > "$NEGWIN_EXT" <<'EOF'
[ext_leaf]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
subjectKeyIdentifier = hash
EOF
issue_window_leaf() { # <out.crt> <startdate> <enddate>
    rm -f "$NEGWIN_DB/index"* "$NEGWIN_DB/serial"* "$NEGWIN_DB"/*.pem
    : > "$NEGWIN_DB/index"
    echo 1000 > "$NEGWIN_DB/serial"
    openssl ca -batch -config "$NEGWIN_CNF" -in "$LEAF_CSR" \
        -extfile "$NEGWIN_EXT" -extensions ext_leaf \
        -startdate "$2" -enddate "$3" -notext -out "$1"
}
echo "==> issuing the EXPIRED window leaf (1990..2020, real root, real leaf key)"
issue_window_leaf uefi/tools/trust/carrier-neg-window-expired.crt \
    19900101000000Z 20200101000000Z
echo "==> issuing the NOT-YET-VALID window leaf (2400..2500)"
issue_window_leaf uefi/tools/trust/carrier-neg-window-future.crt \
    24000101000000Z 25000101000000Z
echo "==> signing the neg-window-expired negative carrier over the real kernel"
openssl cms -sign -binary -md sha256 -noattr \
    -in "$KERNEL" -signer uefi/tools/trust/carrier-neg-window-expired.crt -inkey "$LEAF_KEY" \
    -outform DER -out uefi/src/carrier-neg-window-expired.der
echo "==> signing the neg-window-future negative carrier over the real kernel"
openssl cms -sign -binary -md sha256 -noattr \
    -in "$KERNEL" -signer uefi/tools/trust/carrier-neg-window-future.crt -inkey "$LEAF_KEY" \
    -outform DER -out uefi/src/carrier-neg-window-future.der

# ---- the dbx revocation gate's every-boot controls: GENERATED EFI
#      signature-list blobs (the shape the firmware's own dbx
#      variable carries), each revoking the chain the walk itself
#      verifies -- the signer by BYTE, the signer by issuer+serial
#      (a re-key: a certificate RE-ISSUED by the same root with the
#      SAME serial over a DIFFERENT key, the exact attack the pair
#      rule exists for), and the anchor by BYTE.
# The re-key's serial must BYTE-EQUAL the real signer certificate's,
# so the real serial is read straight out of the good carrier's
# SignerIdentifier (tools/patch_carrier_negatives.py).
echo "==> building the dbx negative blobs (signer-byte / re-key pair / anchor-byte)"
LEAF_SERIAL_HEX=$(python3 tools/patch_carrier_negatives.py print-sid-serial-hex "$CARRIER_OUT")
rm -f "$NEGWIN_DB/index"* "$NEGWIN_DB/serial"* "$NEGWIN_DB"/*.pem
: > "$NEGWIN_DB/index"
# The serial file (hex, one line) is how a re-key certificate is
# forced onto the signer's serial: `openssl ca` reads its value for
# the issuance in progress.
echo "$LEAF_SERIAL_HEX" > "$NEGWIN_DB/serial"
openssl ca -batch -config "$NEGWIN_CNF" -in "$LEAF_CSR" \
    -extfile "$NEGWIN_EXT" -extensions ext_leaf -notext \
    -out uefi/tools/trust/carrier-neg-rekey.crt
openssl x509 -in uefi/tools/trust/carrier-neg-rekey.crt -outform DER \
    -out uefi/tools/trust/carrier-neg-rekey.der
python3 tools/patch_carrier_negatives.py make-dbx-siglist \
    uefi/tools/trust/carrier-neg-rekey.der uefi/src/dbx-neg-signer-pair.der
openssl x509 -in "$LEAF_CRT" -outform DER -out uefi/tools/trust/carrier-leafcert.der
python3 tools/patch_carrier_negatives.py make-dbx-siglist \
    uefi/tools/trust/carrier-leafcert.der uefi/src/dbx-neg-signer-byte.der
python3 tools/patch_carrier_negatives.py make-dbx-siglist \
    "$ROOT_OUT" uefi/src/dbx-neg-anchor-byte.der

echo "==> make_carrier.sh: GOOD carrier self-check via openssl cms -verify PASS ($(stat -c%s "$CARRIER_OUT") carrier bytes, $(stat -c%s "$ROOT_OUT") root bytes); negatives written (anchor/digest/sid/attrs/detached/window-expired/window-future) + dbx blobs (signer-byte/re-key-pair/anchor-byte) -- tools/verify_carrier.sh proves each refusing"
