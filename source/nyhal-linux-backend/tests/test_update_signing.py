"""test_update_signing — Tests for delta update signature verification.

References:
    - NPS-026 §6: Digital Signatures
    - backend/update_signing.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestUpdateManifest(unittest.TestCase):
    """Tests for UpdateManifest dataclass."""

    def test_create_manifest(self):
        """Can create an UpdateManifest."""
        from backend.update_signing import UpdateManifest, UpdateType
        manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        self.assertEqual(manifest.package_id, "test-app")
        self.assertEqual(manifest.version_from, "1.0.0")
        self.assertEqual(manifest.version_to, "1.1.0")


class TestVerificationResult(unittest.TestCase):
    """Tests for VerificationResult dataclass."""

    def test_create_result(self):
        """Can create a VerificationResult."""
        from backend.update_signing import VerificationResult, VerificationStatus
        result = VerificationResult(
            status=VerificationStatus.VALID,
            message="All good",
        )
        self.assertEqual(result.status, VerificationStatus.VALID)
        self.assertEqual(result.message, "All good")


class TestUpdateVerifier(unittest.TestCase):
    """Tests for UpdateVerifier."""

    def setUp(self):
        """Create a temp directory for test files."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-update-test-")
        self.trust_store = os.path.join(self.tmpdir, "trust.json")
        
        # Create a trust store with a test key
        trust_data = {
            "trusted_keys": [
                {
                    "key_id": "test-key-001",
                    "public_key": "dGVzdC1wdWJsaWMta2V5",
                    "name": "Test Publisher",
                }
            ]
        }
        with open(self.trust_store, "w") as f:
            json.dump(trust_data, f)

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_verify_full_update_valid(self):
        """Verify a full update with valid signature."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        # Create a test payload
        payload_path = os.path.join(self.tmpdir, "payload.nypkg")
        with open(payload_path, "w") as f:
            f.write("test payload content")
        
        # Create manifest
        manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        # Compute checksum
        import hashlib
        sha256 = hashlib.sha256()
        with open(payload_path, "rb") as f:
            sha256.update(f.read())
        manifest.checksum = sha256.hexdigest()
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.verify_full_update(manifest, payload_path)
        
        self.assertEqual(result.status, VerificationStatus.VALID)

    def test_verify_full_update_missing_signature(self):
        """Verify a full update without signature fails."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.verify_full_update(manifest, "/nonexistent")
        
        self.assertEqual(result.status, VerificationStatus.MISSING)

    def test_verify_full_update_tampered(self):
        """Verify a full update with wrong checksum fails."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        payload_path = os.path.join(self.tmpdir, "payload.nypkg")
        with open(payload_path, "w") as f:
            f.write("test payload content")
        
        manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="wrong-checksum",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.verify_full_update(manifest, payload_path)
        
        self.assertEqual(result.status, VerificationStatus.TAMPERED)

    def test_verify_delta_update_valid(self):
        """Verify a delta update with valid patches."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        base_manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        
        delta_manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.1.0",
            version_to="1.2.0",
            update_type=UpdateType.DELTA,
            delta_patches=[
                {"op": "modify", "path": "/bin/app", "hash": "def456"},
            ],
            checksum="abc123",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        delta_path = os.path.join(self.tmpdir, "delta.nypkg")
        with open(delta_path, "w") as f:
            f.write("delta content")
        
        # Compute checksum
        import hashlib
        sha256 = hashlib.sha256()
        with open(delta_path, "rb") as f:
            sha256.update(f.read())
        delta_manifest.checksum = sha256.hexdigest()
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.verify_delta_update(base_manifest, delta_manifest, delta_path)
        
        self.assertEqual(result.status, VerificationStatus.VALID)

    def test_verify_delta_update_version_mismatch(self):
        """Verify a delta update with wrong base version fails."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        base_manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        
        delta_manifest = UpdateManifest(
            package_id="test-app",
            version_from="1.2.0",  # Wrong base version
            version_to="1.3.0",
            update_type=UpdateType.DELTA,
            delta_patches=[],
            checksum="abc123",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.verify_delta_update(base_manifest, delta_manifest, "/nonexistent")
        
        self.assertEqual(result.status, VerificationStatus.INVALID)

    def test_validate_rollback_valid(self):
        """Validate a rollback to an older version."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        current = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.2.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        
        rollback = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",  # Older version
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.validate_rollback(current, rollback)
        
        self.assertEqual(result.status, VerificationStatus.VALID)

    def test_validate_rollback_to_newer_fails(self):
        """Validate that rollback to newer version fails."""
        from backend.update_signing import (
            UpdateVerifier, UpdateManifest, UpdateType, VerificationStatus
        )
        
        current = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.1.0",
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
        )
        
        rollback = UpdateManifest(
            package_id="test-app",
            version_from="1.0.0",
            version_to="1.2.0",  # Newer version
            update_type=UpdateType.FULL,
            delta_patches=[],
            checksum="abc123",
            signature=b"fake-signature",
            key_id="test-key-001",
        )
        
        verifier = UpdateVerifier(self.trust_store)
        result = verifier.validate_rollback(current, rollback)
        
        self.assertEqual(result.status, VerificationStatus.INVALID)


class TestDiagnostics(unittest.TestCase):
    """Tests for the diagnostic system."""

    def test_run_diagnostics(self):
        """Diagnostics run without crashing."""
        from nyrqis_init import run_diagnostics
        checks = run_diagnostics()
        self.assertGreater(len(checks), 0)
        
        # All checks should have required fields
        for check in checks:
            self.assertIsNotNone(check.name)
            self.assertIsInstance(check.passed, bool)
            self.assertIsNotNone(check.message)


if __name__ == "__main__":
    unittest.main()
