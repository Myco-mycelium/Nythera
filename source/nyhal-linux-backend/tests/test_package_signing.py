"""test_package_signing — Tests for Ed25519 package signing.

References:
    - NPS-026: Package signing (§6)
    - backend/package_signing.py
"""

from __future__ import annotations

import base64
import hashlib
import json
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


class TestKeyFingerprintMigration(unittest.TestCase):
    """The §6.7.2 key fingerprint (AG decision log D4, 2026-09-22).

    Key identity is SHA-256(public key), displayed in FULL as 64
    lowercase hex characters — one spelling everywhere (NPS-028 §3.3);
    the historical 8-byte key_id is a deprecated alias equal to the
    fingerprint, and pre-D4 persisted stores re-key on load.
    """

    def setUp(self):
        from backend.package_signing import SigningKeypair
        self.kp = SigningKeypair.generate()

    def test_fingerprint_is_full_sha256_hex(self):
        from backend.package_signing import SigningKeypair
        expected = hashlib.sha256(self.kp.public_key).hexdigest()
        self.assertEqual(self.kp.fingerprint, expected)
        self.assertEqual(len(self.kp.fingerprint), 64)
        self.assertEqual(self.kp.fingerprint, self.kp.fingerprint.lower())
        # never the legacy 8-byte form
        self.assertNotEqual(self.kp.fingerprint, self.kp.public_key[:8].hex())

    def test_fingerprint_deterministic_across_constructors(self):
        from backend.package_signing import SigningKeypair
        via_private = SigningKeypair.from_private_key(self.kp.private_key)
        via_public = SigningKeypair.from_public_key(self.kp.public_key)
        self.assertEqual(self.kp.fingerprint, via_private.fingerprint)
        self.assertEqual(self.kp.fingerprint, via_public.fingerprint)

    def test_key_id_alias_deprecates(self):
        from backend.package_signing import LegacyKeyAliasWarning
        with self.assertWarns(LegacyKeyAliasWarning):
            self.assertEqual(self.kp.key_id, self.kp.fingerprint)

    def test_fingerprint_rejects_bad_key_length(self):
        from backend.package_signing import PackageSignError, key_fingerprint
        with self.assertRaises(PackageSignError):
            key_fingerprint(b"short")

    def test_signature_block_derives_fingerprint(self):
        from backend.package_signing import PackageSignature
        block = PackageSignature(
            public_key=self.kp.public_key,
            signature=b"\x00" * 64,
            fingerprint=self.kp.fingerprint,
        )
        restored = PackageSignature.from_bytes(block.to_bytes())
        self.assertEqual(restored.fingerprint, self.kp.fingerprint)
        self.assertEqual(len(restored.to_bytes()), 97)

    def test_signature_block_from_dict_accepts_v1(self):
        """A pre-D4 dict (8-byte key_id, no fingerprint) still loads."""
        from backend.package_signing import PackageSignature
        legacy = {
            "public_key": base64.b64encode(self.kp.public_key).decode(),
            "signature": base64.b64encode(b"\x00" * 64).decode(),
            "key_id": self.kp.public_key[:8].hex(),
        }
        sig = PackageSignature.from_dict(legacy)
        self.assertEqual(sig.fingerprint, self.kp.fingerprint)

    def test_trust_store_keys_by_fingerprint(self):
        from backend.package_signing import TrustStore
        store = TrustStore()
        returned = store.add_trusted(self.kp.public_key)
        self.assertEqual(returned, self.kp.fingerprint)
        self.assertEqual(store.list_trusted(), [self.kp.fingerprint])
        self.assertTrue(store.is_trusted(self.kp.public_key))
        # the deprecated key_id parameter is ignored, never used as index
        store.add_trusted(self.kp.public_key, key_id="custom-alias")
        self.assertEqual(store.list_trusted(), [self.kp.fingerprint])

    def test_trust_store_rekeys_legacy_store(self):
        """A pre-D4 persisted store (8-byte ids) loads re-keyed."""
        from backend.package_signing import TrustStore
        store = TrustStore()
        store.add_trusted(self.kp.public_key)
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as fh:
            # hand-written v1 store: legacy ids, no version marker
            json.dump({
                "trusted_keys": {
                    self.kp.public_key[:8].hex():
                        base64.b64encode(self.kp.public_key).decode(),
                }
            }, fh)
            legacy_path = fh.name
        try:
            loaded = TrustStore.load(legacy_path)
            self.assertTrue(loaded.is_trusted(self.kp.public_key))
            self.assertEqual(loaded.list_trusted(), [self.kp.fingerprint])
        finally:
            os.unlink(legacy_path)

    def test_to_dict_carries_version_marker(self):
        from backend.package_signing import SigningKeypair
        d = self.kp.to_dict()
        self.assertEqual(d["key_id_version"], 2)
        self.assertEqual(d["fingerprint"], d["key_id"])
        self.assertEqual(len(d["fingerprint"]), 64)

    def test_package_signer_generate_key_defaults_to_fingerprint(self):
        from backend.package_signing import PackageSigner
        tmpdir = tempfile.mkdtemp(prefix="nyrqis-fp-test-")
        try:
            signer = PackageSigner(os.path.join(tmpdir, "trust.json"))
            key = signer.generate_key("D4 Key")
            self.assertEqual(
                key.key_id,
                hashlib.sha256(key.public_key).hexdigest())
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


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
