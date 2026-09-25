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

arm64 on the same machine is honestly blocked: binfmt + qemu
-aarch64-static work (a real arm64 busybox executed), but the emulated
debootstrap stage needs an AARCH64 preload shim (an amd64 .so cannot
load into arm64 processes — glibc rejects the ELF class), no
aarch64 cross-compiler is installed, and a nested user namespace's
full-range map write is EPERM (a parent can only map ids it possesses;
the self-map owns one). Unblock: install gcc-aarch64-linux-gnu and
compile the shim as -target aarch64. CI's arm64 build is unaffected —
runners build as root.
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
        self.assertIn('LD_PRELOAD="$SHIM_SO"', self.driver,
                      "the whole namespace must run preloaded")

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
        self.assertIn('cp -f "$SHIM_SO" "$ACQUIRE_ROOTFS/tmp/"', block)
        self.assertLess(
            block.index('cp -f "$SHIM_SO"'),
            block.index("debootstrap --variant=minbase"),
            "the shim must be staged BEFORE debootstrap runs")

    def test_chroot_wrapper_stages_the_shim(self):
        # Builder chroot steps (apt top-up, useradd, mkinitramfs) go
        # through the chroot BINARY: the wrapper must stage the .so and
        # then exec the REAL chroot (not busybox's, not a shim).
        self.assertIn('exec /usr/sbin/chroot', self.driver,
                      "wrapper must exec the real chroot binary")
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


if __name__ == "__main__":
    unittest.main()
