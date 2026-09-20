---
title: AG Decision Brief — Standing Item D2, NPS-027 Package Trust Model (Draft → Acceptance)
document_id: AG-BRIEF-2026-09-D2
version: 1.0.0
status: Pre-read — awaiting the Group's decision (see AG_AGENDA.md, standing items)
owners: [Nyrqis Architecture]
created: 2026-09-20
ai_assisted: true
depends_on: [AG-AGENDA-2026-09, NPS-027, NPS-026, NPS-006, ADR-0014, ADR-0018]
---

# AG Decision Brief — Standing Item D2: NPS-027 (Package Trust Model) Acceptance

**What this document is:** an AI-drafted pre-read for the standing item
registered in `AG_AGENDA.md` (NPS-027 — Package Trust Model, threat
model Phase 7, Draft → review for Acceptance). The spec has existed
since 2026-08-12; this brief packages what the session needs to judge
it.

**What this document is not:** a decision. Per NPC-001 §11.1 the
suggest-vs-act boundary applies — the recommendation below is
overridable Group judgment, and nothing in the tree changes until the
Group's decision is recorded in `AG_AGENDA.md`'s decision log.

---

## The decision required

Accept `NPS-027` (Package Trust Model) as the completed Phase 7 of the
threat model, or hold/amend it. Acceptance closes the **planned**
threat-model phase list formally (Phases 1a/1b/2/3/4/5/6/7) and makes
the spec's dispositions binding guidance for the future package
manager.

## What the session reviews

`NPS-027` covers `TB-PACKAGE` (package publisher/source ↔ installed,
running software; NPS-018 §4) against the **Malicious package
publisher** profile, examining `SURFACE-FS-0001` (`.nygi` mount-time
verification) and — at the signature-verification boundary only —
`SURFACE-AND-0001` (APK signatures).

**The findings ledger (5 items, each with a recorded resolution):**

| Finding | Severity | One-line substance | Resolution |
|---|---|---|---|
| `FIND-PACKAGE-001` | High/High | Checksums detect corruption, not tampering — an attacker recomputes a valid checksum | **Disposition**: `NPS-026` §6's mandatory signature block is the response; the finding stands until NPS-026 exits Draft and verification enforces it |
| `FIND-PACKAGE-002` | High/Medium | The *governing* Accepted spec (NPS-006 §3.1/§6) still required only checksums — a Draft response it doesn't reference is not closure | `NPS-006` amended (v1.1.0 §6.3): signature-based authenticity per NPS-026 §6, with an explicit Draft-status note (NPS-010 §9 precedent); `REQ-SEC-0003` |
| `FIND-PACKAGE-003` | Medium/High | Publisher key enrollment/revocation: the *pattern* is named (ADR-0014 mirror), the *mechanism* is not — trust-on-first-use of a key delivered with the package it signs defeats the mechanism | `REQ-SEC-0004`; NPS-026 §6 to gain the design on its path to Accepted — **the design decision is routed to NPS-026's review, not invented here** |
| `FIND-PACKAGE-004` | Medium/Medium | Verification ends at the base image; the runtime view is base + unsigned overlay, and nothing constrains what a mod may shadow | `NPS-006` amended (v1.1.0 §6.4): verification MUST distinguish base/overlay provenance and constrain shadowing; `REQ-SEC-0005` |
| `FIND-PACKAGE-005` | Low/Medium | No audit trail for install/uninstall/verify events — a repudiation/forensics gap | `REQ-SEC-0006`, reusing ADR-0018's mechanism (the FIND-AI-002 precedent), not a new logging design |

**Already landed on the spec side** (verified 2026-09-20): `NPS-006`
v1.1.0 (2026-08-12) carries all three §6 amendments (signature
authenticity 6.3, overlay/base provenance 6.4, package-event audit 6.5)
and `REQ-SEC-0003..0006` are in the requirements ledger. The session is
therefore reviewing a *coherent* analysis whose spec-side resolutions
exist — not approving work that still needs doing.

**Honest scope limits:** like Phases 3 and 6, `NPS-027` reasons from the
specified guarantees of NPS-006/NPS-026 rather than from code — no
package manager or installer exists yet. Acceptance asserts the analysis
and the requirements, not an implementation.

## Options and the brief's recommendation

| Option | What it means | Brief's recommendation |
|---|---|---|
| **A. Accept NPS-027 as-is** | Status → `Accepted`; indexes updated; the planned threat-model phase list closes; the four new findings + disposition stand as recorded | **Recommended.** The analysis is internally consistent, its spec-side resolutions already landed (NPS-006 v1.1.0, REQ-SEC-0003..0006), the one genuinely open design (key enrollment/revocation) is explicitly routed to NPS-026's review rather than deferred into limbo, and the severities are defensible against the recorded attacker profiles. |
| **B. Accept with amendments** | Same, but the Group adjusts a severity, a disposition wording, or the REQ set before accepting | Viable if the session disputes specifics — the likeliest candidates are FIND-PACKAGE-003's Likelihood (High rests on first-install trust being universal) and the strictness of the §6.4 overlay-shadowing constraint. Amendments are cheap now; they get expensive after implementations rely on them. |
| **C. Hold at Draft pending NPS-026's acceptance** | Review the phases together so the trust-model story lands as one unit | Rejected: it couples two reviews of different natures (NPS-026 also needs its §6.3 *design*, which does not exist yet — holding NPS-027 hostage to it delays a completed phase for an open one) and leaves the planned phase list formally open for no analytical benefit. |

**Recommendation: Option A.** Accept NPS-027, record in the decision log
that FIND-PACKAGE-003's design decision is formally owned by NPS-026's
review, and let the phase list close.

## Consequences ledger — what each option mechanically triggers

| Option | Immediate tree edits | Follow-up work |
|---|---|---|
| A (accept) | `NPS-027` frontmatter/Status → `Accepted`; `004-SPECIFICATION_INDEX.md` + `adr`-style listings reconciled; `AG_AGENDA.md` decision log row; standing item disposed | FIND-PACKAGE-003's enrollment/revocation design lands via NPS-026's own path to Accepted; the PKI implementation follows the package manager (item 18's residual) |
| B (accept w/ amendments) | Option A's edits + `NPS-027` v1.1.0 amendment + possibly REQ ledger adjustments | same as A for the routed design decision |
| C (hold) | none beyond a decision-log row | the phase list stays open; NPS-027 needs a re-review cycle after NPS-026 |

## What this brief deliberately does not answer

1. **The key enrollment/revocation design itself** (REQ-SEC-0004) —
   bundled root set vs out-of-band vs user-enrolled, revocation
   propagation semantics. That is NPS-026's review decision, and this
   brief does not pre-empt it.
2. **Implementation sequencing** — no package manager exists; when PKI
   work begins and against which codebase is a roadmap question.
3. **Any change to NPS-006's already-amended §6 text** — v1.1.0 is
   recorded history; if the Group wants it changed, that is a new
   amendment, not part of accepting NPS-027.

---

**End of Document**
