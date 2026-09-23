"""Tests pinning the package manager's real-store wiring.

The UI PackageManager loads its catalogue from a SIGNED, VERIFIED
repository index (backend/package_repo.PackageRepository.load_index —
fail-closed per NPS-026 §6/§8) when given repo_root + trust_store_path;
install/update operations verify their source entry/delta before
reporting success, and a tampered payload produces a FAILED operation,
never a simulated success. Without a configured repo the manager falls
back to the marked sample data (dev/UI fixture, never a store).

Closes the roadmap's M14 Phase 3 package-manager wiring gap: the CLI/UI
layer now drives the real store path, with the simulated catalogue
demoted to a documented fallback.
"""

import base64
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from backend.package_signing import SigningKeypair  # noqa: E402
from backend.package_repo import PackageRepository  # noqa: E402
from ui.package_manager import PackageManager, PackageStatus  # noqa: E402


def _trust_store(kp: SigningKeypair, path: Path) -> None:
    with open(path, "w") as f:
        json.dump({"trusted_keys": [{
            "key_id": kp.key_id,
            "public_key": base64.b64encode(kp.public_key).decode(),
            "name": "Repo Publisher",
        }]}, f)


def _make_repo(tmp: Path):
    """A real signed repo: two packages + one delta, plus its trust store."""
    repo_root = tmp / "repo"
    repo = PackageRepository(str(repo_root))
    kp = SigningKeypair.generate()
    for pid, ver in (("app-a", "1.0.0"), ("app-b", "2.0.0")):
        d = tmp / "stage" / pid / ver
        (d / "bin").mkdir(parents=True)
        (d / "bin" / "app").write_text(f"payload {pid} {ver}")
        (d / "manifest.json").write_text(
            json.dumps({"id": pid, "version": ver}))
        repo.publish_package(str(d), pid, ver, kp)
    delta_doc = {"package_id": "app-a", "version_from": "1.0.0",
                 "version_to": "1.1.0"}
    delta_payload = tmp / "app-a.delta"
    delta_payload.write_text("delta-bytes")
    repo.publish_delta(delta_doc, str(delta_payload), kp)
    trust = tmp / "trust.json"
    _trust_store(kp, trust)
    return str(repo_root), str(trust)


class TestPackageManagerStoreWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pkmgr-store-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_loads_catalogue_from_verified_index(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        self.assertEqual(pm._source, "signed-repo")
        ids = {p.id for p in pm.packages}
        self.assertIn("app-a", ids)
        self.assertIn("app-b", ids)

    def test_delta_drives_updatable_status(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        a = pm.get_package("app-a")
        self.assertEqual(a.status, PackageStatus.UPDATABLE)
        self.assertEqual(a.latest_version, "1.1.0")

    def test_install_against_verified_entry_completes(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        op = pm.install_package("app-b")
        self.assertIsNotNone(op)
        self.assertEqual(op.status, "completed")
        self.assertTrue(any("signed-repo" in line for line in op.log))
        self.assertEqual(pm.get_package("app-b").status,
                         PackageStatus.INSTALLED)

    def test_update_against_verified_delta_completes(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        op = pm.update_package("app-a")
        self.assertIsNotNone(op)
        self.assertEqual(op.status, "completed")
        self.assertEqual(pm.get_package("app-a").version, "1.1.0")

    def test_tampered_payload_fails_closed(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        payload = (self.tmp / "repo" / "packages" / "app-b" / "2.0.0"
                   / "bin" / "app")
        payload.write_text("TAMPERED")
        op = pm.install_package("app-b")
        self.assertIsNotNone(op)
        self.assertEqual(op.status, "failed")
        self.assertEqual(pm.get_package("app-b").status,
                         PackageStatus.AVAILABLE)

    def test_missing_delta_fails_closed(self):
        repo_root, trust = _make_repo(self.tmp)
        pm = PackageManager(repo_root=repo_root, trust_store_path=trust)
        delta_file = (self.tmp / "repo" / "deltas" / "app-a"
                      / "1.0.0_1.1.0.delta")
        self.assertTrue(delta_file.exists())
        delta_file.write_text("TAMPERED-DELTA")
        op = pm.update_package("app-a")
        self.assertEqual(op.status, "failed")
        self.assertEqual(pm.get_package("app-a").version, "1.0.0")

    def test_unsigned_repo_falls_back_to_marked_sample_data(self):
        repo_root, trust = _make_repo(self.tmp)
        unsigned = self.tmp / "unsigned"
        PackageRepository(str(unsigned))._ensure_layout()
        (unsigned / "index.json").write_text(
            json.dumps({"version": 1, "packages": [], "deltas": []}))
        pm = PackageManager(repo_root=str(unsigned), trust_store_path=trust)
        self.assertEqual(pm._source, "sample")
        self.assertGreater(len(pm.packages), 0)

    def test_default_constructor_is_marked_sample(self):
        pm = PackageManager()
        self.assertEqual(pm._source, "sample")
        self.assertGreater(len(pm.packages), 0)


if __name__ == "__main__":
    unittest.main()
