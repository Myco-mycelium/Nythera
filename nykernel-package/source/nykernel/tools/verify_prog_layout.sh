#!/usr/bin/env bash
#
# verify_prog_layout.sh -- the CI layout gate for every userprog .so
# the kernel embeds via include_bytes!. Each file's contractually
# load-bearing facts (SONAMEs, version tables, DT_NEEDED chains,
# JUMP_SLOT/GLOB_DAT surface, weak dynsym entries) are read out with
# readelf and compared against the fact list the kernel's loader and
# demos are written against. A mismatch REFUSES THE BUILD: the file
# facts are the input the kernel's proofs are pinned to; kernel code
# must never see a drifted .so and discover it by panic.
#
# Usage: sourced by build.sh (after the .so links, before the kernel
# build), or run standalone: tools/verify_prog_layout.sh

set -euo pipefail
cd "$(dirname "$0")/.."

factcheck() {
    local desc="$1"
    local file="$2"
    shift 2
    if [[ ! -f "$file" ]]; then
        echo "FAIL: $file does not exist (build.sh order drifted past verify_prog_layout.sh?)" >&2
        exit 1
    fi
    local out
    out="$(readelf -d -V -r --dyn-syms "$file" 2>&1)"
    for pat in "$@"; do
        if ! printf '%s' "$out" | grep -qsE -- "$pat"; then
            echo "FAIL: userprog layout fact missing in $file: [$pat] ($desc)" >&2
            echo "     --- actual readelf surface: ---" >&2
            printf '%s\n' "$out" >&2
            exit 1
        fi
    done
    echo "  layout-gate: $file OK ($desc)"
}

# Provider 1 (V1 build): SONAME crosslib.so.1, provides VERS_1.0.
# Both hash styles are REQUIRED facts: DT_HASH stays the symbol-count
# authority, and the GNU table (bloom + bucket/chain) is what the
# kernel's lookup prefers when both exist.
factcheck "version+SONAME provider (V1)" userprog/userprog_cross_all_lib.elf \
    'SONAME.*crosslib\.so\.1' \
    'VERDEF' \
    'VERS_1\.0' \
    '\(HASH\)' \
    'GNU_HASH'

# Provider 1 (V2 build): SAME SONAME, VERS_2.0 only (the version
# negative control's other half).
factcheck "version+SONAME provider (V2 control)" userprog/userprog_cross_all_lib2.elf \
    'SONAME.*crosslib\.so\.1' \
    'VERS_2\.0'

# The versioned dependent: NEEDED crosslib.so.1, VERNEED VERS_1.0,
# a JUMP_SLOT for cross_fn, the versioned GLOB_DAT for cross_value.
factcheck "full dependent (NEEDED + VERSYM + lazy PLT)" userprog/userprog_cross_all.elf \
    'NEEDED.*crosslib\.so\.1' \
    'VERNEED' \
    'VERS_1\.0' \
    'JUMP_SLO.*cross_fn' \
    'GLOB_DAT.*cross_value'

# The depth-3 catalog chain: B NEEDs C, C NEEDs D, D NEEDs A; the
# loader must auto-load B, C and D from the catalog before relocating
# the dependent (the demo's auto-load count fact pins 3).
#
# COVERAGE RULE for the loader's negative controls: the DEPENDENCY-CYCLE
# and DEPTH-CAP refusals run their deliberately-looping / deliberately-
# too-shallow catalogs over exactly the artifact set fact-checked
# above -- no control uses an object this gate has not verified, so a
# future fifth provider cannot enter the demo tree without its own
# factcheck block appearing here first.
factcheck "chain provider B (SONAME + hop to C)" userprog/userprog_cross_all2.elf \
    'SONAME.*crosslib2\.so\.1' \
    'NEEDED.*crosslib3\.so\.1'
factcheck "chain provider C (SONAME + hop to D)" userprog/userprog_cross_all3.elf \
    'SONAME.*crosslib3\.so\.1' \
    'NEEDED.*crosslib4\.so\.1'
factcheck "chain provider D (SONAME + hop to A)" userprog/userprog_cross_all4.elf \
    'SONAME.*crosslib4\.so\.1' \
    'NEEDED.*crosslib\.so\.1'

# The interposition overlay: its own SONAME + the conflicting export.
factcheck "interposition overlay" userprog/userprog_interposer.elf \
    'SONAME.*interposer\.so\.1' \
    'dup_symbol'

# Dependent #3: BOTH NEEDED edges, both version facts, the lazy PLT
# surface -- and the WEAK, versioned UNDEF ghost_fn (demos the weak-
# semantics: refused-if-strong, NULL-slot-if-weak).
factcheck "catalog dependent (chain + weak + lazy)" userprog/userprog_deps.elf \
    'NEEDED.*crosslib2\.so\.1' \
    'NEEDED.*crosslib\.so\.1' \
    'VERNEED' \
    'VERS_1\.0' \
    'JUMP_SLO.*cross_fn' \
    'GLOB_DAT.*cross_value' \
    'WEAK.*UND ghost_fn|UND ghost_fn.*WEAK' \
    'GLOB_DAT.*ghost_fn' \
    'GLOB_DAT.*interp_mark'

# The weak VERSION-FLAG artifact: its only UNDEF is the WEAK bind of
# the versioned cross_value; because NO bind in that version's
# dependency is strong, GNU ld MUST set VER_FLG_WEAK on the AUX entry
# (the kernel tolerates the unmet requirement through exactly that
# flag path when the scope cannot provide VERS_1.0).
factcheck "weak version-flag artifact" userprog/userprog_weak.elf \
    'NEEDED.*crosslib\.so\.1' \
    'Flags: WEAK' \
    'GLOB_DAT.*cross_value' \
    'WEAK.*UND|UND.*WEAK'

# The OWN-.plt GNU-protocol dependent: an ORDINARY `ld -shared`
# product -- the whole point. The gate pins that the REAL linker
# machinery is present (PLTGOT + JMPREL/PLTREL=RELA/PLTRELSZ, a real
# .plt JUMP_SLOT for cross_fn) and that the kernel demo's readelf-
# pinned addresses (PLTGOT 0x2fe8, JUMP_SLOT slot 0x3000, GNU initial
# plt+6 value 0x1016) match the file facts. These are the facts
# experimental::elf_dyn_plt_demo (and elf::arm_own_plt's no-touch
# JUMP_SLOT policy) are written against.
factcheck "own-.plt GNU-protocol dependent" userprog/userprog_plt_res.elf \
    'NEEDED.*crosslib\.so\.1' \
    '\(PLTGOT\)' \
    '\(JMPREL\)' \
    '\(PLTREL\).*RELA' \
    '\(PLTRELSZ\)' \
    'JUMP_SLO.*cross_fn' \
    'GLOB_DAT.*cross_value'

# The MULTI-ENTRY GNU pair: a second sonamed provider with two
# function exports, and a dependent linked against BOTH providers, so
# its .rela.plt carries TWO JUMP_SLOT entries (fn_a, fn_b) -- the
# resolver's first multi-entry object, one link-map cookie bundling
# TWO first-use binds.
factcheck "multi-entry GNU provider" userprog/userprog_pltprov.elf \
    'SONAME.*pltprov\.so\.1' \
    'fn_a' \
    'fn_b'
factcheck "multi-entry GNU dependent" userprog/userprog_multi.elf \
    'NEEDED.*crosslib\.so\.1' \
    'NEEDED.*pltprov\.so\.1' \
    '\(PLTGOT\)' \
    '\(JMPREL\)' \
    '\(PLTREL\).*RELA' \
    '\(PLTRELSZ\).*48' \
    'JUMP_SLO.*fn_b' \
    'JUMP_SLO.*fn_a'

# The PLT-LESS boundary artifact: a versioned GLOB_DAT-dependent with
# NO PLT machinery WHATEVER (no DT_JMPREL / DT_PLTGOT / DT_PLTRELSZ /
# DT_PLTREL). The ABSENCE is the load-bearing fact for the GNU
# protocol's boundary so it is gate-checked directly: if any of those
# tags ever appeared here, the refusal/untouched-side-effects proofs
# below would be testing a different artifact than the one embedded.
factcheck "PLT-less boundary artifact" userprog/userprog_pltless.elf \
    'NEEDED.*crosslib\.so\.1' \
    'GLOB_DAT.*cross_value'
pltless_tags="$(readelf -d userprog/userprog_pltless.elf | grep -cE '\(JMPREL\)|\(PLTGOT\)|\(PLTRELSZ\)|\(PLTREL\)' || true)"
if [ "$pltless_tags" -ne 0 ]; then
    echo "FAIL: userprog_pltless.elf carries PLT dynamic tags ($pltless_tags) -- the GNU-protocol boundary artifact must NOT have any" >&2
    exit 1
fi
echo "  layout-gate: userprog/userprog_pltless.elf OK (zero PLT tags: the absence is pinned)"

echo "  layout-gate: all embedded userprog .so facts verified"

# =========================================================================
# PE32+ import-resolution pair (experimental::pe_import_demo). Both files
# are plain `ld -m i386pep` products: the provider DLL's .edata and the
# client's .idata/IAT are ld-synthesized from a DIRECT dll link -- no
# dlltool exists on this host and none is needed. The facts below are what
# pe.rs's import walk and pe_import_demo's assertions are written against;
# the same-loudness gate rule applies: a drift REFUSES THE BUILD.
# =========================================================================
pafacts() {
    local desc="$1"
    local file="$2"
    local tool="$3"
    shift 3
    if [[ ! -f "$file" ]]; then
        echo "FAIL: $file does not exist (build.sh order drifted past verify_prog_layout.sh?)"
        exit 1
    fi
    local out
    out="$($tool "$file" 2>&1)"
    for pat in "$@"; do
        if ! printf '%s' "$out" | grep -qsE -- "$pat"; then
            echo "FAIL: userprog PE layout fact missing in $file: [$pat] ($desc)" >&2
            echo "     --- actual $(basename "$tool") surface: ---" >&2
            printf '%s\n' "$out" >&2
            exit 1
        fi
    done
    echo "  layout-gate: $file OK ($desc)"
}

# The provider DLL: a REAL Export Directory (Entry 0 nonzero), at the
# pinned ImageBase, exporting exactly the two fixture symbols by name
# with the pinned EAT RVAs and Ordinal Base 1.
pafacts "PE import provider DLL (real export directory)" userprog/userprog_pe_import_dll.dll \
    "objdump -p" \
    'ImageBase.*0000180000000000' \
    'Entry 0 0000000000003000.*Export Directory' \
    'Export Address Table -- Ordinal Base 1' \
    '\[ *0\] \+base\[ *1\] 1000 Export RVA' \
    '\[ *1\] \+base\[ *2\] 2000 Export RVA' \
    'crossfn' \
    'crossmark'
# ...and exactly two names in its name/ordinal table (NUMBER pinned, not
# just presence: a third export would change the demo's ==2 assertion's
# meaning).
prov_names="$(objdump -p userprog/userprog_pe_import_dll.dll | sed -n '/Ordinal\/Name Pointer/,/^$/p' | grep -cE '\] cross')"
if [ "$prov_names" -ne 2 ]; then
    echo "FAIL: provider DLL exports $prov_names name(s) -- the demo pins exactly 2 (crossfn, crossmark)" >&2
    exit 1
fi
echo "  layout-gate: userprog/userprog_pe_import_dll.dll OK (exactly 2 exported names: the count is pinned)"

# The dependent client: a REAL Import Directory (Entry 1 nonzero) at the
# pinned ImageBase, NO export directory (Entry 0 -- the negative-control
# carrier's load-bearing ABSENCE: the client exe registered as its own
# provider is an empty table), the pinned DLL name, BOTH intended
# imports by hint/name, and the faithful Windows-shaped call site
# (indirect call through the IAT slot, plus the demo's own trap).
pafacts "PE import dependent client (real import directory)" userprog/userprog_pe_import_app.exe \
    "objdump -p" \
    'ImageBase.*0000140000000000' \
    'Entry 0 0000000000000000 00000000 Export Directory' \
    'Entry 1 0000000000002000.*Import Directory'
pafacts "PE import dependent client (import table contents)" userprog/userprog_pe_import_app.exe \
    "objdump -x" \
    'DLL Name: userprog_pe_import_dll\.dll' \
    'crossfn' \
    'crossmark'
# The disassembly pin: ld emits `call *0x8d(%rip)` for the demo's
# `call *__imp_crossfn(%rip)` (GOT-relative, no literal __IAT_start__),
# so only the trap instruction is pinned by text; DAR-through-IAT is
# asserted at runtime by the demo itself.
pafacts "PE import dependent client (faithful IAT call site)" userprog/userprog_pe_import_app.exe \
    "objdump -d" \
    'int    \$0x8d'
iat_slots="$(objdump -x userprog/userprog_pe_import_app.exe | grep -cE '__imp_crossfn|__imp_crossmark|__imp_crossfn_fwd')"
if [ "$iat_slots" -lt 3 ]; then
    echo "FAIL: client carries $iat_slots __imp_* IAT definitions -- all three fixture imports (named, ordinal-composed, forwarder-resolved) must exist"
    exit 1
fi
echo "  layout-gate: userprog/userprog_pe_import_app.exe OK (both __imp_ IAT entries pinned; call sites are DAR-through-IAT)"
echo "  layout-gate: PE import pair facts verified"

# ---- the FORWARDER HOST's composed facts, from objdump's OWN eyes --
#
# The composition is not something only the patcher self-confesses:
# a plain `objdump -p` prints the grown directory's EAT entries as
# genuine `Forwarder RVA -- <string>` lines. Four facts are pinned:
# three names (the composition count), and each forwarder STRING'S
# CONTENT (it was composed from THESE exact bytes; the kernel's
# forwarder chase resolves them as strings).
pafacts "PE forwarder host (real export directory, 3 names)" userprog/userprog_pe_import_fwdprov.dll \
    "objdump -p" \
    'Name Pointer/Ordinal] Table.*00000003' \
    'Export Address Table.*00000003'
pafacts "PE forwarder host (the three forwarder strings as objdump sees them)" userprog/userprog_pe_import_fwdprov.dll \
    "objdump -p" \
    'Forwarder RVA -- userprog_pe_import_dll[.]dll[.]crossfn' \
    'Forwarder RVA -- userprog_pe_import_fwdprov[.]dll[.]crossfn_cycle' \
    'Forwarder RVA -- nobody[.]dll[.]crossfn'
echo "  layout-gate: PE forwarder host composition facts verified"

# ---- the BOUND-IMPORT composition's facts, from objdump's OWN eyes --
#
# The same rule the forwarder composition established: the composition
# is not something only the patcher self-confesses. A plain
# `objdump -x` prints the composed bound directory as a genuine
# `Entry b ... Bound Import Directory` line (letter-indexed: 11 -> b).
# The stamp/prefill VALUES are then cross-checked against the OTHER
# FILES' bytes (below): the stamps move every build (time(2) at link
# time) so no gate number exists for them — the invariant is the
# cross-file AGREEMENT, which the python block pins file-side exactly
# as the kernel pins it image-side (two parsers, same file).
pafacts "PE bound-import good client (composed bounded directory, Entry b)" userprog/userprog_pe_import_app.exe \
    "objdump -x" \
    'Entry b 0000000000002100 [0-9a-f]{8} Bound Import Directory'
pafacts "PE bound-import bad client (its OWN Entry b line -- 16-digit RVA as objdump prints it)" userprog/userprog_pe_bound_bad.exe \
    "objdump -x" \
    'Entry b [0-9a-f]{16} [0-9a-f]{8} Bound Import Directory'
pafacts "PE bound-import refstale carrier (its OWN Entry b line -- 16-digit RVA as objdump prints it)" userprog/userprog_pe_bound_refstale.exe \
    "objdump -x" \
    'Entry b [0-9a-f]{16} [0-9a-f]{8} Bound Import Directory'
echo "  layout-gate: PE bound-import composition facts verified"

# The bound composition's cross-FILE invariants (file bytes only, no
# demo constants involved): the good client's fresh entry stamp == the
# provider file's own .edata TimeDateStamp; the stale entry is stamp+1
# (a stamp no file carries); the inert entry parses; the honored slots
# hold PROVIDER_BASE + the EAT addresses read from the provider's own
# directory; the stale slot holds the OLD-build contradiction
# (PROVIDER_BASE + crossmark's EAT); the bad client's matching-stamp
# entry's single slot holds the patcher's WRONG constant. And the
# demo-driver/build twins for base and wrong value agree.
python3 - <<'EOF' || exit 1
import re, struct

def pe_dirs_sorted(data):
    e = struct.unpack_from('<I', data, 0x3C)[0]
    opt = e + 4 + 20
    ndirs = struct.unpack_from('<I', data, opt + 108)[0]
    return [(struct.unpack_from('<II', data, opt + 112 + i*8)) for i in range(ndirs)], opt

def pe_image_base(data):
    e = struct.unpack_from('<I', data, 0x3C)[0]
    return struct.unpack_from('<Q', data, e + 4 + 20 + 24)[0]

def edata_stamp(data, opt):
    erva, esize = struct.unpack_from('<II', data, opt + 112 + 0*8)
    nsec = struct.unpack_from('<H', data, struct.unpack_from('<I', data, 0x3C)[0] + 6)[0]
    coff = struct.unpack_from('<I', data, 0x3C)[0] + 4
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    sh = coff + 20 + optsize
    for i in range(nsec):
        b = sh + i*40
        name = data[b:b+8].rstrip(b'\x00')
        vsize, vaddr, rawsize, raw = struct.unpack_from('<IIII', data, b+8)
        if vaddr <= erva < vaddr + max(vsize, rawsize):
            return struct.unpack_from('<I', data, raw + (erva - vaddr) + 4)[0]
    raise SystemExit('FAIL: provider .edata section not found')

def bound_section(data, opt):
    nsec = struct.unpack_from('<H', data, struct.unpack_from('<I', data, 0x3C)[0] + 6)[0]
    coff = struct.unpack_from('<I', data, 0x3C)[0] + 4
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    sh = coff + 20 + optsize
    sections = []
    for i in range(nsec):
        b = sh + i*40
        vsize, vaddr, rawsize, raw = struct.unpack_from('<IIII', data, b+8)
        sections.append((vaddr, vsize, rawsize, raw))
    return sections

def rva_at(data, sections, rva):
    for vaddr, vsize, rawsize, raw in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return raw + (rva - vaddr)
    raise SystemExit(f'FAIL: rva {rva:#x} not inside any section')

prov = open('userprog/userprog_pe_import_dll.dll','rb').read()
good = open('userprog/userprog_pe_import_app.exe','rb').read()
bado = open('userprog/userprog_pe_bound_bad.exe','rb').read()

pdirs, popt = pe_dirs_sorted(prov)
stamp = edata_stamp(prov, popt)
if stamp in (0, 0xFFFFFFFF): raise SystemExit('FAIL: provider stamp sentinel')
print(f'  layout-gate: provider .edata TimeDateStamp {stamp:#x} (non-sentinel) -- OK')

gdirs, gopt = pe_dirs_sorted(good)
g_rva, g_size = gdirs[11]
if g_rva == 0 or g_size == 0: raise SystemExit('FAIL: good client has no bound directory after composition')
gsec = bound_section(good, gopt)
base = pe_image_base(prov)  # 0x180000000000 pinned by the patcher's own check
records = []
for k in range(8):
    at = rva_at(good, gsec, g_rva)
    st, off = struct.unpack_from('<IH', good, at + k*8)[:2]
    if st == 0 and off == 0:
        if k == 3: break
        raise SystemExit(f'FAIL: good client bound entry {k} zero before the terminator')
    n_at = rva_at(good, gsec, g_rva)
    name = good[n_at + off: n_at + off + 64].split(b'\x00')[0]
    records.append((name, st))
if len(records) != 3:
    raise SystemExit(f'FAIL: good client bound directory carries {len(records)} real entries, composed exactly 3')
names = [r[0] for r in records]
if names[0] != b'userprog_pe_import_dll.dll' or names[1] != b'userprog_pe_import_fwdprov.dll' or names[2] != b'nobody.dll':
    raise SystemExit(f'FAIL: bound module names drifted: {names}')
if records[0][1] != stamp:
    raise SystemExit(f'FAIL: fresh entry stamp {records[0][1]:#x} != provider .edata stamp {stamp:#x}')
if records[1][1] != stamp + 1:
    raise SystemExit(f'FAIL: stale entry stamp is {records[1][1]:#x}, must be fresh + 1 ({stamp+1:#x})')
print('  layout-gate: good client bound directory -- 3 composed entries, fresh==provider stamp, stale==fresh+1, nobody.dll inert -- OK')

# The honored prefills and the stale contradiction, from the file:
imp_rva = gdirs[1][0]
desc_off = None
for d in range(16):
    at = rva_at(good, gsec, imp_rva + d*20)
    oft, ts, fc, nrva, ft = struct.unpack_from('<IIIII', good, at)
    if oft == 0 and nrva == 0 and ft == 0:
        break
    n_at = rva_at(good, gsec, nrva)
    if good[n_at:n_at + 64].split(b'\x00')[0] == b'userprog_pe_import_dll.dll':
        desc_off = (oft, ft)
    stale_ft_off = None
for d in range(16):
    at = rva_at(good, gsec, imp_rva + d*20)
    oft, ts, fc, nrva, ft = struct.unpack_from('<IIIII', good, at)
    if oft == 0 and nrva == 0 and ft == 0:
        break
    n_at = rva_at(good, gsec, nrva)
    nm = good[n_at:n_at + 64].split(b'\x00')[0]
    if nm == b'userprog_pe_import_fwdprov.dll':
        stale_ft_off = rva_at(good, gsec, ft)
if desc_off is None or stale_ft_off is None:
    raise SystemExit('FAIL: import descriptors for both providers not found in the good client')
oft_rva, ft_rva = desc_off
slots = [struct.unpack_from('<Q', good, rva_at(good, gsec, ft_rva + j*8))[0] for j in range(2)]
if slots != [base + 0x1000, base + 0x2000]:
    raise SystemExit(f'FAIL: honored prefills drifted: {slots}')
stale_val = struct.unpack_from('<Q', good, stale_ft_off)[0]
if stale_val != base + 0x2000:
    raise SystemExit(f'FAIL: stale slot carries {stale_val:#x}, must be the OLD-build contradiction {base + 0x2000:#x}')
print('  layout-gate: honored prefills == provider base + crossfn/crossmark EAT; stale slot == OLD-build contradiction -- OK')

bdirs, bopt = pe_dirs_sorted(bado)
b_rva, b_size = bdirs[11]
if b_rva == 0 or b_size == 0:
    raise SystemExit('FAIL: bad client has no bound directory')
bsec = bound_section(bado, bopt)
at = rva_at(bado, bsec, b_rva)
bad_stamp, bad_off, bad_refs = struct.unpack_from('<IHH', bado, at)
if bad_stamp != stamp:
    raise SystemExit(f'FAIL: bad client declares stamp {bad_stamp:#x}, must MATCH the provider stamp {stamp:#x}')
n_at = rva_at(bado, bsec, b_rva)
bad_name = bado[n_at + bad_off: n_at + bad_off + 64].split(b'\x00')[0]
if bad_name != b'userprog_pe_import_fwdprov.dll':
    raise SystemExit(f'FAIL: bad client bound name {bad_name!r} drifted')
# The ref-aware composition: ONE forwarder ref follows the descriptor
# (the composed NumberOfModuleForwarderRefs field makes the walk
# unambiguous). The bad carrier's ref is ALL-FRESH -- the provider's
# OWN stamp -- so the honor gate must flip by the VALUE verification
# alone, never by a stale ref.
if bad_refs != 1:
    raise SystemExit(f'FAIL: bad client NumberOfModuleForwarderRefs {bad_refs}, composed exactly 1')
ref_stamp, ref_off = struct.unpack_from('<IH', bado, at + 8)[:2]
ref_name = bado[n_at + ref_off: n_at + ref_off + 64].split(b'\x00')[0]
if ref_name != b'userprog_pe_import_dll.dll':
    raise SystemExit(f'FAIL: bad client forwarder ref module {ref_name!r} drifted')
if ref_stamp != stamp:
    raise SystemExit(f'FAIL: bad client forwarder ref stamp {ref_stamp:#x}, must be FRESH (the provider stamp {stamp:#x})')
# its single IAT slot must hold the WRONG value
imp_rva_bad = bdirs[1][0]
for d in range(16):
    at = rva_at(bado, bsec, imp_rva_bad + d*20)
    oft, ts, fc, nrva, ft = struct.unpack_from('<IIIII', bado, at)
    if oft == 0 and nrva == 0 and ft == 0:
        break
    slot_off = rva_at(bado, bsec, ft)
    val = struct.unpack_from('<Q', bado, slot_off)[0]
    break
wrong = 0xDEADBEEF00001000
if val != wrong:
    raise SystemExit(f'FAIL: bad client IAT slot is {val:#x}, must be the composed {wrong:#x}')
print('  layout-gate: bad client -- FRESH descriptor + ONE all-FRESH forwarder ref, single crossfn slot carries the wrong value -- OK')

# The refstale carrier: the same single-entry composition except the
# REF's stamp is provider + 1 (a stamp NO file carries) -- the gate
# flips OFF by the ref alone and the ruling becomes DISCARD, not
# refuse. The directory is otherwise identical to the bad client's:
# the composed pair differs by that one stamp byte.
rsto = open('userprog/userprog_pe_bound_refstale.exe','rb').read()
rdirs, ropt = pe_dirs_sorted(rsto)
rs_rva, rs_size = rdirs[11]
if rs_rva == 0 or rs_size == 0:
    raise SystemExit('FAIL: refstale carrier has no bound directory')
if rs_size != b_size:
    raise SystemExit(f'FAIL: refstale bound directory size {rs_size} != bad client {b_size} (the pair must compose identically)')
rsec = bound_section(rsto, ropt)
at = rva_at(rsto, rsec, rs_rva)
rs_stamp, rs_off, rs_refs = struct.unpack_from('<IHH', rsto, at)
if rs_stamp != stamp:
    raise SystemExit(f'FAIL: refstale descriptor stamp {rs_stamp:#x}, must MATCH the provider stamp {stamp:#x}')
rn_at = rva_at(rsto, rsec, rs_rva)
rs_name = rsto[rn_at + rs_off: rn_at + rs_off + 64].split(b'\x00')[0]
if rs_name != bad_name:
    raise SystemExit(f'FAIL: refstale descriptor module {rs_name!r} != the bad client {bad_name!r}')
if rs_refs != 1:
    raise SystemExit(f'FAIL: refstale NumberOfModuleForwarderRefs {rs_refs}, composed exactly 1')
rs_ref_stamp, rs_ref_off = struct.unpack_from('<IH', rsto, at + 8)[:2]
rs_ref_name = rsto[rn_at + rs_ref_off: rn_at + rs_ref_off + 64].split(b'\x00')[0]
if rs_ref_name != ref_name:
    raise SystemExit(f'FAIL: refstale forwarder ref module {rs_ref_name!r} != the bad client ref {ref_name!r}')
if rs_ref_stamp != stamp + 1:
    raise SystemExit(f'FAIL: refstale forwarder ref stamp {rs_ref_stamp:#x}, must be stale (provider + 1 = {stamp + 1:#x})')
print('  layout-gate: refstale carrier -- same composition, SAME fresh descriptor, ref stamp provider+1: exactly one stamp byte decides refuse-vs-discard -- OK')

# The TWO-REF carriers (the multi-hop increment): the same
# single-descriptor composition but with TWO forwarder refs -- the
# PRIMARY PROVIDER then the FORWARDER HOST ITSELF, each declaring
# its own module's real stamp. ref2bad carries both FRESH; ref2stale
# is identical except the SECOND ref's stamp is the host's own + 1
# (a stamp NO file carries). File-side the gate pins the composed
# count=2 surfaces byte-exactly; the demo's Ok-path control pins the
# same facts from mapped bytes.
def bound_probe(data, label):
    dirs, opt = pe_dirs_sorted(data)
    rva, size = dirs[11]
    if rva == 0 or size == 0:
        raise SystemExit(f'FAIL: {label} has no bound directory')
    sec = bound_section(data, opt)
    at = rva_at(data, sec, rva)
    desc_stamp, desc_off, ref_cnt = struct.unpack_from('<IHH', data, at)
    return dirs, sec, rva, size, at, desc_stamp, desc_off, ref_cnt

def name_at(data, sec, rva, off):
    n_at = rva_at(data, sec, rva)
    return data[n_at + off: n_at + off + 64].split(b'\x00')[0]

fprovo = open('userprog/userprog_pe_import_fwdprov.dll','rb').read()
fpdirs, fpopt = pe_dirs_sorted(fprovo)
fststamp = edata_stamp(fprovo, fpopt)
if fststamp in (0, 0xFFFFFFFF): raise SystemExit('FAIL: forwarder host stamp sentinel')
for label, path, second_ref_stamp, expect in (
    ('ref2bad', 'userprog/userprog_pe_bound_ref2bad.exe', fststamp, 'refuse with BoundValueCorrupt'),
    ('ref2stale', 'userprog/userprog_pe_bound_ref2stale.exe', fststamp + 1, 'DISCARD and resolve fresh'),
):
    two = open(path,'rb').read()
    tdirs, tsec, t_rva, t_size, t_at, t_stamp, t_off, t_refs = bound_probe(two, label)
    # 1 descriptor + 2 refs = 3 entries (24 B) + terminator (8 B)
    # + the three module names in entry order:
    want_size = 3*8 + 8 + (len(b'userprog_pe_import_fwdprov.dll')+1)*2 + len(b'userprog_pe_import_dll.dll')+1
    if t_size != want_size:
        raise SystemExit(f'FAIL: {label} bound directory size {t_size}, composed exactly {want_size}')
    if t_stamp != fststamp:
        raise SystemExit(f'FAIL: {label} descriptor stamp {t_stamp:#x}, must be the forwarder host\'s OWN stamp {fststamp:#x}')
    if t_refs != 2:
        raise SystemExit(f'FAIL: {label} NumberOfModuleForwarderRefs {t_refs}, composed exactly 2')
    ref1_stamp, ref1_off = struct.unpack_from('<IH', two, t_at + 8)[:2]
    ref2_stamp, ref2_off = struct.unpack_from('<IH', two, t_at + 16)[:2]
    ref1_name = name_at(two, tsec, t_rva, ref1_off)
    ref2_name = name_at(two, tsec, t_rva, ref2_off)
    if ref1_name != b'userprog_pe_import_dll.dll' or ref2_name != b'userprog_pe_import_fwdprov.dll':
        raise SystemExit(f'FAIL: {label} ref modules drifted: {ref1_name!r}, {ref2_name!r}')
    if ref1_stamp != stamp:
        raise SystemExit(f'FAIL: {label} first ref stamp {ref1_stamp:#x}, must be FRESH (the provider stamp {stamp:#x})')
    ref2_expect = "the host's OWN (FRESH)" if second_ref_stamp == fststamp else "host + 1 (STALE)"
    if ref2_stamp != second_ref_stamp:
        raise SystemExit(f'FAIL: {label} second ref stamp {ref2_stamp:#x}, must be {ref2_expect} {second_ref_stamp:#x}')
    # the single wrong IAT slot, composed exactly:
    imp_rva_t = tdirs[1][0]
    for d in range(16):
        at = rva_at(two, tsec, imp_rva_t + d*20)
        oft, ts, fc, nrva, ft = struct.unpack_from('<IIIII', two, at)
        if oft == 0 and nrva == 0 and ft == 0:
            break
        slot_off = rva_at(two, tsec, ft)
        val = struct.unpack_from('<Q', two, slot_off)[0]
        break
    if val != wrong:
        raise SystemExit(f'FAIL: {label} IAT slot is {val:#x}, must be the composed {wrong:#x}')
    print(f'  layout-gate: {label} carrier -- fresh descriptor + TWO refs in ENTRY ORDER (provider FRESH {stamp:#x}, host {second_ref_stamp:#x}), single crossfn slot carries the wrong value -> {expect} -- OK')

demo = open('src/experimental/pe_import_demo.rs').read()
m = re.search(r'const BAD_IMAGE_BASE: u64 = (0x[0-9a-f_]+);', demo)
if not m: raise SystemExit('FAIL: demo BAD_IMAGE_BASE constant drifted')
db = int(m.group(1).replace('_',''), 16)
b = pe_image_base(bado)
if b != db:
    raise SystemExit(f'FAIL: bad client ImageBase {b:#x} != demo BAD_IMAGE_BASE {db:#x}')
if (b >> 39) != (db >> 39):
    raise SystemExit('FAIL: bad client PML4 index drift')
idx = db >> 39
if db >= (1 << 47):
    raise SystemExit(f'FAIL: BAD_IMAGE_BASE {db:#x} sits in the 4-level x86-64 NON-CANONICAL hole (bit 47 set) -- every access #GP(0)s')
if idx == 0 or idx >= 512 or idx == 0x80:
    raise SystemExit(f'FAIL: BAD_IMAGE_BASE PML4 index {idx:#x} is not a private fresh index')
mw = re.search(r'const BOUND_WRONG_VALUE: u64 = (0x[0-9a-fA-F_]+);', demo)
if not mw or int(mw.group(1).replace('_',''),16) != wrong:
    raise SystemExit('FAIL: demo BOUND_WRONG_VALUE != the composed wrong value')
pt = open('tools/patch_pe_bound.py').read()
mp = re.search(r'WRONG = (0x[0-9a-fA-F_]+)', pt)
if not mp or int(mp.group(1).replace('_',''),16) != wrong:
    raise SystemExit('FAIL: patcher WRONG constant != the demo/composed wrong value')
print(f'  layout-gate: BAD_IMAGE_BASE {db:#x} (PML4 index {idx:#x}, private) and the {wrong:#x} contradiction -- demo/patcher/file all agree -- OK')

# Every remaining probe carrier's fixed ImageBase is pinned the same
# way: the file bytes LinkObject agree with the demo's constant, and
# the PML4 index is private and fresh (never the shared index-0
# identity subtree, never another demo's fixed region).
for base_const, path, label in (
    ('REFSTALE_IMAGE_BASE', 'userprog/userprog_pe_bound_refstale.exe', 'refstale carrier'),
    ('REF2BAD_IMAGE_BASE', 'userprog/userprog_pe_bound_ref2bad.exe', 'two-ref refusal carrier'),
    ('REF2STALE_IMAGE_BASE', 'userprog/userprog_pe_bound_ref2stale.exe', 'two-ref stale carrier'),
):
    mc = re.search(r'const ' + base_const + r': u64 = (0x[0-9a-fA-F_]+);', demo)
    if not mc:
        raise SystemExit(f'FAIL: demo {base_const} constant drifted')
    want_base = int(mc.group(1).replace("_", ""), 16)
    got_base = pe_image_base(open(path, 'rb').read())
    if got_base != want_base:
        raise SystemExit(f'FAIL: {label} ImageBase {got_base:#x} != demo {base_const} {want_base:#x}')
    pidx = want_base >> 39
    if want_base >= (1 << 47):
        raise SystemExit(f'FAIL: {label} ImageBase {want_base:#x} sits in the 4-level x86-64 NON-CANONICAL hole (bit 47 set, bits 63:48 clear) -- every access to the image #GP(0)s; link it below 2^47')
    if pidx == 0 or pidx >= 512 or pidx == 0x80:
        raise SystemExit(f'FAIL: {label} PML4 index {pidx:#x} is not a private fresh index')
    print(f'  layout-gate: {label} ImageBase {want_base:#x} (PML4 index {pidx:#x}, private, below 2^47) -- OK')
EOF

# ---- PE DELAY-LOAD composition: the three carriers' [13] facts, from
# the FILES: dir[13] present with a canonical composed span, no
# ordinary import directory at all (toolchain-raw no-import clients),
# grAttrs = dlattrRva (the only documented attribute), zero bound and
# unload RVAs, exact names, exact IAT == INT arrays (entry-for-entry,
# terminator included), the exact composed dwTimeStamp, and the exact
# private PML4-index ImageBases the demo consts declare.
python3 - <<'EOF' || exit 1
import re, struct

def pe_dirs_sorted(data):
    e = struct.unpack_from('<I', data, 0x3C)[0]
    opt = e + 4 + 20
    ndirs = struct.unpack_from('<I', data, opt + 108)[0]
    return [(struct.unpack_from('<II', data, opt + 112 + i*8)) for i in range(ndirs)], opt

def pe_image_base(data):
    e = struct.unpack_from('<I', data, 0x3C)[0]
    return struct.unpack_from('<Q', data, e + 4 + 20 + 24)[0]

def edata_stamp(data, opt):
    erva, esize = struct.unpack_from('<II', data, opt + 112 + 0*8)
    nsec = struct.unpack_from('<H', data, struct.unpack_from('<I', data, 0x3C)[0] + 6)[0]
    coff = struct.unpack_from('<I', data, 0x3C)[0] + 4
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    sh = coff + 20 + optsize
    for i in range(nsec):
        b = sh + i*40
        vsize, vaddr, rawsize, raw = struct.unpack_from('<IIII', data, b+8)
        if vaddr <= erva < vaddr + max(vsize, rawsize):
            return struct.unpack_from('<I', data, raw + (erva - vaddr) + 4)[0]
    raise SystemExit('FAIL: provider .edata section not found')

def sections_of(data):
    nsec = struct.unpack_from('<H', data, struct.unpack_from('<I', data, 0x3C)[0] + 6)[0]
    coff = struct.unpack_from('<I', data, 0x3C)[0] + 4
    optsize = struct.unpack_from('<H', data, coff + 16)[0]
    sh = coff + 20 + optsize
    out = []
    for i in range(nsec):
        b = sh + i*40
        name = data[b:b+8].rstrip(b'\x00')
        vsize, vaddr, rawsize, raw = struct.unpack_from('<IIII', data, b+8)
        out.append((name, vaddr, vsize, rawsize, raw))
    return out

def rva_at(sections, rva):
    for name, vaddr, vsize, rawsize, raw in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return raw + (rva - vaddr)
    raise SystemExit(f'FAIL: delay rva {rva:#x} not inside any section')

prov = open('userprog/userprog_pe_import_dll.dll','rb').read()
pdirs, popt = pe_dirs_sorted(prov)
stamp = edata_stamp(prov, popt)
demo = open('src/experimental/pe_import_demo.rs').read()
ord_mark = 1 << 63 | 2
for base_const, path, label, dll_name, n_imports, dw_ts, first_sym, second_ord in (
    ('DELAYGOOD_IMAGE_BASE', 'userprog/userprog_pe_delay_good.exe', 'delaygood', b'userprog_pe_import_dll.dll', 2, 0, b'crossfn', ord_mark),
    ('DELAYBAD_IMAGE_BASE', 'userprog/userprog_pe_delay_bad.exe', 'delaybad', b'nowhere.dll', 1, 0, b'crossfn', None),
    ('DELAYMUT_IMAGE_BASE', 'userprog/userprog_pe_delay_mut.exe', 'delaymut', b'userprog_pe_import_dll.dll', 2, stamp + 1, b'crossfn', ord_mark),
):
    data = open(path, 'rb').read()
    dirs, _opt = pe_dirs_sorted(data)
    secs = sections_of(data)
    # The carrier must be toolchain-raw with NO import: GNU ld still
    # declares dir[1] for the file (an empty .idata), so the pin is
    # the stronger, factual form: whatever dir[1] declares, its
    # descriptor array must be the ALL-ZERO terminator -- a declared
    # but EMPTY import table, never a descriptor.
    i_rva, i_size = dirs[1]
    if i_rva != 0:
        iat_desc = struct.unpack_from('<IIIII', data, rva_at(secs, i_rva))
        if any(t != 0 for t in iat_desc):
            raise SystemExit(f'FAIL: {label} carries a REAL import descriptor (the carrier must be toolchain-raw, no imports)')
    elif i_size != 0:
        raise SystemExit(f'FAIL: {label} dir[1] has a size without an rva')
    drva, dsize = dirs[13]
    if drva == 0 or dsize == 0:
        raise SystemExit(f'FAIL: {label} has no delay directory')
    idata = [s for s in secs if s[0] == b'.idata']
    if len(idata) != 1:
        raise SystemExit(f'FAIL: {label} must carry exactly one .idata section')
    id_vaddr, id_vsize = idata[0][1], idata[0][2]
    if drva != id_vaddr + id_vsize - dsize:
        raise SystemExit(f'FAIL: {label} delay dir at {drva:#x}, must sit exactly at .idata vaddr + (grown vsize - dir size) {id_vaddr + id_vsize - dsize:#x}')
    at = rva_at(secs, drva)
    fields = struct.unpack_from('<8I', data, at)
    if fields[0] != 1:
        raise SystemExit(f'FAIL: {label} grAttrs is {fields[0]:#x}, must be dlattrRva (0x1)')
    if fields[5] != 0 or fields[6] != 0:
        raise SystemExit(f'FAIL: {label} rvaBoundIAT/rvaUnloadIAT not zero')
    if fields[7] != dw_ts:
        raise SystemExit(f'FAIL: {label} dwTimeStamp is {fields[7]:#x}, composed exactly {dw_ts:#x}')
    term = struct.unpack_from('<8I', data, at + 32)
    if any(t != 0 for t in term):
        raise SystemExit(f'FAIL: {label} delay directory terminator is not the all-zero descriptor')
    n_at = rva_at(secs, fields[1])
    got_name = data[n_at: n_at + 64].split(b'\x00')[0]
    if got_name != dll_name:
        raise SystemExit(f'FAIL: {label} delay module name {got_name!r} drifted')
    # The canonical composed span: descriptor(32) + terminator(32) +
    # name + hmod qword + (n+1)*8 twice (IAT, INT) + ONE hint/name
    # blob PER NAMED import (the ordinal import composes no blob):
    blob_len = 2 + len(first_sym) + 1
    want = 64 + len(dll_name) + 1 + 8 + 2 * (n_imports + 1) * 8 + blob_len
    if dsize != want:
        raise SystemExit(f'FAIL: {label} dir[13] size {dsize}, composed exactly {want}')
    iat_off = rva_at(secs, fields[3])
    int_off = rva_at(secs, fields[4])
    for j in range(n_imports + 1):
        (it,) = struct.unpack_from('<Q', data, iat_off + j*8)
        (nt,) = struct.unpack_from('<Q', data, int_off + j*8)
        if it != nt:
            raise SystemExit(f'FAIL: {label} INT entry {j} != its IAT entry (IAT==INT is the composed pre-state)')
        if j == n_imports:
            if it != 0:
                raise SystemExit(f'FAIL: {label} delay IAT terminator is {it:#x}')
        elif j == 0:
            b_at = rva_at(secs, it)
            (hint,) = struct.unpack_from('<H', data, b_at)
            sym = data[b_at + 2: b_at + 2 + 16].split(b'\x00')[0]
            if hint != 0 or sym != first_sym:
                raise SystemExit(f'FAIL: {label} first named import drifted: hint {hint}, sym {sym!r}')
        elif second_ord is None:
            raise SystemExit(f'FAIL: {label} composed exactly {n_imports} imports (extra entry {j})')
        elif it != second_ord:
            raise SystemExit(f'FAIL: {label} ordinal import {j} is {it:#x}, composed {second_ord:#x}')
    mc = re.search(r'const ' + base_const + r': u64 = (0x[0-9a-fA-F_]+);', demo)
    if not mc:
        raise SystemExit(f'FAIL: demo {base_const} constant drifted')
    want_base = int(mc.group(1).replace('_', ''), 16)
    got_base = pe_image_base(data)
    if got_base != want_base:
        raise SystemExit(f'FAIL: {label} ImageBase {got_base:#x} != demo {base_const} {want_base:#x}')
    pidx = want_base >> 39
    # The delay-load round's cause-shaped rule: BAD/MUT were first
    # linked at 0x8000_0000_0000 -- bit 47 EXACTLY, so the base sits in
    # 4-level x86-64's non-canonical hole and the boot #GP(0)'d on the
    # very first byte read (QEMU's CR2, 0xffff800000000000, named the
    # tentatively-canonical pointer). No load image may ever be linked
    # there again, in any increment.
    if want_base >= (1 << 47):
        raise SystemExit(f'FAIL: {label} ImageBase {want_base:#x} sits in the 4-level x86-64 NON-CANONICAL hole (bit 47 set, bits 63:48 clear) -- every access to the image #GP(0)s; link it below 2^47')
    if pidx == 0 or pidx >= 512 or pidx == 0x80:
        raise SystemExit(f'FAIL: {label} PML4 index {pidx:#x} is not a private fresh index')
    kind = ('EAGER-RESOLVE surface' if label == 'delaygood' else
            ('MissingDelayDllImport surface' if label == 'delaybad' else 'dwTimeStamp!=0 refusal surface'))
    print(f"  layout-gate: {label} -- dir[13] canonical span {dsize} bytes at the .idata padding, grAttrs=dlattrRva, name {got_name.decode()}, IAT==INT ({n_imports} import(s)), dwTimeStamp {fields[7]:#x}, ImageBase {want_base:#x} (PML4 index {pidx:#x}, private) -- {kind} -- OK")
EOF
# ---- PE import pair: slot-constant cross-check (the digit-class rule) ----
#
# The ring-3 hang of this demo's first confident boots was NOT a kernel
# bug: the client assembly's slot constants were 16x below the slots the
# kernel mapped -- the same 11-vs-12-digit constant class that bit other
# demo artifacts (fault-time PML4 sweeps proved the kernel mapping was
# never wrong; the client walked PML4 index 8 instead of index 128).
# Two independent copies of the constants exist (the tracked client
# .s and the demo driver's consts), so an agreement drift in EITHER
# half would hang the boot while every other fact stayed green. Pinned
# here, at gate time: (1) the two copies must be EQUAL; (2) every slot
# constant must sit in the PRIVATE PML4 index 128 subtree -- bit_length
# 47 (2^46 has bit_length 47, i.e. the 0x4000_0000_0000 12-digit class;
# the 11-digit 0x40000000000 = 2^42 is bit_length 43 and is refused
# outright); (3) SLOT_B stays SLOT_A + 8, inside the page one mapping
# covers.
python3 - <<'EOF' || exit 1
import re, sys
s_src = open('userprog/prog_pe_import_app.s').read()
demo_src = open('src/experimental/pe_import_demo.rs').read()
def one(pat, why, text, to_int=False):
    m = re.search(pat, text)
    if not m:
        sys.exit(f"FAIL: got-by-check -- {why} not found by the gate parser (format drifted?)")
    v = m.group(1)
    return int(v.replace('_', ''), 16) if to_int else v
cs_a = one(r'(?m)^\.set SLOT_A, (0x[0-9a-f]+)\s*$', "client SLOT_A .set", s_src, True)
cs_b = one(r'(?m)^\.set SLOT_B, (0x[0-9a-f]+)\s*$', "client SLOT_B .set", s_src, True)
cs_c = one(r'(?m)^\.set SLOT_C, (0x[0-9a-f]+)\s*$', "client SLOT_C .set", s_src, True)
rs_a = one(r'const SLOT_A: u64 = (0x[0-9a-f_]+);', "demo const SLOT_A", demo_src, True)
rs_b = one(r'const SLOT_B: u64 = (0x[0-9a-f_]+);', "demo const SLOT_B", demo_src, True)
rs_c = one(r'const SLOT_C: u64 = (0x[0-9a-f_]+);', "demo const SLOT_C", demo_src, True)
rs_stack = one(r'const USER_STACK_ADDR: u64 = (0x[0-9a-f_]+);', "demo const USER_STACK_ADDR", demo_src, True)
print(f"  layout-gate: slot-constant cross-check -- client .set SLOT_A={cs_a:#x} SLOT_B={cs_b:#x} SLOT_C={cs_c:#x}; demo consts SLOT_A={rs_a:#x} SLOT_B={rs_b:#x} SLOT_C={rs_c:#x} stack={rs_stack:#x}")
for name, v, a, b in (("SLOT_A", cs_a, rs_a, rs_a), ("SLOT_B", cs_b, rs_b, rs_b), ("SLOT_C", cs_c, rs_c, rs_c)):
    if v != a or a != b:
        sys.exit(f"FAIL: PE import slot constants DIVERGE between the client .s and the demo driver ({name}: client={v:#x}, demo={a:#x}) -- the 16x-off digit-class hang, reintroduced")
if cs_c != cs_a + 16:
    sys.exit(f"FAIL: SLOT_C must be SLOT_A + 16 (one mapped page covers all three report slots; client {cs_c:#x} != {cs_a + 16:#x})")
for name, v in (("SLOT_A", cs_a), ("SLOT_B", cs_b), ("SLOT_C", cs_c), ("USER_STACK_ADDR", rs_stack)):
    if v.bit_length() != 47:
        sys.exit(f"FAIL: {name}={v:#x} is NOT the private-index-128 12-digit class (bit_length {v.bit_length()}, need 47 = 2^46) -- this is the 11-vs-12-digit bug class, refused at gate time")
if cs_b - cs_a != 8:
    sys.exit(f"FAIL: SLOT_B must be SLOT_A + 8 (client {cs_b - cs_a:+#x}); one mapped page must cover both report slots")
print("  layout-gate: PE import slot-constant digit-class rule -- both halves equal, 47-bit 2^46 private-index class, SLOT_B = SLOT_A + 8 -- OK")
EOF

# ---- negative-control window pin (the scratch-base rule) ----
#
# The kernel's negative controls load deliberately-refused objects and
# probe deliberately-unregistered trampolines at KERNEL-PICKED bases
# (the source constants named here). The rule: no embedded artifact may
# ever be PRE-LINKED (non-PIE, fixed placed) inside those windows --
# if one were, a control could land a load mid-artifact (or the probe
# page inside a real mapping) and the refusal would mean something
# different from what the demo asserts. Today every artifact is ET_DYN
# with PT_LOAD VirtAddr near 0 (the demos choose bases at runtime), so
# this check passes trivially -- that is exactly the property a gate
# pins BEFORE it becomes a regression.
# Windows (one open-handed stride above each base group, matching
# the source constants):
scratch_windows=(
    "0x6d000500000 0x6d000580000" # deps demo: cycle scratch 0x6D0_0050_0000, depth-cap scratch 0x6D0_0055_0000, lazy-binder probe page 0x6D0_0057_0000
    "0x6c000400000 0x6c000510000" # full demo refusal carriers SCRATCH_BASE_A 0x6C0_0040_0000 / SCRATCH_BASE_B 0x6C0_0050_0000
    "0x6d000700000 0x6d000800000" # plt demo: dependent 0x6D0_0070_0000, resolver thunk 0x6D0_0071_0000, private page 0x6D0_0072_0000, user stack 0x6D0_0073_0000
    "0x200000000000 0x200000100000" # bound-import bad client: fixed ImageBase 0x2000_0000_0000 (its entry point never runs; the pe-import demo probes its bound/import surfaces only) -- no ELF artifact may ever land in this PE image's private PML4-index-0x40 region either
    "0x300000000000 0x300000100000" # bound-import refstale carrier: fixed ImageBase 0x3000_0000_0000 (same never-executed probe-only rule; PML4 index 0x60)
    "0x500000000000 0x500000100000" # two-ref bound-import refusal carrier: fixed ImageBase 0x5000_0000_0000 (PML4 index 0xA0)
    "0x600000000000 0x600000100000" # two-ref bound-import stale carrier: fixed ImageBase 0x6000_0000_0000 (PML4 index 0xC0)
    "0x700000000000 0x700000100000" # delay-load good carrier: fixed ImageBase 0x7000_0000_0000 (PML4 index 0xE0)
    "0x410000000000 0x410000100000" # delay-load missing-DLL carrier: fixed ImageBase 0x4100_0000_0000 -- a canonical private subtree (the first failed boot proved 0x8000_0000_0000 is NON-canonical in 4-level x86-64: bit 47 set, upper bits clear, so every read of the image #GP(0)s; QEMU's CR2 showed 0xffff800000000000)
    "0x420000000000 0x420000100000" # delay-load bound-marker carrier: fixed ImageBase 0x4200_0000_0000 (canonical private subtree, same below-bit-47 rule)
)
for art in userprog/userprog_cross_all_lib.elf userprog/userprog_cross_all_lib2.elf \
           userprog/userprog_cross_all.elf userprog/userprog_cross_all2.elf \
           userprog/userprog_cross_all3.elf userprog/userprog_cross_all4.elf \
           userprog/userprog_interposer.elf userprog/userprog_deps.elf \
           userprog/userprog_weak.elf userprog/userprog_plt_res.elf \
           userprog/userprog_pltprov.elf userprog/userprog_multi.elf \
           userprog/userprog_pltless.elf; do
    out=$(readelf -l "$art" 2>&1) || { echo "FAIL: readelf -l on $art" >&2; exit 1; }
    # Each '  LOAD ...' program-header line: Offset VirtAddr PhysAddr FileSiz MemSiz Flags Align
    while read -r off vaddr paddr fsiz msiz flags align; do
        v=$(( $vaddr )); m=$(( $msiz )); end=$((v + m))
        for w in "${scratch_windows[@]}"; do
            read -r w0 w1 <<<"$w"
            s=$(( $w0 )); e=$(( $w1 ))
            if [ $v -lt $e ] && [ $end -gt $s ]; then
                echo "FAIL: $art has a PT_LOAD [0x$vaddr..0x$(printf %x $end)) intersecting the kernel negative-control window [0x$w0..0x$w1) -- refuse the artifact or re-place the control base" >&2
                exit 1
            fi
        done
    done < <(printf '%s\n' "$out" | grep -E '^  LOAD ')
    echo "  layout-gate: $art OK (no PT_LOAD inside the kernel negative-control windows)"

done
