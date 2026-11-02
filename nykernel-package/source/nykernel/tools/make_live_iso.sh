#!/usr/bin/env bash
#
# make_live_iso.sh -- builds the bootable live ISO from THIS tree.
#
# What the image is: a GRUB El Torito disc that loads `target/kernel.elf`
# through the Multiboot2 header this kernel's boot path already implements,
# with one menu entry per device mode. Booting it gives a live session on
# any BIOS/CSM or legacy-booting machine, with nothing written to disk.
#
# TWO artifacts come out of this script, because they are two different
# boot paths and pretending otherwise hides the trust boundary:
#
#   dist/nyrqis-live.iso        GRUB El Torito, Multiboot2 (BIOS/CSM).
#   dist/nyrqis-live-uefi.iso   El Torito EFI whose boot image is the
#                               SIGNED stub's ESP -- firmware loads the
#                               stub, the stub verifies the carrier
#                               (ADR-0014/0028/0029), then the kernel.
#
# The UEFI disc deliberately does NOT use GRUB-EFI: that would load the
# kernel through GRUB and skip the carrier verification the trust chain
# exists for.
#
# Usage:
#   ./tools/make_live_iso.sh             # build the kernel, then both ISOs
#   ./tools/make_live_iso.sh --no-build  # package the existing build
#   ./tools/make_live_iso.sh --bios-only | --uefi-only
#   ./tools/make_live_iso.sh --out /path/to/name.iso   (renames the BIOS one)
#
# Output: the ISO(s), each with a .sha256 beside it, plus
# dist/nyrqis-live.kernel.sha256 recording the kernel image they carry.

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD=1
BIOS=1
UEFI=1
OUT="dist/nyrqis-live.iso"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) BUILD=0; shift ;;
        --bios-only) UEFI=0; shift ;;
        --uefi-only) BIOS=0; shift ;;
        --out) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

command -v grub-mkrescue >/dev/null 2>&1 || { echo "FAIL: grub-mkrescue not found (install grub-pc-bin / grub-common)" >&2; exit 1; }

if [[ "$BUILD" == "1" ]]; then
    echo "==> building the kernel (./build.sh)"
    ./build.sh
fi
[[ -s target/kernel.elf ]] || { echo "FAIL: target/kernel.elf missing -- run ./build.sh or drop --no-build" >&2; exit 1; }

KERNEL_SHA="$(sha256sum target/kernel.elf | cut -d' ' -f1)"
printf '%s\n' "$KERNEL_SHA" > "dist/nyrqis-live.kernel.sha256" 2>/dev/null || {
    mkdir -p dist
    printf '%s\n' "$KERNEL_SHA" > dist/nyrqis-live.kernel.sha256
}

# ---------------------------------------------------------------------
# BIOS/CSM disc: GRUB loads the kernel through Multiboot2.
# ---------------------------------------------------------------------
if [[ "$BIOS" == "1" ]]; then
WORK=target/live-iso
rm -rf "$WORK"
mkdir -p "$WORK/boot/grub"
cp target/kernel.elf "$WORK/boot/kernel.elf"

# One entry per device mode. The mode is selected by the kernel's own
# `nyrqis.mode=` parsing (crate::device_mode), so the disc and the kernel
# cannot drift: an unknown token would be REPORTED by the kernel, and this
# menu only uses the four it accepts.
cat > "$WORK/boot/grub/grub.cfg" <<'EOF'
set timeout=10
set default=0

menuentry "Nyrqis (desktop)" {
    multiboot2 /boot/kernel.elf nyrqis.mode=desktop
    boot
}
menuentry "Nyrqis (tablet)" {
    multiboot2 /boot/kernel.elf nyrqis.mode=tablet
    boot
}
menuentry "Nyrqis (phone)" {
    multiboot2 /boot/kernel.elf nyrqis.mode=phone
    boot
}
menuentry "Nyrqis (console / serial)" {
    multiboot2 /boot/kernel.elf nyrqis.mode=console
    boot
}
menuentry "Nyrqis (automatic: display class decides)" {
    multiboot2 /boot/kernel.elf
    boot
}
EOF

mkdir -p "$(dirname "$OUT")"
echo "==> packaging $OUT (GRUB El Torito, Multiboot2)"
grub-mkrescue -o "$OUT" "$WORK" >/dev/null 2>&1

[[ -s "$OUT" ]] || { echo "FAIL: grub-mkrescue produced no image" >&2; exit 1; }

ISO_SHA="$(sha256sum "$OUT" | cut -d' ' -f1)"
printf '%s  %s\n' "$ISO_SHA" "$(basename "$OUT")" > "$OUT.sha256"
du -h "$OUT" | awk '{print "==> wrote " $2 " (" $1 ")"}'
echo "==> live ISO sha256   $ISO_SHA  (recorded in $OUT.sha256)"
fi

# ---------------------------------------------------------------------
# UEFI disc: the El Torito EFI boot image IS the signed stub's ESP, so the
# firmware boots our stub (carrier verified) and never GRUB.
# ---------------------------------------------------------------------
if [[ "$UEFI" == "1" ]]; then
    command -v cargo >/dev/null 2>&1 || { echo "FAIL: cargo not found (needed for the UEFI stub)" >&2; exit 1; }
    command -v mformat >/dev/null 2>&1 || { echo "FAIL: mtools not found (needed to build the ESP)" >&2; exit 1; }
    command -v xorriso >/dev/null 2>&1 || { echo "FAIL: xorriso not found" >&2; exit 1; }

    # rust-lld is the UEFI target's linker and cargo will not find it on
    # its own; same discovery the UEFI boot gate uses.
    if ! command -v rust-lld >/dev/null 2>&1; then
        _sysroot="$(rustc --print sysroot 2>/dev/null || true)"
        if [[ -n "$_sysroot" ]]; then
            _rust_lld="$(find "$_sysroot" -name rust-lld -type f 2>/dev/null | head -n1)"
            if [[ -n "$_rust_lld" ]]; then
                export PATH="$(dirname "$_rust_lld"):$PATH"
            fi
        fi
    fi

    echo "==> building the UEFI stub (the BIOS chain uses the same kernel image)"
    (cd uefi && RUSTC_BOOTSTRAP=1 cargo build --target x86_64-unknown-uefi)
    STUB_EFI=uefi/target/x86_64-unknown-uefi/debug/nyrqis_uefi_stub.efi
    [[ -s "$STUB_EFI" ]] || { echo "FAIL: stub built but not found at $STUB_EFI" >&2; exit 1; }

    ESP=target/live-esp.img
    rm -f "$ESP"
    # 32 MiB: the stub is ~15 MiB because it EMBEDS the kernel image
    # (`include_bytes!` of target/kernel.elf, verified against its own
    # sha256 at boot), so the ESP cannot be smaller than that. Report the
    # stub's size rather than letting a too-small image surface later as
    # an mcopy failure.
    STUB_BYTES="$(stat -c %s "$STUB_EFI")"
    ESP_BYTES=$((32 * 1024 * 1024))
    if (( STUB_BYTES > ESP_BYTES - 1024 * 1024 )); then
        echo "FAIL: the stub is $STUB_BYTES bytes, too large for a 32 MiB ESP" >&2
        exit 1
    fi
    mformat -i "$ESP" -C -T 65536
    mmd -i "$ESP" ::EFI ::EFI/BOOT
    mcopy -i "$ESP" "$STUB_EFI" ::EFI/BOOT/BOOTX64.EFI
    [[ -s "$ESP" ]] || { echo "FAIL: ESP image generation produced nothing" >&2; exit 1; }

    UWORK=target/live-uefi
    rm -rf "$UWORK"
    mkdir -p "$UWORK/boot"
    cp "$ESP" "$UWORK/boot/efi.img"

    UOUT=dist/nyrqis-live-uefi.iso
    echo "==> packaging $UOUT (El Torito EFI -> signed stub ESP)"
    xorriso -as mkisofs -o "$UOUT" \
        -isohybrid-gpt-basdat -eltorito-alt-boot \
        -e boot/efi.img -no-emul-boot -V NYRQIS_UEFI \
        "$UWORK" >/dev/null 2>&1
    [[ -s "$UOUT" ]] || { echo "FAIL: xorriso produced no UEFI image" >&2; exit 1; }

    USHA="$(sha256sum "$UOUT" | cut -d' ' -f1)"
    printf '%s  %s\n' "$USHA" "$(basename "$UOUT")" > "$UOUT.sha256"
    du -h "$UOUT" | awk '{print "==> wrote " $2 " (" $1 ")"}'
    echo "==> UEFI ISO sha256   $USHA  (recorded in $UOUT.sha256)"
fi

echo "==> kernel.elf sha256  $KERNEL_SHA  (recorded in dist/nyrqis-live.kernel.sha256)"
