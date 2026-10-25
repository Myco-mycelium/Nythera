#!/usr/bin/env python3
"""patch_pe_res.py -- the PE32+ RESOURCE-DIRECTORY increment's checked
composition step, the same discipline patch_pe_pdata.py established on
top of the delay/bound/forwarders patchers: this host's GNU ld builds
real, toolchain-raw PE32+ clients but emits no resource directory
(`IMAGE_DIRECTORY_ENTRY_RESOURCE`, DataDirectory[2]) for a
native-subsystem client -- nothing here declares a .rsrc section -- so
the resource TREE this increment proves gets COMPOSED here, deliberately
and self-verified, in each carrier's `.idata` in-block alignment
padding (with the VirtualSize growth the kernel's bounds authority
requires), on top of otherwise completely ordinary toolchain output.

For the good carrier the composition lays the resource tree's
legitimate minimal shape (one named type entry -> one subdirectory ->
one ID entry -> one DATA_ENTRY leaf):

    [P+0]  IMAGE_RESOURCE_DIRECTORY header L1 (16 bytes:
           Characteristics/TimeStamp 8, Version 4, named=1, id=0)
    [P+16] L1 ENTRY 0 (8 bytes): name flag + name offset (named
           "TXT"), offset-to-data = subdirectory (high bit set)
    [P+24] name table blob: u16 length 3 + "T","X","T" in UTF-16LE
           (8 bytes, aligned)
    [P+32] IMAGE_RESOURCE_DIRECTORY header L2 (16 bytes: named=0,
           id=1)
    [P+48] L2 ENTRY 0 (8 bytes): ID (name_or_id = 1) ,
           offset-to-data = leaf (high bit clear)
    [P+56] IMAGE_RESOURCE_DATA_ENTRY (16 bytes): DataRVA (pointing at
           the string blob below), Size, CodePage 0, Reserved 0
    [P+72] the leaf's data bytes (the resource payload string)

and points DataDirectory[2] at the L1 header with the exact byte size.
Everything past the good shape stays canonic -- the refusal carriers
break exactly ONE typed invariant each:

  * good (userprog_pe_res_good.exe): the canonical tree above; the
    kernel walks it, states the facts, and the client's ring-3 body
    runs, as always.
  * rbad (userprog_pe_res_bad.exe): the DATA_ENTRY's Size field runs
    the leaf's bytes PAST the image's own mapped footprint -- the
    kernel must refuse the load with the typed
    `MalformedResourceDirectory("resource data beyond the image's mapped footprint")`.
  * rname (userprog_pe_res_rname.exe): L1's named entry names a blob
    whose u16 length is 0 -- an EMPTY resource name, which the walk
    refuses with `MalformedResourceDirectory("empty resource name")`;
    everything else stays canonic so the refusal names exactly one
    cause.
  * rmany (userprog_pe_res_rmany.exe): L1 claims 17 ID entries (named
    0, id 17), past the walk's 16-entry cap -- refused with
    `MalformedResourceDirectory("resource directory entry array unbounded")`.

Each write is followed by a re-parse of the exact bytes the file NOW
contains (dir[2] bounds, both headers, the entry fields, the name blob,
and the DATA_ENTRY leaf) and the build is refused unless the on-disk
state IS the composed state. A second run on an already-composed file
is refused (a dir[2] already pointing somewhere is not this patcher's
to move).
"""

import struct
import sys


def die(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


def parse_headers(data: bytes):
    """MZ -> e_lfanew -> PE -> COFF -> optional -> data dirs -> section table."""
    if data[0:2] != b"MZ":
        die("not a PE file (bad DOS magic)")
    (e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        die("bad PE signature")
    coff = e_lfanew + 4
    (nsec,) = struct.unpack_from("<H", data, coff + 2)
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    (n_dirs,) = struct.unpack_from("<I", data, opt + 108)
    if n_dirs < 3:
        die(f"optional header declares {n_dirs} data directories; entry 2 not usable")
    dirs = []
    for i in range(n_dirs):
        rva, size = struct.unpack_from("<II", data, opt + 112 + i * 8)
        dirs.append((rva, size))
    sections = []
    sh_off = opt + opt_size
    for i in range(nsec):
        b = sh_off + i * 40
        name = data[b:b + 8].rstrip(b"\x00").decode("ascii", "replace")
        vsize, vaddr, rawsize, raw = struct.unpack_from("<IIII", data, b + 8)
        sections.append({
            "hdr_off": b, "name": name, "vsize": vsize, "vaddr": vaddr,
            "rawsize": rawsize, "raw": raw,
        })
    return coff, opt, dirs, sections


def section_by_name(sections, want: str, label: str):
    hits = [s for s in sections if s["name"] == want]
    if len(hits) != 1:
        die(f"{label}: expected exactly one section named {want!r}, found {len(hits)}")
    return hits[0]


def dir2_off(opt: int) -> int:
    return opt + 112 + 2 * 8


# The composed tree's ENTIRE byte count (the good shape; refusal
# carriers never need more than this):
#   L1 header 16 + L1 entry 8 + name blob 8 + L2 header 16 +
#   L2 entry 8 + DATA_ENTRY 16 + leaf string 8 = 80 bytes.
TREE_BYTES = 80
NAME_BLOB_OFF = 24
L2_DIR_OFF = 32
L2_ENTRY_OFF = 48
DATA_ENTRY_OFF = 56
DATA_OFF = 72


def compose(path: str, mode: str) -> None:
    label = f"{path} ({mode})"
    with open(path, "rb") as f:
        data = bytearray(f.read())

    coff, opt, dirs, sections = parse_headers(bytes(data))
    idata = section_by_name(sections, ".idata", label)
    text = section_by_name(sections, ".text", label)

    # The optional header must DECLARE entry 2 and leave it zero: the
    # composition owns it. A file that already points dir[2] somewhere
    # is not this patcher's to move (idempotence refusal).
    d2 = dir2_off(opt)
    rva0, size0 = struct.unpack_from("<II", data, d2)
    if rva0 != 0 or size0 != 0:
        die(f"{label}: DataDirectory[2] already declares ({rva0:#x}, {size0:#x}) "
            "-- refusing to re-compose (idempotence refusal, not a re-roll)")

    # The composition lives in .idata's IN-BLOCK alignment padding: at
    # the RVA just past the section's VirtualSize, and within the
    # section's OWN raw data, so no byte outside .idata's
    # FileAlignment-padded block is touched.
    compose_rva = idata["vaddr"] + idata["vsize"]
    in_block = compose_rva - idata["vaddr"]
    if in_block < 0 or in_block + TREE_BYTES > idata["rawsize"]:
        die(f"{label}: .idata in-block padding holds {idata['rawsize'] - in_block:#x} bytes, "
            f"not the {TREE_BYTES} the composition needs")
    file_off = idata["raw"] + in_block

    # The kernel's bounds authority is the section table (max_rva =
    # VirtualAddress + VirtualSize per section), so the composition
    # MUST grow `.idata`'s VirtualSize to cover the bytes it lays in
    # the padding (SizeOfRawData stays: the bytes are real file
    # content already inside the section's raw data).
    vsize_new = in_block + TREE_BYTES
    if vsize_new <= idata["vsize"]:
        die(f"{label}: in-block compose at {compose_rva:#x} would not exceed "
            f".idata VirtualSize {idata['vsize']:#x} -- refusing a no-op patch")
    struct.pack_into("<I", data, idata["hdr_off"] + 8, vsize_new)

    def put(off, fmt, *vals):
        struct.pack_into(fmt, data, file_off + off, *vals)

    # L1 header: Characteristics 0, TimeStamp 0, version 4.0, and the
    # counts: the good shape composes named=1/id=0; the over-cap
    # refusal composes named=0/id=17 (nothing else differs).
    good_counts = (1, 0)
    named_l1, id_l1 = (0, 17) if mode == "rmany" else good_counts
    put(0, "<IIHHHH", 0, 0, 4, 0, named_l1, id_l1)

    # The composed tree (everything relative to compose_rva):
    name_off_rva = NAME_BLOB_OFF                 # relative to root
    subdir_off = L2_DIR_OFF | (1 << 31)          # relative to root
    dataentry_off = DATA_ENTRY_OFF               # relative to root
    if mode != "rmany":
        # L1 ENTRY 0: name is a NAMED entry (high bit + offset),
        # child is the subdirectory.
        put(16, "<II", (1 << 31) | name_off_rva, subdir_off)
    # Name blob: u16 length + UTF-16LE "TXT" (or length 0 for rname).
    if mode == "rname":
        put(NAME_BLOB_OFF, "<HHHH", 0, 0, 0, 0)
    else:
        # The composed "TXT" string: 3 wchars, 8-byte-aligned blob.
        put(NAME_BLOB_OFF, "<HHHH", 3, ord("T"), ord("X"), ord("T"))
    # L2 header: named 0, id 1.
    if mode != "rmany":
        put(L2_DIR_OFF, "<IIHHHH", 0, 0, 4, 0, 0, 1)
        # L2 ENTRY 0: ID entry (no flag), child the DATA_ENTRY leaf.
        put(L2_ENTRY_OFF, "<II", 1, dataentry_off)
        # DATA_ENTRY leaf: DataRVA (string blob rva), Size, code page
        # 0, reserved 0. The string blob sits at compose_rva+DATA_OFF.
        string_rva = compose_rva + DATA_OFF
        text_rva = text["vaddr"]
        text_end = text_rva + text["vsize"]
        # rbad: the Size runs past the image's own footprint -- the
        # data-entry's bytes overwrite the map. (Composition keeps
        # DataRVA canonic so ONLY the size is the contradiction.)
        size = text_end - string_rva + 0x10000 if mode == "rbad" else 8
        put(DATA_ENTRY_OFF, "<IIII", string_rva, size, 0, 0)
        # The leaf bytes: the LAST 8 bytes of the 80-byte block (the
        # string ends the block exactly so the good tree's own leaf
        # fits its own grown .idata VirtualSize -- a 16-byte payload
        # would refuse the good carrier itself).
        put(DATA_OFF, "<8s", b"rsrcpay!")

    # dir[2] names the L1 header with the composed tree's byte size.
    struct.pack_into("<II", data, d2, compose_rva, TREE_BYTES)

    # --- the re-parse: the on-disk state must BE the composed state ---
    _, _, dirs2, sections2 = parse_headers(bytes(data))
    if dirs2[2] != (compose_rva, TREE_BYTES):
        die(f"{label}: re-parse reads dir[2] = {dirs2[2]}, expected "
            f"({compose_rva:#x}, {TREE_BYTES})")
    idata2 = section_by_name(sections2, ".idata", label)
    if idata2["vsize"] != vsize_new:
        die(f"{label}: re-parse reads .idata VirtualSize {idata2['vsize']:#x}, "
            f"expected the composed {vsize_new:#x}")
    header_named = struct.unpack_from("<H", data, file_off + 12)[0]
    header_id = struct.unpack_from("<H", data, file_off + 14)[0]
    if (header_named, header_id) != good_counts and (header_named, header_id) != (named_l1, id_l1):
        die(f"{label}: re-parse reads L1 counts ({header_named},{header_id}), expected ({named_l1},{id_l1})")
    if mode != "rmany":
        n_off = struct.unpack_from("<I", data, file_off + 16)[0]
        c_off = struct.unpack_from("<I", data, file_off + 20)[0]
        if n_off != (1 << 31) | name_off_rva or c_off != subdir_off:
            die(f"{label}: re-parse reads L1 entry ({n_off:#x},{c_off:#x}), composed "
                f"({(1 << 31) | name_off_rva:#x},{subdir_off:#x})")
        (nlen,) = struct.unpack_from("<H", data, file_off + NAME_BLOB_OFF)
        if nlen != (0 if mode == "rname" else 3):
            die(f"{label}: re-parse reads the name length {nlen}, composed "
                f"{0 if mode == 'rname' else 3}")
        l2_named = struct.unpack_from("<H", data, file_off + L2_DIR_OFF + 12)[0]
        l2_id = struct.unpack_from("<H", data, file_off + L2_DIR_OFF + 14)[0]
        if (l2_named, l2_id) != (0, 1):
            die(f"{label}: re-parse reads L2 counts ({l2_named},{l2_id}), expected (0,1)")
        d_rva, d_size = struct.unpack_from("<II", data, file_off + DATA_ENTRY_OFF)
        if d_rva != string_rva:
            die(f"{label}: re-parse reads the DATA_ENTRY rva {d_rva:#x}, composed {string_rva:#x}")
        if mode == "good" and d_size != 8:
            die(f"{label}: re-parse reads the DATA_ENTRY size {d_size:#x}, composed 8")
    with open(path, "wb") as f:
        f.write(data)

    kind = {
        "good": "the canonical 1-named -> 1-subdir -> 1-ID -> data-leaf tree",
        "rbad": "a DATA_ENTRY whose Size runs past the image footprint",
        "rname": "a named entry whose length-prefixed string is EMPTY",
        "rmany": "an L1 entry array over the walk's 16-entry cap (17 IDs)",
    }[mode]
    print(f"resource composed {label}: dir[2] = ({compose_rva:#x}, {TREE_BYTES}) at in-block "
          f"padding rva {compose_rva:#x}; {kind}")


def main() -> None:
    if len(sys.argv) != 5:
        die(f"usage: {sys.argv[0]} <GOOD.exe> <RBAD.exe> <RNAME.exe> <RMANY.exe>")
    compose(sys.argv[1], "good")
    compose(sys.argv[2], "rbad")
    compose(sys.argv[3], "rname")
    compose(sys.argv[4], "rmany")
    print("patch_pe_res: 4 carrier(s) composed and re-verified")


if __name__ == "__main__":
    main()
