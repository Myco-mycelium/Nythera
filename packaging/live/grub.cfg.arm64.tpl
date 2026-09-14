# GRUB menu for the Nyrqis live ISO — arm64 build (UEFI only).
# __VOLUME_ID__ is substituted by build-live-iso.sh --arch arm64.
#
# arm64 has no BIOS/el torito and no isolinux (syslinux is x86-only):
# this image boots exclusively through GRUB's arm64-efi path.
#
# Every entry carries console=tty0 console=ttyAMA0,115200: the QEMU
# 'virt' machine (and most arm64 SBCs) wire the primary UART to
# ttyAMA0, not ttyS0. The VGA console stays the primary human surface
# AND kernel/demo output lands on the serial line — that is what makes
# the HUMAN boot path observable by
# tests/boot_smoke_menu.py --arch arm64, which boots the ISO with no
# hand-built cmdline and lets GRUB run exactly as a real machine would.

set timeout=5
set default=0

menuentry "Nyrqis Live (demo)" {
    linux /live/vmlinuz boot=live quiet splash console=tty0 console=ttyAMA0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (pill shell — registry-1.1 cornerRadius demo)" {
    linux /live/vmlinuz boot=live nyrqis.variant=pill quiet splash console=tty0 console=ttyAMA0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (verbose — full boot log)" {
    linux /live/vmlinuz boot=live console=tty0 console=ttyAMA0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (RAM — copy the squashfs into RAM)" {
    linux /live/vmlinuz boot=live toram quiet splash console=tty0 console=ttyAMA0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (serial console — CI boot smoke)" {
    linux /live/vmlinuz boot=live console=ttyAMA0,115200
    initrd /live/initrd
}
