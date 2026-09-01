"""Unit tests for backend.installer — package installer with signature verification.

Tests cover:
- Package installation with valid signatures
- Rejection of unsigned packages
- Rejection of untrusted keys
- Integrity tree verification
- Uninstall and re-install
- Package database operations
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.installer import (
    PackageManifest,
    IntegrityTree,
    PackageInstaller,
    PackageDatabase,
    InstallerError,
)
from backend.package_signing import (
    SigningKeypair,
    sign_package,
    PackageSignature,
    TrustStore,
    HAS_NACL,
)


def _create_test_package(tmpdir, kp, name="test-app", version="1.0.0", sign=True):
    """Helper to create a signed test package."""
    pkg_dir = Path(tmpdir) / f"{name}.nypkg"
    pkg_dir.mkdir()
    (pkg_dir / "images").mkdir()

    # Content file
    content = b"Hello, World!"
    (pkg_dir / "images" / "hello.txt").write_bytes(content)

    # Manifest
    manifest = PackageManifest(
        name=name,
        version=version,
        publisher="Test Publisher",
        capabilities=["CAP_FS_READ"],
    )
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), sort_keys=True, separators=(',', ':')))

    # Integrity tree
    content_hash = hashlib.sha256(content).digest()
    tree = IntegrityTree(
        root_hash=content_hash,
        file_hashes={"hello.txt": content_hash},
    )
    (pkg_dir / "integrity.json").write_text(json.dumps(tree.to_dict(), indent=2))

    # Signature
    if sign:
        sig_bytes = sign_package(manifest.to_bytes(), tree.to_bytes(), kp)
        sig_block = PackageSignature(
            public_key=kp.public_key,
            signature=sig_bytes,
            key_id=kp.key_id,
        )
        (pkg_dir / "signature.json").write_text(json.dumps(sig_block.to_dict(), indent=2))

    return pkg_dir


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestPackageManifest(unittest.TestCase):
    """Test PackageManifest serialization."""

    def test_roundtrip(self):
        manifest = PackageManifest(
            name="test",
            version="1.0.0",
            publisher="Pub",
            capabilities=["CAP_FS_READ"],
        )
        d = manifest.to_dict()
        restored = PackageManifest.from_dict(d)
        self.assertEqual(restored.name, "test")
        self.assertEqual(restored.capabilities, ["CAP_FS_READ"])

    def test_to_bytes_is_deterministic(self):
        manifest = PackageManifest(name="test", version="1.0.0", publisher="Pub")
        b1 = manifest.to_bytes()
        b2 = manifest.to_bytes()
        self.assertEqual(b1, b2)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestIntegrityTree(unittest.TestCase):
    """Test IntegrityTree."""

    def test_roundtrip(self):
        tree = IntegrityTree(
            root_hash=b"\x00" * 32,
            file_hashes={"a.txt": b"\x01" * 32},
        )
        d = tree.to_dict()
        restored = IntegrityTree.from_dict(d)
        self.assertEqual(restored.root_hash, tree.root_hash)
        self.assertIn("a.txt", restored.file_hashes)

    def test_verify_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test content")
            path = f.name
        try:
            expected = hashlib.sha256(b"test content").digest()
            tree = IntegrityTree(root_hash=expected)
            self.assertTrue(tree.verify_file(path, expected))
        finally:
            os.unlink(path)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestPackageInstaller(unittest.TestCase):
    """Test PackageInstaller install/uninstall/verify."""

    def setUp(self):
        self.kp = SigningKeypair.generate()
        self.tmpdir = tempfile.mkdtemp()
        self.install_dir = os.path.join(self.tmpdir, "installed")
        self.trust_path = os.path.join(self.tmpdir, "trust.json")

        # Set up trust store
        store = TrustStore()
        store.add_trusted(self.kp.public_key)
        store.save(self.trust_path)

        self.installer = PackageInstaller(
            install_dir=self.install_dir,
            trust_store_path=self.trust_path,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_signed_package(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        result = self.installer.install(str(pkg))
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["name"], "test-app")

    def test_reject_unsigned_package(self):
        pkg = _create_test_package(self.tmpdir, self.kp, sign=False)
        with self.assertRaises(InstallerError) as ctx:
            self.installer.install(str(pkg))
        self.assertIn("not signed", str(ctx.exception))

    def test_reject_untrusted_key(self):
        wrong_kp = SigningKeypair.generate()
        pkg = _create_test_package(self.tmpdir, wrong_kp)
        with self.assertRaises(InstallerError) as ctx:
            self.installer.install(str(pkg))
        self.assertIn("not trusted", str(ctx.exception))

    def test_already_installed_returns_early(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        self.installer.install(str(pkg))
        result = self.installer.install(str(pkg))
        self.assertEqual(result["status"], "already_installed")

    def test_list_installed(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        self.installer.install(str(pkg))
        installed = self.installer.list_installed()
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0]["name"], "test-app")

    def test_uninstall(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        self.installer.install(str(pkg))
        result = self.installer.uninstall("test-app")
        self.assertTrue(result)
        self.assertEqual(len(self.installer.list_installed()), 0)

    def test_uninstall_nonexistent(self):
        result = self.installer.uninstall("nonexistent")
        self.assertFalse(result)

    def test_verify_installed(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        self.installer.install(str(pkg))
        self.assertTrue(self.installer.verify_installed("test-app"))

    def test_verify_nonexistent(self):
        self.assertFalse(self.installer.verify_installed("nonexistent"))

    def test_tampered_content_fails_integrity(self):
        pkg = _create_test_package(self.tmpdir, self.kp)
        # Tamper with the content file
        content_path = pkg / "images" / "hello.txt"
        content_path.write_bytes(b"TAMPERED")
        # The signature is still valid for the original manifest+tree,
        # but the integrity tree check will fail
        with self.assertRaises(InstallerError) as ctx:
            self.installer.install(str(pkg))
        self.assertIn("Integrity check failed", str(ctx.exception))


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestPackageDatabase(unittest.TestCase):
    """Test PackageDatabase operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "packages.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_and_list(self):
        db = PackageDatabase(self.db_path)
        db.register("app1", "1.0.0", "/opt/app1", "key123")
        self.assertTrue(db.is_installed("app1"))
        self.assertEqual(len(db.list_installed()), 1)

    def test_unregister(self):
        db = PackageDatabase(self.db_path)
        db.register("app1", "1.0.0", "/opt/app1", "key123")
        result = db.unregister("app1")
        self.assertTrue(result)
        self.assertFalse(db.is_installed("app1"))

    def test_persistence(self):
        db = PackageDatabase(self.db_path)
        db.register("app1", "1.0.0", "/opt/app1", "key123")
        # Load from disk
        db2 = PackageDatabase(self.db_path)
        self.assertTrue(db2.is_installed("app1"))


if __name__ == "__main__":
    unittest.main()
