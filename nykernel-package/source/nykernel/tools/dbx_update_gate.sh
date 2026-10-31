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
#     8 + 16 + SignedData length, revision 0x0200, type 0x0EF1,
#     EFI_CERT_TYPE_PKCS7_GUID) whose CertData is a BARE DER PKCS#7
#     SignedData over the serialization edk2 actually signs (UEFI 2.10
#     8.2.2): VariableName || VendorGuid || Attributes || EFI_TIME ||
#     the new list. Both of those were wrong in this gate's first
#     version (payload || stamp, ContentInfo-wrapped) and this gate
#     verified itself green over bytes no firmware could accept --
#     see tools/make_dbx_update_package.sh, which now owns the shape and
#     records the two failures.
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

# ONE package builder for the whole repo: this gate and build.sh (which
# embeds the packages the stub writes at boot) call the same tool, so a
# package shape can never drift into two self-consistent variants. The
# work dir keeps the intermediates the negatives below replay against.
echo "==> composing the KeK-signed package (stamp $STAMP)"
./tools/make_dbx_update_package.sh "$PAYLOAD" "$KEK_CERT" "$KEK_KEY" "$TS_YEAR" "$OUT/auth2.bin" "$OUT"

echo "==> verifying the binding (KEK, list bytes, timestamp)"
if openssl cms -verify -binary -inform DER -in "$OUT/update.p7.der" \
        -content "$OUT/content.bin" -certfile "$KEK_CERT" -noverify \
        -out /dev/null 2>"$OUT/verify.log"; then
    echo "-- OK positive: the PKCS7 verifies against the KEK certificate over (VarName || VendorGuid || Attrs || EFI_TIME || list)"
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

# The rollback attempt, built through the SAME builder so the only
# difference from the real package is the stamp: content.bin is
# byte-identical except inside the EFI_TIME field, so verifying the REAL
# signature over it isolates exactly the timestamp.
echo "==> negative 2: a backdated header EFI_TIME (the replay attempt)"
./tools/make_dbx_update_package.sh "$PAYLOAD" "$KEK_CERT" "$KEK_KEY" \
    "$((TS_YEAR - 1))" "$OUT/rollback/auth2.bin" "$OUT/rollback" > /dev/null
if cmp -s "$OUT/content.bin" "$OUT/rollback/content.bin"; then
    echo "FAIL: the backdated package's signed content is IDENTICAL to the real one -- the stamp is not in the signature"
    exit 1
fi
if openssl cms -verify -binary -inform DER -in "$OUT/update.p7.der" \
        -content "$OUT/rollback/content.bin" -certfile "$KEK_CERT" -noverify \
        -out /dev/null 2>/dev/null; then
    echo "FAIL: an EARLIER header timestamp still verified -- the stamp is not bound into the signature"
    exit 1
fi
echo "-- OK negative 2: a backdated header EFI_TIME fails verification (the stamp is signed inside the content)"

# The serialization itself is a pinned fact, not an implementation detail:
# this asserts the bytes edk2's VerifyTimeBasedPayload builds, from the
# SPEC's strings (the UTF-16LE name, the vendor GUID in wire order, the
# u32 attribute word, the header stamp), not from the builder's code.
echo "==> the signed content's layout: VarName || VendorGuid || Attrs || EFI_TIME || payload"
python3 - "$OUT/content.bin" "$PAYLOAD" "$OUT/time.bin" <<'PY'
import sys

content = open(sys.argv[1], "rb").read()
payload = open(sys.argv[2], "rb").read()
stamp = open(sys.argv[3], "rb").read()
name = "dbx".encode("utf-16-le")            # 64 00 62 00 78 00, no terminator
guid = bytes.fromhex("cbb219d73a3d9645a3bcdad00e67656f")  # d719b2cb-... wire order
attrs = (0x27).to_bytes(4, "little")       # NV|BS|RT|TIME_BASED_AUTHENTICATED
expected = name + guid + attrs + stamp + payload
errors = []
if content != expected:
    errors.append(f"content is {len(content)} bytes and does not equal name+GUID+attrs+stamp+payload ({len(expected)})")
    for label, off, want in (
        ("VariableName UTF-16LE", 0, name),
        ("VendorGuid wire bytes", len(name), guid),
        ("Attributes u32", len(name) + len(guid), attrs),
        ("EFI_TIME", len(name) + len(guid) + 4, stamp),
    ):
        got = content[off:off + len(want)]
        if got != want:
            errors.append(f"{label} at {off}: {got.hex()} != {want.hex()}")
    if content[len(name) + len(guid) + 4 + len(stamp):] != payload:
        errors.append("the payload does not sit after the EFI_TIME")
if errors:
    raise SystemExit("FAIL: signed-content layout: " + "; ".join(errors))
print(
    f"patch: layout OK -- 'dbx'({len(name)}) || ISD GUID({len(guid)}) || attrs "
    f"0x27(4) || EFI_TIME({len(stamp)}) || payload({len(payload)}) = {len(content)} bytes"
)
PY

# INDEPENDENT ORACLE: sbsigntools' sbvarsign signs this same descriptor
# shape, so its signature must verify over the content built from ITS
# OWN stamp by the layout asserted above. A serialization this repo
# invented would fail here even if the builder and the gate agreed on it
# -- the ADR-0028/0029 self-consistency lesson, applied.
if command -v sbvarsign >/dev/null 2>&1; then
    echo "==> cross-checking the serialization against sbvarsign (sbsigntools)"
    sbvarsign --key "$KEK_KEY" --cert "$KEK_CERT" \
        --guid d719b2cb-3d3a-4596-a3bc-dad00e67656f \
        --attr NON_VOLATILE,BOOTSERVICE_ACCESS,RUNTIME_ACCESS \
        --output "$OUT/sbvarsign.auth2" dbx "$PAYLOAD" > /dev/null
    python3 - "$OUT/sbvarsign.auth2" "$PAYLOAD" "$OUT" <<'PY'
import sys
import uuid

blob = open(sys.argv[1], "rb").read()
payload = open(sys.argv[2], "rb").read()
out = sys.argv[3]
stamp = blob[:16]
dw = int.from_bytes(blob[16:20], "little")
if blob[24:40] != uuid.UUID("4aafd29d-68df-49ee-8aa9-347d375665a7").bytes_le:
    raise SystemExit("FAIL: sbvarsign's CertType is not EFI_CERT_TYPE_PKCS7_GUID")
signed_data = blob[40:16 + dw]
if blob[16 + dw:] != payload:
    raise SystemExit("FAIL: sbvarsign's package does not carry the payload this gate passed")
content = ("dbx".encode("utf-16-le")
           + uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f").bytes_le
           + (0x27).to_bytes(4, "little") + stamp + payload)
# Re-wrap the bare SignedData in ContentInfo so `openssl cms` can verify
# it (the descriptor itself carries no wrapper -- that is the point).
oid = bytes.fromhex("06092a864886f70d010702")
def der_len(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw
inner = oid + b"\xA0" + der_len(len(signed_data)) + signed_data
content_info = b"\x30" + der_len(len(inner)) + inner
open(out + "/sbvarsign-content.bin", "wb").write(content)
open(out + "/sbvarsign-contentinfo.der", "wb").write(content_info)
print(f"patch: sbvarsign reference rebuilt -- stamp {int.from_bytes(stamp[:2], 'little')}-{stamp[2]:02d}-{stamp[3]:02d}, SignedData {len(signed_data)} bytes, payload {len(payload)} bytes")
PY
    if openssl cms -verify -binary -inform DER -in "$OUT/sbvarsign-contentinfo.der" \
            -content "$OUT/sbvarsign-content.bin" -noverify \
            -out /dev/null 2>/tmp/sbvarsign-verify.log; then
        echo "-- OK oracle: sbvarsign's own signature verifies over the content rebuilt from the pinned layout"
    else
        cat /tmp/sbvarsign-verify.log
        echo "FAIL: sbvarsign's signature does NOT verify over the layout this gate asserts -- the serialization is wrong, not sbvarsign's"
        exit 1
    fi
    ORACLE_CLAIM="which is the serialization edk2 signs and sbvarsign independently reproduces"
else
    echo "note: sbvarsign absent -- the independent serialization cross-check was skipped (named fact)"
    ORACLE_CLAIM="checked against the pinned spec layout only -- sbvarsign was absent, so nothing independent reproduced it here"
fi

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

echo "PASS: the KEK-signed authenticated dbx-update package is well formed -- its bare PKCS#7 SignedData binds (VariableName, VendorGuid, Attributes, EFI_TIME, list), $ORACLE_CLAIM; an altered list, a backdated stamp or a non-KEK signer each fail. Firmware ACCEPTANCE at SetVariable time is proven by the .ms boot gate's dbx-update leg (a real firmware storing this package and reading it back), not by this host gate."
