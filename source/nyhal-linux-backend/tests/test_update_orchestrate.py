"""Contract pins for the UPD-001 Option A update orchestration
(accepted as owner direction 2026-09-27; UPD-001 v0.2.0 §7).

Pinned properties (the UPD-001 §7.7 plan):
- verification STRICTLY precedes any payload mutation (a tampered or
  unsigned delta yields a refusal, never a partial apply);
- validate_rollback posture: a same-or-newer rollback target is
  refused;
- fail-closed without PyNaCl (the shipped delta_signing posture —
  inherited, not re-implemented);
- the restore point exists BEFORE any payload mutation, and a failed
  restore point REFUSES the apply;
- the audit chain carries the delta checksum;
- the NO-DIRECT-EGRESS assertion: the module imports no HTTP client —
  fetch stays behind the operator-configured registry client.
"""

import base64
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

uo = importlib.import_module("backend.update_orchestrate")
UpdateOrchestrator = uo.UpdateOrchestrator
UpdateOrchestrationError = uo.UpdateOrchestrationError
version_tuple = uo.version_tuple

repo_mod = importlib.import_module("backend.package_repo")
delta_mod = importlib.import_module("backend.delta_update")
signing_mod = importlib.import_module("backend.package_signing")

HAS_NACL = getattr(delta_mod, "HAS_NACL", False)


class _FakeAuditManager:
    def __init__(self):
        self.created = 0
        self.entries = []

    def create_audit_chain(self, container_id):
        self.created += 1
        return {"chain_id": f"chain-fake-{self.created}"}

    def append_audit_entry(self, chain_id, op, result=None):
        self.entries.append((op, dict(result or {})))
        return {"ok": True}


def _write_payload(root: Path, files: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes)
                      else content.encode())
    return root


class _DemoRepo:
    """A real signed repo with v1.0.0 published and a v1.0.0→v1.1.0
    delta (built with the shipped publisher machinery — no mocks)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.kp = signing_mod.SigningKeypair.generate()
        self.repo = repo_mod.PackageRepository(str(tmp / "repo"))
        # Flat payload layout (diff_packages supports .nypkg AND flat;
        # flat keeps paths consistent with apply_delta_update's
        # "without the images/ prefix" convention).
        self.v1 = _write_payload(tmp / "build" / "v1", {
            "app.bin": b"APP-v1",
            "data.bin": b"DATA-v1",
            "meta.txt": "one",
        })
        self.v2 = _write_payload(tmp / "build" / "v2", {
            "app.bin": b"APP-v2-UPGRADED",
            "data.bin": b"DATA-v1",
            "meta.txt": "two",
        })
        self.repo.publish_package(str(self.v1), "demo", "1.0.0", self.kp)
        self.delta = delta_mod.create_delta_update(
            str(self.v1), str(self.v2), "demo", "1.0.0", "1.1.0",
            signing_keypair=self.kp)
        payload = tmp / "build" / "demo-1.0.0_1.1.0.delta"
        delta_mod.save_delta_update(self.delta, str(payload))
        self.repo.publish_delta(self.delta, str(payload), self.kp)
        # The trust store in package_repo.load_index's expected shape:
        # trusted_keys: [{key_id, public_key}] (list-of-objects form).
        # SigningKeypair.public_key IS the raw 32-byte verify key and
        # .fingerprint IS the key_id (key_id is a deprecated alias).
        self.trust_store = tmp / "trust.json"
        self.trust_store.write_text(json.dumps({
            "trusted_keys": [{
                "key_id": self.kp.fingerprint,
                "public_key": base64.b64encode(
                    self.kp.public_key).decode(),
            }],
        }))
        # The installed base: the v1 payload copied into place.
        self.install_root = tmp / "install"
        self.install_dir = self.install_root / "demo"
        shutil.copytree(self.v1, self.install_dir)

    def orchestrator(self, audit=None) -> UpdateOrchestrator:
        return UpdateOrchestrator(
            repo_root=str(self.tmp / "repo"),
            trust_store_path=str(self.trust_store),
            install_root=str(self.install_root),
            state_dir=str(self.tmp / "state"),
            audit_manager=audit,
        )


@unittest.skipUnless(HAS_NACL, "PyNaCl required for signed-update pins")
class ApplyUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.demo = _DemoRepo(Path(self._tmp.name))

    def test_happy_path_applies_verified_delta_with_restore_point(self):
        orch = self.demo.orchestrator()
        result = orch.apply_update("demo", "1.0.0")
        self.assertEqual(result["version_to"], "1.1.0")
        self.assertEqual(result["delta_checksum"], self.demo.delta["checksum"])
        self.assertTrue(result["restore_point"])
        # delta ops normalize away the images/ container prefix (the
        # shipped diff_packages behavior) — touched paths are relative
        # to the install dir.
        self.assertIn("app.bin", result["touched"])
        self.assertEqual(
            (self.demo.install_dir / "app.bin").read_bytes(),
            b"APP-v2-UPGRADED")

    def test_apply_is_audit_chained_with_the_delta_checksum(self):
        fake = _FakeAuditManager()
        orch = self.demo.orchestrator(audit=fake)
        result = orch.apply_update("demo", "1.0.0")
        ops = [op for op, _ in fake.entries]
        self.assertIn("package_update_applied", ops)
        entry = dict(fake.entries[ops.index("package_update_applied")][1])
        self.assertEqual(entry["delta_checksum"], self.demo.delta["checksum"])

    def test_restore_point_taken_before_apply_and_usable_for_rollback(self):
        orch = self.demo.orchestrator()
        result = orch.apply_update("demo", "1.0.0")
        rp = Path(result["restore_point"])
        self.assertTrue(rp.is_dir())
        self.assertEqual(
            (rp / "app.bin").read_bytes(), b"APP-v1")
        # The rollback restores the pre-apply content.
        rolled = orch.rollback("demo", "1.1.0")
        self.assertEqual(
            (self.demo.install_dir / "app.bin").read_bytes(),
            b"APP-v1")
        self.assertIn("operator-invoked", rolled["note"])

    def test_rollback_to_same_or_newer_version_refused(self):
        orch = self.demo.orchestrator()
        orch.apply_update("demo", "1.0.0")
        with self.assertRaises(UpdateOrchestrationError) as ctx:
            orch.rollback("demo", "1.1.0", target_version="1.1.0")
        self.assertIn("not older", str(ctx.exception))

    def test_rollback_without_restore_point_refused(self):
        orch = self.demo.orchestrator()
        with self.assertRaises(UpdateOrchestrationError) as ctx:
            orch.rollback("demo", "1.0.0")
        self.assertIn("no restore point", str(ctx.exception))

    def test_uninstalled_package_refused(self):
        orch = self.demo.orchestrator()
        with self.assertRaises(UpdateOrchestrationError):
            orch.apply_update("ghost", "1.0.0")

    def test_tampered_delta_refused_before_mutation(self):
        orch = self.demo.orchestrator()
        # Corrupt the repository's delta payload AFTER publishing.
        payload = self.demo.repo.payload_path({
            "type": "delta", "package_id": "demo",
            "version_from": "1.0.0", "version_to": "1.1.0"})
        payload.write_bytes(payload.read_bytes() + b"TAMPER")
        before = (self.demo.install_dir / "app.bin").read_bytes()
        with self.assertRaises(UpdateOrchestrationError):
            orch.apply_update("demo", "1.0.0")
        # The content checksum in the index no longer matches → the
        # candidate is excluded at RESOLVE; the install dir is
        # untouched either way (verify precedes mutation).
        self.assertEqual(
            (self.demo.install_dir / "app.bin").read_bytes(),
            before)

    def test_failed_restore_point_refuses_the_apply(self):
        orch = self.demo.orchestrator()
        orch.state_dir = Path(self._tmp.name) / "blocked-file"
        # A FILE where the state dir must be: restore points cannot be
        # created → the apply must refuse, leaving v1 intact.
        (Path(self._tmp.name) / "blocked-file").write_text("not a dir")
        with self.assertRaises(UpdateOrchestrationError) as ctx:
            orch.apply_update("demo", "1.0.0")
        self.assertIn("restore point failed", str(ctx.exception))
        self.assertEqual(
            (self.demo.install_dir / "app.bin").read_bytes(),
            b"APP-v1")


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.demo = _DemoRepo(Path(self._tmp.name))

    def test_resolve_finds_the_verified_candidate(self):
        orch = self.demo.orchestrator()
        cands = orch.resolve_updates({"demo": "1.0.0"})
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["version_to"], "1.1.0")

    def test_uninstalled_or_current_packages_yield_nothing(self):
        orch = self.demo.orchestrator()
        self.assertEqual(orch.resolve_updates({"other": "1.0.0"}), [])
        self.assertEqual(orch.resolve_updates({"demo": "1.1.0"}), [])

    def test_bad_trust_store_fails_closed(self):
        orch = UpdateOrchestrator(
            repo_root=str(self.demo.tmp / "repo"),
            trust_store_path=str(self.demo.tmp / "nope.json"),
            install_root=str(self.demo.install_root),
            state_dir=str(self.demo.tmp / "s2"))
        with self.assertRaises(repo_mod.RepoError):
            orch.resolve_updates({"demo": "1.0.0"})


class VersionOrderingTests(unittest.TestCase):
    def test_numeric_ordering_not_lexicographic(self):
        self.assertGreater(version_tuple("1.10.2"), version_tuple("1.9.9"))
        self.assertGreater(version_tuple("2.0"), version_tuple("1.99"))
        self.assertEqual(version_tuple("1.2.3"), version_tuple("1.2.3"))

    def test_garbage_segments_compare_as_zero(self):
        self.assertGreater(version_tuple("1.2"), version_tuple("1.x"))


class VerifyOnlyTests(unittest.TestCase):
    def test_verify_only_reports_verified_counts(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        demo = _DemoRepo(Path(self._tmp.name))
        out = uo.verify_only(str(demo.tmp / "repo"), str(demo.trust_store))
        self.assertTrue(out["verified"])
        self.assertEqual(out["packages"], 1)
        self.assertEqual(out["deltas"], 1)


class ReadHistoryTests(unittest.TestCase):
    """read_history is the status-only half of the orchestrator's
    history: readable WITHOUT the repo-bound constructor (the bare
    `packages status` form the demo banner teaches)."""

    def test_round_trip_and_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            hp = state / "history-apply.jsonl"
            hp.write_text(
                json.dumps({"package_id": "demo-app", "version_from": "1.0.0",
                            "version_to": "1.1.0"}) + "\n"
                + "not-json-at-all\n"
                + json.dumps({"package_id": "other", "version_from": "2.0.0",
                              "version_to": "2.1.0"}) + "\n",
                encoding="utf-8")
            out = uo.read_history(str(state), "apply")
            self.assertEqual(len(out), 2)
            self.assertEqual(out[0]["package_id"], "demo-app")
            self.assertEqual(out[1]["version_to"], "2.1.0")

    def test_missing_state_dir_reads_empty(self):
        self.assertEqual(
            uo.read_history("/nonexistent-nyrqis-state-xyz", "apply"), [])

    def test_orchestrator_history_delegates_to_read_history(self):
        # Same layout, one source of truth.
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "orch"
            state.mkdir()
            (state / "history-apply.jsonl").write_text(
                json.dumps({"package_id": "p", "version_from": "1",
                            "version_to": "2"}) + "\n", encoding="utf-8")
            orch = UpdateOrchestrator(
                repo_root=str(Path(td) / "repo"),
                trust_store_path=str(Path(td) / "trust.json"),
                install_root=str(Path(td) / "install"),
                state_dir=str(state))
            self.assertEqual(orch.history("apply")[0]["package_id"], "p")
            self.assertEqual(
                uo.read_history(str(state), "apply"),
                orch.history("apply"))


class PackagesStatusCliTests(unittest.TestCase):
    """The bare `packages status` form must WORK: the parser defaults
    --repo-root to "" and the demo banner teaches exactly that form, but
    the CLI used to construct the repo-bound orchestrator first and die
    on ValueError (in-VM ops drill finding, 2026-10-02)."""

    @classmethod
    def _ctl(cls, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(Path(_HERE) / "nyrqisctl.py"), *argv],
            capture_output=True, text=True, timeout=120)

    def test_bare_status_answers_without_a_repo(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._ctl("packages", "status",
                          "--install-root", str(Path(td) / "install"),
                          "--state-dir", str(Path(td) / "orch"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("installed packages under", r.stdout)
        self.assertIn("(none)", r.stdout)

    def test_status_lists_inventory_and_last_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = root / "install" / "demo-app"
            app.mkdir(parents=True)
            (app / "manifest.json").write_text(
                json.dumps({"package_id": "demo-app", "version": "1.1.0"}),
                encoding="utf-8")
            orch = root / "orch"
            orch.mkdir()
            (orch / "history-apply.jsonl").write_text(
                json.dumps({"package_id": "demo-app", "version_from": "1.0.0",
                            "version_to": "1.1.0"}) + "\n", encoding="utf-8")
            r = self._ctl("packages", "status",
                          "--install-root", str(root / "install"),
                          "--state-dir", str(orch))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("demo-app 1.1.0", r.stdout)
        self.assertIn("last apply: demo-app 1.0.0 → 1.1.0", r.stdout)


class NoEgressTests(unittest.TestCase):
    """The UPD-001 §7.7 pin: no direct egress — fetch stays behind the
    operator-configured registry client."""

    def test_module_imports_no_http_client(self):
        src = Path(uo.__file__).read_text()
        for forbidden in ("urllib", "requests", "http.client", "httpx",
                          "urlopen", "socket.socket"):
            self.assertNotIn(
                forbidden, src,
                f"update_orchestrate must not reference {forbidden!r}")

    def test_local_imports_are_only_the_shipped_primitives(self):
        src = Path(uo.__file__).read_text()
        local = [l.strip() for l in src.splitlines()
                 if l.strip().startswith("from .")]
        for line in local:
            self.assertRegex(
                line,
                r"^from \.(delta_update|package_repo|update_signing) import",
                line)


if __name__ == "__main__":
    unittest.main()
