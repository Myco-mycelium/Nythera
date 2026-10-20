#!/usr/bin/env python3
"""patch_pe_bound.py -- the PE32+ BOUND-IMPORT increment's checked
composition step, the same discipline tools/patch_weak_verneed.py and
tools/patch_pe_forwarders.py established: this host's GNU ld builds real
DLLs and real dependent clients but emits neither bound-import
directories nor pre-filled IAT thunks, so the two structures this
increment proves get COMPOSED here, deliberately and self-verified, on
top of otherwise completely ordinary toolchain output.

Usage: patch_pe_bound.py <primary_provider_dll> <forwarder_host_dll> \
           <good_client_exe> <bad_client_exe>

PROVIDER (userprog_pe_import_dll.dll): its REAL .edata TimeDateStamp is
  read from the file (the exact quantity the kernel's bound-directory
  walker compares against); its two exports' EAT RVAs are read through
  the directory's OWN name/ordinal tables.

GOOD CLIENT (userprog_pe_import_app.exe, the resolution demo's
  artifact):
  * composes into its .idata section's own in-block padding (only
    VirtualSize grows; SizeOfRawData stays -- the forwarders patcher's
    room rule) a three-entry bound-import directory terminated by the
    null entry:
      providerDLL : the provider's OWN stamp -- the HONOR path's entry;
      fwdprovDLL  : stamp + 1 -- the STALE entry (deliberately NOT any
                    file's real stamp: the client was "bound" against an
                    older build of that provider than is registered);
      nobody.dll  : an unregistered name that owns no import descriptor
                    -- inert metadata the spec's per-descriptor walk
                    must tolerate;
  * PRE-FILLS the provider's OWN FirstThunk slots with the provider's
    freshly-resolved absolute addresses -- exactly the values a real
    binder writes -- so the kernel's verify-then-leave path has real
    pre-filled values to reconcile;
  * the fwdprov descriptor's FirstThunk slots carry the OLD BUILD's
    contradiction -- the stale binding's pre-filled value is the WRONG
    export's address (crossmark's in the crossfn-fwd slot), exactly
    what an old binder would have stamped in; any loader that trusted
    the stale stamp would call the wrong target, and the DISCARD is
    proven because the kernel overwrites it with the fresh resolution
    (the forwarder chase) and ring 3 then calls the correct target;
    the slot's pre-loader value is recorded verbatim as the discard
    fact; unsigned note -- the subtlety the first boot caught: without
    the stale prefill the slot's on-disk content is just the toolchain's
    unbound hint/name RVA, and 'discard' would be indistinguishable
    from ordinary fresh resolution;
  * dir[11]'s span is checked against EVERY other declared directory
    (the client's own dir[12] IAT copy, dir[1], ...) -- overlap is
    refused, never tolerated;
  * the RESULT is re-parsed (exact directory bounds, exact stamps,
    exact module-name strings at exact offsets, all-zero terminator,
    exact prefill values) and the build is refused unless the on-disk
    state is the composed state.

BAD CLIENT (userprog_pe_bound_bad.exe, built by build.sh from its own
  small .s whose ONLY import is crossfn): one bound entry whose stamp
  MATCHES the registered provider's -- the stamp PROCLAIMS a fresh
  binding -- while its single FirstThunk slot carries a deliberately
  WRONG bound value. A loader that trusts the stamp would hand ring 3
  the wrong target; the kernel's verify-then-leave path sees the
  contradiction and refuses with the typed BoundValueCorrupt. This file
  IS embedded in the kernel (include_bytes!) so the boot itself runs
  the refusal against real object-file bytes every time.
"""

import struct
import sys

MAX_THUNKS = 32
MAX_DESCRIPTORS = 16
PROVIDER_IMAGE_BASE = 0x1800_0000_0000
FWDPROV_IMAGE_BASE = 0x1C00_0000_0000
ORDINAL_FLAG64 = 1 << 63


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
    if n_dirs < 12:
        die(f"optional header declares {n_dirs} data directories; entry 11 not usable")
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


def dir11_field_off(data: bytes) -> int:
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    return opt + 112 + 11 * 8


def dir_field_off(data: bytes, index: int) -> int:
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    return opt + 112 + index * 8


def section_containing(sections, rva):
    for s in sections:
        if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rawsize"]):
            return s
    return None


def rva_to_file(sections, rva):
    s = section_containing(sections, rva)
    if s is None:
        die(f"rva {rva:#x} is not inside any section")
    return s["raw"] + (rva - s["vaddr"]), s


def read_cstring(data: bytes, file_off: int, section_end_file_off: int) -> bytes:
    nul = data.find(b"\x00", file_off, section_end_file_off)
    if nul < 0:
        die("a name/string with no NUL inside its section")
    return data[file_off:nul]


def read_export_dir(data: bytes, dirs, sections):
    """The export directory's load-bearing fields, including the
    TimeDateStamp the whole bound-import question is about."""
    rva, size = dirs[0]
    if rva == 0 or size == 0:
        die("provider has no export directory")
    s = section_containing(sections, rva)
    if s is None or s["name"] != ".edata":
        die("the export directory does not resolve to the .edata section")
    dir_foff = s["raw"] + (rva - s["vaddr"])
    (stamp,) = struct.unpack_from("<I", data, dir_foff + 4)
    (ordinal_base,) = struct.unpack_from("<I", data, dir_foff + 16)
    (nfun,) = struct.unpack_from("<I", data, dir_foff + 20)
    (nnames,) = struct.unpack_from("<I", data, dir_foff + 24)
    eat_rva, names_rva, ords_rva = struct.unpack_from("<III", data, dir_foff + 28)
    return {
        "dir_rva": rva, "dir_size": size, "stamp": stamp,
        "ordinal_base": ordinal_base, "nfun": nfun, "nnames": nnames,
        "eat_rva": eat_rva, "names_rva": names_rva, "ords_rva": ords_rva,
        "section": s,
    }


def eat_rva_by_name(data: bytes, sections, x, name: bytes) -> int:
    """EAT RVA for `name` through the directory's OWN name/ordinal
    tables -- computed from the real file, never assumed."""
    for i in range(x["nnames"]):
        npr_off, _ = rva_to_file(sections, x["names_rva"] + i * 4)
        (p,) = struct.unpack_from("<I", data, npr_off)
        s = section_containing(sections, p)
        if s is None:
            die(f"export name pointer rva {p:#x} outside any section")
        at = s["raw"] + (p - s["vaddr"])
        if read_cstring(data, at, s["raw"] + s["rawsize"]) != name:
            continue
        ordt_off, _ = rva_to_file(sections, x["ords_rva"] + i * 2)
        (idx,) = struct.unpack_from("<H", data, ordt_off)
        eat_off, _ = rva_to_file(sections, x["eat_rva"] + idx * 4)
        (rva,) = struct.unpack_from("<I", data, eat_off)
        if x["dir_rva"] <= rva < x["dir_rva"] + x["dir_size"]:
            die(f"{name!r}'s EAT entry is a declared FORWARDER -- a bound "
                "import's pre-filled value must be a plain address")
        return rva
    die(f"export name {name!r} is absent from the provider's directory")


def find_import_descriptor(data: bytes, sections, dll_name: bytes):
    """The client's IMPORT_DIRECTORY (dir[1]) descriptor whose DLL name
    matches -- returns (oft_rva, ft_rva): the arrays the bound prefills
    are composed into. Absent is a composition error, refused."""
    imp_rva, imp_size = 0, 0
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    imp_rva, imp_size = struct.unpack_from("<II", data, opt + 112 + 1 * 8)
    if imp_rva == 0 or imp_size == 0:
        die("the client carries no import directory to compose against")
    for desc in range(MAX_DESCRIPTORS):
        d = imp_rva + desc * 20
        at, s = rva_to_file(sections, d)
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
        if read_cstring(data, at2, s2["raw"] + s2["rawsize"]) == dll_name:
            return (oft if oft != 0 else ft), ft
        if desc == MAX_DESCRIPTORS - 1:
            die("import descriptor array unbounded (no null terminator)")
    die("the descriptor loop exhausted without the provider descriptor")


def thunks_as_written(data: bytes, sections, oft_rva):
    """Per-thunk shape, read from the OFT array as it exists NOW (after
    the forwarders patcher may have composed the ordinal form):
    ('name', bytes) or ('ordinal', ordinal_value)."""
    out = []
    foff, _s = rva_to_file(sections, oft_rva)
    for j in range(MAX_THUNKS):
        (th,) = struct.unpack_from("<Q", data, foff + j * 8)
        if th == 0:
            return out
        if th & ORDINAL_FLAG64:
            out.append(("ordinal", th & ~ORDINAL_FLAG64))
        else:
            s2 = section_containing(sections, th + 2)
            if s2 is None:
                die("a thunk's hint/name rva is not inside any section")
            at2 = s2["raw"] + ((th + 2) - s2["vaddr"])
            out.append(("name", read_cstring(data, at2, s2["raw"] + s2["rawsize"])))
            if j == MAX_THUNKS - 1:
                die("thunk array unbounded (no null terminator)")
    return out


def resolved_rtva_for(pdata: bytes, psecs, x, thunk) -> int:
    """The provider's EAT RVA the thunk resolves to: by name through the
    directory's own ordinal table, or (ordinal - OrdinalBase) for the
    composed ordinal form. Same quantity the kernel re-derives. NOTE:
    `pdata`/`psecs` must be the PROVIDER's bytes/sections -- the RVAs
    here are provider-side (a client's section map must never be
    confused with them)."""
    if thunk[0] == "ordinal":
        idx = thunk[1] - x["ordinal_base"]
        if not (0 <= idx < x["nfun"]):
            die(f"ordinal-form thunk {thunk[1]} is out of the EAT range")
    else:
        idx = None
        for k in range(x["nnames"]):
            npr_off, _ = rva_to_file(psecs, x["names_rva"] + k * 4)
            (p,) = struct.unpack_from("<I", pdata, npr_off)
            s = section_containing(psecs, p)
            if s is None:
                die("export name pointer rva outside any section")
            at = s["raw"] + (p - s["vaddr"])
            if read_cstring(pdata, at, s["raw"] + s["rawsize"]) == thunk[1]:
                ordt_off, _ = rva_to_file(psecs, x["ords_rva"] + k * 2)
                (idx,) = struct.unpack_from("<H", pdata, ordt_off)
                break
        if idx is None:
            die(f"thunk name {thunk[1]!r} does not export-resolve in the provider")
        if not (0 <= idx < x["nfun"]):
            die(f"thunk's stored ordinal {idx} exceeds the EAT bounds")
    eat_off, _ = rva_to_file(psecs, x["eat_rva"] + idx * 4)
    (rva,) = struct.unpack_from("<I", pdata, eat_off)
    if rva == 0:
        die(f"EAT slot {idx} is null -- an unbind-able export")
    if x["dir_rva"] <= rva < x["dir_rva"] + x["dir_size"]:
        die(f"EAT slot {idx} is a forwarder -- a bound slot cannot carry one")
    return rva


def compose_bound_dir(data: bytearray, sections, dirs, records):
    """Composes records [(dll_name, stamp, refs)] -- each `refs` a list
    of (ref_name, ref_stamp) composed as consecutive
    IMAGE_BOUND_FORWARDER_REF entries AFTER its descriptor (the real
    spec's NumberOfModuleForwarderRefs contract: the count makes the
    walk unambiguous) -- into the .idata section's own in-block padding
    (VirtualSize grows; SizeOfRawData stays, the forwarders-patcher
    room rule), then dir[11] points at the result. Returns
    (dir_rva, dir_size). Every name field is an OFFSET from the
    directory's start (the bound-import contract), never an absolute
    RVA."""
    idata = None
    for s in sections:
        if s["name"] == ".idata":
            idata = s
    if idata is None:
        die("the client has no .idata section to compose into")
    pad = idata["rawsize"] - idata["vsize"]
    # 8 bytes per ENTRY (descriptors + refs), +8 for the null
    # terminator, + the names (descriptors' and refs'):
    entry_count = len(records) + sum(len(r[2]) for r in records)
    want = entry_count * 8 + 8 + sum(len(n) + 1 for n, _s, _r in records)
    want += sum(len(rn) + 1 for _n, _s, refs in records for rn, _rs in refs)
    if pad < want:
        die(f".idata in-block padding {pad:#x} < {want} needed bytes: extend the "
            "patcher's room logic (SizeOfRawData extension) for this file shape")
    base_rva = idata["vaddr"] + idata["vsize"]
    base_foff = idata["raw"] + idata["vsize"]
    # The directory's full span: the descriptors, their refs, the
    # names, and the terminator -- exactly what was measured above.
    dir_size = want

    # Not overlapping any OTHER declared directory in this file:
    for idx, (rva, size) in enumerate(dirs):
        if rva == 0 or size == 0:
            continue
        if not (base_rva + want <= rva or rva + size <= base_rva):
            die(f"the composed directory [{base_rva:#x}..{base_rva + want:#x}) "
                f"would overlap declared dir[{idx}] [{rva:#x}..{rva + size:#x})")

    # Layout: each descriptor followed by ITS ref entries, then the
    # NUL-terminated module names (own names and ref names, in entry
    # order, strictly sequential) after the entries. Every name offset
    # counts from the directory start.
    entries = []
    for name, stamp, refs in records:
        entries.append(("desc", name, stamp))
        for rn, rs in refs:
            entries.append(("ref", rn, rs))
    name_area = entry_count * 8 + 8
    cursor = name_area
    for k, (kind, nm, st) in enumerate(entries):
        ent = base_foff + k * 8
        off = cursor
        cursor += len(nm) + 1
        if kind == "desc":
            n_refs = len(records[k][2])
        else:
            n_refs = 0  # a ref's own count field is Reserved, zero
        if off > 0xFFFF:
            die("a module-name offset exceeds the u16 field")
        struct.pack_into("<IHH", data, ent, st, off, n_refs)
        at = base_foff + off
        data[at:at + len(nm) + 1] = nm + b"\x00"
    # the terminating ALL-ZERO entry:
    struct.pack_into("<Q", data, base_foff + entry_count * 8, 0)
    # dir[11] = the bound directory; VirtualSize grows to cover it.
    struct.pack_into("<II", data, dir11_field_off(bytes(data)), base_rva, dir_size)
    new_vsize = max(idata["vsize"], dir_size + (base_rva - idata["vaddr"]))
    struct.pack_into("<I", data, idata["hdr_off"] + 8, new_vsize)
    return base_rva, dir_size


def verify_bound_dir(data: bytes, base_rva: int, dir_size: int, records):
    """Re-parse what we wrote: exact directory bounds, exact stamps,
    exact ref counts, exact module-name strings at exact offsets,
    terminator all-zero."""
    vdata = bytes(data)
    d2, sec2 = parse_headers(vdata)
    got_rva, got_size = d2[11]
    if got_rva != base_rva or got_size != dir_size:
        die(f"re-parse: dir[11] is ({got_rva:#x},{got_size:#x}), composed was ({base_rva:#x},{dir_size:#x})")
    foff, s = rva_to_file(sec2, base_rva)
    end_foff = s["raw"] + s["rawsize"]
    entry_count = len(records) + sum(len(r[2]) for r in records)
    name_area = entry_count * 8 + 8
    walk = 0
    expected = name_area
    for name, stamp, refs in records:
        at = foff + walk * 8
        (st, off, cnt) = struct.unpack_from("<IHH", vdata, at)
        if st != stamp:
            die(f"re-parse: entry {walk} stamp is {st:#x}, composed was {stamp:#x}")
        if cnt != len(refs):
            die(f"re-parse: entry {walk} NumberOfModuleForwarderRefs is {cnt}, composed was {len(refs)}")
        if off != expected or foff + off >= end_foff:
            die(f"re-parse: entry {walk} name offset {off} is not the composed offset {expected}")
        nm = read_cstring(vdata, foff + off, end_foff)
        if nm != name:
            die(f"re-parse: entry {walk} name is {nm!r}, composed was {name!r}")
        expected += len(nm) + 1
        walk += 1
        for rn, rs in refs:
            at = foff + walk * 8
            (rst, roff, rcnt) = struct.unpack_from("<IHH", vdata, at)
            if rst != rs:
                die(f"re-parse: ref {walk} of {name!r} stamp is {rst:#x}, composed was {rs:#x}")
            if rcnt != 0:
                die("re-parse: a ref's Reserved field is not zero")
            if roff != expected or foff + roff >= end_foff:
                die(f"re-parse: ref {walk} name offset {roff} is not the composed offset {expected}")
            rnm = read_cstring(vdata, foff + roff, end_foff)
            if rnm != rn:
                die(f"re-parse: ref {walk} name is {rnm!r}, composed was {rn!r}")
            expected += len(rnm) + 1
            walk += 1
    (term,) = struct.unpack_from("<Q", vdata, foff + walk * 8)
    if term != 0:
        die("re-parse: the composed directory's null terminator is not all zero")
    (vsize,) = struct.unpack_from("<I", vdata, s["hdr_off"] + 8)
    if base_rva + dir_size > s["vaddr"] + vsize:
        die("re-parse: the composed directory runs past the grown VirtualSize")


def prefill_and_verify(data: bytearray, sections, ft_rva, values, labels):
    """Writes each FirstThunk slot's composed bound value, verifying
    slot-by-slot that the write took."""
    for j, (value, label) in enumerate(zip(values, labels)):
        at, _s = rva_to_file(sections, ft_rva + j * 8)
        struct.pack_into("<Q", data, at, value)
    vdata = bytes(data)
    for k, (value, label) in enumerate(zip(values, labels)):
        at, _ = rva_to_file(sections, ft_rva + k * 8)
        (got,) = struct.unpack_from("<Q", vdata, at)
        if got != value:
            die(f"re-parse: {label}'s bound IAT value is {got:#x}, composed was {value:#x}")
    print(f"  patcher: {len(values)} FirstThunk slot(s) composed ({', '.join(labels)}) and verified by re-parse -- OK")


def main() -> None:
    if len(sys.argv) != 6:
        die("usage: patch_pe_bound.py <primary_provider_dll> <forwarder_host_dll> "
            "<good_client_exe> <bad_client_exe> <refstale_client_exe>")
    if len(sys.argv) != 6 or len(set(sys.argv[3:6])) != 3:
        die("the bad and refstale clients must be SEPARATE files from each other "
            "and from the good client")
    prov_path, fwd_path, good_path, bad_path, refstale_path = sys.argv[1:6]

    # ---- the provider: the stamp and EAT addresses the whole
    # composition is pinned against ----
    pdata = open(prov_path, "rb").read()
    pdirs, psecs = parse_headers(pdata)
    px = read_export_dir(pdata, pdirs, psecs)
    if image_base_of(pdata) != PROVIDER_IMAGE_BASE:
        die(f"provider ImageBase is {image_base_of(pdata):#x}, composed facts assume {PROVIDER_IMAGE_BASE:#x}")
    stamp = px["stamp"]
    if stamp in (0, 0xFFFFFFFF):
        die(f"provider .edata TimeDateStamp is {stamp:#x} -- the composition needs a real, non-sentinel stamp")
    fn_rva = eat_rva_by_name(pdata, psecs, px, b"crossfn")
    mark_rva = eat_rva_by_name(pdata, psecs, px, b"crossmark")
    print(f"  patcher: provider stamp {stamp:#x}; EAT crossfn {fn_rva:#x}, crossmark {mark_rva:#x} -- OK")

    fdata = open(fwd_path, "rb").read()
    fdirs, fsecs = parse_headers(fdata)
    fx = read_export_dir(fdata, fdirs, fsecs)
    if image_base_of(fdata) != FWDPROV_IMAGE_BASE:
        die(f"forwarder host ImageBase is {image_base_of(fdata):#x}, composed facts assume {FWDPROV_IMAGE_BASE:#x}")
    if fx["stamp"] == stamp:
        print("  patcher: note -- the forwarder host's own stamp equals the provider's "
              "(same-build-second toolchain); the composed stale entry is stamp + 1, "
              "deliberately NEVER any file's real stamp")
    if stamp + 1 == 0:
        die("stamp + 1 wraps -- pick a different stamp basis for the stale entry")

    # ---- the GOOD client: one HONORED entry (fresh stamp, real
    # prefills), one STALE entry (stamp + 1, no prefill), one inert
    # unregistered name ----
    good = bytearray(open(good_path, "rb").read())
    gdirs, gsecs = parse_headers(bytes(good))
    if gdirs[11] != (0, 0):
        die("the good client already carries a bound directory (double patch?)")
    g_oft, g_ft = find_import_descriptor(bytes(good), gsecs, b"userprog_pe_import_dll.dll")
    g_thunks = thunks_as_written(bytes(good), gsecs, g_oft)
    if len(g_thunks) != 2:
        die(f"the provider descriptor carries {len(g_thunks)} thunk(s), fixture prefill logic expects exactly 2")
    if g_thunks[0] != ("name", b"crossfn"):
        die(f"the provider descriptor's first thunk is {g_thunks[0]!r}, not named crossfn -- re-pin the composition")
    # The thunk shapes resolve against the PROVIDER's tables (the
    # crossmark thunk may already be ordinal-form from the forwarders
    # patcher); the client's bytes contribute only the thunk array.
    g_mark_rva = resolved_rtva_for(pdata, psecs, px, g_thunks[1])
    if g_mark_rva != mark_rva:
        die(f"the second thunk (crossmark, {g_thunks[1]!r} form) does not resolve to crossmark's EAT {mark_rva:#x}")
    good_records = [
        (b"userprog_pe_import_dll.dll", stamp, []),
        (b"userprog_pe_import_fwdprov.dll", stamp + 1, []),
        (b"nobody.dll", 42, []),
    ]
    base_rva, dir_size = compose_bound_dir(good, gsecs, gdirs, good_records)
    verify_bound_dir(bytes(good), base_rva, dir_size, good_records)
    print(f"  patcher: good client bound directory composed at rva {base_rva:#x} "
          f"({dir_size} bytes, 3 entries + terminator) and verified -- OK")
    # The honored entry's prefill: the provider's freshly-resolved
    # absolute addresses, exactly the values a real binder writes.
    honored = [PROVIDER_IMAGE_BASE + fn_rva, PROVIDER_IMAGE_BASE + mark_rva]
    prefill_and_verify(good, gsecs, g_ft, honored, ["good crossfn (honored)", "good crossmark (honored)"])
    # The stale entry's prefill: the OLD BUILD's contradiction -- the
    # WRONG export's address in the fwd slot. Stale stamps must be
    # DISCARDED, and the discard is only an observable fact when the
    # slot actually carries a stale (wrong) value to discard: with the
    # slot left at the toolchain's unbound hint/rva content, a discard
    # would be indistinguishable from ordinary fresh resolution.
    fk_oft, fk_ft = find_import_descriptor(bytes(good), gsecs, b"userprog_pe_import_fwdprov.dll")
    fk_thunks = thunks_as_written(bytes(good), gsecs, fk_oft)
    if len(fk_thunks) != 1:
        die(f"the fwdprov descriptor carries {len(fk_thunks)} thunk(s), the stale composition expects exactly 1")
    stale = [PROVIDER_IMAGE_BASE + mark_rva]
    prefill_and_verify(good, gsecs, fk_ft, stale, ["stale crossfn_fwd (discarded contradiction)"])
    print("  patcher: the stale entry's slot carries the OLD build's wrong address; "
          "the kernel must NOT honor it (stamp is stale) -- its fresh resolution "
          "(the forwarder chase) overwrites it at boot")
    open(good_path, "wb").write(bytes(good))

    # ---- the BAD and REFSTALE clients: the same composition on the
    # same DLL pair, differing in ONE composed u32: the forwarder
    # REF's stamp. Both import crossfn_fwd THROUGH the forwarder
    # host; both declare the forwarder host's OWN real .edata stamp
    # (a fresh descriptor) and one forwarder ref naming the primary
    # provider; both slots carry the same deliberately WRONG bound
    # value. The BAD client's ref carries the provider's own stamp
    # (all-fresh) -- the honor path is GATED ON, so its verify sees the
    # slot contradicting the forwarder-chased answer and refuses with
    # the typed BoundValueCorrupt, whose fresh half IS the chase
    # target. The REFSTALE client's ref carries stamp + 1 (stale) --
    # the honor path is GATED OFF by the ref alone, so the same wrong
    # slot is DISCARDED and resolved fresh through the same chase.
    # The pair demonstrates the ref-aware ruling's gate byte
    # for byte: one stamp byte decides refuse-vs-discard.
    WRONG = 0xDEAD_BEEF_0000_1000  # the deliberately wrong bound value
    for label, path, ref_stamp, expect in (
        ("bad", bad_path, stamp, "refuse with BoundValueCorrupt"),
        ("refstale", refstale_path, stamp + 1, "DISCARD and resolve fresh"),
    ):
        bio = bytearray(open(path, "rb").read())
        bdirs, bsecs = parse_headers(bytes(bio))
        if bdirs[11] != (0, 0):
            die(f"the {label} client already carries a bound directory (double patch?)")
        _b_oft, b_ft = find_import_descriptor(bytes(bio), bsecs, b"userprog_pe_import_fwdprov.dll")
        b_thunks = thunks_as_written(bytes(bio), bsecs, _b_oft)
        if len(b_thunks) != 1 or b_thunks[0] != ("name", b"crossfn_fwd"):
            die(f"the {label} client's fwdprov descriptor must carry exactly one NAMED crossfn_fwd thunk, got {b_thunks!r}")
        bad_records = [
            (b"userprog_pe_import_fwdprov.dll", fx["stamp"],
             [(b"userprog_pe_import_dll.dll", ref_stamp)]),
        ]
        b_base, b_size = compose_bound_dir(bio, bsecs, bdirs, bad_records)
        verify_bound_dir(bytes(bio), b_base, b_size, bad_records)
        prefill_and_verify(bio, bsecs, b_ft, [WRONG], [f"{label} crossfn_fwd ({expect})"])
        open(path, "wb").write(bytes(bio))
        print(f"  patcher: {label} client composed at rva {b_base:#x} ({b_size} bytes): descriptor stamp "
              f"{fx['stamp']:#x} (the forwarder host's OWN), ref stamp {ref_stamp:#x} "
              f"({ 'FRESH, all-fresh honors' if ref_stamp == stamp else 'STALE (provider + 1)' }) -> "
              f"{expect}")


if __name__ == "__main__":
    main()
