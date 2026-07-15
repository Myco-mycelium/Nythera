---
title: Hash-Chained Append-Only Log for Capability Audit Records
document_id: ADR-0018
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, NPS-018, NPS-021]
---

# ADR-0018 — Hash-Chained Append-Only Log for Capability Audit Records

## Context
NPS-010 §8.1 requires every capability grant and revocation be recorded
in a user-inspectable form, but doesn't specify how that record resists
tampering. The threat model (NPS-021 §5.2, `FIND-CAPABILITY-002`)
identified this as a real gap: a compromised container or service with
write access to the audit store could falsify its own history, defeating
the transparency guarantee the audit log exists to provide.

## Decision (Proposed)
Adopt a **hash-chained, append-only log** for the capability audit
record: each entry includes a hash of the previous entry, so altering or
deleting any past entry breaks the chain in a way that's detectable
without needing a separate, independently-trusted signing authority.

- The log **MUST** be append-only at the storage layer — no in-place
  edits, no deletions, backed by NyFS's copy-on-write guarantees
  (NPS-004 §4.1) so a "modification" is structurally a new write, not an
  edit of existing blocks.
- Each entry **MUST** include: the capability affected, the action
  (grant/revoke/attenuate), the affected container, a timestamp, and the
  hash of the immediately preceding entry.
- Chain verification (detecting a broken or truncated chain) **MUST** be
  possible locally, without a network connection or third-party service,
  consistent with NTM-000's offline-first principle.
- This does **not** require a distributed ledger, proof-of-work, or any
  blockchain-style consensus mechanism — those solve a different problem
  (agreement among mutually-distrusting parties) that doesn't apply to a
  single-device local audit log. Hash-chaining alone gives tamper
  *evidence*; it does not need tamper *prevention* beyond what NyFS's
  append-only enforcement already provides.

## Alternatives Considered
- **Plain append-only log, no hash chaining** — rejected; append-only
  storage prevents in-place edits but doesn't prevent truncation (deleting
  the most recent N entries) from being silently undetectable. Hash
  chaining makes truncation detectable because the last remaining entry's
  hash no longer matches what a legitimate chain would produce at that
  length... actually more precisely: makes *any* removal detectable via
  chain discontinuity, not just truncation.
- **Cryptographically signed entries (each entry signed by a system key)**
  — rejected as the default; adds real key-management complexity (where
  does the signing key live, how is it protected from the same compromise
  that would falsify the log) for a marginal benefit over hash-chaining
  for the single-device, no-mutual-distrust threat model described in
  NPS-021 §5.2. **MAY** be revisited if a cross-device or multi-user audit
  scenario emerges that hash-chaining alone doesn't address.
- **External/remote audit log (write to a cloud endpoint)** — rejected as
  the default; would make a core security guarantee depend on network
  connectivity and a remote service, conflicting with NTM-000's
  offline-first principle and NPC-001 §10.2's cloud-sync-is-optional rule.
  A user **MAY** opt into remote audit backup as a separate feature under
  the existing `CAP-CLOUD-SYNC` opt-in model (NPS-016), but the local
  chain is the canonical, always-available record.

## Consequences
- NPS-010 §8 requires amendment to reference this mechanism explicitly —
  applied in the same change as this ADR.
- Chain verification becomes a concrete, testable operation, giving the
  eventual test suite (NPC-003 §5.3) something specific to exercise rather
  than a vague "audit log exists" check.
- Performance cost (computing and verifying hashes on every grant/revoke)
  is expected to be negligible relative to the operations themselves, but
  per NPC-002 §5.2 this is not asserted as a claim without a benchmark —
  it's a reasonable expectation given hash-chaining's typical cost
  profile, not a measured fact yet.

## Status
Proposed — pending Architecture Group review.
