---
title: Leaf Revocation by dbx TBS Hash — the Enforcement Semantics the Gates Enforce
document_id: ADR-0029
version: 1.5.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-10-29
updated: 2026-10-31
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
separate surface, and nothing here weakens or fakes it. As of
2026-10-31 its ACCEPTANCE half is no longer untested -- see "The UPDATE
side reaches firmware ACCEPTANCE" below, which also records that
closing it exposed two defects in this repository's own package builder.

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

## Implementation Note (2026-10-31): the UPDATE side reaches firmware ACCEPTANCE

The section above said the platform's authenticated-update machinery is
"a separate surface, not weakened or faked by anything here, and no gate
in this repo pretends to test it". The ACCEPTANCE half of that surface is
now tested on real firmware, and getting there found two defects in this
repository's own package builder -- each invisible to every host check
because the builder and the gate agreed with each other, the ADR-0028
failure class a third time.

### What runs now

`tools/make_dbx_update_package.sh` is the ONE builder: `build.sh` embeds
a KeK-signed package and a non-KeK-signed twin over the same list (a
hash-typed entry revoking the negative-only stray certificate, which
covers nothing this chain trusts), and the `.ms` gate's positive store
carries a one-byte trigger in the stub's own vendor namespace
(`NyrqisDbxUpdate`). When the trigger is present the stub writes the KeK
package to `dbx` with `EFI_VARIABLE_AUTHENTICATION_2`'s attributes, so
the FIRMWARE verifies the signature and decides; it then reads the
variable back and consumes the trigger. No other store carries the
trigger, so the revocation and probe stores' composed dbx entries are
never replaced.

Asserted in the positive boot: the non-KeK-signed package is REFUSED by
the firmware (first, so its stamp cannot explain the refusal), the
KeK-signed package is ACCEPTED, the read-back carries the exact
`EFI_CERT_X509_SHA256` entry the package signed, and the trigger is
deleted so a re-boot does not replay a now-stale stamp.

### Defect 1: the signed content is not (list || EFI_TIME)

UEFI 2.10 8.2.2 (and edk2's `VerifyTimeBasedPayload`) serializes the
signed bytes as

    VariableName (CHAR16, NO terminator)
    VendorGuid   (EFI wire order)
    Attributes   (UINT32, the SetVariable call's word)
    EFI_TIME     (the descriptor's stamp)
    Data         (the new value)

This ADR's earlier note, `dbx_update_gate.sh` and the first version of
the builder all signed `Data || EFI_TIME`. `openssl cms -verify` agreed,
because it was handed the same wrong content; `edk2` refused every
package with `EFI_SECURITY_VIOLATION`, which is how the defect surfaced.
The builder now signs the five-field serialization for `dbx` under
`EFI_IMAGE_SECURITY_DATABASE_GUID` with attributes `0x27`
(NV|BS|RT|TIME_BASED_AUTHENTICATED), and the gate asserts that layout
byte-for-byte from the spec's strings rather than from the builder.

### Defect 2: the descriptor carries a BARE PKCS#7 SignedData

edk2 reads `WIN_CERTIFICATE_UEFI_GUID.CertData` as a PKCS#7
**SignedData** SEQUENCE and checks the SHA-256 OID at a fixed `+13` into
it. `openssl cms -sign` emits a `ContentInfo` wrapper, which shifts every
offset by eight bytes (and the OID check fails). The builder now strips
the `[0]` element's content and embeds the bare SignedData, keeping the
wrapper only for its own `openssl` self-verification.

### The independent oracle, and what the gate now pins

Both defects survived because one implementation checked itself. The
gate now (a) rebuilds the signed content from the spec strings and
asserts every field's offset, and (b) cross-checks against
`sbsigntools`' `sbvarsign`: that tool's own signature must verify over
the content rebuilt from the pinned layout using ITS stamp. If this
repository's serialization were an invention, the oracle would disagree.

Measured verdict (the `.ms` gate's positive boot, real
`OVMF_CODE_4M.ms.fd`):

    dbx-update: trigger present -- this store asks for the authenticated write
    dbx-update: non-KeK-signed package REFUSED by the firmware = 0x800000000000001a
    dbx-update: SetVariable(dbx) ACCEPTED 0x5c6 bytes (KeK-signed auth2) -- EFI_SUCCESS
    dbx-update: read-back VERIFIED -- the firmware's dbx now carries the 0x5c-byte entry the package signed
    dbx-update: trigger consumed (deleted)

### What this still does not claim

The package is stamped deterministically (2027-01-01) rather than at
build time, delivery and rollout scheduling are still the platform's
business, and acceptance is measured on the one firmware build in this
tree that implements the surface (see the cross-build control below).
What changed is that a firmware now verifies one of these packages and
stores it: the acceptance half of the surface is measured, not asserted.

## Implementation Note (2026-10-31, second): the signing ceremony, and a
## cross-build control that keeps the acceptance claim honest

### The signing/distribution ceremony

`tools/sign_dbx_update_ceremony.sh` is the step between "a package can be
built" and "an operator ships it". It composes the revocation list for a
named certificate, signs it with the KEK, and then refuses to hand a
package over unless six properties hold:

1. **custody** -- the KEK private key has no group/world permission bits
   (the same first control `rotate_leaf_ceremony.sh` applies to the root);
2. **target** -- the certificate being revoked is not the trust anchor,
   because revoking the anchor refuses every carrier including the
   replacements a rotation is about to issue;
3. **signer** -- the signing certificate IS the enrolled KEK, compared
   byte for byte, where the enrolled KEK is named explicitly rather than
   assumed to be the signer (otherwise the check is a tautology);
4. **stamp** -- the new stamp is strictly newer than `--previous-stamp`
   when one is given, since firmware rejects a non-increasing time-based
   stamp;
5. **scope** -- the composed `EFI_CERT_X509_SHA256` list carries exactly
   the target's To-Be-Signed hash, once, owner-less, at the ceremony's
   stamp, pinned from the spec's list/entry sizes rather than from this
   repository's generator;
6. **binding** -- the PKCS#7 verifies over the signed serialization and
   the package's signer certificate is the KEK certificate.

Each refusal is a named FAIL, and `tools/update_ceremony_refusals_test.sh`
exercises all six paths plus a positive control, asserting the exit class
AND the specific message, so a check that silently stopped firing cannot
look like a passing ceremony. The ceremony writes a manifest beside the
package (package sha256, target subject/serial/TBS hash, stamp, signer,
delivery note) and states in that manifest what it does not do: deliver
or schedule.

### The cross-build control: acceptance is firmware SUPPORT

Measured 2026-10-31 by running the gate against the other firmware build
in this tree (`OVMF_CODE_4M.fd`, sha256 `ad23261d..`) with the same
enrolled store and the same package:

    nyrqis ubld: stub alive under UEFI; SecureBoot=absent
    dbx-update: trigger present -- this store asks for the authenticated write
    dbx-update: non-KeK-signed package REFUSED by the firmware = 0x8000000000000002
    dbx-update: SetVariable(dbx, NV|BS|RT|TBA, 0x5c6 bytes) = 0x8000000000000002 -- REFUSED by the firmware (named fact)

That build has no time-based-auth variable surface at all -- it answers
`EFI_INVALID_PARAMETER` to BOTH packages, the correctly-KeK-signed one
included -- so a full gate run there fails the positive leg, which is the
correct verdict for a build that cannot satisfy an enforcement claim.
Consequently:

- Acceptance is **firmware support measured**, not a property of the
  host gate: the bytes the `.ms` build accepts are refused outright by a
  build that lacks the surface.
- The gate carries this as an opt-in control
  (`SECBOOT_CROSS_BUILD_CODE=<code image>`), asserting on the second
  build: the stub ran, the store asked for the write, the KeK package was
  REFUSED (not accepted), and the status is the measured
  `EFI_INVALID_PARAMETER`. If a future build starts accepting, the
  control fails with the message that such a build belongs in the
  acceptance leg -- a red that names its own resolution.
- The control refuses to run against the same image as the main leg, so
  "a control against itself" cannot pass.

CI runs it on the weekly drift schedule only, because the distro's plain
OVMF build is an external input whose behaviour can change without a push
to this repository.

### What remains open (unchanged in kind)

Delivery, rollout scheduling and a fleet inventory are platform process;
real firmware beyond the builds measured here; and the `.ms` surface
remains the only acceptance measurement available. None of that is
claimed as covered anywhere.

## Implementation Note (2026-11-01, third): the fleet end, and a third
## firmware build carrying the same assertions

### The fleet end: a store that CARRIES a revocation vs a machine that
### ENFORCES one

Composition and acceptance were covered. What was still missing is the
observation an operator actually needs after a rollout: does THIS store
refuse THAT certificate. `tools/verify_dbx_store.py` answers it for an
edk2 varstore (read through `virt-fw-vars --output-json`) or for a bare
list / auth2 package, and it distinguishes the verdicts that mean
different things operationally rather than collapsing them into a
boolean:

- **revoked, effective** -- an enforced entry exists and its revocation
  date is not later than `--at` (default: today);
- **revoked, not yet** -- the entry exists but firmware will only start
  refusing from its `EFI_TIME`. REFUSED by default, because "we revoked
  it" is false today; `--allow-pending` accepts a deliberate schedule;
- **present, not enforced** -- the target's hash sits in a class the
  kernel's walk does not read (an `EFI_CERT_SHA256` image-hash list, or
  the SHA-1/224/384/512 forms ADR-0030 reports). REFUSED, naming
  ADR-0030: a store can look like it carries the revocation while no
  machine refuses anything;
- **absent** -- no enforced entry, including the case of no dbx variable
  at all.

`--absent <cert>` is the other direction: assert the certificate the
rotation issued is NOT revoked in that store, which is how an over-broad
revocation is caught before it is discovered as a fleet outage.

The To-Be-Signed hash is derived by the verifier's own DER walk, NOT by
calling the helper the ceremony signs with: a checker that shares the
signer's implementation can only restate it, and this repository has
already shipped one defect (the `EFI_CERT_X509_SHA256` field order) that
was exactly that. `tools/verify_dbx_store_test.sh` proves every verdict
bites (both enforced forms pass; absent, scheduled, ignored-class,
unreadable and over-broad each fail with their own named message) and
cross-checks the derived hash against `patch_carrier_negatives.py`, a
different implementation on purpose. It runs in CI on every push.

Measured on the gate's own stores, which is the cross-check that matters
most (firmware-composed data, not this tool's idea of it):

    $ ./tools/verify_dbx_store.py --store uefi/dist/VARS.ms-secboot-dbxrev.fd \
          --cert uefi/tools/trust/carrier-dev-leaf-cert.pem
      match: EFI_CERT_X509_SHA256 -- To-Be-Signed sha256, revoked 2020-01-01
      effective: 1 enforced entry(ies) revoke CN = Nyrqis dev carrier leaf ... at 2026-11-01
    PASS

    $ ./tools/verify_dbx_store.py --store uefi/dist/VARS.ms-secboot-dbxcert.fd \
          --cert uefi/tools/trust/carrier-dev-leaf-cert.pem
      match: EFI_CERT_X509 -- whole-certificate byte match
    PASS

and the dev ceremony's package, whose stamp is deliberately in the
future, is refused until that date as designed (`revoked only FROM
2027-01-01 ... pass --allow-pending`).

The operator sequence (custody, ceremony, artifact check, delivery,
fleet check, and a table mapping each refusal to its remedy) is
`docs/how-to/revoke-a-secure-boot-carrier.md`, which states the two
limits plainly: delivery/scheduling are platform process, and a firmware
without the time-based-auth surface answers `EFI_INVALID_PARAMETER` to a
correct package, which looks like a verification failure and is really
unsupported firmware.

### A third firmware build, same assertions

The cross-build control above measured a build that LACKS the surface.
The complementary question -- do the acceptance assertions hold on a
different build that HAS it -- was open, and the two images in this tree
cannot answer it: both are Ubuntu's `ovmf 2024.02` package, byte for
byte (`OVMF_CODE_4M.ms.fd` `1286f2a3..` is that package's `secboot`
image). Measured 2026-11-01 against Debian trixie's edk2 **2025.02**
(`OVMF_CODE_4M.ms.fd`, sha256 `1a462955..`, with that package's own
`OVMF_VARS_4M.fd` template) via the gate's existing overrides:

    OVMF_CODE_OVERRIDE=.../OVMF_CODE_4M.ms.fd \
    OVMF_VARS_OVERRIDE=.../OVMF_VARS_4M.fd \
        ./tools/qemu_uefi_ms_secboot_test.sh     ->  EXIT=0

Every leg held, including the acceptance sequence on the third build's
real firmware:

    dbx-update: non-KeK-signed package REFUSED by the firmware = 0x800000000000001a
    dbx-update: SetVariable(dbx) ACCEPTED 0x5c6 bytes (KeK-signed auth2) -- EFI_SUCCESS
    dbx-update: read-back VERIFIED
    dbx-update: trigger consumed (deleted)

so the acceptance claim is not an artefact of one edk2 version: a
2025.02 build verifies the same bare PKCS#7 `SignedData` over the same
signed serialization and stores it. The gate needed no change to run
there -- `OVMF_CODE_OVERRIDE`/`OVMF_VARS_OVERRIDE` already existed, and
the transcript names the firmware's sha256 in every verdict.

Not claimed: this is still one build per measurement, not a matrix; a
fleet may carry firmware older than 2024.02 or otherwise patched, and
that remains unknown until measured on that firmware.

## Implementation Note (2026-11-01, fourth): the cross dimension becomes an
## artifact, the fleet check becomes a report, and the runbook runs itself

### The matrix: which firmware builds were measured is now recorded, not
### remembered

Every acceptance measurement above was one gate run against one image.
The claim that travelled with it -- "acceptance holds on this firmware" --
could not be widened by re-reading any file, because the cross dimension
lived in whichever shell session ran the second build. `tools/firmware_matrix.sh`
turns that into an artifact: each entry is `<image>[:<expect>[:<vars>]]`,
each leg is the gate unmodified with `OVMF_CODE_OVERRIDE` set, and the run
ends with a table of firmware sha256 -> expected -> measured verdict, plus
`matrix-summary.json` for machine consumption.

The driver adds the accounting the single-image run cannot do, and each
piece is a way a matrix can lie:

- **one sha256 per entry, duplicates refused.** Two paths to byte-identical
  firmware would report two verdicts for one build and imply coverage that
  did not happen;
- **an expectation per entry.** Not every build in a matrix is expected to
  enforce. A build without the time-based-auth surface answers
  `EFI_INVALID_PARAMETER` to a correctly KeK-signed package, which is a
  real verdict (`refuse-auth`) and not a failure to be hidden or a pass to
  be mislabelled. A pass expected from such a build, or the inverse, is
  named as the mislabel it is rather than counted as a matrix failure of
  unknown kind;
- **the transcript is pinned to the entry.** The gate prints `firmware
  under test: <path> (sha256 <sha>)`, so a stale or shared log cannot
  satisfy a leg;
- **an inherited `SECBOOT_CROSS_BUILD_CODE` is cleared per leg.** Inside a
  matrix the matrix IS the cross dimension; inheriting the single-image
  control would re-run one build inside every leg and report it as
  coverage.

The driver's own verdict logic is tested hermetically against a stub gate
(`tools/firmware_matrix_test.sh`): both verdicts pass through, and a
mislabelled expectation, an unclassifiable failure, a duplicated build, a
missing image, an unpinned transcript, an inherited cross-build control and
both usage errors each fail with their own named message. It runs in CI on
every push; the matrix itself boots, so it is a scheduled measurement.

Measured 2026-11-01, four distinct builds, one run, `MATRIX EXIT=0` (the
two in-tree images are Ubuntu's `ovmf 2024.02` package and are named for
what they are):

    FIRMWARE                                     SHA256       EXPECTED     MEASURED     RESULT
    uefi/tools/OVMF_CODE_4M.ms.fd                1286f2a3217d pass         pass         OK
    .../root13/.../OVMF_CODE_4M.ms.fd (2025.02)  1a4629557443 pass         pass         OK
    uefi/tools/OVMF_CODE_4M.fd                   ad23261d4311 refuse-auth  refuse-auth  OK
    .../root13/.../OVMF_CODE_4M.fd    (2025.02)   624e06de18b4 refuse-auth  refuse-auth  OK

So the acceptance claim spans two enforcing builds of different edk2
versions -- both ACCEPT the KeK-signed package and verify the read-back --
and the refusal claim spans two builds of different versions that lack the
surface, both answering `0x8000000000000002` (`EFI_INVALID_PARAMETER`) to
the non-KeK control AND to the correctly-signed package. One run, one
artifact naming all four.

The first matrix run reported the two plain builds as *unclassifiable*, and
that was a real defect rather than an unknown: the gate's positive-leg
transcript printed only the lines its key-line `grep` selected, which did
not include `dbx-update` -- so the two facts POS_OK asserts (the SetVariable
status of each package) were judged but never shown. A consumer could not
see what the gate saw, and the driver's `refuse-auth` classification had
nothing to match. The pattern now includes `dbx-update`, which is why the
lines above are quotable from the transcript rather than from a re-run.

Not claimed: a fleet's firmware. Two builds with the surface and two
without say nothing about an OEM's 2019 build; the matrix makes adding it a
line, not a claim.

### The fleet report: one verdict per machine, and the distribution

`--fleet DIR` audits a whole collection in one call (`--fleet-glob`,
default `*.fd`; the machine is named by its file) and ends with a
per-machine line plus a count of how many machines hold each kind of
refusal, which is the sentence a rollout report needs:

    2 machine(s): 1 enforcing, 1 no-dbx-variable

Each failure carries a category (`enforcing`, `scheduled`, `no-dbx-variable`,
`absent`, `ignored-class`, `over-broad`, `unreadable`) so the summary counts
kinds rather than repeating prose, and `--json` carries the same category
per machine. Two accounting decisions are deliberate: an EMPTY collection is
refused (`auditing an empty fleet proves nothing`) instead of passing as a
clean audit of nothing, and a non-store file in the directory is ignored by
the glob rather than read as a machine.

### The runbook, executed

`docs/how-to/revoke-a-secure-boot-carrier.md` is a claim about tools, so
`tools/runbook_rehearsal.sh` executes it: it DELETES the generated trust
directory, rebuilds the chain, and then walks the page's own sequence --
the anchor refusal, the ceremony, the manifest's sha256 recomputed against
the artifact, the future-stamp artifact check in both directions, a store
composed from the package, a clean and a mixed fleet audit through
`--fleet`, and the over-broad `--absent` refusal. It closes by checking the
page still documents the flags it exercised and no longer shows the
superseded per-store loop, so the page cannot drift silently. It runs in CI
on every push.

Rehearsing it found a defect that had nothing to do with revocation:
`build.sh` failed on a fresh tree with `FileNotFoundError:
uefi/tools/trust/carrier-neg-stray-cert.der`. `tools/make_carrier.sh`
generated the stray certificate only as PEM, while `build.sh` composed a dbx
blob from its DER form -- so "a built tree", the page's first precondition,
was reachable only by someone who already had one. `make_carrier.sh` now
derives the DER from the PEM it just ensured (and re-derives it when the PEM
is newer), so a checkout builds from nothing. The rehearsal also caught its
own vacuity: a consistency grep against a wrong path passes silently, so
the page is resolved and checked before any grep runs.

## Consequences

- `dbx.rs`'s hash-typed branch semantics are pinned three ways: the
  host harness (five generated dbx refusals + skip/future controls),
  the stub's every-boot embedded negative controls, and now the `.ms`
  gate's firmware-served leg. A parser drift fails at least one of
  them; a GUID-classification drift fails the ADR-apparatus from
  ADR-0028's record (the spec-canonical fixture check) in addition.
- The fleet end has a verifier that is deliberately a SECOND
  implementation of the To-Be-Signed hash (`tools/verify_dbx_store.py`),
  cross-checked against the signing path rather than sharing it, so a
  defect of the "generator and parser agree with each other" class cannot
  hide behind the audit that is supposed to catch it. Its "present, not
  enforced" verdict is ADR-0030 made operational: a store may carry the
  identity in a class the walk ignores and thereby revoke nothing.
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
