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
    if len(args) != 3:
        print(
            "usage: patch_carrier_negatives.py flip-content <kernel> <out.bin>\n"
            "       patch_carrier_negatives.py mutate-sid-serial <good-carrier.der> <out.der>\n"
            "       patch_carrier_negatives.py print-sid-serial-hex <good-carrier.der>\n"
            "       patch_carrier_negatives.py make-dbx-siglist <payload.der> <out.der>",
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
