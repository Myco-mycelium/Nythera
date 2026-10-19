#!/usr/bin/env bash
#
# build.sh -- builds boot/boot.o (GNU as), the Rust kernel staticlib
# (via cargo build-std for the freestanding target -- see
# .cargo/config.toml and IMPLEMENTATION_STATUS.md for why RUSTC_BOOTSTRAP
# is required in this environment), and links them into a single
# Multiboot2-bootable ELF using linker.ld.
#
# Usage: ./build.sh [--release] [--qemu-test]

set -euo pipefail
cd "$(dirname "$0")"

# The freestanding target spec asks for `rust-lld` by name (linker-flavor
# gnu-lld). A rustup toolchain ships rust-lld inside its sysroot bin
# directory, which is NOT on PATH by default, so a bare `./build.sh` would
# otherwise fail at the link step with "rust-lld not found". Prefer a
# rust-lld already reachable on PATH (e.g. a distro lld); otherwise prepend
# the active toolchain's sysroot bin directory. No-op when rust-lld is
# already available.
if ! command -v rust-lld >/dev/null 2>&1; then
    _sysroot="$(rustc --print sysroot 2>/dev/null || true)"
    if [[ -n "$_sysroot" ]]; then
        _rust_lld="$(find "$_sysroot" -name rust-lld -type f 2>/dev/null | head -n1)"
        if [[ -n "$_rust_lld" ]]; then
            export PATH="$(dirname "$_rust_lld"):$PATH"
        fi
    fi
fi

PROFILE=debug
CARGO_FLAGS=()
FEATURES=()
if [[ "${1:-}" == "--release" || "${2:-}" == "--release" ]]; then
    PROFILE=release
    CARGO_FLAGS+=(--release)
fi
if [[ "${1:-}" == "--qemu-test" || "${2:-}" == "--qemu-test" ]]; then
    FEATURES+=(--features qemu_test)
fi

mkdir -p target

echo "==> building userprog/userprog.elf and userprog/userprog_b.elf (real ELF64 test executables for experimental::elf)"
as --64 userprog/prog.s -o userprog/prog.o
ld -n -T userprog/linker.ld -o userprog/userprog.elf userprog/prog.o
as --64 userprog/prog_b.s -o userprog/prog_b.o
ld -n -T userprog/linker.ld -o userprog/userprog_b.elf userprog/prog_b.o
as --64 userprog/prog_yield.s -o userprog/prog_yield.o
ld -n -T userprog/linker_yield.ld -o userprog/userprog_yield.elf userprog/prog_yield.o
as --64 userprog/prog_spin.s -o userprog/prog_spin.o
ld -n -T userprog/linker_yield.ld -o userprog/userprog_spin.elf userprog/prog_spin.o
as --64 userprog/prog_pie.s -o userprog/prog_pie.o
ld -pie --no-dynamic-linker -o userprog/userprog_pie.elf userprog/prog_pie.o
as --64 userprog/prog_dyn.s -o userprog/prog_dyn.o
echo "==> link userprog/userprog_dyn.elf (a real shared object, `ld -shared`: DT_HASH/DT_SYMTAB/DT_STRTAB + GLOB_DAT/64 relocations)"
ld -shared --hash-style=both -o userprog/userprog_dyn.elf userprog/prog_dyn.o
echo "==> link userprog/userprog_cross_{lib,app}.elf (real .so PAIR for cross-object resolution: lib exports cross_value/dup_symbol; app references cross_value UNDEF via GOTPCREL and re-exports dup_symbol as the interposition control)"
as --64 userprog/prog_cross_lib.s -o userprog/prog_cross_lib.o
ld -shared --hash-style=both -o userprog/userprog_cross_lib.elf userprog/prog_cross_lib.o
as --64 userprog/prog_cross_app.s -o userprog/prog_cross_app.o
ld -shared --hash-style=both -o userprog/userprog_cross_app.elf userprog/prog_cross_app.o
echo "==> link userprog/userprog_cross_all_*: the VERSIONED, SONAMED provider pair + the all-in-one DEPENDENT (DT_NEEDED + VERNEED/VERSYM + lazy-capable PLT)"
as --64 userprog/prog_cross_all_lib.s -o userprog/prog_cross_all_lib.o
ld -shared --hash-style=both -soname crosslib.so.1 --version-script=userprog/ver_lib_v1.ld -o userprog/userprog_cross_all_lib.elf userprog/prog_cross_all_lib.o
ld -shared --hash-style=both -soname crosslib.so.1 --version-script=userprog/ver_lib_v2.ld -o userprog/userprog_cross_all_lib2.elf userprog/prog_cross_all_lib.o
as --64 userprog/prog_cross_all.s -o userprog/prog_cross_all.o
ld -shared --hash-style=both -o userprog/userprog_cross_all.elf userprog/prog_cross_all.o userprog/userprog_cross_all_lib.elf
echo "==> link userprog/userprog_cross_all4.elf: provider D (SONAME crosslib4.so.1; the THIRD catalog hop -- the chain's reliance on provider A is recorded by link line, as always)"
as --64 userprog/prog_cross_all4_lib.s -o userprog/prog_cross_all4_lib.o
ld -shared --no-as-needed --hash-style=both -soname crosslib4.so.1 -o userprog/userprog_cross_all4.elf userprog/prog_cross_all4_lib.o userprog/userprog_cross_all_lib.elf
echo "==> link userprog/userprog_cross_all3.elf: provider C (SONAME crosslib3.so.1; its link against provider D gives the depth-3 chain its MIDDLE hop)"
as --64 userprog/prog_cross_all3_lib.s -o userprog/prog_cross_all3_lib.o
ld -shared --no-as-needed --hash-style=both -soname crosslib3.so.1 -o userprog/userprog_cross_all3.elf userprog/prog_cross_all3_lib.o userprog/userprog_cross_all4.elf
echo "==> link userprog/userprog_cross_all2.elf: provider B (SONAME crosslib2.so.1; its link against provider C records the chain's crosslib2 -> crosslib3 edge, the FIRST of THREE catalog hops)"
as --64 userprog/prog_cross_all2_lib.s -o userprog/prog_cross_all2_lib.o
ld -shared --no-as-needed --hash-style=both -soname crosslib2.so.1 -o userprog/userprog_cross_all2.elf userprog/prog_cross_all2_lib.o userprog/userprog_cross_all3.elf
echo "==> link userprog/userprog_interposer.elf: the interposition overlay (SONAME interposer.so.1; exports a CONFLICTING dup_symbol -- the interposition proof's other side)"
as --64 userprog/prog_interpos.s -o userprog/prog_interpos.o
ld -shared --hash-style=both -soname interposer.so.1 -o userprog/userprog_interposer.elf userprog/prog_interpos.o
echo "==> link userprog/userprog_deps.elf: dependent #3 -- lazy-capable PLT + versioned GLOB_DAT + INTERPOSED UNDEF GLOB_DAT + WEAK UNDEF (weak bind, no provider matches it at all)"
as --64 userprog/prog_deps_dep.s -o userprog/prog_deps_dep.o
ld -shared --no-as-needed --hash-style=both -o userprog/userprog_deps.elf userprog/prog_deps_dep.o userprog/userprog_cross_all2.elf userprog/userprog_cross_all_lib.elf
echo "==> link userprog/userprog_weak.elf: the WEAK VERSION-FLAG artifact -- its ONLY undefined symbol is a WEAK bind of cross_value@VERS_1.0, so GNU ld records the VERNEED dependency with VER_FLG_WEAK (the gate pins Flags: WEAK)"
as --64 userprog/prog_weak_dep.s -o userprog/prog_weak_dep.o
ld -shared --no-as-needed --hash-style=both -o userprog/userprog_weak.elf userprog/prog_weak_dep.o userprog/userprog_cross_all_lib.elf
echo "==> link userprog/userprog_plt_res.elf: the OWN-.plt GNU-protocol dependent -- an ORDINARY `ld -shared` product (real linker .plt + .got.plt + JUMP_SLOT cross_fn); the increment is that the KERNEL arms the object's OWN .plt/.got.plt (GOT[1] link-map cookie, GOT[2] resolver thunk) instead of emitting synthetic int-0x88 stubs"
as --64 userprog/prog_plt_res.s -o userprog/prog_plt_res.o
ld -shared --hash-style=both -o userprog/userprog_plt_res.elf userprog/prog_plt_res.o userprog/userprog_cross_all_lib.elf
echo "==> link userprog/userprog_pltprov.elf + userprog_multi.elf: the MULTI-ENTRY GNU pair -- a second sonamed provider (pltprov.so.1, two function exports fn_a/fn_b) and a dependent linking BOTH providers, so its .rela.plt carries TWO JUMP_SLOT entries bound lazily, one per first call, through one link-map cookie"
as --64 userprog/prog_pltprov.s -o userprog/prog_pltprov.o
ld -shared --hash-style=both -soname pltprov.so.1 -o userprog/userprog_pltprov.elf userprog/prog_pltprov.o
as --64 userprog/prog_multi_plt.s -o userprog/prog_multi_plt.o
ld -shared --hash-style=both --no-as-needed -o userprog/userprog_multi.elf userprog/prog_multi_plt.o userprog/userprog_cross_all_lib.elf userprog/userprog_pltprov.elf
echo "==> link userprog/userprog_pltless.elf: the PLT-LESS boundary artifact -- a real `ld -shared` dependent whose ONLY relocation is a versioned GLOB_DAT (no .rela.plt at all): requesting the GNU own-.plt mode on it is a typed MalformedDynamicTables refusal; its eager load resolves with no GNU side effects"
as --64 userprog/prog_pltless.s -o userprog/prog_pltless.o
ld -shared --no-as-needed --hash-style=both -o userprog/userprog_pltless.elf userprog/prog_pltless.o userprog/userprog_cross_all_lib.elf
# This environment's GNU ld records weak binds but never emits VER_FLG_WEAK
# on the vernaux; the weak-version-FLAG half of the kernel's rule needs a
# real flagged artifact, so the build composes it (readelf-verified by the gate).
python3 tools/patch_weak_verneed.py userprog/userprog_weak.elf || exit 1

echo "==> link userprog pe-import PAIR: the provider DLL (its own ld-synthesized .edata) + the client linked DIRECTLY against it -- GNU ld emits the client's REAL import table (IMAGE_DIRECTORY_ENTRY_IMPORT) and IAT; no dlltool, no import library"
as --64 userprog/prog_pe_import_dll.s -o userprog/prog_pe_import_dll.o
# ImageBase 0x180000000000: PML4 index 0x18. The bases must be OUTSIDE
# the shared PML4[0] identity subtree: pe_demo's program lives at
# 0x140000000 under index 0, and a shared-index collision surfaced as a
# PageAlreadyMapped on the second import demo's header page (boot-caught,
# fixed at cause by picking private indexes for both halves).
ld -m i386pep --shared --enable-auto-import --image-base=0x180000000000 -o userprog/userprog_pe_import_dll.dll userprog/prog_pe_import_dll.o
as --64 userprog/prog_pe_import_app.s -o userprog/prog_pe_import_app.o
as --64 userprog/prog_pe.s -o userprog/prog_pe.o
ld -m i386pep --subsystem native -e _start -o userprog/userprog_pe.exe userprog/prog_pe.o
as --64 userprog/prog_pe_reloc.s -o userprog/prog_pe_reloc.o
ld -m i386pep --subsystem native -e _start -o userprog/userprog_pe_reloc.exe userprog/prog_pe_reloc.o

# The FORWARDER HOST (a second real provider) and the checked
# COMPOSITION step: links DIRECTLY against the primary provider is how
# the client got its first two imports; this second provider is linked
# first LIKE a normal DLL -- three real exported stub functions -- and
# then tools/patch_pe_forwarders.py composes, self-verified:
# (a) the forwarder-host's three EAT entries become directory-internal
#     forwarder STRING references (into raw-section padding the
#     extension freshly owns -- the extension is checked to not touch
#     any other section's bytes), and
# (b) the CLIENT's crossmark OFT thunk becomes ORDINAL form with the
#     ordinal computed from the provider's own export tables (never
#     assumed). Patcher refuses the build on any mismatch it re-parses.
as --64 userprog/prog_pe_import_fwdprov.s -o userprog/prog_pe_import_fwdprov.o
ld -m i386pep --shared --enable-auto-import --image-base=0x1c0000000000 -o userprog/userprog_pe_import_fwdprov.dll userprog/prog_pe_import_fwdprov.o
ld -m i386pep --subsystem native -e _start --image-base=0x140000000000 -o userprog/userprog_pe_import_app.exe userprog/prog_pe_import_app.o userprog/userprog_pe_import_dll.dll userprog/userprog_pe_import_fwdprov.dll
python3 tools/patch_pe_forwarders.py userprog/userprog_pe_import_fwdprov.dll userprog/userprog_pe_import_dll.dll userprog/userprog_pe_import_app.exe

echo "==> readelf LAYOUT GATE: every embedded userprog artifact is fact-checked (SONAMEs, version tables, NEEDED chains, PLT/JUMP_SLOT, WEAK dynsym, PE edata/idata) BEFORE kernel code ever sees them"
./tools/verify_prog_layout.sh

echo "==> assembling boot/boot.s"
as --64 boot/boot.s -o target/boot.o --gdwarf-2 2>/dev/null || {
    as --64 boot/boot.s -o target/boot.o
}

echo "==> building nykernel staticlib (${PROFILE})"
# -Zjson-target-spec is required by newer toolchains (>=1.8x) to accept
# the custom `x86_64-nyrqis-kernel.json` target specified in
# .cargo/config.toml; older rustc (the apt 1.75 documented in
# IMPLEMENTATION_STATUS.md) accepted a bare .json target without it.
# Harmless/no-op on those older toolchains under RUSTC_BOOTSTRAP=1.
RUSTC_BOOTSTRAP=1 cargo build -Zjson-target-spec "${CARGO_FLAGS[@]}" "${FEATURES[@]}"

LIBDIR="target/x86_64-nyrqis-kernel/${PROFILE}"

echo "==> linking kernel.elf"
ld -n --gc-sections -T linker.ld -o target/kernel.elf target/boot.o \
    "${LIBDIR}/libnykernel.a"

echo "==> done: target/kernel.elf"
file target/kernel.elf
