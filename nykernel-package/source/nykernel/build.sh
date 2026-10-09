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
as --64 userprog/prog_pe.s -o userprog/prog_pe.o
ld -m i386pep --subsystem native -e _start -o userprog/userprog_pe.exe userprog/prog_pe.o
as --64 userprog/prog_pe_reloc.s -o userprog/prog_pe_reloc.o
ld -m i386pep --subsystem native -e _start -o userprog/userprog_pe_reloc.exe userprog/prog_pe_reloc.o

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
