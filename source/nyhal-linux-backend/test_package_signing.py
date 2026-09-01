"""Unit tests for backend.package_signing — Ed25519 package signing.

Tests cover:
- Keypair generation and reconstruction
- Signing and verification
- Trust store management
- Serialization roundtrips
- Error handling
"""

import os
import tempfile
import unittest

from backend.package_signing import (
    SigningKeypair,
    sign_package,
    verify_package,
    PackageSignature,
    PackageSignError,
    TrustStore,
    HAS_NACL,
)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestSigningKeypair(unittest.TestCase):
    """Test SigningKeypair generation and reconstruction."""

    def test_generate_creates_valid_keypair(self):
        kp = SigningKeypair.generate()
        self.assertEqual(len(kp.public_key), 32)
        self.assertEqual(len(kp.private_key), 32)
        self.assertEqual(len(kp.key_id), 16)  # 8 bytes hex
        self.assertTrue(kp.can_sign)

    def test_from_private_key_reconstructs(self):
        original = SigningKeypair.generate()
        restored = SigningKeypair.from_private_key(original.private_key)
        self.assertEqual(restored.public_key, original.public_key)
        self.assertEqual(restored.private_key, original.private_key)
        self.assertEqual(restored.key_id, original.key_id)
        self.assertTrue(restored.can_sign)

    def test_from_public_key_is_verify_only(self):
        original = SigningKeypair.generate()
        verify_only = SigningKeypair.from_public_key(original.public_key)
        self.assertEqual(verify_only.public_key, original.public_key)
        self.assertEqual(verify_only.private_key, b"")
        self.assertEqual(verify_only.key_id, original.key_id)
        self.assertFalse(verify_only.can_sign)

    def test_invalid_private_key_length(self):
        with self.assertRaises(PackageSignError):
            SigningKeypair.from_private_key(b"too short")

    def test_invalid_public_key_length(self):
        with self.assertRaises(PackageSignError):
            SigningKeypair.from_public_key(b"too short")

    def test_to_dict_contains_public_key(self):
        kp = SigningKeypair.generate()
        d = kp.to_dict()
        self.assertIn("public_key", d)
        self.assertIn("key_id", d)

    def test_private_dict_contains_private_key(self):
        kp = SigningKeypair.generate()
        d = kp.private_dict()
        self.assertIn("private_key", d)
        self.assertIn("public_key", d)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestSigningVerification(unittest.TestCase):
    """Test sign_package and verify_package."""

    def setUp(self):
        self.kp = SigningKeypair.generate()
        self.manifest = b'{"name": "test", "version": "1.0.0"}'
        self.tree = b"merkle-root-hash"

    def test_sign_returns_64_bytes(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        self.assertEqual(len(sig), 64)

    def test_verify_returns_true(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        result = verify_package(self.manifest, self.tree, sig, self.kp.public_key)
        self.assertTrue(result)

    def test_verify_fails_with_wrong_key(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        wrong_kp = SigningKeypair.generate()
        with self.assertRaises(PackageSignError):
            verify_package(self.manifest, self.tree, sig, wrong_kp.public_key)

    def test_verify_fails_with_wrong_manifest(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        with self.assertRaises(PackageSignError):
            verify_package(b"wrong manifest", self.tree, sig, self.kp.public_key)

    def test_verify_fails_with_wrong_tree(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        with self.assertRaises(PackageSignError):
            verify_package(self.manifest, b"wrong tree", sig, self.kp.public_key)

    def test_verify_fails_with_corrupted_signature(self):
        sig = sign_package(self.manifest, self.tree, self.kp)
        corrupted = bytearray(sig)
        corrupted[0] ^= 0xFF
        with self.assertRaises(PackageSignError):
            verify_package(self.manifest, self.tree, bytes(corrupted), self.kp.public_key)

    def test_sign_with_verify_only_key_fails(self):
        verify_only = SigningKeypair.from_public_key(self.kp.public_key)
        with self.assertRaises(PackageSignError):
            sign_package(self.manifest, self.tree, verify_only)

    def test_different_messages_produce_different_signatures(self):
        sig1 = sign_package(b"msg1", self.tree, self.kp)
        sig2 = sign_package(b"msg2", self.tree, self.kp)
        self.assertNotEqual(sig1, sig2)

    def test_same_message_produces_deterministic_signature(self):
        sig1 = sign_package(self.manifest, self.tree, self.kp)
        sig2 = sign_package(self.manifest, self.tree, self.kp)
        self.assertEqual(sig1, sig2)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestPackageSignature(unittest.TestCase):
    """Test PackageSignature serialization."""

    def setUp(self):
        self.kp = SigningKeypair.generate()
        sig = sign_package(b"test", b"tree", self.kp)
        self.sig_block = PackageSignature(
            public_key=self.kp.public_key,
            signature=sig,
            key_id=self.kp.key_id,
        )

    def test_to_bytes_is_97_bytes(self):
        data = self.sig_block.to_bytes()
        self.assertEqual(len(data), 97)

    def test_to_bytes_version_byte(self):
        data = self.sig_block.to_bytes()
        self.assertEqual(data[0], 0x01)

    def test_from_bytes_roundtrip(self):
        data = self.sig_block.to_bytes()
        restored = PackageSignature.from_bytes(data)
        self.assertEqual(restored.public_key, self.sig_block.public_key)
        self.assertEqual(restored.signature, self.sig_block.signature)
        self.assertEqual(restored.key_id, self.sig_block.key_id)

    def test_from_bytes_invalid_length(self):
        with self.assertRaises(PackageSignError):
            PackageSignature.from_bytes(b"too short")

    def test_from_bytes_invalid_version(self):
        data = bytearray(97)
        data[0] = 0xFF
        with self.assertRaises(PackageSignError):
            PackageSignature.from_bytes(bytes(data))

    def test_to_dict_roundtrip(self):
        d = self.sig_block.to_dict()
        restored = PackageSignature.from_dict(d)
        self.assertEqual(restored.public_key, self.sig_block.public_key)
        self.assertEqual(restored.signature, self.sig_block.signature)

    def test_verify_signature_block(self):
        result = verify_package(
            b"test", b"tree",
            self.sig_block.signature,
            self.sig_block.public_key,
        )
        self.assertTrue(result)


@unittest.skipUnless(HAS_NACL, "PyNaCl required")
class TestTrustStore(unittest.TestCase):
    """Test TrustStore management."""

    def setUp(self):
        self.store = TrustStore()
        self.kp = SigningKeypair.generate()

    def test_add_trusted(self):
        key_id = self.store.add_trusted(self.kp.public_key)
        self.assertEqual(key_id, self.kp.key_id)
        self.assertIn(key_id, self.store.list_trusted())

    def test_remove_trusted(self):
        self.store.add_trusted(self.kp.public_key)
        result = self.store.remove_trusted(self.kp.key_id)
        self.assertTrue(result)
        self.assertNotIn(self.kp.key_id, self.store.list_trusted())

    def test_remove_nonexistent_returns_false(self):
        result = self.store.remove_trusted("nonexistent")
        self.assertFalse(result)

    def test_is_trusted(self):
        self.store.add_trusted(self.kp.public_key)
        self.assertTrue(self.store.is_trusted(self.kp.public_key))

    def test_is_not_trusted(self):
        wrong_kp = SigningKeypair.generate()
        self.assertFalse(self.store.is_trusted(wrong_kp.public_key))

    def test_verify_against_trust(self):
        self.store.add_trusted(self.kp.public_key)
        sig = sign_package(b"test", b"tree", self.kp)
        sig_block = PackageSignature(
            public_key=self.kp.public_key,
            signature=sig,
            key_id=self.kp.key_id,
        )
        self.assertTrue(self.store.verify_against_trust(sig_block))

    def test_save_and_load(self):
        self.store.add_trusted(self.kp.public_key)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            self.store.save(path)
            loaded = TrustStore.load(path)
            self.assertEqual(loaded.list_trusted(), self.store.list_trusted())
        finally:
            os.unlink(path)

    def test_invalid_public_key_length(self):
        with self.assertRaises(PackageSignError):
            self.store.add_trusted(b"too short")


if __name__ == "__main__":
    unittest.main()
