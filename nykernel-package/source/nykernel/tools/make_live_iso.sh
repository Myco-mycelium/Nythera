#!/usr/bin/env bash
#
# make_live_iso.sh -- builds the bootable live ISO from THIS tree.
#
# What the image is: a GRUB El Torito disc that loads `target/kernel.elf`
# through the Multiboot2 header this kernel's boot path already implements,
# with one menu entry per device mode. Booting it gives a live session on
# any BIOS/CSM or legacy-booting machine, with nothing written to disk.
#
# What it deliberately is NOT: a UEFI-bootable disc. This project's UEFI
# path is the SIGNED stub (ADR-0014/0028/0029), and a GRUB-EFI El Torito
# image would load the kernel through GRUB instead -- bypassing the carrier
# verification the whole trust chain exists for. A UEFI live medium is
# therefore a separate artifact (the ESP the stub produces), not this ISO.
# The script says so in its output rather than leaving the absence to be
# discovered the hard way.
#
# Usage:
#   ./tools/make_live_iso.sh            # build the kernel, then the ISO
#   ./tools/make_live_iso.sh --no-build # package the existing kernel.elf
#   ./tools/make_live_iso.sh --out /path/to/name.iso
#
# Output: dist/nyrqis-live.iso (+ dist/nyrqis-live.iso.sha256), and the
# kernel image's own digest so an image can be traced back to a build.

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD=1
OUT="dist/nyrqis-live.iso"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) BUILD=0; shift ;;
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

KERNEL_SHA="$(sha256sum target/kernel.elf | cut -d' ' -f1)"
ISO_SHA="$(sha256sum "$OUT" | cut -d' ' -f1)"
printf '%s  %s\n' "$ISO_SHA" "$(basename "$OUT")" > "$OUT.sha256"
# The kernel digest the image CARRIES, recorded beside it: an ISO in a
# repository is otherwise unattributable -- nothing on the disc says which
# build of the kernel it holds, so a stale artifact looks exactly like a
# fresh one. `tools/check_live_iso.sh` re-derives this from the ISO.
printf '%s\n' "$KERNEL_SHA" > "${OUT%.iso}.kernel.sha256"

du -h "$OUT" | awk '{print "==> wrote " $2 " (" $1 ")"}'
echo "==> kernel.elf sha256  $KERNEL_SHA"
echo "==> live ISO sha256   $ISO_SHA  (recorded in $OUT.sha256)"
echo "==> note: BIOS/CSM boot only. The UEFI path is the signed stub's ESP"
echo "    (ADR-0014/0028/0029); a GRUB-EFI disc would bypass that chain."
