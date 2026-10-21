#!/usr/bin/env python3
"""patch_pe_delay.py -- the PE32+ DELAY-LOAD increment's checked
composition step, the same discipline patch_pe_bound.py and
patch_pe_forwarders.py established: this host's GNU ld builds real DLLs
and real dependent clients but emits no delay-load directory, so the
structure this increment proves gets COMPOSED here, deliberately and
self-verified, on top of otherwise completely ordinary toolchain
output.

Each carrier (a real, no-import GNU-ld-built PE32+ client) gets a
composed MS-spec delay-load directory (`IMAGE_DELAYLOAD_DESCRIPTOR`,
8 DWORD fields per delayimp.h) laid out in the .idata section's own
alignment-padded in-block room (only VirtualSize grows; SizeOfRawData
stays -- the room rule the forwarders/bound patchers verified) in this
canonical order:

    [+0]  the descriptor's 8 DWORD fields, RVA form (grAttrs = 0x1 =
          dlattrRva, the ONLY documented attribute; pre-VC7 VA-form
          descriptors are composed NOWHERE)
    [+32] an all-zero 32-byte terminator descriptor
    [+64] the DLL name cstring
    [..]  the module-handle qword (0 -- the kernel's eager ruling
          writes the provider's mapped base here)
    [..]  the IAT array: n hint/name (or ordinal) entries + null
    [..]  the INT array: mirrored entry-for-entry (the unbound
          pre-state the kernel's walk requires: INT == IAT always)
    [..]  the named imports' hint/name blobs (u16 hint 0 + name)
dir[13] points at the descriptor; the span covers everything composed.

CARRIERS:
  * delaygood (userprog_pe_delay_good.exe): ONE descriptor naming the
    REAL provider with TWO imports (named crossfn; ordinal crossmark
    as IMAGE_ORDINAL_FLAG64 | ordinal, the ordinal computed from the
    provider's OWN directory), rvaBoundIAT/rvaUnloadIAT zero,
    dwTimeStamp 0. Expected: both slots EAGER-RESOLVE through the
    same registry the normal walk uses.
  * delaybad  (userprog_pe_delay_bad.exe): the same descriptor naming
    'nowhere.dll' -> the typed MissingDelayDllImport refusal.
  * delaymut  (userprog_pe_delay_mut.exe): delaygood's composition
    except ONE composed u32 -- dwTimeStamp = provider stamp + 1, the
    old-BIND marker: a binding guarantee the kernel does not honor
    and never silently ignores -> typed MalformedDelayImport refusal.

The RESULT is re-parsed (exact dir[13] bounds, exact fields at exact
offsets, exact name strings, exact IAT==INT arrays and terminators)
and the build is refused unless the on-disk state IS the composed
state.
"""

import struct
import sys

PROVIDER_IMAGE_BASE = 0x180000000000
ORDINAL_FLAG64 = 1 << 63
PROVIDER_NAME = b"userprog_pe_import_dll.dll"


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
    if n_dirs < 14:
        die(f"optional header declares {n_dirs} data directories; entry 13 not usable")
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


def image_base_of(data: bytes) -> int:
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    return struct.unpack_from("<Q", data, opt + 24)[0]


def dir13_entry_off(data: bytes) -> int:
    """File offset of DataDirectory[13]'s 8-byte (RVA, Size) entry."""
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    return opt + 112 + 13 * 8


def rva_to_file(sections, rva):
    for s in sections:
        if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rawsize"]):
            return s["raw"] + (rva - s["vaddr"]), s
    die(f"rva {rva:#x} is not inside any section")


def cstr(b: bytes) -> bytes:
    return b + b"\x00"


def read_export_info(data: bytes, sections) -> dict:
    """The provider's export-directory facts, read from the file:
    export-directory layout is EAT(0)/stamp(4)/MajorVer(8)/MinorVer(10)/
    name(12)/Base(16)/NumberOfFunctions(20)/NumberOfNames(24)/
    AddressOfFunctions(28)/AddressOfNames(32)/AddressOfNameOrdinals(36)."""
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    erva, esize = struct.unpack_from("<II", data, opt + 112 + 0 * 8)
    if erva == 0 or esize == 0:
        die("the provider carries no export directory")
    edata, esec = rva_to_file(sections, erva)
    (stamp,) = struct.unpack_from("<I", data, edata + 4)
    (ordinal_base, nfunc, nname) = struct.unpack_from("<III", data, edata + 16)
    (eat_rva, names_rva, ords_rva) = struct.unpack_from("<III", data, edata + 28)
    return {
        "rva": erva, "size": esize, "off": edata, "sec": esec,
        "stamp": stamp, "ordinal_base": ordinal_base,
        "nfunc": nfunc, "nname": nname,
        "eat_rva": eat_rva, "names_rva": names_rva, "ords_rva": ords_rva,
    }


def export_index_by_name(data: bytes, sections, px, name: bytes):
    """EAT index for `name`, via the directory's own names/ordinal
    tables -- computed from the real file, never assumed."""
    for i in range(px["nname"]):
        npr_off, _ = rva_to_file(sections, px["names_rva"] + i * 4)
        (p,) = struct.unpack_from("<I", data, npr_off)
        at, s = rva_to_file(sections, p)
        lim = s["raw"] + s["rawsize"]
        end = data.index(b"\x00", at, lim)
        if data[at:end] == name:
            ordt_off, _ = rva_to_file(sections, px["ords_rva"] + i * 2)
            (idx,) = struct.unpack_from("<H", data, ordt_off)
            return idx
    die(f"export name {name!r} is absent from the provider's directory")


def compose_delay_dir(data: bytearray, sections, dirs, dll_name, imports, dw_ts):
    """Composes ONE descriptor record (with its marker-free canonical
    layout described in the module docstring) into the .idata in-block
    padding. Returns (base_rva, dir_size)."""
    idata = None
    for s in sections:
        if s["name"] == ".idata":
            idata = s
    if idata is None:
        die("the client has no .idata section to compose into")
    pad = idata["rawsize"] - idata["vsize"]
    n = len(imports)
    hnlobs = []
    for kind, payload in imports:
        if kind == "name":
            hnlobs.append(struct.pack("<H", 0) + cstr(payload))
    hnlobs_len = sum(len(b) for b in hnlobs)
    want = 64 + len(cstr(dll_name)) + 8 + 2 * (n + 1) * 8 + hnlobs_len
    if pad < want:
        die(f".idata in-block padding {pad:#x} < {want} needed bytes: extend the "
            "patcher's room logic (SizeOfRawData extension) for this file shape")
    base_rva = idata["vaddr"] + idata["vsize"]
    base_foff = idata["raw"] + idata["vsize"]
    dir_size = want

    # Not overlapping any OTHER declared directory in this file:
    for idx, (rva, size) in enumerate(dirs):
        if rva == 0 or size == 0:
            continue
        if not (base_rva + want <= rva or rva + size <= base_rva):
            die(f"the composed delay directory [{base_rva:#x}..{base_rva + want:#x}) "
                f"would overlap declared dir[{idx}] [{rva:#x}..{rva + size:#x})")

    # Offsets (RVA = base_rva + off); each is cursor-sequential:
    desc_foff = base_foff
    term_foff = base_foff + 32
    name_foff = base_foff + 64
    data[name_foff:name_foff + len(cstr(dll_name))] = cstr(dll_name)
    hmod_foff = name_foff + len(cstr(dll_name))
    iat_foff = hmod_foff + 8
    int_foff = iat_foff + (n + 1) * 8
    hnl_foff = int_foff + (n + 1) * 8

    # The hint/name blobs first (their RVAs fill the arrays):
    hnlobs_rva = base_rva + (hnl_foff - base_foff)
    cursor = 0
    for b in hnlobs:
        at = hnl_foff + cursor
        data[at:at + len(b)] = b
        cursor += len(b)

    # The module handle starts at ZERO (the kernel's eager ruling
    # writes the provider's mapped base here; the pre-state fact is
    # 'no handle yet'):
    struct.pack_into("<Q", data, hmod_foff, 0)
    # The arrays: IAT and INT hold the SAME entries, entry-for-entry:
    hnl_cursor = 0
    for j, (kind, payload) in enumerate(imports):
        if kind == "name":
            hn_rva = hnlobs_rva + hnl_cursor
            hnl_cursor += len(hnlobs[j])
            th = hn_rva
        else:
            th = ORDINAL_FLAG64 | payload
        struct.pack_into("<Q", data, iat_foff + j * 8, th)
        struct.pack_into("<Q", data, int_foff + j * 8, th)
    struct.pack_into("<Q", data, iat_foff + n * 8, 0)
    struct.pack_into("<Q", data, int_foff + n * 8, 0)
    # The 32-byte all-zero terminator descriptor:
    struct.pack_into("<8I", data, term_foff, *(0,) * 8)

    # The descriptor itself, RVA form:
    name_rva = base_rva + 64
    hmod_rva = base_rva + (hmod_foff - base_foff)
    iat_rva = base_rva + (iat_foff - base_foff)
    int_rva = base_rva + (int_foff - base_foff)
    fields = (
        1,          # grAttrs: dlattrRva, the only documented attribute
        name_rva,   # rvaDLLName
        hmod_rva,   # rvaHmod
        iat_rva,    # rvaIAT
        int_rva,    # rvaINT
        0,          # rvaBoundIAT: unbound (no old-BIND in this composition)
        0,          # rvaUnloadIAT
        dw_ts,      # 0 = not bound; delaymut composes provider stamp + 1
    )
    struct.pack_into("<8I", data, desc_foff, *fields)

    # dir[13] points here; VirtualSize grows to cover it.
    struct.pack_into("<II", data, dir13_entry_off(bytes(data)), base_rva, dir_size)
    new_vsize = max(idata["vsize"], dir_size + (base_rva - idata["vaddr"]))
    struct.pack_into("<I", data, idata["hdr_off"] + 8, new_vsize)
    return base_rva, dir_size


def verify_delay_dir(data: bytes, base_rva, dir_size, dll_name, imports, dw_ts):
    """Re-parse what we wrote: exact dir[13] bounds, exact 8 fields,
    exact name string, exact IAT==INT arrays and terminators, exact
    hint/name blobs."""
    vdata = bytes(data)
    d2, sec2 = parse_headers(vdata)
    got_rva, got_size = d2[13]
    if got_rva != base_rva or got_size != dir_size:
        die(f"re-parse: dir[13] is ({got_rva:#x},{got_size:#x}), composed was ({base_rva:#x},{dir_size:#x})")
    foff, s = rva_to_file(sec2, base_rva)
    fields = struct.unpack_from("<8I", vdata, foff)
    if fields[0] != 1:
        die("re-parse: grAttrs is not dlattrRva")
    for k, label in ((1, "rvaDLLName"), (2, "rvaHmod"), (3, "rvaIAT")):
        if fields[k] == 0:
            die(f"re-parse: {label} is zero")
    if fields[5] != 0 or fields[6] != 0:
        die("re-parse: rvaBoundIAT/rvaUnloadIAT are not zero")
    if fields[7] != dw_ts:
        die(f"re-parse: dwTimeStamp is {fields[7]:#x}, composed {dw_ts:#x}")
    at, _ = rva_to_file(sec2, fields[1])
    lim = s["raw"] + s["rawsize"]
    end = vdata.index(b"\x00", at, lim)
    if vdata[at:end] != dll_name:
        die(f"re-parse: module name is {vdata[at:end]!r}, composed {dll_name!r}")
    n = len(imports)
    iat_foff, _ = rva_to_file(sec2, fields[3])
    int_foff, _ = rva_to_file(sec2, fields[4])
    for j in range(n + 1):
        (it,) = struct.unpack_from("<Q", vdata, iat_foff + j * 8)
        (nt,) = struct.unpack_from("<Q", vdata, int_foff + j * 8)
        if it != nt:
            die(f"re-parse: INT entry {j} does not equal its IAT entry ({it:#x} vs {nt:#x})")
        if j == n:
            if it != 0:
                die(f"re-parse: terminator entry is {it:#x}, must be 0")
            continue
        kind, payload = imports[j]
        if kind == "ordinal":
            if it != ORDINAL_FLAG64 | payload:
                die(f"re-parse: ordinal import {j} is {it:#x}, composed {ORDINAL_FLAG64 | payload:#x}")
        else:
            at2, s2 = rva_to_file(sec2, it)
            lim2 = s2["raw"] + s2["rawsize"]
            (hint,) = struct.unpack_from("<H", vdata, at2)
            if hint != 0:
                die(f"re-parse: named import {j}'s hint is {hint}, composed 0")
            end2 = vdata.index(b"\x00", at2 + 2, lim2)
            if vdata[at2 + 2:end2] != payload:
                die(f"re-parse: named import {j} is {vdata[at2 + 2:end2]!r}, composed {payload!r}")


def main() -> None:
    if len(sys.argv) != 5:
        die("usage: patch_pe_delay.py <provider_dll> <delaygood_exe> <delaybad_exe> <delaymut_exe>")
    prov_path, good_path, bad_path, mut_path = sys.argv[1:5]

    pdata = open(prov_path, "rb").read()
    pdirs, psecs = parse_headers(pdata)
    px = read_export_info(pdata, psecs)
    if image_base_of(pdata) != PROVIDER_IMAGE_BASE:
        die(f"provider ImageBase is {image_base_of(pdata):#x}, composed facts assume {PROVIDER_IMAGE_BASE:#x}")
    stamp = px["stamp"]
    if stamp in (0, 0xFFFFFFFF):
        die(f"provider .edata TimeDateStamp is {stamp:#x} -- the composition needs a real, non-sentinel stamp")
    crossfn_idx = export_index_by_name(pdata, psecs, px, b"crossfn")
    crossmark_idx = export_index_by_name(pdata, psecs, px, b"crossmark")
    crossfn_ordinal = px["ordinal_base"] + crossfn_idx
    crossmark_ordinal = px["ordinal_base"] + crossmark_idx
    print(f"  patcher: provider stamp {stamp:#x}; crossfn ordinal {crossfn_ordinal}, "
          f"crossmark ordinal {crossmark_ordinal} -- OK")
    if len({good_path, bad_path, mut_path}) != 3:
        die("the three carriers must be SEPARATE files")

    for label, path, dll_name, imports, dw_ts, expect in (
        ("delaygood", good_path, PROVIDER_NAME,
         [("name", b"crossfn"), ("ordinal", crossmark_ordinal)], 0, "EAGER-RESOLVE expected"),
        ("delaybad", bad_path, b"nowhere.dll", [("name", b"crossfn")], 0,
         "MissingDelayDllImport refusal expected"),
        ("delaymut", mut_path, PROVIDER_NAME,
         [("name", b"crossfn"), ("ordinal", crossmark_ordinal)], stamp + 1,
         "MalformedDelayImport refusal expected"),
    ):
        bio = bytearray(open(path, "rb").read())
        bdirs, bsecs = parse_headers(bytes(bio))
        if bdirs[13] != (0, 0):
            die(f"the {label} carrier already carries a delay directory (double patch?)")
        base_rva, dir_size = compose_delay_dir(bio, bsecs, bdirs, dll_name, imports, dw_ts)
        verify_delay_dir(bytes(bio), base_rva, dir_size, dll_name, imports, dw_ts)
        open(path, "wb").write(bytes(bio))
        print(f"  patcher: {label} composed at rva {base_rva:#x} ({dir_size} bytes): "
              f"{dll_name.decode()} with {len(imports)} import(s), dwTimeStamp {dw_ts:#x} -> {expect}")


if __name__ == "__main__":
    main()
