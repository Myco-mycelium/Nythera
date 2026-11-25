#!/usr/bin/env python3
"""Build `userprog/classes_hello.dex` -- a real, checksum-correct DEX file
whose string_ids / type_ids / class_defs tables are non-empty.

The kernel's earlier fixture (`classes.dex`) is the smallest legal DEX: a
header, no sections, and the one map_list entry every DEX must have to
describe itself. That file can only prove the HEADER parses. This one is the
next step's specimen: it declares one class, `Lnyrqis/hello/Hello;`, whose
superclass is `Ljava/lang/Object;`, with a source-file string. The kernel
parses those tables out of the bytes and answers the descriptors, so the
proof is about contents rather than counts.

Everything here is deterministic: no timestamps, no dict iteration order
that could drift, and the two integrity fields are computed with hashlib /
zlib exactly as the DEX format specifies (Adler-32 over bytes[12:], SHA-1
over bytes[32:]). Re-running the script must reproduce the file byte for
byte, which the demo's own digest pin and the repository's snapshot tooling
both rely on.

The layout is laid out by hand (not by a DEX writer library) so every offset
this kernel reads is a number this script can assert before it writes:

    header        0..112
    string_ids  112..124   (3 x 4)
    type_ids    124..132   (2 x 4)
    class_defs  132..164   (1 x 32)
    string_data 164..218   (three string_data_item, ULEB128 length + MUTF-8 + NUL)
    map_list    218..294   (6 entries: header, string_id, type_id, class_def,
                            string_data, map_list)
    file_size   294

MUTF-8 note: these descriptors are ASCII, so their MUTF-8 encoding is their
bytes and their UTF-16 length is their byte count. The builder asserts that
rather than assuming it, and the kernel's decoder checks the same equality
on every parse.
"""

import hashlib
import pathlib
import struct
import zlib

CLASS = b"Lnyrqis/hello/Hello;"
SUPER = b"Ljava/lang/Object;"
SOURCE = b"Hello.java"

STRINGS = [CLASS, SUPER, SOURCE]

# type_ids: the class type, then its superclass type. Both name strings by
# index, which is what makes `type_descriptor` a two-hop read.
TYPES = [0, 1]

HEADER_SIZE = 0x70
STRING_IDS_OFF = HEADER_SIZE
TYPE_IDS_OFF = STRING_IDS_OFF + 4 * len(STRINGS)
CLASS_DEFS_OFF = TYPE_IDS_OFF + 4 * len(TYPES)
STRING_DATA_OFF = CLASS_DEFS_OFF + 32
MAP_OFF = STRING_DATA_OFF + sum(1 + len(s) + 1 for s in STRINGS)
FILE_SIZE = MAP_OFF + 4 + 12 * 6

# map_list item types (DEX spec, "map_item.type").
TYPE_HEADER_ITEM = 0x0000
TYPE_STRING_ID_ITEM = 0x0001
TYPE_TYPE_ID_ITEM = 0x0002
TYPE_CLASS_DEF_ITEM = 0x0006
TYPE_STRING_DATA_ITEM = 0x2002
TYPE_MAP_LIST = 0x1000


def uleb128(value: int) -> bytes:
    assert 0 <= value < (1 << 32)
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def main() -> int:
    for s in STRINGS:
        # ASCII descriptors: one byte, one UTF-16 code unit (the premise the
        # kernel's MUTF-8 decoder re-checks on every string it resolves).
        assert all(0x20 <= b < 0x7F for b in s), s

    strings_blob = bytearray()
    string_offsets = []
    for s in STRINGS:
        string_offsets.append(STRING_DATA_OFF + len(strings_blob))
        strings_blob += uleb128(len(s)) + s + b"\x00"

    header = bytearray(HEADER_SIZE)
    header[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", header, 32, FILE_SIZE)
    struct.pack_into("<I", header, 36, HEADER_SIZE)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<I", header, 44, 0)  # link_size
    struct.pack_into("<I", header, 48, 0)  # link_off
    struct.pack_into("<I", header, 52, MAP_OFF)
    struct.pack_into("<I", header, 56, len(STRINGS))
    struct.pack_into("<I", header, 60, STRING_IDS_OFF)
    struct.pack_into("<I", header, 64, len(TYPES))
    struct.pack_into("<I", header, 68, TYPE_IDS_OFF)
    # proto/field/method ids are all empty.
    struct.pack_into("<I", header, 96, 1)  # class_defs_size
    struct.pack_into("<I", header, 100, CLASS_DEFS_OFF)
    struct.pack_into("<I", header, 104, FILE_SIZE - STRING_DATA_OFF)
    struct.pack_into("<I", header, 108, STRING_DATA_OFF)

    string_ids = b"".join(struct.pack("<I", off) for off in string_offsets)
    type_ids = b"".join(struct.pack("<I", idx) for idx in TYPES)
    class_def = struct.pack(
        "<8I",
        0,  # class_idx -> TYPES[0] -> "Lnyrqis/hello/Hello;"
        0x1,  # ACC_PUBLIC
        1,  # superclass_idx -> TYPES[1] -> "Ljava/lang/Object;"
        0,  # interfaces_off
        2,  # source_file_idx -> STRINGS[2]
        0,  # annotations_off
        0,  # class_data_off (no methods or fields)
        0,  # static_values_off
    )
    assert len(class_def) == 32

    map_items = [
        (TYPE_HEADER_ITEM, 1, 0),
        (TYPE_STRING_ID_ITEM, len(STRINGS), STRING_IDS_OFF),
        (TYPE_TYPE_ID_ITEM, len(TYPES), TYPE_IDS_OFF),
        (TYPE_CLASS_DEF_ITEM, 1, CLASS_DEFS_OFF),
        (TYPE_STRING_DATA_ITEM, len(STRINGS), STRING_DATA_OFF),
        (TYPE_MAP_LIST, 1, MAP_OFF),
    ]
    assert map_items == sorted(map_items, key=lambda item: item[2])
    map_list = struct.pack("<I", len(map_items))
    for item_type, size, off in map_items:
        map_list += struct.pack("<HHI I", item_type, 0, size, off)

    body = header + string_ids + type_ids + class_def + strings_blob + map_list
    assert len(body) == FILE_SIZE, (len(body), FILE_SIZE)
    assert len(body) == STRING_DATA_OFF + len(strings_blob) + len(map_list)

    # Order matters: the SHA-1 covers bytes[32:] and the Adler-32 covers
    # bytes[12:], i.e. the signature field is INSIDE the checksum's range. So
    # the signature is written first and the checksum computed over the
    # finished bytes; doing it the other way round leaves a checksum that no
    # reader (this kernel's included) would accept.
    body[12:32] = hashlib.sha1(bytes(body[32:])).digest()
    checksum = zlib.adler32(bytes(body[12:])) & 0xFFFFFFFF
    struct.pack_into("<I", body, 8, checksum)

    out = pathlib.Path(__file__).resolve().parent.parent / "userprog" / "classes_hello.dex"
    out.write_bytes(bytes(body))
    print(f"wrote {out} ({len(body)} bytes)")
    print(f"  checksum 0x{checksum:08x}")
    print(f"  sha1     {hashlib.sha1(bytes(body[32:])).hexdigest()}")
    print(f"  class    {CLASS.decode()} : {SUPER.decode()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
