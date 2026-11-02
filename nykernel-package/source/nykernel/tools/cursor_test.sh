#!/usr/bin/env bash
#
# cursor_test.sh -- proves the pointer moves because of REAL hardware
# input, not just a synthetic packet.
#
# The kernel's own boot proof injects a packet through the assembler,
# which pins the decode -> cursor -> pixel path but says nothing about
# IRQ12 delivering live bytes. This harness closes that: it boots the
# image headless, waits for the boot session's 2 s listen window, and
# drives QEMU's emulated PS/2 mouse through the monitor. The kernel must
# report at least one host-driven packet, and the cursor's position in
# the boot log must have changed.
#
# It also takes a screendump before and after and requires the two to
# differ, so "the pointer moved" is checked in the pixels QEMU produced
# as well as in the kernel's own bookkeeping.
#
# Usage: ./tools/cursor_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

WORK=target/cursor-test
rm -rf "$WORK"
mkdir -p "$WORK/boot/grub"

FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

command -v qemu-system-x86_64 >/dev/null 2>&1 || { echo "FAIL: qemu-system-x86_64 missing"; exit 1; }
command -v grub-mkrescue >/dev/null 2>&1 || { echo "FAIL: grub-mkrescue missing"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 missing"; exit 1; }

echo "==> building the kernel (non-test profile: the boot session must stay up)"
if ! ./build.sh >"$WORK/build.log" 2>&1; then
    echo "FAIL: ./build.sh failed (see $WORK/build.log)"
    tail -20 "$WORK/build.log"
    exit 1
fi

cp target/kernel.elf "$WORK/boot/kernel.elf"
cat > "$WORK/boot/grub/grub.cfg" <<'EOF'
set timeout=0
set default=0
menuentry "Nyrqis (cursor)" {
    multiboot2 /boot/kernel.elf nyrqis.mode=desktop
    boot
}
EOF
grub-mkrescue -o "$WORK/cursor.iso" "$WORK" >/dev/null 2>&1 \
    || { echo "FAIL: grub-mkrescue failed"; exit 1; }

SERIAL="$WORK/serial.log"
MON="$WORK/monitor.sock"
BEFORE="$WORK/before.ppm"
AFTER="$WORK/after.ppm"
rm -f "$SERIAL" "$MON" "$BEFORE" "$AFTER"

qemu-system-x86_64 \
    -cdrom "$WORK/cursor.iso" -m 512 \
    -serial "file:$SERIAL" -display none -vga std \
    -monitor "unix:$MON,server,nowait" -no-reboot \
    >"$WORK/qemu.log" 2>&1 &
QPID=$!

# Wait for the pointer to exist (the kernel prints its position).
waited=0
while [[ $waited -lt 120 ]]; do
    grep -q "cursor: pointer drawn" "$SERIAL" 2>/dev/null && break
    grep -q "KERNEL PANIC" "$SERIAL" 2>/dev/null && break
    sleep 0.5
    waited=$((waited + 1))
done
if ! grep -q "cursor: pointer drawn" "$SERIAL" 2>/dev/null; then
    bad "the kernel never drew a pointer"
    tail -8 "$SERIAL" 2>/dev/null | sed 's/^/        /'
    kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null
    exit 1
fi
ok "the kernel drew a pointer"

START_POS="$(grep -o "pointer drawn at ([0-9-]*,[0-9-]*)" "$SERIAL" | head -1)"

python3 - "$MON" "$BEFORE" "$AFTER" <<'PY'
import socket, sys, time
mon, before, after = sys.argv[1:4]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
for _ in range(40):
    try:
        s.connect(mon)
        break
    except OSError:
        time.sleep(0.25)
else:
    raise SystemExit("could not connect to the QEMU monitor")
s.sendall(("screendump %s\n" % before).encode())
time.sleep(0.5)
# Drive the emulated PS/2 mouse: relative motion, several times, so the
# packets arrive across successive polling windows.
for _ in range(6):
    s.sendall(b"mouse_move 40 25\n")
    time.sleep(0.15)
time.sleep(1.5)
s.sendall(("screendump %s\n" % after).encode())
time.sleep(0.6)
s.sendall(b"quit\n")
time.sleep(0.2)
s.close()
PY

# Let the kernel's listen window finish and the line be written.
waited=0
while [[ $waited -lt 40 ]]; do
    grep -q "host-driven motion packet" "$SERIAL" 2>/dev/null && break
    sleep 0.5
    waited=$((waited + 1))
done
kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

MOVES_LINE="$(grep "host-driven motion packet" "$SERIAL" | tail -1)"
if [[ -z "$MOVES_LINE" ]]; then
    bad "the kernel never reported its listen-window result"
else
    MOVES="$(sed -n 's/.*cursor: \([0-9]*\) host-driven.*/\1/p' <<<"$MOVES_LINE")"
    if [[ "${MOVES:-0}" -gt 0 ]]; then
        ok "IRQ12 delivered $MOVES host-driven packet(s) to the pointer"
    else
        bad "no host-driven motion reached the kernel: $MOVES_LINE"
    fi
    END_POS="$(grep -o "pointer now at ([0-9-]*,[0-9-]*)" "$SERIAL" | tail -1)"
    if [[ -n "$END_POS" && "$END_POS" != "${START_POS/pointer drawn at/pointer now at}" ]]; then
        ok "the pointer's position changed ($START_POS -> $END_POS)"
    else
        bad "the pointer's position did not change ($START_POS -> ${END_POS:-<no line>})"
    fi
fi

if [[ -s "$BEFORE" && -s "$AFTER" ]]; then
    H1="$(sha256sum "$BEFORE" | cut -d' ' -f1)"
    H2="$(sha256sum "$AFTER" | cut -d' ' -f1)"
    if [[ "$H1" != "$H2" ]]; then
        ok "the two screendumps differ, so the pointer moved in the pixels QEMU produced"
    else
        bad "the screendumps are identical: the pointer did not move on screen"
    fi
else
    bad "no screendump pair to compare"
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS cursor case(s) failed -- see $WORK/"
    exit 1
fi
echo "PASS: a host-driven PS/2 motion packet moves a drawn pointer whose pixels are verified, and the screen changes with it"
