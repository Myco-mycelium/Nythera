---
title: Reject Domain-Grouped NPS Renumbering
document_id: ADR-0017
version: 1.0.0
status: Rejected
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, NPC-003, NPC-004]
---

# ADR-0017 — Reject Domain-Grouped NPS Renumbering

## Context
An external review proposed reorganizing NPS identifiers from a flat
sequence (NPS-001..017) into domain-grouped ranges — NPS-100 for
Kernel & HAL, NPS-200 for Core Platform, NPS-500 for Gaming, and so on —
reasoning that this would make the specification set easier to grow
without renumbering later.

## Decision
**Rejected.** NPS identifiers remain sequential and permanent.
Domain-grouping — the actual goal behind the proposal — is achieved
differently: every NPS document already carries a `subsystem` front-matter
field (`core-architecture`, `storage`, `runtime`, `security`, `gaming`,
`ai`), and `SPECIFICATION_INDEX.md` already groups and can be sorted by
that field. That gets the reviewer's real objective — being able to see
"everything in Gaming" at a glance — without touching the identifier
itself.

## Rationale

1. **Document identity is meant to be permanent.** NPC-001 §5 defines a
   document lifecycle (Draft → Review → Accepted → Deprecated/Rejected)
   precisely so that once something is `Accepted`, it's a stable reference
   point other documents can safely cite. NPS-001 through NPS-017 are
   already cited by ID across dozens of `depends_on` fields, cross-
   references in prose ("per NPS-002 §4"), the capability registry, the
   benchmark plan, and now the requirements work in this same change.
   Renumbering isn't a metadata edit — it's rewriting every one of those
   citations correctly, by hand or by a script trusted not to silently
   corrupt a reference, across a repository that has no automated
   citation-integrity check yet (see the Consequences section below).

2. **This is exactly the failure mode NTM-000 §4 ("Longevity") warns
   against.** A decision made for near-term tidiness that requires
   touching every existing artifact is the opposite of "evaluated... for
   the computing landscape ten or twenty years into the future." Real
   specification bodies that deal with this at scale — IETF RFCs, IEEE
   standards — do not renumber published documents when a new category
   needs room; they let the sequence be sparse and messy and rely on an
   index, exactly as `SPECIFICATION_INDEX.md` already does here.

3. **The premise undersells how much room a flat sequence actually has.**
   "Instead of renumbering everything" is a real cost to pay now for a
   problem — running out of tidy sequential space — that a four-digit or
   even three-digit sequential ID doesn't meaningfully have. NPS-001..017
   after ten milestones suggests decades of runway before three digits
   becomes a real constraint, if it ever does.

4. **A hybrid is available if a stronger form of this is wanted later**:
   `SPECIFICATION_INDEX.md` could be extended to render as a
   domain-grouped view (already partially true, since it's organized by
   subsystem section today) without deprecating a single existing ID.
   That path stays open; renumbering forecloses it by spending the
   "clean identifier space" budget for a benefit the index already
   provides.

## Alternatives Considered
- **Renumber immediately, before the set gets larger** (the reviewer's
  proposal) — rejected per the reasoning above; "before it gets larger"
  is backwards — the set is precisely large enough now that renumbering
  has real cost (17 documents' worth of cross-references) and precisely
  small enough that the cost is contained if paid *now* versus later,
  which makes now the worst time to discover a citation was missed, since
  there's no tooling yet to catch it.
- **Partial renumbering (only future documents get domain-grouped IDs,
  NPS-001..017 stay as-is)** — rejected; produces a permanently
  inconsistent numbering scheme (some sequential, some domain-grouped)
  that's more confusing than either pure approach, violating NTM-000 §4
  ("Simplicity").
- **Do nothing about domain visibility** — rejected; the reviewer's
  underlying concern (grouping by domain) is legitimate. Addressed
  instead via the existing `subsystem` field and index, at zero
  citation-breakage cost.

## Consequences
- No existing document changes as a result of this ADR.
- If the specification set grows large enough that `SPECIFICATION_INDEX.md`
  alone stops being a sufficient domain-navigation aid, the fix is a
  richer index or generated per-subsystem index pages — not renumbering.
- This is the first ADR in the project to reach `Rejected` status,
  exercising a document-lifecycle path (NPC-001 §5) that had only been
  used for `Draft`/`Proposed`/`Accepted` until now.

## Status
Rejected — 2026-07-13.
