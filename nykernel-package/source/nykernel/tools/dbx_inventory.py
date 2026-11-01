#!/usr/bin/env python3
"""
dbx_inventory.py -- report what a REAL dbx artifact actually contains.

ADR-0030's decision (do not add the seven extra UEFI 2.10 32.4.1 entry
classes to the enforced revocation surface) rests on a claim about what
shipped dbx distributions contain. That claim is checkable, so it is
checked rather than argued: point this tool at a vendor dbx update (an
EFI_VARIABLE_AUTHENTICATION_2 package, or the bare EFI_SIGNATURE_LIST
sequence a firmware stores) and it prints the entry types, their sizes
and their counts.

Measured 2026-10-31 against Microsoft's published artifacts (this is
the recheck recorded in ADR-0030 v1.1.0):

    DBXUpdate.bin       (PostSignedObjects/DBX/amd64)          24,629 B
        443 x EFI_CERT_SHA256 (48-byte entries)
    DBXUpdate2024.bin   (PostSignedObjects/Optional/DBX)        5,052 B
        1 x EFI_CERT_X509 (1515-byte DER certificate)
        3 x EFI_CERT_SHA256
    DBXUpdateSVN.bin    (PostSignedObjects/Optional/DBX)        3,524 B
        3 x EFI_CERT_SHA256

and against PreSignedObjects/DBX/dbx_info_msft_latest.json, the
inventory the artifacts are built from: images x64 445 / ia32 96 /
aarch64 26 / arm 110, every one hashType SHA256; certificates: ONE
(CN = Microsoft Windows Production PCA 2011, revoked as a whole
certificate); svns: per-image version counters, which ride the
EFI_CERT_SHA256 list type (the 48-byte entry is a 16-byte owner GUID
plus the 32-byte value).

Usage:
    ./tools/dbx_inventory.py <update.bin> [more.bin ...]
    ./tools/dbx_inventory.py --structured <update.bin>   # machine-readable

Exit 0 when every file parses as a well-formed signature-list sequence,
1 otherwise (a malformed artifact is a fact worth failing on).
"""

import json
import struct
import sys
import uuid

# Canonical wire GUIDs, from the spec strings rather than a hand-typed
# byte table (uuid.bytes_le IS the EFI_GUID wire layout: Data1-3
# little-endian, Data4 verbatim). Source: UEFI 2.10 32.4.1.1 and edk2's
# ImageAuthentication.h / SignatureDatabase.h.
TYPES = {
    "c1c41626-504c-4092-aca9-41f936934328": "EFI_CERT_SHA256",
    "826ca512-cf10-4ac9-b187-be01496631bd": "EFI_CERT_SHA1",
    "0b6e5233-a65c-44c9-9407-d9ab83bfc8bd": "EFI_CERT_SHA224",
    "ff3e5307-9fd0-48c9-85f1-8ad56c701e01": "EFI_CERT_SHA384",
    "093e0fae-a6c4-4f50-9f1b-d41e2b89c19a": "EFI_CERT_SHA512",
    "a5c059a1-94e4-4aa7-87b5-ab155c2bf072": "EFI_CERT_X509",
    "3bd2a492-96c0-4079-b420-fcf98ef103ed": "EFI_CERT_X509_SHA256",
    "7076876e-80c2-4ee6-aafd-28c3499e3fdb": "EFI_CERT_X509_SHA384",
    "446dbf63-2502-4cda-bcfa-2465d2b0fe9d": "EFI_CERT_X509_SHA512",
    "a7717414-c616-4977-9420-844712a735bf": "EFI_CERT_TYPE_RSA2048_SHA256",
}

WIN_CERT_TYPE_EFI_GUID = 0x0EF1
PKCS7_GUID = "4aafd29d-68df-49ee-8aa9-347d375665a7"


def guid(b):
    return str(uuid.UUID(bytes_le=bytes(b[:16])))


def parse_lists(buf, off):
    """EFI_SIGNATURE_LIST sequence; returns (lists, end_offset)."""
    lists = []
    start = off
    while off + 28 <= len(buf):
        sig_type = guid(buf[off:off + 16])
        size, hdr, entry = struct.unpack("<III", buf[off + 16:off + 28])
        if size < 28 or off + size > len(buf):
            raise ValueError(
                f"list at +{off} declares size {size}, which does not fit "
                f"{len(buf) - off} remaining bytes")
        if entry == 0:
            count = 0
        elif (size - 28 - hdr) % entry:
            raise ValueError(
                f"list {sig_type} at +{off}: entries of {entry} bytes do not "
                f"tile {size - 28 - hdr} bytes")
        else:
            count = (size - 28 - hdr) // entry
        lists.append({
            "type": sig_type,
            "name": TYPES.get(sig_type, "unknown"),
            "list_size": size,
            "header_size": hdr,
            "entry_size": entry,
            "count": count,
        })
        off += size
    return lists, off, start


def load(path):
    data = open(path, "rb").read()
    if len(data) < 28:
        raise ValueError(f"too short to be a dbx artifact ({len(data)} bytes)")

    # Mode 1: bare EFI_SIGNATURE_LIST sequence (what a firmware stores).
    try:
        lists, end, _ = parse_lists(data, 0)
        if end == len(data) and lists:
            return {"form": "signature-list", "stamp": None,
                    "lists": lists, "bytes": len(data)}
    except (ValueError, struct.error):
        pass

    # Mode 2: EFI_VARIABLE_AUTHENTICATION_2 = 16-byte EFI_TIME +
    # WIN_CERTIFICATE_UEFI_GUID (dwLength covers the whole structure,
    # header included) + the new value.
    if len(data) >= 16 + 8 + 16:
        year = int.from_bytes(data[0:2], "little")
        month, day = data[2], data[3]
        dw_len, w_rev, w_type = struct.unpack("<IHH", data[16:24])
        cert_type = guid(data[24:40])
        # Microsoft's shipped updates carry the vintage 2010-03-06 stamp, so
        # the plausible-year window has to span the whole UEFI era rather than
        # "recent": this is a shape test, not a freshness judgement.
        if 1990 <= year <= 2100 and w_type == WIN_CERT_TYPE_EFI_GUID:
            if cert_type != PKCS7_GUID:
                raise ValueError(
                    f"win-certificate type is {cert_type}, not EFI_CERT_TYPE_PKCS7_GUID")
            payload_at = 16 + dw_len
            if payload_at >= len(data):
                raise ValueError(
                    f"win-certificate length {dw_len} leaves no payload in {len(data)} bytes")
            lists, end, _ = parse_lists(data, payload_at)
            if end != len(data):
                raise ValueError(
                    f"{len(data) - end} trailing bytes after the last signature list")
            return {
                "form": "auth2",
                "stamp": f"{year:04d}-{month:02d}-{day:02d}",
                "cert_type": cert_type,
                "win_cert_length": dw_len,
                "revision": f"0x{w_rev:04x}",
                "lists": lists,
                "bytes": len(data),
            }

    raise ValueError("not an EFI_SIGNATURE_LIST sequence and not an auth2 update package")


def main(argv):
    structured = "--structured" in argv
    paths = [a for a in argv[1:] if not a.startswith("--")]
    if not paths:
        print(__doc__.strip())
        return 2

    rc = 0
    reports = []
    for path in paths:
        try:
            r = load(path)
        except (OSError, ValueError) as exc:
            print(f"FAIL: {path}: {exc}")
            rc = 1
            continue
        reports.append({"path": path, **r})

    if structured:
        print(json.dumps(reports, indent=1))
        return rc

    for r in reports:
        print(f"== {r['path']}: {r['bytes']} bytes, form {r['form']}"
              + (f", stamp {r['stamp']}" if r["stamp"] else ""))
        for item in r["lists"]:
            print(f"   {item['name']:26} entry {item['entry_size']:5}B "
                  f"x {item['count']:4}  (list {item['list_size']}B)")
        total = sum(i["count"] for i in r["lists"])
        classes = sorted({i["name"] for i in r["lists"]})
        print(f"   {total} entries in {len(r['lists'])} list(s); classes: {', '.join(classes)}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
