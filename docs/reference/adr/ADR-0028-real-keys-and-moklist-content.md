---
title: Real Keys for ADR-0014 — One-Hop Hierarchy and the MokList Content Decision
document_id: ADR-0028
version: 1.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-10-29
updated: 2026-10-29
ai_assisted: true
depends_on: [ADR-0014, NPC-001]
---

# ADR-0028 — Real Keys for ADR-0014: Hierarchy and the MokList Content Decision

## Context

## The decision

ADR-0014 proved the chain on **generated** keys. That is a test property,
not a posture statement: a generated root lives in the same directory as
the code that verifies it, so "the stub trusts the right root" and "the
stub trusts *a* root" are indistinguishable from inside the build. Real
keys separate those by physical distance, and every step of the
hierarchy below is chosen so the distance can be *checked* (a ceremony
log, an offline medium, a date-stamped filename) rather than assured by
convention.

### The hierarchy

A ROOT and a LEAF, no intermediate:

```
PROD-ROOT-CA       offline, never on a build host
    └── PROD-LEAF  per-signing-host image signer
         cert has: CA:FALSE crit, digitalSignature/NL crit
```

Not a two-tier tree. uefi/src/trust.rs implements EXACTLY the one-hop
walk: the leaf's TBSCertificate is verified over the pinned single
anchor's RSA SPKI, and anything else (a deeper chain, a stray anchor, an
intermediate presented as an anchor) is refused by a specific named
refusal this repo's every-boot controls already pin. Therefore any
two-tier (root → intermediate → leaf) production design is a DIFFERENT
ADR with DIFFERENT walk code, not a stronger variant of this one; this
ADR models the chain that actually runs.

The intermediate's *policy* role is played by geometry instead: the root
is offline, the leaf is generated on the signing host and issued BY the
root (tools/make_carrier.sh does exactly this already, and the gate
composes the issuing db), and every change in signing authority is
constrained by geometry to require the root's interaction.

### What lands in a real MokList

Enrollment via `MokList` is a user hand on a trust root, so the content
decision is "what ust a user be able to grant by typing a passphrase":

1. The **root CA** -- rejected. Enrolling it grants everything the
   root can ever issue, including future leaves the user never saw;
   the anchor stays rather than pinning "this signer".
2. The **leaf signing cert** -- chosen as the default. The user grants
   *this signer*, now, on this machine; future leaves need a fresh
   enrollment the user can see. Revocation is by TBS hash in dbx
   (ADI, the `EFI_CERT_X509_SHA256_RULE` path `uefi/src/dbx.rs`
   implements, which both controls and gates pin), which kills an
   enrolled-away leaf on every machine still holding it - no weaker
   than a hash-grant that names the anchor itself.

A future two-tier design (separate ADR) would likely want the
intermediate to be enrollable in real MokList usage (the "trust this
vendor's entire future issuance" grant, which is what real-world
distro-level MOK enrollment actually means). Under the verified
one-hop walk that is out of scope; the single-store dbx revocation
pin covers revocation of an on-chain leaf exactly as it covers rest.

### Issuance, rotation, revocation, and offline posture

- **Issuance.** The leaf key lives on the signing host; the root key
  lives offline with its cert. Chain issuance is the one call
  `make_carrier.sh` has proven safe to the walk (`openssl x509 -req`
  signed from the root's key and cert); the Africa
  (i.e. the subject/keyUsage shape) must match the walk's refs, and
  production issuance MUST be done with the same extensions the
  generated chain's stub already proved (CA:FALSE critical,
  digitalSignature critical, no pathLen).
- **Rotation.** Rotate the leaf freely, forever: the anchor pin
  attaches to *the root*, not to any leaf, so a NEW leaf validates
  after a stub/em-store rebuild with no anchor change (this is the
  whole reason the chain has a one-anchor design). Rotating the root
  is a re-enroll (regenerate the enforcing store and update
  `trusted-root.der`); it should be a ceremony, not a build step.
- **Revocation.** The dbx hash path is the only revocation surface
  this codebase implements, and is described in ADR-0014's dbx
  controls. A compromised leaf is handled by a dbx TBS-hash entry; a
  compromised root is handled by the offline posture above (the root
  is not reachable to be compromised in mass).
- **Physical posture.** Production private keys are not in the
  repository, are not on the build host's disk, and are kept on
  encrypted media in a physically controlled location; the tooling
  consumes keys by path, never generates them at build time (the
  generated chain in `make_carrier.sh` exists solely for the gates,
  and is created on first run, not at this repo's design time).

### Tooling

`tools/make_carrier.sh` keeps its generated-key path: that is the
witness what the CI gates (host proof, plain UEFI gate, .ms secboot
gate) prove. There is no separate production tool here; instead, the
enforcing gate now composes a real MokList too (see the .ms
secboot script below), so the testing story covers the enrollable
surface, and production enrollment is done by the real tool (mokutil
or MokManager) against the same surface with the same content rule
(the leaf cert, not the root cert).

The gate's MokList enrollment is the decision's proof artifact: the
contents it writes are exactly the shapes mok.rs can verify, in the
GUID/name/attributes shim's SHIM_LOCK_GUID -- verified line-for-line
against shim's `lib/guid.c` -- expects.

## Not required yet

- Hardware-backed key storage (TPM, smartcard, HSM). An offline
  encrypted medium is sufficient for the ceremony volume this repo's
  release story implies; hard ADR if it ever changes.
- Automated revocation (a CI watcher that reacts to a leaked key and
  composes dbx entries). Manual dbx composition through existing
  tooling is the posture; automation is a followup ADR when a
  monitorable leak channel exists.
- A MART-aligned root ceremony (witnessed attack paths on key
  generation). This ADR's root lifecycle is the same one-shot flow
  make_carrier.sh already exercises for the generated chain, applied
  to offline media the operations team controls.