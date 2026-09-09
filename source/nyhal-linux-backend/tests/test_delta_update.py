"""test_delta_update — Tests for delta update generation and application.

Verifies ``backend/delta_update.py`` against the shipped verification
half (``backend/update_signing.py``): a delta generated here must pass
``UpdateVerifier.verify_delta_update`` unmodified.

References:
    - NPS-026 §6: Digital Signatures
    - backend/delta_update.py
    - backend/update_signing.py
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

from backend.delta_update import (
    DeltaSignError,
    DeltaUpdateError,
    apply_delta_update,
    create_delta_update,
    delta_payload_bytes,
    diff_packages,
    load_delta_update,
    save_delta_update,
)

try:
    from backend.package_signing import HAS_NACL, SigningKeypair
except ImportError:  # pragma: no cover
    HAS_NACL = False


def _make_pkg(root: str, name: str, files: dict) -> str:
    """Create a package directory in the .nypkg layout."""
    pkg = Path(root) / name
    (pkg / "images").mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = pkg / "images" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return str(pkg)


@unittest.skipUnless(HAS_NACL, "PyNaCl required for signing tests")
class TestDiffPackages(unittest.TestCase):
    """Payload diffing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-delta-diff-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_add_modify_remove(self):
        old = _make_pkg(self.tmp, "old", {
            "keep.bin": b"same",
            "changed.bin": b"old content",
            "gone.bin": b"delete me",
        })
        new = _make_pkg(self.tmp, "new", {
            "keep.bin": b"same",
            "changed.bin": b"new content",
            "fresh.bin": b"added",
        })
        ops = diff_packages(old, new)
        by_op = {op["path"]: op["op"] for op in ops}
        self.assertEqual(by_op.get("gone.bin"), "remove")
        self.assertEqual(by_op.get("changed.bin"), "modify")
        self.assertEqual(by_op.get("fresh.bin"), "add")
        self.assertNotIn("keep.bin", by_op)

    def test_identical_payloads_yield_no_ops(self):
        files = {"a.bin": b"1", "sub/b.bin": b"2"}
        old = _make_pkg(self.tmp, "o", files)
        new = _make_pkg(self.tmp, "n", files)
        self.assertEqual(diff_packages(old, new), [])

    def test_flat_layout_normalized(self):
        """Directories without the images/ prefix diff the same way."""
        old = Path(self.tmp) / "o2"
        new = Path(self.tmp) / "n2"
        old.mkdir()
        new.mkdir()
        (old / "x.bin").write_bytes(b"1")
        (new / "x.bin").write_bytes(b"2")
        ops = diff_packages(str(old), str(new))
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "modify")
        self.assertEqual(ops[0]["path"], "x.bin")

    def test_missing_directory_raises(self):
        with self.assertRaises(DeltaUpdateError):
            diff_packages(
                os.path.join(self.tmp, "nope"), os.path.join(self.tmp, "nope2")
            )

    def test_deterministic_op_order(self):
        old = _make_pkg(self.tmp, "o3", {"b.bin": b"1", "a.bin": b"1"})
        new = _make_pkg(self.tmp, "n3", {"b.bin": b"2", "a.bin": b"2"})
        ops = diff_packages(old, new)
        self.assertEqual([op["path"] for op in ops], ["a.bin", "b.bin"])


@unittest.skipUnless(HAS_NACL, "PyNaCl required for signing tests")
class TestCreateDeltaUpdate(unittest.TestCase):
    """Delta document generation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-delta-gen-")
        self.old = _make_pkg(self.tmp, "old", {"data.bin": b"v1"})
        self.new = _make_pkg(self.tmp, "new", {"data.bin": b"v2"})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsigned_document_shape(self):
        delta = create_delta_update(
            self.old, self.new, "test-app", "1.0.0", "1.1.0"
        )
        self.assertEqual(delta["package_id"], "test-app")
        self.assertEqual(delta["version_from"], "1.0.0")
        self.assertEqual(delta["version_to"], "1.1.0")
        self.assertEqual(delta["update_type"], "delta")
        self.assertNotIn("signature", delta)
        # checksum covers the canonical op list
        expected = hashlib.sha256(
            json.dumps(delta["ops"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(delta["checksum"], expected)

    def test_signed_document_carries_signature(self):
        kp = SigningKeypair.generate()
        delta = create_delta_update(
            self.old, self.new, "test-app", "1.0.0", "1.1.0", kp
        )
        self.assertIn("signature", delta)
        self.assertEqual(delta["key_id"], kp.key_id)
        self.assertEqual(len(base64.b64decode(delta["signature"])), 64)

    def test_verify_only_keypair_rejected(self):
        kp = SigningKeypair.generate()
        verify_only = SigningKeypair.from_public_key(kp.public_key)
        with self.assertRaises(DeltaSignError):
            create_delta_update(
                self.old, self.new, "test-app", "1.0.0", "1.1.0", verify_only
            )

    def test_save_and_load_roundtrip(self):
        delta = create_delta_update(
            self.old, self.new, "test-app", "1.0.0", "1.1.0"
        )
        path = os.path.join(self.tmp, "delta.json")
        save_delta_update(delta, path)
        loaded = load_delta_update(path)
        self.assertEqual(loaded["checksum"], delta["checksum"])
        self.assertEqual(loaded["ops"], delta["ops"])

    def test_load_garbage_raises(self):
        path = os.path.join(self.tmp, "bad.json")
        Path(path).write_text('{"not": "a delta"}')
        with self.assertRaises(DeltaUpdateError):
            load_delta_update(path)


@unittest.skipUnless(HAS_NACL, "PyNaCl required for verification tests")
class TestApplyDeltaUpdate(unittest.TestCase):
    """Delta application with fail-closed signature checks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-delta-apply-")
        self.kp = SigningKeypair.generate()
        # Trust store trusting our test key
        self.trust_store = os.path.join(self.tmp, "trust.json")
        with open(self.trust_store, "w") as f:
            json.dump({
                "trusted_keys": [{
                    "key_id": self.kp.key_id,
                    "public_key": base64.b64encode(self.kp.public_key).decode(),
                    "name": "Test Publisher",
                }]
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_installed(self, files: dict) -> str:
        pkg = Path(self.tmp) / "installed"
        pkg.mkdir(exist_ok=True)
        for rel, data in files.items():
            p = pkg / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return str(pkg)

    def _signed_delta(self, files_from: dict, files_to: dict) -> dict:
        old = _make_pkg(self.tmp, "d_old", files_from)
        new = _make_pkg(self.tmp, "d_new", files_to)
        return create_delta_update(
            old, new, "test-app", "1.0.0", "1.1.0", self.kp
        )

    def test_add_modify_remove_applied(self):
        installed = self._make_installed({
            "keep.bin": b"same",
            "changed.bin": b"old",
            "gone.bin": b"bye",
        })
        delta = self._signed_delta(
            {"keep.bin": b"same", "changed.bin": b"old", "gone.bin": b"bye"},
            {"keep.bin": b"same", "changed.bin": b"new", "fresh.bin": b"hi"},
        )
        touched = apply_delta_update(
            delta, installed, verify_trust_store=self.trust_store
        )
        root = Path(installed)
        self.assertEqual((root / "changed.bin").read_bytes(), b"new")
        self.assertEqual((root / "fresh.bin").read_bytes(), b"hi")
        self.assertFalse((root / "gone.bin").exists())
        self.assertEqual((root / "keep.bin").read_bytes(), b"same")
        self.assertIn("changed.bin", touched)

    def test_tampered_ops_rejected(self):
        """A modified op list must fail the signature check."""
        installed = self._make_installed({"data.bin": b"v1"})
        delta = self._signed_delta({"data.bin": b"v1"}, {"data.bin": b"v2"})
        delta["ops"][0]["data_b64"] = base64.b64encode(b"evil").decode()
        with self.assertRaises(DeltaUpdateError):
            apply_delta_update(
                delta, installed, verify_trust_store=self.trust_store
            )
        # The install directory is untouched.
        self.assertEqual(
            (Path(installed) / "data.bin").read_bytes(), b"v1"
        )

    def test_unsigned_delta_rejected_when_store_given(self):
        installed = self._make_installed({"data.bin": b"v1"})
        delta = create_delta_update(
            _make_pkg(self.tmp, "u_o", {"data.bin": b"v1"}),
            _make_pkg(self.tmp, "u_n", {"data.bin": b"v2"}),
            "test-app", "1.0.0", "1.1.0",
        )
        with self.assertRaises(DeltaUpdateError):
            apply_delta_update(
                delta, installed, verify_trust_store=self.trust_store
            )

    def test_unknown_key_rejected(self):
        installed = self._make_installed({"data.bin": b"v1"})
        delta = self._signed_delta({"data.bin": b"v1"}, {"data.bin": b"v2"})
        delta["key_id"] = "00000000"
        with self.assertRaises(DeltaUpdateError):
            apply_delta_update(
                delta, installed, verify_trust_store=self.trust_store
            )

    def test_path_traversal_rejected(self):
        installed = self._make_installed({"data.bin": b"v1"})
        delta = self._signed_delta({"data.bin": b"v1"}, {"data.bin": b"v2"})
        delta["ops"].append({
            "op": "add",
            "path": "../escape.bin",
            "data_b64": base64.b64encode(b"evil").decode(),
        })
        with self.assertRaises(DeltaUpdateError):
            apply_delta_update(delta, installed)

    def test_invalid_op_rejected(self):
        installed = self._make_installed({"data.bin": b"v1"})
        delta = self._signed_delta({"data.bin": b"v1"}, {"data.bin": b"v2"})
        delta["ops"].append({"op": "chmod", "path": "x"})
        with self.assertRaises(DeltaUpdateError):
            apply_delta_update(delta, installed)


@unittest.skipUnless(HAS_NACL, "PyNaCl required for cross-module tests")
class TestDeltaPassesShippedVerifier(unittest.TestCase):
    """The generation half must satisfy the verification half."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-delta-verifier-")
        self.kp = SigningKeypair.generate()
        self.trust_store = os.path.join(self.tmp, "trust.json")
        with open(self.trust_store, "w") as f:
            json.dump({
                "trusted_keys": [{
                    "key_id": self.kp.key_id,
                    "public_key": base64.b64encode(self.kp.public_key).decode(),
                    "name": "Test Publisher",
                }]
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generated_delta_verifies_via_update_signing(self):
        from backend.update_signing import UpdateManifest, UpdateType, UpdateVerifier

        old = _make_pkg(self.tmp, "v_o", {"data.bin": b"v1"})
        new = _make_pkg(self.tmp, "v_n", {"data.bin": b"v2"})
        delta = create_delta_update(
            old, new, "test-app", "1.0.0", "1.1.0", self.kp
        )
        # Ship the delta document (signed) alongside its payload file;
        # the verifier checks the payload FILE checksum.
        doc_path = save_delta_update(
            delta, os.path.join(self.tmp, "delta.json")
        )
        payload_path = os.path.join(self.tmp, "delta.payload")
        with open(payload_path, "wb") as f:
            f.write(delta_payload_bytes(delta))

        manifest = UpdateManifest(
            package_id=delta["package_id"],
            version_from=delta["version_from"],
            version_to=delta["version_to"],
            update_type=UpdateType.DELTA,
            delta_patches=delta["ops"],
            checksum=delta["checksum"],
            signature=base64.b64decode(delta["signature"]),
            key_id=delta["key_id"],
        )
        base = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.0.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="0" * 64,
        )
        verifier = UpdateVerifier(trust_store_path=self.trust_store)
        result = verifier.verify_delta_update(base, manifest, payload_path)
        self.assertEqual(result.status.value, "valid")

    def test_forged_delta_fails_shipped_verifier(self):
        from backend.update_signing import UpdateManifest, UpdateType, UpdateVerifier

        old = _make_pkg(self.tmp, "f_o", {"data.bin": b"v1"})
        new = _make_pkg(self.tmp, "f_n", {"data.bin": b"v2"})
        delta = create_delta_update(
            old, new, "test-app", "1.0.0", "1.1.0", self.kp
        )
        # Tamper with the ops after signing.
        delta["ops"][0]["data_b64"] = base64.b64encode(b"evil").decode()

        manifest = UpdateManifest(
            package_id=delta["package_id"],
            version_from=delta["version_from"],
            version_to=delta["version_to"],
            update_type=UpdateType.DELTA,
            delta_patches=delta["ops"],
            checksum=delta["checksum"],
            signature=base64.b64decode(delta["signature"]),
            key_id=delta["key_id"],
        )
        base = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.0.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="0" * 64,
        )
        verifier = UpdateVerifier(trust_store_path=self.trust_store)
        result = verifier.verify_delta_update(
            base, manifest,
            os.path.join(self.tmp, "missing.bin"),
        )
        self.assertIn(
            result.status.value, ("tampered", "invalid", "unknown_key")
        )


if __name__ == "__main__":
    unittest.main()
