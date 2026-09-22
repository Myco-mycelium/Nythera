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

    # -- persistence (JSON; §3.4 custody is the named next increment) --------

    def save(self, path: str) -> None:
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
        }
        tmp = f"{path}.tmp"
        Path(tmp).write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "PkiKeyStore":
        data = json.loads(Path(path).read_text())
        if data.get("schema") != "nyrqis-pki-store":
            raise PkiError(f"{path} is not a pki store")
        store = cls()
        for fp, pk_b64 in data.get("root_anchors", {}).items():
            store._roots[fp] = _unb64(pk_b64)
        for e in data.get("enrolled", []):
            entry = KeyStoreEntry.from_dict(e)
            store._enrolled[entry.fingerprint] = entry
        for fp, pk_b64 in data.get("enrolled_keys", {}).items():
            store._enrolled_keys[fp] = _unb64(pk_b64)
        for r in data.get("revocations", []):
            store._revocations[r["fingerprint"]] = r
        return store


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
                    "stages": list(result.stages),
                    "at": time.time(),
                })
            except Exception as exc:  # noqa: BLE001
                result.record("audit", "warn",
                              f"audit sink failed ({exc}); verdict unchanged")
        return result


__all__ = [
    "PkiError",
    "PkiKeyStore",
    "KeyStoreEntry",
    "EnrollmentConfirmation",
    "RevocationList",
    "apply_revocation_list",
    "VerificationPipeline",
    "VerificationResult",
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
