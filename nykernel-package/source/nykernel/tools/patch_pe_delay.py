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

The three STRUCTURAL carriers (same composition machinery, one
composed property broken each, so the kernel's other
MalformedDelayImport reasons get boot-controlled surfaces too -- a
refusal that is only code-verified is a claim, not a fact):
  * delayattrs (userprog_pe_delay_attrs.exe): grAttrs = 0x3 -- the
    documented dlattrRva bit PLUS an undefined one, which the Windows
    helper itself treats as unrecoverable -> 'unknown delay descriptor
    grAttrs bits'.
  * delaymis   (userprog_pe_delay_mis.exe): INT[0] is the named
    crossfn hint/name RVA while IAT[0] carries an ORDINAL thunk --
    the unbound pre-state the walk requires (INT == IAT) is broken
    -> 'delay INT entry does not match its IAT entry'.
  * delayva    (userprog_pe_delay_va.exe): grAttrs = 0 (the pre-VC7
    VA form) with the fields left as the RVAs the canonical layout
    computes -- every value is far below the image's own
    ImageBase -> 'delay VA-form descriptor field below the preferred
    image base'.
  * delayvabias (userprog_pe_delay_vabias.exe): the genuinely VA-form
    carrier. Its fields are real VAs (preferred ImageBase + rva), so
    it must be linked at a LOW ImageBase -- a VA has to fit in the
    descriptor's 32-bit field -- hence 0x0040_0000. Loaded at its own
    base it would be readable; loaded at a BIAS (the demo's 0x4600...
    base) the descriptor is refused as stale by construction: every
    baked VA still addresses the ORIGINAL base. -> 'delay VA-form
    descriptor in a relocated image'.

The CAP-BOUNDARY carriers (the descriptor-array and thunk-array caps
in `pe::resolve_delay_imports` are EXCLUSIVE: the terminator is read
BEFORE the cap is consulted, so exactly 16 descriptors and exactly 64
thunk entries are the LEGAL maxima, and only a real 17th descriptor /
65th thunk entry is refused). A refusal alone cannot evidence that --
a cap checked before the terminator read would refuse the 17th
identically while wrongly refusing a legal 16 -- so both sides get a
file:
  * delaymany   (userprog_pe_delay_many.exe): 17 descriptors, each
    naming the real provider with one named import -> the walk resolves
    the first 16 (their IAT slots and handles are left WRITTEN) and
    refuses the 17th with 'delay descriptor array unbounded'.
  * delaythunks (userprog_pe_delay_thunks.exe): ONE descriptor whose
    INT/IAT carry 65 entries -> 64 slots resolve, the 65th entry is
    refused with 'delay thunk array unbounded'.
  * delayfull   (userprog_pe_delay_full.exe): the two legal maxima at
    once -- exactly 16 descriptors whose FIRST carries exactly 64
    thunk entries (79 imports total) -> the walk must ACCEPT and
    resolve every slot. This is the carrier that pins the caps as
    exclusive rather than merely 'some bound exists'.

Those three need more than the section's FileAlignment-bounded padding
(<= 0x1ff bytes) can hold, so the composition EXTENDS .idata's raw
span -- checked: .idata must be the last section by raw placement,
every trailing byte must be exactly the COFF symbol table `ld`
appended (stripped explicitly, pointer/count zeroed), and the new span
is FileAlignment-aligned. Nothing else's bytes are touched.

The RESULT is re-parsed (exact dir[13] bounds, exact composed
descriptor COUNT with the terminator after the last one, exact fields
at exact offsets, exact name strings, exact IAT==INT arrays and
terminators, exact hint/name blobs) and the build is refused unless
the on-disk state IS the composed state.
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


def extend_idata_raw(data, sections, idata, need_end, label):
    """Grow `.idata`'s raw span so `need_end` bytes fit inside it.

    The cap-boundary carriers (17 descriptors, 65 thunks, and the legal
    16-descriptor / 64-thunk maximum) need more room than the section's
    in-block alignment padding -- which is bounded by FileAlignment and
    therefore never more than 0x1ff bytes -- so the file is EXTENDED.
    The extension is checked, not assumed:

      1. `.idata` must be the LAST section by raw placement, so appended
         bytes can never collide with another section's raw data;
      2. every byte after `.idata`'s raw end must be exactly the COFF
         symbol table `ld` appended (nothing in this tree reads it, and
         it is not loaded), and it is STRIPPED EXPLICITLY -- pointer and
         count zeroed -- rather than silently overwritten. A file with an
         unexplained overlay is refused;
      3. the new span is FileAlignment-aligned, so SizeOfRawData stays a
         legal section size.
    """
    if need_end <= idata["rawsize"]:
        return
    for s in sections:
        if s is idata or s["rawsize"] == 0:
            continue
        if s["raw"] + s["rawsize"] > idata["raw"]:
            die(f"{label}: section {s['name']!r} holds raw bytes AFTER .idata -- "
                "extending .idata's raw span would overwrite them")
    e = struct.unpack_from("<I", data, 0x3C)[0]
    coff = e + 4
    opt = coff + 20
    old_raw = idata["rawsize"]
    raw_end = idata["raw"] + old_raw
    sym_ptr, n_sym = struct.unpack_from("<II", data, coff + 8)
    if sym_ptr == 0 and n_sym == 0:
        if len(data) != raw_end:
            die(f"{label}: {len(data) - raw_end:#x} bytes follow .idata's raw data and are "
                "NOT a COFF symbol table -- refusing to extend over bytes nothing accounts for")
    else:
        if sym_ptr != raw_end:
            die(f"{label}: COFF symbol table at {sym_ptr:#x}, not at .idata's raw end "
                f"{raw_end:#x} -- the file layout is not the one this extension was checked against")
        strtab = sym_ptr + n_sym * 18
        if strtab + 4 > len(data):
            die(f"{label}: the COFF symbol table runs past EOF")
        (str_size,) = struct.unpack_from("<I", data, strtab)
        if str_size < 4 or strtab + str_size != len(data):
            die(f"{label}: bytes [{strtab + str_size:#x}..{len(data):#x}) after the COFF "
                "symbol table are unexplained -- refusing to extend over them")
        struct.pack_into("<II", data, coff + 8, 0, 0)
        del data[raw_end:]
    file_align = struct.unpack_from("<I", data, opt + 36)[0]
    if file_align < 0x200 or file_align & (file_align - 1) != 0:
        die(f"{label}: FileAlignment {file_align:#x} is not a power of two >= 0x200")
    new_raw = (need_end + file_align - 1) & ~(file_align - 1)
    data.extend(b"\x00" * (new_raw - old_raw))
    struct.pack_into("<I", data, idata["hdr_off"] + 16, new_raw)
    idata["rawsize"] = new_raw
    print(f"  patcher: {label} raw extension -- .idata SizeOfRawData {old_raw:#x} -> "
          f"{new_raw:#x} (COFF symbol table stripped, file {len(data):#x} bytes, "
          f"layout re-checked by the caller's re-parse)")


def compose_delay_dir(data: bytearray, sections, dirs, descriptors, va_form=False):
    """Composes N delay descriptors into the `.idata` in-block room.

    `descriptors` is a list of `(dll_name, imports, dw_ts, gr_attrs,
    mismatch)` records -- `imports` being `("name", bytes)` /
    `("ordinal", u32)` pairs, and `mismatch` composing a broken
    INT[0]/IAT[0] pre-state for the structural carriers.

    Layout. With ONE descriptor this is byte-for-byte the layout this
    patcher has always composed (descriptor, terminator, name, handle
    qword, IAT, INT, hint/name blobs), so the established carriers are
    unchanged by the generalization:

        [+0]                 N descriptors, 32 bytes each
        [+32N]               the all-zero terminator descriptor
        [..]                 per descriptor: DLL name cstring, handle
                             qword (0 -- the kernel's eager ruling
                             writes the provider's mapped base here),
                             IAT array (n+1, terminated), INT array
                             (n+1, mirrored)
        [..]                 every named import's hint/name blob, in
                             descriptor order

    Room. The layout goes in the section's alignment padding when it
    fits; otherwise `extend_idata_raw` grows the raw span (checked --
    see its docstring). Only ever VirtualSize and SizeOfRawData grow; no
    other section's bytes are touched.

    Returns `(base_rva, dir_size)`.
    """
    idata = None
    for s in sections:
        if s["name"] == ".idata":
            idata = s
    if idata is None:
        die("the client has no .idata section to compose into")
    n = len(descriptors)
    if n == 0:
        die("a delay directory needs at least one descriptor")
    if va_form and n != 1:
        die("the pre-VC7 VA form is composed for exactly one descriptor")

    # Section-relative offsets, walked in the layout order above.
    off = 32 * (n + 1)
    layout = []
    for dll_name, imports, _ts, _gr, _mis in descriptors:
        name_off = off
        off += len(cstr(dll_name))
        hmod_off = off
        off += 8
        iat_off = off
        off += (len(imports) + 1) * 8
        int_off = off
        off += (len(imports) + 1) * 8
        layout.append((name_off, hmod_off, iat_off, int_off))
    thunks = []
    for _dll, imports, _ts, _gr, _mis in descriptors:
        vals = []
        for kind, payload in imports:
            if kind == "name":
                vals.append(("name", off, payload))
                off += len(struct.pack("<H", 0) + cstr(payload))
            elif kind == "ordinal":
                vals.append(("ordinal", ORDINAL_FLAG64 | payload, None))
            else:
                die(f"unknown import kind {kind!r}")
        thunks.append(vals)
    want = off

    base_rva = idata["vaddr"] + idata["vsize"]
    need_end = idata["vsize"] + want
    extend_idata_raw(data, sections, idata, need_end, "delay composition")
    base_foff = idata["raw"] + idata["vsize"]
    dir_size = want

    # Not overlapping any OTHER declared directory in this file:
    for idx, (rva, size) in enumerate(dirs):
        if rva == 0 or size == 0:
            continue
        if not (base_rva + want <= rva or rva + size <= base_rva):
            die(f"the composed delay directory [{base_rva:#x}..{base_rva + want:#x}) "
                f"would overlap declared dir[{idx}] [{rva:#x}..{rva + size:#x})")

    def rva_of(o):
        return base_rva + o

    for di, (dll_name, imports, dw_ts, gr_attrs, mismatch) in enumerate(descriptors):
        name_off, hmod_off, iat_off, int_off = layout[di]
        data[base_foff + name_off: base_foff + name_off + len(cstr(dll_name))] = cstr(dll_name)
        # The module handle starts at ZERO (the kernel's eager ruling
        # writes the provider's mapped base here; the pre-state fact is
        # 'no handle yet'):
        struct.pack_into("<Q", data, base_foff + hmod_off, 0)
        for j, ent in enumerate(thunks[di]):
            if ent[0] == "name":
                _, o, payload = ent
                blob = struct.pack("<H", 0) + cstr(payload)
                data[base_foff + o: base_foff + o + len(blob)] = blob
                th = rva_of(o)
            else:
                th = ent[1]
            iat_th = th
            if mismatch and j == 0:
                # ONE composed contradiction: the IAT claims an ordinal
                # thunk while the INT names a symbol. Any value that is
                # NOT the INT entry would do; a well-formed-looking
                # ordinal is the least artificial (it is what a
                # half-resolved import looks like).
                iat_th = ORDINAL_FLAG64 | 1
            struct.pack_into("<Q", data, base_foff + iat_off + j * 8, iat_th)
            struct.pack_into("<Q", data, base_foff + int_off + j * 8, th)
        struct.pack_into("<Q", data, base_foff + iat_off + len(imports) * 8, 0)
        struct.pack_into("<Q", data, base_foff + int_off + len(imports) * 8, 0)

        # The descriptor itself, RVA form:
        fields = (
            gr_attrs,                          # grAttrs: dlattrRva normally
            rva_of(name_off),                  # rvaDLLName
            rva_of(hmod_off),                  # rvaHmod
            rva_of(iat_off),                   # rvaIAT
            rva_of(int_off),                   # rvaINT
            0,                                 # rvaBoundIAT: unbound
            0,                                 # rvaUnloadIAT
            dw_ts,                             # 0 = not bound; delaymut = stamp+1
        )
        if va_form:
            # The pre-VC7 form: every address field is an ABSOLUTE VA of
            # the image's own preferred base. Only representable when that
            # base plus the composed RVAs fits the descriptor's 32-bit
            # fields, which is exactly why that carrier is linked at
            # 0x40_0000.
            base = image_base_of(bytes(data))
            out = [fields[0]]
            for v in fields[1:]:
                if v == 0:
                    out.append(0)
                    continue
                va = base + v
                if va > 0xFFFFFFFF:
                    die(f"VA {va:#x} does not fit a descriptor's 32-bit field: "
                        f"the VA-form carrier must be linked below 4 GiB")
                out.append(va)
            fields = tuple(out)
        struct.pack_into("<8I", data, base_foff + di * 32, *fields)

    # The all-zero terminator descriptor, explicitly written (a file's
    # alignment padding is not guaranteed zero):
    struct.pack_into("<8I", data, base_foff + n * 32, *(0,) * 8)

    # dir[13] points here; VirtualSize grows to cover the composition
    # exactly (no gap between the directory's end and vsize -- the gate
    # pins that shape).
    struct.pack_into("<II", data, dir13_entry_off(bytes(data)), base_rva, dir_size)
    new_vsize = max(idata["vsize"], need_end)
    struct.pack_into("<I", data, idata["hdr_off"] + 8, new_vsize)
    idata["vsize"] = new_vsize
    # The loader copies SizeOfRawData bytes into the section's mapping,
    # which spans only ceil(VirtualSize / page) pages. That is safe for
    # every carrier here (raw and virtual spans share their last page)
    # -- unless the virtual end lands EXACTLY on a page boundary and the
    # raw span does not, in which case the copy would touch an unmapped
    # page. Refuse that shape instead of letting a boot find it.
    if idata["rawsize"] > new_vsize and (idata["vaddr"] + new_vsize) % 4096 == 0:
        die(f"composed .idata ends exactly on a page boundary ({base_rva + dir_size:#x}) "
            f"but its raw span is {idata['rawsize']:#x}: the loader's raw copy would "
            "cross into an unmapped page -- pad the composition")
    # SizeOfImage: the image's own statement of its span. Idempotent for
    # every carrier whose sections already fit.
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 4 + 20
    sect_align = struct.unpack_from("<I", data, opt + 32)[0]
    span = 0
    for s in sections:
        span = max(span, s["vaddr"] + max(s["vsize"], s["rawsize"]))
    want_size = (span + sect_align - 1) & ~(sect_align - 1)
    old_size = struct.unpack_from("<I", data, opt + 56)[0]
    if want_size > old_size:
        struct.pack_into("<I", data, opt + 56, want_size)
    return base_rva, dir_size


def verify_delay_dir(data: bytes, base_rva, dir_size, descriptors, va_form=False):
    """Re-parse what we wrote: exact dir[13] bounds, the exact composed
    descriptor COUNT (every one non-zero, and the record after the last
    one the all-zero terminator -- a composition that lost or gained a
    descriptor cannot pass), exact 8 fields, exact name strings, exact
    IAT==INT arrays and terminators, exact hint/name blobs."""
    vdata = bytes(data)
    d2, sec2 = parse_headers(vdata)
    got_rva, got_size = d2[13]
    if got_rva != base_rva or got_size != dir_size:
        die(f"re-parse: dir[13] is ({got_rva:#x},{got_size:#x}), composed was ({base_rva:#x},{dir_size:#x})")
    n = len(descriptors)
    foff, s = rva_to_file(sec2, base_rva)
    for di in range(n):
        fields = struct.unpack_from("<8I", vdata, foff + di * 32)
        if all(v == 0 for v in fields):
            die(f"re-parse: descriptor {di} of {n} is all zero -- the composed descriptor count is short")
    term = struct.unpack_from("<8I", vdata, foff + n * 32)
    if any(t != 0 for t in term):
        die(f"re-parse: the record after the {n} composed descriptors is not the all-zero terminator")
    for di, (dll_name, imports, dw_ts, gr_attrs, mismatch) in enumerate(descriptors):
        fields = struct.unpack_from("<8I", vdata, foff + di * 32)
        tag = f"descriptor {di}" if n > 1 else "descriptor"
        if fields[0] != gr_attrs:
            die(f"re-parse: {tag} grAttrs is {fields[0]:#x}, composed {gr_attrs:#x}")
        if va_form:
            # Every non-zero address field must be a VA of THIS image's
            # preferred base (not a bare RVA), and the values must fit the
            # 32-bit fields the format gave them.
            vb = image_base_of(vdata)
            for k in (1, 2, 3, 4):
                if fields[k] < vb:
                    die(f"re-parse: {tag} field {k} is {fields[k]:#x}, below the preferred base "
                        f"{vb:#x}: not a VA")
            fields = (fields[0],) + tuple(
                0 if v == 0 else v - vb for v in fields[1:7]
            ) + (fields[7],)
        for k, label in ((1, "rvaDLLName"), (2, "rvaHmod"), (3, "rvaIAT")):
            if fields[k] == 0:
                die(f"re-parse: {tag} {label} is zero")
        if fields[5] != 0 or fields[6] != 0:
            die(f"re-parse: {tag} rvaBoundIAT/rvaUnloadIAT are not zero")
        if fields[7] != dw_ts:
            die(f"re-parse: {tag} dwTimeStamp is {fields[7]:#x}, composed {dw_ts:#x}")
        at, _ = rva_to_file(sec2, fields[1])
        lim = s["raw"] + s["rawsize"]
        end = vdata.index(b"\x00", at, lim)
        if vdata[at:end] != dll_name:
            die(f"re-parse: {tag} module name is {vdata[at:end]!r}, composed {dll_name!r}")
        nimp = len(imports)
        iat_foff, _ = rva_to_file(sec2, fields[3])
        int_foff, _ = rva_to_file(sec2, fields[4])
        for j in range(nimp + 1):
            (it,) = struct.unpack_from("<Q", vdata, iat_foff + j * 8)
            (nt,) = struct.unpack_from("<Q", vdata, int_foff + j * 8)
            if mismatch and j == 0:
                # The composed-in contradiction must be EXACTLY there, and
                # nowhere else: an accidental second one would make the
                # kernel's refusal ambiguous about which entry it saw.
                if it == nt:
                    die(f"re-parse: {tag} composed IAT/INT difference is gone")
                continue
            if it != nt:
                die(f"re-parse: {tag} INT entry {j} does not equal its IAT entry ({it:#x} vs {nt:#x})")
            if j == nimp:
                if it != 0:
                    die(f"re-parse: {tag} terminator entry is {it:#x}, must be 0")
                continue
            kind, payload = imports[j]
            if kind == "ordinal":
                if it != ORDINAL_FLAG64 | payload:
                    die(f"re-parse: {tag} ordinal import {j} is {it:#x}, "
                        f"composed {ORDINAL_FLAG64 | payload:#x}")
            else:
                at2, s2 = rva_to_file(sec2, it)
                lim2 = s2["raw"] + s2["rawsize"]
                (hint,) = struct.unpack_from("<H", vdata, at2)
                if hint != 0:
                    die(f"re-parse: {tag} named import {j}'s hint is {hint}, composed 0")
                end2 = vdata.index(b"\x00", at2 + 2, lim2)
                if vdata[at2 + 2:end2] != payload:
                    die(f"re-parse: {tag} named import {j} is {vdata[at2 + 2:end2]!r}, "
                        f"composed {payload!r}")


def main() -> None:
    if len(sys.argv) != 12:
        die("usage: patch_pe_delay.py <provider_dll> <delaygood_exe> <delaybad_exe> "
            "<delaymut_exe> <delayattrs_exe> <delaymis_exe> <delayva_exe> <delayvabias_exe> "
            "<delaymany_exe> <delaythunks_exe> <delayfull_exe>")
    (prov_path, good_path, bad_path, mut_path,
     attrs_path, mis_path, va_path, vabias_path,
     many_path, thunks_path, full_path) = sys.argv[1:12]

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

    one = lambda dll, imports, ts, attrs, mis=False: [(dll, imports, ts, attrs, mis)]
    # The cap-boundary carriers. DELAY_MAX_DESCRIPTORS (16) and
    # DELAY_MAX_THUNKS (64) are EXCLUSIVE in the kernel's walk: the
    # terminator is read BEFORE the cap is consulted, so exactly 16
    # descriptors and exactly 64 thunks are the legal maxima. A refusal
    # alone cannot show that (a check placed before the terminator read
    # would refuse the 17th descriptor identically while wrongly
    # refusing a legal 16), so BOTH sides get a file: delaymany carries
    # the 17th descriptor, delaythunks the 65th thunk entry, and
    # delayfull the two legal maxima at once -- 16 descriptors whose
    # first carries 64 thunks -- which the walk must ACCEPT.
    many = [(PROVIDER_NAME, [("name", b"crossfn")], 0, 1, False)] * 17
    thunks = one(PROVIDER_NAME, [("name", b"crossfn")] * 65, 0, 1)
    full = ([(PROVIDER_NAME, [("name", b"crossfn")] * 64, 0, 1, False)]
            + [(PROVIDER_NAME, [("name", b"crossfn")], 0, 1, False)] * 15)

    carriers = [
        ("delaygood", good_path, one(PROVIDER_NAME,
                                     [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                     0, 1), False, "EAGER-RESOLVE expected"),
        ("delaybad", bad_path, one(b"nowhere.dll", [("name", b"crossfn")], 0, 1), False,
         "MissingDelayDllImport refusal expected"),
        ("delaymut", mut_path, one(PROVIDER_NAME,
                                   [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                   stamp + 1, 1), False,
         "MalformedDelayImport (dwTimeStamp) refusal expected"),
        ("delayattrs", attrs_path, one(PROVIDER_NAME,
                                       [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                       0, 3), False,
         "MalformedDelayImport (grAttrs) refusal expected"),
        ("delaymis", mis_path, one(PROVIDER_NAME,
                                   [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                   0, 1, mis=True), False,
         "MalformedDelayImport (INT != IAT) refusal expected"),
        ("delayva", va_path, one(PROVIDER_NAME,
                                 [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                 0, 0), False,
         "MalformedDelayImport (VA form, fields below base) refusal expected"),
        ("delayvabias", vabias_path, one(PROVIDER_NAME,
                                         [("name", b"crossfn"), ("ordinal", crossmark_ordinal)],
                                         0, 0), True,
         "MalformedDelayImport (VA form, relocated image) refusal expected"),
        ("delaymany", many_path, many, False,
         "MalformedDelayImport (17th descriptor) refusal expected"),
        ("delaythunks", thunks_path, thunks, False,
         "MalformedDelayImport (65th thunk) refusal expected"),
        ("delayfull", full_path, full, False,
         "16 descriptors x 64 thunks ACCEPTED expected"),
    ]
    all_paths = [c[1] for c in carriers]
    if len(set(all_paths)) != len(all_paths):
        die("every carrier must be a SEPARATE file")

    for label, path, descriptors, va_form, expect in carriers:
        bio = bytearray(open(path, "rb").read())
        bdirs, bsecs = parse_headers(bytes(bio))
        if bdirs[13] != (0, 0):
            die(f"the {label} carrier already carries a delay directory (double patch?)")
        base_rva, dir_size = compose_delay_dir(
            bio, bsecs, bdirs, descriptors, va_form)
        verify_delay_dir(bytes(bio), base_rva, dir_size, descriptors, va_form)
        open(path, "wb").write(bytes(bio))
        nimp = sum(len(d[1]) for d in descriptors)
        print(f"  patcher: {label} composed at rva {base_rva:#x} ({dir_size} bytes): "
              f"{len(descriptors)} descriptor(s), {nimp} import(s), "
              f"grAttrs {descriptors[0][3]:#x}, dwTimeStamp {descriptors[0][2]:#x} -> {expect}")


if __name__ == "__main__":
    main()
