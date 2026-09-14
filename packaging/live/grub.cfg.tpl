# GRUB menu for the Nyrqis live ISO (UEFI + BIOS via grub-mkrescue).
# __VOLUME_ID__ is substituted by build-live-iso.sh.
#
# Every entry carries console=tty0 console=ttyS0,115200: the VGA console
# stays the primary human surface AND kernel/demo output lands on the
# serial line — that is what makes the HUMAN boot path (this menu, its
# default entry, the full interactive demo) observable by
# tests/boot_smoke_menu.py, which boots the ISO with no hand-built
# cmdline and lets GRUB run exactly as a real machine would.
set timeout=5
set default=0

menuentry "Nyrqis Live (demo)" {
    linux /live/vmlinuz boot=live quiet splash console=tty0 console=ttyS0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (pill shell — registry-1.1 cornerRadius demo)" {
    linux /live/vmlinuz boot=live nyrqis.variant=pill quiet splash console=tty0 console=ttyS0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (verbose — full boot log)" {
    linux /live/vmlinuz boot=live console=tty0 console=ttyS0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (RAM — copy the squashfs into RAM)" {
    linux /live/vmlinuz boot=live toram quiet splash console=tty0 console=ttyS0,115200
    initrd /live/initrd
}

menuentry "Nyrqis Live (serial console — CI boot smoke)" {
    linux /live/vmlinuz boot=live console=ttyS0,115200
    initrd /live/initrd
}
