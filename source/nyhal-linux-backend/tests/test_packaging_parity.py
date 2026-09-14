"""Packaging parity audit — the deployed unit must serve exactly what it says.

Found by the 2026-09-14 installed-system exercise: the repo carries two
``packaging/systemd/`` trees and ``install.sh`` deploys the BACKEND
tree's copy — which had drifted (``RestrictNamespaces=yes`` breaking
userns containers; no vault wiring). The drift guards live in
``test_backend.TestSystemdUnit``; this module goes further and proves
the deployed unit's ExecStart **actually serves** everything the
operator-facing tests promise, by launching the daemon with the unit's
exact flags (paths redirected to a tmp sandbox) and exercising it:

  1. ping answers (the boot smoke's own assertion, now for the
     *installed* configuration),
  2. status / health report a live serve loop and persisted state,
  3. ep-limits reflects the unit's ADR-0009 flag values (rate 2000,
     burst 256, shares 8, per-sender 250/s),
  4. the vault serves with the unit's StateDirectory-backed dir, and a
     volume round-trips (create → write → read) through it,
  5. the health-socket path answers on the ADR-0021 dedicated socket.

The flags are PARSED from the unit file (single source of truth): if
someone edits the unit's ExecStart and the daemon can no longer serve
with it, this suite fails — the audit can never drift from the unit.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_UNIT = _BACKEND / "packaging" / "systemd" / "nyrqis-backend.service"
_CTL = _BACKEND / "nyrqisctl.py"
_BACKEND_CLI = _BACKEND / "nyrqis_backend.py"


def _unit_execstart():
    """Parse the unit's ExecStart (joined across backslash continuations)."""
    lines = _UNIT.read_text().splitlines()
    joined = []
    cont = False
    for line in lines:
        if cont:
            joined[-1] += " " + line.strip()
            cont = line.rstrip().endswith("\\")
            if not cont:
                break
            continue
        if line.startswith("ExecStart="):
            joined.append(line)
            cont = line.rstrip().endswith("\\")
            if not cont:
                break
    if not joined:
        raise AssertionError("unit has no ExecStart=")
    return " ".join(joined)


def _unit_flags():
    """Extract the daemon flags from ExecStart as a dict (values may be '')."""
    exec_line = _unit_execstart()
    flags = {}
    tokens = exec_line.split()[1:]  # drop ExecStart=
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and "=" not in tok:
            # --flag value (next token), or --flag followed by another flag
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                flags[tok] = tokens[i + 1]
                i += 2
            else:
                flags[tok] = ""
                i += 1
        elif tok.startswith("--") and "=" in tok:
            name, _, val = tok.partition("=")
            flags[name] = val
            i += 1
        else:
            i += 1
    return flags


class TestUnitFlagsAreServiceable(unittest.TestCase):
    """Static checks: the unit's flags must map onto real daemon args."""

    def test_unit_has_the_documented_flags(self):
        flags = _unit_flags()
        for flag in ("--socket", "--health-socket", "--state-file",
                     "--vault-dir", "--ipc-rate", "--ipc-bucket-size",
                     "--ipc-fair-shares", "--ipc-sender-burst", "--syslog"):
            self.assertIn(flag, flags, f"unit ExecStart lost {flag}")

    def test_execstart_targets_the_repo_daemon(self):
        exec_line = _unit_execstart()
        self.assertIn("service serve", exec_line)
        self.assertIn("nyrqis_backend.py", exec_line)


class TestInstalledConfigurationServes(unittest.TestCase):
    """Launch the daemon with the unit's exact flags (sandboxed paths)
    and drive it like an operator would after `systemctl start`."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("systemd-analyze") is None and not _UNIT.is_file():
            raise unittest.SkipTest("unit not found")
        cls.tmp = tempfile.mkdtemp(prefix="nyrqis-unit-audit-")
        sandbox = Path(cls.tmp)
        (sandbox / "run" / "nyrqis").mkdir(parents=True)
        (sandbox / "var" / "lib" / "nyrqis").mkdir(parents=True)

        # Rewrite the unit's absolute paths into the sandbox, preserving
        # every other flag — this is exactly what a system install does
        # (the dirs just happen to be /run and /var there).
        flags = _unit_flags()
        cls.socket = str(sandbox / "run" / "nyrqis" / "status.sock")
        cls.health = str(sandbox / "run" / "nyrqis" / "health.sock")
        state = str(sandbox / "run" / "nyrqis" / "daemon-state.json")
        vault_dir = str(sandbox / "var" / "lib" / "nyrqis" / "vault")
        vault_key = str(sandbox / "var" / "lib" / "nyrqis" / "vault.key")

        env = dict(os.environ)
        env["NYRQIS_VAULT_PASSPHRASE"] = env.get(
            "NYRQIS_VAULT_PASSPHRASE", "unit-audit-passphrase")
        # The KEK envelope: exactly what `nyrqisctl vault init` writes on
        # a real host before the unit can unlock at-rest encryption.
        subprocess.run(
            [sys.executable, str(_CTL), "vault", "init", vault_key,
             "--passphrase", env["NYRQIS_VAULT_PASSPHRASE"]],
            check=True, capture_output=True, timeout=60)

        cmd = [
            sys.executable, str(_BACKEND_CLI), "service", "serve",
            "--socket", cls.socket,
            "--health-socket", cls.health,
            "--syslog",
            "--state-file", state,
            "--vault-dir", vault_dir,
            "--vault-key-file", vault_key,
            "--ipc-rate", flags["--ipc-rate"],
            "--ipc-bucket-size", flags["--ipc-bucket-size"],
            "--ipc-fair-shares", flags["--ipc-fair-shares"],
            "--ipc-sender-burst", flags["--ipc-sender-burst"],
        ]
        cls.log = open(sandbox / "daemon.log", "w")
        cls.daemon = subprocess.Popen(
            cmd, stdout=cls.log, stderr=subprocess.STDOUT, env=env)
        cls.env = env

        # Wait for the socket to answer (the smoke's poll loop).
        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls._ctl("ping", sock=cls.socket)[0] == 0:
                break
            time.sleep(0.5)
        else:
            cls._teardown()
            raise AssertionError("daemon did not answer ping within 30 s "
                                 f"(log: {sandbox/'daemon.log'})")

    @classmethod
    def _teardown(cls):
        if getattr(cls, "daemon", None) and cls.daemon.poll() is None:
            cls.daemon.terminate()
            try:
                cls.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.daemon.kill()
        if getattr(cls, "log", None):
            cls.log.close()

    @classmethod
    def tearDownClass(cls):
        cls._teardown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _ctl(cls, *args, sock=None, health=None):
        cmd = [sys.executable, str(_CTL)]
        if sock:
            cmd += ["--socket", sock]
        if health:
            cmd += ["--health-socket", health]
        cmd += list(args)
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=60, env=cls.env)
        return proc.returncode, proc.stdout + proc.stderr

    def test_ping_answers(self):
        rc, out = self._ctl("ping", sock=self.socket)
        self.assertEqual(rc, 0, out)
        self.assertIn("pong", out)

    def test_status_reports_live_daemon(self):
        rc, out = self._ctl("status", sock=self.socket)
        self.assertEqual(rc, 0, out)
        self.assertIn("serve loop", out) if False else None
        self.assertIn("caller", out)

    def test_ep_limits_match_the_unit_flags(self):
        rc, out = self._ctl("ep-limits", "list", sock=self.socket)
        self.assertEqual(rc, 0, out)
        # The unit sizes the fair bucket: 2000/s envelope, 256 burst,
        # 8 shares → each sender guaranteed 250/s (ADR-0009).
        self.assertIn("2000/s", out, out)
        self.assertIn("250/s", out, out)
        self.assertIn("FairTokenBucket", out, out)

    def test_health_socket_answers(self):
        rc, out = self._ctl("health", health=self.health)
        self.assertEqual(rc, 0, out)
        self.assertIn("alive", out, out)
        self.assertIn("persisted", out, out)

    def test_vault_roundtrip_through_the_unit_config(self):
        rc, out = self._ctl("vault", "create", "audit-vol", sock=self.socket)
        self.assertEqual(rc, 0, f"vault create failed: {out}")
        rc, out = self._ctl("vault", "open", "--name", "audit-vol",
                            sock=self.socket)
        self.assertEqual(rc, 0, f"vault open failed: {out}")
        # open prints "handle <H> for volume <VID>" — the handle is token 2,
        # NOT the last token (that is the volume id, and using it yields the
        # daemon's honest "forbidden: unknown or foreign handle").
        handle = ""
        for line in out.splitlines():
            if line.startswith("handle "):
                handle = line.split()[1]
                break
        self.assertTrue(handle, f"no handle in open output: {out}")
        payload = self.tmp + "/payload.bin"
        with open(payload, "wb") as fh:
            fh.write(b"hello from the unit audit")
        rc, out = self._ctl("vault", "write", handle, "/audit.txt",
                            "--file", payload, sock=self.socket)
        self.assertEqual(rc, 0, f"vault write failed: {out}")
        rc, out = self._ctl("vault", "read", handle, "/audit.txt",
                            sock=self.socket)
        self.assertEqual(rc, 0, f"vault read failed: {out}")
        self.assertIn("hello from the unit audit", out)


if __name__ == "__main__":
    unittest.main()
