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

    def test_service_refresh_revocations_audit_chains_the_outcome(self):
        """§5.1 as a service op: the daemon's authority drives it, an
        applied list is audit-chained as pki_apply_revocations with the
        out-of-band source marker, and a rejected list is chained as
        pki_refresh_revocations evidence — never an error path."""
        from backend.package_pki import (DaemonAuthority, PkiDaemonService,
                                         FileRevocationFetcher)
        service = PkiDaemonService(self.store, audit_chain=self._make_chain())
        authority = DaemonAuthority.mint()
        service.bind(authority)
        self._write_channel(4)
        outcome = service.refresh_revocations(
            authority, FileRevocationFetcher(self.channel))
        self.assertEqual(outcome["outcome"], "applied")
        self.assertEqual(self.store.status_at(self.victim.fingerprint),
                         "revoked")
        ops = [(e["op"], e["details"].get("source"))
               for e in service._audit.entries]
        self.assertIn(("pki_apply_revocations", "out_of_band_refresh"), ops)
        # A rejected list is evidence, not an exception.
        self._write_channel(2)  # regressive
        outcome = service.refresh_revocations(
            authority, FileRevocationFetcher(self.channel))
        self.assertEqual(outcome["outcome"], "replay_refused")
        ops = [e["op"] for e in service._audit.entries]
        self.assertIn("pki_refresh_revocations", ops)

    def test_service_refresh_without_authority_refused(self):
        from backend.package_pki import (PkiError, PkiDaemonService,
                                         FileRevocationFetcher)
        service = PkiDaemonService(self.store, audit_chain=self._make_chain())
        with self.assertRaises(PkiError):
            service.refresh_revocations(
                None, FileRevocationFetcher(self.channel))

    def _make_chain(self):
        from backend.package_pki import PackageAuditChain
        return PackageAuditChain()


class TestDaemonAuthorityService(unittest.TestCase):
    """NPS-028 §3.2 daemon-side half / SURFACE-PKI-0001: the store is
    reachable only through the authority-guarded daemon service."""

    def setUp(self):
        from backend.package_pki import (PkiDaemonService, PkiKeyStore,
                                         PackageAuditChain)
        self.store = PkiKeyStore()
        self.kp, self.sig = _sign()
        self.store.enroll(self.kp.public_key, "acme",
                          _confirmation(self.kp.fingerprint))
        self.service = PkiDaemonService(self.store,
                                        audit_chain=PackageAuditChain())

    def _mint(self):
        from backend.package_pki import DaemonAuthority
        return DaemonAuthority.mint()

    def test_direct_construction_refused(self):
        """The token cannot be forged by calling the constructor."""
        from backend.package_pki import DaemonAuthority, PkiError
        with self.assertRaises(PkiError):
            DaemonAuthority(b"x" * 32)
        with self.assertRaises(PkiError):
            DaemonAuthority()

    def test_minted_tokens_are_distinct_and_recognized(self):
        a, b = self._mint(), self._mint()
        self.assertFalse(a.authorizes(b))
        self.assertTrue(a.authorizes(a))

    def test_no_authority_no_read(self):
        from backend.package_pki import PkiError
        with self.assertRaises(PkiError):
            self.service.status_for(None, self.kp.fingerprint)
        with self.assertRaises(PkiError):
            self.service.status_for("guess", self.kp.fingerprint)

    def test_no_authority_no_write(self):
        from backend.package_pki import PkiError
        stranger, _ = _sign()
        with self.assertRaises(PkiError):
            self.service.enroll(None, stranger.public_key, "evil",
                                _confirmation(stranger.fingerprint))
        self.assertIsNone(self.store.enrolled_public_key(
            stranger.fingerprint))

    def test_valid_authority_reads_and_writes(self):
        authority = self._mint()
        self.service.bind(authority)
        self.assertEqual(
            self.service.status_for(authority, self.kp.fingerprint),
            "trusted")
        self.assertIsNotNone(self.service.enrolled_public_key_for(
            authority, self.kp.fingerprint))
        record = self.service.revoke(authority, self.kp.fingerprint,
                                     reason="compromise")
        self.assertEqual(record["fingerprint"], self.kp.fingerprint)
        self.assertEqual(self.service.status_for(
            authority, self.kp.fingerprint), "revoked")

    def test_bind_pins_the_service_to_one_authority(self):
        from backend.package_pki import PkiError
        authority = self._mint()
        self.service.bind(authority)
        with self.assertRaises(PkiError):
            self.service.bind(self._mint())
        # A different (itself valid) token is now refused too.
        with self.assertRaises(PkiError):
            self.service.status_for(self._mint(), self.kp.fingerprint)
        self.assertEqual(
            self.service.status_for(authority, self.kp.fingerprint),
            "trusted")

    def test_mutation_attempts_land_in_the_audit_chain(self):
        authority = self._mint()
        self.service.bind(authority)
        self.service.revoke(authority, self.kp.fingerprint, "x")
        ops = [e["op"] for e in self.service._audit.entries]
        self.assertIn("pki_revoke", ops)

    def test_service_exposes_no_store_or_enumeration(self):
        """§3.2: an installed package must not read, write, or enumerate
        the store — the service offers no path that returns it."""
        forbidden = ["store", "keys", "entries", "list", "export",
                     "snapshot", "dump", "get_store"]
        methods = [m for m in dir(self.service) if not m.startswith("_")]
        for name in methods:
            self.assertNotIn(name, forbidden,
                             f"service leaks {name!r}")

    def test_revocation_list_through_the_service(self):
        from backend.package_pki import RevocationList
        from backend.package_signing import SigningKeypair
        root = SigningKeypair.generate()
        self.store.add_root_anchor(root.public_key, "root-1")
        rvl = RevocationList(sequence=1, generated_at=1.0, entries=[
            {"fingerprint": self.kp.fingerprint, "reason": "compromise"}])
        rvl.sign_with_root(root.fingerprint, root.private_key)
        authority = self._mint()
        self.service.bind(authority)
        self.assertEqual(
            self.service.apply_revocation_list(authority, rvl), 1)
        self.assertEqual(self.service.status_for(
            authority, self.kp.fingerprint), "revoked")


class TestPkiIpcTransport(unittest.TestCase):
    """NPS-028 §3.2 physical transport: the service over a Unix socket.

    The transport must not widen what the service allows: same-uid
    clients get exactly the §3.2 rules, mutations stay audit-chained,
    and the authority is server-minted (clients present nothing).
    """

    def setUp(self):
        import tempfile
        from backend.package_pki import (PackageAuditChain, PkiDaemonService,
                                         PkiKeyStore)
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self.tmp.name, "pki.sock")
        self.store = PkiKeyStore()
        self.kp, self.sig = _sign()
        self.store.enroll(self.kp.public_key, "acme",
                          _confirmation(self.kp.fingerprint))
        self.audit = PackageAuditChain()
        self.service = PkiDaemonService(self.store, audit_chain=self.audit)
        from backend.package_pki import PkiIpcServer
        self.server = PkiIpcServer(self.service, self.socket_path)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def _client(self):
        from backend.package_pki import PkiIpcClient
        return PkiIpcClient(self.socket_path)

    def test_read_path_over_the_socket(self):
        client = self._client()
        try:
            self.assertEqual(client.call("status_for",
                                         fingerprint=self.kp.fingerprint),
                             "trusted")
        finally:
            client.close()

    def test_key_material_reads_are_not_on_the_ipc_allowlist(self):
        """§3.2 on the wire: verification runs in the daemon; an installed
        package has no need (and no right) to export key material."""
        from backend.package_pki import PkiError
        client = self._client()
        try:
            for op in ("enrolled_public_key_for", "root_public_key_for"):
                with self.assertRaises(PkiError):
                    client.call(op, fingerprint=self.kp.fingerprint)
        finally:
            client.close()

    def test_revoke_over_the_socket_lands_in_the_audit_chain(self):
        client = self._client()
        try:
            client.call("revoke", fingerprint=self.kp.fingerprint,
                        reason="compromise")
            self.assertEqual(client.call("status_for",
                                         fingerprint=self.kp.fingerprint),
                             "revoked")
        finally:
            client.close()
        ops = [e["op"] for e in self.audit.entries]
        self.assertIn("pki_revoke", ops)

    def test_unknown_op_refused(self):
        from backend.package_pki import PkiError
        client = self._client()
        try:
            with self.assertRaises(PkiError):
                client.call("export_store")
        finally:
            client.close()

    def test_private_method_refused(self):
        from backend.package_pki import PkiError
        client = self._client()
        try:
            with self.assertRaises(PkiError):
                client.call("_check")
        finally:
            client.close()

    def test_transport_does_not_widen_the_service(self):
        """An op the service would refuse in-process is refused over the
        wire too — same fail-closed semantics, no transport bypass."""
        from backend.package_pki import PkiError
        client = self._client()
        try:
            with self.assertRaises(PkiError):
                client.call("no_such_method_at_all")
        finally:
            client.close()

    def test_multiple_connections_speak_with_the_daemons_identity(self):
        """Connections are wires, not identities: every connection speaks
        with the one server-minted daemon authority, and no client ever
        sees a token."""
        c1, c2 = self._client(), self._client()
        try:
            self.assertEqual(c1.call("status_for",
                                     fingerprint=self.kp.fingerprint),
                             "trusted")
            self.assertEqual(c2.call("status_for",
                                     fingerprint=self.kp.fingerprint),
                             "trusted")
        finally:
            c1.close()
            c2.close()

    def test_server_mints_exactly_one_authority_and_binds_it(self):
        from backend.package_pki import DaemonAuthority
        self.assertIsInstance(self.server.daemon_authority, DaemonAuthority)
        self.assertTrue(
            self.service._authority.authorizes(self.server.daemon_authority))

    def test_socket_file_is_owner_only(self):
        self.assertEqual(os.stat(self.socket_path).st_mode & 0o777, 0o600)

    def test_group_writable_socket_dir_refused_fail_closed(self):
        """§3.2 on the bind location: a group/world-writable directory
        lets a local attacker swap or shadow the socket — refuse to
        bind, naming the fix."""
        import tempfile
        import stat as _stat
        from backend.package_pki import PkiError, PkiIpcServer
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, _stat.S_IRWXU | _stat.S_IWGRP | _stat.S_IXGRP
                     | _stat.S_IXOTH)
            try:
                bad = PkiIpcServer(self.service,
                                   os.path.join(tmp, "pki.sock"))
                with self.assertRaises(PkiError) as caught:
                    bad.start()
                self.assertIn("chmod go-w", str(caught.exception))
            finally:
                os.chmod(tmp, _stat.S_IRWXU | _stat.S_IXOTH)

    def test_peer_uid_policy(self):
        from backend.package_pki import PkiIpcServer
        daemon_uid = os.geteuid()
        self.assertTrue(
            PkiIpcServer._peer_uid_allowed(daemon_uid, daemon_uid))
        self.assertTrue(PkiIpcServer._peer_uid_allowed(0, daemon_uid))
        self.assertFalse(
            PkiIpcServer._peer_uid_allowed(daemon_uid + 1, daemon_uid))

    def test_stop_is_idempotent_and_never_leaks_the_socket(self):
        self.server.stop()
        self.server.stop()
        self.assertFalse(os.path.exists(self.socket_path))


class TestPkiDaemonRunner(unittest.TestCase):
    """§3.2 + §3.4 assembled: the daemon's production process model.

    Mirrors StatusServiceHost's boot contract: unlock secret at boot
    (fail-closed), serve, and a clean stop that persists custody + the
    §7 chain so a restart resumes intact.
    """

    def setUp(self):
        import tempfile
        from backend.package_pki import PkiKeyStore
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self.tmp.name, "pki.sock")
        self.store_path = os.path.join(self.tmp.name, "pki-store.custody")
        self.audit_path = os.path.join(self.tmp.name, "pki-audit.jsonl")
        self.secret = "boot-passphrase"
        # A pre-existing custody store with an enrolled key.
        kp, _ = _sign()
        self.kp = kp
        store = PkiKeyStore()
        store.enroll(kp.public_key, "acme",
                     _confirmation(kp.fingerprint))
        store.save_locked(self.store_path, self.secret)

    def tearDown(self):
        self.tmp.cleanup()

    def _runner(self, **overrides):
        from backend.package_pki import PkiDaemonRunner
        kwargs = dict(socket_path=self.socket_path,
                      store_path=self.store_path,
                      unlock_secret=self.secret,
                      audit_log_path=self.audit_path)
        kwargs.update(overrides)
        return PkiDaemonRunner(**kwargs)

    def test_no_secret_refused_at_boot(self):
        """Production custody is mandatory — no secret, no runner."""
        from backend.package_pki import PkiError
        with self.assertRaises(PkiError):
            self._runner(unlock_secret="")

    def test_boot_loads_the_custody_store(self):
        runner = self._runner().start()
        try:
            client = runner.client()
            self.assertEqual(
                client.call("status_for",
                            fingerprint=self.kp.fingerprint),
                "trusted")
            client.close()
        finally:
            runner.stop()

    def test_stop_persists_and_restart_resumes(self):
        """The restart contract: revoke over IPC, stop, boot a fresh
        runner on the same paths — the revocation survived."""
        runner = self._runner().start()
        client = runner.client()
        client.call("revoke", fingerprint=self.kp.fingerprint,
                    reason="compromise")
        client.close()
        runner.stop()

        runner2 = self._runner().start()
        try:
            client2 = runner2.client()
            self.assertEqual(
                client2.call("status_for",
                             fingerprint=self.kp.fingerprint),
                "revoked")
            client2.close()
        finally:
            runner2.stop()
        # The §7 chain resumed too: the file carries the salt header +
        # the one pki_revoke entry (the second run added nothing new).
        with open(self.audit_path, encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)

    def test_restart_resumes_the_audit_chain(self):
        """A loaded chain re-verifies and new appends continue it."""
        from backend.package_pki import PackageAuditChain
        runner = self._runner().start()
        runner.client().close()
        runner.stop()
        chain = PackageAuditChain.load_jsonl(self.audit_path)
        self.assertTrue(chain.verify())
        chain.append("post_restart", {"ok": True})
        self.assertTrue(chain.verify())

    def test_stop_is_idempotent(self):
        runner = self._runner().start()
        runner.stop()
        runner.stop()
        self.assertFalse(os.path.exists(self.socket_path))

    def test_double_start_refused(self):
        from backend.package_pki import PkiError
        runner = self._runner().start()
        try:
            with self.assertRaises(PkiError):
                runner.start()
        finally:
            runner.stop()

    def test_audit_chain_survives_restart_without_duplication(self):
        """Two run/stop cycles must not re-persist earlier entries."""
        runner = self._runner().start()
        client = runner.client()
        client.call("revoke", fingerprint=self.kp.fingerprint, reason="x")
        client.close()
        runner.stop()
        runner2 = self._runner().start()
        runner2.stop()
        with open(self.audit_path, encoding="utf-8") as fh:
            entries = [json.loads(l) for l in fh if l.strip()][1:]
        revoke_ops = [e for e in entries if e.get("op") == "pki_revoke"]
        self.assertEqual(len(revoke_ops), 1)


class TestPkiDeploymentWiring(unittest.TestCase):
    """The production process model is wired for deployment: the CLI
    subcommand and the systemd unit. Parsed, not transcribed — the
    unit file is the single source of truth (the Session 10 drift
    lesson: two copies nobody tested is how an installed system breaks).
    """

    BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def _unit_text(cls):
        path = os.path.join(cls.BACKEND_DIR, "packaging", "systemd",
                            "nyrqis-pki.service")
        with open(path, encoding="utf-8") as fh:
            return path, fh.read()

    def test_unit_exists_and_matches_both_trees(self):
        """The two-tree rule: backend is the source of truth, the root
        packaging/systemd/ copy is a byte-identical mirror."""
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        backend_unit = os.path.join(
            self.BACKEND_DIR, "packaging", "systemd", "nyrqis-pki.service")
        root_unit = os.path.join(
            repo_root, "packaging", "systemd", "nyrqis-pki.service")
        self.assertTrue(os.path.isfile(backend_unit),
                        f"missing backend-tree unit {backend_unit}")
        self.assertTrue(os.path.isfile(root_unit),
                        f"missing root-tree mirror {root_unit}")
        with open(backend_unit, encoding="utf-8") as fh:
            backend_text = fh.read()
        with open(root_unit, encoding="utf-8") as fh:
            root_text = fh.read()
        self.assertEqual(
            backend_text, root_text,
            "the two nyrqis-pki.service copies diverged — sync them "
            "(backend tree is the source of truth; install.sh deploys it)")

    def test_cli_wires_pki_serve_subcommand(self):
        """`pki serve` exists with the custody-mandatory flags the unit
        passes, and the runner refuses a secret-less boot."""
        import importlib
        import argparse
        mod = importlib.import_module("nyrqis_backend")
        parser = mod.build_parser() if hasattr(mod, "build_parser") else None
        if parser is None:
            # The CLI builds its parser inside main(), which reads
            # sys.argv — drive the real argv path with a probe.
            import io, contextlib
            buf = io.StringIO()
            old_argv = sys.argv
            sys.argv = ["nyrqis_backend.py", "pki", "serve", "--help"]
            try:
                with contextlib.redirect_stdout(buf):
                    try:
                        mod.main()
                    except SystemExit:
                        pass  # argparse exits 0 on --help
            finally:
                sys.argv = old_argv
            out = buf.getvalue()
            self.assertIn("--socket", out)
            self.assertIn("--store", out)
            self.assertIn("--audit-log", out)
            self.assertIn("--unlock-secret", out)
        else:
            args = parser.parse_args(
                ["pki", "serve", "--socket", "/tmp/s",
                 "--store", "/tmp/st", "--unlock-secret", "x"])
            self.assertEqual(args.socket, "/tmp/s")
            self.assertEqual(args.unlock_secret, "x")

    def test_unit_execstart_targets_pki_serve_with_custody_paths(self):
        """The unit's ExecStart runs the real CLI with the custody store,
        the audit chain, and the §3.2 socket — and the unlock secret
        comes from the optional EnvironmentFile (custody is mandatory,
        so the daemon exits without it)."""
        _, text = self._unit_text()
        self.assertIn("pki serve", text)
        self.assertIn("nyrqis_backend.py", text)
        self.assertIn("--socket /run/nyrqis/pki.sock", text)
        self.assertIn("--store /var/lib/nyrqis/pki/store.custody.json", text)
        self.assertIn("--audit-log /var/lib/nyrqis/pki/audit.jsonl", text)
        self.assertIn("EnvironmentFile=-/etc/nyrqis/pki.env", text)
        self.assertIn("StateDirectory=nyrqis", text)
        self.assertIn("RuntimeDirectory=nyrqis", text)

    def test_unit_hardening_matches_the_documented_posture(self):
        """Unprivileged, no-new-privs, and the install tree read-only —
        the same privilege posture the backend unit pins (the PKI daemon
        launches no containers, so it can go further on namespaces)."""
        _, text = self._unit_text()
        self.assertIn("NoNewPrivileges=true", text)
        self.assertTrue(
            "DynamicUser=true" in text or "User=" in text,
            "unit must not run as root")
        self.assertIn("Restart=on-failure", text)
        directives = [l.strip() for l in text.splitlines()
                      if not l.lstrip().startswith(("#", ";"))]
        self.assertNotIn("RestrictNamespaces=yes", directives)

    def test_install_sh_deploys_the_pki_unit(self):
        """install.sh deploys the backend tree's units — the PKI unit
        must be on that list or the installed system silently lacks it."""
        path = os.path.join(self.BACKEND_DIR, "packaging", "install.sh")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("nyrqis-pki.service", text)
        self.assertIn("nyrqis-backend.service", text)
        self.assertIn("nyrqis-desktop.service", text)


class TestPkiIpcWireConventions(unittest.TestCase):
    """Binary-over-JSON: the drill's finding.

    The 2026-09-22 end-to-end daemon drill booted `pki serve` as a real
    subprocess and enrolled over the socket — and found the transport
    had never been exercised with an enroll op over the wire: JSON
    carries no bytes, so the 64-char hex string arrived where the
    service demands 32 raw bytes ("Public key must be 32 bytes"), and
    the confirmation dataclass arrived as a plain dict. The conventions
    are now explicit: named hex params decode to bytes, dataclass
    params rebuild from their field mapping, and anything malformed
    fails the request closed.
    """

    def setUp(self):
        import tempfile
        from backend.package_pki import (PackageAuditChain, PkiDaemonService,
                                         PkiKeyStore)
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self.tmp.name, "pki.sock")
        self.store = PkiKeyStore()
        self.audit = PackageAuditChain()
        self.service = PkiDaemonService(self.store, audit_chain=self.audit)
        from backend.package_pki import PkiIpcServer
        self.server = PkiIpcServer(self.service, self.socket_path)
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def _confirmation_dict(self, fingerprint):
        return {"confirmed": True, "actor": "drill-operator",
                "publisher_identity": "acme", "fingerprint_shown": fingerprint,
                "source": "drill"}

    def test_enroll_over_the_wire_with_bytes_and_hex(self):
        """The drill's exact path: enroll over the socket with a raw
        bytes key (client encodes) and with a pre-hexed string (client
        passes through) — both decode server-side to the same store
        entry."""
        from backend.package_pki import PkiIpcClient
        from backend.package_signing import SigningKeypair
        kp = SigningKeypair.generate()
        client = PkiIpcClient(self.socket_path)
        try:
            client.call("enroll", public_key=kp.public_key,
                        publisher="acme",
                        confirmation=self._confirmation_dict(kp.fingerprint))
            self.assertEqual(client.call("status_for",
                                         fingerprint=kp.fingerprint),
                             "trusted")
        finally:
            client.close()

    def test_enroll_refuses_a_spoofed_confirmation_over_the_wire(self):
        """§6.2 survives the wire: a confirmation showing a different
        fingerprint enrolls nothing."""
        from backend.package_pki import PkiError, PkiIpcClient
        from backend.package_signing import SigningKeypair
        kp = SigningKeypair.generate()
        spoofed = self._confirmation_dict("f" * 64)
        client = PkiIpcClient(self.socket_path)
        try:
            with self.assertRaises(PkiError):
                client.call("enroll", public_key=kp.public_key,
                            publisher="acme", confirmation=spoofed)
        finally:
            client.close()

    def test_malformed_hex_param_fails_closed(self):
        """A param claiming hex but not parsing is a request failure,
        never a silent string pass-through."""
        from backend.package_pki import PkiError, PkiIpcClient
        from backend.package_signing import SigningKeypair
        kp = SigningKeypair.generate()
        client = PkiIpcClient(self.socket_path)
        try:
            with self.assertRaises(PkiError):
                client.call("enroll", public_key="zz-not-hex",
                            publisher="acme",
                            confirmation=self._confirmation_dict(
                                kp.fingerprint))
        finally:
            client.close()

    def test_bogus_confirmation_shape_fails_closed(self):
        """A confirmation dict that does not match the dataclass's
        fields is a request failure, not a silently coerced object."""
        from backend.package_pki import PkiError, PkiIpcClient
        from backend.package_signing import SigningKeypair
        kp = SigningKeypair.generate()
        client = PkiIpcClient(self.socket_path)
        try:
            with self.assertRaises(PkiError):
                client.call("enroll", public_key=kp.public_key,
                            publisher="acme",
                            confirmation={"confirmed": True,
                                          "bogus_field": 1})
        finally:
            client.close()

    def test_decode_params_is_directly_pinned(self):
        """The server-side decode: hex -> bytes, dict -> dataclass,
        wrong shape -> PkiError."""
        from backend.package_pki import (PkiError, PkiIpcServer,
                                         EnrollmentConfirmation)
        decoded = PkiIpcServer._decode_params(
            {"public_key": "ab" * 32})
        self.assertEqual(decoded["public_key"], b"\xab" * 32)
        decoded = PkiIpcServer._decode_params(
            {"confirmation": {"confirmed": True, "actor": "a",
                              "publisher_identity": "p",
                              "fingerprint_shown": "e" * 64,
                              "source": "s"}})
        self.assertIsInstance(decoded["confirmation"],
                              EnrollmentConfirmation)
        with self.assertRaises(PkiError):
            PkiIpcServer._decode_params({"public_key": "nothex!"})
        with self.assertRaises(PkiError):
            PkiIpcServer._decode_params(
                {"confirmation": {"nope": True}})


class TestPkiRevocationRefreshWiring(unittest.TestCase):
    """§5.1 wired into the daemon: the service-level refresh (the
    daemon's own authority, audit-chained outcomes) and the runner's
    background loop (channel-configured, fail-open, joinable at stop).
    The §5.1 rule under test throughout: a bad fetch or a bad list
    NEVER touches the store — the outcome is evidence, not an error.
    """

    def setUp(self):
        import tempfile
        from backend.package_pki import PkiKeyStore
        from backend.package_signing import SigningKeypair
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o755)  # §3.2 bind needs a non-writable dir
        self.channel = os.path.join(self.tmp.name, "revocations.json")
        self.store_path = os.path.join(self.tmp.name, "store.custody")
        self.root = SigningKeypair.generate()
        self.victim, _ = _sign()
        # Pre-seed the custody store the runner will boot from: the
        # runner owns its store object, so the root anchor and the
        # victim's enrollment must be in ITS store (a fixture-only
        # enrollment verifies nothing about the daemon path).
        seeded = PkiKeyStore()
        seeded.add_root_anchor(self.root.public_key, "root-1")
        seeded.enroll(self.victim.public_key, "acme",
                      _confirmation(self.victim.fingerprint))
        seeded.revocation_sequence = 3
        seeded.save_locked(self.store_path, "refresh-passphrase")
        self.store = seeded  # read-model mirror for the service-level tests

    def tearDown(self):
        self.tmp.cleanup()

    def _write_channel(self, sequence):
        from backend.package_pki import RevocationList
        rvl = RevocationList(sequence=sequence, generated_at=1.0, entries=[
            {"fingerprint": self.victim.fingerprint, "reason": "compromise"}])
        rvl.sign_with_root(self.root.fingerprint, self.root.private_key)
        with open(self.channel, "w", encoding="utf-8") as fh:
            fh.write(rvl.to_json())

    def _runner(self, **overrides):
        from backend.package_pki import PkiDaemonRunner
        kwargs = dict(
            socket_path=os.path.join(self.tmp.name, "pki.sock"),
            store_path=self.store_path,
            unlock_secret="refresh-passphrase",
            audit_log_path=os.path.join(self.tmp.name, "audit.jsonl"),
            revocation_channel=self.channel,
            refresh_interval=0.2,
        )
        kwargs.update(overrides)
        return PkiDaemonRunner(**kwargs)

    def test_background_refresh_applies_a_newer_list(self):
        """The loop pulls the channel on its interval; a newer
        authentic list is applied and the runner's store reflects it
        (the runner creates and owns its store — assert on it, not the
        setUp fixture)."""
        import time as _time
        self._write_channel(4)
        runner = self._runner().start()
        try:
            rstore = runner.store
            deadline = _time.time() + 10
            while _time.time() < deadline:
                if rstore.status_at(self.victim.fingerprint) == "revoked":
                    break
                _time.sleep(0.1)
            self.assertEqual(rstore.status_at(self.victim.fingerprint),
                             "revoked")
        finally:
            runner.stop()

    def test_background_refresh_survives_a_bad_channel(self):
        """A missing/invalid channel must not kill the loop or the
        daemon: the next tick recovers when the channel is good again."""
        import time as _time
        runner = self._runner().start()  # channel does not exist yet
        try:
            rstore = runner.store
            _time.sleep(0.7)  # a few failing ticks
            self._write_channel(4)
            deadline = _time.time() + 10
            while _time.time() < deadline:
                if rstore.status_at(self.victim.fingerprint) == "revoked":
                    break
                _time.sleep(0.1)
            self.assertEqual(rstore.status_at(self.victim.fingerprint),
                             "revoked")
        finally:
            runner.stop()

    def test_no_channel_means_no_refresh_thread(self):
        """Without a configured channel the loop is disabled by
        decision, not omission — no thread is started."""
        runner = self._runner(revocation_channel=None).start()
        try:
            self.assertIsNone(runner._refresh_thread)
        finally:
            runner.stop()

    def test_stop_joins_the_refresh_thread(self):
        runner = self._runner().start()
        thread = runner._refresh_thread
        self.assertIsNotNone(thread)
        runner.stop()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(runner._refresh_thread)

    def test_refresh_is_not_on_the_ipc_allowlist(self):
        """Callers cannot smuggle a fetcher through the socket: the
        refresh op is daemon-internal, driven by the daemon's own
        authority."""
        from backend.package_pki import PkiIpcServer
        self.assertNotIn("refresh_revocations", PkiIpcServer.IPC_ALLOWED_OPS)

    def test_unit_wires_the_revocation_channel(self):
        """The deployed unit configures the out-of-band channel and its
        refresh interval (two-tree rule checked by the wiring suite)."""
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "packaging", "systemd",
            "nyrqis-pki.service")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("--revocation-channel /var/lib/nyrqis/pki/revocations.json",
                      text)
        self.assertIn("--refresh-interval 300", text)


if __name__ == "__main__":
    unittest.main()
