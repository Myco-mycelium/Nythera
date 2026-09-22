"""test_package_pki — Tests for the NPS-028 PKI implementation start.

Covers: the key store (§3), the verification pipeline (§4), the
revocation list (§5), and enrollment/rotation (§6) — all against the
decided scheme (NPS-026 v1.3.0 §6.7, AG decision log D4): the §6.7.2
fingerprint, single-root list verification, TOFU fail-closed resolution,
and the §6.3.4 advisory/block split.

References:
    - NPS-028: Package PKI Implementation Surface
    - NPS-026 v1.3.0 §6.7: the decided concrete scheme
    - backend/package_pki.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

MANIFEST = json.dumps({
    "package_id": "acme-app",
    "version": "1.0.0",
    "publisher": "acme",
}).encode()
TREE = b"merkle-root-bytes"


def _sign(manifest: bytes = MANIFEST, tree: bytes = TREE):
    from backend.package_signing import SigningKeypair, sign_package
    kp = SigningKeypair.generate()
    sig = sign_package(manifest, tree, kp)
    return kp, sig


def _cross_sign(signing_key, payload: bytes) -> bytes:
    """Ed25519 signature over arbitrary bytes (for cross-signing)."""
    from backend.package_signing import SigningKey, RawEncoder
    sk = SigningKey(signing_key.private_key, encoder=RawEncoder)
    return bytes(sk.sign(payload).signature)


def _confirmation(fingerprint: str, actor: str = "operator",
                  publisher: str = "acme", source: str = "sideload",
                  confirmed: bool = True):
    from backend.package_pki import EnrollmentConfirmation
    return EnrollmentConfirmation(
        confirmed=confirmed,
        actor=actor,
        publisher_identity=publisher,
        fingerprint_shown=fingerprint,
        source=source,
    )


class TestPkiKeyStore(unittest.TestCase):
    """NPS-028 §3 — the key store."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.store = PkiKeyStore()
        self.root = SigningKeypair.generate()
        self.store.add_root_anchor(self.root.public_key, "platform-root")

    def test_enrollment_records_the_3_3_fields(self):
        kp, _sig = _sign()
        entry = self.store.enroll(
            kp.public_key, "acme",
            _confirmation(kp.fingerprint, publisher="acme"))
        self.assertEqual(entry.publisher, "acme")
        self.assertEqual(entry.fingerprint, kp.fingerprint)
        self.assertEqual(len(entry.fingerprint), 64)
        self.assertEqual(entry.source, "sideload")
        self.assertEqual(entry.status, "trusted")
        self.assertGreater(entry.enrolled_at, 0)

    def test_enrollment_requires_a_confirmation(self):
        kp, _ = _sign()
        with self.assertRaises(Exception):
            self.store.enroll(kp.public_key, "acme", None)
        with self.assertRaises(Exception):
            self.store.enroll(kp.public_key, "acme",
                              _confirmation(kp.fingerprint, confirmed=False))

    def test_enrollment_refuses_a_spoofed_confirmation(self):
        """A confirmation showing a DIFFERENT fingerprint enrolls nothing."""
        kp, _ = _sign()
        with self.assertRaises(Exception):
            self.store.enroll(
                kp.public_key, "acme",
                _confirmation("f" * 64))  # UI showed some other key
        self.assertEqual(self.store.status_at(kp.fingerprint), "unknown")

    def test_enrollment_requires_actor_and_source(self):
        kp, _ = _sign()
        with self.assertRaises(Exception):
            self.store.enroll(
                kp.public_key, "acme",
                _confirmation(kp.fingerprint, actor=""))
        with self.assertRaises(Exception):
            self.store.enroll(
                kp.public_key, "acme",
                _confirmation(kp.fingerprint, source=""))

    def test_double_enrollment_of_a_trusted_key_refused(self):
        kp, _ = _sign()
        self.store.enroll(kp.public_key, "acme", _confirmation(kp.fingerprint))
        with self.assertRaises(Exception):
            self.store.enroll(kp.public_key, "acme",
                              _confirmation(kp.fingerprint))

    def test_expiry_evaluated_at_verification_time(self):
        kp, _ = _sign()
        self.store.enroll(kp.public_key, "acme",
                          _confirmation(kp.fingerprint),
                          expires_at=1000.0)
        self.assertEqual(self.store.status_at(kp.fingerprint, now=999.0),
                         "trusted")
        self.assertEqual(self.store.status_at(kp.fingerprint, now=1001.0),
                         "expired")

    def test_unenroll_behaves_as_revocation(self):
        kp, _ = _sign()
        self.store.enroll(kp.public_key, "acme", _confirmation(kp.fingerprint))
        self.store.unenroll(kp.fingerprint)
        self.assertEqual(self.store.status_at(kp.fingerprint), "revoked")
        with self.assertRaises(Exception):
            self.store.unenroll("a" * 64)  # never enrolled

    def test_store_roundtrip(self):
        kp, _ = _sign()
        self.store.enroll(kp.public_key, "acme", _confirmation(kp.fingerprint))
        self.store.revoke("b" * 64, reason="test")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "pki.json")
            self.store.save(path)
            loaded = type(self.store).load(path)
        self.assertEqual(loaded.status_at(kp.fingerprint), "trusted")
        self.assertEqual(loaded.status_at("b" * 64), "revoked")
        self.assertEqual(loaded.root_fingerprints,
                         [self.root.fingerprint])


class TestRotation(unittest.TestCase):
    """NPS-028 §6.3 / NPS-026 §6.3.5 — cross-signed rotation."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.store = PkiKeyStore()
        self.old_key = SigningKeypair.generate()
        self.store.enroll(self.old_key.public_key, "acme",
                          _confirmation(self.old_key.fingerprint))

    def test_rotation_with_valid_cross_signature(self):
        from backend.package_signing import SigningKeypair
        new_key = SigningKeypair.generate()
        cross = _cross_sign(self.old_key, new_key.public_key)
        entry = self.store.rotate(
            self.old_key.fingerprint, new_key.public_key, "acme",
            _confirmation(new_key.fingerprint), cross)
        self.assertEqual(entry.rotation_of, self.old_key.fingerprint)
        self.assertEqual(self.store.status_at(new_key.fingerprint), "trusted")

    def test_rotation_refuses_a_forged_cross_signature(self):
        from backend.package_signing import SigningKeypair
        new_key = SigningKeypair.generate()
        other = SigningKeypair.generate()  # not the enrolled old key
        forged = _cross_sign(other, new_key.public_key)
        with self.assertRaises(Exception):
            self.store.rotate(
                self.old_key.fingerprint, new_key.public_key, "acme",
                _confirmation(new_key.fingerprint), forged)
        self.assertEqual(self.store.status_at(new_key.fingerprint), "unknown")

    def test_rotation_requires_an_enrolled_predecessor(self):
        from backend.package_signing import SigningKeypair
        new_key = SigningKeypair.generate()
        with self.assertRaises(Exception):
            self.store.rotate(
                "c" * 64, new_key.public_key, "acme",
                _confirmation(new_key.fingerprint), b"\x00" * 64)


class TestRevocationList(unittest.TestCase):
    """NPS-028 §5 — distribution, §6.7.4 quorum rule."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore, RevocationList
        from backend.package_signing import SigningKeypair
        self.store = PkiKeyStore()
        self.root1 = SigningKeypair.generate()
        self.root2 = SigningKeypair.generate()
        self.store.add_root_anchor(self.root1.public_key, "root-1")
        self.store.add_root_anchor(self.root2.public_key, "root-2")
        self.victim, _ = _sign()
        self.store.enroll(self.victim.public_key, "acme",
                          _confirmation(self.victim.fingerprint))
        self.list = RevocationList(sequence=1, generated_at=1.0, entries=[
            {"fingerprint": self.victim.fingerprint, "reason": "compromise"}])

    def test_single_root_signature_suffices(self):
        """§6.7.4: authentic under ANY single platform root."""
        self.list.sign_with_root(self.root2.fingerprint, self.root2.private_key)
        self.assertTrue(self.list.verify_authentic(
            {self.root2.fingerprint: self.root2.public_key}))

    def test_unsigned_or_foreign_signed_list_rejected(self):
        self.assertFalse(self.list.verify_authentic({}))
        self.list.signatures.append({"root_fp": "f" * 64,
                                     "signature": "00" * 64})
        self.assertFalse(self.list.verify_authentic({}))

    def test_apply_revokes_and_advances_sequence(self):
        from backend.package_pki import apply_revocation_list
        self.list.sign_with_root(self.root1.fingerprint, self.root1.private_key)
        seq = apply_revocation_list(self.store, self.list, current_sequence=0)
        self.assertEqual(seq, 1)
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "revoked")

    def test_replay_refused_and_store_unchanged(self):
        from backend.package_pki import apply_revocation_list
        self.list.sign_with_root(self.root1.fingerprint, self.root1.private_key)
        # First delivery applies cleanly.
        apply_revocation_list(self.store, self.list, current_sequence=0)
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "revoked")
        # The same list again is a replay: refused, and the refused
        # application changes nothing further.
        with self.assertRaises(Exception):
            apply_revocation_list(self.store, self.list, current_sequence=1)
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "revoked")

    def test_atomicity_malformed_entry_commits_nothing(self):
        from backend.package_pki import RevocationList, apply_revocation_list
        bad = RevocationList(sequence=2, generated_at=1.0, entries=[
            {"reason": "no fingerprint field"}])  # type: ignore[dict-item]
        bad.sign_with_root(self.root1.fingerprint, self.root1.private_key)
        with self.assertRaises(Exception):
            apply_revocation_list(self.store, bad, current_sequence=0)
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "trusted")


class TestCustody(unittest.TestCase):
    """NPS-028 §3.4 — the store's at-rest custody (ADR-0023 envelope)."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.store = PkiKeyStore()
        self.root = SigningKeypair.generate()
        self.store.add_root_anchor(self.root.public_key, "platform-root")
        self.kp, _ = _sign()
        self.store.enroll(self.kp.public_key, "acme",
                          _confirmation(self.kp.fingerprint))
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-pki-custody-")
        self.path = os.path.join(self.tmpdir, "pki.locked")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_locked_roundtrip_preserves_state(self):
        self.store.save_locked(self.path, "operator-secret")
        loaded = type(self.store).load_locked(self.path, "operator-secret")
        self.assertEqual(loaded.status_at(self.kp.fingerprint), "trusted")
        self.assertEqual(loaded.root_fingerprints, [self.root.fingerprint])

    def test_locked_file_is_envelope_only(self):
        """No schema marker, no publisher data, no fingerprint in the clear."""
        self.store.save_locked(self.path, "operator-secret")
        raw = open(self.path).read()
        self.assertIn("NYRQIS-PKI-STORE", raw)          # the magic, not data
        self.assertNotIn("nyrqis-pki-store", raw)       # schema marker absent
        self.assertNotIn("acme", raw)
        self.assertNotIn(self.kp.fingerprint, raw)
        self.assertNotIn("trusted", raw)

    def test_wrong_unlock_secret_refused(self):
        self.store.save_locked(self.path, "operator-secret")
        with self.assertRaises(Exception):
            type(self.store).load_locked(self.path, "wrong-secret")

    def test_tampered_payload_refused(self):
        self.store.save_locked(self.path, "operator-secret")
        doc = json.loads(open(self.path).read())
        blob = bytearray(__import__("base64").b64decode(doc["payload"]))
        blob[0] ^= 0x01
        doc["payload"] = __import__("base64").b64encode(bytes(blob)).decode()
        with open(self.path, "w") as fh:
            json.dump(doc, fh)
        with self.assertRaises(Exception):
            type(self.store).load_locked(self.path, "operator-secret")

    def test_custody_requires_a_secret(self):
        with self.assertRaises(Exception):
            self.store.save_locked(self.path, "")
        with self.assertRaises(Exception):
            type(self.store).load_locked(self.path, "")

    def test_plaintext_save_stays_the_marked_dev_path(self):
        """save()/load() keep working for tests — visibly not custody."""
        plain = os.path.join(self.tmpdir, "pki.plain")
        self.store.save(plain)
        raw = open(plain).read()
        self.assertIn("nyrqis-pki-store", raw)
        self.assertNotIn("NYRQIS-PKI-STORE", raw)


class TestDaemonAuthority(unittest.TestCase):
    """NPS-028 §3.2 — the store layer's daemon-authority enforcement."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.store = PkiKeyStore()
        self.root = SigningKeypair.generate()
        self.store.add_root_anchor(self.root.public_key, "platform-root")
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-pki-authority-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_saved_stores_are_owner_only(self):
        for name, save in (
                ("plain", lambda p: self.store.save(p)),
                ("locked", lambda p: self.store.save_locked(p, "s"))):
            path = os.path.join(self.tmpdir, name)
            save(path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, name)
            self.assertFalse(os.path.exists(f"{path}.tmp"),
                             f"{name}: no temp file may persist")

    def test_load_refuses_a_group_readable_store(self):
        path = os.path.join(self.tmpdir, "open-plain")
        self.store.save(path)
        os.chmod(path, 0o644)
        with self.assertRaises(Exception) as ctx:
            type(self.store).load(path)
        self.assertIn("§3.2", str(ctx.exception))

    def test_load_locked_refuses_a_group_readable_store(self):
        path = os.path.join(self.tmpdir, "open-locked")
        self.store.save_locked(path, "secret")
        os.chmod(path, 0o664)
        with self.assertRaises(Exception) as ctx:
            type(self.store).load_locked(path, "secret")
        self.assertIn("§3.2", str(ctx.exception))

    def test_chmod_back_to_0600_restores_the_load(self):
        path = os.path.join(self.tmpdir, "fixed")
        self.store.save(path)
        os.chmod(path, 0o644)
        with self.assertRaises(Exception):
            type(self.store).load(path)
        os.chmod(path, 0o600)
        loaded = type(self.store).load(path)
        self.assertEqual(loaded.root_fingerprints, [self.root.fingerprint])


class TestVerificationPipeline(unittest.TestCase):
    """NPS-028 §4 — the single ordered path, §6.3.4 split, §7.2 audit."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        self.store = PkiKeyStore()
        self.kp, self.sig = _sign()
        self.store.enroll(self.kp.public_key, "acme",
                          _confirmation(self.kp.fingerprint))
        self.audit: list = []

    def _pipeline(self):
        from backend.package_pki import VerificationPipeline
        return VerificationPipeline(self.store,
                                    audit_sink=self.audit.append)

    def test_full_path_approves_install(self):
        result = self._pipeline().run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(result.verdict, "approved")
        outcomes = [(s["stage"], s["outcome"]) for s in result.stages]
        self.assertIn(("4_verify_signature", "ok"), outcomes)
        self.assertEqual(result.fingerprint, self.kp.fingerprint)
        self.assertEqual(result.publisher, "acme")

    def test_every_stage_recorded(self):
        result = self._pipeline().run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "install")
        stages = [s["stage"] for s in result.stages]
        self.assertEqual(stages, ["1_parse_manifest", "2_resolve_key",
                                  "3_key_status", "4_verify_signature",
                                  "5_touchpoint"])

    def test_unknown_key_fails_closed_tofu(self):
        """Stage 2: the FIND-PACKAGE-003 attack dies here."""
        stranger, sig = _sign()
        result = self._pipeline().run(
            MANIFEST, TREE, sig, stranger.public_key, "install")
        self.assertEqual(result.verdict, "denied")
        self.assertIn("TOFU rejection", result.reason)
        self.assertIn("untrusted publisher", result.reason)

    def test_expired_key_denied_at_install(self):
        from backend.package_pki import PkiKeyStore, VerificationPipeline
        kp, sig = _sign()
        store = PkiKeyStore()
        store.enroll(kp.public_key, "acme", _confirmation(kp.fingerprint),
                     expires_at=1000.0)
        result = VerificationPipeline(store).run(
            MANIFEST, TREE, sig, kp.public_key, "install", now=2000.0)
        self.assertEqual(result.verdict, "denied")
        self.assertIn("expired", result.reason)

    def test_revoked_key_blocks_install_but_advises_launch(self):
        """§6.3.4: hard block at install, advisory at launch."""
        self.store.revoke(self.kp.fingerprint, reason="compromise")
        denied = self._pipeline().run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(denied.verdict, "denied")
        advisory = self._pipeline().run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "launch")
        self.assertEqual(advisory.verdict, "advisory")

    def test_tampered_content_denied(self):
        result = self._pipeline().run(
            b'{"package_id": "acme-app", "version": "9.9.9"}',
            TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(result.verdict, "denied")
        self.assertIn("signature", result.reason)

    def test_garbage_manifest_denied_at_stage_1(self):
        result = self._pipeline().run(
            b"not json", TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(result.verdict, "denied")
        self.assertEqual(result.stages[0]["stage"], "1_parse_manifest")
        self.assertEqual(result.stages[0]["outcome"], "fail")

    def test_audit_sink_failure_never_changes_the_verdict(self):
        """§7.2: the audit trail is evidence, not a verification stage."""
        def exploding_sink(record):
            raise RuntimeError("audit backend down")
        from backend.package_pki import VerificationPipeline
        pipeline = VerificationPipeline(self.store, audit_sink=exploding_sink)
        result = pipeline.run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(result.verdict, "approved")
        warns = [s for s in result.stages if s["outcome"] == "warn"]
        self.assertEqual(len(warns), 1)

    def test_audit_sink_receives_the_record(self):
        self._pipeline().run(
            MANIFEST, TREE, self.sig, self.kp.public_key, "install")
        self.assertEqual(len(self.audit), 1)
        self.assertEqual(self.audit[0]["verdict"], "approved")
        self.assertEqual(self.audit[0]["fingerprint"], self.kp.fingerprint)

    def test_unknown_touchpoint_refused(self):
        with self.assertRaises(Exception):
            self._pipeline().run(
                MANIFEST, TREE, self.sig, self.kp.public_key, "download")


class TestPackageAuditChain(unittest.TestCase):
    """NPS-028 §7 — ADR-0018 reuse: the scheme-2 chain over package events."""

    def setUp(self):
        from backend.package_pki import PackageAuditChain
        self.chain = PackageAuditChain()

    def _populate(self):
        self.chain.append("package_verification", {"package_id": "acme-app",
                                                   "verdict": "approved"})
        self.chain.append("package_verification", {"package_id": "zed-cli",
                                                   "verdict": "denied",
                                                   "reason": "TOFU"})
        self.chain.append("key_rotation", {"from": "a", "to": "b"})

    def test_verify_passes_on_untouched_chain(self):
        self._populate()
        self.assertTrue(self.chain.verify())

    def test_tampered_details_break_the_chain(self):
        self._populate()
        self.chain.entries[1]["details"]["verdict"] = "approved"  # rewrite history
        self.assertFalse(self.chain.verify())

    def test_removed_entry_breaks_the_chain(self):
        self._populate()
        del self.chain.entries[0]
        self.assertFalse(self.chain.verify())

    def test_reordered_entries_break_the_chain(self):
        self._populate()
        self.chain.entries[0], self.chain.entries[1] = (self.chain.entries[1],
                                                        self.chain.entries[0])
        self.assertFalse(self.chain.verify())

    def test_jsonl_roundtrip_preserves_tamper_evidence(self):
        self._populate()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            self.chain.save_jsonl(path)
            loaded = type(self.chain).load_jsonl(path)
        self.assertTrue(loaded.verify())
        loaded.entries[0]["details"]["package_id"] = "forged"
        self.assertFalse(loaded.verify())

    def test_differential_pin_against_container_audit(self):
        """The construction must never drift from ADR-0018's scheme 2
        (the nui floor↔crate lesson: pin the equivalence)."""
        from backend.container import ContainerManager
        cases = [
            ("salt1", "GENESIS", "package_verification", 1727000000.0,
             {"package_id": "acme-app", "verdict": "approved"}),
            ("salt2", "deadbeef", "key_rotation", 1.5, None),
            ("salt3", "cafe", "package_verification", 0.0, {}),
        ]
        for salt, prev, op, ts, details in cases:
            theirs = ContainerManager._audit_event_content(
                salt, prev, op, ts, details)
            ours = self.chain.event_content(salt, prev, op, ts, details)
            self.assertEqual(ours, theirs)

    def test_none_and_empty_details_hash_differently(self):
        """Preserved from the container scheme on purpose."""
        a = self.chain.event_content("s", "p", "op", 1.0, None)
        b = self.chain.event_content("s", "p", "op", 1.0, {})
        self.assertNotEqual(a, b)


class TestAuditChainPipelineWiring(unittest.TestCase):
    """§7.1 — the pipeline's records land in the tamper-evident chain."""

    def setUp(self):
        from backend.package_pki import PackageAuditChain, PkiKeyStore
        self.chain_cls = PackageAuditChain
        self.store = PkiKeyStore()
        self.kp, self.sig = _sign()
        self.store.enroll(self.kp.public_key, "acme",
                          _confirmation(self.kp.fingerprint))
        self.chain = PackageAuditChain()

    def _run(self):
        from backend.package_pki import VerificationPipeline
        pipeline = VerificationPipeline(
            self.store,
            audit_sink=self.chain_cls.make_sink(self.chain))
        return pipeline.run(MANIFEST, TREE, self.sig, self.kp.public_key,
                            "install")

    def test_record_carries_the_package_identity(self):
        self._run()
        self.assertEqual(len(self.chain.entries), 1)
        record = self.chain.entries[0]["details"]
        self.assertEqual(record["package_id"], "acme-app")
        self.assertEqual(record["version"], "1.0.0")
        self.assertEqual(record["verdict"], "approved")
        self.assertEqual(record["fingerprint"], self.kp.fingerprint)
        self.assertEqual(record["publisher"], "acme")

    def test_chain_stays_tamper_evident_after_wiring(self):
        self._run()
        self.assertTrue(self.chain.verify())
        self.chain.entries[0]["details"]["verdict"] = "denied"
        self.assertFalse(self.chain.verify())

    def test_sink_failure_still_leaves_verdict_alone(self):
        """§7.2 holds through the chain wiring too."""
        def exploding(record):
            raise RuntimeError("audit backend down")
        from backend.package_pki import VerificationPipeline
        pipeline = VerificationPipeline(self.store, audit_sink=exploding)
        result = pipeline.run(MANIFEST, TREE, self.sig, self.kp.public_key,
                              "install")
        self.assertEqual(result.verdict, "approved")


class TestRevocationTransport(unittest.TestCase):
    """NPS-028 §5.1 — out-of-band fetch, independent of the package feed."""

    def setUp(self):
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PkiKeyStore()
        self.root = SigningKeypair.generate()
        self.store.add_root_anchor(self.root.public_key, "root-1")
        self.victim, _ = _sign()
        self.store.enroll(self.victim.public_key, "acme",
                          _confirmation(self.victim.fingerprint))
        self.store.revocation_sequence = 3  # prior lists already applied
        self.channel = os.path.join(self.tmp.name, "revocations.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_channel(self, sequence):
        from backend.package_pki import RevocationList
        rvl = RevocationList(sequence=sequence, generated_at=1.0, entries=[
            {"fingerprint": self.victim.fingerprint, "reason": "compromise"}])
        rvl.sign_with_root(self.root.fingerprint, self.root.private_key)
        with open(self.channel, "w", encoding="utf-8") as fh:
            fh.write(rvl.to_json())

    def _refresh(self):
        from backend.package_pki import FileRevocationFetcher
        from backend.package_pki import refresh_revocations
        return refresh_revocations(
            self.store, FileRevocationFetcher(self.channel))

    def test_newer_authentic_list_applies(self):
        self._write_channel(4)
        self.assertEqual(self._refresh()["outcome"], "applied")
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "revoked")
        self.assertEqual(self.store.revocation_sequence, 4)

    def test_regressive_list_refused_store_intact(self):
        self._write_channel(2)
        outcome = self._refresh()
        self.assertEqual(outcome["outcome"], "replay_refused")
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "trusted")

    def test_missing_channel_fails_without_touching_the_store(self):
        self.assertEqual(self._refresh()["outcome"], "fetch_failed")
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "trusted")

    def test_unauthentic_list_refused_store_intact(self):
        from backend.package_pki import RevocationList
        rvl = RevocationList(sequence=9, generated_at=1.0, entries=[
            {"fingerprint": self.victim.fingerprint, "reason": "x"}])
        with open(self.channel, "w", encoding="utf-8") as fh:
            fh.write(rvl.to_json())  # unsigned
        outcome = self._refresh()
        self.assertEqual(outcome["outcome"], "replay_refused")
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "trusted")

    def test_channel_is_independent_of_the_package_feed(self):
        """The fetcher's source is its own configuration; the refresh
        call involves no repo/feed object at all, so feed compromise
        cannot suppress revocation delivery."""
        self._write_channel(4)
        # No repo object exists in this test — only the channel file and
        # the store. The applied result proves delivery works without one.
        self.assertEqual(self._refresh()["outcome"], "applied")


if __name__ == "__main__":
    unittest.main()
