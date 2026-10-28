#!/usr/bin/env python3
#
# patch_carrier_negatives.py -- DER-structurally aware byte surgery for
# the ADR-0014 negative carriers (see tools/make_carrier.sh). Two
# modes, each refusing to write garbage:
#
#   flip-content <kernel> <flip.bin>
#       Reads the kernel image, flips exactly one bit (mid-image),
#       writes it out. Signed by make_carrier.sh into
#       carrier-neg-digest.der: a signature honestly made over the
#       WRONG bytes.
#
#   mutate-sid-serial <good-carrier.der> <out.der>
#       Walks the good carrier's DER structure structurally --
#       ContentInfo -> [0] -> SignedData -> signerInfos -> one
#       SignerInfo -> sid (IssuerAndSerial) -> serialNumber INTEGER --
#       flips the LAST byte of the serialNumber's content in place,
#       re-walks the output to assert the serial really changed, and
#       writes the result. The result is a structurally VALID carrier
#       whose SignerIdentifier no longer ties the signature to the
#       embedded certificate, exactly the cross-check the stub's walk
#       must refuse (not a truncation, not a length lie).
#
# The DER walk here mirrors uefi/src/der.rs + trust.rs (definite
# lengths only), so a carrier the stub parses is a carrier this tool
# can locate its targets inside -- and vice versa.

import sys


def read_tlv(buf, off):
    """One TLV at `off`: (tag, content_start, content_len, total_len)."""
    if off + 2 > len(buf):
        raise ValueError(f"truncated at tag byte, offset {off}")
    tag = buf[off]
    p = off + 1
    first = buf[p]
    p += 1
    if first & 0x80 == 0:
        content_len = first
    elif first == 0x80:
        raise ValueError(f"indefinite (BER) length at offset {off}: not DER")
    else:
        n = first & 0x7F
        if n == 0 or n > 4:
            raise ValueError(f"unsupported long-form length width at offset {off}")
        if p + n > len(buf):
            raise ValueError(f"truncated length bytes at offset {off}")
        content_len = int.from_bytes(buf[p : p + n], "big")
        p += n
    if p + content_len > len(buf):
        raise ValueError(f"content runs past the buffer from offset {off}")
    return tag, p, content_len, p + content_len - off


def children(buf, content_start, content_len, what):
    """All TLVs laid end-to-end inside one constructed node's content."""
    out = []
    pos = content_start
    end = content_start + content_len
    while pos < end:
        tlv = read_tlv(buf, pos)
        if tlv[3] <= 0:
            raise ValueError(what)
        out.append(tlv)
        pos += tlv[3]
    if pos != end:
        raise ValueError(what)
    return out


def expect(cond, msg):
    if not cond:
        raise ValueError(msg)


def tlv_kind(tag, cls, num):
    return (tag >> 6) == cls and (tag & 0x1F) == num


def find_sid_serial_node(buf):
    """Locates the SignerInfo's serialNumber INTEGER TLV in a carrier."""
    root = children(buf, 0, len(buf), "carrier: not exactly one ContentInfo")
    expect(len(root) == 1 and root[0][0] == 0x30, "carrier: not a ContentInfo SEQUENCE")
    ci = root[0]
    ci_kids = children(buf, ci[1], ci[2], "ContentInfo body")
    expect(
        len(ci_kids) == 2 and ci_kids[0][0] == 0x06 and ci_kids[1][0] == 0xA0,
        "carrier: ContentInfo does not shape like contentType+[0]",
    )
    a0 = ci_kids[1]
    sd_kids = children(buf, a0[1], a0[2], "SignedData wrapper")
    expect(len(sd_kids) == 1 and sd_kids[0][0] == 0x30, "carrier: SignedData wrapper")
    sd = sd_kids[0]
    fields = children(buf, sd[1], sd[2], "SignedData body")
    # version, digestAlgorithms SET, encapContentInfo, [0] certs, signerInfos SET
    expect(
        len(fields) == 5
        and fields[0][0] == 0x02
        and fields[1][0] == 0x31
        and fields[2][0] == 0x30
        and fields[3][0] == 0xA0
        and fields[4][0] == 0x31,
        "carrier: SignedData field shapes mismatch (is this a -noattr detached carrier?)",
    )
    sis = fields[4]
    si_kids = children(buf, sis[1], sis[2], "signerInfos SET")
    expect(len(si_kids) == 1 and si_kids[0][0] == 0x30, "signerInfos: not exactly one SignerInfo")
    si = si_kids[0]
    si_fields = children(buf, si[1], si[2], "SignerInfo body")
    # version, sid, digestAlgorithm, signatureAlgorithm, signature
    expect(
        len(si_fields) == 5 and si_fields[1][0] == 0x30,
        "SignerInfo: field shapes mismatch (signedAttrs carrier is not sid-surgery input)",
    )
    sid = si_fields[1]
    sid_kids = children(buf, sid[1], sid[2], "IssuerAndSerial body")
    expect(
        len(sid_kids) == 2 and sid_kids[0][0] == 0x30 and sid_kids[1][0] == 0x02,
        "sid: not IssuerAndSerial (Name SEQUENCE + serialNumber INTEGER)",
    )
    return sid_kids[1]


def mutate_sid_serial(in_path, out_path):
    buf = bytearray(open(in_path, "rb").read())
    node = find_sid_serial_node(buf)
    tag, content_start, content_len, _ = node
    expect(content_len >= 1, "sid serialNumber: empty INTEGER")
    # Flip the LAST content byte: any serial change breaks the
    # byte-equality cross-check; flipping one bit keeps the INTEGER
    # same-width (no accidental canonical/short-form drift).
    idx = content_start + content_len - 1
    before = bytes(buf[idx : content_start + content_len])
    buf[idx] ^= 0x01
    after = bytes(buf[idx : content_start + content_len])
    expect(before != after, "sid surgery: the byte did not change")
    # The output must still WALK: same structure, serial now different.
    node2 = find_sid_serial_node(buf)
    serial2 = bytes(buf[node2[1] : node2[1] + node2[2]])
    expect(serial2 != before, "sid surgery: re-walk sees the original serial")
    open(out_path, "wb").write(bytes(buf))
    print(
        f"patch_carrier_negatives: mutate-sid-serial OK "
        f"({tag:#04x}, {content_len} content bytes, flipped at {idx}: "
        f"{before.hex()} -> {after.hex()}), wrote {out_path}"
    )


def find_sid_issuer_serial(buf):
    """Same walk as find_sid_serial_node, returning BOTH the issuer
    Name TLV and the serialNumber INTEGER TLV of the SignerInfo's
    sid."""
    root = children(buf, 0, len(buf), "carrier: not exactly one ContentInfo")
    expect(len(root) == 1 and root[0][0] == 0x30, "carrier: not a ContentInfo SEQUENCE")
    ci = root[0]
    ci_kids = children(buf, ci[1], ci[2], "ContentInfo body")
    a0 = ci_kids[1]
    sd_kids = children(buf, a0[1], a0[2], "SignedData wrapper")
    sd = sd_kids[0]
    fields = children(buf, sd[1], sd[2], "SignedData body")
    sis = fields[4]
    si_kids = children(buf, sis[1], sis[2], "signerInfos SET")
    si = si_kids[0]
    si_fields = children(buf, si[1], si[2], "SignerInfo body")
    sid = si_fields[1]
    sid_kids = children(buf, sid[1], sid[2], "IssuerAndSerial body")
    expect(
        len(sid_kids) == 2 and sid_kids[0][0] == 0x30 and sid_kids[1][0] == 0x02,
        "sid: not IssuerAndSerial (Name SEQUENCE + serialNumber INTEGER)",
    )
    return sid_kids[0], sid_kids[1]


def print_sid_serial_hex(in_path):
    """Prints the SignerInfo sid's serialNumber content octets as an
    UPPERCASE hex string (openssl ca -serial's format) for the
    re-key negative in make_carrier.sh: the dbx pair-control's
    certificate must carry THE SAME identity pair as the carrier's
    signer. A DER sign byte of 0x00 (the INTEGER's leading zero) is
    stripped: openssl ca re-canonicalizes, and the walk compares the
    CONTENT octets both `Cert::parse`s produce."""
    buf = open(in_path, "rb").read()
    _, serial = find_sid_issuer_serial(buf)
    content = bytes(buf[serial[1] : serial[1] + serial[2]])
    expect(
        1 <= serial[2] < 0x80,
        "sid serial: short-form INTEGER expected in a carrier the stub itself walks",
    )
    if content[0] == 0x00:
        content = content[1:]
    expect(len(content) > 0, "sid serial: empty after sign-byte strip")
    print(content.hex().upper())


def le_u32_bytes(v):
    return bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF])


CERT_X509_GUID = bytes(
    [
        0xA1, 0x59, 0xC0, 0xA5, 0x94, 0xE4, 0xA7, 0x4A,
        0x87, 0xB5, 0xAB, 0x15, 0x5C, 0x2B, 0xF0, 0x72,
    ]
)


def make_dbx_siglist(payload_path, out_path):
    """Wraps one DER payload (a certificate, or a synthetic IssuerAndSerial
    pair) in a one-entry EFI_SIGNATURE_LIST -- the exact shape of the
    firmware's dbx variable content (UEFI 2.10 section 32.4.1), with
    the X.509 CertType EFI_CERT_X509_GUID: a 28-byte list header (the
    CertType GUID, SignatureListSize, SignatureHeaderSize=0,
    SignatureSize) followed by one entry: a 16-byte all-zero
    SignatureOwner GUID and the payload. The stub's dbx walk (dbx.rs)
    parses exactly this layout, so the generated controls exercise
    the same byte path a REAL firmware dbx would."""
    payload = open(payload_path, "rb").read()
    expect(len(payload) > 0, "make-dbx-siglist: empty payload")
    sig_size = 16 + len(payload)
    list_size = 28 + sig_size
    entry_owner = bytes(16)
    with open(out_path, "wb") as f:
        f.write(CERT_X509_GUID)
        f.write(le_u32_bytes(list_size))
        f.write(le_u32_bytes(0))
        f.write(le_u32_bytes(sig_size))
        f.write(entry_owner)
        f.write(payload)
    print(
        f"patch_carrier_negatives: make-dbx-siglist OK "
        f"(payload {len(payload)} bytes, list {list_size} bytes, "
        f"sig_size {sig_size}), wrote {out_path}"
    )


CERT_X509_SHA256_GUID = bytes(
    [
        0x75, 0x9F, 0x68, 0x44, 0x64, 0x9E, 0x49, 0x4A,
        0xA7, 0x28, 0x67, 0xF7, 0x6E, 0x83, 0xDC, 0x4A,
    ]
)


def cert_tbs(buf):
    """A certificate DER's tbsCertificate WHOLE TLV bytes (tag+length+
    content) -- the bytes RFC 5280 4.1.1.2 hashes, and exactly what
    uefi/src/dbx.rs computes sha256 over for hash-typed revocation
    comparison. The first child of the outer SEQUENCE starts exactly
    at the outer SEQUENCE's content start, so the whole TLV is
    [outer.content_start, child_total_end) -- computing the child's
    start this way avoids re-deriving long-form length-byte widths.
    Mirrors trust.rs's cert_tbs."""
    outer = read_tlv(buf, 0)
    expect(outer[0] == 0x30, "cert: not a DER SEQUENCE")
    expect(outer[3] == len(buf), "cert: trailing bytes after the outer SEQUENCE")
    start = outer[1]
    tbs = read_tlv(buf, start)
    expect(tbs[0] == 0x30, "cert: first element is not tbsCertificate SEQUENCE")
    # read_tlv's 4th element is the TLV's total LENGTH, not an end.
    return bytes(buf[start : start + tbs[3]])


def make_dbx_hashlist(cert_path, revoke_year, out_path):
    """Wraps ONE EFI_CERT_X509_SHA256_SIGNATURE entry -- a revoked
    certificate BY HASH: a 16-byte EFI_TIME revocation date followed
    by the SHA-256 of the certificate's To-Be-Signed content -- into a
    one-entry EFI_SIGNATURE_LIST of type EFI_CERT_X509_SHA256_GUID
    (UEFI 2.10 section 32.4.1's hash-typed revocation form; exactly
    the shape real-world Microsoft dbx updates use to revoke CA
    certificates). The revocation date is written with explicit
    deterministic fields `revoke_year`-01-01 00:00:00 UTC (year, then
    month..second as u8s, then the zeroed padding/nanoseconds/
    timezone/daylight tail), so the refusal is reproducible."""
    import hashlib

    buf = open(cert_path, "rb").read()
    tbs = cert_tbs(buf)
    tbs_hash = hashlib.sha256(tbs).digest()
    year = int(revoke_year)
    expect(1900 <= year <= 2999, "make-dbx-hashlist: revoke_year must be 1900..2999")
    efi_time = (
        year.to_bytes(2, "little")
        + bytes([1, 1, 0, 0, 0, 0])  # month=1, day=1, hour..second=0, Pad1
        + bytes(4)  # nanoseconds
        + bytes(2)  # timezone (0 = UTC)
        + bytes([0])  # daylight
        + bytes(1)  # pad2
    )
    expect(len(efi_time) == 16, "make-dbx-hashlist: EFI_TIME shape")
    sig_size = 16 + 16 + 32  # owner GUID + EFI_TIME + TBS hash
    list_size = 28 + sig_size
    with open(out_path, "wb") as f:
        f.write(CERT_X509_SHA256_GUID)
        f.write(le_u32_bytes(list_size))
        f.write(le_u32_bytes(0))
        f.write(le_u32_bytes(sig_size))
        f.write(bytes(16))  # SignatureOwner
        f.write(efi_time)
        f.write(tbs_hash)
    print(
        f"patch_carrier_negatives: make-dbx-hashlist OK "
        f"(tbs {len(tbs)} bytes, sha256 {tbs_hash.hex()[:16]}.., "
        f"revoked-at {year}-01-01, list {list_size} bytes), wrote {out_path}"
    )


def flip_content(in_path, out_path):
    data = bytearray(open(in_path, "rb").read())
    expect(len(data) > 0, "flip-content: empty input")
    idx = len(data) // 2
    data[idx] ^= 0x01
    open(out_path, "wb").write(bytes(data))
    print(f"patch_carrier_negatives: flip-content OK (bit flipped at {idx}), wrote {out_path}")


def main():
    argv = sys.argv
    args = argv[1:]
    if len(args) == 2 and args[0] == "print-sid-serial-hex":
        print_sid_serial_hex(args[1])
        return
    if len(args) == 3 and args[0] == "make-dbx-siglist":
        make_dbx_siglist(args[1], args[2])
        return
    if len(args) == 4 and args[0] == "make-dbx-hashlist":
        make_dbx_hashlist(args[1], args[2], args[3])
        return
    if len(args) != 3:
        print(
            "usage: patch_carrier_negatives.py flip-content <kernel> <out.bin>\n"
            "       patch_carrier_negatives.py mutate-sid-serial <good-carrier.der> <out.der>\n"
            "       patch_carrier_negatives.py print-sid-serial-hex <good-carrier.der>\n"
            "       patch_carrier_negatives.py make-dbx-siglist <payload.der> <out.der>\n"
            "       patch_carrier_negatives.py make-dbx-hashlist <cert.der> <revoke_year> <out.der>",
            file=sys.stderr,
        )
        sys.exit(2)
    mode, a, b = args
    if mode == "flip-content":
        flip_content(a, b)
    elif mode == "mutate-sid-serial":
        mutate_sid_serial(a, b)
    else:
        print(f"patch_carrier_negatives: unknown mode {mode!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
