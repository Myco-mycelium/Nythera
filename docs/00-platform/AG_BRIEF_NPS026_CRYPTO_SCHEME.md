---
title: AG Review Input — NPS-026 §6.3 Concrete Crypto Scheme (the NPC-002 §6.2 package)
document_id: AG-BRIEF-2026-09-CRYPTO
version: 1.0.0
status: Proposal — suggest-side review input for the dedicated human crypto review (NPC-002 §6.2); not a decision
owners: [Nyrqis Architecture]
created: 2026-09-21
ai_assisted: true
depends_on: [NPS-026, NPS-027, NPS-028, ADR-0014, ADR-0023, AG-AGENDA-2026-09, NPC-001, NPC-002]
---

# AG Review Input — The Concrete Crypto Scheme for NPS-026 §6.3

**What this document is:** the propose-side package for the review
NPC-002 §6.2 reserves — the concrete cryptographic scheme behind the
§6.3 trust mechanism the Architecture Group decided on 2026-09-21
(decision log D3, the ADR-0014 mirror). It proposes algorithms and
formats, records the implementation evidence that already exists, and
frames the judgment calls a human reviewer must make. The Group
reviews it alongside NPS-028 (the implementation surface).

**What this document is not:** a decision, and not a substitute for the
dedicated human review. NPC-002 §6.2's rule stands: nothing here is
settled until that review concludes. Every recommendation below is
overridable, and the reviewer is expected to attack the choices, not
rubber-stamp them.

---

## 1. The review's three questions

1. **Are the proposed primitives sound for each §6.3 role?**
   (manifest signing, key identity, revocation-list authenticity,
   key-store integrity.)
2. **Do the shipped implementation's choices match the proposal — and
   if the proposal changes, is the migration honest about it?**
3. **Which parameters are frozen now vs. deferred to implementation
   validation?**

## 2. Proposed scheme, per role

| Role | Proposal | Rationale | Status in the tree |
|------|----------|-----------|--------------------|
| Manifest/package signature | **Ed25519** (PureEdDSA, RFC 8032) over a SHA-256 digest of the canonical manifest + integrity trees | Deterministic (no nonce failure mode), fast verify, small keys/signatures (32/64 B), misuse-resistant — the right shape for a publisher signs / many devices verify asymmetry | **Shipped** (`backend/package_signing.py`, PyNaCl, fail-closed — no stub fallback by design) |
| Hash | **SHA-256** (the integrity trees' existing hash, NPS-026 §7) | One hash everywhere; collision resistance adequate for signatures over digests | Shipped, shared with §7 |
| Key identity | **Full 32-byte public key, fingerprint = SHA-256(public key), hex; the first 16 bytes (32 hex chars) are the display form** | The display form is what enrollment UIs and audit records show (NPS-028 §3.3's "one spelling"); full-key bytes remain the identity | **Shipped deviates**: `package_signing.py` uses `key_id` = first 8 bytes of the raw key — see §4, gap G1 |
| Revocation-list authenticity | **Ed25519 signature by the platform root set** (any-root quorum MAY, single-root MUST verify) over the canonical list + monotonic sequence number | Reuses the one signature scheme; NPS-028 §5.2's replay rule is structural | Not yet implemented (no revocation channel exists — NPS-028 §5) |
| Key-store at-rest integrity | **Envelope encryption via the ADR-0023 key manager** (Rust-held KEK, XChaCha20-Poly1305 wrapping) over the store's persisted form | One custody story for both trust anchors' material; the NPS-026 §14 hardware-root convergence question stays open, not prejudged | Partially shipped (ADR-0023's manager exists; the package store does not — NPS-028 §3) |
| Root-set distribution | **Anchors in the OS image** (§6.3.1), verified by the image's own integrity machinery (NPS-006 §6) | The boot anchor analogue; no network trust at first boot | Not yet implemented (no root set exists) |

**Deliberately NOT proposed:** RSA (signature size, parameter-picking
burden), ECDSA over NIST curves (nonce fragility without RFC 6979 —
Ed25519 subsumes it), any novel construction, and any homegrown AEAD.
The scheme is deliberately boring; the review's value is in attacking
the composition and the parameters, not the primitive choice.

## 3. The implementation evidence (verified 2026-09-21)

The scheme is not hypothetical — the signing half **ships today**:

- `backend/package_signing.py`: Ed25519 keypair generation/load,
  `sign_package` / `verify_package` over SHA-256 digests of canonical
  manifest bytes; **fail-closed** (PyNaCl absence raises
  `PackageSignError`; there is deliberately no stub fallback — the
  docstring records why: a forgeable stub would fail closed only by
  accident).
- `backend/update_signing.py`: delta-update verification that checks
  signature presence, validity, and trust **before** any filesystem
  mutation (the FIND-PACKAGE-003 attack surface, closed at this layer
  for updates).
- `backend/package_repo.py`: a **signed repository index** — the
  client verifies the index before downloading anything; an
  untrusted-key index is an error, never a warning.
- Test evidence: `test_package_signing.py` (10), `test_package_repo.py`
  (16), `test_update_signing.py` (11) — 37 tests over the shipped
  scheme, including cross-verification between generator and verifier.

What the shipped code does **not** yet carry (and must not pretend to):
expiry checking, revocation state, enrollment flow, the platform root
set, and key-store persistence. Those are NPS-028 §3/§5/§6's build
items, not gaps in what exists.

## 4. Known gaps between the shipped scheme and the §6.3 mechanism

| ID | Gap | Proposed resolution | Reviewer call |
|----|-----|---------------------|---------------|
| G1 | `key_id` = 8-byte key prefix; §6.3/NPS-028 §3.3 want a hash-based fingerprint | Adopt SHA-256(public key), 32-hex display form; keep the raw key as identity; migrate `key_id` fields with a version marker | Confirm fingerprint length + display form |
| G2 | No expiry/revocation checking in `verify_package` | Add status evaluation at verification time (NPS-028 §4 stage 3) once the store exists | None — mechanism is decided; ordering only |
| G3 | Signature covers manifest + trees, but canonicalization is the module's own JSON byte form | Freeze the canonical form in NPS-026 (with the §9 serialization decision) so independent implementations cannot diverge | Which canonical form; whether it waits for §9 |
| G4 | Root-set format and quorum rules undefined | Define the root-set document (keys + sequence + signature) alongside the revocation list format | Quorum semantics (MUST single-root verify; MAY root quorum) |

## 5. Parameters: freeze now vs. defer

**Freeze at review:** the primitive set (Ed25519 + SHA-256 +
XChaCha20-Poly1305 wrapping via ADR-0023), the fingerprint definition
(G1), the fail-closed posture as a normative requirement, and the
"one verification pipeline" rule (NPS-028 §4).

**Defer to implementation validation (NPS-026/028's exit from Draft):**
revocation-list cadence and stale-list bounds (NPS-028 §5.3), the
root-set's initial membership, key-rotation operational cadence, and
the canonical serialization (G3) until NPS-026 §9 decides binary vs.
structured text.

## 6. What the reviewer must judge

1. Primitive soundness per role (§2) — including whether Ed25519's
   determinism is being leaned on correctly given the canonical-
   binding question (G3).
2. The four gap resolutions (§4) — G1's migration and G3's sequencing
   are the real decisions; G2/G4 are confirmations.
3. The freeze/defer split (§5) — nothing proposed here should prevent
   NPS-026/028 from exiting Draft when implementation validation lands.

On a positive review, the disposition is: the scheme text lands in
NPS-026 (a v1.3.0 amendment adding §6.7 "Concrete scheme (reviewed)")
with the reserved-status note removed, NPS-028's §2 fence narrows to
match, and REQ-SEC-0003's implementation gate opens. A negative review
returns concrete primitive objections to this document, not to the
trust model — the §6.3 mechanism is decided and is scheme-agnostic.

---

## What this package deliberately does not answer

1. **Whether the composition is side-channel-clean in deployment** —
   verification runs daemon-side (NPS-028 §3.2), but the review should
   confirm the shipped PyNaCl usage has no timing footguns worth
   re-implementing against (it should not — that is the point of a
   vetted library).
2. **Hardware-root convergence** (NPS-026 §14) — open there, unchanged.
3. **Post-quantum migration** — Ed25519 is not PQ-secure; the honest
   position is that package signatures are a PQ-migration-friendly
   surface (re-signable artifacts) and the review may wish to record
   that as a forward note rather than act on it now.

**End of Document**
