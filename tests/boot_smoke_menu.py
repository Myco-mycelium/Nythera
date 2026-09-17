#!/usr/bin/env python3
"""Menu-path boot smoke for the Nyrqis live-demo ISO.

The existing smoke (tests/boot_smoke.py) boots the ISO's kernel/initrd
DIRECTLY with a hand-built kernel command line — deliberately immune to
bootloader problems, which also means a broken GRUB/isolinux menu or a
broken default entry can ship green. This driver closes that gap: it
boots the ISO **exactly like a machine does** — the el torito BIOS
image runs, the GRUB menu times out, its DEFAULT entry boots — with no
-kernel/-initrd/-append hand-holding. What it asserts is the human
boot contract:

  1. GRUB actually boots the default entry (kernel + initrd from the
     menu, i.e. the ISO's boot structure is intact),
  2. live-boot finds and mounts the live medium,
  3. systemd reaches the getty and the demo session runs,
  4. the interactive demo banner prints ("== nyrqis live demo =="),
  5. the daemon answers ping ("NYRQIS_BOOT_SMOKE_PONG=1" is emitted by
     the smoke handshake on the serial console — the demo script's
     banner path prints it via the probe section; on non-smoke boots
     the daemon "ok" line from the Backend daemon section is accepted
     as the daemon evidence).

Every menu entry carries `console=tty0 console=ttyS0,115200`, so the
full human path is observable on the serial line while the VGA console
stays the primary human surface.

Exit codes: 0 = the human boot path works end to end; 1 = any stage of
it failed (the serial log tail is printed for diagnosis, and the whole
log is emitted as ::error:: annotations — the credential-free failure
channel, same as tests/boot_smoke.py).

Usage:
    python3 tests/boot_smoke_menu.py dist/nyrqis-live.iso \
        [--qemu qemu-system-x86_64] [--timeout 780] [--keep-logs] \
        [--arch amd64]

Architectures (--arch): amd64 (default) and arm64. On arm64 there is
no BIOS/el torito, so the "machine" boots through UEFI: qemu-system-
aarch64 -M virt with the qemu-efi-aarch64 (edk2) firmware image, whose
boot entry chain-loads the ISO's grubaa64 EFI. The demo serial console
is ttyAMA0 on the virt machine; the arm64 GRUB template carries it on
every entry.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

# The interactive demo banner's first section header (printed by
# nyrqis-demo after the kernel booted, live-boot pivoted, systemd
# reached multi-user, and the autologin getty exec'd the session).
MARKER_BANNER = "== Backend daemon =="
# The daemon evidence on the serial line: the smoke handshake markers
# are printed by the demo script on the arch's serial console (ttyS0 on
# x86, ttyAMA0 on arm64 — both consoles autologin and the handshake runs
# on the serial one under NYRQIS_BOOT_SMOKE; on a plain menu boot WITHOUT
# the flag the "daemon serving" ok-line is the equivalent evidence —
# accept either).
MARKER_PONG_OK = "NYRQIS_BOOT_SMOKE_PONG=1"
MARKER_PONG_FAIL = "NYRQIS_BOOT_SMOKE_PONG=0"
MARKER_DAEMON_OK = "daemon serving on"
MARKER_DAEMON_ADOPTED = "daemon already serving on"
# The probe's required-package class, verified INSIDE the booted image
# (gating: a MISSING-class gap fails the menu smoke too — the human
# boot path and the smoke path boot the SAME squashfs).
MARKER_PKGS_OK = "NYRQIS_BOOT_SMOKE_PKGS=ok"
MARKER_PKGS_BAD = "NYRQIS_BOOT_SMOKE_PKGS=missing"

# Failure surfaces that mean the human path is dead (no banner can
# ever arrive): GRUB did not find its config/kernel, the live medium
# was not found, or the pivot/init failed.
DEAD_PATTERNS = (
    "kernel panic",
    "unable to find a medium containing a live file system",
    "gave up waiting for root device",
    "(initramfs)",
    "entering emergency mode",
    "can't execute '/sbin/init'",
    "no init found",
    "error: file `/live/vmlinuz' not found",
    "error: file `/live/initrd' not found",
    "unknown filesystem",
)


def _emit_annotation(message):
    """Surface the failure through a GitHub check annotation."""
    flat = " ".join(message.split())[:600]
    print(f"::error::menu boot smoke: {flat}", flush=True)


def _emit_full_log(text, summary, qemu_stderr=""):
    """Chunk the whole serial log into ::error:: annotations."""
    _emit_annotation(summary)
    compact = " ".join(text.split())
    # A whitespace-only serial log (qemu died before the guest wrote
    # anything readable) compacts to "" — an empty log[1/1] annotation
    # carries zero information; the qemu stderr does not (2026-09-17,
    # arm64 round 12).
    if not compact and qemu_stderr:
        compact = "[qemu stderr] " + " ".join(qemu_stderr.split())
    if not compact:
        compact = "(serial log empty AND qemu stderr empty — qemu died "\
                  "before the guest or the emulator wrote anything)"
    chunk_size = 950
    max_chunks = 9
    total = len(compact)
    n = min(max_chunks, (total + chunk_size - 1) // chunk_size or 1)
    start = 0 if total <= n * chunk_size else total - (n - 1) * chunk_size
    for i in range(n):
        piece = compact[start + i * chunk_size:start + (i + 1) * chunk_size]
        print(f"::error::menu boot smoke: log[{i + 1}/{n}] {piece}", flush=True)


def _find_uefi_firmware(arch):
    """Locate the edk2 firmware image for UEFI boot (arm64 has no BIOS)."""
    if arch == "arm64":
        candidates = (
            "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
            "/usr/share/AAVMF/AAVMF_CODE.fd",
            "/usr/share/qemu/edk2-aarch64-code.fd",
        )
    else:
        candidates = (
            "/usr/share/OVMF/OVMF_CODE.fd",
            "/usr/share/ovmf/OVMF_CODE.fd",
        )
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return None


def _read_tail(path, n=2000):
    """Last n characters of a file, or "" (qemu may not have started)."""
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()[-n:]
    except FileNotFoundError:
        return ""


def run_smoke(iso, qemu, timeout_s, keep_logs, arch="amd64"):
    tmp = tempfile.mkdtemp(prefix="nyrqis-boot-smoke-menu-")
    serial_log = os.path.join(tmp, "serial.log")
    # qemu's stderr explains early death (bad option, firmware missing,
    # port collision) — discarding it made instant-exit rounds opaque.
    qemu_stderr_log = os.path.join(tmp, "qemu-stderr.log")
    proc = None
    try:
        # NO -kernel/-initrd/-append: the ISO's own boot image must run,
        # present the boot menu, time out, and boot the default entry —
        # exactly what a real machine does with this ISO. On arm64 the
        # "machine" is UEFI: without -bios the virt machine has no
        # firmware and cannot boot anything, so the edk2 image is part
        # of the machine, not part of the boot selection.
        machine = (["-machine", "virt,accel=kvm:tcg", "-cpu", "cortex-a57"]
                   if arch == "arm64" else ["-machine", "accel=kvm:tcg"])
        cmd = [
            qemu,
            *machine,
            "-m", "2048",
            "-nographic",
            "-no-reboot",
            "-cdrom", iso,
            "-boot", "d",
            "-serial", f"file:{serial_log}",
            "-monitor", "none",
        ]
        if arch == "arm64":
            firmware = _find_uefi_firmware("arm64")
            if firmware is None:
                print("[menu-boot-smoke] ERROR: no arm64 UEFI firmware "
                      "found (apt-get install qemu-efi-aarch64)")
                return 1
            cmd += ["-bios", firmware]
            print(f"[menu-boot-smoke] UEFI firmware: {firmware}", flush=True)
        print(f"[menu-boot-smoke] qemu: {' '.join(cmd)}", flush=True)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=open(qemu_stderr_log, "wb"),
            stdin=subprocess.DEVNULL)

        deadline = time.monotonic() + timeout_s
        saw_banner = saw_daemon = saw_pong_fail = False
        saw_pkgs_ok = saw_pkgs_bad = False
        dead_hit = None
        text = ""
        while time.monotonic() < deadline:
            try:
                with open(serial_log, "r", errors="replace") as fh:
                    text = fh.read()
            except FileNotFoundError:
                text = ""
            saw_banner = saw_banner or MARKER_BANNER in text
            saw_daemon = (saw_daemon or MARKER_PONG_OK in text
                          or MARKER_DAEMON_OK in text
                          or MARKER_DAEMON_ADOPTED in text)
            saw_pong_fail = saw_pong_fail or MARKER_PONG_FAIL in text
            saw_pkgs_ok = saw_pkgs_ok or MARKER_PKGS_OK in text
            saw_pkgs_bad = saw_pkgs_bad or MARKER_PKGS_BAD in text
            # The pkg marker is printed AFTER the daemon section (the
            # desktop attempt runs in between), so a daemon sighting
            # alone breaks too early — the parity evidence must land
            # before the verdict (deadline still bounds a hung demo).
            if saw_banner and (saw_daemon or saw_pong_fail) \
                    and (saw_pkgs_ok or saw_pkgs_bad):
                break
            if dead_hit is None:
                for pattern in DEAD_PATTERNS:
                    if pattern in text.lower():
                        dead_hit = pattern
                        break
            if dead_hit is not None:
                print(f"[menu-boot-smoke] dead boot pattern: {dead_hit!r} "
                      "— failing fast (no banner can arrive)")
                break
            if proc.poll() is not None:
                print(f"[menu-boot-smoke] qemu exited early: "
                      f"rc={proc.returncode} stderr: "
                      f"{_read_tail(qemu_stderr_log, 600).strip() or '(empty)'}",
                      flush=True)
                break
            time.sleep(2.0)

        print(f"[menu-boot-smoke] markers: banner={saw_banner} "
              f"daemon={saw_daemon} pong_fail={saw_pong_fail} "
              f"pkgs_ok={saw_pkgs_ok} pkgs_bad={saw_pkgs_bad}")
        if saw_pkgs_bad:
            pkg_line = "(marker line not found)"
            for line in text.splitlines():
                if line.startswith(MARKER_PKGS_BAD):
                    pkg_line = line.strip()
                    break
            print(f"[menu-boot-smoke] FAIL: the image's capability probe "
                  f"would report missing components at boot — {pkg_line}")
        try:
            with open(serial_log, "r", errors="replace") as fh:
                tail = fh.read()[-2000:]
        except FileNotFoundError:
            tail = "(no serial log written)"
        qerr_tail = _read_tail(qemu_stderr_log, 2000)
        if keep_logs:
            print(f"[menu-boot-smoke] serial log kept: {serial_log}")
            print(f"[menu-boot-smoke] qemu stderr kept: {qemu_stderr_log}")

        if not (saw_banner and saw_daemon):
            _emit_full_log(
                text,
                f"menu-path markers banner={saw_banner} daemon={saw_daemon} "
                f"pong_fail={saw_pong_fail}; dead_pattern={dead_hit!r}; "
                f"qemu_rc={proc.returncode if proc else 'n/a'}; "
                f"full serial log follows in chunks",
                qemu_stderr=qerr_tail)
            print("[menu-boot-smoke] ---- serial log tail ----")
            if not tail.strip():
                print(repr(tail) if tail else "(empty)")
            else:
                print(tail)
            print("[menu-boot-smoke] -----------------------------")
            if qerr_tail.strip():
                print("[menu-boot-smoke] diagnosis: qemu itself reported:")
                print("[menu-boot-smoke]   " + "\n[menu-boot-smoke]   ".join(
                    qerr_tail.strip().splitlines()[-5:]))

        if saw_banner and saw_daemon and saw_pkgs_bad:
            print("[menu-boot-smoke] FAIL: the session ran but the probe's "
                  "required packages are incomplete — the image must not "
                  "ship (see the marker line above)")
            return 1
        if saw_banner and saw_daemon and not (saw_pkgs_ok or saw_pkgs_bad):
            print("[menu-boot-smoke] FAIL: the session ran but the "
                  "package-parity marker never appeared — the demo never "
                  "reached its probe")
            return 1
        if saw_banner and saw_daemon:
            print("[menu-boot-smoke] PASS: GRUB booted the default entry, "
                  "the demo session ran, the daemon answered, and the "
                  "probe's required packages are complete")
            return 0
        if saw_banner and saw_pong_fail:
            print("[menu-boot-smoke] FAIL: the demo session ran but the "
                  "daemon did not answer ping")
            return 1
        if dead_hit is not None:
            print(f"[menu-boot-smoke] FAIL: dead boot pattern {dead_hit!r} "
                  "— the human boot path is broken")
        else:
            print(f"[menu-boot-smoke] FAIL: no demo banner within "
                  f"{timeout_s}s (GRUB/live-boot/session never completed)")
        return 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not keep_logs:
            for name in (serial_log,):
                try:
                    os.unlink(name)
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("iso", help="path to the live ISO")
    parser.add_argument("--qemu", default=None,
                        help="qemu binary (default: qemu-system-x86_64 for "
                             "amd64, qemu-system-aarch64 for arm64)")
    parser.add_argument("--arch", default="amd64", choices=("amd64", "arm64"),
                        help="ISO architecture (default: amd64)")
    parser.add_argument("--timeout", type=float, default=780.0,
                        help="boot budget in seconds (default: 780)")
    parser.add_argument("--keep-logs", action="store_true",
                        help="keep the serial log")
    args = parser.parse_args()
    if args.qemu is None:
        args.qemu = ("qemu-system-aarch64" if args.arch == "arm64"
                     else "qemu-system-x86_64")

    if not os.path.exists(args.iso):
        print(f"[menu-boot-smoke] ERROR: ISO not found: {args.iso}")
        return 1
    if os.system(f"command -v {args.qemu} >/dev/null 2>&1") != 0:
        print(f"[menu-boot-smoke] SKIP: {args.qemu} not on PATH "
              "(install qemu-system-x86 or qemu-system-arm to run the menu "
              "boot smoke)")
        return 0
    return run_smoke(args.iso, args.qemu, args.timeout, args.keep_logs,
                     arch=args.arch)


if __name__ == "__main__":
    sys.exit(main())
