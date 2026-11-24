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

# Windows hosts carry no distro GRUB/xorriso/mtools/QEMU and no GCC binutils;
# tools/win_toolchain.sh puts them (and the ELF/PE binutils dispatchers) on
# PATH. On Linux this branch is not taken.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    # shellcheck disable=SC1091
    source tools/win_toolchain.sh || exit 1
fi

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

# Two ways to build the BIOS disc, because GRUB's Windows build ships
# grub-mkimage but NOT grub-mkrescue (it is a shell wrapper around mkimage and
# xorriso that only exists on Unix hosts). Both routes produce the same thing:
# an El Torito image whose boot image is GRUB's i386-pc-eltorito core, holding
# the modules and prefix needed to read grub.cfg and the kernel off the disc.
GRUB_MKRESCUE=0
GRUB_MKIMAGE=0
command -v grub-mkrescue >/dev/null 2>&1 && GRUB_MKRESCUE=1
command -v grub-mkimage  >/dev/null 2>&1 && GRUB_MKIMAGE=1
if [[ "$GRUB_MKRESCUE" == "0" && "$GRUB_MKIMAGE" == "0" ]]; then
    echo "FAIL: neither grub-mkrescue nor grub-mkimage found (install grub-pc-bin / grub-common, or the GRUB Windows build)" >&2
    exit 1
fi

if [[ "$BUILD" == "1" ]]; then
    # The discs embed the kernel twice over (the stub `include_bytes!`es it,
    # and GRUB loads it), so the DEBUG symbols are pure weight here: the
    # image the stub verifies is the stripped one, produced before the
    # carrier and the digest are computed, so nothing is pinned to bytes
    # that no longer exist. Symbols are kept (`--strip-debug`), which the
    # stub needs to resolve `uefi_entry` by name.
    echo "==> building the kernel, stripped for the image (./build.sh)"
    NYKERNEL_STRIP_DEBUG=1 ./build.sh
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

# Resolve the disc's own root instead of trusting $root. A loader that reads
# this grub.cfg from somewhere other than the disc -- chainloading it, mapping
# the ISO through a loopback, or starting it with root on the medium the ISO
# file lives on -- leaves $root on a device where /boot/kernel.elf does not
# exist. Every entry then dies with "file `/boot/kernel.elf' not found" and
# "you need to load the kernel first", which is indistinguishable from a
# corrupted image and sends the reader looking in the wrong place. `search`
# names the device that really holds the kernel and points root at it. From a
# plain CD or a dd'd stick $root is already correct, so this is a no-op there.
#
# This is a guard, not a workaround for one manager's bug: Ventoy 1.1.07, BIOS,
# "Boot in normal mode" (measured from an IDE disk and from USB mass storage)
# passes the correct root by itself, and a disc without this block boots there
# too. The block is what keeps the image independent of that behaviour.
# ($cmdpath is not a portable substitute: grub-mkrescue leaves it empty.)
if ! [ -e /boot/kernel.elf ]; then
    search --no-floppy --file --set=root /boot/kernel.elf
fi

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

# One entry per platform identity (crate::platform_mode). The kernel
# detects the identity from the firmware by default and keys 1..5 switch
# it at runtime; these entries pin one from the menu, for hardware whose
# firmware does not identify itself. An unknown token is reported by the
# kernel, and this menu only uses the five it accepts.
submenu "Nyrqis platform identity (default: auto-detect)" {
    menuentry "Nyrqis native" {
        multiboot2 /boot/kernel.elf nyrqis.persona=nyrqis
        boot
    }
    menuentry "Android mode" {
        multiboot2 /boot/kernel.elf nyrqis.persona=android
        boot
    }
    menuentry "Windows mode" {
        multiboot2 /boot/kernel.elf nyrqis.persona=windows
        boot
    }
    menuentry "macOS mode" {
        multiboot2 /boot/kernel.elf nyrqis.persona=macos
        boot
    }
    menuentry "Console mode" {
        multiboot2 /boot/kernel.elf nyrqis.persona=console
        boot
    }
}
EOF

mkdir -p "$(dirname "$OUT")"
if [[ "$GRUB_MKRESCUE" == "1" ]]; then
    echo "==> packaging $OUT (GRUB El Torito, Multiboot2)"
    ISO_ROUTE=grub-mkrescue
    grub-mkrescue -o "$OUT" "$WORK" >/dev/null 2>&1
else
    # The grub-mkrescue-free route. `-O i386-pc-eltorito` makes grub-mkimage
    # prepend cdboot.img to the core and lay the result out for an El Torito
    # no-emulation load; embedding the modules in that core is what lets it
    # find /boot/grub/grub.cfg (prefix -p) on the ISO9660 medium without a
    # separate module directory having to be probed first.
    echo "==> packaging $OUT (GRUB El Torito via grub-mkimage + xorriso, Multiboot2)"
    ISO_ROUTE="grub-mkimage + xorriso"
    GRUB_MODDIR="${NYKERNEL_GRUB_MODULES:-$(dirname "$(command -v grub-mkimage)")/i386-pc}"
    [[ -d "$GRUB_MODDIR" ]] || { echo "FAIL: GRUB module directory $GRUB_MODDIR not found" >&2; exit 1; }
    command -v xorriso >/dev/null 2>&1 || { echo "FAIL: xorriso not found" >&2; exit 1; }
    # biosdisk + iso9660 are the El Torito boot path: GRUB 2 has NO `cdrom`
    # module -- a no-emulation El Torito CD is an int13 drive that biosdisk
    # names, and iso9660 mounts it (naming one here made grub-mkimage die with
    # "cannot open .../cdrom.mod", which the old `>/dev/null 2>&1` hid).
    # search_fs_file backs the grub.cfg's own `search --file` guard;
    # multiboot2 loads this kernel; the video/font set lets the menu and the
    # console entry render.
    GRUB_MODULES="biosdisk iso9660 normal search search_fs_file search_label \
multiboot2 all_video font gfxterm gfxterm_background video video_fb png jpeg \
terminal relocator boot echo test configfile ls cat sleep serial minicmd halt reboot \
part_msdos part_gpt"
    # Output NOT suppressed: both tools report the reason a core is
    # unbootable (a missing module, an unreadable directory) on stderr, and
    # swallowing it left a bare exit 1 with no diagnosis.
    # The prefix carries its DEVICE. GRUB names a BIOS El Torito CD `(cd)`
    # (an unnumbered device, unlike a disk's `(cd0)`), and a bare `-p
    # /boot/grub` leaves grub.cfg's device to be resolved from a `root` the
    # core has not set -- which is exactly how this disc first landed at the
    # `grub>` rescue prompt with `ls` listing `(cd) (fd0)` and no config: the
    # image is only ever booted as that CD, so name it.
    grub-mkimage -O i386-pc-eltorito -d "$GRUB_MODDIR" -p '(cd)/boot/grub' \
        -o "$WORK/boot/grub/eltorito.img" $GRUB_MODULES
    [[ -s "$WORK/boot/grub/eltorito.img" ]] || { echo "FAIL: grub-mkimage produced no eltorito image" >&2; exit 1; }
    xorriso -as mkisofs -o "$OUT" -V NYRQIS \
        -b boot/grub/eltorito.img -no-emul-boot -boot-load-size 4 -boot-info-table \
        "$WORK"
fi

# Named by the route that actually ran: with no grub-mkrescue on the host the
# mkimage + xorriso fallback writes this file, and blaming grub-mkrescue for it
# sent the reader looking for a tool that was never invoked.
[[ -s "$OUT" ]] || { echo "FAIL: $ISO_ROUTE produced no image" >&2; exit 1; }

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
    # Each mtool named as it is probed: "mtools not found" was printed when
    # only mformat had been looked for, and a missing mmd/mcopy then died as a
    # bare 127 further down with no sentence at all.
    for _t in mformat mmd mcopy; do
        command -v "$_t" >/dev/null 2>&1 \
            || { echo "FAIL: $_t not found (mtools is needed to build the ESP)" >&2; exit 1; }
    done
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
    # Size the ESP to the stub instead of always reserving 32 MiB: an image
    # padded to a fixed size is most of what a committed artifact costs, and
    # the only thing in this one is one .efi file. Rounded up to whole MiB
    # plus 3 MiB of FAT/directory slack, and never below 4 MiB.
    STUB_BYTES="$(stat -c %s "$STUB_EFI")"
    ESP_MIB=$(( (STUB_BYTES / 1024 / 1024) + 3 ))
    (( ESP_MIB < 4 )) && ESP_MIB=4
    ESP_BYTES=$(( ESP_MIB * 1024 * 1024 ))
    echo "==> ESP ${ESP_MIB} MiB for a $((STUB_BYTES / 1024)) KiB stub"
    mformat -i "$ESP" -C -T $(( ESP_BYTES / 512 ))
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
