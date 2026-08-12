# How to Propose an NPS or ADR

*Applies to: anyone proposing a technical specification (NPS) or an
architecture decision (ADR).*

## When you need this

You want to change how Nyrqis works — a new subsystem, a new contract, a
new architectural decision. Both proposal types follow the same process
(NPC-001 §6); they differ in what they record (NPS = "here is how X works,"
ADR = "here is the decision to do X and why").

## Steps

### 1. Say what problem you're solving, in writing

Open the proposal with a plain description of the problem, the proposed
change, and the alternatives you considered (NPC-001 §6.1). ADRs have a
fixed skeleton for this: Context / Decision / Alternatives Considered /
Consequences (see [ADR-0001](../reference/adr/ADR-0001-diataxis-mkdocs.md)
for a short, clean example).

### 2. State which Manifest principles it advances

The proposal MUST state which NTM-000 §4 principles it strengthens and
confirm it does not violate NTM-000 §5 ("What Nyrqis Will Never Become")
(NPC-001 §6.2). If you can't articulate either one, that's a signal the
proposal isn't ready.

### 3. Write the front-matter

Every normative document carries YAML front-matter: `title`,
`document_id`, `version`, `status`, `owners`, `created`, `updated`, and
`depends_on` (NPC-001 §4). Start with `status: Draft` — nothing skips the
lifecycle (NPC-001 §5).

For `depends_on`, follow the cycle-checker's convention: list only
documents *this* document depends on. Don't list a document back just
because it cites you — that's how cycles form
(`tools/check_depends_on_cycles.py`, see
[Verify the Docs Dependency Graph](check-dependency-cycles.md)).

### 4. Use RFC 2119 language, and mean it

Write **MUST** / **MUST NOT** / **SHOULD** / **MAY** per RFC 2119 (NPC-001
§1). Reviewers will treat a **MUST** as an enforceable promise. If you
can't defend it, use **SHOULD** or delete it.

Two honesty rules that apply to the whole project:

- No fabricated performance or compatibility numbers (NPC-002 §5.2). If a
  section needs a number you don't have, either mark it pending benchmark
  data or block the document's acceptance on it — the project holds
  several documents at `Draft`/`Proposed` for exactly this reason.
- No unverified claims about external systems in a document past `Review`
  (NPC-002 §5.1).

### 5. Tag the affected owners, record the decision

Affected subsystem owners MUST be tagged for review (NPC-001 §6.3). The
Architecture Group then records `Accepted`, `Rejected`, or `Deferred`
(NPC-001 §6.4). A `Draft` may not become `Accepted` without at least one
recorded Architecture Group review (NPC-001 §5).

Emergency security fixes may bypass steps 1–4 but MUST produce a
retroactive ADR within 14 days (NPC-001 §6).

### 6. Update the index files in the same commit

NPC-001 §6.5 is not optional: the same change set that adds or accepts a
document MUST update:

- [`SPECIFICATION_INDEX.md`](../00-platform/004-SPECIFICATION_INDEX.md) —
  add/update the document's row and revision history
- [`REPOSITORY_STATE.md`](../00-platform/REPOSITORY_STATE.md) — the living
  status snapshot
- [`CHANGE_REQUEST_LOG.md`](../00-platform/CHANGE_REQUEST_LOG.md) — a new
  `CR-XXXX` entry

## Checklist

- [ ] Problem, proposed change, and alternatives written down
- [ ] Manifest principles named; NTM-000 §5 confirmed unviolated
- [ ] Front-matter complete, `status: Draft`, `depends_on` cycle-free
- [ ] RFC 2119 language used precisely
- [ ] No fabricated numbers or unverified external claims
- [ ] Owners tagged; Architecture Group decision recorded
- [ ] All three index files updated in the same commit
- [ ] `python3 tools/check_depends_on_cycles.py` still passes
