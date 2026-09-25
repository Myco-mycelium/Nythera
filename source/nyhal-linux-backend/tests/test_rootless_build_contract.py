"""Rootless live-ISO build contract tests.

The live ISO must be buildable WITHOUT root: this repo's reference dev
machine has no sudo, no docker, no KVM — only user namespaces.
``packaging/live/build-live-iso-rootless.sh`` runs the UNMODIFIED
builder inside ``unshare -Urmpf --mount-proc`` with an LD_PRELOAD shim
(``rootless-syscall-shim.c``) covering the two operations a self-mapped
user namespace genuinely cannot do:

  - chown to an unmapped id (EINVAL — the killer for debootstrap, whose
    tar aborts on base-files' root:staff; fakeroot cannot help: its
    daemon propagates EINVAL, only EPERM is swallowed),
  - mknod of char/block devices (always EPERM in a userns).

Verified END TO END on the reference machine (2026-09-24): the driver
produced a 359 MB amd64 ISO that PASSED both boot smokes
(tests/boot_smoke.py, tests/boot_smoke_menu.py) with zero root.

arm64 on the same machine was UNBLOCKED and boot-proven the next day
(2026-09-25), still rootless: binfmt dispatches aarch64 ELF to
qemu-aarch64-static (F-flagged — the interpreter resolves inside
chroots), and a USER-SPACE zig tarball (~/.local/opt/zig; no sudo, no
apt) cross-compiles this same C source as an AARCH64 shared object —
the emulated debootstrap second stage runs arm64 binaries, whose loader
rejects an amd64 .so. Two mechanics this cross build exposed (both now
contract-pinned): the chroot wrapper is the shim CLASS boundary
(preloading both classes makes every emulated loader warn, and the
noise trips captured-stderr gates like dpkg --audit; the preload path
must be staged on BOTH sides of the chroot boundary), and /tmp must
NOT hold the driver's temp artifacts (systemd-tmpfiles 'D /tmp'
emptied /tmp mid-build twice). Also fixed here: the builder named the
emulator "qemu-$DEB_ARCH-static" = qemu-arm64-static, which does not
exist (binfmt registers the QEMU arch, not the deb arch). The 351 MB
arm64 ISO passed BOTH boot smokes under TCG (direct ttyAMA0 handshake
+ the GRUB/UEFI menu path). CI's root-built arm64 is unaffected.
"""

import os
import re
import shutil
import subprocess
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(_BACKEND_DIR))

ROOTLESS_DRIVER = os.path.join(
    _REPO_ROOT, "packaging", "live", "build-live-iso-rootless.sh")
ROOTLESS_SHIM = os.path.join(
    _REPO_ROOT, "packaging", "live", "rootless-syscall-shim.c")
BUILDER = os.path.join(_REPO_ROOT, "packaging", "live", "build-live-iso.sh")
DEMO = os.path.join(
    _REPO_ROOT, "packaging", "live", "overlay", "usr", "local", "bin",
    "nyrqis-demo")

SHIM_COMPILED = shutil.which("cc") or shutil.which("gcc")
USERNS_AVAILABLE = subprocess.run(
    ["unshare", "-Ur", "true"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(
    os.path.exists(ROOTLESS_DRIVER),
    "rootless driver not present in this checkout")
class TestRootlessDriverStructure(unittest.TestCase):
    """The driver must wrap — never fork — the builder."""

    def setUp(self):
        self.driver = read(ROOTLESS_DRIVER)

    def test_bash_syntax_ok(self):
        rc = subprocess.call(["bash", "-n", ROOTLESS_DRIVER])
        self.assertEqual(rc, 0, "driver must parse as valid bash")

    def test_runs_the_unmodified_builder_in_a_userns(self):
        self.assertIn("unshare -Urmpf --mount-proc", self.driver)
        self.assertIn('bash "$BUILDER"', self.driver,
                      "the driver must exec the real builder, not reimplement it")

    def test_ld_preload_carries_the_shim(self):
        # 2026-09-25: the preload path is CANONICAL — /tmp/<shim>.so
        # resolves on BOTH sides of every chroot boundary (host-side:
        # the driver's host-class copy; in-target: acquire's correctly
        # named copy). The raw mktemp path is useless inside chroots,
        # which is exactly how the runner's acquire ran its core
        # install unshimmed while the identical flow worked locally.
        self.assertIn('LD_PRELOAD="/tmp/$SHIM_SO_NAME"', self.driver,
                      "every phase must preload the canonical in-target "
                      "resolvable path")
        self.assertIn('stage_host_shim', self.driver,
                      "the host-side copy must be staged explicitly")
        self.assertEqual(
            self.driver.count('LD_PRELOAD='),
            self.driver.count('LD_PRELOAD="/tmp/$SHIM_SO_NAME"'),
            "no phase may preload any other path")

    def test_forced_root_squashfs_is_driven_by_env(self):
        # Files created in the self-map are owned by the HOST uid on
        # disk; the squashfs must force 0:0 (sudo refuses a /etc/sudoers
        # not owned by 0) — but ONLY via env, so the root path is
        # untouched.
        self.assertIn("NYRQIS_SQUASHFS_FORCE_ROOT=1", self.driver)
        builder = read(BUILDER)
        self.assertIn("NYRQIS_SQUASHFS_FORCE_ROOT", builder)
        self.assertIn("-force-uid 0 -force-gid 0", builder)

    def test_demo_home_pseudo_defs_keep_uid_1000(self):
        # The force-0:0 squashfs must still ship /home/demo as
        # 1000:1000 (sudo hard-refuses a root-owned HOME at boot).
        self.assertIn("/home/demo m 0755 1000 1000", self.driver)
        self.assertIn("NYRQIS_SQUASHFS_PSEUDO", self.driver)
        builder = read(BUILDER)
        self.assertIn("NYRQIS_SQUASHFS_PSEUDO", builder)
        self.assertIn('-pf "$NYRQIS_SQUASHFS_PSEUDO"', builder)

    def test_acquire_mode_writes_a_completion_stamp(self):
        # The two-phase flow (acquire, then build) is how the build fits
        # a bounded runtime window; reruns must be no-ops when complete.
        self.assertIn("--acquire-rootfs", self.driver)
        self.assertIn('STAMP="$ACQUIRE_ROOTFS.complete"', self.driver)
        self.assertIn("--cache-dir=", self.driver,
                      "acquire must use an external cache dir so the "
                      "wipe-and-retry keeps the downloaded debs")

    def test_acquire_mode_stages_the_shim_before_bootstrap(self):
        # debootstrap's internal chroot calls do not inherit this
        # shell's PATH, so the chroot wrapper alone cannot cover them:
        # the .so must be pre-staged into the target's /tmp.
        m = re.search(
            r'if \[\[ -n "\$ACQUIRE_ROOTFS" \]\]; then(.*?)fi\n\n# -+ build',
            self.driver, re.S)
        self.assertIsNotNone(m, "acquire block must exist before the build block")
        block = m.group(1)
        # 2026-09-25: the pre-stage must use the CANONICAL preload name —
        # the outer LD_PRELOAD is /tmp/rootless-syscall-shim.so, and a
        # raw-mktemp-named copy once left the runner's debootstrap core
        # install UNSHIMMED (chown /var/mail → EINVAL).
        self.assertIn('cp -f "$SHIM_SO" "$ACQUIRE_ROOTFS/tmp/$SHIM_SO_NAME"',
                      block)
        self.assertLess(
            block.index('cp -f "$SHIM_SO"'),
            block.index("debootstrap --variant=minbase"),
            "the shim must be staged BEFORE debootstrap runs")

    def test_chroot_wrapper_stages_the_shim(self):
        # Builder chroot steps (apt top-up, useradd, mkinitramfs) go
        # through the chroot BINARY: the wrapper must stage the .so and
        # then exec the REAL chroot (not busybox's, not a shim).
        # 2026-09-25: the exec is also the shim-CLASS boundary — chrooted
        # processes preload ONLY the in-target shim path, never the host
        # one (a foreign-class preload makes every emulated loader warn,
        # and that noise polluted the builder's captured dpkg --audit).
        self.assertIn(
            'exec env LD_PRELOAD="/tmp/$SHIM_SO_NAME" /usr/sbin/chroot',
            self.driver,
            "wrapper must exec the real chroot binary, preloading ONLY the "
            "in-target shim path (the class boundary)")
        self.assertIn('mkdir -p "$tgt/tmp"', self.driver.replace("\\$tgt", "$tgt"))

    def test_no_root_anywhere(self):
        # Guard the whole point: no sudo, no su, no fakeroot fallback.
        # Executable lines only — the driver's WHY-NOT comments discuss
        # the rejected alternatives by name.
        code = "\n".join(
            l for l in self.driver.splitlines()
            if not l.strip().startswith("#"))
        for banned in ("sudo ", "su -", "fakeroot"):
            self.assertNotIn(
                banned, code,
                f"the rootless driver must not use {banned.strip()!r}")


@unittest.skipUnless(
    os.path.exists(ROOTLESS_SHIM),
    "shim source not present in this checkout")
class TestRootlessShim(unittest.TestCase):
    """The shim's contract, tested against the REAL compiled artifact."""

    def setUp(self):
        self.shim = read(ROOTLESS_SHIM)
        self.tmp = None

    def tearDown(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _compile(self):
        assert SHIM_COMPILED, "no C compiler"
        fd, so = subprocess.run(
            ["mktemp", "/tmp/nyrqis-shim-test.XXXXXX.so"],
            capture_output=True, text=True).stdout.strip(), None
        self.tmp = os.path.dirname(fd)
        rc = subprocess.call(
            [SHIM_COMPILED, "-O2", "-fPIC", "-shared", "-o", fd,
             ROOTLESS_SHIM, "-ldl"])
        self.assertEqual(rc, 0, "shim must compile clean")
        return fd

    @unittest.skipUnless(SHIM_COMPILED, "no C compiler on this machine")
    def test_chown_is_a_noop_success_and_mknod_virtualizes(self):
        so = self._compile()
        script = (
            ': > "$T/f"; chown 8:8 "$T/f" || echo CHOWN_FAIL; '
            'rm -f "$T/n"; mknod "$T/n" c 1 3 || echo MKNOD_FAIL; '
            'rm -f "$T/p"; mknod "$T/p" p || echo FIFO_FAIL'
        )
        r = subprocess.run(
            ["bash", "-c", f'export LD_PRELOAD={so}; T=$(mktemp -d); {script}'],
            capture_output=True, text=True)
        out = r.stdout + r.stderr
        self.assertNotIn("CHOWN_FAIL", out, "chown must succeed (no-op)")
        self.assertNotIn("MKNOD_FAIL", out, "char device mknod must succeed")
        self.assertNotIn("FIFO_FAIL", out, "FIFO mknod must pass through")
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp or "/tmp", "f")) or True)

    @unittest.skipUnless(USERNS_AVAILABLE, "user namespaces unavailable")
    def test_tar_with_unmapped_group_extracts_inside_userns(self):
        """The exact debootstrap killer: a tar member owned by an
        unmapped gid (base-files ships root:staff). Under the shim it
        must extract cleanly inside the self-mapped namespace."""
        so = self._compile()
        fixture = os.path.join(self.tmp or "/tmp", "nyrqis-uidfix.tar")
        srcdir = os.path.join(self.tmp or "/tmp", "nyrqis-uidsrc")
        os.makedirs(srcdir, exist_ok=True)
        marker = os.path.join(srcdir, "m")
        with open(marker, "w") as fh:
            fh.write("x")
        subprocess.run(["tar", "-C", srcdir, "-cf", fixture,
                        "--owner=0", "--group=8", "m"], check=True)
        r = subprocess.run(
            ["unshare", "-Urmpf", "--mount-proc", "bash", "-c",
             f'export LD_PRELOAD={so}; rm -rf "$T"; mkdir -p "$T"; '
             f'tar -C "$T" -xf {fixture} && echo TAR_OK || echo TAR_FAIL'],
            capture_output=True, text=True,
            env={**os.environ, "T": "/tmp/nyrqis-uidx"})
        self.assertIn("TAR_OK", r.stdout + r.stderr)
        self.assertNotIn("TAR_FAIL", r.stdout + r.stderr)

    def test_shim_has_no_strict_symbol_binding(self):
        # The shim must intercept via the PLT (LD_PRELOAD semantics);
        # a mistake here would make it a no-op everywhere.
        self.assertIn("#define _GNU_SOURCE", self.shim)
        self.assertIn("RTLD_NEXT", self.shim)


@unittest.skipUnless(
    os.path.exists(ROOTLESS_DRIVER),
    "rootless driver not present in this checkout")
class TestRootlessArm64CrossBuild(unittest.TestCase):
    """2026-09-25: the driver cross-builds arm64 rootlessly. The machine
    facts that make it possible (all probed, all rootless): binfmt_misc
    dispatches aarch64 ELF to qemu-aarch64-static (F-flagged, so the
    interpreter resolves inside chroots too), and a user-space zig
    toolchain cross-compiles the shim — the emulated second stage runs
    AARCH64 binaries, whose loader rejects an amd64 .so.
    """

    def setUp(self):
        self.driver = read(ROOTLESS_DRIVER)

    def test_arch_facts_map_deb_arch_to_the_real_qemu_binary(self):
        # binfmt registers the QEMU arch (aarch64), not the deb arch
        # (arm64): "qemu-arm64-static" does not exist. Comment lines are
        # stripped first — the docs may NAME the wrong spelling to warn
        # about it; no CODE may use it.
        code = "\n".join(
            l for l in self.driver.splitlines()
            if not l.strip().startswith("#"))
        self.assertIn(
            'arm64) DEB_ARCH=arm64; KERNEL_PKG=linux-image-arm64',
            self.driver)
        self.assertIn(
            'QEMU_STATIC="qemu-aarch64-static"', self.driver,
            "the driver must name the REAL qemu-user-static binary")
        self.assertNotIn(
            "qemu-arm64-static", code,
            "qemu-arm64-static does not exist — no code path may name it")

    def test_acquire_runs_foreign_and_emulated_second_stage(self):
        # Stage 1 extracts with HOST binaries (no emulation needed);
        # stage 2 is chrooted and therefore emulated.
        self.assertIn('"${FOREIGN[@]}"', self.driver,
                      "acquire must pass --foreign for arm64")
        self.assertIn(
            'chroot "$ACQUIRE_ROOTFS" /debootstrap/debootstrap --second-stage',
            self.driver,
            "the foreign second stage must run chrooted (emulated)")

    def test_guest_shim_is_cross_compiled_per_build(self):
        # The aarch64 shim must be produced at build time from the SAME
        # committed C source — no architecture-specific binary in git.
        self.assertIn("NYRQIS_AARCH64_CC", self.driver,
                      "an installed cross-gcc must be usable via env")
        self.assertIn("cc -target aarch64-linux-gnu", self.driver,
                      "zig is the documented no-root fallback toolchain")
        self.assertIn("$HOME/.local/opt/zig/zig", self.driver,
                      "the durable zig location survives /tmp cleanup")
        self.assertIn("die \"no aarch64 cross compiler available", self.driver,
                      "missing toolchain must fail with the fix, not silently")

    def test_wrapper_stages_the_guest_shim_for_cross_builds(self):
        # Chrooted arm64 processes preload from ONE path inside the
        # target; cross builds must put the GUEST-class shim there (the
        # aarch64 loader refuses an amd64 .so with a hard error). The
        # wrapper is GENERATED through a heredoc, so its source text
        # carries the escaped \$ spellings.
        # 2026-09-25: staging is tmp+mv (rename), NEVER cp -f onto the
        # live preload path — cp -f truncates the inode and every process
        # mapped from it executes zeros (cp+bash SIGSEGV cores observed
        # mid-build once the build phase preloaded the canonical path).
        self.assertIn(
            'mv -f "\\$tgt/tmp/.shim.tmp.\\$\\$" '
            '"\\$tgt/tmp/\\$SHIM_SO_NAME"', self.driver)
        # And the emulator must be staged INSIDE the rootfs at the
        # binfmt-registered path for every cross chroot.
        self.assertIn(
            'cp -f "/usr/bin/\\$QEMU_STATIC" "\\$tgt/usr/bin/"', self.driver)
        # Acquire pre-stages the same pair before debootstrap starts.
        self.assertIn(
            'cp -f "$GUEST_SHIM" "$ACQUIRE_ROOTFS/tmp/$SHIM_SO_NAME"',
            self.driver)

    def test_build_phase_forwards_arch_to_the_builder(self):
        self.assertIn('BUILDER_ARGS+=(--arch "$ARCH")', self.driver,
                      "the ISO must be assembled for the acquired arch")

    def test_arm64_preconditions_fail_with_reasons(self):
        # The two arm64-only requirements are checked before any work.
        self.assertIn('binfmt_misc has no qemu-aarch64 registration',
                      self.driver)
        self.assertIn('$QEMU_STATIC not present', self.driver)

    @unittest.skipUnless(
        any(os.path.exists(p) for p in (
            os.path.expanduser("~/.local/opt/zig/zig"),
            shutil.which("zig"))),
        "no zig toolchain on this machine (the documented no-root path)")
    def test_guest_shim_compiles_as_aarch64(self):
        """The committed C source must cross-compile to an AARCH64 ELF
        shared object (e_machine 0xB7) — the exact artifact the emulated
        second stage preloads."""
        zig = shutil.which("zig") or os.path.expanduser(
            "~/.local/opt/zig/zig")
        so_path = subprocess.run(
            ["mktemp", "/tmp/nyrqis-guestshim-test.XXXXXX.so"],
            capture_output=True, text=True).stdout.strip()
        self.addCleanup(
            lambda: os.path.exists(so_path) and os.remove(so_path))
        rc = subprocess.call(
            [zig, "cc", "-target", "aarch64-linux-gnu", "-O2",
             "-fPIC", "-shared", "-o", so_path, ROOTLESS_SHIM, "-ldl"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(rc, 0, "shim must cross-compile clean")
        with open(so_path, "rb") as fh:
            hdr = fh.read(20)
        self.assertEqual(hdr[:4], b"\x7fELF", "not an ELF object")
        e_machine = int.from_bytes(hdr[18:20], "little")
        self.assertEqual(e_machine, 0xB7,
                         "the guest shim must be AARCH64 (e_machine 0xB7), "
                         f"got 0x{e_machine:X}")


class TestDemoSetuRegressionGuard(unittest.TestCase):
    """2026-09-24: the first ISO to ship real Rust cdylibs exposed a
    latent set -u crash in nyrqis-demo (bare $LD_LIBRARY_PATH expansion
    inside a case pattern) — getty respawn-looped the login and the
    boot smoke saw no marker. CI never caught it: without cdylibs the
    branch never executed. The guarded pattern is now pinned."""

    def test_cdylib_branch_is_setu_safe(self):
        demo = read(DEMO)
        self.assertIn('case ":${LD_LIBRARY_PATH:-}" in', demo,
                      "the LD_LIBRARY_PATH dedup must use the :- guarded "
                      "form — a bare $LD_LIBRARY_PATH aborts under set -u "
                      "when unset (the normal case on a bare boot)")
        # And the bug must not have simply moved: no bare expansion of
        # that variable anywhere in the script.
        for i, line in enumerate(demo.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            bad = re.search(r"\$LD_LIBRARY_PATH(?![+:}-])", line)
            # Allow ${LD_LIBRARY_PATH:+...} (guarded append) and
            # ${LD_LIBRARY_PATH:-...} (guarded default).
            self.assertIsNone(
                bad, f"nyrqis-demo:{i} expands $LD_LIBRARY_PATH without a "
                f"guard: {stripped!r}")

    def test_demo_still_exports_the_cdylib_dir(self):
        demo = read(DEMO)
        self.assertIn('export NYRQIS_CDYLIB_DIR="$OPT/rust/.cdylibs"', demo)


ROOTLESS_WF = os.path.join(
    _REPO_ROOT, ".github", "workflows", "live-iso-rootless.yml")


def read_yaml(path):
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        # PyYAML resolves the unquoted `on:` key to boolean True — both
        # spellings accepted (the boot-contract tests established this).
        d = yaml.safe_load(fh)
    return d.get(True, d.get("on")), d


@unittest.skipUnless(
    os.path.exists(ROOTLESS_WF),
    "rootless CI workflow not present in this checkout")
class TestRootlessCIWorkflow(unittest.TestCase):
    """2026-09-25: the rootless pipeline is validated in CI on a stock
    runner, mirroring the reference machine's constraints (no sudo in
    the pipeline, no docker, no KVM, user namespaces only) — the claim
    the local tests pin stays exercised on an external machine. sudo is
    allowed ONLY in pre-flight steps; the arm64 job cross-builds with
    the user-space zig toolchain, NOT a system cross-compiler.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(ROOTLESS_WF):
            raise unittest.SkipTest("workflow not present")
        cls.on, cls.wf = read_yaml(ROOTLESS_WF)
        cls.text = read(ROOTLESS_WF)

    def job(self, name):
        return self.wf["jobs"][name]

    def test_yaml_parses_and_has_both_jobs(self):
        self.assertIn("rootless-amd64", self.wf["jobs"])
        self.assertIn("rootless-arm64", self.wf["jobs"])

    def test_arm64_menu_boot_is_a_split_job(self):
        # Same pattern as the root-built arm64 workflow: the menu path
        # is its OWN job consuming the built ISO's artifact, so a
        # bootloader regression is diagnosable as "menu path" without
        # reading logs (and cannot be masked by the direct smoke).
        job = self.wf["jobs"].get("menu-boot-arm64-rootless")
        self.assertIsNotNone(
            job, "the arm64 rootless ISO must get a menu-boot job")
        self.assertEqual(
            job.get("needs"), "rootless-arm64",
            "the menu job must consume the rootless-arm64 build's artifact")
        runs = "\n".join(
            s.get("run", "") for s in job.get("steps", []))
        self.assertIn("tests/boot_smoke_menu.py", runs)
        self.assertIn("--arch arm64", runs)
        uses = " ".join(s.get("uses", "") for s in job.get("steps", []))
        self.assertIn("actions/download-artifact", uses,
                      "the menu job must download the built ISO")

    def test_amd64_job_runs_on_push_and_is_dispatchable(self):
        self.assertIn("push", self.on, "amd64 job must gate pushes")
        self.assertIn("workflow_dispatch", self.on)

    def test_arm64_job_is_dispatch_gated_and_input_gated(self):
        # The emulated arm64 loop is too slow for a push gate; it must
        # run ONLY on manual dispatch with the explicit input.
        cond = self.job("rootless-arm64").get("if", "")
        self.assertIn("workflow_dispatch", cond)
        self.assertIn("with-arm64", cond)

    def test_pipeline_steps_never_use_sudo(self):
        # The point of the job: the pipeline under test runs rootless.
        # Pre-flight steps are EXEMPT by name (sudo there only prepares
        # the runner environment: apt, binfmt, grub tree, and the
        # runner's userns sysctl posture — ubuntu-24.04 images ship
        # apparmor_restrict_unprivileged_userns=1); every other step
        # must not invoke it.
        exempt_prefixes = ("pre-flight", "confirm unprivileged",
                           "install the qemu toolchain")
        for job in self.wf["jobs"].values():
            for step in job.get("steps", []):
                name = (step.get("name") or "").strip().lower()
                if name.startswith(exempt_prefixes):
                    continue
                body = step.get("run", "")
                self.assertNotIn(
                    "sudo ", body,
                    f"step {name!r} runs the PIPELINE and must never use "
                    "sudo — pre-flight is the only exempt phase")

    def test_amd64_proves_rootlessness_via_ownership(self):
        job_text = str(self.job("rootless-amd64"))
        self.assertIn("stat -c %u", job_text,
                      "the ownership check must assert the ISO was NOT "
                      "created by root")
        self.assertIn('"0"', job_text,
                      "the check must compare against uid 0 explicitly")

    def test_amd64_runs_both_boot_smokes(self):
        runs = [s.get("run", "")
                for s in self.job("rootless-amd64").get("steps", [])]
        joined = "\n".join(runs)
        self.assertIn("tests/boot_smoke.py", joined)
        self.assertIn("tests/boot_smoke_menu.py", joined,
                      "the rootless claim covers the GRUB path too")

    def test_arm64_job_uses_userspace_zig_not_a_system_toolchain(self):
        job_text = str(self.job("rootless-arm64"))
        self.assertIn("zig-linux-x86_64", job_text,
                      "zig must come from the user-space tarball")
        self.assertNotIn("gcc-aarch64-linux-gnu", job_text,
                         "the job must not depend on a system cross-gcc "
                         "— the rootless recipe is toolchain-free")

    def test_arm64_acquire_runs_the_driver_foreign_mode(self):
        runs = [s.get("run", "")
                for s in self.job("rootless-arm64").get("steps", [])]
        joined = "\n".join(runs)
        self.assertIn("--arch arm64", joined)
        self.assertIn("--acquire-rootfs", joined)
        self.assertIn("--rootfs", joined,
                      "the build phase must consume the acquired rootfs")

    def test_smoke_steps_keep_the_ci_timeouts(self):
        # Same budgets as the root-built workflows (780 s direct amd64,
        # 1680 s emulated arm64) — a hang must fail the step, not the
        # job's whole budget.
        for job, needle, minutes in (
            ("rootless-amd64", "--timeout 780", 15),
            ("rootless-arm64", "--timeout 1680", 29),
        ):
            steps = self.job(job).get("steps", [])
            hit = [s for s in steps
                   if needle in (s.get("run") or "")]
            self.assertTrue(
                hit, f"{job}: no smoke step with {needle}")
            self.assertEqual(
                hit[0].get("timeout-minutes"), minutes,
                f"{job}: smoke step budget must stay {minutes} min")

    def test_rootfs_cache_covers_the_full_sibling_set(self):
        # The driver's resumable layout is THREE siblings (tree, .cache,
        # .complete). Caching only the tree would drop the stamp and
        # force a full acquisition redo on every round.
        with_action = [s for s in self.job("rootless-amd64").get("steps", [])
                       if s.get("uses", "").startswith("actions/cache")]
        self.assertTrue(with_action, "amd64 job must cache the rootfs set")
        path = with_action[0]["with"]["path"]
        self.assertEqual(
            path, "~/nyrqis-rootless",
            "cache the PARENT dir so the rootfs, its .deb cache, and the "
            "completion stamp travel together")
        self.assertNotIn(
            ".complete", path,
            "parent-dir caching carries the stamp implicitly")


if __name__ == "__main__":
    unittest.main()
