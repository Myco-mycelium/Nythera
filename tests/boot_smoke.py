#!/usr/bin/env python3
"""Boot smoke for the Nyrqis live-demo ISO.

Boots the ISO headless in QEMU **directly via its kernel and initrd**
(extracted from the ISO at run time) with `console=ttyS0,115200
NYRQIS_BOOT_SMOKE=1` on the hand-built kernel command line, and asserts:

  1. NYRQIS_BOOT_SMOKE_READY=1 — the demo session reached the console,
     and
  2. NYRQIS_BOOT_SMOKE_PONG=1 — the backend daemon answered ping.

The handshake: the kernel command line set HERE carries
NYRQIS_BOOT_SMOKE=1; nyrqis-demo (started by the serial-autologin
session on ttyS0) sees the flag in /proc/cmdline, runs the daemon,
prints NYRQIS_BOOT_SMOKE_PONG={0,1}, and parks on `sleep infinity` so
the marker stays durable in the log. Direct kernel boot sidesteps
bootloader menu selection entirely — the ISO's GRUB/isolinux default
stays the graphical demo for humans.

Timing-tolerant: the total budget is generous (a cold boot in CI is
usually ~40–60 s); the log is polled for the markers rather than
assumed. Exit codes: 0 = both markers seen (pong = 1); 1 = boot
failed, banner absent, or pong = 0 (the serial log tail is printed
for diagnosis).

Usage:
    python3 tests/boot_smoke.py dist/nyrqis-live.iso \
        [--qemu qemu-system-x86_64] [--timeout 300] [--keep-logs]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

MARKER_READY = "NYRQIS_BOOT_SMOKE_READY=1"
MARKER_PONG_OK = "NYRQIS_BOOT_SMOKE_PONG=1"
MARKER_PONG_FAIL = "NYRQIS_BOOT_SMOKE_PONG=0"

# `debug=y` (the debug=* form) makes initramfs-tools AND live-boot
# trace every command to the CONSOLE — the plain `debug` token would
# redirect all init output to /run/initramfs/initramfs.debug inside
# the VM (invisible on serial). See initramfs-tools /init case arms:
# "debug) log_output=/run/..." vs "debug=*) set -x" (no redirect).
KERNEL_CMDLINE = ("boot=live debug=y console=ttyS0,115200 "
                  "systemd.unit=multi-user.target "
                  "NYRQIS_BOOT_SMOKE=1")

# Console patterns that mean the boot is DEAD (no marker can ever
# arrive): fail fast instead of burning the whole budget. The prompt
# form covers the initramfs emergency/rescue shell — usually live-boot
# failing to find the medium.
DEAD_PATTERNS = (
    "kernel panic",
    "unable to find a medium containing a live file system",
    "gave up waiting for root device",
    "(initramfs)",
    "entering emergency mode",
    "can't execute '/sbin/init'",
    "target filesystem doesn't have requested",
    "no init found",
)


def _emit_annotation(message):
    """Surface the failure through a GitHub check annotation.

    ``::error::`` lines in a step log become annotations on the run,
    readable via the API by tooling that cannot fetch job logs or
    artifacts (unauthenticated). Whitespace collapses; the cap keeps us
    inside annotation size limits.
    """
    flat = " ".join(message.split())[:600]
    print(f"::error::boot smoke: {flat}", flush=True)


def _emit_full_log(text, summary):
    """Chunk the WHOLE serial log into ::error:: annotations.

    The serial-log artifact needs auth to download and job logs are not
    in the static job page — annotations are the only failure channel
    readable without credentials. GitHub caps error annotations at 10
    per step; with ~950 usable chars per annotation that carries a
    ~9 KB log, which has covered every real boot so far. The first
    chunk is the diagnosis summary, the rest are numbered log segments
    so the boot narrative can be reconstructed in order.
    """
    _emit_annotation(summary)
    compact = " ".join(text.split())
    chunk_size = 950
    max_chunks = 9  # 1 summary + 9 log chunks = 10 error annotations
    total = len(compact)
    n = min(max_chunks, (total + chunk_size - 1) // chunk_size or 1)
    if total <= n * chunk_size:
        start = 0
    else:
        # Over budget: the INTERESTING part of a boot log is the END —
        # panics, mountroot failures, and rescue shells all sit there.
        # Head gets one chunk for context (kernel version, cmdline);
        # the rest goes to the tail.
        start = total - (n - 1) * chunk_size
    for i in range(n):
        piece = compact[start + i * chunk_size:start + (i + 1) * chunk_size]
        print(f"::error::boot smoke: log[{i + 1}/{n}] {piece}", flush=True)


def _extract_live_kernel(iso, dest_dir):
    """Extract /live/vmlinuz + /live/initrd from the ISO into dest_dir.

    Prefers xorriso (installed by the live-iso CI toolchain step); falls
    back to isoinfo (genisoimage). Returns (kernel_path, initrd_path)
    or raises RuntimeError with an actionable message.
    """
    kernel = os.path.join(dest_dir, "vmlinuz")
    initrd = os.path.join(dest_dir, "initrd")
    xorriso = shutil.which("xorriso")
    if xorriso:
        for iso_path, out in (("/live/vmlinuz", kernel),
                              ("/live/initrd", initrd)):
            rc = subprocess.call([
                xorriso, "-osirrox", "on", "-indev", iso,
                "-extract", iso_path, out,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if rc != 0 or not os.path.exists(out):
                raise RuntimeError(
                    f"xorriso could not extract {iso_path} from {iso} "
                    "(not a Nyrqis live ISO?)")
        return kernel, initrd
    isoinfo = shutil.which("isoinfo")
    if isoinfo:
        for iso_path, out in (("/live/vmlinuz", kernel),
                              ("/live/initrd", initrd)):
            data = subprocess.run(
                [isoinfo, "-i", iso, "-x", iso_path],
                capture_output=True, timeout=120)
            if data.returncode != 0 or not data.stdout:
                raise RuntimeError(
                    f"isoinfo could not extract {iso_path} from {iso} "
                    "(not a Nyrqis live ISO?)")
            with open(out, "wb") as fh:
                fh.write(data.stdout)
        return kernel, initrd
    raise RuntimeError(
        "need xorriso or isoinfo (genisoimage) on PATH to extract the "
        "kernel/initrd for direct boot")



def run_smoke(iso, qemu, timeout_s, keep_logs):
    tmp = tempfile.mkdtemp(prefix="nyrqis-boot-smoke-")
    serial_log = os.path.join(tmp, "serial.log")
    proc = None
    try:
        try:
            kernel, initrd = _extract_live_kernel(iso, tmp)
        except RuntimeError as exc:
            print(f"[boot-smoke] ERROR: {exc}")
            return 1
        cmd = [
            qemu,
            "-machine", "accel=kvm:tcg",   # KVM when available, TCG otherwise
            "-m", "2048",
            "-nographic",
            "-no-reboot",
            "-cdrom", iso,                 # the live medium (squashfs source)
            "-kernel", kernel,             # direct boot: no menu selection
            "-initrd", initrd,
            "-append", KERNEL_CMDLINE,
            "-serial", f"file:{serial_log}",
            "-monitor", "none",
        ]
        print(f"[boot-smoke] qemu: {' '.join(cmd)}", flush=True)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)

        deadline = time.monotonic() + timeout_s
        saw_ready = saw_pong_ok = saw_pong_fail = False
        dead_hit = None
        while time.monotonic() < deadline:
            # Read first: the markers must be parsed even when qemu exits
            # right after writing them (the early-exit check below would
            # otherwise break before the final read).
            try:
                with open(serial_log, "r", errors="replace") as fh:
                    text = fh.read()
            except FileNotFoundError:
                text = ""
            saw_ready = saw_ready or MARKER_READY in text
            saw_pong_ok = saw_pong_ok or MARKER_PONG_OK in text
            saw_pong_fail = saw_pong_fail or MARKER_PONG_FAIL in text
            if saw_ready and (saw_pong_ok or saw_pong_fail):
                break
            if dead_hit is None:
                for pattern in DEAD_PATTERNS:
                    if pattern in text.lower():
                        dead_hit = pattern
                        break
            if dead_hit is not None:
                print(f"[boot-smoke] dead boot pattern: {dead_hit!r} "
                      "— failing fast (no marker can arrive)")
                break
            if proc.poll() is not None:
                print(f"[boot-smoke] qemu exited early: rc={proc.returncode}")
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
        # ALWAYS print the tail on failure — with --keep-logs (CI) the
        # log path is useless without the run's log to read it from.
        if not (saw_ready and saw_pong_ok):
            # Cheap classification so the job log names the failure mode.
            lowered = tail.lower()
            if "kernel panic" in lowered or "run-init" in lowered:
                print("[boot-smoke] diagnosis: the KERNEL panicked — "
                      "initrd/medium mismatch (live-boot could not set "
                      "up the root)")
            elif dead_hit is not None:
                print(f"[boot-smoke] diagnosis: dead boot pattern "
                      f"{dead_hit!r} — see the tail below")
            elif "reached target" not in lowered and saw_ready is False:
                print("[boot-smoke] diagnosis: userspace never reported "
                      "progress within the budget — likely just TCG-slow "
                      "(raise --timeout) or getty never started")
            elif saw_ready and not (saw_pong_ok or saw_pong_fail):
                print("[boot-smoke] diagnosis: session ran but no PONG "
                      "line — the smoke branch's ping loop was cut off")
            print("[boot-smoke] ---- serial log tail ----")
            print(tail)
            print("[boot-smoke] -----------------------------")
            # And surface it as check annotations: that is the one
            # failure channel readable via the API without credentials.
            # The FULL log goes out in chunks — six opaque rounds made
            # this channel the diagnosis bottleneck.
            _emit_full_log(
                text,
                f"markers ready={saw_ready} pong_ok={saw_pong_ok} "
                f"pong_fail={saw_pong_fail}; "
                f"dead_pattern={dead_hit!r}; "
                f"full serial log follows in chunks")

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
    parser.add_argument("--timeout", type=float, default=780.0,
                        help="boot budget in seconds (default: 780 — a "
                             "TCG-slowed full userspace boot needs it)")
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
