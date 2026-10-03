#!/usr/bin/env python3
"""The Nyrqis live demo — a scripted operator session (run_demo.sh).

NOTE: the desktop-rendering demo (screenshots of the shell) lives in
run_demo.py in this directory; this script is the OPERATOR-SESSION
demo — a real daemon, a real signed repo, real crash spooling.

A complete, self-contained demonstration of the platform's OPERATOR
surface, run against a real daemon on this machine:

  Act I    — bring-up: ping/status/health over the IPC transport
  Act II   — containers: create, inspect, audit trail
  Act III  — packages: a REAL signed repository (publisher key, trust
             store, signed index), then the UPD-001 Option A update
             cycle: verify → update (restore point → apply → audit) →
             rollback
  Act IV   — crash reporting: the CRY-001 Option A local spool, driven
             through a real daemon restart (the §4.5 recovery path
             spools a redacted report; the operator inspects it)
  Act V    — diagnostics: the DBG-001 incident bundle (redaction
             default-on)

Every act prints PASS/FAIL verdicts; the script exits 0 only when all
acts pass. No network egress anywhere in the demo.

Usage:
    python3 demo/run_demo.sh                # the full session
    python3 demo/run_demo.sh --quick        # skip Acts II and V
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

PASS = "  PASS "
FAIL = "  FAIL "
_results: list = []

# Slow-machine budgets. TCG-slowed runners boot far slower than a fixed
# sleep can guess (the boot smoke's own philosophy) — the 2026-10-02
# in-VM ops drill died in Act I on the fixed 15 s socket wait / 20 s ctl
# budget: a cold daemon start under TCG with a live desktop session
# exceeds both. Env-overridable so CI and slow hardware can go wider.
DAEMON_WAIT_S = int(os.environ.get("NYRQIS_DEMO_DAEMON_WAIT_S", "60"))
CTL_TIMEOUT_S = int(os.environ.get("NYRQIS_DEMO_CTL_TIMEOUT_S", "90"))


def verdict(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{PASS if ok else FAIL} {name}" + (f" — {detail}" if detail else ""))
    _results.append(ok)
    return ok


def step(msg: str) -> None:
    print(f"\n== {msg}")


class Daemon:
    """A serve subprocess on a private socket/state/vault."""

    def __init__(self, tmp: Path, with_spool: bool = False):
        self.dir = tmp
        self.socket = str(tmp / "demo.sock")
        self.state = str(tmp / "daemon-state.json")
        self.spool = str(tmp / "crash-spool")
        cmd = [
            sys.executable, str(BACKEND / "nyrqis_backend.py"),
            "service", "serve",
            "--socket", self.socket,
            "--state-file", self.state,
            "--vault-dir", str(tmp / "vault"),
            "--commit-interval", "1",
        ]
        if with_spool:
            cmd += ["--crash-spool", self.spool]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._wait_socket(DAEMON_WAIT_S)

    def _wait_socket(self, timeout: float = 15.0) -> bool:
        import socket as sock_mod
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.socket):
                s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_DGRAM)
                try:
                    s.settimeout(0.5)
                    s.connect(self.socket)
                    s.close()
                    return True
                except OSError:
                    pass
            time.sleep(0.1)
        return False

    def ctl(self, *argv: str,
            timeout: int | None = None) -> subprocess.CompletedProcess:
        # Global flags precede the command in nyrqisctl's CLI grammar.
        # A timeout is an HONEST FAIL verdict, not a traceback: the
        # TimeoutExpired used to escape act1 and kill the whole demo
        # before a single verdict printed (2026-10-02 in-VM drill).
        if timeout is None:
            timeout = CTL_TIMEOUT_S
        try:
            return subprocess.run(
                [sys.executable, str(BACKEND / "nyrqisctl.py"),
                 "--socket", self.socket, *argv],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                list(argv), 124,
                stdout="",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str)
                else f"timed out after {timeout}s")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def act1_bringup(d: Daemon) -> None:
    step("Act I — bring-up (ping / status / health over IPC)")
    r = d.ctl("ping")
    verdict("daemon ping", r.returncode == 0, r.stdout.strip()[:70])
    r = d.ctl("status")
    verdict("daemon status", r.returncode == 0 and bool(r.stdout.strip()),
            (r.stdout.strip().splitlines() or [""])[0][:70])


def act2_containers(d: Daemon) -> None:
    step("Act II — containers (inspect)")
    r = d.ctl("containers", "list")
    ok = r.returncode == 0
    verdict("containers list (operator op)", ok, r.stdout.strip()[:60])
    # The audit surface answers per-container (the audit_log op REQUIRES
    # a container_id — there is no daemon-wide trail; discovered on the
    # wire 2026-09-23). With an empty fleet the honest check is that the
    # REFUSAL is well-formed (rc=1 + a reason), not a transport error.
    r = d.ctl("audit-log", "demo-ctr")
    verdict("audit surface answers (well-formed per-container refusal)",
            r.returncode == 1 and "not found" in (r.stdout + r.stderr),
            (r.stderr.strip() or r.stdout.strip())[:60])


def act3_packages(tmp: Path, d: Daemon) -> None:
    step("Act III — packages: a real signed repo + the UPD-001 Option A cycle")
    sys.path.insert(0, str(BACKEND))
    from backend import package_repo, package_signing, delta_update

    build = tmp / "build"
    repo_root = tmp / "repo"
    trust = tmp / "trust.json"
    install_root = tmp / "install"
    state_dir = tmp / "orchestrator"

    kp = package_signing.SigningKeypair.generate()
    v1 = build / "v1"; v1.mkdir(parents=True)
    (v1 / "app.bin").write_bytes(b"DEMO-APP-v1")
    (v1 / "meta.txt").write_text("release one")
    v2 = build / "v2"; v2.mkdir(parents=True)
    (v2 / "app.bin").write_bytes(b"DEMO-APP-v2-UPGRADED")
    (v2 / "meta.txt").write_text("release two")

    repo = package_repo.PackageRepository(str(repo_root))
    repo.publish_package(str(v1), "demo-app", "1.0.0", kp)
    delta = delta_update.create_delta_update(
        str(v1), str(v2), "demo-app", "1.0.0", "1.1.0", signing_keypair=kp)
    payload = build / "demo-app-1.0.0_1.1.0.delta"
    delta_update.save_delta_update(delta, str(payload))
    repo.publish_delta(delta, str(payload), kp)
    trust.write_text(json.dumps({
        "trusted_keys": [{"key_id": kp.fingerprint,
                          "public_key": __import__("base64").b64encode(
                              kp.public_key).decode()}]}))
    install_dir = install_root / "demo-app"
    shutil.copytree(v1, install_dir)
    # The installer-layout manifest _installed_versions reads.
    (install_dir / "manifest.json").write_text(json.dumps({
        "package_id": "demo-app", "version": "1.0.0",
    }))
    verdict("signed repository built (index + delta, publisher key)",
            (repo_root / "index.json").is_file())

    base = [sys.executable, str(BACKEND / "nyrqisctl.py")]

    r = subprocess.run(base + ["packages", "verify",
                               "--repo-root", str(repo_root),
                               "--trust-store", str(trust)],
                       capture_output=True, text=True)
    verdict("packages verify — index signature verified", r.returncode == 0,
            r.stdout.strip())

    r = subprocess.run(base + ["packages", "update", "demo-app",
                               "--repo-root", str(repo_root),
                               "--trust-store", str(trust),
                               "--install-root", str(install_root),
                               "--state-dir", str(state_dir)],
                       capture_output=True, text=True)
    updated = r.returncode == 0 and "1.1.0" in r.stdout
    verdict("packages update — verify→restore point→apply→audit",
            updated, r.stdout.strip()[:70])
    app = install_dir / "app.bin"
    manifest = json.loads((install_dir / "manifest.json").read_text())
    verdict("inventory manifest updated to 1.1.0",
            manifest.get("version") == "1.1.0")
    verdict("payload actually upgraded", app.read_bytes() == b"DEMO-APP-v2-UPGRADED")

    r = subprocess.run(base + ["packages", "rollback", "demo-app", "1.1.0",
                               "--repo-root", str(repo_root),
                               "--trust-store", str(trust),
                               "--install-root", str(install_root),
                               "--state-dir", str(state_dir)],
                       capture_output=True, text=True)
    rolled = r.returncode == 0
    verdict("packages rollback — pre-apply restore point restored",
            rolled and app.read_bytes() == b"DEMO-APP-v1",
            r.stdout.strip()[:60])

    # Tamper demonstration: corrupt the repo delta, the orchestrator
    # must refuse (fail-closed) and leave the install untouched.
    entry = {"type": "delta", "package_id": "demo-app",
             "version_from": "1.0.0", "version_to": "1.1.0"}
    target = repo.payload_path(entry)
    target.write_bytes(target.read_bytes() + b"TAMPER")
    before = app.read_bytes()
    r = subprocess.run(base + ["packages", "update", "demo-app",
                               "--repo-root", str(repo_root),
                               "--trust-store", str(trust),
                               "--install-root", str(install_root),
                               "--state-dir", str(state_dir)],
                       capture_output=True, text=True)
    verdict("tampered delta REFUSED (fail-closed), content untouched",
            r.returncode != 0 and app.read_bytes() == before,
            (r.stderr.strip().splitlines() or [""])[-1][:70])


def act4_crash(tmp: Path) -> None:
    step("Act IV — crash reporting: the CRY-001 Option A local spool")
    spool = tmp / "crash-spool"
    d1 = Daemon(tmp, with_spool=True)
    d1.stop()  # leaves a STALE state file behind
    verdict("first daemon exited (stale state file left)",
            os.path.exists(d1.state))
    d2 = Daemon(tmp, with_spool=True)  # recovery → spool
    try:
        reports = sorted(Path(spool).glob("crash-*.json"))
        ok = bool(reports)
        verdict("recovery spooled a LOCAL crash report", ok,
                reports[0].name if ok else "no report appeared")
        if ok:
            report = json.loads(reports[0].read_text())
            rec = report.get("recovery_record", {})
            verdict("report redacted at write (no vault aggregates)",
                    report.get("redacted") is True
                    and rec.get("vault", {}).get("logical_bytes") is None
                    if isinstance(rec.get("vault"), dict) else
                    report.get("redacted") is True)
            r = d2.ctl("crash", "list", "--spool-dir", str(spool))
            verdict("nyrqisctl crash list (operator surface)", r.returncode == 0,
                    (r.stdout.strip().splitlines() or [""])[0][:60])
            r = d2.ctl("crash", "show", reports[0].stem,
                       "--spool-dir", str(spool))
            verdict("nyrqisctl crash show", r.returncode == 0
                    and "report_id" in r.stdout)
    finally:
        d2.stop()


def act5_bundle(d: Daemon, tmp: Path) -> None:
    step("Act V — diagnostics: the DBG-001 incident bundle")
    out = tmp / "bundle"
    r = d.ctl("debug", "bundle", "--out", str(out))
    ok = r.returncode == 0 and (out / "meta.json").is_file()
    verdict("debug bundle assembled (redaction default-on)", ok,
            r.stdout.strip()[:60])
    if ok:
        meta = json.loads((out / "meta.json").read_text())
        verdict("bundle recorded redaction state", "redacted" in meta)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true",
                    help="Skip Acts II and V (bring-up, packages, crash)")
    args = ap.parse_args()

    print("Nyrqis live demo — scripted operator session")
    print("(everything below runs on THIS machine; no network egress)")
    tmp = Path(tempfile.mkdtemp(prefix="nyrqis-demo-"))
    try:
        d = Daemon(tmp, with_spool=True)
        try:
            act1_bringup(d)
            if not args.quick:
                act2_containers(d)
            act3_packages(tmp, d)
            d.stop()
        finally:
            try:
                d.stop()
            except Exception:
                pass
        act4_crash(tmp)
        if not args.quick:
            d3 = Daemon(tmp, with_spool=False)
            try:
                act5_bundle(d3, tmp)
            finally:
                d3.stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total, passed = len(_results), sum(_results)
    print(f"\nDEMO VERDICT: {passed}/{total} checks passed — "
          f"{'DEMO PASS' if passed == total else 'DEMO FAIL'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
