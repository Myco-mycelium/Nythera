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

import yaml

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

    def test_arm64_workflow_triggers_on_release_tags(self):
        # menu-boot-arm64's release step is gated on
        # refs/tags/v* — without a tags: push trigger the step can
        # NEVER fire, and arm64 releases silently ship without their
        # ISO (found when v0.29.25 got amd64 tag runs only; the amd64
        # workflow carries the same block). Branch push + tag push
        # must BOTH be possible on this workflow.
        d = yaml.safe_load(self.wf)
        push = d[True]["push"]  # PyYAML parses a bare `on:` as boolean True
        self.assertIn("branches", push, "arm64 workflow must keep its main-branch trigger")
        self.assertIn("tags", push,
                      "arm64 workflow must ALSO trigger on tag pushes — its "
                      "menu-boot-arm64 release step is gated on refs/tags/v*, "
                      "so without a tags: trigger the arm64 ISO can never "
                      "reach a GitHub release")
        self.assertTrue(
            any(str(t).startswith("v") for t in push["tags"]),
            "the tags trigger must cover v* release tags")

    def test_arm64_workflow_supplies_the_grub_module_tree(self):
        # grub-efi-arm64-bin is not in the amd64 apt index (arm64-built
        # archives only — round 1 of CI proved it); the workflow must
        # fetch the .deb from the binary-arm64 index and extract the
        # module tree grub-mkrescue builds the UEFI image from.
        self.assertIn(
            "binary-arm64/Packages.xz", self.wf,
            "the workflow must resolve the .deb from the arm64 index")
        self.assertIn(
            "grub-efi-arm64-bin", self.wf,
            "the workflow must fetch grub-efi-arm64-bin")
        self.assertIn(
            "arm64-efi", self.wf,
            "the workflow must stage the arm64-efi module tree")
        self.assertIn(
            "mtools", self.wf,
            "grub-mkrescue builds efi.img via mtools (mformat/mcopy)")
        # The first round failed on exactly this; pin the whole class:
        # never `apt-get install grub-efi-arm64-bin`.
        for line in self.wf.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                "apt-get install", stripped,
                f"this apt-get line installs more than the preflight list? "
                f"check it does not try to apt-install grub-efi-arm64-bin: "
                f"{stripped!r}") if "grub-efi-arm64-bin" in stripped else None
        self.assertNotIn(
            "grub-efi-arm64-bin mtools", self.wf,
            "grub-efi-arm64-bin is NOT apt-installable on amd64 — it broke "
            "CI round 1; fetch the .deb instead")

    def test_amd64_workflow_still_installs_the_ttyS0_drop_in(self):
        # The arm64 additions must not disturb the amd64 image's serial
        # autologin (both smokes depend on it).
        root_wf = read(LIVE_ISO_WF)
        self.assertIn("debootstrap", root_wf)
        self.assertIn("serial-getty", self.builder,
                      "the builder must keep the serial autologin overlay")


class TestArm64SmokeContract(unittest.TestCase):
    """The arm64 image must complete the SAME boot smoke as amd64.

    (Found the hard way: the first arm64 ISO booted, autologged in on
    ttyAMA0, and then hung forever — the demo's smoke handshake matched
    only /dev/ttyS0, so the serial session took the non-serial branch
    and parked on `sleep infinity` before printing a single marker.)
    """

    def setUp(self):
        self.demo = read(DEMO)
        self.builder = read(BUILDER)

    def test_smoke_handshake_matches_both_serial_consoles(self):
        # The serial console is arch-specific: ttyS0 on x86, ttyAMA0 on
        # arm64 (QEMU virt + most SBCs). Both must run the handshake.
        m = re.search(r'case "\$\(tty 2>/dev/null\)" in\s*\n\s*([^)]*?)\)',
                      self.demo)
        self.assertIsNotNone(m, "demo must dispatch the smoke handshake on tty")
        arms = m.group(1)
        self.assertIn("/dev/ttyS0", arms, "amd64 serial console must run the handshake")
        self.assertIn("/dev/ttyAMA0", arms,
                      "arm64 serial console must run the handshake — without "
                      "this arm64 boots hang before any marker")
        self.assertIn("/dev/console", arms)

    def test_builder_ships_the_ttyAMA0_autologin_drop_in(self):
        self.assertIn("serial-getty@ttyAMA0.service.d", self.builder,
                      "the arm64 image needs its own serial autologin drop-in")

    def test_builder_opt_install_is_idempotent(self):
        # cp -a src dst NESTS src inside dst when dst exists (reused
        # --rootfs path): the image grew 354M -> 815M in one rebuild.
        self.assertRegex(
            self.builder,
            r'rm -rf "\$OPT/nyhal-linux-backend"\s*\n\s*cp -a "\$BACKEND_DIR"',
            "the /opt tree must be removed before cp -a so a reused rootfs "
            "cannot nest a duplicate copy")

    def test_demo_user_creation_is_idempotent(self):
        self.assertIn("|| chroot \"$ROOTFS_SRC\" sh -c 'id -u demo", self.builder,
                      "a reused rootfs already has the demo user; useradd "
                      "must not abort the build")


class TestIsoSizeGate(unittest.TestCase):
    """The builder must refuse to ship an oversized ISO.

    (Found the hard way: the reused-rootfs /opt-nesting bug produced an
    815 MB image with a duplicated backend tree — noticed only by
    manual size comparison across rebuilds.)
    """

    def setUp(self):
        self.builder = read(BUILDER)

    def test_builder_gates_the_iso_size(self):
        # The gate must run AFTER the image exists (stat on $OUTPUT)
        # and fail the build (die) — not just warn.
        self.assertRegex(
            self.builder,
            r"stat -c %s \"\$OUTPUT\"",
            "the size gate must measure the built ISO")
        m = re.search(
            r"if \[ \"\$ISO_MB\" -gt (\d+) \]; then\s*\n\s*die", self.builder)
        self.assertIsNotNone(
            m, "the size gate must die (fail the build) over the ceiling")
        ceiling = int(m.group(1))
        # The envelope: known-good builds are ~354 MB (amd64 356M max
        # observed). The ceiling must have headroom above that but stay
        # low enough to catch tree duplication (~2x = 700+ MB).
        self.assertGreaterEqual(ceiling, 450,
                                "ceiling too tight — known-good builds are ~354 MB")
        self.assertLessEqual(ceiling, 600,
                             "ceiling too loose — duplication produces 700+ MB")


class TestJobSplitContract(unittest.TestCase):
    """Each boot path is its own CI job, per architecture — a one-path
    or one-arch failure must be diagnosable from the job list alone,
    and must not mask the other paths' results.
    """

    def setUp(self):
        self.amd64 = read(LIVE_ISO_WF)
        self.arm64 = read(LIVE_ISO_ARM64_WF)

    def test_amd64_menu_smoke_is_its_own_job(self):
        # amd64: build (+ direct smoke in-job) and menu-boot are separate
        # jobs; the menu job downloads the ISO artifact, so a build
        # failure cannot mask the menu verdict (it never runs).
        d = yaml.safe_load(self.amd64)
        jobs = d["jobs"]
        self.assertIn("menu-boot", jobs,
                      "amd64 workflow must keep a dedicated menu-boot job")
        self.assertEqual(jobs["menu-boot"].get("needs"), "build",
                         "amd64 menu-boot must depend on build")
        self.assertIn("boot_smoke_menu.py", self.amd64)
        # The direct smoke stays in the build job (separate path signal).
        build_steps = [
            (s.get("run") or "") if isinstance(s, dict) else ""
            for s in jobs["build"]["steps"]
        ]
        self.assertTrue(any("tests/boot_smoke.py" in r for r in build_steps),
                        "amd64 direct smoke must remain in the build job")
        self.assertFalse(any("boot_smoke_menu.py" in r for r in build_steps),
                         "amd64 menu smoke must live only in the menu-boot job")

    def test_arm64_menu_smoke_is_its_own_job(self):
        d = yaml.safe_load(self.arm64)
        jobs = d["jobs"]
        self.assertIn("menu-boot-arm64", jobs,
                      "arm64 workflow must have a dedicated menu-boot job — "
                      "both smokes inside one build job makes a menu-path "
                      "regression undiagnosable from the job list")
        self.assertEqual(jobs["menu-boot-arm64"].get("needs"), "build-arm64",
                         "arm64 menu-boot must depend on build-arm64")
        # The menu job must download the ISO artifact (it runs on a
        # fresh runner, so it cannot rely on the build job's dist/).
        menu_yaml = self.arm64.split("menu-boot-arm64:")[1]
        self.assertIn("download-artifact", menu_yaml,
                      "arm64 menu-boot runs on a fresh runner — it must "
                      "download the built ISO")
        self.assertIn("--arch arm64", menu_yaml,
                      "arm64 menu-boot must boot the arm64 way")
        # The build job keeps ONLY the direct smoke (the split's point):
        # check the PARSED steps, not raw text (the file header and
        # trigger paths legitimately mention the menu driver).
        build_steps = [
            (s.get("run") or "") if isinstance(s, dict) else ""
            for s in jobs["build-arm64"]["steps"]
        ]
        self.assertTrue(any("tests/boot_smoke.py" in r for r in build_steps),
                        "arm64 direct smoke must remain in the build job")
        self.assertFalse(any("boot_smoke_menu.py" in r for r in build_steps),
                         "arm64 menu smoke must MOVE out of the build job")


class TestJobTimeoutContract(unittest.TestCase):
    """Every job in every workflow carries an explicit timeout-minutes —
    a hung boot (or crate build, or test run) fails its job instead of
    burning GitHub's 6-hour default. Budgets must leave margin above
    the slowest legitimate run but fail fast on a real hang.
    """

    WORKFLOWS = (
        LIVE_ISO_WF,
        LIVE_ISO_ARM64_WF,
        os.path.join(_REPO_ROOT, ".github", "workflows", "ci.yml"),
        os.path.join(_REPO_ROOT, ".github", "workflows",
                     "arm64-conformance.yml"),
        os.path.join(_REPO_ROOT, ".github", "workflows", "docs.yml"),
    )

    def test_every_job_has_a_timeout(self):
        for path in self.WORKFLOWS:
            d = yaml.safe_load(read(path))
            name = os.path.basename(path)
            missing = [job for job, spec in d["jobs"].items()
                       if "timeout-minutes" not in spec]
            self.assertEqual(
                missing, [],
                f"{name}: jobs without timeout-minutes fail after GitHub's "
                "6-hour default — every job needs an explicit budget")

    def test_iso_pipeline_timeouts_keep_margin_but_fail_fast(self):
        # Envelope checks on the boot-critical budgets: generous enough
        # for TCG-slowed runners, tight enough that a hang is cut in
        # minutes-scale rather than hours.
        d = yaml.safe_load(read(LIVE_ISO_ARM64_WF))
        self.assertLessEqual(d["jobs"]["build-arm64"]["timeout-minutes"], 150,
                             "arm64 build budget runaway")
        self.assertGreaterEqual(d["jobs"]["build-arm64"]["timeout-minutes"], 90,
                                "arm64 build budget too tight for TCG")
        menu = d["jobs"]["menu-boot-arm64"]
        self.assertLessEqual(menu["timeout-minutes"], 45,
                             "arm64 menu-boot budget runaway")
        self.assertGreaterEqual(menu["timeout-minutes"], 35,
                                "arm64 menu-boot budget too tight: the TCG "
                                "smoke alone measured ~12 min on a FAST host "
                                "— shared runners are slower (2026-09-14..17 "
                                "CI died on the 15-min arithmetic)")
        amd = yaml.safe_load(read(LIVE_ISO_WF))
        self.assertLessEqual(amd["jobs"]["menu-boot"]["timeout-minutes"], 45,
                             "amd64 menu-boot budget runaway")

    def test_arm64_smoke_budget_covers_the_measured_tcg_envelope(self):
        """The 2026-09-14..17 arm64 CI failures were pure budget, not
        boot bugs: the image boots and passes every marker under TCG in
        ~11.5 min on a FAST host (measured 2026-09-17: banner ~2 min,
        PONG/PKGS/READY at ~685 s) — GitHub's shared runners emulate
        aarch64 several times slower, and the old 15-min smoke budget
        killed QEMU mid-handshake every round. Pin the corrected
        arithmetic: apt isolated from the smoke step, driver timeout
        >2.5x the fast-host envelope, step budget above the driver
        timeout, job budget above the step.
        """
        d = yaml.safe_load(read(LIVE_ISO_ARM64_WF))
        build = d["jobs"]["build-arm64"]
        steps = {s.get("name"): s for s in build["steps"] if "name" in s}
        smoke = steps["Direct boot smoke (arm64 kernel, ttyAMA0 handshake)"]
        # The QEMU toolchain must not share the smoke's budget: apt time
        # is time the handshake does not have.
        self.assertIn("Install the QEMU toolchain", steps,
                      "apt must be its own step — apt time must not eat "
                      "the smoke's boot window")
        apt_step = steps["Install the QEMU toolchain"]
        self.assertEqual(apt_step.get("timeout-minutes"), 5)
        # Driver timeout: >2.5x the fast-host TCG envelope (~11.5 min),
        # still fail-fast on a real hang.
        m = re.search(r"--timeout (\d+)", smoke["run"])
        self.assertIsNotNone(m, "the smoke must pass an explicit --timeout")
        driver_s = int(m.group(1))
        self.assertGreaterEqual(driver_s, 1500,
                                "driver timeout below the measured TCG "
                                "envelope — CI will kill a healthy boot")
        self.assertLessEqual(driver_s, 1800,
                             "driver timeout runaway — a hang must fail fast")
        # Step budget must exceed the driver timeout (apt is separate).
        self.assertGreater(
            smoke["timeout-minutes"] * 60, driver_s,
            "smoke step budget must exceed the driver's own timeout — "
            "otherwise the job-level kill fires before the driver's "
            "diagnostics can be written")
        # Menu smoke: same arithmetic.
        menu = d["jobs"]["menu-boot-arm64"]
        menu_steps = {s.get("name"): s for s in menu["steps"] if "name" in s}
        menu_smoke = menu_steps["Boot the ISO like a machine (GRUB menu → default entry, UEFI)"]
        m = re.search(r"--timeout (\d+)", menu_smoke["run"])
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 1500)
        self.assertGreater(
            menu_smoke["timeout-minutes"] * 60, int(m.group(1)))
        self.assertGreater(
            menu["timeout-minutes"], menu_smoke["timeout-minutes"],
            "menu job budget must cover checkout + download + apt + smoke + upload")


class TestSmokeDriversNeedNoNic(unittest.TestCase):
    """The virt machine's default NIC needs ipxe-qemu's efi-virtio.rom,
    which --no-install-recommends does not install: qemu exited 1 before
    the guest booted (2026-09-17, arm64 rounds 12-13 — diagnosed only
    after the drivers learned to report qemu's stderr). Every localhost
    boot passed only because ipxe-qemu was installed there. The smokes
    talk to the serial console exclusively; -net none removes the whole
    option-ROM dependency class.
    """

    def test_both_smokes_run_without_a_nic(self):
        for label, path in (("boot_smoke", DIRECT_SMOKE),
                            ("boot_smoke_menu", MENU_SMOKE)):
            text = read(path)
            self.assertIn(
                '"-net", "none"', text,
                f"{label} must pass -net none — the default NIC's option "
                "ROM (ipxe-qemu's efi-virtio.rom) is absent on CI's "
                "--no-install-recommends install, and qemu exits 1 "
                "before the guest boots")

    def test_smokes_surface_qemu_stderr_on_early_exit(self):
        # The 2-second round that returned a 190-byte serial log and an
        # empty annotation burned a full CI round for zero information.
        # The drivers must capture qemu's stderr and include it in the
        # failure evidence.
        for label, path in (("boot_smoke", DIRECT_SMOKE),
                            ("boot_smoke_menu", MENU_SMOKE)):
            text = read(path)
            self.assertIn("qemu_stderr", text,
                          f"{label} must capture qemu stderr — DEVNULL made "
                          "instant qemu death undiagnosable")
            self.assertIn("qemu exited early", text)


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


class TestProbeParityContract(unittest.TestCase):
    """The image must carry everything its own capability probe calls
    required — a boot that prints MISSING lines for its own components
    is a broken build, and the builder must refuse to ship it.

    (Found the hard way: a booted image's first screen read
    "✗ MISSING: python3 (backend cannot run)" — the debootstrap
    --include list had no python3 and only the CI chroot step papered
    over it, so any rootfs acquired by any other path shipped dead.)
    """

    def setUp(self):
        self.builder = read(BUILDER)

    def test_debootstrap_includes_the_probe_requirements(self):
        # The include list ends with a line-continuation before "$SUITE".
        m = re.search(r"--include=(\S+)\s*\\\n\s*\"\$SUITE\"", self.builder)
        self.assertIsNotNone(m, "builder must have a debootstrap --include list")
        include = m.group(1)
        for pkg in ("python3", "python3-zstandard", "python3-nacl", "fuse3"):
            self.assertIn(
                pkg, include,
                f"debootstrap --include must carry {pkg}: the probe prints "
                "MISSING for it at boot")

    def test_debootstrap_includes_virtual_dep_providers(self):
        """python3-zstandard depends on the VIRTUAL packages
        python3-cffi-backend-api-min/max (Provided by python3-cffi) and
        python3-pycparser depends on python3-ply-lex/-yacc-3.10
        (Provided by python3-ply). debootstrap's resolver cannot map
        virtual dependencies, so without the real providers named
        explicitly dpkg leaves zstandard unconfigured and second stage
        dies (verified against bookworm: this exact build failure)."""
        m = re.search(r"--include=(\S+)\s*\\\n\s*\"\$SUITE\"", self.builder)
        self.assertIsNotNone(m)
        include = m.group(1)
        for pkg in ("python3-cffi", "python3-ply"):
            self.assertIn(
                pkg, include,
                f"debootstrap --include must carry {pkg}: it is the real "
                "package behind a virtual dependency debootstrap cannot "
                "resolve — without it the build dies configuring "
                "python3-zstandard")

    def test_builder_audits_dpkg_state_before_squash(self):
        """Fail-closed: after the package top-up, `dpkg --audit` inside
        the rootfs must be empty — any unpacked-but-unconfigured package
        aborts the build instead of shipping a broken image."""
        self.assertIn('chroot "$ROOTFS_SRC" dpkg --audit', self.builder,
                      "builder must audit the rootfs dpkg state")
        # The audit result must feed a die(), not a log line.
        m = re.search(
            r'AUDIT_OUT=.*dpkg --audit.*?\n.*?die ',
            self.builder, re.DOTALL)
        self.assertIsNotNone(
            m, "a non-empty dpkg --audit must abort the build (die)")

    def test_builder_ensures_probe_packages_on_every_rootfs_path(self):
        """Tarball/–rootfs acquisitions skip debootstrap — the builder
        must top-up the probe packages on those paths too."""
        self.assertIn("python3-zstandard python3-nacl python3-lz4 fuse3",
                      self.builder,
                      "the ensure-packages step must cover all probe packages")
        self.assertIn("dpkg -s \"$p\"", self.builder,
                      "ensure-packages must check installed state, not assume")

    def test_builder_has_probe_parity_gate(self):
        """Fail-closed gate: no image ships whose own boot probe would
        print MISSING for a required component."""
        self.assertIn("probe-parity", self.builder,
                      "builder must carry the probe-parity gate")
        # The gate checks the interpreter, the three python modules the
        # probe requires, and fusermount3.
        for needle in ("/usr/bin/python3", "zstandard", "nacl", "lz4.frame",
                       "fusermount3"):
            self.assertIn(needle, self.builder)
        # The python3 check must abort the build (die), not warn.
        m = re.search(
            r'\[\[ -x "\$ROOTFS_SRC/usr/bin/python3" \]\] \|\| PROBE_GAPS',
            self.builder)
        self.assertIsNotNone(
            m, "a missing python3 must feed the probe-parity gate")

    def test_probe_marks_python3_required(self):
        """The probe side of the contract: python3 is REQUIRED (the
        backend cannot run) — it must print MISSING, never a warn."""
        demo = read(DEMO)
        self.assertIn('missing "python3 (backend cannot run)"', demo)

    def test_probe_required_components_are_built_into_the_image(self):
        """Every component the probe marks required must appear in the
        builder's ensure-packages step — the two sides of the contract
        stay locked together."""
        demo = read(DEMO)
        builder = self.builder
        # The probe's missing() calls name the required components.
        self.assertIn("python3", demo)
        for pkg in ("python3-zstandard", "python3-nacl", "python3-lz4", "fuse3"):
            self.assertIn(pkg, builder,
                          f"builder must install {pkg} into the image")

    def test_demo_emits_the_package_parity_marker(self):
        """The booted image must state its own package parity on the
        serial log (NYRQIS_BOOT_SMOKE_PKGS=ok|missing:…) — the CI smoke
        drivers assert it, so a probe that would print MISSING fails at
        smoke time instead of shipping as "some files like python3 are
        missing".
        """
        demo = read(DEMO)
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=ok", demo,
                      "the demo must print the ok marker when complete")
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=missing", demo,
                      "the demo must name its gaps when incomplete")
        # The marker covers the probe's REQUIRED class — the same
        # components the probe prints MISSING for.
        m = re.search(
            r"pkg_marker\(\)\s*\{(.+?)\n\}", demo, re.S)
        self.assertIsNotNone(m, "demo must define pkg_marker()")
        body = m.group(1)
        for needle in ("python3", "zstandard", "nacl", "fusermount3"):
            self.assertIn(needle, body,
                          f"pkg_marker must check {needle} (probe-required)")
        # The smoke branch must print the marker BEFORE the ready
        # marker (drivers may stop reading at READY).
        smoke_branch = demo.split("NYRQIS_BOOT_SMOKE_READY=1")[0]
        self.assertIn("pkg_marker", smoke_branch,
                      "the smoke branch must emit the marker before READY")

    def test_direct_smoke_asserts_the_package_parity_marker(self):
        direct = read(DIRECT_SMOKE)
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=ok", direct,
                      "the direct smoke must gate on the ok marker")
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=missing", direct,
                      "the direct smoke must detect the missing marker")
        # Gating, not informational: ok must be part of the PASS
        # condition.
        self.assertRegex(
            direct, r"saw_ready and saw_pong_ok and saw_pkgs_ok",
            "PASS must require the package parity marker")

    def test_menu_smoke_asserts_the_package_parity_marker(self):
        menu = read(MENU_SMOKE)
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=ok", menu,
                      "the menu smoke must gate on the ok marker")
        self.assertIn("NYRQIS_BOOT_SMOKE_PKGS=missing", menu,
                      "the menu smoke must detect the missing marker")
        # The menu driver polls until BOTH daemon and parity evidence
        # arrive — a daemon-only break would race the probe.
        self.assertRegex(
            menu,
            r"saw_banner and \(saw_daemon or saw_pong_fail\)\s*\\?\s*"
            r"and \(saw_pkgs_ok or saw_pkgs_bad\)",
            "the menu smoke must not break before parity evidence lands")


class TestWorkflowProbeTopUp(unittest.TestCase):
    """CI's chroot top-up must stay in sync with the probe contract."""

    def test_amd64_workflow_installs_the_probe_packages(self):
        wf = read(LIVE_ISO_WF)
        for pkg in ("python3", "python3-zstandard", "python3-nacl",
                    "python3-lz4", "fuse3"):
            self.assertIn(pkg, wf,
                          f"live-iso.yml rootfs step must install {pkg}")

    def test_arm64_workflow_installs_the_probe_packages(self):
        wf = read(LIVE_ISO_ARM64_WF)
        for pkg in ("python3", "python3-zstandard", "python3-nacl",
                    "python3-lz4", "fuse3"):
            self.assertIn(pkg, wf,
                          f"live-iso-arm64.yml rootfs step must install {pkg}")


class TestRootfsCacheContract(unittest.TestCase):
    """Both ISO workflows must cache their debootstrap rootfs — the
    bootstrap is a pure function of the include list + Debian suite,
    and CI paying ~15-20 min (amd64) / far longer (emulated arm64) per
    routine rebuild is waste. The cache key must carry the include list
    so a package-set change invalidates it automatically.
    """

    def setUp(self):
        self.amd64 = read(LIVE_ISO_WF)
        self.arm64 = read(LIVE_ISO_ARM64_WF)

    _KEY = r"nyrqis-rootfs-(amd64|arm64)-bookworm-v1-(?P<pkgs>.+)"

    def test_both_workflows_cache_the_rootfs(self):
        for name, wf in (("amd64", self.amd64), ("arm64", self.arm64)):
            self.assertIn("actions/cache@v4", wf,
                          f"{name} workflow must cache the rootfs")
            self.assertIn("nyrqis-rootfs-", wf,
                          f"{name} workflow must use the rootfs cache key")

    def test_cache_keys_carry_the_include_list(self):
        # The include lists live in the debootstrap lines; every package
        # on each must appear in that arch's cache key so a package-set
        # change produces a NEW key (the cache never serves a stale
        # package set). live-boot-initramfs-tools is matched loosely:
        # the key abbreviates it as live-boot (both packages share the
        # prefix, and the key's purpose is invalidation, not prose).
        for name, wf, marker in (
            ("amd64", self.amd64, "nyrqis-rootfs-amd64-bookworm-v1"),
            ("arm64", self.arm64, "nyrqis-rootfs-arm64-bookworm-v1"),
        ):
            m = re.search(r"--include=(\S+)", wf)
            self.assertIsNotNone(m, f"{name} workflow lost its include list")
            key = re.search(rf"{marker}\S*", wf)
            self.assertIsNotNone(key, f"{name} workflow lost its cache key")
            for pkg in m.group(1).split(","):
                if pkg == "live-boot-initramfs-tools":
                    continue  # key carries the shared live-boot prefix
                # The key abbreviates python3-cffi -> cffi and
                # python3-ply -> ply (the python3- prefix is shared by
                # several entries; the full set is what invalidates).
                probe = pkg.replace("python3-", "") if pkg.startswith(
                    "python3-") else pkg
                self.assertIn(probe, key.group(0),
                              f"{name} cache key must carry {pkg} so a "
                              "package-set change invalidates the cache")

    def test_cache_hit_path_skips_bootstrap_but_keeps_the_top_up(self):
        # The chroot apt top-up must run on BOTH paths (hit and miss):
        # a cached rootfs could predate a builder-side top-up change.
        for name, wf in (("amd64", self.amd64), ("arm64", self.arm64)):
            hit = "cache HIT" in wf
            self.assertTrue(hit, f"{name} workflow must detect a cache hit")
            # The top-up lines must sit OUTSIDE the miss-only branch:
            # structural check — exactly ONE chroot apt-get install
            # block exists (shared by hit and miss paths), plus the
            # runner's own non-chroot apt install lines.
            chroot_tops = len(re.findall(
                r"chroot \S+ apt-get install -y --no-install-recommends", wf))
            self.assertEqual(
                chroot_tops, 1,
                f"{name} workflow: the chroot top-up must be a "
                "single shared step, not duplicated per path")

    def test_arm64_cache_survives_with_the_emulator_staged(self):
        # The qemu-aarch64-static INSIDE the rootfs must exist on both
        # paths — build-live-iso.sh's chroot steps need it, and a cached
        # rootfs carries whatever it carried when saved.
        self.assertIn("qemu-aarch64-static", self.arm64)
        self.assertIn(
            '|| sudo cp /usr/bin/qemu-aarch64-static', self.arm64,
            "arm64 workflow must re-stage the emulator if the cached "
            "rootfs lost it")


class TestReleaseUploadContract(unittest.TestCase):
    """Pin the release-upload wiring in both ISO workflows: a ``v*``
    tag push must ship BOTH architectures' boot-smoked ISOs as release
    assets, race-safely (both tag jobs may try to create the release
    concurrently; the loser must tolerate ``already_exists``).
    """

    @classmethod
    def setUpClass(cls):
        cls.amd64 = yaml.safe_load(open(LIVE_ISO_WF))
        cls.arm64 = yaml.safe_load(open(LIVE_ISO_ARM64_WF))
        cls.amd64_raw = open(LIVE_ISO_WF).read()
        cls.arm64_raw = open(LIVE_ISO_ARM64_WF).read()

    def _release_step(self, wf, job):
        steps = wf["jobs"][job]["steps"]
        rel = [s for s in steps if "release" in s.get("name", "").lower()]
        self.assertEqual(
            len(rel), 1,
            f"{job} must have exactly one release-upload step")
        return rel[0]

    def test_both_workflows_have_a_tag_gated_release_step(self):
        for wf, job, iso in (
            (self.amd64, "menu-boot", "nyrqis-live.iso"),
            (self.arm64, "menu-boot-arm64", "nyrqis-live-arm64.iso"),
        ):
            step = self._release_step(wf, job)
            self.assertEqual(
                step.get("if"), "startsWith(github.ref, 'refs/tags/v')",
                f"{job}: release upload must run on v* tags only")
            run = step["run"]
            # The upload goes through curl with a BOUNDED --max-time:
            # `gh release upload` stalled twice on the ~250 MB asset
            # (v0.29.25 rounds 3-4; even a solo success once took 8.8
            # min) and the job-budget kill left no retry chance. The
            # URL's ?name= parameter is what names the asset — it must
            # match the built file exactly.
            self.assertIn(
                f"?name={iso}", run,
                f"{job}: must upload the built ISO {iso} (curl asset name)")
            self.assertIn(
                f'--upload-file "dist/{iso}"', run,
                f"{job}: must upload the exact file the build verified")
            self.assertIn(
                "--max-time", run,
                f"{job}: every upload attempt must be time-bounded — "
                "an unbounded gh upload stalled 28.5 min (v0.29.25)")
            self.assertIn(
                "deleting stale asset", run,
                f"{job}: re-runs must be idempotent (delete-before-upload "
                "is the curl-era --clobber)")
            self.assertEqual(
                run.count("for attempt in 1 2 3"), 1,
                f"{job}: upload must retry with bounded attempts")
            # A hang must die as THIS named step, never as a silent
            # job-budget cancellation (round 3's diagnosis was only
            # possible because the step timeout carried the evidence).
            self.assertEqual(step.get("timeout-minutes"), 20,
                             f"{job}: attach step needs its own 20-min budget")

    def test_both_upload_steps_tolerate_concurrent_release_creation(self):
        # Both jobs' create-failure path re-views the release (the
        # winner of the race created it) before failing the step.
        for raw, name in ((self.amd64_raw, "amd64"),
                          (self.arm64_raw, "arm64")):
            self.assertIn("RACE-SAFE", raw,
                          f"{name}: upload must be race-aware")
            self.assertIn(
                "gh release view \"${GITHUB_REF_NAME}\"", raw,
                f"{name}: create-failure path must re-check the release")
            self.assertIn(
                "::error::release ${GITHUB_REF_NAME} could not be created",
                raw,
                f"{name}: a real create failure must fail the step loudly")

    def test_both_attach_steps_self_heal_a_zombie_draft(self):
        # Deleting a tag (force-moving one) converts that tag's GitHub
        # release into a DRAFT: invisible to anonymous API/clients while
        # authenticated `gh release view` still sees it. v0.29.25's
        # release sat fully-uploaded-but-hidden this way — every CI step
        # "succeeded" into a release nobody could download. Both attach
        # steps must re-publish (a no-op for a live release) after the
        # upload succeeds, and strictly AFTER it (never publish a release
        # whose asset upload failed).
        heal = ('gh release edit "${GITHUB_REF_NAME}" '
                '--repo "$GITHUB_REPOSITORY" --draft=false')
        marker = "asset upload failed after 3 bounded attempts"
        for wf, job, name in (
            (self.amd64, "menu-boot", "amd64"),
            (self.arm64, "menu-boot-arm64", "arm64"),
        ):
            run = self._release_step(wf, job)["run"]
            self.assertIn(heal, run,
                          f"{name}: attach step must clear the draft flag "
                          "(tag deletion drafts the release)")
            self.assertGreater(
                run.index(heal), run.index(marker),
                f"{name}: draft self-heal must run only after the upload "
                "gate passes")

    def test_arm64_workflow_grants_contents_write(self):
        self.assertEqual(
            self.arm64["permissions"].get("contents"), "write",
            "release upload needs contents:write")
        self.assertEqual(
            self.amd64["permissions"].get("contents"), "write")

    def test_uploaded_filenames_match_the_builder_output(self):
        # The uploaded filename must be the exact file the build job
        # produced and verified (a mismatch uploads nothing or the wrong
        # file silently): the builder's -o flag, the post-build `test
        # -f`, and the upload-artifact path must all agree per arch.
        for wf_raw, wf, build_job, iso in (
            (self.amd64_raw, self.amd64, "build", "nyrqis-live.iso"),
            (self.arm64_raw, self.arm64, "build-arm64",
             "nyrqis-live-arm64.iso"),
        ):
            steps = wf["jobs"][build_job]["steps"]
            build_runs = "\n".join(
                s["run"] for s in steps
                if "run" in s and "build-live-iso.sh" in s["run"])
            self.assertIn(
                f"-o dist/{iso}", build_runs,
                f"{build_job}: builder must write dist/{iso}")
            self.assertIn(
                f"test -f dist/{iso}", wf_raw,
                f"{build_job}: must verify dist/{iso} exists post-build")
            upload = [s for s in steps
                      if s.get("uses", "").startswith("actions/upload-artifact")]
            self.assertTrue(
                any(u.get("with", {}).get("path") == f"dist/{iso}"
                    for u in upload),
                f"{build_job}: must upload the artifact dist/{iso}")


class TestSkipRegister(unittest.TestCase):
    """Pin the Test Skip Register (docs/00-platform/TEST_SKIP_REGISTER.md)
    against suite reality: the register must exist, must document the
    no-dead-skips rule, and every skip reason in the suite must be
    environmental (never "not implemented"). Import-failure skips on
    first-party modules are the dead-skip pattern that hid the missing
    SystemSnapshot API for four releases.
    """

    REGISTER = os.path.join(
        _REPO_ROOT, "docs", "00-platform", "TEST_SKIP_REGISTER.md")

    def test_register_exists_and_states_the_rule(self):
        self.assertTrue(os.path.isfile(self.REGISTER))
        text = open(self.REGISTER).read()
        self.assertIn("a skip is an answer, not an excuse", text)
        self.assertIn("## The register", text)

    def test_no_first_party_import_failure_skips(self):
        # The dead-skip pattern: except ImportError → skipTest(...)
        # where the import is a FIRST-PARTY module (ui.*, backend.*).
        # Environmental skips (optional Rust artifacts, hardware) name
        # the crate/hardware explicitly and are allowed.
        pattern = re.compile(
            r"except\s+ImportError.*?:\s*\n\s*self\.skipTest\(\s*['\"]([^'\"]*)['\"]")
        backend_dir = os.path.dirname(_BACKEND_DIR)
        offenders = []
        for root, _dirs, files in os.walk(backend_dir):
            if "target" in root.split(os.sep):
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                for m in pattern.finditer(open(path, encoding="utf-8",
                                               errors="replace").read()):
                    reason = m.group(1)
                    if not re.search(
                            r"crate|vulkan|DRM|Wayland|hardware|display|"
                            r"sandbox|no device|not built|CI|PIL|Pillow",
                            reason, re.I):
                        offenders.append(f"{path}: {reason}")
        self.assertEqual(
            offenders, [],
            "import-failure skips must name an environmental reason "
            "(crate/hardware/display) — a vague skip may be masking a "
            "missing first-party API (the system_monitor lesson): "
            + "; ".join(offenders))

    def test_register_matches_suite_skip_count(self):
        # The register's header must state the current skip count —
        # parse it and compare with the actual suite run data pinned
        # here (updated by whoever changes the skip set).
        text = open(self.REGISTER).read()
        m = re.search(r"\*\*(\d+) skips\*\* in a suite of ([\d,]+) passing",
                      text)
        self.assertIsNotNone(m, "register header must state the counts")
        declared_skips = int(m.group(1))
        declared_passing = int(m.group(2).replace(",", ""))
        # The known-environmental skip set (this host): 2 Vulkan +
        # 1 SCM_RIGHTS sandbox. The 6 wayland-crate entries activate
        # only when the cdylib is absent (0.29.23 builds it here).
        KNOWN_ENV_SKIPS = 3
        self.assertEqual(declared_skips, KNOWN_ENV_SKIPS,
                         "register header out of date — re-run the suite "
                         "with -rs and update TEST_SKIP_REGISTER.md")
        self.assertGreater(declared_passing, 8900)

class TestFfiArtifactsInImage(unittest.TestCase):
    """ADR-0027 supporting rule — boot-time FFI honesty: the image
    ships its compiled cdylibs (rust/.cdylibs, not the target dirs),
    the demo exports their location via LD_LIBRARY_PATH, and the boot
    smoke reports an informational CRATE marker (loaded/shipped).
    """

    @classmethod
    def setUpClass(cls):
        cls.builder = open(BUILDER).read()
        cls.demo = open(DEMO).read()

    def test_builder_keeps_cdylibs_and_drops_target_dirs(self):
        # The artifacts land in rust/.cdylibs BEFORE the target dirs
        # are pruned (ordering matters — the pruner must not be able to
        # delete the kept artifacts). Indexing from the copy SITE (the
        # .cdylibs install line), not the comment that mentions it.
        self.assertIn("rust/.cdylibs", self.builder)
        install_line = self.builder.index(
            '.cdylibs/$(basename "$a")')
        target_prune = self.builder.index("rust/*/target,.pytest_cache")
        self.assertLess(
            install_line, target_prune,
            "builder must copy the cdylibs out BEFORE pruning target dirs")
        self.assertIn("libnyrqis_*.so", self.builder,
                      "must collect the compiled cdylibs")
        self.assertIn("nyrqis-launcher", self.builder,
                      "must keep the Rust launcher-init binary")

    def test_demo_exports_the_cdylib_dir(self):
        self.assertIn("NYRQIS_CDYLIB_DIR", self.demo)
        self.assertIn("LD_LIBRARY_PATH", self.demo,
                      "demo must export the cdylib dir on LD_LIBRARY_PATH")

    def test_boot_smoke_reports_a_crate_marker(self):
        self.assertIn("NYRQIS_BOOT_SMOKE_CRATE=", self.demo)
        # Informational, not a pass/fail gate: crates are optional at
        # runtime (honest stub fallbacks) — the marker explains, the
        # drivers must not fail on it.
        for driver in (DIRECT_SMOKE, MENU_SMOKE):
            text = open(driver).read()
            self.assertNotIn(
                "NYRQIS_BOOT_SMOKE_CRATE", text,
                f"{os.path.basename(driver)}: CRATE is informational — "
                "the drivers must not gate on it")


if __name__ == "__main__":
    unittest.main()
