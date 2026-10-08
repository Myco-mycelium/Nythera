#!/usr/bin/env bash
#
# qemu_boot_test.sh -- builds the kernel with the qemu_test feature
# (which makes kernel_main call qemu_exit::success() after reaching
# BootPhase::Ready instead of idling forever), boots it in QEMU
# headless, and turns the guest's isa-debug-exit write into a real
# process exit code: 0 = boot succeeded through Phase 1, nonzero = it
# didn't. This is what CI should run -- not just "does it link", but
# "does it actually reach a running kernel in an emulated CPU."

set -euo pipefail
cd "$(dirname "$0")/.."

./build.sh --qemu-test

ISO_DIR=target/isofiles-test
rm -rf "$ISO_DIR"
mkdir -p "$ISO_DIR/boot/grub"
cp target/kernel.elf "$ISO_DIR/boot/kernel.elf"
cat > "$ISO_DIR/boot/grub/grub.cfg" <<'EOF'
set timeout=0
set default=0
menuentry "Nyrqis Kernel (test)" {
    multiboot2 /boot/kernel.elf
    boot
}
EOF
grub-mkrescue -o target/nyrqis-test.iso "$ISO_DIR" >/dev/null 2>&1

set +e
timeout 15 qemu-system-x86_64 \
    -cdrom target/nyrqis-test.iso \
    -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
    -serial stdio \
    -display none \
    -no-reboot -no-shutdown \
    > target/qemu-test-stdout.log 2>&1
QEMU_STATUS=$?
set -e

echo "--- guest serial output ---"
cat target/qemu-test-stdout.log
echo "----------------------------"

# isa-debug-exit maps guest value V to host exit code (V << 1) | 1.
# success() writes 0x10 -> host exit code 33.
if [[ "$QEMU_STATUS" -eq 33 ]]; then
    echo "PASS: kernel reached BootPhase::Ready and exited cleanly"
    exit 0
elif [[ "$QEMU_STATUS" -eq 124 ]]; then
    echo "FAIL: boot test timed out (kernel hung or never reached Ready)"
    exit 1
else
    echo "FAIL: unexpected QEMU exit status $QEMU_STATUS"
    exit 1
fi
