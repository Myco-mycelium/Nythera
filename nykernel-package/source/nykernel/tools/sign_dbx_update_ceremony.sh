#!/usr/bin/env bash
#
# sign_dbx_update_ceremony.sh -- the SIGNING AND DISTRIBUTION ceremony for
# a dbx revocation update: the half ADR-0029 names as "the platform's
# authenticated-update machinery" and left uncovered after the .ms gate
# proved firmware ACCEPTANCE of a package.
#
# The gap this closes is not "can a package be built" -- that is
# tools/make_dbx_update_package.sh, tested against a real firmware. It is
# that nothing stood between an operator and shipping a revocation that
# breaks the fleet, or a keystore with shared permissions. This tool is
# that standing check, and it REFUSES rather than warns:
#
#   1. CUSTODY      the KEK private key must have no group/world bits.
#                   Custody is the first control, not the last (the same
#                   rule rotate_leaf_ceremony.sh applies to the root).
#   2. TARGET       the certificate named for revocation must NOT be the
#                   trust anchor: revoking the anchor refuses every
#                   carrier, including the ones being rotated TO. A
#                   ceremony that can brick the fleet silently is not a
#                   ceremony.
#   3. SIGNER       the signing certificate must BE the enrolled KEK,
#                   compared byte for byte (DER sha256) against an
#                   explicit --enrolled-kek. A package signed by a key the
#                   store does not trust is refused by firmware at
#                   SetVariable time, and the operator finds out on the
#                   affected machine.
#   4. STAMP        when --previous-stamp is given, the new stamp must be
#                   strictly newer. Firmware rejects a non-increasing
#                   time-based stamp, so a stale or replayed package is
#                   dead on arrival.
#   5. SCOPE        the composed list must carry exactly the target's
#                   To-Be-Signed hash, once, as this ceremony's stamp.
#                   ADR-0029's whole reason for the hash form is that it
#                   revokes the dead key WITHOUT covering a re-issued
#                   leaf; a list that drifted would break that promise.
#   6. BINDING      the package's PKCS#7 signature must verify against the
#                   KEK over the signed serialization, and the package's
#                   signer certificate must be the KEK certificate.
#
# What it hands off: the signed package plus an update-manifest.txt
# naming the package's sha256, the target's identity and To-Be-Signed
# hash, the stamp, the signer, and the exact delivery note. Everything
# lands in --out for an operator to carry; this tool writes nothing into
# the enrolled chain and does not deliver anything.
#
# What it does NOT claim (ADR-0029's boundary): delivery itself -- which
# channel an OS or OEM update service uses, how an operator schedules the
# revocation, and how a fleet's rollout is verified -- is platform
# process. This tool produces and validates the artifact that process
# distributes.
#
# Usage:
#   ./tools/sign_dbx_update_ceremony.sh --revoke <cert.pem|der>
#       [--kek-cert <pem>] [--kek-key <pem>]
#       [--enrolled-kek <pem>]      # required in production: the KEK the
#                                   # target store actually carries
#       [--anchor <pem>]
#       [--stamp-year <YYYY>] [--previous-stamp <YYYY-MM-DD>]
#       [--out <dir>]
#
# Defaults: the shipped dev chain (uefi/tools/trust/carrier-dev-*.pem)
# as both signer and enrolled KEK, stamp year 2027 (newer than any dev
# store's dummy dbx entry), output uefi/dist/dbx-update-ceremony.
# --enrolled-kek defaults to --kek-cert, which is the right default for
# the dev chain and the WRONG one for production: an operator must name
# the KEK the target store carries, or the check is a tautology. The
# manifest records which was used.

set -euo pipefail
cd "$(dirname "$0")/.."

REVOKE=""
KEK_CERT=uefi/tools/trust/carrier-dev-cert.pem
KEK_KEY=uefi/tools/trust/carrier-dev-key.pem
ENROLLED_KEK=""
ANCHOR=uefi/tools/trust/carrier-dev-cert.pem
STAMP_YEAR=2027
PREVIOUS_STAMP=""
OUT=uefi/dist/dbx-update-ceremony

while [[ $# -gt 0 ]]; do
    case "$1" in
        --revoke) REVOKE="$2"; shift 2 ;;
        --kek-cert) KEK_CERT="$2"; shift 2 ;;
        --kek-key) KEK_KEY="$2"; shift 2 ;;
        --enrolled-kek) ENROLLED_KEK="$2"; shift 2 ;;
        --anchor) ANCHOR="$2"; shift 2 ;;
        --stamp-year) STAMP_YEAR="$2"; shift 2 ;;
        --previous-stamp) PREVIOUS_STAMP="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "FAIL: unknown argument: $1"; exit 2 ;;
    esac
done

if [[ -z "$REVOKE" ]]; then
    echo "FAIL: --revoke <cert> is required (the certificate to revoke)"
    exit 2
fi
[[ -n "$ENROLLED_KEK" ]] || ENROLLED_KEK="$KEK_CERT"

# --- 1. the operator's inputs exist, and the key's custody is real -----
for f in "$REVOKE" "$KEK_CERT" "$KEK_KEY" "$ENROLLED_KEK" "$ANCHOR"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: ceremony input missing or empty: $f"
        exit 1
    fi
done

KEY_MODE="$(stat -c '%a' "$KEK_KEY")"
if (( 8#$KEY_MODE & 8#077 )); then
    echo "FAIL: the KEK private key has group/world bits set (mode $KEY_MODE): $KEK_KEY"
    echo "      custody is the ceremony's first control -- a revocation signer readable"
    echo "      by another account can revoke anything the store trusts"
    exit 1
fi

[[ "$STAMP_YEAR" =~ ^[0-9]{4}$ ]] || { echo "FAIL: --stamp-year must be YYYY (got $STAMP_YEAR)"; exit 2; }
STAMP="$STAMP_YEAR-01-01"

# --- 2. the target is not the anchor -----------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

to_der() { # $1 in (pem|der), $2 out.der
    if openssl x509 -in "$1" -inform PEM -outform DER -out "$2" 2>/dev/null; then
        return 0
    fi
    openssl x509 -in "$1" -inform DER -outform DER -out "$2" 2>/dev/null \
        || { echo "FAIL: not a readable X.509 certificate: $1"; exit 1; }
}

to_der "$REVOKE" "$WORK/target.der"
to_der "$ANCHOR" "$WORK/anchor.der"
to_der "$KEK_CERT" "$WORK/kek.der"
to_der "$ENROLLED_KEK" "$WORK/enrolled.der"

der_sha() { openssl x509 -in "$1" -inform DER -outform DER | sha256sum | cut -d' ' -f1; }
TARGET_SHA="$(der_sha "$WORK/target.der")"
ANCHOR_SHA="$(der_sha "$WORK/anchor.der")"
if [[ "$TARGET_SHA" == "$ANCHOR_SHA" ]]; then
    echo "FAIL: the revocation target IS the trust anchor ($REVOKE)"
    echo "      revoking the anchor refuses every carrier, including the replacements"
    echo "      a rotation is about to issue -- revoke the compromised key, not the root"
    exit 1
fi

# --- 3. the signer is the enrolled KEK --------------------------------
KEK_SHA="$(der_sha "$WORK/kek.der")"
ENROLLED_SHA="$(der_sha "$WORK/enrolled.der")"
if [[ "$KEK_SHA" != "$ENROLLED_SHA" ]]; then
    echo "FAIL: the signing certificate is not the enrolled KEK"
    echo "      signer   ($KEK_CERT): $KEK_SHA"
    echo "      enrolled ($ENROLLED_KEK): $ENROLLED_SHA"
    echo "      firmware would refuse this package at SetVariable time; the ceremony"
    echo "      refuses it first, where the operator can still fix it"
    exit 1
fi

# --- 4. the stamp advances --------------------------------------------
if [[ -n "$PREVIOUS_STAMP" ]]; then
    if [[ ! "$PREVIOUS_STAMP" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        echo "FAIL: --previous-stamp must be YYYY-MM-DD (got $PREVIOUS_STAMP)"
        exit 2
    fi
    if [[ ! "$STAMP" > "$PREVIOUS_STAMP" ]]; then
        echo "FAIL: the requested stamp ($STAMP) is not newer than the previous stamp ($PREVIOUS_STAMP)"
        echo "      firmware rejects a non-increasing time-based stamp, so this package"
        echo "      would be dead on arrival (and a replay would be indistinguishable)"
        exit 1
    fi
fi

# --- compose the revocation list: exactly the target's TBS hash -------
mkdir -p "$OUT"
REVOKE_DER="$WORK/target.der"
LIST="$OUT/revocation-list.der"
python3 ./tools/patch_carrier_negatives.py make-dbx-hashlist "$REVOKE_DER" "$STAMP_YEAR" "$LIST"
TARGET_TBS_HEX="$(python3 ./tools/patch_carrier_negatives.py sha256-tbs-hex "$REVOKE_DER" | sed 's/^sha256_tbs_hex: //')"

# --- 5. scope: the list carries that hash, once, at this stamp --------
python3 - "$LIST" "$TARGET_TBS_HEX" "$STAMP_YEAR" <<'PY'
import sys

path, want_hex, year = sys.argv[1], sys.argv[2].lower(), int(sys.argv[3])
blob = open(path, "rb").read()

# The one list this ceremony is allowed to compose, pinned from the SPEC
# rather than from our own generator's constants: EFI_CERT_X509_SHA256
# (UEFI 2.10 32.4.1), SignatureHeaderSize 0, 64-byte entries
# (16-byte owner GUID + 32-byte TBS hash + 16-byte EFI_TIME).
import uuid
want_type = uuid.UUID("3bd2a492-96c0-4079-b420-fcf98ef103ed").bytes_le
if blob[:16] != want_type:
    raise SystemExit(
        f"FAIL: the composed list's type is {blob[:16].hex()}, not EFI_CERT_X509_SHA256")
size = int.from_bytes(blob[16:20], "little")
hdr = int.from_bytes(blob[20:24], "little")
entry = int.from_bytes(blob[24:28], "little")
if size != len(blob) or hdr != 0 or entry != 64:
    raise SystemExit(
        f"FAIL: the composed list is not the spec's shape (size {size} of "
        f"{len(blob)}, header {hdr}, entry {entry}; want 0/64)")
if (size - 28) % entry:
    raise SystemExit("FAIL: the composed list's entries do not tile it")
count = (size - 28) // entry
if count != 1:
    raise SystemExit(f"FAIL: the composed list carries {count} entries, not exactly 1")

body = blob[28:28 + entry]
owner = body[:16]
tbs = body[16:48]
stamp = body[48:64]
if owner != bytes(16):
    raise SystemExit(
        f"FAIL: the entry's SignatureOwner is {owner.hex()}, not all-zero -- this "
        f"ceremony composes owner-less entries and ADR-0029 reads them that way")
if tbs.hex() != want_hex:
    raise SystemExit(
        f"FAIL: the composed list does not carry the target's To-Be-Signed hash:\n"
        f"      list has {tbs.hex()}\n"
        f"      target is {want_hex}")
if int.from_bytes(stamp[:2], "little") != year:
    raise SystemExit(
        f"FAIL: the entry's revocation EFI_TIME is not the ceremony's stamp year {year}")
print(f"ceremony: scope OK -- 1 EFI_CERT_X509_SHA256 entry, owner-less, "
      f"tbs {tbs.hex()[:16]}.., revoked-at {year}-01-01, {size} bytes")
PY

# --- 6. build the package and prove its binding -----------------------
PACKAGE="$OUT/dbx-update.auth2"
./tools/make_dbx_update_package.sh "$LIST" "$KEK_CERT" "$KEK_KEY" "$STAMP_YEAR" "$PACKAGE" "$WORK"

openssl cms -verify -binary -inform DER -in "$WORK/update.p7.der" \
    -content "$WORK/content.bin" -certfile "$KEK_CERT" -noverify \
    -out /dev/null 2>"$WORK/verify.log" \
    || { cat "$WORK/verify.log"; echo "FAIL: the package's signature does not verify against the KEK"; exit 1; }

openssl pkcs7 -inform DER -in "$WORK/update.p7.der" -print_certs -out "$WORK/signer.pem" 2>/dev/null
SIGNER_SHA="$(openssl x509 -in "$WORK/signer.pem" -outform DER | sha256sum | cut -d' ' -f1)"
if [[ "$SIGNER_SHA" != "$KEK_SHA" ]]; then
    echo "FAIL: the package's signer certificate is not the KEK certificate"
    echo "      package signer: $SIGNER_SHA"
    echo "      KEK:            $KEK_SHA"
    exit 1
fi

# --- the hand-off artifact --------------------------------------------
PACKAGE_SHA="$(sha256sum "$PACKAGE" | cut -d' ' -f1)"
REVOKE_SUBJECT="$(openssl x509 -in "$WORK/target.der" -inform DER -noout -subject | sed 's/^subject=//')"
REVOKE_SERIAL="$(openssl x509 -in "$WORK/target.der" -inform DER -noout -serial | sed 's/^serial=//')"
KEK_SUBJECT="$(openssl x509 -in "$WORK/kek.der" -inform DER -noout -subject | sed 's/^subject=//')"
cat > "$OUT/update-manifest.txt" <<EOF
dbx revocation update -- composed by tools/sign_dbx_update_ceremony.sh
generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

package          : $(basename "$PACKAGE")
package sha256   : $PACKAGE_SHA
package bytes    : $(wc -c < "$PACKAGE")
variable         : dbx (EFI_IMAGE_SECURITY_DATABASE_GUID)
attributes       : 0x27 (NV|BS|RT|TIME_BASED_AUTHENTICATED)
stamp            : $STAMP (deterministic: this ceremony stamps the year, not the clock)
signer           : $KEK_SUBJECT
signer sha256    : $KEK_SHA
enrolled KEK used: $ENROLLED_KEK $( [[ "$ENROLLED_KEK" == "$KEK_CERT" ]] && echo '(same as --kek-cert: the dev default)' )

revoked cert     : $REVOKE
revoked subject  : $REVOKE_SUBJECT
revoked serial   : $REVOKE_SERIAL
revoked tbs sha256: $TARGET_TBS_HEX

list             : $(basename "$LIST") ($(wc -c < "$LIST") bytes, 1 EFI_CERT_X509_SHA256 entry)
distribute       : deliver $(basename "$PACKAGE") (or the list) through the platform's
                   authenticated update path; firmware verifies the PKCS#7 against
                   the enrolled KEK at SetVariable time. This ceremony does not
                   deliver it, and does not schedule the rollout.
verify on target : the .ms boot gate's dbx-update leg is the acceptance proof
                   (tools/qemu_uefi_ms_secboot_test.sh); the host-side binding
                   proof is tools/dbx_update_gate.sh
EOF

echo
echo "PASS: signing/distribution ceremony complete"
echo "  package        : $PACKAGE ($(wc -c < "$PACKAGE") bytes, sha256 ${PACKAGE_SHA:0:16}..)"
echo "  revoking       : $REVOKE_SUBJECT (serial $REVOKE_SERIAL)"
echo "  signer         : $KEK_SUBJECT (enrolled KEK match verified byte-for-byte)"
echo "  stamp          : $STAMP"
echo "  custody        : key mode $KEY_MODE (no group/world bits)"
echo "  manifest       : $OUT/update-manifest.txt"
echo "  NOT done here  : delivery and rollout scheduling (platform process, ADR-0029)"
