# GRUB menu for the Nyrqis live ISO (UEFI + BIOS via grub-mkrescue).
# __VOLUME_ID__ is substituted by build-live-iso.sh.
set timeout=5
set default=0

menuentry "Nyrqis Live (demo)" {
    linux /live/vmlinuz boot=live quiet splash
    initrd /live/initrd
}

menuentry "Nyrqis Live (verbose — full boot log)" {
    linux /live/vmlinuz boot=live
    initrd /live/initrd
}

menuentry "Nyrqis Live (RAM — copy the squashfs into RAM)" {
    linux /live/vmlinuz boot=live toram quiet splash
    initrd /live/initrd
}

menuentry "Nyrqis Live (serial console — CI boot smoke)" {
    linux /live/vmlinuz boot=live console=ttyS0,115200
    initrd /live/initrd
}
