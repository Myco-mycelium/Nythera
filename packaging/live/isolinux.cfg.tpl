# isolinux menu for the Nyrqis live ISO (legacy BIOS fallback layout).
# __VOLUME_ID__ is substituted by build-live-iso.sh.
#
# Every entry carries console=tty0 console=ttyS0,115200 (same rationale
# as grub.cfg.tpl: the human menu path stays serial-observable for the
# menu-path boot smoke).
DEFAULT nyrqis
TIMEOUT 50
PROMPT 0

LABEL nyrqis
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live quiet splash console=tty0 console=ttyS0,115200

LABEL nyrqis-pill
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live nyrqis.variant=pill quiet splash console=tty0 console=ttyS0,115200

LABEL nyrqis-verbose
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live console=tty0 console=ttyS0,115200

LABEL nyrqis-ram
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live toram quiet splash console=tty0 console=ttyS0,115200

LABEL nyrqis-serial
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live console=ttyS0,115200
