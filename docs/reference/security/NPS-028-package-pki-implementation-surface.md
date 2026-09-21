---
title: Package PKI Implementation Surface
document_id: NPS-028
version: 0.1.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nyrqis Architecture
created: 2026-09-21
updated: 2026-09-21
ai_assisted: true
review_cycle: Per implementation milestone
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, NPS-006, NPS-026, NPS-027, ADR-0018, ADR-0023]
---

# NPS-028 — Package PKI Implementation Surface

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
the **implementation surface** for the package trust machinery whose
*trust model* is specified in `NPS-026` §6 (signature and integrity
requirements) and §6.3 (the publisher key-trust mechanism, decided by
the Architecture Group on 2026-09-21, decision log D3) and whose
*threat analysis* is `NPS-027` (Accepted 2026-09-21, decision log D2).

It exists because NPS-027's acceptance left a named residual: the
planned threat-model phase list is closed, but **no PKI implementation
exists** — the package manager itself is still future work. This
document enumerates the components that implementation must provide,
the requirements each satisfies (REQ-SEC-0003..0006), the interfaces it
must offer, and the attack surfaces it will add. It does not restate
the trust model; it turns it into a buildable checklist.

It is a `Draft` **by dependency, not by deficiency**: its normative
anchors (NPS-026 §6.3, NPS-027) are Accepted, but no implementation
exists to validate section numbering, interface shapes, or the REQ
coverage table against. Like NPS-026, it exits Draft on implementation
validation (NPC-002 §5.1/§5.2), and the concrete cryptographic scheme
remains **reserved per NPC-002 §6.2** for dedicated human expert review
— nothing in this document chooses algorithms, key sizes, or formats.

## 2. Scope

This document covers the package-security implementation surface only:

- the **key store** (trust anchors, enrolled publisher keys, revoked/expired
  keys) and its persistence and access rules (§3);
- the **verification pipeline** stages every install/update/verify
  touchpoint must run (§4);
- the **revocation distribution channel** (§5);
- the **enrollment flow** and its protected-confirmation requirement (§6);
- the **package-event audit trail** over ADR-0018's mechanism (§7);
- the **new attack surfaces** the implementation itself introduces, to be
  folded into the threat model's surface enumeration (§8).

Out of scope, explicitly: the package manager's dependency resolution,
download, and transaction semantics (NPS-026 §10–§12's other halves, to
be specified by that document's implementation pass); the `.nygi`
image-format verification path beyond the signature stage (NPS-006 §6);
and any concrete cryptographic primitive (NPC-002 §6.2 reserve).

## 3. The Key Store

The key store is the root of the §6.3 mechanism; every other component
reads from it and only enrollment writes to it.

3.1. The key store **MUST** hold exactly three logical collections:
platform root anchors (shipped in the OS image per §6.3.1), enrolled
publisher keys (added only through §6 of this document's flow), and
revocation state (expiry dates per §6.3.3, plus the platform revocation
list's entries).

3.2. The key store **MUST** be immutable except through the enrollment
and revocation-update paths. An installed package **MUST NOT** be able
to read, write, or enumerate the store; verification runs in the
daemon's authority, not the container's (the NPS-022 §4 lesson:
capability enforcement must not live where the attacker's code runs).

3.3. Key store entries **MUST** record: publisher identity, key
fingerprint, enrollment source, enrollment timestamp, expiry date, and
current status (`trusted` / `expired` / `revoked`). The fingerprint is
the identity used in the audit trail (§7) and the enrollment UI (§6) —
one spelling everywhere.

3.4. **Custody.** The store's own integrity **MUST** be protected
equivalently to the vault key manager's custody model (ADR-0023:
Rust-held keys, envelope encryption at rest). The NPS-026 §14 hardware-
root convergence question applies here unchanged: whether the package
root set shares the boot anchor's hardware root stays an open question
in that document and is not prejudged here.

3.5. **Uninstall semantics.** Removing an enrolled publisher key
**MUST NOT** silently uninstall or stop verifying already-installed
content at launch (§6.3.4's ownership rule); it **MUST** behave as
revocation does — advisory at launch, hard block at the next
install/update/re-verification touchpoint.

## 4. The Verification Pipeline

Every package-verification touchpoint (install, update, re-verify,
and the NPS-006 §6 `.nygi` mount path) **MUST** run the same ordered
stages; a stage failing means the touchpoint fails, with the reason
reported (the no-silent-failure rule, NPS-006 §6.2's precedent):

1. **Parse and validate the manifest** (structure, checksums — corruption
   detection per FIND-PACKAGE-001's disposition).
2. **Resolve the signing key**: identify the publisher's key by
   fingerprint from the manifest's signature block; fail closed if the
   manifest references a key absent from the store (TOFU rejection,
   §6.3.1 — this is where the FIND-PACKAGE-003 attack dies).
3. **Check key status**: expired or revoked at this instant → hard fail
   (§6.3.3). Expiry is evaluated at verification time, not package time.
4. **Verify the signature** over the manifest and integrity trees
   (NPS-026 §6.2) using the resolved key.
5. **Check the launch-advisory set** (touchpoint-dependent): at install
   and update, a revoked-since-install publisher is a hard fail; at
   launch-time re-verification, it is an advisory notice per §6.3.4.

Stages 2–4 **MUST** be constant-time in the secret-independent sense
and **MUST NOT** leak key material through error messages or timing.
The pipeline **MUST** be the single verification path — no component
bypasses it (NPS-010 §7.1.1's one-mechanism precedent), and the audit
trail (§7) records every touchpoint's stage outcomes.

## 5. Revocation Distribution

5.1. The platform **MUST** distribute the revocation list out-of-band
from package downloads (§6.3.3, the ADR-0014 `dbx` analogue) — same
channel compromise as the package feed must not suppress revocations.

5.2. The list **MUST** be signed by the platform root set and **MUST**
carry a monotonic sequence number; a client **MUST NOT** regress to an
older list (replay-protected), and **MUST** apply a delivered list
atomically.

5.3. Delivery cadence and the stale-list posture (how long a client may
operate on a list older than N) are **implementation validation
questions**: the bounds belong to this document's path out of Draft,
informed by real channel behavior, and are intentionally not invented
here.

## 6. Publisher Key Enrollment

6.1. Enrollment is the only path by which a key outside the platform
root set becomes trusted (§6.3.2). The flow **MUST** present, in a
protected confirmation (the NPS-015 §5.5 pattern — unspoofable,
suggestion-side confirmation of a consequential act): the publisher
identity, the key fingerprint (§3.3's spelling), and the enrollment
source (where the key came from).

6.2. The confirmation **MUST NOT** be skippable by the enrolling party,
**MUST NOT** be dismissible by timeout alone, and **MUST** log the
completed enrollment — identity, fingerprint, source, and the
confirming actor — as a package-event audit record (§7).

6.3. Rotation (§6.3.5): a cross-signed rotation **MUST** verify the new
key against the old (chain of continuity) and **MUST** record the
rotation linkage in the store entry; a rotation that cannot cross-sign
is a fresh enrollment and follows §6.1 in full.

## 7. Package-Event Audit Trail

7.1. Every verification, install, uninstall, enrollment, and revocation-
update event **MUST** be recorded in the tamper-evident audit log
(ADR-0018's mechanism — the REQ-SEC-0006 disposition, reusing
FIND-AI-002's precedent of one log rather than a new design) with:
package identity, publisher identity and key fingerprint, version,
touchpoint type, and verification outcome per §4's stages.

7.2. Audit records are **append-only and non-blocking**: a recording
failure degrades to a reported warning, never to a failed verification
by itself — the audit trail is evidence, not a verification stage.
(This ordering is deliberate and is the one place a naive reading of
"record everything" would damage the pipeline; it mirrors the
ADR-0018 §6.5 discipline.)

## 8. New Attack Surfaces (for the threat model's next pass)

The implementation will add surfaces NPS-019's catalogue does not yet
carry; they are enumerated here so the next threat-model pass starts
from a list, not a blank page:

- `SURFACE-PKI-0001` — the key store's daemon interface (read paths,
  enrollment write path, revocation updates).
- `SURFACE-PKI-0002` — the revocation distribution channel and its
  client-side application (list replay, suppression, partial delivery).
- `SURFACE-PKI-0003` — the enrollment confirmation flow itself (UI
  spoofing is the FIND-AI-001 class at a different boundary; the
  NPS-015 §5.5 requirement is the control, its enforcement is new).
- `SURFACE-PKI-0004` — the verification pipeline's error reporting
  (reason strings as an oracle; §4's no-leak requirement is the
  mitigation to be validated).

Each maps to `TB-PACKAGE` and, for the store and pipeline, to the
**Malicious package publisher** and **Unprivileged local application**
profiles jointly — the local attacker wants to enroll a key or flip a
status; the remote one wants to suppress a revocation or substitute a
key. Analysis of these surfaces is **future threat-model work**, not
part of this document.

## 9. Requirements Coverage

| REQ | Requirement (ledger wording) | Addressed by |
|-----|------------------------------|--------------|
| REQ-SEC-0003 | Signature-based authenticity per NPS-026 §6 | §4 stages 1–4; NPS-006 §6.3 |
| REQ-SEC-0004 | User-enrollable, revocable keys; explicit first-install enrollment | §3, §5, §6 (the mechanism decided in AG decision log D3) |
| REQ-SEC-0005 | Base/overlay provenance distinction | Out of scope here — NPS-006 §6.4 owns it; §4 stage 5 consumes its verdict |
| REQ-SEC-0006 | Package events in the tamper-evident audit log | §7 |

REQ-SEC-0005's exclusion is deliberate: this document's boundary is the
signature/key machinery; overlay provenance is image verification and
belongs to the NPS-006 implementation pass. The coverage table exists
so the requirements ledger can cite a real section for each REQ the
moment implementation begins.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 0.1.0   | 2026-09-21 | Initial draft — the PKI implementation surface NPS-027's acceptance left as its residual: key store, verification pipeline, revocation channel, enrollment flow, audit trail, and the four new surfaces enumerated for the threat model's next pass |

---
**End of Document**
