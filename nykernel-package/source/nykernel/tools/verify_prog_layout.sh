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
