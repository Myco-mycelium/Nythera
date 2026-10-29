---
title: Leaf Revocation by dbx TBS Hash — the Enforcement Semantics the Gates Enforce
document_id: ADR-0029
version: 1.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-10-29
updated: 2026-10-29
ai_assisted: true
depends_on: [ADR-0014, ADR-0028]
---

# ADR-0029 — Leaf Revocation by dbx TBS Hash: the Enforcement Semantics the Gates Enforce

## Context

ADR-0028 named the dbx TBS-hash path as THE revocation surface this
codebase implements, in one sentence, without fixing its semantics to a
testable record: what an EFI_CERT_X509_SHA256 dbx entry revokes, when it
takes force, against which certificate, and what "refused" means when
both the firmware and the stub's own walk can be the refusing party.
Those semantics now have a provable artifact — the enforcing `.ms`
gate's fourth boot leg composes a REAL firmware-served dbx containing
the enrolled leaf's To-Be-Signed hash and the leaf-signed stub MUST
fail to boot — so the decision can be written against the code and the
gate rather than intent. This ADR records exactly those semantics, with
the same status rationale as ADR-0028: accepted by implementation
record (the enforcement lives in `uefi/src/dbx.rs`, exercised on host
by `tools/verify_carrier.sh` and at boot by the `.ms` gate), not
ratified by a Group session.

## The decision

### What a hash-typed entry covers

ONE certificate, identified by the SHA-256 of its whole
tbsCertificate TLV (tag + length + content — the bytes RFC 5280 4.1.1.2
kills). The entry is `EFI_CERT_X509_SHA256_SIGNATURE`: a 16-byte
EFI_TIME revocation date followed by the 32-byte digest, UEFI 2.10
section 32.4.1's hash-typed revocation form — deliberately the SAME
form real-world Microsoft dbx updates use for revoked CAs, so the
stub's parser is exercised against a shape the outside world actually
ships. Not the whole-certificate dbx form (ADR-0014's byte-match and
issuer+serial pair rules, which stay implemented and controlled) —
those catch an exact known cert; the TBS-hash form is the compact,
collision-hardened identity for revocation updates, and the two forms
coexist as independent parser branches with independent refusals. A
re-issued leaf (new key, same identity) has a DIFFERENT TBS hash, so
this form revokes the compromised key's certificates exactly — not the
anchor, not the identity — which is the correct scope per ADR-0028's
one-hop hierarchy: the anchor pins the issuer, so killing one leaf's
certs never needs the root ceremony.

### When it takes force

The revocation moment is the entry's EFI_TIME, compared as six civil
fields against the FIRMWARE clock the boot's first gate already
fetched — no calendar arithmetic, no timezone interpretation. An entry
whose revocation date is at or before the boot clock is IN FORCE and a
hash match is a refusal; a future-dated entry is WALKED and COUNTED
(not skipped, not refused) — revocation data that hasn't taken force
yet is a fact the transcript names, never a silent no-op and never a
pull-the-trigger early. Both directions have named controls (the
harness's future-dated 2400 fixtures and the generated 2020-dated
negatives, one on each side of the gate).

### dbx before db

An in-force hash match refuses the boot EVEN IF the same leaf's
certificate is present in db and in MokList — the gate's revocation leg
is precisely this configuration (leaf enrolled in db AND shim's
MokList, its TBS hash in dbx, boot MUST fail). Three independent
readings of the same trust state must disagree with the revocation,
and the revocation must win: that is what makes a dbx entry meaningful
rather than advisory.

### Who refuses, and what counts

Two enforcement paths exist and the gates must not silently prefer one:

1. **The firmware's own check** (this OVMF build's SecureBoot
   verification, which reads the same dbx before the stub could run) —
   the boot fails at the boot manager, "Access Denied", and no stub
   output appears.
2. **The stub's own revocation walk** over the REAL firmware-served dbx
   variable — the boot reaches the stub, which verifies the carrier
   chain first and then refuses with its named refusal
   ("a hash-typed entry covers the signer certificate's
   To-Be-Signed"), taking the boot down before any kernel bytes load.

Either refusal is a PASS on the leg, but the gate prints WHICH one
fired, because they prove different things: (1) proves the composed
store is shape-correct for the firmware's own reader; (2) proves the
stub's parser handles the real bytes this firmware serves. The transcript
therefore records the served shape unconditionally —
`dbx: firmware served <n> signature-list bytes; type GUID (...)` — so
a served-vs-composed mismatch or a classification drift is on the
screen, not inferred from a refusal that did not happen.

### What this gate does NOT claim (the composition boundary)

The gate composes its dbx through virt-fw-vars' `--set-dbx`, whose
authenticated-update header (EFI_TIME + WIN_CERTIFICATE_UEFI_GUID,
per virt-fw's own `empty_auth_header()` — NOT an edk2
CreateTimeBasedPayload PKCS7 exchange) is a varstore-tooling
convenience, not cryptographic signature machinery: the PKCS7 body does
not exist; the payload rides in CertData; nothing verifies anything at
write or read time. This is the SAME composition trust boundary every
other enrollment in these gates uses (PK/KEK/db/MokList are all
virt-fw-vars writes), and it is scoped in `make-dbx-auth-update`'s own
documentation. What the gate proves is the ENFORCEMENT side — the real
firmware serving real bytes through real REAL verification paths and
the walk acting on them — not the UPDATE side. Production revocation is
performed through the platform's authenticated dbx-update path (a
KEK-signed EFI_VARIABLE_AUTHENTICATION_2 package at real SetVariable
time, or the vendor's signed dbx distribution); that machinery is a
separate surface, not weakened or faked by anything here, and no gate
in this repo pretends to test it.

## Consequences

- `dbx.rs`'s hash-typed branch semantics are pinned three ways: the
  host harness (five generated dbx refusals + skip/future controls),
  the stub's every-boot embedded negative controls, and now the `.ms`
  gate's firmware-served leg. A parser drift fails at least one of
  them; a GUID-classification drift fails the ADR-apparatus from
  ADR-0028's record (the spec-canonical fixture check) in addition.
- A caught-in-the-wild compromise procedure is now concrete: issue the
  replacement leaf (ADR-0028 rotation), compose a dbx entry carrying
  the dead leaf's TBS hash (the `make-dbx-hashlist` tooling shape),
  and distribute it through the platform's real update path. The gate
  proves the enforcement end-to-end the moment that entry is present,
  with no stub rebuild.
- NOT decided here: dbx-update signing ceremonies, automated
  revocation pipelines, or SHA-1/SHA-512 hash-type support (the walk
  deliberately reports unknown hash types as skipped facts — the
  controlled surface stays X509 and X509-SHA256). Each is future work
  with its own ADR if needed.
