#!/usr/bin/env bash
#
# make_dbx_update_package.sh -- compose and sign ONE
# EFI_VARIABLE_AUTHENTICATION_2 package carrying a dbx signature list.
#
# This is the UPDATE side of ADR-0029's revocation surface, in the form
# a firmware actually verifies at SetVariable time:
#
#   EFI_TIME TimeStamp                       (16 bytes, from the arg)
#   WIN_CERTIFICATE_UEFI_GUID {
#       UINT32 dwLength                      (8 + 16 + SignedData length)
#       UINT16 wRevision                     (0x0200)
#       UINT16 wCertificateType              (0x0EF1 WIN_CERT_TYPE_EFI_GUID)
#       EFI_GUID CertType                    (EFI_CERT_TYPE_PKCS7_GUID)
#       UINT8  CertData[SignedData length]   (a BARE PKCS#7 SignedData)
#   }
#   UINT8 Payload[SignatureList length]      (the new dbx list)
#
# Two details are the whole reason this tool exists, and BOTH were wrong
# in the first version of it (which agreed with the host gate and with
# no real firmware -- ADR-0028's self-consistent-but-wrong class again):
#
#  1. THE SIGNED CONTENT IS NOT (payload || timestamp). edk2's
#     VerifyTimeBasedPayload serializes
#         (VariableName, VendorGuid, Attributes, TimeStamp, Data)
#     -- the UTF-16LE name WITHOUT its terminator, the 16-byte vendor
#     GUID in wire order, the u32 attributes of the SetVariable call,
#     the 16-byte EFI_TIME, and only then the new payload. This tool
#     signs exactly that, for `dbx` under
#     EFI_IMAGE_SECURITY_DATABASE_GUID with the attributes the stub
#     passes (NV|BS|RT|TIME_BASED_AUTHENTICATED = 0x27).
#  2. THE PKCS7 IN THE DESCRIPTOR IS A BARE SignedData. edk2 reads
#     CertData as the SignedData SEQUENCE directly (its SHA-256 OID
#     check sits at a fixed +13 into it); a full PKCS#7 ContentInfo
#     wrapper -- what `openssl cms -sign` emits -- shifts every offset
#     and is refused. The wrapper is produced here, verified here, and
#     then stripped to its [0] SignedData for the descriptor.
#
# The timestamp sits INSIDE the signed content, so a package cannot be
# replayed with a different stamp; and the payload is signed with it,
# so a package cannot be replayed over a different list. edk2's
# ProcessVarWithKek verifies the PKCS7 against the enrolled KEK, then
# compares TimeStamp against the stored variable's timestamp.
#
# The one implementation: `tools/dbx_update_gate.sh` (which proves the
# binding with negatives) and `build.sh` (which embeds the packages the
# stub writes at boot, covering firmware ACCEPTANCE at SetVariable time)
# both call this script, so the gate and the boot path cannot drift into
# two self-consistent package layouts -- the failure class this repo met
# with ADR-0028's GUID constants and ADR-0029's payload field order.
#
# Signature is generated with `openssl cms -sign -binary -noattr` and
# verified here before the script exits zero, so a package that the
# caller is about to embed can never be one this tool could not verify
# itself.
#
# Usage:
#   ./tools/make_dbx_update_package.sh <payload-siglist-der> \
#       <signer-cert-pem> <signer-key-pem> <year> <out-der> [work-dir]
#
# <year> stamps EFI_TIME as <year>-01-01 00:00:00 UTC (deterministic, so
# a refusal is reproducible). When [work-dir] is given, the intermediates
# the caller may need for its own negatives are copied there:
# content.bin (the exact signed bytes, payload || TimeStamp), time.bin
# (the EFI_TIME alone) and update.p7.der (the DER PKCS7 signature).

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $# -lt 5 || $# -gt 6 ]]; then
    sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
PAYLOAD="$1"
SIGNER_CERT="$2"
SIGNER_KEY="$3"
YEAR="$4"
OUT="$5"
WORK_OUT="${6:-}"

for f in "$PAYLOAD" "$SIGNER_CERT" "$SIGNER_KEY"; do
    if [[ ! -s "$f" ]]; then
        echo "FAIL: make_dbx_update_package: input missing or empty: $f"
        exit 1
    fi
done
if [[ ! "$YEAR" =~ ^[0-9]{4}$ ]] || (( YEAR < 1900 || YEAR > 2999 )); then
    echo "FAIL: make_dbx_update_package: year must be 1900..2999 (got $YEAR)"
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$(dirname "$OUT")"

echo "==> composing the signed content (VarName || VendorGuid || Attrs || EFI_TIME || payload, stamp $YEAR-01-01 00:00:00)"
python3 - "$PAYLOAD" "$YEAR" "$WORK/content.bin" "$WORK/time.bin" <<'PY'
import sys
import uuid

payload_path, year, content_path, time_path = sys.argv[1:5]
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

# The serialization edk2's VerifyTimeBasedPayload signs/verifies
# (AuthService.c), UEFI 2.10 8.2.2: the VariableName as CHAR16 with NO
# terminator, the VendorGuid in EFI wire order, the Attributes of the
# SetVariable call, the descriptor's EFI_TIME, then the new value.
# uuid.bytes_le is exactly the EFI_GUID wire layout (Data1-3
# little-endian, Data4 verbatim), so the GUID comes from the spec
# string rather than a table. The attribute set must be the one the
# caller passes: NV|BS|RT|TIME_BASED_AUTHENTICATED = 0x27, the
# attributes the stub's dbx write uses and the minimum dbx accepts.
var_name = "dbx".encode("utf-16-le")           # 6 bytes, no NUL
var_guid = uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f").bytes_le
var_attrs = (0x1 | 0x2 | 0x4 | 0x20).to_bytes(4, "little")
open(content_path, "wb").write(var_name + var_guid + var_attrs + time_bytes + payload)

# The list must satisfy edk2 CheckSignatureListFormat's own table: one
# EFI_CERT_X509_SHA256 list, SignatureHeaderSize 0, SignatureData 48
# bytes (32-byte To-Be-Signed hash + EFI_TIME), entries tiling exactly.
if payload[:16] != uuid.UUID("3bd2a492-96c0-4079-b420-fcf98ef103ed").bytes_le:
    raise SystemExit(
        "FAIL: the payload list is not EFI_CERT_X509_SHA256 -- this tool builds "
        "the hash-typed revocation package; a whole-certificate package has a "
        "different SignatureSize and belongs with make-dbx-siglist"
    )
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
    f"patch: signed content = 'dbx' || ISD GUID || attrs 0x27 || 16-byte EFI_TIME "
    f"|| {len(payload)}-byte list = {6 + 16 + 4 + 16 + len(payload)} bytes; "
    f"list is one EFI_CERT_X509_SHA256 entry of {sig_size} bytes (hash+EFI_TIME)"
)
PY

echo "==> signing with the KEK key (openssl cms, DER, detached)"
openssl cms -sign -binary -noattr -in "$WORK/content.bin" \
    -signer "$SIGNER_CERT" -inkey "$SIGNER_KEY" \
    -outform DER -out "$WORK/update.p7.der" 2>"$WORK/sign.log" \
    || { cat "$WORK/sign.log"; echo "FAIL: openssl cms could not sign the update content"; exit 1; }

echo "==> stripping the ContentInfo wrapper (the descriptor carries the BARE SignedData)"
# edk2 reads CertData as the PKCS#7 SignedData SEQUENCE: its SHA-256 OID
# check sits at a fixed +13, which only lines up with a bare SignedData.
# `openssl cms` emits ContentInfo { OID signedData, [0] SignedData }; the
# [0] element's content is exactly the SignedData to embed.
python3 - "$WORK/update.p7.der" "$WORK/signed-data.der" <<'PY'
import sys

der = open(sys.argv[1], "rb").read()

def tlv(buf, off):
    tag = buf[off]
    first = buf[off + 1]
    if first & 0x80:
        n = first & 0x7F
        if n == 0 or n > 4:
            raise SystemExit("FAIL: unsupported DER length encoding")
        length = int.from_bytes(buf[off + 2:off + 2 + n], "big")
        hdr = 2 + n
    else:
        length = first
        hdr = 2
    return tag, hdr, length

off = 0
tag, hdr, length = tlv(der, off)
if tag != 0x30 or off + hdr + length != len(der):
    raise SystemExit("FAIL: the signature is not one DER SEQUENCE")
# ContentInfo: SEQUENCE { OID, [0] SignedData }
inner = off + hdr
tag, hdr2, oid_len = tlv(der, inner)
if tag != 0x06:
    raise SystemExit("FAIL: ContentInfo does not start with its contentType OID")
inner += hdr2 + oid_len
tag, hdr3, wrapped = tlv(der, inner)
if tag != 0xA0:
    raise SystemExit("FAIL: ContentInfo carries no [0] content -- not PKCS#7 SignedData")
start = inner + hdr3
tag, hdr4, sd_len = tlv(der, start)
if tag != 0x30 or start + hdr4 + sd_len != inner + hdr3 + wrapped:
    raise SystemExit("FAIL: the [0] content is not exactly one SignedData SEQUENCE")
open(sys.argv[2], "wb").write(der[start:start + hdr4 + sd_len])
print(f"patch: bare SignedData = {hdr4 + sd_len} bytes (ContentInfo was {len(der)})")
PY

echo "==> assembling EFI_VARIABLE_AUTHENTICATION_2 (EFI_TIME + WIN_CERTIFICATE_UEFI_GUID + list)"
python3 - "$WORK/time.bin" "$WORK/signed-data.der" "$PAYLOAD" "$OUT" <<'PY'
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
print(
    f"patch: package = 16-byte stamp + 8+16+{len(sig)}-byte win-certificate "
    f"+ {len(payload)}-byte list = {len(blob)} bytes"
)
PY

echo "==> self-verify (the package this tool emits must verify against its own signer)"
if ! openssl cms -verify -binary -inform DER -in "$WORK/update.p7.der" \
        -content "$WORK/content.bin" -certfile "$SIGNER_CERT" -noverify \
        -out /dev/null 2>"$WORK/verify.log"; then
    cat "$WORK/verify.log"
    echo "FAIL: the composed package's signature does not verify against $SIGNER_CERT"
    exit 1
fi

if [[ -n "$WORK_OUT" ]]; then
    mkdir -p "$WORK_OUT"
    cp "$WORK/content.bin" "$WORK_OUT/content.bin"
    cp "$WORK/time.bin" "$WORK_OUT/time.bin"
    cp "$WORK/update.p7.der" "$WORK_OUT/update.p7.der"
fi

echo "make_dbx_update_package: OK $(wc -c < "$OUT") bytes, signed by $SIGNER_CERT, stamp $YEAR-01-01"
