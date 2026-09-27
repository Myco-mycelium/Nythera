---
title: AG Brief — Automatic Updates and Rollback (UPD-001, M14 Phase 4)
document_id: AG-BRIEF-UPD001
version: 1.0.0
status: Proposed
classification: Internal
owners:
  - Nyrqis Engineering
created: 2026-09-27
updated: 2026-09-27
ai_assisted: true
review_cycle: One sitting
depends_on: [UPD-001, NPS-026, NPS-027, NPS-028, NPS-011, NPS-019, NPS-020, ADR-0018, NPC-001]
---

# AG Brief — Automatic Updates and Rollback (UPD-001)

## 1. Status of This Document

This is the decision package for UPD-001 — the M14 Phase 4 "automatic
updates with rollback" item, staged as AG_AGENDA v2.3.3 Bundle F. It
follows the D3/D4/duality/DBG-PhaseB/CRY001 brief format: verified
evidence, options with consequences ledgers, one recommendation,
overridable. The brief proposes; the Group decides. Nothing in this
item has been implemented — that is the point of this sitting.

One correction is part of the evidence base: UPD-001 v0.2.0's surface
audit was recorded from truncated search output and understated the
wired surface; the untruncated re-run corrected it the same session in
UPD-001 v0.2.1 §2.1 (the 0.29.35 lesson, twice in one day). The
corrected audit is what this brief stages, and its load-bearing claims
are pinned re-runnable in the premise registry
(`upd001-audit-rollback-pin`, `upd001-audit-unwired-pin`).

## 2. The Verified Evidence (audited 2026-09-27, corrected + pinned same day)

**Rollback is NOT the missing concept.** Container/deployment-scoped
rollback is ALREADY WIRED end-to-end:

| Wired surface | Where | Posture |
|---|---|---|
| `rollback_to_snapshot` + deployment version rollback | `backend/container.py` (:18516, :20636) | dry-run DEFAULT — a plan is returned before any mutation |
| Five rollback IPC ops | `ipc/control.py`: `rollback_snapshot`, `rollback_deployment`, `get_rollback_candidates`, `rollback_bluegreen`, `rollback_canary` | dispatched, handler-backed |
| Five CLI verbs | `nyrqisctl.py`: `rollback-snapshot/deployment/candidates/bluegreen/canary` | operator-invoked |
| UI models | `ui/system_restore.py` (snapshots/restore/backup scheduling), `ui/update_manager.py` (update management model with `rollback_update`) | UI-layer, in-memory |

**The signed-package delta machinery — the precise gap.** The
GENERATION half is wired (`nyrqisctl_repo.py` imports
`create_delta_update` for `publish-delta`, the operator front-end for
the signed repository). The VERIFY/APPLY half is not:
`UpdateVerifier(...)`, `validate_rollback(...)`, and
`apply_delta_update(...)` each appear exactly once outside their
defining modules' tests — in their defining modules. Consumers: the
modules themselves and their own tests (`test_update_signing.py`,
`test_delta_update.py`, plus `test_package_repo.py` exercising
`apply_delta_update`). **No IPC op, no CLI command, and no daemon path
consumes the signed-package verify/apply machinery.**
`PackageManager.update_package` (the 2026-09-23 real-store wiring)
verifies the delta entry but does NOT apply it — it reports success and
flips status after verification. No self-update/platform-update code
exists anywhere (whole-repo non-test sweep: zero hits).

**The substrate is real and mostly Accepted:** NPS-026 (.nypkg
integrity trees, `PackageInstaller`), NPS-027 and NPS-028 (both
Accepted — the trust model PROMISES this surface), `package_pki.py`,
the signed repository index (`package_repo.load_index`, fail-closed),
the fetch half end-to-end (`registry_pull`: IPC op + CLI,
operator-configured `registry_url`), the SDK `RestoreManager`
restore-point primitive, and the ADR-0018 audit chain.

**The honest gap:** the SIGNED-PACKAGE verify→apply path is
library-complete and user-unreachable, and every POLICY question is
unanswered. Not cryptography; not the rollback concept.

## 3. The Regulatory Frame (what any option must satisfy)

Per NPS-027/NPS-028 (Accepted), signed updates and their PKI are the
sanctioned trust posture — an update surface that bypasses the shipped
verifier would contradict the record. Per ADR-0018, every accepted
update/rollback event is audit-chained (the
`create_audit_chain`/`append_audit_entry` pattern the debug op family
already uses). Per NPS-029, any consent record for automaticity is
per-identity and revocable. Option A requires NO new NPS-011
capability: `nyrqisctl packages update/rollback` rides the existing
operator-only CLI authorization, exactly as `debug bundle` does.
Option B additionally triggers the NPS-019/NPS-020 pass: a
daemon-initiated fetch is a new entry in the attack-surface
enumeration even to an operator-configured destination — the same
class boundary CRY-001 drew for telemetry.

## 4. Options and Consequences

### Option A — Compose-first, operator-invoked (the draft's recommendation)

Wire the shipped primitives into one audited sequence — verify →
restore point → apply → audit — behind `nyrqisctl packages
update/rollback`; rollback operator-invoked only, gated by the shipped
`validate_rollback`. UPD-001 §7 pre-stages the full build plan
(ordering pins, the no-direct-egress assertion, fail-closed without
PyNaCl).

- Pros: zero new crypto, zero new trust decisions, zero egress policy
  change; every primitive already tested; converts NPS-027/028's
  promise into a reachable surface; acceptance converts directly to
  landed work via §7.
- Cons: the "automatic" half is explicitly deferred; fleet users still
  have no unattended story.

### Option B — Option A plus opt-in automaticity

A: plus a policy record (cadence, apply window, NPS-029 consent), a
scheduled checker, and a health-gated auto-rollback per §5 Q4's
contract; the NPS-019/NPS-020 pass is a PRECONDITION, not a rider.

- Pros: delivers the item's name.
- Cons: new surface class; the health contract is the load-bearing
  safety property (a wrong auto-rollback is worse than a bad update);
  boot-time apply interacts with container liveness; policy questions
  Q2–Q4 need answers before any line lands.

### Option C — Observational close

Record the finding (machinery shipped-but-unwired) and close the item
as scoped-out until demand arrives.

- Pros: zero work, honest record.
- Cons: leaves the tested-but-unreachable signed-update surface that
  NPS-027/028 (both Accepted) visibly promise.

## 5. Recommendation

**Option A**, with UPD-001 §7 pre-staged so acceptance converts to
landed work without a re-planning session. §5 Q4 — the rollback
trigger/health contract — is THE open mechanism question this brief
flags (the CRY-001 spool-default analog): in Option A it stays
operator-judgment-only by construction, which is why A is safe to
accept before Q4 is answered. If the Group treats "automatic" as
non-negotiable, decide Q2 (scope) and Q3 (automaticity posture) first
and schedule the NPS-019/NPS-020 pass as B's precondition.

## 6. Decisions the Group Must Make (UPD-001 §5)

1. **Option choice:** A / B / C (the brief leans A).
2. **Scope:** packages only, or a platform/OS channel in this
   milestone (the live ISO is immutable by respin — re-imaging may BE
   the platform update story).
3. **Automaticity posture (if B):** opt-in vs opt-out; check-only vs
   check-and-apply; the NPS-029 consent record's shape.
4. **The rollback trigger / health contract:** what defines "update
   failed", and whether any automated gate may roll back without an
   operator.
5. **Restore-point retention** for update-path restore points (the SDK
   RestoreManager already cleans up old points; what is normative,
   where they live).

**What a decision unlocks:** A closes the M14 Phase 4 roadmap item
with a small, pinned implementation (no new NPS-011 capability, per
`debug bundle`); B additionally requires the NPS-019/NPS-020 pass; C
closes the item as scoped-out. DECISION-READY — purely judgment; no
code has landed for this item.
