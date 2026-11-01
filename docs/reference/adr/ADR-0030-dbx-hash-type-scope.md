---
title: Reject extra dbx hash types in the enforced revocation surface
document_id: ADR-0030
version: 1.2.0
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
   ship for CA revocation. (This point originally claimed that
   "Microsoft's dbx CA revocations use `EFI_CERT_X509_SHA256`, which is
   already enforced". Measured on 2026-10-31, that is wrong: the
   shipped CA revocation is a single WHOLE-CERTIFICATE `EFI_CERT_X509`
   entry, enforced by the walk's other branch. See the recheck below.)
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

## Recheck (2026-10-31): the shipped dbx, measured

The decision above was argued partly from what distributions do or do
not ship. That was prose; it is now an inventory. `tools/dbx_inventory.py`
parses a real dbx artifact (auth2 package or bare signature lists) and
reports entry classes, sizes and counts. Against Microsoft's published
artifacts (`microsoft/secureboot_objects`, fetched 2026-10-31):

| artifact | bytes | contents |
|---|---|---|
| `PostSignedObjects/DBX/amd64/DBXUpdate.bin` (sha256 `2089e312…`) | 24,629 | 443 × `EFI_CERT_SHA256` (48-byte entries) |
| `PostSignedObjects/Optional/DBX/DBXUpdate2024.bin` (sha256 `54ffcf30…`) | 5,052 | 1 × `EFI_CERT_X509` (1,515-byte DER certificate), 3 × `EFI_CERT_SHA256` |
| `PostSignedObjects/Optional/DBX/DBXUpdateSVN.bin` (sha256 `54499b97…`) | 3,524 | 3 × `EFI_CERT_SHA256` |
| `PreSignedObjects/DBX/dbx_info_msft_latest.json` (sha256 `029fbd10…`) | 396,460 | images x64 445 / ia32 96 / aarch64 26 / arm 110, **every one `hashType: SHA256`**; certificates: **1** (CN = Microsoft Windows Production PCA 2011, "Revoked due to CVE-2023-24932"); svns: per-image version counters |

Reproduce with:

```
./tools/dbx_inventory.py DBXUpdate.bin DBXUpdate2024.bin DBXUpdateSVN.bin
```

What the inventory establishes, and what it corrects:

1. **No 384/512 form ships, and no TBS-hash form ships either.** The
   reopen condition this ADR names ("`EFI_CERT_X509_SHA384`/`512` for CA
   revocation in practice") is unmet, so the decision stands unchanged.
2. **The CA revocation is whole-certificate, not hash-typed.** That is
   the `EFI_CERT_X509` entry above, which the walk's byte-match /
   issuer+serial branch already enforces. The enforced surface does
   cover the real CA revocation — through ADR-0029's OTHER entry type,
   not through `EFI_CERT_X509_SHA256` as the original point 1 said.
   `EFI_CERT_X509_SHA256` remains enforced because the leaf-rotation
   and compromise procedures (ADR-0028/0029) issue it, not because
   vendors ship it.
3. **`EFI_CERT_SHA256` is the image/version surface, which is why point
   3 holds.** The 48-byte entry is a 16-byte owner GUID plus a 32-byte
   value, and the SVN classes ride the same list type: per-image version
   counters rather than certificate identities. Enforcing this class
   inside the chain walk would indeed redefine its subject. The firmware
   enforces image hashes at LoadImage, which is the right owner of that
   judgement.

The practical consequence is worth stating plainly: against
`DBXUpdate.bin`, the stub's walk would enforce zero entries, and that is
correct behaviour, not a gap — none of those 443 entries names a
certificate in a chain it verifies.

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
- The decision rests on an inventory that can be re-run
  (`tools/dbx_inventory.py`) rather than on an assumption about vendor
  content; if a future artifact adds a 384/512 TBS entry, the inventory
  shows it and the reopen condition is decidable without re-litigating
  the argument.
- The `dbx-skip-types` control becomes this decision's guard: an
  implementation of one of these types fails the control, so widening
  must land with a superseding ADR rather than as a quiet change.
- NOT decided here: whether a FIRMWARE should enforce more types. That
  is firmware behaviour, measured per build by the boot gates (which now
  print the firmware image's sha256 with every verdict), not code this
  repository owns.

## Recheck (2026-11-01): the decision is now enforced at the fleet end

`tools/verify_dbx_store.py` (ADR-0029 v1.5.0) makes this ADR operational
for an operator: when a certificate's hash appears in a store only under
one of the seven report-only classes, the verifier's verdict is
**present, not enforced** and it FAILS, naming this ADR. That is the
distinction that matters in a fleet audit — the store carries something
that looks like a revocation and no machine refuses anything — and it is
now a named refusal rather than a fact an operator has to notice.

The decision itself is unchanged and unopened: the reopen condition
(`EFI_CERT_X509_SHA384`/`512` for CA revocation in practice) is still
unmet, and the recheck above measured the shipped artifacts that would
satisfy it.
