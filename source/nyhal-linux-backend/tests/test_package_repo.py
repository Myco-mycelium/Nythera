"""test_package_repo — Tests for signed package repository management.

Verifies ``backend/package_repo.py``:

- Publishing packages and deltas produces a signed index and on-disk
  payloads; republish replaces cleanly.
- Client-side ``load_index`` enforces the trust model fail-closed:
  unsigned indexes, untrusted keys, and tampered entries are refused.
- Entry content verification catches payload tampering.
- Delta lookup by (package, from, to) works through the verified index.

References:
    - backend/package_repo.py
    - backend/package_signing.py (trust model)
    - backend/delta_update.py (delta documents)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from backend.package_repo import (
    PackageRepository,
    RepoError,
    RepoSignError,
    INDEX_VERSION,
)

try:
    from backend.package_signing import HAS_NACL, SigningKeypair
except ImportError:  # pragma: no cover
    HAS_NACL = False


def _trust_store_for(kp: SigningKeypair, path: str) -> None:
    with open(path, "w") as f:
        json.dump({
            "trusted_keys": [{
                "key_id": kp.key_id,
                "public_key": base64.b64encode(kp.public_key).decode(),
                "name": "Repo Publisher",
            }]
        }, f)


def _make_pkg(root: str, name: str, files: dict) -> str:
    pkg = Path(root) / name
    pkg.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = pkg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return str(pkg)


@unittest.skipUnless(HAS_NACL, "PyNaCl required for signing tests")
class TestRepositoryPublish(unittest.TestCase):
    """Publishing packages/deltas into a signed repository."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-repo-")
        self.repo_root = os.path.join(self.tmp, "repo")
        self.kp = SigningKeypair.generate()
        self.repo = PackageRepository(self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_package_creates_entry_and_payload(self):
        pkg = _make_pkg(self.tmp, "pkg1", {"images/app.bin": b"hello"})
        entry = self.repo.publish_package(pkg, "myapp", "1.0.0", self.kp)

        self.assertEqual(entry["package_id"], "myapp")
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(entry["type"], "package")
        self.assertTrue(entry["checksum"])
        self.assertTrue(
            Path(self.repo_root, "packages", "myapp", "1.0.0").is_dir()
        )
        doc = json.loads(
            Path(self.repo_root, "index.json").read_text()
        )
        self.assertEqual(doc["version"], INDEX_VERSION)
        self.assertIn("signature", doc)

    def test_republish_replaces_entry_and_payload(self):
        pkg_v1 = _make_pkg(self.tmp, "p1", {"f": b"one"})
        self.repo.publish_package(pkg_v1, "app", "1.0.0", self.kp)
        entry2 = self.repo.publish_package(pkg_v1, "app", "1.0.0", self.kp)
        doc = json.loads(
            Path(self.repo_root, "index.json").read_text()
        )
        entries = [
            e for e in doc["packages"]
            if e["package_id"] == "app" and e["version"] == "1.0.0"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["checksum"], entry2["checksum"])

    def test_publish_two_versions(self):
        pkg1 = _make_pkg(self.tmp, "v1", {"f": b"one"})
        pkg2 = _make_pkg(self.tmp, "v2", {"f": b"two"})
        self.repo.publish_package(pkg1, "app", "1.0.0", self.kp)
        self.repo.publish_package(pkg2, "app", "1.1.0", self.kp)
        doc = json.loads(
            Path(self.repo_root, "index.json").read_text()
        )
        self.assertEqual(len(doc["packages"]), 2)

    def test_publish_missing_package_dir_raises(self):
        with self.assertRaises(RepoError):
            self.repo.publish_package(
                os.path.join(self.tmp, "nope"), "x", "1", self.kp
            )

    def test_publish_without_private_key_raises(self):
        verify_only = SigningKeypair.from_public_key(self.kp.public_key)
        pkg = _make_pkg(self.tmp, "np", {"f": b"x"})
        with self.assertRaises(RepoSignError):
            self.repo.publish_package(pkg, "x", "1", verify_only)

    def test_remove_package_drops_entry_and_payload(self):
        pkg = _make_pkg(self.tmp, "rm", {"f": b"bye"})
        self.repo.publish_package(pkg, "old", "1.0.0", self.kp)
        self.assertTrue(
            self.repo.remove_package("old", "1.0.0", self.kp)
        )
        self.assertFalse(
            Path(self.repo_root, "packages", "old", "1.0.0").exists()
        )
        doc = json.loads(
            Path(self.repo_root, "index.json").read_text()
        )
        self.assertEqual(
            [e for e in doc["packages"] if e["package_id"] == "old"], []
        )


@unittest.skipUnless(HAS_NACL, "PyNaCl required for verification tests")
class TestRepositoryVerify(unittest.TestCase):
    """Client-side index verification (fail-closed trust model)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-repo-ver-")
        self.repo_root = os.path.join(self.tmp, "repo")
        self.kp = SigningKeypair.generate()
        self.trust = os.path.join(self.tmp, "trust.json")
        _trust_store_for(self.kp, self.trust)
        self.repo = PackageRepository(self.repo_root)

        pkg = _make_pkg(self.tmp, "vp", {"images/a.bin": b"data"})
        self.repo.publish_package(pkg, "app", "1.0.0", self.kp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verified_index_loads(self):
        doc = self.repo.load_index(self.trust)
        self.assertEqual(len(doc["packages"]), 1)
        self.assertEqual(doc["packages"][0]["package_id"], "app")

    def test_unsigned_index_refused(self):
        path = Path(self.repo_root, "index.json")
        doc = json.loads(path.read_text())
        doc.pop("signature")
        path.write_text(json.dumps(doc))
        with self.assertRaises(RepoError):
            self.repo.load_index(self.trust)

    def test_tampered_entry_refused(self):
        """A modified entry breaks the signature — the WHOLE index is
        refused (no partial trust)."""
        path = Path(self.repo_root, "index.json")
        doc = json.loads(path.read_text())
        doc["packages"][0]["checksum"] = "0" * 64
        path.write_text(json.dumps(doc))
        with self.assertRaises(RepoError):
            self.repo.load_index(self.trust)

    def test_untrusted_key_refused(self):
        other = SigningKeypair.generate()
        other_trust = os.path.join(self.tmp, "other-trust.json")
        _trust_store_for(other, other_trust)
        with self.assertRaises(RepoError):
            self.repo.load_index(other_trust)

    def test_missing_trust_store_refused(self):
        with self.assertRaises(RepoError):
            self.repo.load_index(os.path.join(self.tmp, "missing.json"))

    def test_find_entry(self):
        entry = self.repo.find("app", "1.0.0", self.trust)
        self.assertIsNotNone(entry)
        self.assertIsNone(self.repo.find("app", "9.9.9", self.trust))

    def test_verify_entry_content_detects_tampering(self):
        entry = self.repo.find("app", "1.0.0", self.trust)
        self.assertTrue(self.repo.verify_entry_content(entry))
        # Tamper with the published payload after indexing.
        payload = Path(self.repo.payload_path(entry), "images", "a.bin")
        payload.write_bytes(b"evil")
        self.assertFalse(self.repo.verify_entry_content(entry))


@unittest.skipUnless(HAS_NACL, "PyNaCl required for delta tests")
class TestRepositoryDeltas(unittest.TestCase):
    """Delta updates flow through the repo end-to-end."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-repo-delta-")
        self.kp = SigningKeypair.generate()
        self.trust = os.path.join(self.tmp, "trust.json")
        _trust_store_for(self.kp, self.trust)
        self.repo = PackageRepository(os.path.join(self.tmp, "repo"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _publish_pair(self):
        from backend.delta_update import (
            create_delta_update, delta_payload_bytes,
        )

        old = _make_pkg(self.tmp, "d_old", {"data.bin": b"v1"})
        new = _make_pkg(self.tmp, "d_new", {"data.bin": b"v2"})
        self.repo.publish_package(old, "app", "1.0.0", self.kp)
        self.repo.publish_package(new, "app", "1.1.0", self.kp)
        delta = create_delta_update(
            old, new, "app", "1.0.0", "1.1.0", self.kp
        )
        payload = os.path.join(self.tmp, "app.delta")
        with open(payload, "wb") as f:
            f.write(delta_payload_bytes(delta))
        entry = self.repo.publish_delta(delta, payload, self.kp)
        return delta, payload, entry

    def test_delta_publish_and_lookup(self):
        delta, payload, entry = self._publish_pair()
        self.assertEqual(entry["type"], "delta")
        found = self.repo.find_delta("app", "1.0.0", "1.1.0", self.trust)
        self.assertIsNotNone(found)
        self.assertEqual(found["checksum"], entry["checksum"])
        self.assertIsNone(
            self.repo.find_delta("app", "1.1.0", "1.0.0", self.trust)
        )

    def test_delta_payload_survives_content_check(self):
        delta, payload, entry = self._publish_pair()
        self.assertTrue(self.repo.verify_entry_content(entry))

    def test_published_delta_applies_to_published_base(self):
        """End-to-end: fetch base + delta from the repo and apply."""
        from backend.delta_update import (
            apply_delta_update, load_delta_update,
        )

        delta, payload, entry = self._publish_pair()

        # "Client" fetches the 1.0.0 payload and the delta.
        base_dir = os.path.join(self.tmp, "client")
        shutil.copytree(
            self.repo.payload_path(
                self.repo.find("app", "1.0.0", self.trust)
            ),
            base_dir,
        )
        delta_doc = load_delta_update(
            self.repo.payload_path(entry).with_suffix(".json")
            if Path(self.repo.payload_path(entry)).with_suffix(".json").exists()
            else self._write_delta_doc(delta)
        )
        touched = apply_delta_update(
            delta_doc, base_dir, verify_trust_store=self.trust
        )
        self.assertIn("data.bin", touched)
        with open(os.path.join(base_dir, "data.bin"), "rb") as f:
            self.assertEqual(f.read(), b"v2")

    def _write_delta_doc(self, delta: dict) -> str:
        path = os.path.join(self.tmp, "delta.json")
        with open(path, "w") as f:
            json.dump(delta, f)
        return path


if __name__ == "__main__":
    unittest.main()
