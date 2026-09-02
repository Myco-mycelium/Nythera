"""package_signing — Ed25519 package signing for Nyrqis.

Provides cryptographic signing and verification for Nyrqis packages:

1. Generate Ed25519 key pairs
2. Sign packages with private keys
3. Verify package signatures with public keys
4. Manage key trust store

References:
    - NPS-026: Package signing (§6)
    - ADR-0023: Key manager (envelope encryption)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import nacl.signing
    import nacl.encoding
    import nacl.utils
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False
    logger.warning("PyNaCl not available — package signing will use stubs")


class SignatureStatus(Enum):
    """Package signature verification status."""
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN_KEY = "unknown_key"
    TAMPERED = "tampered"
    MISSING = "missing"
    EXPIRED = "expired"


@dataclass
class KeyPair:
    """An Ed25519 key pair."""
    key_id: str
    public_key: bytes
    private_key: Optional[bytes] = None
    created_at: float = 0.0
    expires_at: Optional[float] = None
    name: str = ""


@dataclass
class SignedPackage:
    """A signed Nyrqis package."""
    package_id: str
    version: str
    checksum: str
    signature: bytes
    key_id: str
    timestamp: float


class PackageSigner:
    """Ed25519 package signer for Nyrqis.
    
    Usage:
        signer = PackageSigner()
        
        # Generate a key pair
        key_pair = signer.generate_key("release-signing-key")
        
        # Sign a package
        signed = signer.sign_package("myapp", "1.0.0", payload, key_pair)
        
        # Verify a package
        status = signer.verify_package(signed)
    """
    
    def __init__(self, trust_store_path: Optional[str] = None):
        self.trust_store_path = trust_store_path or os.path.expanduser("~/.nyrqis/trust-store.json")
        self._trust_store: Dict[str, KeyPair] = {}
        self._load_trust_store()
    
    def _load_trust_store(self):
        """Load the trust store from disk."""
        if os.path.exists(self.trust_store_path):
            try:
                with open(self.trust_store_path) as f:
                    data = json.load(f)
                for key_data in data.get("keys", []):
                    key_pair = KeyPair(
                        key_id=key_data["key_id"],
                        public_key=base64.b64decode(key_data["public_key"]),
                        created_at=key_data.get("created_at", 0),
                        expires_at=key_data.get("expires_at"),
                        name=key_data.get("name", ""),
                    )
                    self._trust_store[key_pair.key_id] = key_pair
                logger.info("Loaded %d keys from trust store", len(self._trust_store))
            except Exception as exc:
                logger.warning("Failed to load trust store: %s", exc)
    
    def _save_trust_store(self):
        """Save the trust store to disk."""
        os.makedirs(os.path.dirname(self.trust_store_path), exist_ok=True)
        
        keys_data = []
        for key_pair in self._trust_store.values():
            keys_data.append({
                "key_id": key_pair.key_id,
                "public_key": base64.b64encode(key_pair.public_key).decode(),
                "created_at": key_pair.created_at,
                "expires_at": key_pair.expires_at,
                "name": key_pair.name,
            })
        
        with open(self.trust_store_path, "w") as f:
            json.dump({"keys": keys_data}, f, indent=2)
    
    def generate_key(self, name: str, key_id: Optional[str] = None) -> KeyPair:
        """Generate a new Ed25519 key pair.
        
        Parameters
        ----------
        name : str
            Human-readable name for the key.
        key_id : str, optional
            Unique key identifier. Auto-generated if not provided.
            
        Returns
        -------
        KeyPair
            The generated key pair.
        """
        if NACL_AVAILABLE:
            private_key = nacl.signing.SigningKey.generate()
            public_key = private_key.verify_key
        else:
            # Stub: generate deterministic key
            if key_id is None:
                key_id = hashlib.sha256(f"{name}-{time.time()}".encode()).hexdigest()[:16]
            private_key = None
            public_key = hashlib.sha256(f"stub-{key_id}".encode()).digest()
        
        if key_id is None:
            key_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
        
        key_pair = KeyPair(
            key_id=key_id,
            public_key=bytes(public_key) if NACL_AVAILABLE else public_key,
            private_key=bytes(private_key) if NACL_AVAILABLE and private_key else None,
            created_at=time.time(),
            name=name,
        )
        
        # Add to trust store
        self._trust_store[key_id] = KeyPair(
            key_id=key_id,
            public_key=key_pair.public_key,
            created_at=key_pair.created_at,
            name=name,
        )
        self._save_trust_store()
        
        logger.info("Generated key pair: %s (%s)", key_id, name)
        return key_pair
    
    def sign_payload(self, payload: bytes, key_pair: KeyPair) -> bytes:
        """Sign a payload with a private key.
        
        Parameters
        ----------
        payload : bytes
            The data to sign.
        key_pair : KeyPair
            The key pair (must have private_key).
            
        Returns
        -------
        bytes
            The signature.
        """
        if key_pair.private_key is None:
            raise ValueError("Key pair does not contain a private key")
        
        if NACL_AVAILABLE:
            signing_key = nacl.signing.SigningKey(key_pair.private_key)
            signed = signing_key.sign(payload)
            return signed.signature
        else:
            # Stub signature
            return hashlib.sha256(key_pair.private_key + payload).digest()
    
    def verify_signature(self, payload: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verify a signature.
        
        Parameters
        ----------
        payload : bytes
            The signed data.
        signature : bytes
            The signature to verify.
        public_key : bytes
            The public key to verify against.
            
        Returns
        -------
        bool
            True if the signature is valid, False otherwise.
        """
        if NACL_AVAILABLE:
            try:
                verify_key = nacl.signing.VerifyKey(public_key)
                verify_key.verify(payload, signature)
                return True
            except Exception:
                return False
        else:
            # Stub verification
            expected = hashlib.sha256(b"stub-" + public_key + payload).digest()
            return signature == expected
    
    def sign_package(self, package_id: str, version: str, payload: bytes,
                    key_pair: KeyPair) -> SignedPackage:
        """Sign a Nyrqis package.
        
        Parameters
        ----------
        package_id : str
            Package identifier.
        version : str
            Package version.
        payload : bytes
            Package content.
        key_pair : KeyPair
            The signing key.
            
        Returns
        -------
        SignedPackage
            The signed package.
        """
        # Compute checksum
        checksum = hashlib.sha256(payload).hexdigest()
        
        # Create signature payload
        sig_payload = f"{package_id}:{version}:{checksum}".encode()
        
        # Sign
        signature = self.sign_payload(sig_payload, key_pair)
        
        return SignedPackage(
            package_id=package_id,
            version=version,
            checksum=checksum,
            signature=signature,
            key_id=key_pair.key_id,
            timestamp=time.time(),
        )
    
    def verify_package(self, signed: SignedPackage) -> SignatureStatus:
        """Verify a signed package.
        
        Parameters
        ----------
        signed : SignedPackage
            The signed package to verify.
            
        Returns
        -------
        SignatureStatus
            The verification status.
        """
        # Check if key is in trust store
        if signed.key_id not in self._trust_store:
            return SignatureStatus.UNKNOWN_KEY
        
        key_pair = self._trust_store[signed.key_id]
        
        # Check expiration
        if key_pair.expires_at and time.time() > key_pair.expires_at:
            return SignatureStatus.EXPIRED
        
        # Reconstruct signature payload
        sig_payload = f"{signed.package_id}:{signed.version}:{signed.checksum}".encode()
        
        # Verify signature
        if not self.verify_signature(sig_payload, signed.signature, key_pair.public_key):
            return SignatureStatus.INVALID
        
        return SignatureStatus.VALID
    
    def get_trusted_keys(self) -> Dict[str, KeyPair]:
        """Get all trusted keys."""
        return self._trust_store.copy()
    
    def remove_key(self, key_id: str) -> bool:
        """Remove a key from the trust store."""
        if key_id in self._trust_store:
            del self._trust_store[key_id]
            self._save_trust_store()
            return True
        return False
