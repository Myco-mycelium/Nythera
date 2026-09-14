"""Live-boot contract tests — pin the demo-session boot fix in CI.

The 2026-09-14 live-boot failure shipped green: the boot smoke boots the
ISO's kernel directly and parks on ``sleep infinity`` BEFORE the demo's
final shell handoff, so the infinite banner loop (``.bash_profile`` execs
``nyrqis-demo``, which exec'd ``bash -l`` — re-reading the profile —
forever) never ran in CI. The menu-path QEMU smoke (repo-root
``tests/boot_smoke_menu.py``) covers the real boot; these tests pin the
CONTRACT at unit-test speed so a regression fails in seconds, not after
a full ISO build + boot round:

  1. ``nyrqis-demo`` must not end in any login-shell exec (``bash -l``,
     ``sh -l``) — that is the loop.
  2. The demo must export ``NYRQIS_DEMO_ACTIVE`` (the loop guard's
     other half) and hand off via ``--noprofile`` when non-interactive.
  3. ``build-live-iso.sh`` must write a ``.bash_profile`` that guards
     the demo exec with ``NYRQIS_DEMO_ACTIVE``.
  4. Every GRUB/isolinux menu entry must carry a serial console so the
     menu-path smoke can observe the human boot.
  5. The menu smoke's serial-log upload glob must match the driver's
     tmpdir prefix (a mismatch silently uploads nothing).
  6. The smoke handshake keeps ``sleep infinity`` only on paths that do
     NOT hand off to a shell (the driver needs a durable log).

Locates the repo root relative to this file so the tests work from any
checkout depth.
"""

import os
import re
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# backend dir is <repo>/source/nyhal-linux-backend — the repo root is
# two levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(_BACKEND_DIR))

DEMO = os.path.join(
    _REPO_ROOT, "packaging", "live", "overlay", "usr", "local", "bin",
    "nyrqis-demo")
BUILDER = os.path.join(_REPO_ROOT, "packaging", "live", "build-live-iso.sh")
GRUB = os.path.join(_REPO_ROOT, "packaging", "live", "grub.cfg.tpl")
GRUB_ARM64 = os.path.join(_REPO_ROOT, "packaging", "live", "grub.cfg.arm64.tpl")
ISOLINUX = os.path.join(_REPO_ROOT, "packaging", "live", "isolinux.cfg.tpl")
DIRECT_SMOKE = os.path.join(_REPO_ROOT, "tests", "boot_smoke.py")
MENU_SMOKE = os.path.join(_REPO_ROOT, "tests", "boot_smoke_menu.py")
LIVE_ISO_WF = os.path.join(_REPO_ROOT, ".github", "workflows", "live-iso.yml")
LIVE_ISO_ARM64_WF = os.path.join(
    _REPO_ROOT, ".github", "workflows", "live-iso-arm64.yml")

# Any login-shell re-exec from inside the demo session loops: the
# profile execs the demo, the demo execs the login shell, the shell
# re-reads the profile. `bash -l`, `sh -l`, `exec bash --login`, etc.
LOGIN_SHELL_EXEC = re.compile(
    r"exec\s+(?:\S*/)?(?:ba|z|da|k)?sh\s+(?:--login|-l)\b")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestDemoSessionContract(unittest.TestCase):
    """The demo session must terminate into a non-profile shell."""

    def setUp(self):
        self.script = read(DEMO)

    def test_no_login_shell_exec_anywhere(self):
        for i, line in enumerate(self.script.splitlines(), 1):
            self.assertIsNone(
                LOGIN_SHELL_EXEC.search(line),
                f"nyrqis-demo:{i} execs a LOGIN shell ({line.strip()!r}) — "
                f"the shell re-reads .bash_profile, which execs nyrqis-demo "
                f"again: the infinite banner loop")

    def test_exports_demo_active_guard(self):
        self.assertIn(
            "export NYRQIS_DEMO_ACTIVE=1", self.script,
            "the demo must mark its session so .bash_profile's exec guard "
            "can recognize it")

    def test_noninteractive_handoff_is_noprofile(self):
        # The final handoff (last `case $-` in the file) must never run a
        # shell that would re-read the profile.
        m = re.search(
            r"case \$- in\s*\n\s*\*i\*\)\s*exec bash\s*;;\s*\n"
            r"\s*\*\)\s*exec bash --noprofile -i\s*;;", self.script)
        self.assertIsNotNone(
            m, "the demo's final handoff must be: interactive -> `exec bash` "
            "(non-login), non-interactive -> `exec bash --noprofile -i`")

    def test_bash_syntax_ok(self):
        import subprocess
        rc = subprocess.call(["bash", "-n", DEMO])
        self.assertEqual(rc, 0, "nyrqis-demo must parse as valid bash")


class TestBuilderProfileContract(unittest.TestCase):
    """The builder writes .bash_profile — it must carry the guard."""

    def setUp(self):
        self.builder = read(BUILDER)

    def test_bash_profile_guards_the_demo_exec(self):
        m = re.search(
            r"cat > \"\$ROOTFS_SRC/home/demo/\.bash_profile\" <<'EOF'\n"
            r"(.*?)\nEOF", self.builder, re.S)
        self.assertIsNotNone(m, "builder must write /home/demo/.bash_profile")
        profile = m.group(1)
        self.assertIn(
            "${NYRQIS_DEMO_ACTIVE:-}", profile,
            ".bash_profile must skip the demo exec when NYRQIS_DEMO_ACTIVE "
            "is set (the loop guard)")
        self.assertIn(
            "/usr/local/bin/nyrqis-demo", profile,
            ".bash_profile must exec the demo session on autologin")


class TestMenuSerialObservability(unittest.TestCase):
    """The menu-path smoke can only see what the serial line carries."""

    def setUp(self):
        self.grub = read(GRUB)
        self.isolinux = read(ISOLINUX)

    def test_every_grub_entry_has_serial_console(self):
        entries = re.findall(r"menuentry \"([^\"]+)\" \{(.*?)\}", self.grub, re.S)
        self.assertTrue(entries, "grub.cfg.tpl must define menu entries")
        for name, body in entries:
            self.assertIn(
                "console=ttyS0", body,
                f"GRUB entry {name!r} has no serial console — the menu-path "
                f"smoke would be blind to this boot path")

    def test_every_isolinux_entry_has_serial_console(self):
        entries = re.findall(r"LABEL (\S+)\n(.*?)(?=\nLABEL|\Z)",
                             self.isolinux, re.S)
        self.assertTrue(entries, "isolinux.cfg.tpl must define labels")
        for name, body in entries:
            self.assertIn(
                "console=ttyS0", body,
                f"isolinux label {name!r} has no serial console — the "
                f"menu-path smoke would be blind to this boot path")


class TestArm64BootContract(unittest.TestCase):
    """The arm64 image boots through a DIFFERENT machine: UEFI-only (no
    BIOS/el torito/isolinux on arm64), QEMU virt serial on ttyAMA0, and
    a foreign debootstrap that must not ship its emulator binary."""

    def setUp(self):
        self.builder = read(BUILDER)
        self.grub_arm64 = read(GRUB_ARM64)
        self.direct = read(DIRECT_SMOKE)
        self.menu = read(MENU_SMOKE)
        self.wf = read(LIVE_ISO_ARM64_WF)

    def test_arm64_grub_template_exists_and_covers_every_entry(self):
        entries = re.findall(r"menuentry \"([^\"]+)\" \{(.*?)\}",
                             self.grub_arm64, re.S)
        self.assertTrue(entries, "grub.cfg.arm64.tpl must define entries")
        for name, body in entries:
            self.assertIn(
                "console=ttyAMA0", body,
                f"arm64 GRUB entry {name!r} has no ttyAMA0 console — the "
                f"virt machine's UART is ttyAMA0, the smoke would be blind")

    def test_builder_wires_arm64_end_to_end(self):
        self.assertIn("--arch", self.builder)
        self.assertIn("grub.cfg.arm64.tpl", self.builder,
                      "the arm64 branch must install the arm64 GRUB template")
        self.assertIn("linux-image-arm64", self.builder,
                      "the arm64 debootstrap must pull the arm64 kernel")
        self.assertIn("--foreign", self.builder,
                      "cross-building needs a foreign debootstrap")

    def test_builder_autologins_on_the_arm64_serial_console(self):
        # The demo serial console is arch-specific (ttyAMA0 on the virt
        # machine): without the serial-getty@ttyAMA0 drop-in the arm64
        # smoke waits forever for a banner no autologin ever prints.
        self.assertIn(
            "serial-getty@ttyAMA0.service.d/autologin.conf", self.builder,
            "the builder must ship the ttyAMA0 autologin drop-in (the arm64 "
            "virt machine's demo console)")

    def test_builder_never_ships_the_emulator(self):
        # The qemu-aarch64-static binary is a HOST artifact for emulated
        # chroot steps; shipping it wastes space and confuses audits.
        self.assertIn(
            'rm -f "$ROOTFS_SRC/usr/bin/qemu-aarch64-static"', self.builder,
            "the builder must strip the emulator binary before mksquashfs")

    def test_smokes_support_arm64(self):
        for label, text in (("boot_smoke", self.direct),
                            ("boot_smoke_menu", self.menu)):
            self.assertIn(
                "--arch", text,
                f"{label} must accept --arch")
            self.assertIn(
                "ttyAMA0", text,
                f"{label} must target the virt machine's ttyAMA0 console "
                "for arm64")
            self.assertIn(
                "qemu-system-aarch64", text,
                f"{label} must default the arm64 qemu binary")

    def test_menu_smoke_supplies_uefi_firmware_on_arm64(self):
        # Without -bios the virt machine has NO firmware and cannot boot
        # the ISO at all — the UEFI image is part of the machine.
        self.assertIn(
            "-bios", self.menu,
            "the menu smoke must pass the edk2 firmware with -bios on arm64")
        self.assertIn(
            "qemu-efi-aarch64", self.menu,
            "the menu smoke must name the firmware package in its error "
            "message (actionable CI failures)")

    def test_arm64_workflow_runs_both_smokes(self):
        self.assertIn("--arch arm64", self.wf,
                      "the arm64 workflow must drive the smokes in arm64 mode")
        self.assertIn("tests/boot_smoke.py", self.wf)
        self.assertIn("tests/boot_smoke_menu.py", self.wf)
        self.assertIn("qemu-user-static", self.wf,
                      "the cross rootfs needs the emulator + binfmt")
        self.assertIn("qemu-system-arm", self.wf,
                      "the boot smokes need qemu-system-aarch64 (the "
                      "qemu-system-arm package ships it)")
        # Every serial-log upload glob must match the drivers' tmpdir
        # prefixes (the amd64 lesson: a mismatch silently uploads
        # nothing).
        self.assertIn("/tmp/nyrqis-boot-smoke-", self.wf)
        self.assertIn("/tmp/nyrqis-boot-smoke-menu-", self.wf)

    def test_amd64_workflow_still_installs_the_ttyS0_drop_in(self):
        # The arm64 additions must not disturb the amd64 image's serial
        # autologin (both smokes depend on it).
        root_wf = read(LIVE_ISO_WF)
        self.assertIn("debootstrap", root_wf)
        self.assertIn("serial-getty", self.builder,
                      "the builder must keep the serial autologin overlay")


class TestMenuSmokeWiring(unittest.TestCase):
    """Driver + CI plumbing must agree, or the log upload is silent."""

    def setUp(self):
        self.smoke = read(MENU_SMOKE)
        self.wf = read(LIVE_ISO_WF)

    def test_artifact_glob_matches_driver_tmpdir(self):
        m = re.search(r'mkdtemp\(prefix="([^"]+)"\)', self.smoke)
        self.assertIsNotNone(m, "menu smoke must create a prefixed tmpdir")
        prefix = m.group(1)
        self.assertIn(
            prefix, self.wf,
            f"live-iso.yml's serial-log glob must cover the driver's tmpdir "
            f"prefix {prefix!r} — a mismatch uploads nothing, silently")

    def test_menu_job_exists_and_uses_the_driver(self):
        self.assertIn("menu-boot:", self.wf)
        self.assertIn("tests/boot_smoke_menu.py", self.wf)

    def test_smoke_parks_on_sleep_only_without_shell_handoff(self):
        # `sleep infinity` is how a smoke session keeps its markers in
        # the log; it is correct ONLY on paths that never reach the
        # final shell handoff. There are exactly two: the serial smoke
        # branch and the non-serial console under smoke.
        demo = read(DEMO)
        n_demo = demo.count("exec sleep infinity")
        self.assertEqual(
            n_demo, 2,
            "the demo's sleep-infinity parking spots changed — re-audit "
            "that neither path reaches the final shell handoff")


if __name__ == "__main__":
    unittest.main()
