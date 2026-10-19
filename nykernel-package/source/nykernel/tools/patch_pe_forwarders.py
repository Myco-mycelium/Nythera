#!/usr/bin/env python3
"""patch_pe_forwarders.py -- the PE32+ import-resolution increment's
checked composition step, exactly the discipline the weak-VERNEED
patcher established (tools/patch_weak_verneed.py): this host's GNU ld
builds real DLLs and real dependent clients but emits neither
forwarded-export strings nor ordinal-form import thunks naturally, so
the two structures this increment proves get COMPOSED here, deliberately
and self-verified, on top of otherwise completely ordinary toolchain
output.

Usage: patch_pe_forwarders.py <fwdprov_dll> <defining_provider_dll> <client_exe>

Provider side (the forwarder host built from prog_pe_import_fwdprov.s):
  * parses its REAL headers (DOS -> PE -> COFF -> optional -> sections
    -> data directories -> export directory) from the file bytes,
  * extends the .edata section's VirtualSize and SizeOfRawData into the
    file's post-section RAW PADDING (verified free: every byte of the
    extension must lie before the next section's PointerToRawData or
    the file end, never inside another section's bytes) so the kernel
    loader maps and copies the composed bytes for real,
  * grows the export directory's Size field to cover the composition --
    a forwarded-export string must live inside
    [dir_rva, dir_rva + dir_size) BY SPEC, so a grown declared size is
    legitimate composition, not a lie a reader must tolerate,
  * rewrites the three exports' AddressOfFunctions entries to point at
    the composed strings (an EAT value inside the directory is
    forwarded, per the exact rule the kernel's loader applies),
  * and writes three NUL-terminated strings:
      "userprog_pe_import_dll.dll.crossfn"           -- the real chain:
        provider A's crossfn, resolvable through the registry,
      "userprog_pe_import_fwdprov.dll.crossfn_cycle" -- a self-cycle:
        the `ForwarderDepthExceeded` refusal,
      "nobody.dll.crossfn"                           -- an unregistered
        DLL name: the `MissingDllImport` refusal.
  * then re-parses THE FILE IT WROTE and refuses the build unless the
    on-disk state is exactly the composed state.

Client side:
  * finds the import descriptor for the provider DLL, walks the
    OriginalFirstThunk array, locates the crossmark entry by reading
    its REAL IMAGE_IMPORT_BY_NAME hint/name, and rewrites that OFT
    entry into ordinal form:
      0x8000000000000000 | (OrdinalBase + EAT index of crossmark),
    with the ordinal computed independently from the PROVIDER's export
    directory (name -> via its own ordinal table), never assumed.
  * self-verifies by re-reading the entry and checking the exact bits.

OrdinalBase is READ from the provider's directory header, not assumed:
if a future toolchain changes the base, this patcher and the kernel
(which parses the very same field) both stay correct.
"""

import struct
import sys

ORDINAL_FLAG64 = 1 << 63
MAX_THUNKS = 32
MAX_DESCRIPTORS = 16


def die(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


def parse_headers(data: bytes):
    """DOS -> PE -> COFF -> optional -> data dirs -> section table."""
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
    return dirs, sections


def section_containing(sections, rva):
    for s in sections:
        if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rawsize"]):
            return s
    return None


def read_cstring(data, file_off, section_end_file_off):
    """NUL-terminated read against the section's own file span: a name
    with no terminator inside its section is malformed, never
    truncated."""
    nul = data.find(b"\x00", file_off, section_end_file_off)
    if nul < 0:
        die("a name/string with no NUL inside its section")
    return data[file_off:nul]


def read_export_dir(data: bytes, dirs, sections):
    rva, size = dirs[0]
    if rva == 0 or size == 0:
        die("provider has no export directory to compose into")
    s = section_containing(sections, rva)
    if s is None or s["name"] != ".edata":
        die("the export directory does not resolve to the .edata section")
    # RVAs are translated through the section map -- RVAs are NEVER
    # file offsets in a crafted-file patch.
    #
    # IMAGE_EXPORT_DIRECTORY layout: Flags @0, TimeDateStamp @4,
    # Major/Minor @8, Name @12, OrdinalBase @16, NumberOfFunctions @20,
    # NumberOfNames @24, AddressOfFunctions @28, AddressOfNames @32,
    # AddressOfNameOrdinals @36.
    dir_foff = s["raw"] + (rva - s["vaddr"])
    (ordinal_base,) = struct.unpack_from("<I", data, dir_foff + 16)
    (nfun,) = struct.unpack_from("<I", data, dir_foff + 20)
    (nnames,) = struct.unpack_from("<I", data, dir_foff + 24)
    eat_rva, names_rva, ords_rva = struct.unpack_from("<III", data, dir_foff + 28)
    return {
        "dir_rva": rva, "dir_size": size,
        "number_of_functions": nfun, "number_of_names": nnames,
        "ordinal_base": ordinal_base,
        "eat_rva": eat_rva, "names_rva": names_rva, "ords_rva": ords_rva,
        "section": s,
    }


def export_index_by_name(data: bytes, sections, export_dir, name: bytes):
    """EAT index for `name`, via the directory's own names/ordinal
    tables -- computed from the real file, never assumed."""
    for i in range(export_dir["number_of_names"]):
        # the name-POINTER table entry is itself an RVA; translate it
        npr_off, np_sec = rva_to_file(sections, export_dir["names_rva"] + i * 4)
        (p,) = struct.unpack_from("<I", data, npr_off)
        s = section_containing(sections, p)
        if s is None:
            die(f"export name pointer rva {p:#x} outside any section")
        at = s["raw"] + (p - s["vaddr"])
        candidate = read_cstring(data, at, s["raw"] + s["rawsize"])
        if candidate == name:
            ordt_off, _ = rva_to_file(sections, export_dir["ords_rva"] + i * 2)
            (idx,) = struct.unpack_from("<H", data, ordt_off)
            return idx
    die(f"export name {name!r} is absent from the provider's directory")


def dir_size_field_off(data: bytes) -> int:
    """File offset of the export directory header's Size field
    (DataDirectory[0].Size)."""
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    return opt + 112 + 0 * 8 + 4


def rva_to_file(sections, rva):
    s = section_containing(sections, rva)
    if s is None:
        die(f"rva {rva:#x} is not inside any section")
    return s["raw"] + (rva - s["vaddr"]), s


def patch_provider(path: str, strings, names) -> None:
    data = bytearray(open(path, "rb").read())
    dirs, sections = parse_headers(bytes(data))
    x = read_export_dir(data, dirs, sections)
    edata = x["section"]

    for n in names:
        idx = export_index_by_name(bytes(data), sections, x, n)
        eat_off, _ = rva_to_file(sections, x["eat_rva"] + idx * 4)
        (cur,) = struct.unpack_from("<I", data, eat_off)
        if x["dir_rva"] <= cur < x["dir_rva"] + x["dir_size"]:
            die(f"{n!r}'s EAT entry already points inside the directory (double patch?)")

    want = sum(len(s2) + 1 for s2 in strings)
    # The composed strings are FIRST tried INSIDE the section's own
    # alignment-padded raw block (rawsize is FileAlignment-rounded in
    # this toolchain; the padding after the directory's real content
    # is already the section's own file bytes and already covered by
    # SizeOfRawData, so only VirtualSize -- the mapping/bounds input --
    # has to grow). Only when the block has no room does the patcher
    # extend SizeOfRawData into the inter-section padding, and that
    # extension is verified to touch nobody else's bytes.
    block_room = edata["rawsize"] - edata["vsize"]
    if block_room >= want:
        print(f"  patcher: .edata in-block room {block_room:#x} bytes (rawsize {edata['rawsize']:#x}, vsize {edata['vsize']:#x}) covers the {want} composed bytes -- SizeOfRawData stays")
        string_base_foff = edata["raw"] + edata["vsize"]
        new_rawsize = edata["rawsize"]
    else:
        print(f"  patcher: in-block room {block_room:#x} < {want}; falling back to a SizeOfRawData extension")
        lower_bounds = [
            s2["raw"] for s2 in sections
            if s2 is not edata and s2["raw"] > edata["raw"] + edata["rawsize"]
        ]
        ceiling = min(lower_bounds) if lower_bounds else len(data)
        gap = ceiling - (edata["raw"] + edata["rawsize"])
        if gap < want:
            die(f"need {want} bytes for the composed strings; in-block {block_room:#x} + gap {gap:#x} is short")
        string_base_foff = edata["raw"] + edata["rawsize"]
        new_rawsize = edata["rawsize"] + want

    string_rvas = []
    cursor = 0
    for s2 in strings:
        at = string_base_foff + cursor
        data[at:at + len(s2) + 1] = s2 + b"\x00"
        string_rvas.append(edata["vaddr"] + (at - edata["raw"]))
        cursor += len(s2) + 1

    needed_dir_end = max(rva + len(s2) + 1 for rva, s2 in zip(string_rvas, strings))
    new_dir_size = max(x["dir_size"], needed_dir_end - x["dir_rva"])
    for rva in string_rvas:
        if not (x["dir_rva"] <= rva < x["dir_rva"] + new_dir_size):
            die(f"composed string rva {rva:#x} is not inside the grown directory bounds")
    new_vsize = max(edata["vsize"], needed_dir_end - edata["vaddr"])

    # Section header: VirtualSize then SizeOfRawData. VirtualAddress
    # and PointerToRawData stay put; only the sizes grow.
    struct.pack_into("<I", data, edata["hdr_off"] + 8, new_vsize)
    struct.pack_into("<I", data, edata["hdr_off"] + 16, new_rawsize)
    # The export directory header's declared Size:
    struct.pack_into("<I", data, dir_size_field_off(bytes(data)), new_dir_size)
    # EAT entries -> the composed forwarder strings:
    for k, name in enumerate(names):
        idx = export_index_by_name(bytes(data), sections, x, name)
        eat_off, _ = rva_to_file(sections, x["eat_rva"] + idx * 4)
        struct.pack_into("<I", data, eat_off, string_rvas[k])

    # Self-verify by re-parsing what we wrote:
    vdata = bytes(data)
    d2, sec2 = parse_headers(vdata)
    v = read_export_dir(vdata, d2, sec2)
    if v["dir_size"] != new_dir_size:
        die("re-parse: the grown directory Size did not take")
    for k, name in enumerate(names):
        idx = export_index_by_name(vdata, sec2, v, name)
        eat_off, _ = rva_to_file(sec2, v["eat_rva"] + idx * 4)
        (f_rva,) = struct.unpack_from("<I", vdata, eat_off)
        if not (v["dir_rva"] <= f_rva < v["dir_rva"] + v["dir_size"]):
            die(f"re-parse: {name!r}'s EAT entry is not a directory-internal forwarder")
        s = section_containing(sec2, f_rva)
        at = s["raw"] + (f_rva - s["vaddr"])
        if vdata[at:at + len(strings[k]) + 1] != strings[k] + b"\x00":
            die(f"re-parse: {name!r}'s forwarder string bytes are not the composed bytes")
    print(f"  patcher: provider EAT entries -> directory-internal forwarders ({len(names)}), strings verified by re-parse -- OK")
    open(path, "wb").write(vdata)


def patch_client(path: str, prov_a_path: str, dll_name: bytes, symbol: bytes) -> None:
    """The client's crossmark OFT thunk becomes ordinal form, with the
    ordinal computed from the file that DEFINES it (the primary
    provider), as spec'd."""
    data = bytearray(open(path, "rb").read())
    dirs, sections = parse_headers(bytes(data))
    imp_rva, _imp_size = dirs[1]

    # The export facts, read from the DEFINING provider (provider A,
    # which exports crossmark -- not the forwarder host):
    pdata = open(prov_a_path, "rb").read()
    pdirs, psecs = parse_headers(pdata)
    pexport = read_export_dir(pdata, pdirs, psecs)
    mark_idx = export_index_by_name(pdata, psecs, pexport, symbol)
    thunk_ordinal = pexport["ordinal_base"] + mark_idx

    # Locate the provider's import descriptor in the client:
    thunk_rva = None
    for desc in range(MAX_DESCRIPTORS):
        d = imp_rva + desc * 20
        s = section_containing(sections, d)
        if s is None:
            die("import descriptor array ran outside any section")
        at = s["raw"] + (d - s["vaddr"])
        end = s["raw"] + s["rawsize"]
        if at + 20 > end:
            die("a 20-byte descriptor would run past its section's raw span")
        oft, _ts, _fc, name_rva, ft = struct.unpack_from("<IIIII", data, at)
        if oft == 0 and name_rva == 0 and ft == 0:
            die(f"no import descriptor names {dll_name!r}")
        s2 = section_containing(sections, name_rva)
        if s2 is None:
            die("a descriptor's DLL-name rva is not inside any section")
        at2 = s2["raw"] + (name_rva - s2["vaddr"])
        nm = read_cstring(data, at2, s2["raw"] + s2["rawsize"])
        if nm == dll_name:
            thunk_rva = oft if oft != 0 else ft
            break
    if thunk_rva is None:
        die("the descriptor loop exhausted without the provider descriptor")

    # the thunk array is addressed by RVA too -- translate once:
    thunk_foff, _tsec = rva_to_file(sections, thunk_rva)
    j = 0
    target = None
    th = None
    while True:
        (th,) = struct.unpack_from("<Q", data, thunk_foff + j * 8)
        if th == 0:
            die(f"{symbol!r} is not named in the provider's thunk array")
        if th & ORDINAL_FLAG64:
            die("a thunk entry is ALREADY ordinal form (double patch?)")
        s2 = section_containing(sections, th + 2)
        if s2 is None:
            die("a thunk's hint/name rva is not inside any section")
        at2 = s2["raw"] + (th + 2 - s2["vaddr"])
        nm = read_cstring(data, at2, s2["raw"] + s2["rawsize"])
        if nm == symbol:
            target = thunk_foff + j * 8
            break
        j += 1
        if j >= MAX_THUNKS:
            die("thunk array unbounded")
    new_th = ORDINAL_FLAG64 | thunk_ordinal
    struct.pack_into("<Q", data, target, new_th)
    (reth,) = struct.unpack_from("<Q", bytes(data), target)
    if reth != new_th or (reth >> 63) != 1:
        die("re-read: the ordinal-form OFT entry did not take")
    if (reth & ~ORDINAL_FLAG64) != thunk_ordinal:
        die("re-read: the ordinal value is not the computed one")
    print(f"  patcher: client {symbol!r} OFT thunk -> ordinal form (ordinal {thunk_ordinal}, OrdinalBase read from the provider file) -- OK")
    open(path, "wb").write(bytes(data))


def main() -> None:
    if len(sys.argv) != 4:
        die("usage: patch_pe_forwarders.py <fwdprov_dll> <defining_provider_dll> <client_exe>")
    fwd_path, def_path, client_path = sys.argv[1], sys.argv[2], sys.argv[3]

    strings = (
        b"userprog_pe_import_dll.dll.crossfn",
        b"userprog_pe_import_fwdprov.dll.crossfn_cycle",
        b"nobody.dll.crossfn",
    )
    names = (b"crossfn_fwd", b"crossfn_cycle", b"crossfn_nodll")

    patch_provider(fwd_path, strings, names)
    patch_client(client_path, def_path, b"userprog_pe_import_dll.dll", b"crossmark")


if __name__ == "__main__":
    main()
