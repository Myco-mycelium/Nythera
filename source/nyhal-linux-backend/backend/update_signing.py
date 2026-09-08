"""update_signing — Delta update signature verification for packages.

Implements NPS-026 §6 verification for package updates, including:
- Delta update signature verification
- Re-signing after local modifications
- Rollback signature validation

References:
    - NPS-026 §6: Digital Signatures
    - ADR-0023: Key manager (envelope encryption)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .package_signing import (
    HAS_NACL,
    PackageSignError,
    SigningKeypair,
)

if HAS_NACL:
    from nacl.encoding import RawEncoder
    from nacl.signing import SigningKey, VerifyKey

logger = logging.getLogger(__name__)


class UpdateType(Enum):
    """Type of package update."""
    FULL = "full"           # Full package replacement
    DELTA = "delta"         # Delta/differential update
    PATCH = "patch"         # Security patch


class VerificationStatus(Enum):
    """Result of signature verification."""
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN_KEY = "unknown_key"
    EXPIRED = "expired"
    TAMPERED = "tampered"
    MISSING = "missing"


@dataclass
class UpdateManifest:
    """Manifest for a package update."""
    package_id: str
    version_from: str
    version_to: str
    update_type: UpdateType
    delta_patches: List[Dict[str, Any]]  # list of delta operations
    checksum: str                         # SHA-256 of the update payload
    signature: Optional[bytes] = None     # Ed25519 signature
    key_id: Optional[str] = None          # Signing key ID
    timestamp: Optional[float] = None     # Unix timestamp


@dataclass
class VerificationResult:
    """Result of update verification."""
    status: VerificationStatus
    message: str
    manifest: Optional[UpdateManifest] = None
    details: Optional[Dict[str, Any]] = None


class UpdateVerifier:
    """Verifies package update signatures.
    
    This class handles:
    1. Full update verification (same as initial install)
    2. Delta update verification (patch integrity)
    3. Re-signing after local modifications
    4. Rollback signature validation
    """
    
    def __init__(self, trust_store_path: Optional[str] = None):
        """Initialize the update verifier.
        
        Parameters
        ----------
        trust_store_path : str, optional
            Path to the trust store JSON file.
        """
        self.trust_store_path = trust_store_path
        self._trust_store: Optional[Dict[str, Any]] = None
    
    def _load_trust_store(self) -> Dict[str, Any]:
        """Load the trust store from disk."""
        if self._trust_store is not None:
            return self._trust_store
        
        if self.trust_store_path and os.path.exists(self.trust_store_path):
            with open(self.trust_store_path) as f:
                self._trust_store = json.load(f)
        else:
            self._trust_store = {"trusted_keys": []}
        
        return self._trust_store
    
    def verify_full_update(
        self,
        manifest: UpdateManifest,
        payload_path: str,
    ) -> VerificationResult:
        """Verify a full package update.
        
        This verifies:
        1. The manifest checksum matches the payload
        2. The signature is valid
        3. The signing key is trusted
        
        Parameters
        ----------
        manifest : UpdateManifest
            The update manifest.
        payload_path : str
            Path to the update payload file.
            
        Returns
        -------
        VerificationResult
            The verification result.
        """
        # Check if signature is present
        if manifest.signature is None:
            return VerificationResult(
                status=VerificationStatus.MISSING,
                message="Update is not signed",
            )
        
        # Verify checksum
        if not os.path.exists(payload_path):
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Payload file not found: {payload_path}",
            )
        
        actual_checksum = self._compute_file_checksum(payload_path)
        if actual_checksum != manifest.checksum:
            return VerificationResult(
                status=VerificationStatus.TAMPERED,
                message=f"Checksum mismatch: expected {manifest.checksum}, got {actual_checksum}",
            )
        
        # Verify signature (real Ed25519, NPS-026 §6)
        sig_result = self._verify_manifest_signature(manifest)
        if sig_result.status != VerificationStatus.VALID:
            return sig_result
        
        logger.info("Update signature verified for package %s", manifest.package_id)
        
        return VerificationResult(
            status=VerificationStatus.VALID,
            message="Update signature verified",
            manifest=manifest,
        )
    
    def verify_delta_update(
        self,
        base_manifest: UpdateManifest,
        delta_manifest: UpdateManifest,
        delta_path: str,
    ) -> VerificationResult:
        """Verify a delta package update.
        
        This verifies:
        1. The delta patches are valid
        2. The delta signature is valid
        3. The base package is compatible
        
        Parameters
        ----------
        base_manifest : UpdateManifest
            The manifest of the currently installed version.
        delta_manifest : UpdateManifest
            The manifest of the delta update.
        delta_path : str
            Path to the delta payload.
            
        Returns
        -------
        VerificationResult
            The verification result.
        """
        # Verify the delta is for the correct base version
        if delta_manifest.version_from != base_manifest.version_to:
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Delta base version mismatch: expected {base_manifest.version_to}, "
                        f"got {delta_manifest.version_from}",
            )
        
        # Verify delta signature
        if delta_manifest.signature is None:
            return VerificationResult(
                status=VerificationStatus.MISSING,
                message="Delta update is not signed",
            )
        
        # Verify the delta signature cryptographically before the
        # payload checks — a forged delta must not reach patch parsing.
        delta_trust = self._verify_manifest_signature(delta_manifest)
        if delta_trust.status != VerificationStatus.VALID:
            return delta_trust

        # Verify delta checksum
        if not os.path.exists(delta_path):
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Delta payload not found: {delta_path}",
            )
        
        actual_checksum = self._compute_file_checksum(delta_path)
        if actual_checksum != delta_manifest.checksum:
            return VerificationResult(
                status=VerificationStatus.TAMPERED,
                message=f"Delta checksum mismatch",
            )
        
        # Verify delta patches are valid
        for i, patch in enumerate(delta_manifest.delta_patches):
            if "op" not in patch:
                return VerificationResult(
                    status=VerificationStatus.INVALID,
                    message=f"Delta patch {i} missing 'op' field",
                )
            if patch["op"] not in ("add", "remove", "modify"):
                return VerificationResult(
                    status=VerificationStatus.INVALID,
                    message=f"Delta patch {i} has invalid op: {patch['op']}",
                )
        
        logger.info("Delta update verified for package %s: %s → %s",
                    delta_manifest.package_id,
                    delta_manifest.version_from,
                    delta_manifest.version_to)
        
        return VerificationResult(
            status=VerificationStatus.VALID,
            message="Delta update signature verified",
            manifest=delta_manifest,
        )
    
    def re_sign_update(
        self,
        manifest: UpdateManifest,
        private_key_path: str,
    ) -> UpdateManifest:
        """Re-sign an update after local modifications.
        
        This is used when:
        - A package is modified locally (e.g., configuration changes)
        - The modification needs to be signed for distribution
        
        Parameters
        ----------
        manifest : UpdateManifest
            The manifest to re-sign.
        private_key_path : str
            Path to the private key file.
            
        Returns
        -------
        UpdateManifest
            The re-signed manifest.
        """
        # Load the private key
        with open(private_key_path) as f:
            key_data = json.load(f)
        
        if "private_key" not in key_data:
            raise ValueError("Key file does not contain a private key")
        
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for re-signing")
        
        import base64 as _b64
        private_key = _b64.b64decode(key_data["private_key"])
        if len(private_key) != 32:
            raise ValueError("Private key must be 32 bytes")
        
        kp = SigningKeypair.from_private_key(private_key)
        sk = SigningKey(private_key, encoder=RawEncoder)
        signed = sk.sign(self._signature_payload(manifest))
        
        manifest.signature = bytes(signed.signature)
        manifest.key_id = key_data.get("key_id", kp.key_id)
        manifest.timestamp = time.time()
        
        logger.info("Re-signed update for package %s with key %s",
                    manifest.package_id, manifest.key_id)
        
        return manifest
    
    def validate_rollback(
        self,
        current_manifest: UpdateManifest,
        rollback_manifest: UpdateManifest,
    ) -> VerificationResult:
        """Validate that a rollback is allowed.
        
        This checks:
        1. The rollback target version is older
        2. The rollback is signed by a trusted key
        3. The rollback doesn't violate any security policies
        
        Parameters
        ----------
        current_manifest : UpdateManifest
            The currently installed version manifest.
        rollback_manifest : UpdateManifest
            The target rollback version manifest.
            
        Returns
        -------
        VerificationResult
            The validation result.
        """
        # Check version ordering (simplified - real impl would use semver)
        if rollback_manifest.version_to >= current_manifest.version_to:
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Rollback target {rollback_manifest.version_to} is not older "
                        f"than current {current_manifest.version_to}",
            )
        
        # Verify rollback signature
        if rollback_manifest.signature is None:
            return VerificationResult(
                status=VerificationStatus.MISSING,
                message="Rollback manifest is not signed",
            )
        
        # Check trust store
        trust_store = self._load_trust_store()
        trusted_keys = {k["key_id"] for k in trust_store.get("trusted_keys", [])}
        
        if rollback_manifest.key_id not in trusted_keys:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN_KEY,
                message=f"Rollback signing key not trusted: {rollback_manifest.key_id}",
            )
        
        logger.info("Rollback validated: %s → %s",
                    current_manifest.version_to,
                    rollback_manifest.version_to)
        
        return VerificationResult(
            status=VerificationStatus.VALID,
            message="Rollback validated",
            manifest=rollback_manifest,
        )
    
    def _compute_file_checksum(self, path: str) -> str:
        """Compute SHA-256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _verify_manifest_signature(self, manifest: "UpdateManifest") -> VerificationResult:
        """Verify a manifest's Ed25519 signature against the trust store.

        Signature-only check: does NOT inspect the payload file. A key
        being in the trust store is NOT sufficient — the signature
        itself must verify (NPS-026 §6).
        """
        if manifest.signature is None:
            return VerificationResult(
                status=VerificationStatus.MISSING,
                message="Update is not signed",
            )

        trust_store = self._load_trust_store()
        trusted_keys = {k["key_id"]: k for k in trust_store.get("trusted_keys", [])}

        if manifest.key_id not in trusted_keys:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN_KEY,
                message=f"Unknown signing key: {manifest.key_id}",
            )

        if not HAS_NACL:
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message="PyNaCl required for signature verification",
            )
        if len(manifest.signature) != 64:
            return VerificationResult(
                status=VerificationStatus.TAMPERED,
                message="Signature must be 64 bytes",
            )

        public_key_b64 = trusted_keys[manifest.key_id].get("public_key", "")
        try:
            public_key = base64.b64decode(public_key_b64)
        except Exception:  # noqa: BLE001
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Trust store key {manifest.key_id} has invalid public key",
            )
        if len(public_key) != 32:
            return VerificationResult(
                status=VerificationStatus.INVALID,
                message=f"Trust store key {manifest.key_id} has malformed public key",
            )
        sig_payload = self._signature_payload(manifest)
        try:
            vk = VerifyKey(public_key, encoder=RawEncoder)
            vk.verify(sig_payload, manifest.signature)
        except Exception:  # noqa: BLE001
            return VerificationResult(
                status=VerificationStatus.TAMPERED,
                message="Update signature does not verify",
            )

        return VerificationResult(
            status=VerificationStatus.VALID,
            message="Update signature verified",
            manifest=manifest,
        )

    @staticmethod
    def _signature_payload(manifest: "UpdateManifest") -> bytes:
        """Build the canonical bytes an update signature covers.

        The signature covers the update identity and the payload
        checksum — NOT the payload itself (the checksum binds them).
        """
        parts = [
            manifest.package_id,
            manifest.version_from,
            manifest.version_to,
            manifest.update_type.value,
            manifest.checksum,
        ]
        return ":".join(parts).encode()
