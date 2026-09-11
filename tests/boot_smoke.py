#!/usr/bin/env python3
"""Boot smoke for the Nyrqis live-demo ISO.

Boots the ISO headless in QEMU with the demo session on the serial
console (the `serial` menuentry path — autologin drop-in for
serial-getty@ttyS0 ships in the image) and asserts:

  1. NYRQIS_BOOT_SMOKE_READY=1 — the demo session reached the console,
     and
  2. NYRQIS_BOOT_SMOKE_PONG=1 — the backend daemon answered ping.

The handshake: the serial menuentry's kernel command line (set here via
-append) carries NYRQIS_BOOT_SMOKE=1; nyrqis-demo then runs the daemon,
prints NYRQIS_BOOT_SMOKE_PONG={0,1}, and parks on `sleep infinity` so
the marker stays durable in the log.

Timing-tolerant: the total budget is generous (a cold debootstrap-free
boot in CI is usually ~40–60 s); the log is polled for the markers
rather than assumed. Exit codes: 0 = both markers seen (pong = 1);
1 = boot failed, banner absent, or pong = 0 (the serial log tail is
printed for diagnosis).

Usage:
    python3 tests/boot_smoke.py dist/nyrqis-live.iso \
        [--qemu qemu-system-x86_64] [--timeout 300] [--keep-logs]
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time

MARKER_READY = "NYRQIS_BOOT_SMOKE_READY=1"
MARKER_PONG_OK = "NYRQIS_BOOT_SMOKE_PONG=1"
MARKER_PONG_FAIL = "NYRQIS_BOOT_SMOKE_PONG=0"


def run_smoke(iso, qemu, timeout_s, keep_logs):
    tmp = tempfile.mkdtemp(prefix="nyrqis-boot-smoke-")
    serial_log = os.path.join(tmp, "serial.log")
    proc = None
    try:
        cmd = [
            qemu,
            "-machine", "accel=kvm:tcg",   # KVM when available, TCG otherwise
            "-m", "2048",
            "-nographic",
            "-no-reboot",
            "-cdrom", iso,
            "-serial", f"file:{serial_log}",
            "-monitor", "none",
        ]
        print(f"[boot-smoke] qemu: {' '.join(cmd)}", flush=True)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)

        deadline = time.monotonic() + timeout_s
        saw_ready = saw_pong_ok = saw_pong_fail = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                print(f"[boot-smoke] qemu exited early: rc={proc.returncode}")
                break
            try:
                with open(serial_log, "r", errors="replace") as fh:
                    text = fh.read()
            except FileNotFoundError:
                time.sleep(2.0)
                continue
            saw_ready = saw_ready or MARKER_READY in text
            saw_pong_ok = saw_pong_ok or MARKER_PONG_OK in text
            saw_pong_fail = saw_pong_fail or MARKER_PONG_FAIL in text
            if saw_ready and (saw_pong_ok or saw_pong_fail):
                break
            time.sleep(2.0)

        print(f"[boot-smoke] markers: ready={saw_ready} "
              f"pong_ok={saw_pong_ok} pong_fail={saw_pong_fail}")
        try:
            with open(serial_log, "r", errors="replace") as fh:
                tail = fh.read()[-2000:]
        except FileNotFoundError:
            tail = "(no serial log written)"
        if keep_logs:
            print(f"[boot-smoke] serial log kept: {serial_log}")
        else:
            print("[boot-smoke] ---- serial log tail ----")
            print(tail)
            print("[boot-smoke] -----------------------------")

        if saw_ready and saw_pong_ok:
            print("[boot-smoke] PASS: the demo session reached the serial "
                  "console and the daemon answered ping")
            return 0
        if saw_ready and saw_pong_fail:
            print("[boot-smoke] FAIL: the demo session ran but the daemon "
                  "did not answer ping — see the log tail above")
            return 1
        print(f"[boot-smoke] FAIL: no ready marker within {timeout_s}s "
              "(boot did not reach the demo session) — see the log tail above")
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
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="boot budget in seconds (default: 300)")
    parser.add_argument("--keep-logs", action="store_true",
                        help="keep the serial log (prints its path instead "
                             "of the tail)")
    args = parser.parse_args()

    if not os.path.exists(args.iso):
        print(f"[boot-smoke] ERROR: ISO not found: {args.iso}")
        return 1
    if os.system(f"command -v {args.qemu} >/dev/null 2>&1") != 0:
        print(f"[boot-smoke] SKIP: {args.qemu} not on PATH "
              "(install qemu-system-x86 to run the boot smoke)")
        return 0
    return run_smoke(args.iso, args.qemu, args.timeout, args.keep_logs)


if __name__ == "__main__":
    sys.exit(main())
