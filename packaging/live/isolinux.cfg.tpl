# isolinux menu for the Nyrqis live ISO (legacy BIOS fallback layout).
# __VOLUME_ID__ is substituted by build-live-iso.sh.
DEFAULT nyrqis
TIMEOUT 50
PROMPT 0

LABEL nyrqis
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live quiet splash

LABEL nyrqis-verbose
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live

LABEL nyrqis-ram
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live toram quiet splash

LABEL nyrqis-serial
    KERNEL /live/vmlinuz
    APPEND initrd=/live/initrd boot=live console=ttyS0,115200
