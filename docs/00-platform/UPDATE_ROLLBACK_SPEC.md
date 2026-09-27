---
title: Automatic Updates and Rollback — design note for the M14 Phase 4 item
document_id: UPD-001
version: 0.1.0
status: Draft
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
wave CRY-001 went through; its claims carry the same re-probe
obligation as any other (the 0.29.35 lesson applies to this document
first of all).

## 2. What already exists (the audit, 2026-09-27)

The item name suggests a greenfield update system. In fact the trust
and integrity machinery for updates is shipped, tested, and — the
surprising part — **not yet wired to any user-facing surface**:

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
| Capability gate | NPS-011 registry (operator-only authorization posture) | Which surfaces may trigger installs |

### 2.1 The surface audit — the load-bearing finding

**Verified 2026-09-27 against the current tree (searches recorded so a
future re-probe can re-run them):**

- `grep -rn 'rollback' --include='*.py' backend/` returns hits ONLY in
  `update_signing.py` (`validate_rollback`) and `delta_update.py`
  (docstring references to NPS-026 §6). Nothing else in backend code
  performs or schedules a rollback.
- The only importers of `update_signing` / `delta_update` anywhere in
  the tree are their own tests (`tests/test_update_signing.py`,
  `tests/test_delta_update.py`). **No IPC op, no CLI command, and no
  daemon path consumes the signed-update machinery** — the
  verify/apply half of the update story is library-complete and
  user-unreachable.
- `PackageManager.update_package()` (the 2026-09-23 store wiring)
  verifies the delta entry but does NOT apply it through
  `apply_delta_update` — it reports success and flips status after
  verification. There is no payload mutation to apply in that UI
  model; the real apply path exists only at the delta library layer.
- No self-update / platform-update code exists:
  `grep -rln 'self.update|self_update|self-update|platform update'
  backend/ nyrqisctl.py` returns nothing.

**The honest gap is therefore NOT cryptography or trust (shipped,
Accepted) and NOT application mechanics (shipped, tested) — it is
WIRING and POLICY:**

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

## 7. Revision history

| Version | Date | Changes |
|---|---|---|
| 0.1.0 | 2026-09-27 | Initial draft per the DBG-001/CRY-001 design-note-first discipline: surface audit (the signed-update machinery is shipped, tested, and unwired — §2.1), three options, five open questions, downstream sizing. Roadmap item stays `[ ]` — the draft proposes, the Group decides |
