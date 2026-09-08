"""Package signing — Ed25519 digital signatures for .nypkg files.

Implements NPS-026 §6 (Digital Signatures) using Ed25519 via PyNaCl.
Every package MUST be signed by its publisher; the signature covers
the manifest and content integrity trees.

Architecture:
  Publisher (signing):
    1. Generate or load Ed25519 keypair
    2. Hash the manifest + integrity trees (SHA-256)
    3. Sign the hash with the private key
    4. Embed the public key + signature in the package

  Verifier (install):
    1. Extract the public key from the package
    2. Hash the manifest + integrity trees (SHA-256)
    3. Verify the signature against the hash
    4. Check the public key against trusted keys

References:
  - NPS-026 §6: Digital Signatures
  - NPS-026 §6.3: Trust anchors (ADR-0014 model)
  - FIND-PACKAGE-001: checksum-only integrity hole
  - ADR-0014: boot trust anchor model

Crypto primitives:
  - Ed25519 for signing (64-byte signatures, 32-byte public keys)
  - SHA-256 for hashing (used by PyNaCl internally)
  - XChaCha20-Poly1305 for key wrapping (existing KeyManager)

Fail-closed posture: PyNaCl is a hard dependency of this project
(pyproject.toml). Without it, every signing/verification operation
raises ``PackageSignError`` — there is deliberately NO stub fallback,
because a forgeable "stub signature" would fail closed only by
accident and would be worse than useless (NPS-026 §6, FIND-PACKAGE-001).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.encoding import RawEncoder
    HAS_NACL = True
except ImportError:
    HAS_NACL = False


class PackageSignError(Exception):
    """Raised when package signing or verification fails."""


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

@dataclass
class SigningKeypair:
    """An Ed25519 signing keypair.

    Attributes
    ----------
    public_key : bytes
        32-byte Ed25519 public key (verify key).
    private_key : bytes
        32-byte Ed25519 seed (signing key seed).
    key_id : str
        Hex-encoded first 8 bytes of the public key (for identification).
    """

    public_key: bytes
    private_key: bytes
    key_id: str

    @classmethod
    def generate(cls) -> "SigningKeypair":
        """Generate a new random keypair."""
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for signing")
        sk = SigningKey.generate()
        pk = sk.verify_key
        key_id = pk.encode(RawEncoder)[:8].hex()
        return cls(
            public_key=pk.encode(RawEncoder),
            private_key=bytes(sk),
            key_id=key_id,
        )

    @classmethod
    def from_private_key(cls, private_key: bytes) -> "SigningKeypair":
        """Reconstruct a keypair from a private key seed."""
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for signing")
        if len(private_key) != 32:
            raise PackageSignError("Private key must be 32 bytes")
        sk = SigningKey(private_key, encoder=RawEncoder)
        pk = sk.verify_key
        key_id = pk.encode(RawEncoder)[:8].hex()
        return cls(
            public_key=pk.encode(RawEncoder),
            private_key=private_key,
            key_id=key_id,
        )

    @classmethod
    def from_public_key(cls, public_key: bytes) -> "SigningKeypair":
        """Create a verify-only keypair from a public key.

        The private_key field will be empty; signing will fail.
        """
        if len(public_key) != 32:
            raise PackageSignError("Public key must be 32 bytes")
        key_id = public_key[:8].hex()
        return cls(
            public_key=public_key,
            private_key=b"",
            key_id=key_id,
        )

    @property
    def can_sign(self) -> bool:
        """Whether this keypair can sign (has private key)."""
        return len(self.private_key) == 32

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "public_key": base64.b64encode(self.public_key).decode(),
            "key_id": self.key_id,
        }

    def private_dict(self) -> dict:
        """Serialize private key to a JSON-compatible dict (sensitive)."""
        return {
            "private_key": base64.b64encode(self.private_key).decode(),
            "public_key": base64.b64encode(self.public_key).decode(),
            "key_id": self.key_id,
        }


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _hash_data(*parts: bytes) -> bytes:
    """Hash multiple data parts into a single SHA-256 digest."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.digest()


def sign_package(
    manifest_bytes: bytes,
    integrity_tree_bytes: bytes,
    signing_key: SigningKeypair,
) -> bytes:
    """Sign a package's manifest and integrity tree.

    Parameters
    ----------
    manifest_bytes : bytes
        The serialized package manifest.
    integrity_tree_bytes : bytes
        The serialized integrity tree (Merkle root).
    signing_key : SigningKeypair
        The publisher's signing keypair (must have private key).

    Returns
    -------
    bytes
        The 64-byte Ed25519 signature.

    Raises
    ------
    PackageSignError
        If the keypair cannot sign or PyNaCl is unavailable.
    """
    if not HAS_NACL:
        raise PackageSignError("PyNaCl required for signing")
    if not signing_key.can_sign:
        raise PackageSignError("Keypair has no private key")

    # Hash the manifest + integrity tree
    digest = _hash_data(manifest_bytes, integrity_tree_bytes)

    # Sign the digest — PyNaCl returns a SignedMessage (signature || message)
    sk = SigningKey(signing_key.private_key, encoder=RawEncoder)
    signed = sk.sign(digest)

    # Extract just the 64-byte signature
    return bytes(signed.signature)


def verify_package(
    manifest_bytes: bytes,
    integrity_tree_bytes: bytes,
    signature: bytes,
    public_key: bytes,
) -> bool:
    """Verify a package's signature.

    Parameters
    ----------
    manifest_bytes : bytes
        The serialized package manifest.
    integrity_tree_bytes : bytes
        The serialized integrity tree (Merkle root).
    signature : bytes
        The 64-byte Ed25519 signature.
    public_key : bytes
        The 32-byte Ed25519 public key.

    Returns
    -------
    bool
        True if the signature is valid.

    Raises
    ------
    PackageSignError
        If verification fails (invalid signature, wrong key, etc.).
    """
    if not HAS_NACL:
        raise PackageSignError("PyNaCl required for verification")
    if len(signature) != 64:
        raise PackageSignError("Signature must be 64 bytes")
    if len(public_key) != 32:
        raise PackageSignError("Public key must be 32 bytes")

    # Hash the manifest + integrity tree
    digest = _hash_data(manifest_bytes, integrity_tree_bytes)

    # Reconstruct the signed message: signature || digest
    signed_message = signature + digest

    # Verify the signature
    try:
        vk = VerifyKey(public_key, encoder=RawEncoder)
        vk.verify(signed_message)
        return True
    except Exception as e:
        raise PackageSignError(f"Signature verification failed: {e}")


# ---------------------------------------------------------------------------
# Package signature block (for .nypkg files)
# ---------------------------------------------------------------------------

@dataclass
class PackageSignature:
    """A package signature block embedded in a .nypkg file.

    This is the on-disk format for NPS-026 §6's signature block.
    """

    public_key: bytes     # 32 bytes
    signature: bytes      # 64 bytes
    key_id: str           # hex-encoded first 8 bytes of public key

    def to_bytes(self) -> bytes:
        """Serialize to a compact binary format.

        Format: [1 byte version][32 bytes public_key][64 bytes signature]
        Total: 97 bytes
        """
        return bytes([0x01]) + self.public_key + self.signature

    @classmethod
    def from_bytes(cls, data: bytes) -> "PackageSignature":
        """Deserialize from compact binary format."""
        if len(data) != 97:
            raise PackageSignError(
                f"Signature block must be 97 bytes, got {len(data)}")
        if data[0] != 0x01:
            raise PackageSignError(f"Unknown signature version: {data[0]}")
        public_key = data[1:33]
        signature = data[33:97]
        key_id = public_key[:8].hex()
        return cls(public_key=public_key, signature=signature, key_id=key_id)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "version": 1,
            "public_key": base64.b64encode(self.public_key).decode(),
            "signature": base64.b64encode(self.signature).decode(),
            "key_id": self.key_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PackageSignature":
        """Deserialize from a JSON dict."""
        return cls(
            public_key=base64.b64decode(data["public_key"]),
            signature=base64.b64decode(data["signature"]),
            key_id=data.get("key_id", ""),
        )


# ---------------------------------------------------------------------------
# Trust store
# ---------------------------------------------------------------------------

class TrustStore:
    """A store of trusted publisher public keys.

    Implements NPS-026 §6.3's trust anchor model: a platform trust
    anchor plus user-enrollable keys.
    """

    def __init__(self) -> None:
        self._trusted: dict = {}  # key_id → public_key

    def add_trusted(self, public_key: bytes, key_id: Optional[str] = None) -> str:
        """Add a trusted public key.

        Returns the key_id.
        """
        if len(public_key) != 32:
            raise PackageSignError("Public key must be 32 bytes")
        kid = key_id or public_key[:8].hex()
        self._trusted[kid] = public_key
        return kid

    def remove_trusted(self, key_id: str) -> bool:
        """Remove a trusted key. Returns True if it existed."""
        return self._trusted.pop(key_id, None) is not None

    def is_trusted(self, public_key: bytes) -> bool:
        """Check if a public key is in the trust store."""
        kid = public_key[:8].hex()
        return kid in self._trusted and self._trusted[kid] == public_key

    def verify_against_trust(self, sig: PackageSignature) -> bool:
        """Verify a signature against the trust store.

        Returns True if the public key is trusted AND the signature
        is valid (the actual signature verification is done by the caller
        after checking trust).
        """
        return self.is_trusted(sig.public_key)

    def list_trusted(self) -> list:
        """List all trusted key IDs."""
        return list(self._trusted.keys())

    def save(self, path: str) -> None:
        """Save the trust store to a JSON file."""
        data = {
            "trusted_keys": {
                kid: base64.b64encode(pk).decode()
                for kid, pk in self._trusted.items()
            }
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str) -> "TrustStore":
        """Load a trust store from a JSON file."""
        data = json.loads(Path(path).read_text())
        store = cls()
        for kid, pk_b64 in data.get("trusted_keys", {}).items():
            store._trusted[kid] = base64.b64decode(pk_b64)
        return store


# ---------------------------------------------------------------------------
# Convenience signer (trust-store backed key management)
# ---------------------------------------------------------------------------

@dataclass
class KeyPair:
    """An Ed25519 key pair (convenience wrapper)."""

    key_id: str
    public_key: bytes
    private_key: Optional[bytes] = None
    created_at: float = 0.0
    expires_at: Optional[float] = None
    name: str = ""


@dataclass
class SignedPackage:
    """A signed Nyrqis package (convenience wrapper)."""

    package_id: str
    version: str
    checksum: str
    signature: bytes
    key_id: str
    timestamp: float


class SignatureStatus:
    """Package signature verification status."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN_KEY = "unknown_key"
    TAMPERED = "tampered"
    MISSING = "missing"
    EXPIRED = "expired"


class PackageSigner:
    """Ed25519 package signer with a persistent trust store.

    Usage:
        signer = PackageSigner()

        # Generate a key pair
        key_pair = signer.generate_key("release-signing-key")

        # Sign a package
        signed = signer.sign_package("myapp", "1.0.0", payload, key_pair)

        # Verify a package
        status = signer.verify_package(signed)

    All cryptography is real Ed25519 via PyNaCl. Without PyNaCl every
    operation raises ``PackageSignError`` — there is no stub fallback.
    """

    def __init__(self, trust_store_path: Optional[str] = None):
        self.trust_store_path = trust_store_path or os.path.expanduser(
            "~/.nyrqis/trust-store.json")
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
            except Exception as exc:  # noqa: BLE001
                raise PackageSignError(f"Failed to load trust store: {exc}")

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
        kp = SigningKeypair.generate()
        key_id = key_id or kp.key_id

        key_pair = KeyPair(
            key_id=key_id,
            public_key=kp.public_key,
            private_key=kp.private_key,
            created_at=time.time(),
            name=name,
        )

        # Add to trust store (public key only)
        self._trust_store[key_id] = KeyPair(
            key_id=key_id,
            public_key=key_pair.public_key,
            created_at=key_pair.created_at,
            name=name,
        )
        self._save_trust_store()
        return key_pair

    def sign_payload(self, payload: bytes, key_pair: KeyPair) -> bytes:
        """Sign a payload with a private key."""
        if key_pair.private_key is None:
            raise ValueError("Key pair does not contain a private key")
        sk = SigningKey(key_pair.private_key, encoder=RawEncoder)
        signed = sk.sign(payload)
        return bytes(signed.signature)

    def verify_signature(
        self, payload: bytes, signature: bytes, public_key: bytes,
    ) -> bool:
        """Verify a signature.

        Returns True if valid; raises PackageSignError on malformed
        input; returns False on a genuine signature mismatch.
        """
        if len(signature) != 64:
            raise PackageSignError("Signature must be 64 bytes")
        if len(public_key) != 32:
            raise PackageSignError("Public key must be 32 bytes")
        try:
            vk = VerifyKey(public_key, encoder=RawEncoder)
            vk.verify(signature + payload)
            return True
        except Exception:
            return False

    def sign_package(self, package_id: str, version: str, payload: bytes,
                     key_pair: KeyPair) -> SignedPackage:
        """Sign a Nyrqis package."""
        checksum = hashlib.sha256(payload).hexdigest()
        sig_payload = f"{package_id}:{version}:{checksum}".encode()
        signature = self.sign_payload(sig_payload, key_pair)
        return SignedPackage(
            package_id=package_id,
            version=version,
            checksum=checksum,
            signature=signature,
            key_id=key_pair.key_id,
            timestamp=time.time(),
        )

    def verify_package(self, signed: SignedPackage) -> str:
        """Verify a signed package.

        Returns a SignatureStatus string.
        """
        if signed.key_id not in self._trust_store:
            return SignatureStatus.UNKNOWN_KEY

        key_pair = self._trust_store[signed.key_id]

        if key_pair.expires_at and time.time() > key_pair.expires_at:
            return SignatureStatus.EXPIRED

        sig_payload = f"{signed.package_id}:{signed.version}:{signed.checksum}".encode()
        if not self.verify_signature(
            sig_payload, signed.signature, key_pair.public_key,
        ):
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


__all__ = [
    "SigningKeypair",
    "sign_package",
    "verify_package",
    "PackageSignature",
    "PackageSignError",
    "TrustStore",
    "HAS_NACL",
    "PackageSigner",
    "KeyPair",
    "SignedPackage",
    "SignatureStatus",
]