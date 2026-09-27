---
title: Automatic Updates and Rollback — design note for the M14 Phase 4 item
document_id: UPD-001
version: 0.3.0
status: Accepted (Option A, owner direction 2026-09-27 — implemented; see §7.1)
classification: Informative
owners:
  - Nyrqis Engineering
created: 2026-09-27
updated: 2026-09-27
ai_assisted: true
review_cycle: As needed
depends_on: [NPS-026, NPS-027, NPS-028, NPS-011, NPC-001]
---

# UPD-001 — Automatic Updates and Rollback

## 1. Status of This Document

This is the design note for the roadmap's M14 Phase 4 "Automatic
updates with rollback" item, recorded here because the honest scoping
question comes **before** any code (the DBG-001/CRY-001
design-note-first discipline). **The item's name promises two things —
"automatic" and "rollback" — and the audit in §2 shows the tree already
ships most of the rollback half while the automatic half is almost
entirely a POLICY decision, not a mechanism one.** This document is
**informative** — it proposes, it does not decide. Which option is
taken (§4) and the answers to §5's questions belong to the
Architecture Group. The note has NOT yet been through the re-probe
wave CRY-001 went through — the v0.2.1 corrigendum (§2.1) IS that
re-probe, run the same day the note was written: the first pass's
truncated search output had understated the wired surface
(deployment/snapshot rollback IS wired; the delta GENERATION half is
wired via `nyrqisctl_repo`), and the corrected finding narrows the gap
to the signed-package VERIFY/APPLY path plus the policy questions. The
remaining claims carry the same re-probe obligation as any other.

## 2. What already exists (the audit, 2026-09-27)

The item name suggests a greenfield update system. In fact much of the
machinery is shipped and tested — and part of the rollback story
(container/deployment scope) is already wired end-to-end; what is NOT
wired is the **signed-package verify/apply path** (§2.1):

| Layer | Surface | Already answers |
|---|---|---|
| Package format + integrity | NPS-026 (.nypkg, integrity trees), `backend/installer.py` (PackageInstaller.install) | What an update installs and how its content is verified |
| Update signatures | `backend/update_signing.py` — UpdateVerifier for FULL/DELTA/PATCH updates; `validate_rollback()` enforces (1) target strictly older, (2) trusted-key signature | Is an update/rollback authentic and permitted |
| Delta updates | `backend/delta_update.py` — create_delta_update / apply_delta_update, signature payload identical to the shipped verifier's canonical form | How an update ships small; fail-closed without PyNaCl |
| Trust model | NPS-027 (Accepted), NPS-028 (Package PKI Implementation Surface, Accepted), `backend/package_pki.py` | Who is allowed to sign |
| Signed repository index | `backend/package_repo.load_index` — fail-closed against the trust store | Where updates are announced |
| Update *status* | `ui/package_manager.py` PackageManager: load_index drives UPDATABLE status; update_package() requires a VERIFIED delta (find_delta + verify_entry_content) before reporting success — the 2026-09-23 real-store wiring | What the user sees and what "update" means today |
| Delivery (fetch) | `registry_pull` — operator-configured `registry_url`, wired end-to-end (IPC op `ipc/control.py:265`, CLI `nyrqisctl.py:314`) | How content reaches the machine |
| System state snapshots | `sdk/nyrqis_sdk/restore.py` RestoreManager — create/restore/list/cleanup restore points (references NPC-010/NPS-026) | The rollback safety-net primitive |
| Audit chain | ADR-0018 hash-chained audit log | Tamper-evident record of update events |
| Rollback (wired, other scope) | `rollback_to_snapshot` (dry-run default) + deployment version rollback; five rollback IPC ops (`rollback_snapshot`/`rollback_deployment`/`get_rollback_candidates`/`rollback_bluegreen`/`rollback_canary`) + five `nyrqisctl rollback-*` verbs; `ui/system_restore.py`, `ui/update_manager.py` (UI model) | Rollback IS wired for containers/deployments — the gap is the signed-PACKAGE update path, not the rollback CONCEPT |
| Capability gate | NPS-011 registry (operator-only authorization posture) | Which surfaces may trigger installs |

### 2.1 The surface audit — the load-bearing finding

**v0.2.1 corrigendum:** the v0.2.0 audit below was recorded from
TRUNCATED search output (`head`-capped result lists) and an
under-scoped importer sweep — the 0.29.35 lesson (re-probe hardest the
claim that cannot fail) applied to this document the same day it was
written. The untruncated, whole-repo re-run caught a real surface the
first pass missed: **deployment/snapshot-scoped rollback is ALREADY
WIRED end-to-end.** The corrected reading keeps the load-bearing
finding intact but states it narrowly (see the revised table and the
narrowed gap list below); the revised §7 plan is unaffected — its
compose-first scope (packages) does not overlap the wired
deployment-rollback surface.

**Verified 2026-09-27 against the current tree (untruncated re-run;
searches recorded so a future re-probe can re-run them):**

- Whole-repo non-test `rollback` hits (counted per file):
  `backend/container.py` (102 — `rollback_to_snapshot` at :18516,
  DRY-RUN-DEFAULT, plus deployment version rollback at :20636),
  `nyrqisctl.py` (61 — five `rollback-*` CLI verbs: snapshot,
  deployment, candidates, bluegreen, canary), `ipc/control.py` (49 —
  five dispatch arms: `rollback_snapshot`, `rollback_deployment`,
  `get_rollback_candidates`, `rollback_bluegreen`,
  `rollback_canary`), `ui/terminal.py` (14), `ui/system_restore.py`
  (8), `ui/update_manager.py` (3), `backend/update_signing.py` (16),
  `backend/delta_update.py` (2).
- Importers of the signed-update machinery, non-test:
  `delta_update`'s GENERATION half is wired — `nyrqisctl_repo.py:96`
  imports `create_delta_update` (the `publish-delta` front-end for
  the signed repo). The VERIFY/APPLY half is not:
  `UpdateVerifier` / `validate_rollback` / `apply_delta_update`
  consumers remain their own modules and tests
  (`test_update_signing.py`, `test_delta_update.py`, plus
  `test_package_repo.py` exercising `apply_delta_update`) — **no IPC
  op, no CLI command, and no daemon path consumes the signed-update
  VERIFY/APPLY machinery**.
- `PackageManager.update_package()` (the 2026-09-23 store wiring)
  verifies the delta entry but does NOT apply it through
  `apply_delta_update` — it reports success and flips status after
  verification. There is no payload mutation to apply in that UI
  model; the real apply path exists only at the delta library layer.
- No self-update / platform-update code exists (whole-repo
  non-test sweep for `self_update`/`self-update`: zero hits).
- Adjacent shipped surfaces (characterized, not load-bearing for the
  gap): `ui/system_restore.py` (snapshot/restore/backup-scheduling
  model), `ui/update_manager.py` (an update-management UI MODEL —
  check/history/rollback_update in-memory), and the
  deployment-rollback family above.

**The corrected load-bearing finding, stated narrowly:** the trust
machinery (shipped, Accepted) and the package-apply mechanics
(shipped, tested) are NOT the gap, and container/deployment rollback
is wired — the gap is that the SIGNED-PACKAGE update path
(verify→apply over `UpdateVerifier`/`apply_delta_update`) and the
update-scoped rollback gate (`validate_rollback`) are
**library-complete and user-unreachable**, and every POLICY question
(automaticity, trigger, scope) is unanswered. Concretely:

1. **A delivery-to-apply path**: fetch (registry_pull exists) →
   verify (exists) → apply (exists) → record (audit chain exists) is
   four shipped pieces with no connecting command or op.
2. **The "automatic" half**: a check cadence, a consent posture
   (NPS-029), and an apply window are policy decisions with no
   mechanism yet — and no consensus that unattended apply is wanted at
   all.
3. **The rollback trigger definition**: `validate_rollback` says
   whether a rollback is PERMITTED; nothing defines when one is
   WARRANTED (what counts as a failed update).
4. **Scope question**: the platform ships as a live ISO (immutable by
   respin, `-net none`); "automatic updates" may be package-scoped by
   construction, with platform updates = re-imaging. That is a
   product-shape decision, not an implementation detail.

## 3. What "automatic updates with rollback" needs that does not exist

1. **An update orchestration surface** — one op/CLI verb that owns the
   fetch→verify→apply→audit sequence over the shipped primitives
   (mirroring the client-side-composition discipline of `debug
   bundle`: no new daemon trust decisions, compose what is
   authorized).
2. **A pre-update restore point** wired into that sequence (the SDK
   RestoreManager pattern, promoted from SDK convenience to the
   update path's own fail-safe).
3. **Consent + cadence state** — an explicit, inspectable,
   revocable policy record (off by default is the conservative
   choice; NPS-029 separation applies to any per-identity consent).
4. **A health/rollback contract** — the criteria under which an
   applied update is declared failed and a rollback fires, and WHO is
   allowed to declare it (automated gate vs operator-only).
5. **(Only if platform-scope is chosen) an image update story** —
   distinct from package updates; interacts with the live-ISO build
   and boot-smoke pipelines.

## 4. Options

### Option A — Compose-first, operator-invoked (recommended)

Wire the shipped primitives into an explicit, audited update surface
with **no new trust surface and no automaticity**:
`nyrqisctl packages update` resolves UPDATABLE entries from the
signed index (the existing verified-delta requirement), fetches via
the operator-configured registry (the `registry_pull` posture —
operator-destination-configured, consistent with CRY-001's corrected
egress finding), verifies with the shipped verifier, takes a restore
point, applies via `apply_delta_update`, audit-chains the event, and
rolls back ONLY on explicit operator command (`validate_rollback`
gates it, as today).

- Pros: closes the wire-in gap with zero new crypto, zero new trust
  decisions, zero egress policy change; every primitive is already
  tested (the two test files exist); honest to the
  operator-owned-machine posture.
- Cons: the item's "automatic" half is explicitly deferred, not
  solved; fleet-scale users still have no update story.

### Option B — Option A plus opt-in automaticity

Option A plus: an opt-in policy record (check cadence, apply window,
consent per NPS-029), a scheduled checker, health-gated auto-rollback
per §5 Q4's contract, and the NPS-019/NPS-020 pass for the
scheduled-checker surface (a daemon-initiated fetch is a new surface
class even to an operator-configured destination — the same class
boundary CRY-001 drew for telemetry).

- Pros: actually delivers the roadmap item's name.
- Cons: new surface class requiring the threat-model pass;
  consent/cadence/health contracts are pure policy with real failure
  modes (a bad auto-rollback is worse than a bad update if the health
  contract is wrong); boot-time apply interacts with container
  liveness (§5 Q4).

### Option C — Observational close

Record the §2.1 finding (the machinery exists, unwired) and close the
item as scoped-out until a user-visible demand arrives.

- Pros: zero work, honest record.
- Cons: leaves a tested-but-unreachable security surface (the
  signed-update path) that the trust model (NPS-027/028, both
  Accepted) visibly promises.

## 5. Open questions for the Group

1. **Option choice** — A (compose-first), B (A + automaticity), or C
   (close). The brief's lean: A, with B's policy questions answered
   first if "automatic" is non-negotiable.
2. **Scope** — packages only, or is a platform/OS update channel in
   scope for this milestone at all (the live ISO is
   immutable-by-respin; re-imaging may BE the platform update story)?
3. **Automaticity posture** — if B: opt-in vs opt-out, check-only vs
   check-and-apply, and the consent record's shape under NPS-029.
4. **The rollback trigger** — what defines "update failed" (apply
   error? failed health op? boot failure?) and whether any automated
   gate may roll back without an operator (the open mechanism
   question — the health contract is the load-bearing safety
   property, analogous to CRY-001's spool default).
5. **Restore-point retention** — the SDK RestoreManager already
   cleans up old points; what retention policy is normative for
   update-path restore points, and where do they live (state dir
   alongside the §4.5 crash-recovery file? per-volume?).

## 6. Downstream sizing (per option)

- **Option A**: one orchestration module + CLI verb composing the
  shipped pieces; contract pins for the ordering (verify BEFORE
  restore point BEFORE apply; no apply on unverified delta — the
  update_package posture already pins the negative case) and a
  fail-closed pin (missing PyNaCl refuses to apply, per the
  delta_signing posture). No new NPS-011 capability (operator-only
  CLI authorization, as `debug bundle`).
- **Option B**: everything in A, plus the policy record, the
  checker, the health contract — each its own review package; the
  NPS-019/NPS-020 pass is a precondition, not a rider.
- **Option C**: records only.

## 7. Pre-staged implementation plan (if the Group accepts Option A) — 2026-09-27

Recorded so acceptance converts to landed work without a re-planning
session (the CRY-001 §7 precedent, which followed D7's DBG-001 §4).
Ground rule throughout: the debug-bundle discipline — client-side
composition of already-authorized, already-tested primitives; no new
daemon trust decisions; audit-chained; fail-closed; contract-pinned.

1. **`backend/update_orchestrate.py`** (new module):
   `apply_package_update()` owns the fetch→verify→restore→apply→audit
   sequence over the shipped pieces — the verified signed index
   (`package_repo.load_index`, fail-closed), the shipped verifier
   (`update_signing.UpdateVerifier`), the apply primitive
   (`delta_update.apply_delta_update`), a pre-apply restore point (the
   `sdk/nyrqis_sdk/restore.RestoreManager` pattern, promoted from SDK
   convenience to the update path's own fail-safe), and the audit chain
   (`create_audit_chain`/`append_audit_entry`) with the package id,
   version transition, and delta checksum in the entry result.
2. **Ordering pins (non-negotiable):** verify BEFORE restore point
   BEFORE apply; no apply on an unverified delta (the
   `PackageManager.update_package` posture already pins the negative
   case — a tampered payload yields FAILED, never a simulated
   success); every rollback passes `validate_rollback` (target
   strictly older + trusted key, as shipped); missing PyNaCl refuses
   to sign/verify/apply (the delta_signing fail-closed posture).
3. **Rollback surface:** operator-invoked ONLY in Option A —
   `nyrqisctl packages rollback <name> [version]`, gated by
   `validate_rollback`, confirmation required. The automated
   health-gated rollback is Option B's contract (§5 Q4) and does not
   exist in A.
4. **CLI:** `nyrqisctl packages update <name|--all>` (resolve
   UPDATABLE from the signed index → verify → restore point → apply →
   audit), `packages rollback` (per above), and the existing
   status/list display carrying the updated versions.
5. **The one implementation choice this plan flags:** CLI-side
   composition (DEFAULT — the spool/read-access analogy: local file
   operations on operator-owned state, nothing new to authorize) vs a
   minimal IPC op (needed only if applies must be daemon-coordinated
   with container liveness). A's default keeps zero new daemon
   surface.
6. **Scope default:** packages only (§5 Q2) — any platform/OS update
   channel is explicitly out of scope for this item unless the Group
   says otherwise.
7. **Contract pins (the test plan):** verification strictly precedes
   apply; `validate_rollback` refuses a same-or-newer target and an
   untrusted key; fail-closed without PyNaCl; the restore point exists
   before any payload mutation; the audit chain carries the delta
   checksum; and — the CRY-001 §7.7 analog — **a no-direct-egress
   assertion**: the orchestration module imports no HTTP client, so
   "fetch stays behind the operator-configured registry client" stays
   a tested property, not a prose claim.
8. **On landing:** UPD-001 as-built revision; roadmap strike;
   CHANGELOG + pyproject at the release point.

## 7.1. As-built (v0.3.0, 2026-09-27 — Option A accepted and landed)

Option A was accepted by owner direction on 2026-09-27 (decision
input: the repo operator via the recorded session; the row is on
AG_AGENDA v2.3.9's decision log) and the §7 plan executed the same
day. What the tree carries:

- **`backend/update_orchestrate.py`** — `UpdateOrchestrator`:
  `resolve_updates()` walks the FULLY-VERIFIED signed index
  (`package_repo.load_index`, fail-closed against the trust store); a
  delta is a candidate only when its content checksum re-verifies and
  its target is strictly newer. `apply_update()` owns the ordering
  pins: resolve → payload from the LOCAL repository path (no network
  I/O here — fetch stays behind the operator-configured registry
  client) → `apply_delta_update` verifies the signature against the
  trust store BEFORE any filesystem mutation → the pre-apply restore
  point (a failed snapshot REFUSES the apply; `--no-restore-point` is
  the operator's explicit override) → apply → the installer-layout
  manifest rewrite → JSONL history + the audit chain (package id,
  version transition, delta checksum in the entry result).
  `rollback()` is OPERATOR-INVOKED ONLY: restores the newest pre-apply
  restore point; a same-or-newer target is refused per the
  `validate_rollback` posture; NO automated health gate exists — §5
  Q4 is resolved by construction in Option A. `verify_only()` is the
  no-mutation index check.
- **The operator surface** — `nyrqisctl packages verify|update|rollback|
  status` (client-side composition, zero new daemon surface; the
  packages-only scope default holds — no platform-update channel).
- **Contract pins** — `tests/test_update_orchestrate.py` (16 tests):
  verify-before-mutation (a tampered delta is refused with the
  install untouched), restore-point-before-apply (a failed snapshot
  refuses), rollback ordering + refusal semantics (same-or-newer,
  no-restore-point), resolution (bad trust store fails closed),
  numeric version ordering, and the NO-DIRECT-EGRESS assertion (the
  module imports no HTTP client and only the shipped primitives).
- **The pins did their job:** `upd001-audit-rollback-pin` and
  `upd001-audit-unwired-pin` failed on the landing tree exactly as
  their fail-on-change descriptions prescribe ("then update UPD-001
  in the same commit") — nyrqisctl.py's rollback count 71 → 79 (the
  new packages-rollback verb) and `update_orchestrate.py` recorded as
  the first non-test consumer of `apply_delta_update`. The §2.1 gap
  this note recorded (library-complete, user-unreachable) is CLOSED:
  the signed-package verify/apply path is user-reachable as of this
  landing.

## 8. Revision history

| Version | Date | Change |
|---|---|---|
| 0.3.0 | 2026-09-27 | Option A ACCEPTED by owner direction and LANDED — §7.1 records the as-built (`backend/update_orchestrate.py` with the ordering pins + `nyrqisctl packages verify/update/rollback/status`, 16 contract pins incl. the no-direct-egress assertion); Q4 resolved by construction (rollback operator-judgment-only, no automated gate); Q5 as-built: restore points retained under `state_dir/restore-points/` (no automatic cleanup yet); the two upd001 audit pins updated per their fail-on-change protocol; status Draft → Accepted; AG_AGENDA v2.3.9 decision-log row |
| 0.2.1 | 2026-09-27 | Corrigendum: the v0.2.0 surface audit was recorded from truncated search output — the untruncated whole-repo re-run caught the wired deployment/snapshot rollback family (`rollback_to_snapshot` dry-run-default, five rollback IPC ops + CLI verbs) and the wired delta GENERATION half (`nyrqisctl_repo publish-delta`); corrected load-bearing finding: the gap is the signed-package VERIFY/APPLY path (`UpdateVerifier`/`validate_rollback`/`apply_delta_update` — library-complete, user-unreachable) plus the policy questions; options, recommendation, open questions, and the §7 plan unchanged in scope |
| 0.2.0 | 2026-09-27 | §7 added: the Option A implementation plan pre-staged (orchestration module composing the shipped primitives, ordering pins, operator-only rollback, CLI, the CLI-side-composition default, scope default, contract pins incl. the no-direct-egress assertion) — acceptance converts to landed work without re-planning; content unchanged otherwise |
| 0.1.0 | 2026-09-27 | Initial draft per the DBG-001/CRY-001 design-note-first discipline: surface audit (the signed-update machinery is shipped, tested, and unwired — §2.1), three options, five open questions, downstream sizing. Roadmap item stays `[ ]` — the draft proposes, the Group decides |
