---
title: Reject extra dbx hash types in the enforced revocation surface
document_id: ADR-0030
version: 1.0.0
status: Rejected
owners: [Nyrqis Architecture]
created: 2026-10-31
updated: 2026-10-31
ai_assisted: true
depends_on: [ADR-0029, ADR-0014]
---

# ADR-0030 — Reject extra dbx hash types in the enforced revocation surface

## Context

ADR-0029 fixed the enforced surface at two entry types — `EFI_CERT_X509`
(byte-match and issuer+serial pair) and `EFI_CERT_X509_SHA256` (the
SHA-256 of the certificate's To-Be-Signed content plus an `EFI_TIME`
revocation date) — and deferred the rest in one line: "SHA-1/SHA-512
hash-type support ... Each is future work with its own ADR if needed".

UEFI 2.10 section 32.4.1 and edk2's `mSupportSigItem` name seven more
types in the same database: `EFI_CERT_SHA1`/`224`/`256`/`384`/`512` (a
bare digest), `EFI_CERT_X509_SHA384`/`SHA512` (a 48/64-byte To-Be-Signed
hash plus `EFI_TIME`), and the RSA2048* signature blobs. Today the walk
reports every one of them as a skipped fact, and as of 2026-10-31 that
is a tested assertion rather than a comment: `dbx-skip-types` in
`uefi/tests/trust_selfcheck.rs` builds all seven from the spec's own
GUID strings, asserts they come back skipped with zero enforced entries,
and fails if any class drifts into a refusal. The question is therefore
now decidable on evidence instead of being deferred by default.

## Decision

Do NOT add any of these types to the enforced revocation surface.

## Why

1. **There is no digest implementation to lean on.** The stub's SHA-256
   is a hand-rolled, self-checked implementation (`uefi/src/sha256.rs`);
   the boot path has no SHA-384 or SHA-512. Adding two more hash
   implementations to the code that decides whether the kernel image may
   be trusted is a large, security-critical surface, and the only
   benefit would be matching entries that real distributions do not
   ship for CA revocation — Microsoft's dbx CA revocations use
   `EFI_CERT_X509_SHA256`, which is already enforced.
2. **SHA-1 would make the walk enforce something weaker than the chain
   it just verified.** A revocation identity bound by a broken
   collision-resistant hash is not a revocation guarantee; implementing
   it is worse than honestly reporting it as skipped.
3. **The raw-digest classes are a different mechanism with a different
   subject.** `EFI_CERT_SHA256` and friends name the digest of an IMAGE
   (the "revoke this binary" form the firmware's own image verification
   also checks), not a certificate identity. Enforcing them inside this
   walk would silently redefine its subject from "the chain's
   certificates" to "images", so a dbx entry revoking an unrelated image
   would refuse an otherwise valid carrier.
4. **Declining costs nothing in honesty, because the skip path is
   asserted.** The walk prints how many lists it did not enforce, and
   the fixture control fails if a class drifts in or out of the enforced
   branches. The harmful alternative is a silently ignored entry, and
   silent agreement between our own two implementations is precisely the
   failure class this repository met twice (ADR-0028's GUID constants,
   ADR-0029's payload field order).
5. **RSA2048 signature blobs have no meaning here.** A chain this walk
   already verified carries its own signature over the same digest; a
   bare RSA signature blob in dbx is not an identity for either parsed
   certificate.

## Conditions that would reopen this

- A firmware or dbx distribution shipping `EFI_CERT_X509_SHA384`/`512`
  for CA revocation in practice — an operator-visible need, not a spec
  possibility.
- A second digest implementation arriving in the stub for another
  reason, which would make 384/512 cheap and reviewed rather than new
  attack surface.
- A production-key requirement from ADR-0014's user-enrollable
  hierarchy that names a 384/512 identity.

Reopening means a new ADR superseding this one, with the fixtures
extended from width-fillers to real SHA-384/512 To-Be-Signed hashes.

## Consequences

- The enforced surface stays exactly ADR-0029's two types, and the seven
  other classes stay reported facts in every transcript.
- The `dbx-skip-types` control becomes this decision's guard: an
  implementation of one of these types fails the control, so widening
  must land with a superseding ADR rather than as a quiet change.
- NOT decided here: whether a FIRMWARE should enforce more types. That
  is firmware behaviour, measured per build by the boot gates (which now
  print the firmware image's sha256 with every verdict), not code this
  repository owns.
