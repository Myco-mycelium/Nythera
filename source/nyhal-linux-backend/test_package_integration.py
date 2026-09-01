"""Integration tests for the full package lifecycle.

Tests cover:
- End-to-end: create → sign → install → verify → uninstall
- Multi-package scenarios
- Tamper detection
- Trust store management
- Error handling edge cases
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
    InstallerError,
)
from backend.package_signing import (
    SigningKeypair,
    sign_package,
    PackageSignature,
    TrustStore,
    HAS_NACL,
)


def _create_package(tmpdir, kp, name, version, content=b"content"):
    """Helper to create a complete signed package."""
    pkg_dir = Path(tmpdir) / f"{name}-{version}.nypkg"
    pkg_dir.mkdir()
    (pkg_dir / "images").mkdir()

    # Content
    (pkg_dir / "images" / "data.bin").write_bytes(content)

    # Manifest
    manifest = PackageManifest(
        name=name,
        version=version,
        publisher="Test Publisher",
        capabilities=["CAP_FS_READ"],
    )
    (pkg_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), sort_keys=True, separators=(",", ":"))
    )

    # Integrity tree
    content_hash = hashlib.sha256(content).digest()
    tree = IntegrityTree(
        root_hash=content_hash,
        file_hashes={"data.bin": content_hash},
    )
    (pkg_dir / "integrity.json").write_text(json.dumps(tree.to_dict(), indent=2))

    # Signature
    sig_bytes = sign_package(manifest.to_bytes(), tree.to_bytes(), kp)
    sig_block = PackageSignature(
        public_key=kp.public_key,
        signature=sig_bytes,
        key_id=kp.key_id,
    )
    (pkg_dir / "signature.json").write_text(json.dumps(sig_block.to_dict(), indent=2))

    return pkg_dir


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestPackageLifecycle(unittest.TestCase):
    """End-to-end package lifecycle tests."""

    def setUp(self):
        self.kp = SigningKeypair.generate()
        self.tmpdir = tempfile.mkdtemp()
        self.install_dir = os.path.join(self.tmpdir, "installed")
        self.trust_path = os.path.join(self.tmpdir, "trust.json")

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

    def test_full_lifecycle(self):
        """Create → sign → install → verify → uninstall."""
        # Create
        pkg = _create_package(self.tmpdir, self.kp, "myapp", "1.0.0")
        self.assertTrue(pkg.exists())

        # Install
        result = self.installer.install(str(pkg))
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["name"], "myapp")

        # Verify
        self.assertTrue(self.installer.verify_installed("myapp"))

        # List
        installed = self.installer.list_installed()
        self.assertEqual(len(installed), 1)

        # Uninstall
        self.assertTrue(self.installer.uninstall("myapp"))
        self.assertEqual(len(self.installer.list_installed()), 0)

    def test_multiple_packages(self):
        """Install multiple packages."""
        pkg1 = _create_package(self.tmpdir, self.kp, "app1", "1.0.0")
        pkg2 = _create_package(self.tmpdir, self.kp, "app2", "2.0.0")

        self.installer.install(str(pkg1))
        self.installer.install(str(pkg2))

        installed = self.installer.list_installed()
        names = {p["name"] for p in installed}
        self.assertEqual(names, {"app1", "app2"})

    def test_upgrade_package(self):
        """Upgrade a package to a newer version."""
        pkg_v1 = _create_package(self.tmpdir, self.kp, "myapp", "1.0.0", b"v1")
        pkg_v2 = _create_package(self.tmpdir, self.kp, "myapp", "2.0.0", b"v2")

        self.installer.install(str(pkg_v1))
        result = self.installer.install(str(pkg_v2))
        self.assertEqual(result["version"], "2.0.0")

    def test_tamper_detection(self):
        """Detect tampered content after installation."""
        pkg = _create_package(self.tmpdir, self.kp, "myapp", "1.0.0")
        self.installer.install(str(pkg))

        # Tamper with installed content
        data_path = Path(self.install_dir) / "myapp" / "images" / "data.bin"
        data_path.write_bytes(b"TAMPERED")

        self.assertFalse(self.installer.verify_installed("myapp"))

    def test_multiple_signers(self):
        """Packages from different publishers."""
        kp1 = SigningKeypair.generate()
        kp2 = SigningKeypair.generate()

        store = TrustStore()
        store.add_trusted(kp1.public_key)
        store.add_trusted(kp2.public_key)
        store.save(self.trust_path)

        pkg1 = _create_package(self.tmpdir, kp1, "app1", "1.0.0")
        pkg2 = _create_package(self.tmpdir, kp2, "app2", "1.0.0")

        self.installer.install(str(pkg1))
        self.installer.install(str(pkg2))

        installed = self.installer.list_installed()
        self.assertEqual(len(installed), 2)

    def test_uninstall_nonexistent(self):
        """Uninstalling nonexistent package returns False."""
        self.assertFalse(self.installer.uninstall("nonexistent"))


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestTrustStoreIntegration(unittest.TestCase):
    """Trust store management tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.trust_path = os.path.join(self.tmpdir, "trust.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_remove_trusted_keys(self):
        """Add and remove trusted keys."""
        kp = SigningKeypair.generate()
        store = TrustStore()
        store.add_trusted(kp.public_key)
        store.save(self.trust_path)

        loaded = TrustStore.load(self.trust_path)
        self.assertTrue(loaded.is_trusted(kp.public_key))

        loaded.remove_trusted(kp.key_id)
        loaded.save(self.trust_path)

        reloaded = TrustStore.load(self.trust_path)
        self.assertFalse(reloaded.is_trusted(kp.public_key))

    def test_reject_untrusted_after_removal(self):
        """Reject package after key is removed from trust store."""
        kp = SigningKeypair.generate()

        # Install with trusted key
        store = TrustStore()
        store.add_trusted(kp.public_key)
        store.save(self.trust_path)

        installer = PackageInstaller(
            install_dir=os.path.join(self.tmpdir, "installed"),
            trust_store_path=self.trust_path,
        )
        pkg = _create_package(self.tmpdir, kp, "myapp", "1.0.0")
        installer.install(str(pkg))

        # Remove key from trust store
        store.remove_trusted(kp.key_id)
        store.save(self.trust_path)

        # Try to install again — should fail
        installer2 = PackageInstaller(
            install_dir=os.path.join(self.tmpdir, "installed2"),
            trust_store_path=self.trust_path,
        )
        with self.assertRaises(InstallerError):
            installer2.install(str(pkg))


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestErrorHandling(unittest.TestCase):
    """Error handling edge cases."""

    def setUp(self):
        self.kp = SigningKeypair.generate()
        self.tmpdir = tempfile.mkdtemp()
        self.install_dir = os.path.join(self.tmpdir, "installed")

        store = TrustStore()
        store.add_trusted(self.kp.public_key)
        self.trust_path = os.path.join(self.tmpdir, "trust.json")
        store.save(self.trust_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_manifest(self):
        """Package without manifest fails."""
        pkg_dir = Path(self.tmpdir) / "bad.nypkg"
        pkg_dir.mkdir()
        installer = PackageInstaller(
            install_dir=self.install_dir,
            trust_store_path=self.trust_path,
        )
        with self.assertRaises(InstallerError) as ctx:
            installer.install(str(pkg_dir))
        self.assertIn("manifest", str(ctx.exception))

    def test_missing_signature(self):
        """Package without signature fails."""
        pkg = _create_package(self.tmpdir, self.kp, "myapp", "1.0.0")
        (pkg / "signature.json").unlink()
        installer = PackageInstaller(
            install_dir=self.install_dir,
            trust_store_path=self.trust_path,
        )
        with self.assertRaises(InstallerError) as ctx:
            installer.install(str(pkg))
        self.assertIn("not signed", str(ctx.exception))

    def test_nonexistent_path(self):
        """Installing from nonexistent path fails."""
        installer = PackageInstaller(
            install_dir=self.install_dir,
            trust_store_path=self.trust_path,
        )
        with self.assertRaises(InstallerError):
            installer.install("/nonexistent/path")


if __name__ == "__main__":
    unittest.main()
