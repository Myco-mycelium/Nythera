"""test_package_signing — Tests for Ed25519 package signing.

References:
    - NPS-026: Package signing (§6)
    - backend/package_signing.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestPackageSigner(unittest.TestCase):
    """Tests for the package signer."""

    def setUp(self):
        """Create a temp trust store."""
        self._tmpdir = tempfile.mkdtemp(prefix="nyrqis-signing-test-")
        self._trust_store = os.path.join(self._tmpdir, "trust-store.json")
    
    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
    
    def test_generate_key(self):
        """Can generate a key pair."""
        from backend.package_signing import PackageSigner
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Test Key")
        self.assertIsNotNone(key)
        self.assertIsNotNone(key.key_id)
        self.assertEqual(key.name, "Test Key")
        self.assertGreater(len(key.public_key), 0)
    
    def test_sign_and_verify(self):
        """Can sign and verify a payload."""
        from backend.package_signing import PackageSigner, SignatureStatus
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Release Key")
        payload = b"Hello, Nyrqis!"
        
        signature = signer.sign_payload(payload, key)
        self.assertGreater(len(signature), 0)
        
        result = signer.verify_signature(payload, signature, key.public_key)
        self.assertTrue(result)
    
    def test_verify_invalid_signature(self):
        """Invalid signature fails verification."""
        from backend.package_signing import PackageSigner
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Test Key")
        payload = b"Hello, Nyrqis!"
        wrong_payload = b"Goodbye, Nyrqis!"
        
        signature = signer.sign_payload(payload, key)
        result = signer.verify_signature(wrong_payload, signature, key.public_key)
        self.assertFalse(result)
    
    def test_sign_package(self):
        """Can sign a package."""
        from backend.package_signing import PackageSigner
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Package Signer")
        payload = b"package content"
        
        signed = signer.sign_package("myapp", "1.0.0", payload, key)
        self.assertEqual(signed.package_id, "myapp")
        self.assertEqual(signed.version, "1.0.0")
        self.assertEqual(signed.key_id, key.key_id)
    
    def test_verify_package(self):
        """Can verify a signed package."""
        from backend.package_signing import PackageSigner, SignatureStatus
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Package Signer")
        payload = b"package content"
        
        signed = signer.sign_package("myapp", "1.0.0", payload, key)
        status = signer.verify_package(signed)
        self.assertEqual(status, SignatureStatus.VALID)
    
    def test_verify_package_unknown_key(self):
        """Unknown key fails verification."""
        from backend.package_signing import PackageSigner, SignatureStatus
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Test Key")
        payload = b"package content"
        
        signed = signer.sign_package("myapp", "1.0.0", payload, key)
        signed.key_id = "unknown-key-id"
        
        status = signer.verify_package(signed)
        self.assertEqual(status, SignatureStatus.UNKNOWN_KEY)
    
    def test_trust_store_persistence(self):
        """Trust store persists across instances."""
        from backend.package_signing import PackageSigner
        signer1 = PackageSigner(self._trust_store)
        signer1.generate_key("Persistent Key")
        
        signer2 = PackageSigner(self._trust_store)
        keys = signer2.get_trusted_keys()
        self.assertEqual(len(keys), 1)
    
    def test_remove_key(self):
        """Can remove a key from trust store."""
        from backend.package_signing import PackageSigner
        signer = PackageSigner(self._trust_store)
        
        key = signer.generate_key("Temp Key")
        self.assertTrue(signer.remove_key(key.key_id))
        self.assertEqual(len(signer.get_trusted_keys()), 0)
    
    def test_multiple_keys(self):
        """Can manage multiple keys."""
        from backend.package_signing import PackageSigner
        signer = PackageSigner(self._trust_store)
        
        signer.generate_key("Key 1")
        signer.generate_key("Key 2")
        signer.generate_key("Key 3")
        
        self.assertEqual(len(signer.get_trusted_keys()), 3)


class TestPackageSigningIntegration(unittest.TestCase):
    """Integration tests for package signing."""

    def test_full_signing_workflow(self):
        """Full workflow: generate → sign → verify."""
        from backend.package_signing import PackageSigner, SignatureStatus
        
        with tempfile.TemporaryDirectory() as tmpdir:
            trust_store = os.path.join(tmpdir, "trust.json")
            signer = PackageSigner(trust_store)
            
            # Generate release signing key
            release_key = signer.generate_key("Nyrqis Release Signing Key")
            
            # Sign a package
            payload = b"nyrqis-app-1.0.0-content"
            signed = signer.sign_package("nyrqis-app", "1.0.0", payload, release_key)
            
            # Verify
            status = signer.verify_package(signed)
            self.assertEqual(status, SignatureStatus.VALID)
            
            # Tamper with checksum
            signed.checksum = "tampered"
            status = signer.verify_package(signed)
            self.assertEqual(status, SignatureStatus.INVALID)


if __name__ == "__main__":
    unittest.main()
