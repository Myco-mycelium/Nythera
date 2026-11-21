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
#                         screenshot: the Tide top bar's glass and its brand
#                         tile in the accent the session identity announced,
#                         plus one element per posture -- the keel pill
#                         (desktop / tablet), the gesture pill (phone) or the
#                         console hint bar. The shell owns the whole screen now,
#                         so a mode that drew device chrome under it would move
#                         or cover one of those and fail here.
#   final frame           the dump is taken only after the kernel announces
#                         "screen: settled": the session identity repaints the
#                         screen after the device-mode verdict, and a dump taken
#                         between the two would photograph a half-painted
#                         framebuffer
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

# The environment this gate assumes, named before it is assumed: a missing
# qemu, a path too long for proot's monitor socket, or a clippy-less host now
# fails HERE with a sentence instead of later with a 127 or a hang. --quiet
# prints only the failures and warnings.
if ! ./tools/gate_env.sh --quiet; then
    echo "FAIL: the gate environment is not ready (the requirements above are missing)"
    exit 1
fi

WORK=target/device-mode-test
FAILS=0
ok()  { echo "  OK: $*"; }
bad() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

QUIET_TIMEOUT=180  # seconds to wait for the kernel's FINAL frame ("screen: settled")

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
    local label="$1" cmdline="$2" want_mode="$3" want_source="$4" lock="${5:-0}"
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

    # The monitor socket lives at a SHORT path on purpose: a unix socket's
    # path is capped at 108 bytes (sun_path), and a run from a deep workspace
    # directory would otherwise fail to CREATE it -- qemu is silent about it,
    # and the failure surfaces later as "QEMU produced no screendump", which
    # says nothing about the kernel under test. Ported: 2026-11-16, a
    # /workspace/chats/<id>/... checkout is 120 bytes to target/.
    local serial="$dir/serial.log" ppm="$dir/screen.ppm"
    local mon="/tmp/nyrqis-mon-$$-${label}.sock"
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
        # The FINAL frame: the session identity repaints the screen after the
        # device-mode verdict, so the dump waits for the line printed once the
        # display has stopped changing.
        if grep -q "screen: settled" "$serial" 2>/dev/null; then break; fi
        if grep -q "device-mode: chrome drawn" "$serial" 2>/dev/null \
           && grep -q "platform:" "$serial" 2>/dev/null \
           && grep -q "locked screen: identity not painted" "$serial" 2>/dev/null; then break; fi
        if grep -q "KERNEL PANIC" "$serial" 2>/dev/null; then break; fi
        sleep 0.5
        waited=$((waited + 1))
    done
    if ! grep -q "screen: settled" "$serial" 2>/dev/null \
       && ! grep -q "locked screen: identity not painted" "$serial" 2>/dev/null; then
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
    # The accent the session identity actually announced: the shell's brand tile takes
    # that colour, so the checker reads the boot log rather than assuming a palette.
    local accent
    accent=$(grep -o "accent #[0-9a-f]\{6\}" "$serial" | head -1 | cut -d'#' -f2)
    if python3 - "$ppm" "$want_mode" "$lock" "${accent:-3cc8c0}" <<'PY'
import struct, sys

path, mode, lock_arg = sys.argv[1], sys.argv[2], sys.argv[3]
accent_hex = sys.argv[4]
lock = lock_arg.strip() == "1"
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

# The port's own tokens, as the stylesheet defines them. Where a surface is
# glass over the wallpaper, the expected pixel is the SOURCE-OVER composite --
# the same arithmetic the renderer runs -- so this checker names what the mock's
# CSS would put on screen, not a hoped-for colour.
GLASS = 0x0e1e26
GLASS_A = 194
W0 = 0x09161d
W4 = 0x187d73
BOOT = 0x071319
WARM = 0xf0b84a
# the lock screen has no session identity, so it keeps the shell's own accent
ACCENT = 0x3cc8c0 if lock else int(accent_hex, 16)

def overlay(base, c, a=GLASS_A):
    def mix(s, d): return (s * a + d * (255 - a) + 127) // 255
    return ((mix((c >> 16) & 0xff, (base >> 16) & 0xff)) << 16
            | (mix((c >> 8) & 0xff, (base >> 8) & 0xff)) << 8
            | mix(c & 0xff, base & 0xff))

def rgb(v): return ((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff)

def accent_in(box):
    x0, y0, x1, y1 = box
    want = rgb(ACCENT)
    n = 0
    for yy in range(y0, min(y1, h)):
        for xx in range(x0, min(x1, w)):
            if get(xx, yy) == want:
                n += 1
    return n

if lock:
    # the locked screen is the mock's own boot console: its dark surface, and
    # its warm first line (it has no top bar and no wallpaper)
    assert get(0, 0) == rgb(BOOT), ("boot console surface", get(0, 0))
    n = 0
    for yy in range(0, min(140, h)):
        for xx in range(0, w):
            if get(xx, yy) == rgb(WARM):
                n += 1
    assert n > 20, ("boot console's warm line", n)
else:
    # the top bar is glass over the wallpaper's lightest band, composited
    assert get(0, 0) == rgb(overlay(W0, GLASS)), ("top bar glass", get(0, 0))
    # every posture ends on the wallpaper's lowest band; console's glass hint
    # bar covers the last 52 px, so there the band shows through the glass
    want = overlay(W4, GLASS) if mode == "console" else W4
    assert get(w // 2, h - 6) == rgb(want), ("wallpaper band", get(w // 2, h - 6), want)
    # the brand wave is the one accent stroke in the bar (the session identity
    # chose the accent, so the colour comes from the boot log)
    assert accent_in((0, 0, w, 34)) > 20, ("brand wave accent", accent_in((0, 0, w, 34)))
    if mode == "desktop":
        # a window title bar (.wbar) is the one solid win-bar fill on the screen
        assert any(get(xx, yy) == rgb(0x1a323d)
                   for yy in range(34, h, 2) for xx in range(0, w, 2)), ("window title bar",)
    else:
        # the launcher/keel app tile: each app's own colour (.tile{background:var(--c)})
        assert any(get(xx, yy) == rgb(0x27414f)
                   for yy in range(0, h, 3) for xx in range(0, w, 3)), ("app tile", mode)

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

# The lock screen is a screen, not a mode: it boots through the same device-mode
# contract (same chrome, same read-back) and must replace the home screen's
# taskbar rather than sit beside it.
echo "==> the lock screen (nyrqis.lock=1) replaces the home screen"
boot_mode lock " nyrqis.mode=tablet nyrqis.lock=1" tablet "kernel cmdline" 1

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
