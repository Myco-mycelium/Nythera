#!/usr/bin/env bash
#
# dbx_update_gate.sh -- the UPDATE side of revocation: a KEK-signed
# EFI_VARIABLE_AUTHENTICATION_2 package carrying a dbx signature list.
#
# ADR-0029 splits this surface in two. The boot gates prove the
# ENFORCEMENT side (a real firmware reading a real dbx and refusing a
# revoked chain). Production revocation is performed by the UPDATE side:
# a KEK-signed time-based authenticated variable write, which the
# firmware verifies against its enrolled KEK before storing the list --
# the surface ADR-0029 calls out as "a separate surface, not weakened or
# faked by anything here". This gate covers what this repository can
# honestly reach without a provisioned PK/KEK store and a runtime
# SetVariable exchange inside the stub:
#
#   - it BUILDS a real package: the new EFI_SIGNATURE_LIST, the 16-byte
#     EFI_TIME stamp, and a WIN_CERTIFICATE_UEFI_GUID (dwLength =
#     8 + 16 + PKCS7 length, revision 0x0200, type 0x0EF1,
#     EFI_CERT_TYPE_PKCS7_GUID) whose CertData is a genuine DER PKCS7
#     signature over the new list followed by that same EFI_TIME;
#   - it VERIFIES the binding with openssl: the right KEK, the right
#     list bytes, the right timestamp;
#   - it proves the binding is LOAD-BEARING, not decorative: an altered
#     list byte, an altered header timestamp (the rollback/replay case),
#     or a signature from a non-KEK key each make verification fail.
#   - it checks the list itself against edk2's own SignatureSize table
#     (CheckSignatureListFormat's mSupportSigItem: X.509-SHA256 entries
#     are 48 bytes of SignatureData, i.e. 32-byte hash + EFI_TIME), so a
#     package that verifies cryptographically but carries a malformed
#     list is still a failure.
#
# NOT covered, stated rather than implied: firmware ACCEPTANCE of the
# package at SetVariable time (a real authenticated write against a
# provisioned store). The stored-key enrollment in this repo's gates is
# composed with virt-fw-vars, which bypasses the auth path by design --
# ADR-0029's composition boundary -- and the stub's own db write is the
# SetupMode arm (UEFI 2.10 32.4.1: no signature verification). Driving
# the user-mode arm belongs with the platform keys, not with this gate.
#
# Usage: ./tools/dbx_update_gate.sh

set -euo pipefail
cd "$(dirname "$0")/.."

KEK_CERT=uefi/tools/trust/carrier-dev-cert.pem   # the dev root stands in as KEK
KEK_KEY=uefi/tools/trust/carrier-dev-key.pem
STRANGER_CERT=uefi/tools/trust/carrier-neg-stray-cert.pem
STRANGER_KEY=uefi/tools/trust/carrier-neg-stray-key.pem
PAYLOAD=uefi/src/dbx-neg-signer-hash.der         # X.509-SHA256 revocation list

OUT=target/dbx-update
mkdir -p "$OUT"

for f in "$KEK_CERT" "$KEK_KEY" "$STRANGER_CERT" "$STRANGER_KEY" "$PAYLOAD"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: missing $f (run ./build.sh first: the carrier set generates these)"
        exit 1
    fi
done

TS_YEAR=2026
STAMP="$TS_YEAR-01-01 00:00:00"

echo "==> composing the signed content (new dbx list || auth-header EFI_TIME, stamp $STAMP)"
python3 - "$PAYLOAD" "$TS_YEAR" "$OUT/content.bin" "$OUT/time.bin" "$OUT/list-size" <<'PY'
import sys

payload_path, year, content_path, time_path, size_path = sys.argv[1:6]
payload = open(payload_path, "rb").read()
if not payload:
    raise SystemExit("FAIL: empty dbx payload")
# EFI_TIME, UEFI 2.10 3.2.5: u16 year, u8 month/day/hour/minute/second,
# u8 pad1, u32 nanosecond, i16 timezone (0 = UTC), u8 daylight, u8 pad2.
time_bytes = (
    int(year).to_bytes(2, "little")
    + bytes([1, 1, 0, 0, 0, 0])
    + bytes(4)
    + bytes(2)
    + bytes([0, 0])
)
if len(time_bytes) != 16:
    raise SystemExit("FAIL: EFI_TIME is not 16 bytes")
open(time_path, "wb").write(time_bytes)
open(content_path, "wb").write(payload + time_bytes)
open(size_path, "w").write(str(len(payload)))
print(
    f"patch: signed content = {len(payload)}-byte list || 16-byte EFI_TIME "
    f"= {len(payload) + 16} bytes"
)
PY

echo "==> signing with the KEK key (openssl cms, DER, detached)"
openssl cms -sign -binary -noattr -in "$OUT/content.bin" \
    -signer "$KEK_CERT" -inkey "$KEK_KEY" \
    -outform DER -out "$OUT/update.p7.der" 2>"$OUT/sign.log" \
    || { cat "$OUT/sign.log"; echo "FAIL: openssl cms could not sign the update content"; exit 1; }

echo "==> assembling EFI_VARIABLE_AUTHENTICATION_2 (EFI_TIME + WIN_CERTIFICATE_UEFI_GUID + list)"
python3 - "$OUT/time.bin" "$OUT/update.p7.der" "$PAYLOAD" "$OUT/auth2.bin" <<'PY'
import sys
import uuid

time_path, sig_path, payload_path, out_path = sys.argv[1:5]
stamp = open(time_path, "rb").read()
sig = open(sig_path, "rb").read()
payload = open(payload_path, "rb").read()
if len(stamp) != 16:
    raise SystemExit("FAIL: auth header EFI_TIME is not 16 bytes")
if not sig:
    raise SystemExit("FAIL: empty PKCS7 signature")
# EFI_CERT_TYPE_PKCS7_GUID, canonical 4aafd29d-68df-49ee-8aa9-347d375665a7.
# uuid.bytes_le is exactly the EFI_GUID wire layout (Data1-3 little-endian,
# Data4 verbatim), so the bytes come from the spec string, not a table.
pkcs7_type = uuid.UUID("4aafd29d-68df-49ee-8aa9-347d375665a7").bytes_le
hdr_len = 8 + 16 + len(sig)
blob = bytearray()
blob += stamp
blob += hdr_len.to_bytes(4, "little")        # dwLength
blob += (0x0200).to_bytes(2, "little")       # wRevision
blob += (0x0EF1).to_bytes(2, "little")       # wCertificateType
blob += pkcs7_type
blob += sig
blob += payload
open(out_path, "wb").write(bytes(blob))

# The list must satisfy edk2 CheckSignatureListFormat's own table: one
# EFI_CERT_X509_SHA256 list, SignatureHeaderSize 0, SignatureData 48
# bytes (32-byte To-Be-Signed hash + EFI_TIME), entries tiling exactly.
if payload[:16] != uuid.UUID("3bd2a492-96c0-4079-b420-fcf98ef103ed").bytes_le:
    raise SystemExit("FAIL: the payload's list is not EFI_CERT_X509_SHA256")
size = int.from_bytes(payload[16:20], "little")
hdr = int.from_bytes(payload[20:24], "little")
sig_size = int.from_bytes(payload[24:28], "little")
if size != len(payload):
    raise SystemExit(f"FAIL: list size {size} != payload {len(payload)}")
# edk2's CheckSignatureListFormat compares SignatureSize - sizeof(EFI_GUID)
# against mSupportSigItem's SigDataSize: 48 for EFI_CERT_X509_SHA256, so the
# whole entry (16-byte owner GUID + 32-byte hash + EFI_TIME) is 64 bytes.
if hdr != 0 or sig_size - 16 != 48:
    raise SystemExit(
        f"FAIL: list SignatureHeaderSize/SignatureSize {hdr}/{sig_size} are not "
        f"the spec's 0/{16 + 48} (edk2 mSupportSigItem EFI_CERT_X509_SHA256 = 48)"
    )
print(
    f"patch: package = 16-byte stamp + 8+16+{len(sig)}-byte win-certificate "
    f"+ {len(payload)}-byte list = {len(blob)} bytes; "
    f"list is one EFI_CERT_X509_SHA256 entry of {sig_size} bytes (hash+EFI_TIME)"
)
PY

echo "==> verifying the binding (KEK, list bytes, timestamp)"
if openssl cms -verify -binary -inform DER -in "$OUT/update.p7.der" \
        -content "$OUT/content.bin" -certfile "$KEK_CERT" -noverify \
        -out /dev/null 2>"$OUT/verify.log"; then
    echo "-- OK positive: the PKCS7 verifies against the KEK certificate over (list || EFI_TIME)"
else
    cat "$OUT/verify.log"
    echo "FAIL: the update package's signature did not verify against the KEK certificate"
    exit 1
fi

echo "==> negative controls: the binding must be load-bearing"
flip() { # $1 input, $2 output: flip one byte mid-file
    python3 - "$1" "$2" <<'PY'
import sys
data = bytearray(open(sys.argv[1], "rb").read())
if not data:
    raise SystemExit("FAIL: flip target is empty")
idx = len(data) // 2
data[idx] ^= 0x01
open(sys.argv[2], "wb").write(bytes(data))
print(f"patch: flipped byte {idx} of {len(data)}")
PY
}

flip "$OUT/content.bin" "$OUT/content-tampered.bin"
if openssl cms -verify -binary -inform DER -in "$OUT/update.p7.der" \
        -content "$OUT/content-tampered.bin" -certfile "$KEK_CERT" -noverify \
        -out /dev/null 2>/dev/null; then
    echo "FAIL: a ONE-BYTE-ALTERED list still verified -- the signature does not cover the payload"
    exit 1
fi
echo "-- OK negative 1: a one-byte-altered dbx list fails verification"

python3 - "$OUT/time.bin" "$OUT/time-rollback.bin" <<'PY'
import sys
stamp = bytearray(open(sys.argv[1], "rb").read())
# Move the header stamp back a year: the classic rollback/replay attempt,
# which only the timestamp's presence INSIDE the signed content stops.
year = int.from_bytes(stamp[:2], "little") - 1
stamp[:2] = year.to_bytes(2, "little")
open(sys.argv[2], "wb").write(bytes(stamp))
print(f"patch: header stamp moved back to {year}-01-01")
PY
python3 - "$PAYLOAD" "$OUT/time-rollback.bin" "$OUT/content-rollback.bin" <<'PY'
import sys
payload = open(sys.argv[1], "rb").read()
stamp = open(sys.argv[2], "rb").read()
open(sys.argv[3], "wb").write(payload + stamp)
PY
if openssl cms -verify -binary -inform DER -in "$OUT/update.p7.der" \
        -content "$OUT/content-rollback.bin" -certfile "$KEK_CERT" -noverify \
        -out /dev/null 2>/dev/null; then
    echo "FAIL: an EARLIER header timestamp still verified -- the stamp is not bound into the signature"
    exit 1
fi
echo "-- OK negative 2: a backdated header EFI_TIME fails verification (the stamp is signed)"

openssl cms -sign -binary -noattr -in "$OUT/content.bin" \
    -signer "$STRANGER_CERT" -inkey "$STRANGER_KEY" \
    -outform DER -out "$OUT/stranger.p7.der" 2>/dev/null \
    || { echo "FAIL: openssl cms could not sign with the stranger key"; exit 1; }

# Identity, not just signature validity: `openssl cms -verify` accepts any
# signer certificate the message carries, so the KEK requirement is proven
# by comparing the package's signer certificate to the KEK certificate
# byte for byte (a gate that only checked "the signature verifies" would
# pass a package signed by a stranger key -- found on this gate's own
# first run).
signer_der_sha() { # $1 package DER, $2 scratch PEM -> DER sha256 on stdout
    openssl pkcs7 -inform DER -in "$1" -print_certs -out "$2" 2>/dev/null || return 1
    openssl x509 -in "$2" -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1
}
KEK_DER_SHA="$(openssl x509 -in "$KEK_CERT" -outform DER | sha256sum | cut -d' ' -f1)"
GOOD_SIGNER_SHA="$(signer_der_sha "$OUT/update.p7.der" "$OUT/good-signer.pem" || true)"
STRANGER_SIGNER_SHA="$(signer_der_sha "$OUT/stranger.p7.der" "$OUT/stranger-signer.pem" || true)"
if [[ -z "$GOOD_SIGNER_SHA" || "$GOOD_SIGNER_SHA" != "$KEK_DER_SHA" ]]; then
    echo "FAIL: the update package's signer certificate is not the KEK certificate"
    exit 1
fi
if [[ -z "$STRANGER_SIGNER_SHA" || "$STRANGER_SIGNER_SHA" == "$KEK_DER_SHA" ]]; then
    echo "FAIL: the stranger-signed package resolved to the KEK certificate (or no signer at all)"
    exit 1
fi
echo "-- OK negative 3: a package signed by a non-KEK key carries a DIFFERENT signer certificate (${STRANGER_SIGNER_SHA:0:16}.. vs the KEK's ${KEK_DER_SHA:0:16}..), so it is not a KEK-authorised update"

echo "PASS: the KEK-signed authenticated dbx-update package is well formed and its signature binds the list, the header EFI_TIME and the KEK key -- an altered list, a backdated stamp or a non-KEK signer each fail; firmware ACCEPTANCE at SetVariable time is not covered here (named in ADR-0029 as the platform's authenticated-update surface)"
