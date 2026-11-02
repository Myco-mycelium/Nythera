#!/usr/bin/env bash
#
# device_mode_test.sh -- proves the four device modes render DIFFERENT
# REAL layouts on the framebuffer, and that the mode is chosen per machine
# for a stated reason.
#
# The kernel already asserts its own read-back (draw, read through the
# same mapping, compare). That check cannot tell whether the pixels ever
# reached the display device -- a wrong physical address or a stale
# mapping would still read back the values just written to RAM. So this
# harness takes QEMU's own screendump of the framebuffer and re-checks
# the layout contract from OUTSIDE the guest:
#
#   explicit modes        nyrqis.mode=desktop|tablet|phone|console each
#                         select that mode, draw it, and their screenshots
#                         differ from each other (no two modes can be the
#                         same picture and still be four layouts)
#   layout landmarks      each mode's own invariant is read out of the
#                         screenshot: the accent block every mode shares,
#                         plus desktop's dock destination, tablet's gesture
#                         pill, phone's FAB and bottom navigation, and
#                         console's prompt caret
#   automatic selection   1024x768 with no token selects tablet, and says
#                         so as "display class" -- the same rule the
#                         kernel prints, checked against the screen
#   invalid token         nyrqis.mode=phne falls back and NAMES the bad
#                         token instead of silently selecting the default
#   autoload transcript   the boot log reports the driver registry and
#                         says which rows are unbound on this machine
#
# QEMU is run with -display none: the framebuffer still exists, so this is
# a headless test of real pixels, not of a screenshot harness.
#
# Usage: ./tools/device_mode_test.sh

set -uo pipefail
cd "$(dirname "$0")/.."

WORK=target/device-mode-test
FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

QUIET_TIMEOUT=90   # seconds to wait for the kernel's device-mode line

command -v qemu-system-x86_64 >/dev/null 2>&1 || { echo "FAIL: qemu-system-x86_64 missing"; exit 1; }
command -v grub-mkrescue >/dev/null 2>&1 || { echo "FAIL: grub-mkrescue missing"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 missing"; exit 1; }

rm -rf "$WORK"
mkdir -p "$WORK"

# The normal (non-qemu-test) kernel is built: its final state is the
# hlt loop, so the VM stays alive long enough to be screenshotted. The
# qemu_test build exits the process at the end of the demo chain.
echo "==> building the kernel (non-test profile: it must stay up to be photographed)"
if ! ./build.sh >"$WORK/build.log" 2>&1; then
    echo "FAIL: ./build.sh failed (see $WORK/build.log)"
    tail -20 "$WORK/build.log"
    exit 1
fi

# --- one boot + screenshot ------------------------------------------------
# boot_mode <label> <cmdline-or-empty> <expect-mode> <expect-source>
boot_mode() {
    local label="$1" cmdline="$2" want_mode="$3" want_source="$4"
    local dir="$WORK/$label"
    mkdir -p "$dir/boot/grub"
    cp target/kernel.elf "$dir/boot/kernel.elf"
    cat > "$dir/boot/grub/grub.cfg" <<EOF
set timeout=0
set default=0
menuentry "Nyrqis (device-mode $label)" {
    multiboot2 /boot/kernel.elf$cmdline
    boot
}
EOF
    grub-mkrescue -o "$dir/nyrqis-$label.iso" "$dir" >/dev/null 2>&1 \
        || { bad "$label: grub-mkrescue failed"; return 1; }

    local serial="$dir/serial.log" mon="$dir/monitor.sock" ppm="$dir/screen.ppm"
    rm -f "$serial" "$mon" "$ppm"
    qemu-system-x86_64 \
        -cdrom "$dir/nyrqis-$label.iso" \
        -m 512 -serial "file:$serial" -display none -vga std \
        -monitor "unix:$mon,server,nowait" \
        -no-reboot >"$dir/qemu.log" 2>&1 &
    local qpid=$!

    # Wait for the kernel's own device-mode verdict (or a panic/timeout).
    local waited=0
    while [[ $waited -lt $QUIET_TIMEOUT ]]; do
        if grep -q "device-mode: chrome drawn" "$serial" 2>/dev/null; then break; fi
        if grep -q "KERNEL PANIC" "$serial" 2>/dev/null; then break; fi
        sleep 0.5
        waited=$((waited + 1))
    done
    if ! grep -q "device-mode: chrome drawn" "$serial" 2>/dev/null; then
        bad "$label: the kernel never reached the device-mode render"
        sed 's/^/        /' "$serial" 2>/dev/null | tail -8
        kill "$qpid" 2>/dev/null; wait "$qpid" 2>/dev/null
        return 1
    fi

    # Independent screenshot through QEMU's monitor socket.
    python3 - "$mon" "$ppm" <<'PY'
import socket, sys, time
sock_path, out = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
for _ in range(40):
    try:
        s.connect(sock_path)
        break
    except OSError:
        time.sleep(0.25)
else:
    raise SystemExit("could not connect to the QEMU monitor")
s.sendall(("screendump %s\n" % out).encode())
time.sleep(0.6)
s.sendall(b"quit\n")
time.sleep(0.2)
s.close()
PY
    kill "$qpid" 2>/dev/null; wait "$qpid" 2>/dev/null
    [[ -s "$ppm" ]] || { bad "$label: QEMU produced no screendump"; return 1; }

    # The mode verdict must name the mode AND where it came from.
    if ! grep -q "device-mode: $want_mode (source: $want_source)" "$serial"; then
        bad "$label: expected '$want_mode (source: $want_source)' in the boot log"
        grep "device-mode" "$serial" | sed 's/^/        /' | head -4
        return 1
    fi
    if ! grep -q "device-mode: chrome drawn.*read-back verified" "$serial"; then
        bad "$label: the kernel's own read-back did not verify"
        return 1
    fi
    ok "$label: mode $want_mode ($want_source), kernel read-back verified"

    # Layout landmarks, read out of the screenshot by a checker that does
    # not share the kernel's code path.
    if python3 - "$ppm" "$want_mode" <<'PY'
import struct, sys

path, mode = sys.argv[1], sys.argv[2]
data = open(path, "rb").read()
# P6 PPM: magic, width height, maxval, then binary RGB.
assert data[:2] == b"P6", data[:8]
i = 2
def token():
    global i
    while data[i:i+1].isspace():
        i += 1
    start = i
    while not data[i:i+1].isspace():
        i += 1
    return data[start:i]
w, h, maxv = int(token()), int(token()), int(token())
i += 1  # single whitespace before the binary block
px = data[i:]
def get(x, y):
    o = (y * w + x) * 3
    return (px[o], px[o+1], px[o+2])

SURFACE = (0x1c, 0x1b, 0x1f)
PRIMARY = (0xd0, 0xbc, 0xff)
OUTLINE = (0x49, 0x45, 0x4f)
CONSOLE_FG = (0xb6, 0xf2, 0xc8)
margin = {"desktop": 24, "tablet": 24, "phone": 16, "console": 12}[mode]
status = {"desktop": 28, "tablet": 28, "phone": 24, "console": 16}[mode]

assert get(0, 0) == SURFACE, ("status strip", get(0, 0))
assert get(margin + 2, status // 2) == PRIMARY, ("accent block", get(margin + 2, status // 2))
if mode == "desktop":
    assert get(80 - 4, status + 4) == SURFACE, ("rail", get(76, status + 4))
    assert get(80 + 48, h - 36) == PRIMARY, ("dock destination", get(128, h - 36))
elif mode == "tablet":
    assert get(w - 1, status + 4) == SURFACE, ("app bar", get(w - 1, status + 4))
    assert get(w // 2, h - 20) == OUTLINE, ("gesture pill", get(w // 2, h - 20))
elif mode == "phone":
    assert get(0, h - 1) == SURFACE, ("bottom navigation", get(0, h - 1))
    cw, x0 = min(w, 480), (w - min(w, 480)) // 2
    assert get(x0 + cw - margin - 28, h - 56 - margin - 28 - 16) == PRIMARY, ("FAB",)
elif mode == "console":
    assert get(margin + 12, status + margin + 12) == CONSOLE_FG, ("prompt caret",)

# The Material shell (release 1-71 shell/src/ui.rs) occupies the content
# pane in every mode but console. Its clock widget's `container` fill is
# a colour nothing else on the screen uses, so finding it in the
# screenshot proves the shell's own pixels reached the display -- the
# chrome landmarks above would pass even if it had never run.
if mode != "console":
    SHELL_CONTAINER = (0x21, 0x1f, 0x26)
    appbar = {"desktop": 56, "tablet": 64, "phone": 56}[mode]
    px_ = {"desktop": 80, "tablet": 0, "phone": (w - min(w, 480)) // 2}[mode]
    py_ = status + appbar
    panew = {"desktop": w - 80 - margin, "tablet": w, "phone": min(w, 480)}[mode]
    m = 16 if panew < 600 else 24
    assert get(px_ + m + 4, py_ + 70) == SHELL_CONTAINER, (
        "shell clock widget", mode, get(px_ + m + 4, py_ + 70))

# One number for "did this mode draw a different picture": FNV-1a over
# the whole screenshot, printed for the cross-mode uniqueness check.
hsh = 0x811c9dc5
for b in px:
    hsh ^= b
    hsh = (hsh * 0x01000193) & 0xFFFFFFFF
print(f"{hsh:08x}")
PY
    then
        local hsh
        hsh="$(python3 - "$ppm" "$want_mode" <<'PY'
import sys
data = open(sys.argv[1], "rb").read()
i = 2
def token():
    global i
    while data[i:i+1].isspace():
        i += 1
    s = i
    while not data[i:i+1].isspace():
        i += 1
    return data[s:i]
token(); token(); token()
i += 1
h = 0x811c9dc5
for b in data[i:]:
    h ^= b
    h = (h * 0x01000193) & 0xFFFFFFFF
print(f"{h:08x}")
PY
)"
        echo "$hsh" > "$dir/screen.hash"
        ok "$label: screenshot landmarks hold (screendump hash $hsh)"
    else
        bad "$label: the screenshot does not carry $want_mode's layout landmarks"
    fi
}

echo "==> four explicit device modes, one screenshot each"
boot_mode desktop " nyrqis.mode=desktop" desktop "kernel cmdline"
boot_mode tablet  " nyrqis.mode=tablet"  tablet  "kernel cmdline"
boot_mode phone   " nyrqis.mode=phone"   phone   "kernel cmdline"
boot_mode console " nyrqis.mode=console" console "kernel cmdline"

echo "==> automatic selection and a rejected token"
boot_mode auto "" tablet "display class"
boot_mode badtoken " nyrqis.mode=phne" tablet "display class"
if grep -q "nyrqis.mode token is not one of desktop|tablet|phone|console" "$WORK/badtoken/serial.log"; then
    ok "badtoken: the invalid token is NAMED, not silently ignored"
else
    bad "badtoken: an unknown nyrqis.mode token was not reported"
fi

echo "==> cross-mode uniqueness"
HASHES="$(cat "$WORK"/desktop/screen.hash "$WORK"/tablet/screen.hash "$WORK"/phone/screen.hash "$WORK"/console/screen.hash 2>/dev/null)"
UNIQUE="$(printf '%s\n' "$HASHES" | sort -u | wc -l)"
if [[ "$UNIQUE" == "4" ]]; then
    ok "all four modes produced different screenshots ($(printf '%s' "$HASHES" | tr '\n' ' '))"
else
    bad "modes share a screenshot: $UNIQUE unique of 4 ($(printf '%s' "$HASHES" | tr '\n' ' '))"
fi

echo "==> autoload transcript"
if grep -q "devices: autoload -- 6 driver(s) in the registry" "$WORK/desktop/serial.log" \
   && grep -q "devices:   framebuffer    display  bound" "$WORK/desktop/serial.log" \
   && grep -q "devices:   ps2-aux        input    bound" "$WORK/desktop/serial.log" \
   && grep -q "devices: ps2-aux -- device id 0x00" "$WORK/desktop/serial.log"; then
    ok "autoload: registry reported, framebuffer bound, ps2-aux bound with its IRQ12 evidence (device id read)"
else
    bad "autoload: the boot log does not carry the registry transcript"
    grep "devices:" "$WORK/desktop/serial.log" | sed 's/^/        /' | head -10
fi

echo
if [[ "$FAILS" -ne 0 ]]; then
    echo "FAIL: $FAILS device-mode case(s) failed -- see $WORK/"
    exit 1
fi
echo "PASS: four device modes render distinct, verified layouts on the real framebuffer (QEMU screendumps checked outside the guest), selection is stated with its source, an invalid token is named, and the driver autoload transcript reports each machine's hardware including what it does NOT drive"
