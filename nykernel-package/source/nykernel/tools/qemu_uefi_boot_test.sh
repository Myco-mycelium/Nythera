#!/usr/bin/env bash
#
# qemu_uefi_boot_test.sh -- the UEFI-side twin of qemu_boot_test.sh.
# Instead of GRUB/Multiboot2, the same --qemu-test kernel image is
# embedded into the Phase 10 UEFI stub (include_bytes!), the stub is
# installed on a FAT ESP image, and OVMF boots the whole handoff
# natively: UEFI stub -> uefi_entry -> kernel reaches BootPhase::Ready
# and exits via isa-debug-exit, exactly like the BIOS path's exit-code
# contract. 0 = the UEFI handoff boots through Ready in an emulated
# CPU; nonzero = it did not. This is what CI should run for the UEFI
# path -- not "does the stub exist", but "does OVMF actually boot it".
#
# Every step rebuilds from source in the same order as the interactive
# builds (kernel BEFORE stub, because the stub embeds the kernel image
# bytes at compile time), so a stale embed cannot pass by accident.

set -euo pipefail
cd "$(dirname "$0")/.."

# --- build the --qemu-test kernel (also builds every userprog artifact) ---
./build.sh --qemu-test

# --- build the UEFI stub against the fresh kernel image ---
# The stub crate is deliberately not a workspace member (see
# uefi/Cargo.toml): it builds with the built-in x86_64-unknown-uefi
# target while the kernel one builds with x86_64-nyrqis-kernel.json.
# Its cargo config discovery walks up and finds this kernel's
# .cargo/config.toml, so the same RUSTC_BOOTSTRAP requirement applies.
if ! command -v rust-lld >/dev/null 2>&1; then
    _sysroot="$(rustc --print sysroot 2>/dev/null || true)"
    if [[ -n "$_sysroot" ]]; then
        _rust_lld="$(find "$_sysroot" -name rust-lld -type f 2>/dev/null | head -n1)"
        if [[ -n "$_rust_lld" ]]; then
            export PATH="$(dirname "$_rust_lld"):$PATH"
        fi
    fi
fi
(cd uefi && RUSTC_BOOTSTRAP=1 cargo build --target x86_64-unknown-uefi)
STUB_EFI=uefi/target/x86_64-unknown-uefi/debug/nyrqis_uefi_stub.efi
if [[ ! -f "$STUB_EFI" ]]; then
    echo "FAIL: stub built but not found at $STUB_EFI"
    exit 1
fi

# --- build a FRESH ESP image, install the stub at the UEFI fallback path ---
# uefi/dist/ is UNTRACKED by policy (see nykernel-package/.gitignore --
# it is bring-up-era generated state, not a source), so this script can
# never depend on it existing: a fresh CI checkout doesn't have it, and
# the first runner run failed exactly there (mcopy under set -e). The
# image is generated from scratch with mformat/mmd/mcopy instead: 64 MiB
# (131072 sectors), FAT, the one fallback-path directory the UEFI boot
# manager looks for. No loop mounts, no root. The staged dist/EFI tree
# is refreshed too so any inspected tree mirrors what actually boots.
rm -f uefi/dist/esp.img
mkdir -p uefi/dist uefi/dist/EFI/BOOT
mformat -i uefi/dist/esp.img -C -T 131072
mmd -i uefi/dist/esp.img ::EFI ::EFI/BOOT
mcopy -i uefi/dist/esp.img "$STUB_EFI" ::EFI/BOOT/BOOTX64.EFI
cp "$STUB_EFI" uefi/dist/EFI/BOOT/BOOTX64.EFI
test -s uefi/dist/esp.img || {
    echo "FAIL: ESP image generation produced nothing"
    exit 1
}

# --- fresh writable OVMF variable store from the shipped template ---
# pflash writes persist into VARS.fd run to run; starting from the
# template each time keeps the test deterministic (no accumulated
# stale firmware state from previous runs can change the outcome).
cp uefi/tools/OVMF_VARS_4M.fd uefi/dist/VARS.fd

set +e
# 120 s settle-bound, catalogued: same reasoning as the BIOS gate's
# cap (tools/qemu_boot_test.sh) -- on the ubuntu-26 CI runners the
# whole OVMF-spawn + demo chain can exceed the previous 45 s under
# shared cores; a cap this generous keeps the required gate outside
# the runner's speed jitter.
#
# -no-shutdown is DROPPED below for the same catalogued QEMU 10.x
# delta as the BIOS gate: the isa-debug-exit write routes through
# qemu_system_shutdown_request_with_code(), which -no-shutdown would
# have kept stopped-not-exited. -no-reboot stays: reboot-loop guard.
timeout 120 qemu-system-x86_64 \
    -machine q35 \
    -m 256 \
    -drive if=pflash,format=raw,readonly=on,file=uefi/tools/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=uefi/dist/VARS.fd \
    -drive format=raw,file=uefi/dist/esp.img \
    -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
    -serial stdio \
    -display none \
    -no-reboot \
    > target/qemu-uefi-test-stdout.log 2>&1
QEMU_STATUS=$?
set -e

echo "--- guest serial output ---"
cat target/qemu-uefi-test-stdout.log
echo "----------------------------"

# isa-debug-exit maps guest value V to host exit code (V << 1) | 1.
# success() writes 0x10 -> host exit code 33. Same contract as the
# BIOS path's qemu_boot_test.sh, asserted on the UEFI handoff now.
if [[ "$QEMU_STATUS" -eq 33 ]]; then
    echo "PASS: OVMF booted the UEFI stub; kernel reached BootPhase::Ready and exited cleanly"
    exit 0
elif [[ "$QEMU_STATUS" -eq 124 ]]; then
    echo "FAIL: UEFI boot test timed out (firmware, stub, or kernel hung before Ready)"
    exit 1
else
    echo "FAIL: unexpected QEMU exit status $QEMU_STATUS"
    exit 1
fi
