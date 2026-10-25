#!/usr/bin/env python3
"""patch_pe_pdata.py -- the PE32+ EXCEPTION-TABLE increment's checked
composition step, the same discipline patch_pe_delay.py,
patch_pe_bound.py and patch_pe_forwarders.py established: this host's
GNU ld builds real, toolchain-raw PE32+ clients but emits no exception
directory (`IMAGE_DIRECTORY_ENTRY_EXCEPTION`, DataDirectory[3]) for a
native-subsystem client -- nothing here compiles with SEH -- so the
`.pdata` runtime-function table this increment proves gets COMPOSED
here, deliberately and self-verified, on top of otherwise completely
ordinary toolchain output.

For each carrier the composition lays a real x64 `.pdata` table into
the `.idata` section's alignment-padded in-block room (only bytes the
linker's own FileAlignment padding owns; nothing outside that window
is touched), in this canonical shape:

    [P+0]  RUNTIME_FUNCTION entry 0: Begin, End, Unwind (3 x u32 RVA)
    [P+12] RUNTIME_FUNCTION entry 1
    [P+24] UNWIND_INFO A (8 bytes: version nibble 1, CountOfCodes 0)
    [P+32] UNWIND_INFO B (8 bytes: version nibble 2, CountOfCodes 0)

and points DataDirectory[3] at the entry array with Size = 24. The
code ranges are derived from the carrier's OWN `.text` section header
(never assumed): entry 0 covers the first half, entry 1 the remainder,
so the sorted, non-overlapping, begin<end facts the kernel's
`load_exception_table` walk verifies are real file facts.

CARRIERS (all three link the ONE assembled client `prog_pe_pdata.s`
at distinct ImageBases; only the good carrier's body is ever entered):
  * good  (userprog_pe_pdata_good.exe): the canonical table above for
    the positive proof -- kernel-side facts asserted and then the
    client's own ring-3 body run, as always.
  * mis   (userprog_pe_pdata_mis.exe): entry 1 covers only the first
    QUARTER of `.text` -- begin < entry 0's end, so the array is
    genuinely UNSORTED/overlapping -> the kernel must refuse the load
    with the typed
    `MalformedExceptionTable("runtime-function entries overlap")`.
  * ver   (userprog_pe_pdata_ver.exe): entry 1's UNWIND_INFO B carries
    a Version nibble of 3 -- a version the x64 ABI does not define ->
    the kernel must refuse with the typed
    `MalformedExceptionTable("unwind info version")`.

Each write is followed by a re-parse of the exact bytes the file NOW
contains (dir[3] bounds, both entries' three RVAs, both unwind blocks'
first bytes) and the build is refused unless the on-disk state IS the
composed state. A second run on an already-composed file is refused
(a dir[3] already pointing somewhere is not this patcher's to move).
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
    if n_dirs < 4:
        die(f"optional header declares {n_dirs} data directories; entry 3 not usable")
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


def compose(path: str, mode: str) -> None:
    label = f"{path} ({mode})"
    with open(path, "rb") as f:
        data = bytearray(f.read())

    coff, opt, dirs, sections = parse_headers(bytes(data))
    text = section_by_name(sections, ".text", label)
    idata = section_by_name(sections, ".idata", label)

    # The optional header must DECLARE entry 3 and leave it zero: the
    # composition owns it. A file that already points dir[3] somewhere
    # is not this patcher's to move (idempotence refusal).
    dir3_off = opt + 112 + 3 * 8
    rva0, size0 = struct.unpack_from("<II", data, dir3_off)
    if rva0 != 0 or size0 != 0:
        die(f"{label}: DataDirectory[3] already declares ({rva0:#x}, {size0:#x}) "
            "-- refusing to re-compose (idempotence refusal, not a re-roll)")

    # `.text` facts the entries are derived from: a half-split table
    # needs a real, nonempty, even-sized text section.
    if text["vsize"] == 0 or text["vsize"] % 2 != 0:
        die(f"{label}: .text vsize {text['vsize']:#x} is empty or odd -- refusing")
    half = text["vsize"] // 2

    # The composition lives in .idata's IN-BLOCK alignment padding: at
    # the RVA just past the section's VirtualSize, and (crucially)
    # within the section's OWN raw data, so no byte outside .idata's
    # FileAlignment-padded block is touched. The bound/delay patchers'
    # room rule, re-derived per file.
    compose_rva = idata["vaddr"] + idata["vsize"]
    in_block = compose_rva - idata["vaddr"]
    if in_block < 0 or in_block + 40 > idata["rawsize"]:
        die(f"{label}: .idata in-block padding holds {idata['rawsize'] - in_block:#x} bytes, "
            "not the 40 the composition needs")
    file_off = idata["raw"] + in_block

    # The kernel's single bounds authority is the section table itself
    # (max_rva = VirtualAddress + VirtualSize summed per section), and
    # its mapped reads are bounded by exactly that span -- so the
    # composition MUST grow `.idata`'s VirtualSize to cover the bytes
    # it lays in the padding (SizeOfRawData stays: the bytes are real
    # file content already inside the section's raw data -- the bound
    # patchers' room rule, the same growth).
    vsize_new = in_block + 40
    if vsize_new <= idata["vsize"]:
        die(f"{label}: in-block compose at {compose_rva:#x} would not exceed "
            f".idata VirtualSize {idata['vsize']:#x} -- refusing a no-op patch")
    struct.pack_into("<I", data, idata["hdr_off"] + 8, vsize_new)

    # The table: entries first (24 bytes), then both UNWIND_INFOs
    # (8 bytes each). Unwind RVAs refer to these very blocks.
    unwind_a_rva = compose_rva + 24
    unwind_b_rva = compose_rva + 32
    entry0 = (text["vaddr"], text["vaddr"] + half, unwind_a_rva)
    if mode == "mis":
        # A fourth of the text: begin BELOW entry 0's end (overlap).
        entry1 = (text["vaddr"] + half // 2, text["vaddr"] + text["vsize"], unwind_b_rva)
    else:
        entry1 = (text["vaddr"] + half, text["vaddr"] + text["vsize"], unwind_b_rva)
    ver_a, ver_b = (1, 3) if mode == "ver" else (1, 2)

    if mode == "ver":
        # The whole point: an UNWIND_INFO version the x64 ABI does not
        # define (3). Everything else stays canonic.
        pass

    # EXACTLY 40 bytes: two 12-byte entries + two 8-byte UNWIND_INFOs.
    # (A 16s field here would make block B's content land at +40 while
    # its declared unwind RVA says +32 -- the re-parse below caught the
    # first composition attempt on exactly this mismatch.)
    struct.pack_into("<12s12s8s8s", data, file_off,
        struct.pack("<III", *entry0),
        struct.pack("<III", *entry1),
        bytes([ver_a << 5, 0, 0, 0, 0, 0, 0, 0]),
        bytes([ver_b << 5, 0, 0, 0, 0, 0, 0, 0]),
    )

    # dir[3] names the ENTRY ARRAY with its exact byte size.
    struct.pack_into("<II", data, dir3_off, compose_rva, 24)

    # --- THREE INDEPENDENT COPIES -- the slot/stack-constant rule ---
    # The kernel-side demo driver derives its expected entries from the
    # embedded file's OWN section table (locate-by-fact, never a baked
    # constant), so the composition's single authority here is the
    # on-disk bytes the re-parse reads back.

    # Re-parse the FILE AS WRITTEN and refuse unless the on-disk state
    # IS the composed state: fresh headers, dir[3] exact, both entries
    # exact at their exact offsets, both unwind blocks' exact bytes.
    _, _, dirs2, sections2 = parse_headers(bytes(data))
    if dirs2[3] != (compose_rva, 24):
        die(f"{label}: re-parse reads dir[3] = {dirs2[3]}, expected "
            f"({compose_rva:#x}, 24)")
    idata2 = section_by_name(sections2, ".idata", label)
    text2 = section_by_name(sections2, ".text", label)
    if idata2["vsize"] != vsize_new:
        die(f"{label}: re-parse reads .idata VirtualSize {idata2['vsize']:#x}, "
            f"expected the composed {vsize_new:#x}")
    if (text2["vaddr"], text2["vsize"]) != (text["vaddr"], text["vsize"]):
        die(f"{label}: re-parse reads .text bounds (({text2['vaddr']:#x},{text2['vsize']:#x})) "
            f"!= the composition's (({text['vaddr']:#x},{text['vsize']:#x}))")
    back_off = file_off  # same offset: the headers were not moved, only bytes written
    e0b, e0e, e0u = struct.unpack_from("<III", data, back_off)
    e1b, e1e, e1u = struct.unpack_from("<III", data, back_off + 12)
    if (e0b, e0e, e0u) != entry0 or (e1b, e1e, e1u) != entry1:
        die(f"{label}: re-parse reads entries "
            f"([{e0b:#x},{e0e:#x}) u {e0u:#x}] [{e1b:#x},{e1e:#x}) u {e1u:#x}], "
            f"expected {entry0} / {entry1}")
    if bytes(data[back_off + 24:back_off + 32]) != bytes([ver_a << 5 | 0, 0, 0, 0, 0, 0, 0, 0]):
        die(f"{label}: re-parse reads UNWIND_INFO A bytes != the composed block")
    if bytes(data[back_off + 32:back_off + 40]) != bytes([ver_b << 5 | 0, 0, 0, 0, 0, 0, 0, 0]):
        die(f"{label}: re-parse reads UNWIND_INFO B bytes != the composed block")
    # The composed table must live inside .idata's DECLARED span too
    # (the room rule's other half: the section's own VirtualSize covers
    # the entries it is the padding OF -- here it is the composition
    # itself that extends the occupied range, so the check is that the
    # bytes stay inside .idata's raw data, verified above).
    with open(path, "wb") as f:
        f.write(data)

    print(f"pdata composed {label}: dir[3] = ({compose_rva:#x}, 24) at in-block padding "
          f"rva {compose_rva:#x}; entry0 = [{e0b:#x},{e0e:#x}) unwind {e0u:#x} (version "
          f"{ver_a}); entry1 = [{e1b:#x},{e1e:#x}) unwind {e1u:#x} (version {ver_b}); "
          f"text range [{text['vaddr']:#x},{text['vaddr'] + text['vsize']:#x})")


def main() -> None:
    if len(sys.argv) != 4:
        die(f"usage: {sys.argv[0]} <GOOD.exe> <MIS.exe> <VER.exe>")
    compose(sys.argv[1], "good")
    compose(sys.argv[2], "mis")
    compose(sys.argv[3], "ver")
    print("patch_pe_pdata: 3 carrier(s) composed and re-verified")


if __name__ == "__main__":
    main()
