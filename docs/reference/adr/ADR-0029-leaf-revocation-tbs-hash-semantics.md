---
title: Leaf Revocation by dbx TBS Hash — the Enforcement Semantics the Gates Enforce
document_id: ADR-0029
version: 1.2.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-10-29
updated: 2026-10-30
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
kills). The entry is an `EFI_CERT_X509_SHA256`: the 32-byte digest FOLLOWED BY
a 16-byte EFI_TIME revocation date (`EFI_SHA256_HASH ToBeSignedHash;
EFI_TIME TimeOfRevocation;` — UEFI 2.10 section 32.4.1 and edk2's
MdePkg `Guid/ImageAuthentication.h`, SignatureSize 16 + 48), the
hash-typed revocation form — deliberately the SAME
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

## Implementation Note (2026-10-30)

This ADR's Consequences bullet -- issue the replacement leaf, compose the
dbx entry carrying the dead leaf's TBS hash, distribute it -- now has
tooling, and the `.ms` gate's fourth leg now asserts what it used to
print as a note. No decision in this ADR changed; this note records what
was built against it and one firmware behaviour the gates can now state.

### The procedure as one step: `tools/rotate_leaf_ceremony.sh`

ADR-0028's rotation and this ADR's revocation procedure are one
ceremony, not two: issue a replacement leaf under the root (fresh key,
same extension shape the stub's leaf walk vets), sign the kernel with
it, and compose the DEAD leaf's `EFI_CERT_X509_SHA256` entry plus its
authenticated-update wrapping -- this ADR's "What a hash-typed entry
covers" and "When it takes force" sections in executable form
(EFI_TIME 2020-01-01, so any firmware clock after it is in force).

The tool's named checks encode this ADR's decisions rather than
restating them in prose: the replacement's To-Be-Signed hash must
DIFFER from the dead leaf's ("A re-issued leaf (new key, same identity)
has a DIFFERENT TBS hash" -- equal hashes would mean the entry revokes
the replacement), its serialNumber must differ too (the whole-
certificate form's scope), the replacement must verify under the root
and sign the kernel, and the composed blob must survive both virt-fw's
own `EfiVar` parser and a `virt-fw-vars --set-dbx` composition with the
read-back variable's size matched to the composed SigDB. Ceremony
posture is a control, not a comment: no explicit root pair, no run; a
root key with group/world bits, no run; nothing is written into the
enrolled chain. It runs as a CI step, so a broken issuance or
revocation path fails a build instead of a ceremony.

### The gate asserts the refusal party and the served bytes

"Who refuses, and what counts" above requires the gates not to silently
prefer one enforcement path and to record the served shape
unconditionally. Both are now assertions, and BOTH dbx parser branches
have a firmware-served enforcing leg of their own (a hash-typed store
and a whole-certificate store), each with the same enrollment (signer
in db AND MokList) and each required to be refused. Per leg: whichever
party refused is a verdict field; on the stub path the `dbx:
firmware served <n> signature-list bytes; type GUID (...)` line is
required, `<n>` checked against the SigDB the gate composed, and the
printed GUID normalized to the branch's canonical serialization
(`92a4d23bc0967940b420fcf98ef103ed` for `EFI_CERT_X509_SHA256`,
`a159c0a5e494a74a87b5ab155c2bf072` for `EFI_CERT_X509`) -- spec bytes
written in the gate, not repo constants. On the FIRMWARE path there is
no served-bytes line to check BY CONSTRUCTION (the stub never ran), so
that path's shape evidence is the composition-time virt-fw read-back
printed with the store plus the firmware's own refusal; a firmware
refusal alongside a printed served line is a contradiction and fails.
A boot that reached the stub without naming its refusal, or whose
served bytes differ from what the gate composed, is neither path and
fails.

### Observed firmware behaviour, re-measured AFTER the correction: the firmware enforces BOTH forms

On the distro `OVMF_CODE_4M.ms.fd` these gates use, with the payload
order this ADR now specifies, each leg reports its own refusing party,
signer enrolled in db AND MokList in both:

- **Whole-certificate form (`EFI_CERT_X509`): the FIRMWARE refuses.**
  The boot manager reports "Access Denied" for the leaf-signed image
  and no stub output appears at all -- path 1.
- **Hash-typed form (`EFI_CERT_X509_SHA256`, the corrected bytes): the
  FIRMWARE refuses too** -- `[hash-typed] leg: refused by firmware;
  served shape firmware-refused`, no stub output. edk2's
  `IsCertHashFoundInDbx` compared the digest at the START of
  `SignatureData` against the leaf's To-Be-Signed hash and refused at
  the boot manager, which is this ADR's "What a hash-typed entry
  covers" enforced by the platform itself rather than by our code.

An earlier revision of this note reported the OPPOSITE split (firmware
enforcing whole-cert only, the hash-typed form left to the stub). That
reading was the field-order defect recorded below, not a firmware
limitation: against bytes no reader outside this repo could parse, the
firmware's silence looked like non-enforcement. Two consequences are
worth stating plainly, because they change what the legs prove:

- **The stub's own TBS-hash walk is now PREEMPTED on this firmware** --
  the firmware refuses before the stub runs, so path 2 does not fire on
  these images. Its coverage is the host harness over the same
  regenerated bytes, plus the probe leg, which shows the stub reading a
  composed dbx through the firmware and reaching Ready when the entry
  does not match (975 bytes, `EFI_CERT_X509` GUID `a159…`). The stub's
  walk stays in the build as defence in depth for firmware that does
  not enforce hash-typed entries; that is a platform question, not a
  claim these gates make.

### Correction (2026-10-30, same day): the payload's FIELD ORDER

An earlier revision of this ADR and of the code put `EFI_TIME` first and
the 32-byte digest second. Generator and parser agreed with each other,
so the host harness and the stub's every-boot controls all passed while
the bytes were wrong: `EFI_CERT_X509_SHA256` is `EFI_SHA256_HASH
ToBeSignedHash; EFI_TIME TimeOfRevocation;` — digest FIRST — and
edk2's `IsCertHashFoundInDbx` compares the digest at the START of
`SignatureData` and reads the date from `SignatureData +
DigestLength`. A time-first entry therefore matches no real firmware
and revokes nothing there; only this repo's own parser could see it.

The firmware probe written to look for an OVMF gap found the real cause
instead and is the reason this correction exists at all: with the
corrected order in `tools/patch_carrier_negatives.py`
(`make-dbx-hashlist`) and `uefi/src/dbx.rs` (`read_revocation`), the
hash-typed form is enforced by the firmware exactly as this ADR's
semantics describe -- measured on the re-run as `[hash-typed] leg:
refused by firmware`, where the pre-correction bytes on the same
firmware got as far as the stub. This is ADR-0028's "self-consistent-but-wrong"
failure class again, in the one place where two implementations of ours
could agree with nobody else — and it is why the gates now compare
against SPEC bytes (the type GUIDs, the served SigDB sizes) rather than
against each other's constants.

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
