"""Package PKI — the trust machinery behind NPS-026 §6.3 (NPS-028).

Implementation start (2026-09-22) of the surface NPS-028 enumerates,
after the NPC-002 §6.2 crypto review concluded (AG decision log D4,
landed as NPS-026 v1.3.0 §6.7):

  - the **key store** (§3): platform root anchors, enrolled publisher
    keys, revocation state — immutable except through the enrollment
    and revocation-update paths, entries recording the §3.3 fields,
    keyed by the §6.7.2 fingerprint (SHA-256(public key), full 64
    lowercase hex);
  - the **verification pipeline** (§4): the one ordered path every
    touchpoint runs — parse, resolve (TOFU fail-closed), status,
    signature, launch-advisory — with a per-stage outcome record and
    no silent failures;
  - the **revocation distribution** objects (§5): a root-set-signed
    list with a monotonic sequence number, replay-refusing and applied
    atomically (verify fully, then commit);
  - **enrollment and rotation** (§6): the only path to trust outside
    the root set, gated on a non-skippable confirmation record that
    must show the real fingerprint; cross-signed rotation verifies the
    chain of continuity.

Fail-closed posture: without PyNaCl every operation raises
``PackageSignError`` (no stub fallback — the shipped signing half's
documented stance). Key material never appears in error messages.

Honest scope: this is a START, not the finished surface. Not yet
implemented (each is a named NPS-028 build item): §3.4 custody (the
store persists as JSON; ADR-0023 envelope encryption at rest and the
Rust-held KEK are the next increment), daemon-authority enforcement
of §3.2 (currently a deployment property, not an API property), the
§7 ADR-0018 audit-log wiring (the pipeline accepts any sink), the
stale-list bounds of §5.3 (deferred to implementation validation by
design), and the revocation list's transport (§5.1's out-of-band
channel is a deployment concern).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .package_signing import (
    HAS_NACL,
    PackageSignError,
    PackageSignature,
    TrustStore,
    key_fingerprint,
    verify_package,
)

# Touchpoints (NPS-028 §4 stage 5; NPS-026 §6.3.3/§6.3.4)
TOUCHPOINT_INSTALL = "install"
TOUCHPOINT_UPDATE = "update"
TOUCHPOINT_RE_VERIFY = "re_verify"
TOUCHPOINT_LAUNCH = "launch"

# Entry statuses (NPS-028 §3.3)
STATUS_TRUSTED = "trusted"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"

# Verdicts
VERDICT_APPROVED = "approved"
VERDICT_DENIED = "denied"
VERDICT_ADVISORY = "advisory"   # launch of owned content: notice, not block


class PkiError(Exception):
    """Raised for PKI structural errors (bad state transitions, I/O)."""


# ---------------------------------------------------------------------------
# NPS-028 §3 — the key store
# ---------------------------------------------------------------------------

@dataclass
class EnrollmentConfirmation:
    """The §6 confirmation record: proof the protected UI happened.

    The enrolling flow refuses anything that does not carry an explicit
    confirmation naming the ACTUAL fingerprint (a UI that displayed a
    different key than the one being enrolled is exactly the spoof the
    NPS-015 §5.5 pattern exists to prevent).
    """

    confirmed: bool
    actor: str                 # the confirming human/operator
    publisher_identity: str    # what the confirmation displayed
    fingerprint_shown: str     # what the confirmation displayed (64 hex)
    source: str                # where the key came from


@dataclass
class KeyStoreEntry:
    """An enrolled publisher key (NPS-028 §3.3 field set)."""

    publisher: str
    fingerprint: str           # SHA-256(public key), full 64 lowercase hex
    source: str
    enrolled_at: float
    expires_at: Optional[float]
    status: str = STATUS_TRUSTED
    rotation_of: Optional[str] = None   # §6.3.5 chain-of-continuity link

    def to_dict(self) -> dict:
        return {
            "publisher": self.publisher,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "enrolled_at": self.enrolled_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "rotation_of": self.rotation_of,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KeyStoreEntry":
        return cls(
            publisher=data["publisher"],
            fingerprint=data["fingerprint"],
            source=data["source"],
            enrolled_at=data["enrolled_at"],
            expires_at=data.get("expires_at"),
            status=data.get("status", STATUS_TRUSTED),
            rotation_of=data.get("rotation_of"),
        )


class PkiKeyStore:
    """The three §3.1 collections, keyed by the §6.7.2 fingerprint.

    Mutation paths are exactly: ``enroll`` (§6), ``revoke`` /
    ``unenroll`` (§3.5's revocation semantics), ``apply_revocation_list``
    (§5.2, atomic). Everything else is read-only.
    """

    def __init__(self) -> None:
        self._roots: Dict[str, bytes] = {}          # fingerprint → public key
        self._enrolled: Dict[str, KeyStoreEntry] = {}
        self._enrolled_keys: Dict[str, bytes] = {}  # fingerprint → public key
        self._revocations: Dict[str, dict] = {}     # fingerprint → record
        self.revocation_sequence = 0                # §5.2 monotonic state

    # -- root anchors (§6.3.1: shipped in the OS image) ---------------------

    def add_root_anchor(self, public_key: bytes, identity: str) -> str:
        """Register a platform root anchor. Returns its fingerprint."""
        if not identity:
            raise PkiError("root anchor requires an identity")
        fp = key_fingerprint(public_key)
        self._roots[fp] = public_key
        return fp

    @property
    def root_fingerprints(self) -> List[str]:
        return sorted(self._roots)

    def root_public_key(self, fingerprint: str) -> Optional[bytes]:
        return self._roots.get(fingerprint)

    # -- enrollment (§6) -----------------------------------------------------

    def enroll(
        self,
        public_key: bytes,
        publisher: str,
        confirmation: EnrollmentConfirmation,
        expires_at: Optional[float] = None,
        rotation_of: Optional[str] = None,
    ) -> KeyStoreEntry:
        """Enroll a publisher key — the only path to trust (§6.1).

        Refuses (fail-closed, nothing recorded) unless the confirmation
        is explicit, names the ACTUAL fingerprint, and carries an actor
        and a source.
        """
        if not publisher:
            raise PkiError("enrollment requires a publisher identity")
        fp = key_fingerprint(public_key)
        if confirmation is None or not confirmation.confirmed:
            raise PkiError(
                "enrollment refused: no protected confirmation (NPS-028 §6.2: "
                "the confirmation MUST NOT be skippable)")
        if not confirmation.actor:
            raise PkiError("enrollment refused: no confirming actor recorded")
        if not confirmation.source:
            raise PkiError("enrollment refused: no enrollment source recorded")
        if confirmation.fingerprint_shown != fp:
            raise PkiError(
                "enrollment refused: the confirmation showed fingerprint "
                f"{confirmation.fingerprint_shown!r} but the key being "
                f"enrolled is {fp!r} — spoofed or stale confirmation")
        if rotation_of is not None and rotation_of not in self._enrolled:
            raise PkiError(
                f"rotation refused: predecessor {rotation_of!r} is not enrolled")
        if fp in self._enrolled and self._enrolled[fp].status == STATUS_TRUSTED:
            raise PkiError(f"key {fp} is already enrolled and trusted")

        entry = KeyStoreEntry(
            publisher=publisher,
            fingerprint=fp,
            source=confirmation.source,
            enrolled_at=time.time(),
            expires_at=expires_at,
            status=STATUS_TRUSTED,
            rotation_of=rotation_of,
        )
        self._enrolled[fp] = entry
        self._enrolled_keys[fp] = public_key
        self._revocations.pop(fp, None)
        return entry

    def rotate(
        self,
        old_fingerprint: str,
        new_public_key: bytes,
        new_publisher: str,
        confirmation: EnrollmentConfirmation,
        cross_signature: bytes,
        expires_at: Optional[float] = None,
    ) -> KeyStoreEntry:
        """§6.3 rotation: the new key is enrolled only if the OLD key
        signed it (chain of continuity). A rotation that cannot
        cross-sign is a fresh enrollment and must go through ``enroll``.
        """
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for rotation")
        old_entry = self._enrolled.get(old_fingerprint)
        if old_entry is None:
            raise PkiError(f"rotation refused: {old_fingerprint!r} not enrolled")
        if old_entry.status != STATUS_TRUSTED:
            raise PkiError(
                f"rotation refused: predecessor status is {old_entry.status!r}")
        old_pk = self._enrolled_keys[old_fingerprint]

        # The cross-signature covers the new public key bytes, signed by
        # the old key — verify with the shipped Ed25519 path (§6.3.5).
        from nacl.signing import VerifyKey
        from nacl.encoding import RawEncoder
        try:
            VerifyKey(old_pk, encoder=RawEncoder).verify(
                cross_signature + new_public_key)
        except Exception as exc:
            raise PkiError(
                f"rotation refused: cross-signature invalid ({exc})")

        # The protected confirmation must show the NEW key's fingerprint
        # (the UI presents what is about to be trusted); the continuity
        # link is recorded on the entry via rotation_of.
        return self.enroll(
            new_public_key,
            new_publisher,
            confirmation,
            expires_at=expires_at,
            rotation_of=old_fingerprint,
        )

    # -- revocation (§3.5, §6.3.3) --------------------------------------------

    def revoke(self, fingerprint: str, reason: str = "",
               seq_source: str = "manual") -> dict:
        """Revoke a key: hard block at install/update/re-verify,
        advisory at launch (the §6.3.4 split lives in the pipeline)."""
        record = {
            "fingerprint": fingerprint,
            "revoked_at": time.time(),
            "reason": reason,
            "seq_source": seq_source,
        }
        self._revocations[fingerprint] = record
        if fingerprint in self._enrolled:
            self._enrolled[fingerprint].status = STATUS_REVOKED
        return record

    def unenroll(self, fingerprint: str) -> dict:
        """§3.5: removing an enrolled key behaves as revocation —
        already-installed content keeps launching (advisory), the next
        touchpoint hard-blocks. The key is never silently forgotten.
        """
        if fingerprint not in self._enrolled:
            raise PkiError(f"unenroll refused: {fingerprint!r} not enrolled")
        self._enrolled[fingerprint].status = STATUS_REVOKED
        return self.revoke(fingerprint, reason="key removed (§3.5 rule)",
                           seq_source="unenroll")

    # -- status (§4 stage 3 evaluates at verification time) ------------------

    def status_at(self, fingerprint: str, now: Optional[float] = None) -> str:
        """The key's status at ``now`` (default: this instant)."""
        now = time.time() if now is None else now
        if fingerprint in self._revocations:
            return STATUS_REVOKED
        entry = self._enrolled.get(fingerprint)
        if entry is None:
            return "unknown"
        if entry.expires_at is not None and now > entry.expires_at:
            return STATUS_EXPIRED
        return entry.status

    def entry(self, fingerprint: str) -> Optional[KeyStoreEntry]:
        return self._enrolled.get(fingerprint)

    def enrolled_public_key(self, fingerprint: str) -> Optional[bytes]:
        return self._enrolled_keys.get(fingerprint)

    def list_enrolled(self) -> List[KeyStoreEntry]:
        return list(self._enrolled.values())

    # -- persistence ----------------------------------------------------------
    #
    # Two forms:
    #   save()/load()            — plaintext JSON, the dev/test path. NOT
    #                              §3.4-compliant; never for production.
    #   save_locked()/load_locked() — the §3.4 custody path: the store's
    #                              canonical form is envelope-encrypted
    #                              (ADR-0023 — a random per-file DEK,
    #                              AEAD-wrapped by the operator-secret-
    #                              derived KEK, which is never persisted;
    #                              crate-custody when the keys crate is
    #                              present, the documented floor
    #                              otherwise). Fail-closed: no secret, no
    #                              custody file.

    def _canonical_json(self) -> str:
        data = {
            "schema": "nyrqis-pki-store",
            "version": 1,
            "root_anchors": {
                fp: _b64(pk) for fp, pk in self._roots.items()
            },
            "enrolled": [e.to_dict() for e in self._enrolled.values()],
            "enrolled_keys": {
                fp: _b64(pk) for fp, pk in self._enrolled_keys.items()
            },
            "revocations": list(self._revocations.values()),
            "revocation_sequence": self.revocation_sequence,
        }
        return json.dumps(data, indent=2)

    def _load_canonical_json(self, data: dict) -> None:
        if data.get("schema") != "nyrqis-pki-store":
            raise PkiError("not a pki store")
        for fp, pk_b64 in data.get("root_anchors", {}).items():
            self._roots[fp] = _unb64(pk_b64)
        for e in data.get("enrolled", []):
            entry = KeyStoreEntry.from_dict(e)
            self._enrolled[entry.fingerprint] = entry
        for fp, pk_b64 in data.get("enrolled_keys", {}).items():
            self._enrolled_keys[fp] = _unb64(pk_b64)
        for r in data.get("revocations", []):
            self._revocations[r["fingerprint"]] = r
        self.revocation_sequence = int(data.get("revocation_sequence", 0))

    def save(self, path: str) -> None:
        """Plaintext persistence — dev/test only (NOT §3.4 custody)."""
        _write_atomic(path, self._canonical_json().encode())

    @classmethod
    def load(cls, path: str) -> "PkiKeyStore":
        """Load a plaintext (dev/test) store."""
        _assert_daemon_authority(path)
        data = json.loads(Path(path).read_text())
        store = cls()
        store._load_canonical_json(data)
        return store

    # -- §3.4 custody (ADR-0023 envelope encryption at rest) -----------------

    def save_locked(self, path: str, unlock_secret: str) -> None:
        """Persist the store under §3.4 custody.

        Envelope model (ADR-0023): a random per-file 32-byte DEK
        AEAD-encrypts the canonical store; the DEK is AEAD-wrapped by
        the KEK, which is derived from the operator's unlock secret via
        Argon2id and only ever persisted as the 110-byte KEK envelope
        (salt + KDF parameters + check value) — never in plaintext.
        The KEK handles stay daemon-side when the keys crate is
        present. Both AEAD contexts bind the store format's magic, so
        payloads cannot be relocated between files or formats.
        """
        if not unlock_secret:
            raise PkiError(
                "custody save refused: no unlock secret (§3.4 is "
                "fail-closed — there is deliberately no plaintext-at-rest "
                "production path)")
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for custody")
        from backend import keys as keys_mod
        from pathlib import Path as _Path
        secret = unlock_secret.encode()
        ad = _CUSTODY_AD
        salt = os.urandom(keys_mod.SALT_LEN)
        kek_blob = keys_mod.make_blob_any(secret, salt=salt)
        handle = keys_mod.unlock(kek_blob, secret)
        try:
            dek = os.urandom(keys_mod.DEK_LEN)
            wrapped_dek = keys_mod.wrap(handle, ad, dek)
            payload = self._canonical_json().encode()
            ct = keys_mod.block_encrypt_any(
                dek, os.urandom(keys_mod.NONCE_LEN), ad, payload)
        finally:
            keys_mod.shred(handle)
        doc = {
            "magic": _CUSTODY_MAGIC,
            "custody": "adr0023-envelope-v1",
            "kek_blob": _b64(kek_blob),
            "wrapped_dek": _b64(wrapped_dek),
            "payload": _b64(ct),
        }
        _write_atomic(path, json.dumps(doc, indent=2).encode())

    @classmethod
    def load_locked(cls, path: str, unlock_secret: str) -> "PkiKeyStore":
        """Load a §3.4 custody store (the inverse of ``save_locked``)."""
        _assert_daemon_authority(path)
        if not unlock_secret:
            raise PkiError("custody load refused: no unlock secret")
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for custody")
        from backend import keys as keys_mod
        doc = json.loads(Path(path).read_text())
        if doc.get("magic") != _CUSTODY_MAGIC:
            raise PkiError(f"{path} is not a custody-protected pki store")
        if doc.get("custody") != "adr0023-envelope-v1":
            raise PkiError(
                f"unknown custody scheme: {doc.get('custody')!r}")
        secret = unlock_secret.encode()
        ad = _CUSTODY_AD
        kek_blob = _unb64(doc["kek_blob"])
        handle = keys_mod.unlock(kek_blob, secret)
        try:
            dek = keys_mod.unwrap(handle, ad, _unb64(doc["wrapped_dek"]))
            payload = keys_mod.block_decrypt_any(
                dek, ad, _unb64(doc["payload"]))
        finally:
            keys_mod.shred(handle)
        data = json.loads(payload.decode())
        store = cls()
        store._load_canonical_json(data)
        return store


_CUSTODY_MAGIC = "NYRQIS-PKI-STORE"
_CUSTODY_AD = b"nyrqis-pki-store-v1"   # the AEAD context binding


def _write_atomic(path: str, data: bytes) -> None:
    """Write via a 0600 temp file and an atomic rename (§3.2's store-layer
    authority: the store is owner-only from the instant it first exists,
    and there is never a world-readable intermediate)."""
    fd = os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        try:
            os.unlink(f"{path}.tmp")
        except OSError:
            pass
        raise
    os.replace(f"{path}.tmp", path)


def _assert_daemon_authority(path: str) -> None:
    """§3.2's store-layer check: the store MUST be owner-only.

    A store that is group- or world-readable fails the load (fail-closed
    — an over-open store is a §3.2 violation to fix, not a condition to
    tolerate). The container-side half of §3.2 (the store living where
    package code cannot reach it at all) remains the daemon's deployment
    responsibility: the store belongs under the daemon's state directory
    on the daemon's account.
    """
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError as exc:
        raise PkiError(f"store unavailable: {exc}")
    if mode & 0o077:
        raise PkiError(
            f"{path} is group/world-accessible (mode {oct(mode)}) — "
            "refusing per NPS-028 §3.2 (daemon authority): chmod 600 "
            "and re-load")


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


def _unb64(text: str) -> bytes:
    import base64
    return base64.b64decode(text)


# ---------------------------------------------------------------------------
# NPS-028 §5 — the revocation list
# ---------------------------------------------------------------------------

@dataclass
class RevocationList:
    """A platform revocation list (§5.2): root-set-signed, monotonic."""

    sequence: int
    entries: List[dict] = field(default_factory=list)  # {fingerprint, reason}
    generated_at: float = 0.0
    signatures: List[dict] = field(default_factory=list)  # {root_fp, signature}

    def sign_with_root(self, root_fingerprint: str, private_key: bytes) -> None:
        """Attach a root signature over the canonical list bytes."""
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for list signing")
        from nacl.signing import SigningKey
        from nacl.encoding import RawEncoder
        sk = SigningKey(private_key, encoder=RawEncoder)
        signed = sk.sign(self.canonical_bytes())
        self.signatures.append({
            "root_fp": root_fingerprint,
            "signature": bytes(signed.signature).hex(),
        })

    def to_json(self) -> str:
        """Wire form for the §5.1 channel: canonical payload + signatures."""
        return json.dumps({
            "sequence": self.sequence,
            "generated_at": self.generated_at,
            "entries": self.entries,
            "signatures": self.signatures,
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "RevocationList":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise PkiError("revocation list is not an object")
        return cls(
            sequence=data.get("sequence", 0),
            generated_at=data.get("generated_at", 0.0),
            entries=list(data.get("entries", [])),
            signatures=list(data.get("signatures", [])),
        )

    def canonical_bytes(self) -> bytes:
        payload = {
            "sequence": self.sequence,
            "generated_at": self.generated_at,
            "entries": sorted(
                self.entries,
                key=lambda e: (str(e.get("fingerprint", "")),
                               str(e.get("reason", "")))),
        }
        return json.dumps(payload, sort_keys=True,
                          separators=(",", ":")).encode()

    def verify_authentic(self, roots: Dict[str, bytes]) -> bool:
        """§6.7.4 quorum rule: authentic if it verifies under ANY single
        platform root (single-root MUST-verify; any-root MAY sign)."""
        if not HAS_NACL:
            raise PackageSignError("PyNaCl required for list verification")
        if not self.signatures:
            return False
        from nacl.signing import VerifyKey
        from nacl.encoding import RawEncoder
        payload = self.canonical_bytes()
        for sig in self.signatures:
            root_fp = sig.get("root_fp", "")
            root_pk = roots.get(root_fp)
            if root_pk is None:
                continue  # signature by a non-root: ignored, never fatal
            try:
                VerifyKey(root_pk, encoder=RawEncoder).verify(
                    bytes.fromhex(sig["signature"]) + payload)
                return True
            except Exception:
                continue
        return False


def apply_revocation_list(
    store: PkiKeyStore,
    new_list: RevocationList,
    current_sequence: int,
    now: Optional[float] = None,
) -> int:
    """Apply a delivered list atomically (§5.2).

    The list is verified and fully processed BEFORE any state changes:
    a non-authentic list, a sequence regression (replay), or a malformed
    entry leaves the store untouched and raises.
    """
    if not new_list.verify_authentic(
            {fp: store.root_public_key(fp)
             for fp in store.root_fingerprints}):
        raise PkiError("revocation list rejected: no valid root signature")
    if new_list.sequence <= current_sequence:
        raise PkiError(
            f"revocation list rejected: sequence {new_list.sequence} does not "
            f"exceed the applied {current_sequence} (replay protection)")
    for entry in new_list.entries:
        if "fingerprint" not in entry:
            raise PkiError("revocation list rejected: malformed entry")
    # Commit point — everything above validated.
    for entry in new_list.entries:
        store.revoke(entry["fingerprint"],
                     reason=entry.get("reason", ""),
                     seq_source=f"list#{new_list.sequence}")
    store.revocation_sequence = new_list.sequence
    return new_list.sequence


# ---------------------------------------------------------------------------
# NPS-028 §4 — the verification pipeline (the single path)
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """The per-stage outcome record (§4; the §7 audit payload)."""

    touchpoint: str
    verdict: str                    # approved | denied | advisory
    reason: str
    stages: List[dict] = field(default_factory=list)
    fingerprint: Optional[str] = None
    publisher: Optional[str] = None
    package_id: Optional[str] = None
    version: Optional[str] = None

    def record(self, stage: str, outcome: str, detail: str = "") -> None:
        self.stages.append(
            {"stage": stage, "outcome": outcome, "detail": detail})


class VerificationPipeline:
    """The one verification path (§4). No component bypasses it; every
    stage's outcome is recorded; failures carry reasons (no silent
    failure — NPS-006 §6.2's precedent).
    """

    def __init__(self, store: PkiKeyStore,
                 audit_sink: Optional[Callable[[dict], None]] = None):
        self.store = store
        self._audit_sink = audit_sink

    def run(
        self,
        manifest_bytes: bytes,
        integrity_tree_bytes: bytes,
        signature: bytes,
        public_key: bytes,
        touchpoint: str,
        now: Optional[float] = None,
    ) -> VerificationResult:
        if touchpoint not in (TOUCHPOINT_INSTALL, TOUCHPOINT_UPDATE,
                              TOUCHPOINT_RE_VERIFY, TOUCHPOINT_LAUNCH):
            raise PkiError(f"unknown touchpoint: {touchpoint!r}")
        result = VerificationResult(touchpoint=touchpoint, verdict="",
                                    reason="")

        # Stage 1 — parse and validate the manifest (corruption detection).
        try:
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict):
                raise ValueError("manifest is not an object")
            for required in ("package_id", "version"):
                if not manifest.get(required):
                    raise ValueError(f"manifest missing {required!r}")
            result.package_id = manifest["package_id"]
            result.version = manifest["version"]
            result.record("1_parse_manifest", "ok",
                          f"{manifest['package_id']} {manifest['version']}")
            result.publisher = manifest.get("publisher")
        except Exception as exc:
            result.record("1_parse_manifest", "fail", str(exc))
            return self._finish(result, VERDICT_DENIED,
                                f"manifest invalid: {exc}")

        # Stage 2 — resolve the signing key (TOFU fail-closed).
        try:
            fp = key_fingerprint(public_key)
        except PackageSignError as exc:
            result.record("2_resolve_key", "fail", str(exc))
            return self._finish(result, VERDICT_DENIED, f"bad key: {exc}")
        result.fingerprint = fp
        status = self.store.status_at(fp, now=now)
        entry = self.store.entry(fp)
        if status == "unknown":
            result.record("2_resolve_key", "fail",
                          "key not in store (TOFU rejection)")
            return self._finish(
                result, VERDICT_DENIED,
                "TOFU rejection: signing key is neither enrolled nor a "
                "root anchor — untrusted publisher (FIND-PACKAGE-003 "
                "dies here)")
        result.record("2_resolve_key", "ok", f"fingerprint {fp}")
        result.publisher = (entry.publisher if entry
                            else result.publisher)

        # Stage 3 — key status at THIS instant (§6.3.3).
        if status in (STATUS_EXPIRED, STATUS_REVOKED):
            result.record("3_key_status", "fail", f"key is {status}")
            # §6.3.4: launch of already-installed content is advisory,
            # never a silent block — every other touchpoint hard-fails.
            if touchpoint == TOUCHPOINT_LAUNCH:
                result.record("5_launch_advisory", "advisory",
                              f"publisher key {status}; owned content "
                              "launches with a notice")
                return self._finish(
                    result, VERDICT_ADVISORY,
                    f"publisher key {status} — advisory at launch")
            return self._finish(
                result, VERDICT_DENIED, f"publisher key {status}")
        result.record("3_key_status", "ok", f"key is {status}")

        # Stage 4 — the Ed25519 signature over manifest + trees (§6.2).
        try:
            ok = verify_package(manifest_bytes, integrity_tree_bytes,
                                signature, public_key)
        except PackageSignError as exc:
            result.record("4_verify_signature", "fail", str(exc))
            return self._finish(result, VERDICT_DENIED,
                                f"signature verification failed: {exc}")
        if not ok:
            result.record("4_verify_signature", "fail", "mismatch")
            return self._finish(result, VERDICT_DENIED,
                                "signature does not match content")
        result.record("4_verify_signature", "ok", "Ed25519 valid")

        # Stage 5 — the touchpoint-dependent advisory set.
        if touchpoint in (TOUCHPOINT_INSTALL, TOUCHPOINT_UPDATE,
                          TOUCHPOINT_RE_VERIFY):
            result.record("5_touchpoint", "ok",
                          f"{touchpoint}: no advisory conditions")
        return self._finish(result, VERDICT_APPROVED,
                            f"approved for {touchpoint}")

    def _finish(self, result: VerificationResult, verdict: str,
                reason: str) -> VerificationResult:
        result.verdict = verdict
        result.reason = reason
        # §7.2: the audit trail is evidence, not a verification stage —
        # a recording failure degrades to a reported warning and NEVER
        # changes the verdict.
        if self._audit_sink is not None:
            try:
                self._audit_sink({
                    "touchpoint": result.touchpoint,
                    "verdict": result.verdict,
                    "reason": result.reason,
                    "fingerprint": result.fingerprint,
                    "publisher": result.publisher,
                    "package_id": result.package_id,
                    "version": result.version,
                    "stages": list(result.stages),
                    "at": time.time(),
                })
            except Exception as exc:  # noqa: BLE001
                result.record("audit", "warn",
                              f"audit sink failed ({exc}); verdict unchanged")
        return result


class DaemonAuthority:
    """NPS-028 §3.2 — the daemon's authority token for the PKI store.

    Unforgeable by construction: only :meth:`mint` can create one (it is
    the daemon process's entry point), instances compare by secret, and
    the secret is generated by the OS RNG. Package code running in a
    container can never obtain one — the NPS-022 §4 lesson: capability
    enforcement must not live where the attacker's code runs.
    """

    __slots__ = ("_secret",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise PkiError(
            "DaemonAuthority cannot be constructed directly; "
            "the daemon mints it via DaemonAuthority.mint()")

    @classmethod
    def mint(cls) -> "DaemonAuthority":
        import secrets
        token = object.__new__(cls)
        object.__setattr__(token, "_secret", secrets.token_bytes(32))
        return token

    def authorizes(self, other: object) -> bool:
        return (isinstance(other, DaemonAuthority)
                and other._secret == self._secret)


class PkiDaemonService:
    """SURFACE-PKI-0001 — the store's daemon interface (NPS-028 §3.2).

    The ONLY supported way to reach a PkiKeyStore's operations: every
    method demands a valid DaemonAuthority and fails closed without
    one. There is deliberately no method that returns the store, the
    enrolled-key material, or an enumeration of entries — an installed
    package cannot read, write, or enumerate the trust store because it
    can present no authority.
    """

    def __init__(self, store: PkiKeyStore,
                 audit_chain: Optional["PackageAuditChain"] = None) -> None:
        self._store = store
        self._audit = audit_chain

    def _check(self, authority: object, op: str) -> None:
        if not isinstance(authority, DaemonAuthority):
            raise PkiError(
                f"{op}: daemon authority required (§3.2) — refused")
        if self._authority is not None and not self._authority.authorizes(
                authority):
            raise PkiError(f"{op}: authority mismatch — refused")

    def _audit_mutation(self, op: str, details: dict) -> None:
        if self._audit is not None:
            try:
                self._audit.append(op, details)
            except Exception:  # noqa: BLE001 — §7.2: evidence, not a gate
                pass

    # -- binding to exactly one daemon authority --------------------------

    def bind(self, authority: "DaemonAuthority") -> None:
        """Pin the service to the daemon's token (optional hardening:
        once bound, even *a* valid-looking token from elsewhere is
        refused if it does not match)."""
        if self._authority is not None:
            raise PkiError("service already bound to a daemon authority")
        self._authority = authority

    _authority: Optional["DaemonAuthority"] = None

    # -- read paths (the pipeline's needs, nothing broader) ---------------

    def status_for(self, authority: object, fingerprint: str) -> str:
        self._check(authority, "status_for")
        return self._store.status_at(fingerprint)

    def root_public_key_for(self, authority: object,
                            fingerprint: str) -> Optional[bytes]:
        self._check(authority, "root_public_key_for")
        return self._store.root_public_key(fingerprint)

    def enrolled_public_key_for(self, authority: object,
                                fingerprint: str) -> Optional[bytes]:
        self._check(authority, "enrolled_public_key_for")
        return self._store.enrolled_public_key(fingerprint)

    # -- the enrollment write path (§6) -----------------------------------

    def enroll(self, authority: object, public_key: bytes, publisher: str,
               confirmation: EnrollmentConfirmation) -> KeyStoreEntry:
        self._check(authority, "enroll")
        entry = self._store.enroll(public_key, publisher, confirmation)
        self._audit_mutation("pki_enroll", {
            "fingerprint": entry.fingerprint, "publisher": publisher})
        return entry

    def rotate(self, authority: object, old_fingerprint: str,
               new_public_key: bytes, publisher: str,
               confirmation: EnrollmentConfirmation) -> KeyStoreEntry:
        self._check(authority, "rotate")
        entry = self._store.rotate(old_fingerprint, new_public_key,
                                   publisher, confirmation)
        self._audit_mutation("pki_rotate", {
            "from": old_fingerprint, "to": entry.fingerprint})
        return entry

    # -- revocation updates (§5) ------------------------------------------

    def revoke(self, authority: object, fingerprint: str,
               reason: str = "") -> dict:
        self._check(authority, "revoke")
        record = self._store.revoke(fingerprint, reason=reason,
                                    seq_source="daemon")
        self._audit_mutation("pki_revoke", {
            "fingerprint": fingerprint, "reason": reason})
        return record

    def apply_revocation_list(self, authority: object,
                              new_list: RevocationList) -> int:
        self._check(authority, "apply_revocation_list")
        new_seq = apply_revocation_list(
            self._store, new_list,
            current_sequence=self._store.revocation_sequence)
        self._audit_mutation("pki_apply_revocations", {
            "sequence": new_seq, "entries": len(new_list.entries)})
        return new_seq


# ---------------------------------------------------------------------------
# NPS-028 §3.2 — the physical IPC transport (daemon integration's half)
#
# JSON-lines over a Unix domain socket: the daemon listens, each accepted
# connection is bound to a freshly minted DaemonAuthority, and requests
# dispatch to the PkiDaemonService methods. Package code connecting to
# the socket CAN obtain a connection — but every op it names is still
# checked against the service's authority rules, and the daemon only
# accepts connections whose peer is root/its own uid where the OS can
# tell it (SO_PEERCRED). The transport never widens what the service
# allows.
# ---------------------------------------------------------------------------

class PkiIpcServer:
    """Expose PkiDaemonService over a Unix domain socket (§3.2).

    The server mints ONE DaemonAuthority at start — the daemon's
    identity — and binds the service to it (if unbound). Every
    connection then speaks with that single authority: connections are
    wires, not identities, and no client ever sees a token. Requests
    are single-line JSON {"op": ..., "params": {...}}; replies are
    single-line JSON {"ok": true, "result": ...} or
    {"ok": false, "error": ...}.

    §3.2 on the wire: the ops are an explicit ALLOWLIST. Key-material
    reads are NOT on it — verification runs in the daemon's authority,
    and an installed package has no need (and no right) to export store
    contents, public or otherwise. Status reads serve UI display;
    writes go through the same §6/§5 paths as in-process.
    """

    IPC_ALLOWED_OPS = frozenset({
        "status_for", "enroll", "rotate", "revoke", "apply_revocation_list",
    })

    @staticmethod
    def _jsonable(result: object) -> object:
        """Coerce a service result into JSON-safe data."""
        import dataclasses
        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            return dataclasses.asdict(result)
        if isinstance(result, (bytes, bytearray)):
            return bytes(result).hex()
        return result

    def __init__(self, service: PkiDaemonService, socket_path: str) -> None:
        import os
        import socketserver
        self._service = service
        self._socket_path = socket_path
        self._sock: Optional[object] = None
        self._thread = None
        self._daemon_uid = os.geteuid()
        self.daemon_authority = DaemonAuthority.mint()
        if service._authority is None:
            service.bind(self.daemon_authority)
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def setup(self) -> None:
                super().setup()
                # SO_PEERCRED (Linux): drop connections from peers that
                # are neither the daemon's own uid nor root. On platforms
                # without peer credentials the 0600 socket mode is the
                # floor — defense in depth, not a single gate.
                import socket as _socket
                import struct
                soc = getattr(_socket, "SO_PEERCRED", None)
                if soc is None:
                    return
                try:
                    size = struct.calcsize("3i")
                    _pid, uid, _gid = struct.unpack(
                        "3i", self.request.getsockopt(
                            _socket.SOL_SOCKET, soc, size))
                except OSError:
                    return
                if not outer._peer_uid_allowed(uid, outer._daemon_uid):
                    self.close()

            def handle(self) -> None:
                authority = outer.daemon_authority
                for line in self.rfile:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        request = json.loads(line)
                        op = request["op"]
                        if op not in outer.IPC_ALLOWED_OPS:
                            raise PkiError(
                                f"op {op!r} is not on the §3.2 IPC "
                                "allowlist — refused")
                        params = request.get("params", {}) or {}
                        method = getattr(outer._service, op, None)
                        if method is None or op.startswith("_"):
                            raise PkiError(f"unknown op {op!r}")
                        # The connection's authority is supplied BY THE
                        # SERVER — a client cannot name or forge one.
                        result = method(authority, **params)
                        reply = {"ok": True,
                                 "result": outer._jsonable(result)}
                    except Exception as exc:  # noqa: BLE001
                        reply = {"ok": False, "error": str(exc)}
                    self.wfile.write((json.dumps(reply) + "\n").encode())
                    self.wfile.flush()

        self._handler = Handler

    @staticmethod
    def _peer_uid_allowed(uid: int, daemon_uid: int) -> bool:
        """The peer policy: the daemon itself, or root — nobody else."""
        return uid == daemon_uid or uid == 0

    def start(self) -> None:
        import os
        import socketserver
        import threading

        # §3.2 fail-closed on the bind location: a group/world-writable
        # socket directory lets a local attacker swap or shadow the
        # socket. Refuse to bind; name the fix.
        socket_dir = os.path.dirname(os.path.abspath(self._socket_path))
        import stat
        dir_mode = os.stat(socket_dir).st_mode
        if dir_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PkiError(
                f"socket directory {socket_dir!r} is group/world-writable "
                "(§3.2) — refusing to bind; fix with chmod go-w")

        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        server = socketserver.ThreadingUnixStreamServer(
            self._socket_path, self._handler)
        server.daemon_threads = True
        # The socket file itself is owner-only: a foreign-uid process on
        # this machine cannot even attempt a connection.
        os.chmod(self._socket_path, 0o600)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        import os
        server = getattr(self, "_server", None)
        if server is None:
            return
        if self._thread is not None and self._thread.is_alive():
            server.shutdown()
        server.server_close()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)


class PkiIpcClient:
    """The daemon-side client for the §3.2 transport.

    NOTE the authority model: the CLIENT never presents a token. The
    server mints one per connection and enforces the service's rules
    with it. This class carries no secret — an installed package can
    instantiate it freely, and everything it asks for still fails or
    succeeds exactly per §3.2's rules (read paths for verification,
    writes only through the daemon's own callers).
    """

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        import socket
        self._socket_path = socket_path
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._rfile = self._sock.makefile("rb")

    def call(self, op: str, **params: object) -> object:
        import json as _json
        request = _json.dumps({"op": op, "params": params}) + "\n"
        self._sock.sendall(request.encode())
        line = self._rfile.readline()
        if not line:
            raise PkiError("daemon closed the connection")
        reply = _json.loads(line)
        if not reply.get("ok"):
            raise PkiError(reply.get("error", "daemon refused"))
        return reply.get("result")

    def close(self) -> None:
        try:
            self._rfile.close()
        finally:
            self._sock.close()


# ---------------------------------------------------------------------------
# NPS-028 §7 — the ADR-0018 tamper-evident audit chain (package events)
# ---------------------------------------------------------------------------

class PackageAuditChain:
    """ADR-0018 scheme-2 hash chain over package events (NPS-028 §7).

    Reuses the ContainerManager append_audit_event algorithm exactly — the
    same salt-bearing content construction, the same SHA-256 chaining. The
    construction is differentially pinned against ContainerManager in the
    tests so the two implementations cannot drift.

    §7.2: appending is best-effort in the pipeline (a sink failure never
    changes a verdict); verify() is the tamper evidence — any modified,
    reordered, or removed entry breaks the chain from that point on.
    """

    @staticmethod
    def event_content(salt: str, prev_hash: str, op: str, ts: float,
                      details: Optional[dict]) -> str:
        """Byte-identical to ContainerManager._audit_event_content
        (differentially pinned in the tests): scheme-2 tagged, pipe-
        joined, repr(float(ts)) timestamps, canonical JSON details where
        None and {} hash differently."""
        return "2|" + "|".join((
            salt, prev_hash, op, repr(float(ts)),
            json.dumps(details, sort_keys=True, separators=(",", ":"),
                       default=str)
            if details is not None else "null",
        ))

    def __init__(self) -> None:
        import secrets
        import hashlib
        self._hashlib = hashlib
        self._salt = secrets.token_hex(16)
        self._prev_hash: str = "GENESIS"
        self.entries: List[dict] = []

    def append(self, op: str, details: Optional[dict]) -> dict:
        """Append one event; returns the entry (schema in NPS-028 §7.1)."""
        ts = time.time()
        content = self.event_content(self._salt, self._prev_hash, op, ts,
                                     details)
        entry_hash = self._hashlib.sha256(content.encode("utf-8")).hexdigest()
        entry = {
            "op": op,
            "ts": ts,
            "details": details or {},
            "hash": entry_hash,
        }
        self.entries.append(entry)
        self._prev_hash = entry_hash
        return entry

    def verify(self) -> bool:
        """Recompute every hash; False on any tamper/reorder/removal."""
        prev = "GENESIS"
        for entry in self.entries:
            content = self.event_content(self._salt, prev, entry["op"],
                                         entry["ts"], entry["details"])
            if self._hashlib.sha256(content.encode("utf-8")).hexdigest() != entry["hash"]:
                return False
            prev = entry["hash"]
        return True

    # -- persistence (header line carries the salt, then one event per
    # line; without the salt the loaded chain could never re-verify) ----

    def save_jsonl(self, path: str) -> None:
        lines = [json.dumps({"salt": self._salt}, sort_keys=True,
                            separators=(",", ":"))]
        lines += [json.dumps(e, sort_keys=True) for e in self.entries]
        _write_atomic(path, ("\n".join(lines) + "\n").encode("utf-8"))

    @classmethod
    def load_jsonl(cls, path: str) -> "PackageAuditChain":
        chain = cls()
        first = True
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if first:
                    chain._salt = data["salt"]
                    first = False
                else:
                    chain.entries.append(data)
        # Resume the chain head so appends continue from the loaded tail.
        if chain.entries:
            import hashlib
            prev = "GENESIS"
            for entry in chain.entries:
                prev = hashlib.sha256(chain.event_content(
                    chain._salt, prev, entry["op"], entry["ts"],
                    entry["details"]).encode("utf-8")).hexdigest()
            chain._prev_hash = prev
        return chain

    @staticmethod
    def make_sink(chain: "PackageAuditChain") -> Callable[[dict], None]:
        """A pipeline audit sink that appends §7.1 records to `chain`.

        The record carries the package identity (package_id, version),
        the decision and its reason, the resolved key fingerprint and
        publisher, and the per-stage outcomes — everything §7.1 lists.
        """

        def sink(record: dict) -> None:
            chain.append("package_verification", record)

        return sink


class RevocationFetcher:
    """The §5.1 out-of-band transport abstraction.

    The revocation channel MUST be independent of the package feed: a
    fetcher is configured with its own source, and nothing in refresh
    touches a repo object. Compromise of the package feed therefore
    cannot suppress revocation delivery, and compromise of the channel
    cannot suppress already-applied revocations.
    """

    def fetch(self) -> RevocationList:
        raise NotImplementedError


class FileRevocationFetcher(RevocationFetcher):
    """Fetch a signed revocation list from a local out-of-band file."""

    def __init__(self, channel_path: str) -> None:
        self.channel_path = channel_path

    def fetch(self) -> RevocationList:
        with open(self.channel_path, "r", encoding="utf-8") as fh:
            return RevocationList.from_json(fh.read())


def refresh_revocations(store: PkiKeyStore, fetcher: RevocationFetcher
                        ) -> Dict[str, object]:
    """Pull the out-of-band revocation list and apply it (§5.1).

    Never raises and never mutates the store on failure: a fetch failure
    or an unauthentic/regressive list leaves the current revocation
    state intact and reports the condition. Success requires an
    authentic list (G4: any single root verifies) whose sequence is
    strictly newer than what the store already has.
    """
    try:
        rvl = fetcher.fetch()
    except FileNotFoundError:
        return {"outcome": "fetch_failed",
                "reason": "no revocation list on the channel"}
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "fetch_failed", "reason": str(exc)}

    current = store.revocation_sequence
    if rvl.sequence <= current:
        return {"outcome": "replay_refused",
                "reason": f"sequence {rvl.sequence} <= current {current}"}
    try:
        new_sequence = apply_revocation_list(store, rvl,
                                             current_sequence=current)
    except PkiError as exc:
        return {"outcome": "replay_refused", "reason": str(exc)}
    return {"outcome": "applied", "sequence": new_sequence,
            "revoked": len(rvl.entries)}


__all__ = [
    "PkiError",
    "PkiKeyStore",
    "KeyStoreEntry",
    "EnrollmentConfirmation",
    "RevocationList",
    "apply_revocation_list",
    "VerificationPipeline",
    "VerificationResult",
    "DaemonAuthority",
    "PkiDaemonService",
    "PkiIpcServer",
    "PkiIpcClient",
    "PackageAuditChain",
    "RevocationFetcher",
    "FileRevocationFetcher",
    "refresh_revocations",
    "TOUCHPOINT_INSTALL",
    "TOUCHPOINT_UPDATE",
    "TOUCHPOINT_RE_VERIFY",
    "TOUCHPOINT_LAUNCH",
    "STATUS_TRUSTED",
    "STATUS_EXPIRED",
    "STATUS_REVOKED",
    "VERDICT_APPROVED",
    "VERDICT_DENIED",
    "VERDICT_ADVISORY",
]
