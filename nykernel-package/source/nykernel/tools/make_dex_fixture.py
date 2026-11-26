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


def write_integrity(body: bytearray) -> None:
    """Write the signature then the checksum, in the order the format forces.

    The SHA-1 covers bytes[32:] and the Adler-32 covers bytes[12:], i.e. the
    signature field is INSIDE the checksum's range, so the signature is written
    first and the checksum computed over the finished bytes. Doing it the other
    way round leaves a checksum no reader (this kernel's included) accepts.
    """
    body[12:32] = hashlib.sha1(bytes(body[32:])).digest()
    checksum = zlib.adler32(bytes(body[12:])) & 0xFFFFFFFF
    struct.pack_into("<I", body, 8, checksum)
    assert struct.unpack_from("<I", body, 8)[0] == checksum


# ---------------------------------------------------------------------------
# `classes_methods.dex` -- the same class, with one real method.
#
# The class-table fixture above can only prove that a class NAME resolves: its
# class_data_off is 0, so it declares no methods at all. This fixture is the
# next table: one direct method, `main([Ljava/lang/String;)V`, whose proto_id,
# method_id and class_data are all non-empty, so the kernel's resolvers answer
# a signature rather than a count.
#
# The method is declared ACC_PUBLIC|ACC_STATIC|ACC_NATIVE (0x109) so that its
# code_off is legitimately 0: a native method has no code_item, and this
# fixture is deliberately about the SIGNATURE tables, not about code. Claiming a
# normal method with no code_item would make the file malformed rather than
# small.
# ---------------------------------------------------------------------------

METHOD_STRINGS = [CLASS, SUPER, SOURCE, b"main", b"VL", b"V", b"[Ljava/lang/String;"]

# type_ids name strings: the class, its superclass, the parameter type, and the
# void return type. `proto_id.return_type_idx` and the type_list entries below
# both index this table.
METHOD_TYPES = [0, 1, 6, 5]

CLASS_TYPE = 0
VOID_TYPE = 3
PARAM_TYPE = 2
SHORTY_STRING = 4
RETURN_STRING = 5

TYPE_PROTO_ID_ITEM = 0x0003
TYPE_FIELD_ID_ITEM = 0x0004
TYPE_METHOD_ID_ITEM = 0x0005
TYPE_TYPE_LIST = 0x1001
TYPE_CLASS_DATA_ITEM = 0x2000


def align4(value: int) -> int:
    return (value + 3) & ~3


def write_methods_fixture() -> int:
    for s in METHOD_STRINGS:
        assert all(0x20 <= b < 0x7F for b in s), s

    strings_blob = bytearray()
    string_offsets = []
    for s in METHOD_STRINGS:
        string_offsets.append(0)  # patched below once the blob's base is known
        strings_blob += uleb128(len(s)) + s + b"\x00"

    string_ids_off = HEADER_SIZE
    type_ids_off = string_ids_off + 4 * len(METHOD_STRINGS)
    proto_ids_off = type_ids_off + 4 * len(METHOD_TYPES)
    method_ids_off = proto_ids_off + 12  # one proto_id_item
    class_defs_off = method_ids_off + 8  # one method_id_item
    string_data_off = class_defs_off + 32
    base = string_data_off
    string_offsets = []
    for s in METHOD_STRINGS:
        string_offsets.append(base)
        base += 1 + len(s) + 1

    type_list_off = align4(base)
    # size (u4) + one u2 type index, padded to the format's 4-byte alignment.
    class_data_off = type_list_off + align4(4 + 2 * 1)
    # An `encoded_method` is THREE ULEB128s -- method_idx_diff, access_flags,
    # code_off -- while an `encoded_field` is two. Reading only two happens to
    # work for a single method at index 0 (nothing follows it to misalign), which
    # is exactly why this fixture now writes the third field: a native method has
    # code_off 0, so the byte is a legal, meaningful zero here. The file stays the
    # same size because the trailing zero replaces padding the map needed anyway.
    class_data = bytes(
        [0, 0, 1, 0]  # static fields, instance fields, direct methods, virtual
        + [0]  # method_idx_diff -> METHOD_IDS[0]
        + list(uleb128(0x109))  # ACC_PUBLIC | ACC_STATIC | ACC_NATIVE
        + [0]  # code_off: a native method has no code_item
    )
    assert class_data_off + len(class_data) == class_data_off + 8
    map_off = align4(class_data_off + len(class_data))
    file_size = map_off + 4 + 12 * 10

    header = bytearray(HEADER_SIZE)
    header[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", header, 32, file_size)
    struct.pack_into("<I", header, 36, HEADER_SIZE)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<I", header, 44, 0)  # link_size
    struct.pack_into("<I", header, 48, 0)  # link_off
    struct.pack_into("<I", header, 52, map_off)
    struct.pack_into("<I", header, 56, len(METHOD_STRINGS))
    struct.pack_into("<I", header, 60, string_ids_off)
    struct.pack_into("<I", header, 64, len(METHOD_TYPES))
    struct.pack_into("<I", header, 68, type_ids_off)
    struct.pack_into("<I", header, 72, 1)  # proto_ids_size
    struct.pack_into("<I", header, 76, proto_ids_off)
    struct.pack_into("<I", header, 80, 0)  # field_ids_size
    struct.pack_into("<I", header, 84, 0)  # field_ids_off
    struct.pack_into("<I", header, 88, 1)  # method_ids_size
    struct.pack_into("<I", header, 92, method_ids_off)
    struct.pack_into("<I", header, 96, 1)  # class_defs_size
    struct.pack_into("<I", header, 100, class_defs_off)
    struct.pack_into("<I", header, 104, file_size - string_data_off)
    struct.pack_into("<I", header, 108, string_data_off)

    string_ids = b"".join(struct.pack("<I", off) for off in string_offsets)
    type_ids = b"".join(struct.pack("<I", idx) for idx in METHOD_TYPES)
    # shorty_idx, return_type_idx, parameters_off
    proto_id = struct.pack("<3I", SHORTY_STRING, VOID_TYPE, type_list_off)
    # class_idx (u2), proto_idx (u2), name_idx (u4) -- 8 bytes, not three u32.
    method_id = struct.pack("<HHI", CLASS_TYPE, 0, 3)
    assert len(method_id) == 8
    class_def = struct.pack(
        "<8I",
        CLASS_TYPE,  # class_idx
        0x1,  # ACC_PUBLIC
        1,  # superclass_idx
        0,  # interfaces_off
        2,  # source_file_idx
        0,  # annotations_off
        class_data_off,
        0,  # static_values_off
    )
    type_list = struct.pack("<I", 1) + struct.pack("<H", PARAM_TYPE) + b"\x00\x00"
    assert len(type_list) == 8 and type_list_off % 4 == 0

    body = bytearray()
    body += header + string_ids + type_ids + proto_id + method_id + class_def
    assert len(body) == string_data_off, (len(body), string_data_off)
    body += strings_blob
    assert len(body) == type_list_off or len(body) <= type_list_off
    body += b"\x00" * (type_list_off - len(body))
    body += type_list
    assert len(body) == class_data_off
    body += class_data
    body += b"\x00" * (map_off - len(body))

    map_items = [
        (TYPE_HEADER_ITEM, 1, 0),
        (TYPE_STRING_ID_ITEM, len(METHOD_STRINGS), string_ids_off),
        (TYPE_TYPE_ID_ITEM, len(METHOD_TYPES), type_ids_off),
        (TYPE_PROTO_ID_ITEM, 1, proto_ids_off),
        (TYPE_METHOD_ID_ITEM, 1, method_ids_off),
        (TYPE_CLASS_DEF_ITEM, 1, class_defs_off),
        (TYPE_STRING_DATA_ITEM, len(METHOD_STRINGS), string_data_off),
        (TYPE_TYPE_LIST, 1, type_list_off),
        (TYPE_CLASS_DATA_ITEM, 1, class_data_off),
        (TYPE_MAP_LIST, 1, map_off),
    ]
    assert map_items == sorted(map_items, key=lambda item: item[2])
    map_list = struct.pack("<I", len(map_items))
    for item_type, size, off in map_items:
        assert off % 4 == 0, (item_type, off)
        map_list += struct.pack("<HHI I", item_type, 0, size, off)
    body += map_list
    assert len(body) == file_size, (len(body), file_size)

    write_integrity(body)

    out = pathlib.Path(__file__).resolve().parent.parent / "userprog" / "classes_methods.dex"
    out.write_bytes(bytes(body))
    print(f"wrote {out} ({len(body)} bytes)")
    print(f"  checksum 0x{struct.unpack_from('<I', body, 8)[0]:08x}")
    print(f"  sha1     {hashlib.sha1(bytes(body[32:])).hexdigest()}")
    print(f"  method   main([Ljava/lang/String;)V (ACC_PUBLIC|ACC_STATIC|ACC_NATIVE, code_off 0)")
    print(f"  layout   string_ids=0x{string_ids_off:x} type_ids=0x{type_ids_off:x} "
          f"proto_ids=0x{proto_ids_off:x} method_ids=0x{method_ids_off:x} "
          f"class_defs=0x{class_defs_off:x} string_data=0x{string_data_off:x} "
          f"type_list=0x{type_list_off:x} class_data=0x{class_data_off:x} "
          f"map=0x{map_off:x}")
    return 0


# ---------------------------------------------------------------------------
# `classes_fields.dex` -- fields, a code_item, and class_data that only parses
# if its indices are read as DIFFERENTIALS.
#
# The method fixture above proves the signature tables. It cannot prove three
# things this one exists for, each of them a rule of the format rather than a
# count:
#
#   1. `field_ids` resolve to a name and a type (`COUNT:I`, `DEPTH:I`,
#      `name:Ljava/lang/String;`), so a field entry in class_data answers
#      something about the app instead of a number.
#   2. `class_data` indices are DELTAS from the previous entry in the same
#      list, and each list restarts from its own first delta. The static field
#      list here is [0, 1] (indices 0 and 1) and the instance list is [2] -- a
#      parser that treated a delta as an absolute index would read the instance
#      field as 2 and be right by accident, while one that forgot to reset the
#      accumulator between lists would read it as 1 + 2 = 3 and be REFUSED. The
#      direct method list is [0,1,1] = indices 0, 1, 2 for the same reason.
#   3. `encoded_method` is (method_idx_diff, access_flags, code_off) -- three
#      ULEB128s, not the two the earlier fixture could get away with. The third
#      is what makes a method's code reachable.
#
# So: two code_items with real Dalvik words (`const/4 v0, #0` then `return v0`
# for `size()I`; the same constant then `return-void` for `run()V`), and one
# ACC_NATIVE method with code_off 0 -- which is the format's own way of saying
# "no code", and the contrast that makes the code_off columns mean something.
# The kernel parses those words and checks their count and bounds; it does not
# decode or execute them, and the fixture is not run.
# ---------------------------------------------------------------------------

FIELD_STRINGS = [
    b"Lnyrqis/hello/Fields;",
    b"Ljava/lang/Object;",
    b"Fields.java",
    b"COUNT",
    b"DEPTH",
    b"name",
    b"I",
    b"Ljava/lang/String;",
    b"size",
    b"make",
    b"run",
    b"V",
]

# type_ids name strings: class, superclass, int, String, void.
FIELD_TYPES = [0, 1, 6, 7, 11]
F_CLASS = 0
F_INT = 2
F_STRING = 3
F_VOID = 4
F_SHORTY_I = 6
F_SHORTY_V = 11

# field_ids are (class_idx u2, type_idx u2, name_idx u4), sorted by name index
# as the format requires: COUNT (3), DEPTH (4), name (5).
FIELD_FIELDS = [(F_CLASS, F_INT, 3), (F_CLASS, F_INT, 4), (F_CLASS, F_STRING, 5)]

# method_ids are (class_idx u2, proto_idx u2, name_idx u4), sorted by name
# index: size (8), make (9), run (10).
FIELD_METHODS = [(F_CLASS, 0, 8), (F_CLASS, 1, 9), (F_CLASS, 1, 10)]

TYPE_CODE_ITEM = 0x2001

ACC_PUBLIC = 0x1
ACC_PRIVATE = 0x2
ACC_STATIC = 0x8
ACC_FINAL = 0x10
ACC_NATIVE = 0x100


def dalvik_const4_zero_return_v0() -> bytes:
    """`const/4 v0, #0` then `return v0` -- two real 16-bit code units.

    0x12 is const/4 (format 11n): the value word is op | A<<8 | B<<12, so
    v0/#0 is exactly 0x0012. 0x0f is return (11x): op | A<<8, so v0 is 0x000f.
    These are the format's encodings, written down here so the kernel's
    insns proof compares against bytes a reader can check by hand.
    """
    return struct.pack("<HH", 0x0012, 0x000F)


def dalvik_const4_zero_return_void() -> bytes:
    """`const/4 v0, #0` then `return-void` (0x0e, format 10x)."""
    return struct.pack("<HH", 0x0012, 0x000E)


def code_item(insns: bytes) -> bytes:
    """A code_item with no tries and no debug info: 16-byte header + insns."""
    assert len(insns) % 2 == 0
    insns_size = len(insns) // 2
    return struct.pack(
        "<HHHHII",
        1,  # registers_size: one register, v0
        0,  # ins_size: no parameters
        0,  # outs_size: this code calls nothing
        0,  # tries_size
        0,  # debug_info_off
        insns_size,
    ) + insns


def write_fields_fixture() -> int:
    for s in FIELD_STRINGS:
        assert all(0x20 <= b < 0x7F for b in s), s

    strings_blob = bytearray()
    for s in FIELD_STRINGS:
        strings_blob += uleb128(len(s)) + s + b"\x00"

    string_ids_off = HEADER_SIZE
    type_ids_off = string_ids_off + 4 * len(FIELD_STRINGS)
    proto_ids_off = type_ids_off + 4 * len(FIELD_TYPES)
    field_ids_off = proto_ids_off + 12 * 2
    method_ids_off = field_ids_off + 8 * len(FIELD_FIELDS)
    class_defs_off = method_ids_off + 8 * len(FIELD_METHODS)
    string_data_off = class_defs_off + 32

    string_offsets = []
    base = string_data_off
    for s in FIELD_STRINGS:
        string_offsets.append(base)
        base += 1 + len(s) + 1

    size_code_off = align4(base)
    size_code = code_item(dalvik_const4_zero_return_v0())
    run_code_off = align4(size_code_off + len(size_code))
    run_code = code_item(dalvik_const4_zero_return_void())
    class_data_off = align4(run_code_off + len(run_code))

    # counts, then (static, instance, direct) member lists. The deltas below are
    # the format's: within a list each index is the previous index plus the
    # delta, and each list starts from its own first delta.
    class_data = bytearray([2, 1, 3, 0])
    class_data += uleb128(0) + uleb128(ACC_PUBLIC | ACC_STATIC | ACC_FINAL)  # COUNT, index 0
    class_data += uleb128(1) + uleb128(ACC_PRIVATE | ACC_STATIC | ACC_FINAL)  # DEPTH, index 1
    # The instance list restarts at its own delta, so this is index 2 -- not
    # 1 + 2, which is what an accumulator that was not reset would read.
    class_data += uleb128(2) + uleb128(ACC_PRIVATE)  # name, index 2
    class_data += uleb128(0) + uleb128(ACC_PUBLIC | ACC_STATIC) + uleb128(size_code_off)
    class_data += uleb128(1) + uleb128(ACC_PUBLIC | ACC_STATIC | ACC_NATIVE) + uleb128(0)
    class_data += uleb128(1) + uleb128(ACC_PUBLIC | ACC_STATIC) + uleb128(run_code_off)
    assert class_data_off + len(class_data) <= align4(class_data_off + len(class_data))

    map_off = align4(class_data_off + len(class_data))
    map_entry_count = 11
    file_size = map_off + 4 + 12 * map_entry_count

    header = bytearray(HEADER_SIZE)
    header[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", header, 32, file_size)
    struct.pack_into("<I", header, 36, HEADER_SIZE)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<I", header, 44, 0)
    struct.pack_into("<I", header, 48, 0)
    struct.pack_into("<I", header, 52, map_off)
    struct.pack_into("<I", header, 56, len(FIELD_STRINGS))
    struct.pack_into("<I", header, 60, string_ids_off)
    struct.pack_into("<I", header, 64, len(FIELD_TYPES))
    struct.pack_into("<I", header, 68, type_ids_off)
    struct.pack_into("<I", header, 72, 2)  # proto_ids_size
    struct.pack_into("<I", header, 76, proto_ids_off)
    struct.pack_into("<I", header, 80, len(FIELD_FIELDS))
    struct.pack_into("<I", header, 84, field_ids_off)
    struct.pack_into("<I", header, 88, len(FIELD_METHODS))
    struct.pack_into("<I", header, 92, method_ids_off)
    struct.pack_into("<I", header, 96, 1)
    struct.pack_into("<I", header, 100, class_defs_off)
    struct.pack_into("<I", header, 104, file_size - string_data_off)
    struct.pack_into("<I", header, 108, string_data_off)

    string_ids = b"".join(struct.pack("<I", off) for off in string_offsets)
    type_ids = b"".join(struct.pack("<I", idx) for idx in FIELD_TYPES)
    proto_ids = struct.pack("<3I", F_SHORTY_I, F_INT, 0) + struct.pack("<3I", F_SHORTY_V, F_VOID, 0)
    field_ids = b"".join(struct.pack("<HHI", c, t, n) for c, t, n in FIELD_FIELDS)
    method_ids = b"".join(struct.pack("<HHI", c, p, n) for c, p, n in FIELD_METHODS)
    assert len(field_ids) == 24 and len(method_ids) == 24
    class_def = struct.pack(
        "<8I",
        F_CLASS,
        ACC_PUBLIC,
        1,  # superclass_idx
        0,  # interfaces_off
        2,  # source_file_idx
        0,  # annotations_off
        class_data_off,
        0,  # static_values_off
    )

    body = bytearray()
    body += header + string_ids + type_ids + proto_ids + field_ids + method_ids + class_def
    assert len(body) == string_data_off, (len(body), string_data_off)
    body += strings_blob
    body += b"\x00" * (size_code_off - len(body))
    body += size_code
    body += b"\x00" * (run_code_off - len(body))
    body += run_code
    body += b"\x00" * (class_data_off - len(body))
    body += class_data
    body += b"\x00" * (map_off - len(body))

    map_items = [
        (TYPE_HEADER_ITEM, 1, 0),
        (TYPE_STRING_ID_ITEM, len(FIELD_STRINGS), string_ids_off),
        (TYPE_TYPE_ID_ITEM, len(FIELD_TYPES), type_ids_off),
        (TYPE_PROTO_ID_ITEM, 2, proto_ids_off),
        (TYPE_FIELD_ID_ITEM, len(FIELD_FIELDS), field_ids_off),
        (TYPE_METHOD_ID_ITEM, len(FIELD_METHODS), method_ids_off),
        (TYPE_CLASS_DEF_ITEM, 1, class_defs_off),
        (TYPE_STRING_DATA_ITEM, len(FIELD_STRINGS), string_data_off),
        (TYPE_CODE_ITEM, 2, size_code_off),
        (TYPE_CLASS_DATA_ITEM, 1, class_data_off),
        (TYPE_MAP_LIST, 1, map_off),
    ]
    assert map_items == sorted(map_items, key=lambda item: item[2])
    map_list = struct.pack("<I", len(map_items))
    for item_type, size, off in map_items:
        assert off % 4 == 0, (item_type, off)
        map_list += struct.pack("<HHI I", item_type, 0, size, off)
    body += map_list
    assert len(body) == file_size, (len(body), file_size)

    write_integrity(body)

    out = pathlib.Path(__file__).resolve().parent.parent / "userprog" / "classes_fields.dex"
    out.write_bytes(bytes(body))
    print(f"wrote {out} ({len(body)} bytes)")
    print(f"  checksum 0x{struct.unpack_from('<I', body, 8)[0]:08x}")
    print(f"  sha1     {hashlib.sha1(bytes(body[32:])).hexdigest()}")
    print(f"  sha256   {hashlib.sha256(bytes(body)).hexdigest()}")
    print("  members  COUNT:I, DEPTH:I (static); name:Ljava/lang/String; (instance)")
    print("           size()I [code], make()V [native], run()V [code]")
    print(f"  layout   string_ids=0x{string_ids_off:x} type_ids=0x{type_ids_off:x} "
          f"proto_ids=0x{proto_ids_off:x} field_ids=0x{field_ids_off:x} "
          f"method_ids=0x{method_ids_off:x} class_defs=0x{class_defs_off:x} "
          f"string_data=0x{string_data_off:x} size_code=0x{size_code_off:x} "
          f"run_code=0x{run_code_off:x} class_data=0x{class_data_off:x} "
          f"map=0x{map_off:x}")
    return 0


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
    write_methods_fixture()
    write_fields_fixture()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
