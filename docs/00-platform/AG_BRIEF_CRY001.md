---
title: AG Brief — Crash Reporting and Telemetry (CRY-001, M14 Phase 4)
document_id: AG-BRIEF-CRY001
version: 1.0.0
status: Proposed
classification: Internal
owners:
  - Nyrqis Engineering
created: 2026-09-27
updated: 2026-09-27
ai_assisted: true
review_cycle: One sitting
depends_on: [CRY-001, NPS-019, NPS-020, NPS-029, ADR-0018, DBG-001, NPC-001]
---

# AG Brief — Crash Reporting and Telemetry (CRY-001)

## 1. Status of This Document

This is the decision package for CRY-001 — the M14 Phase 4 "crash
reporting and telemetry (opt-in)" item, staged as AG_AGENDA v2.3.0
Bundle E. It follows the D3/D4/duality/DBG-PhaseB brief format:
verified evidence, options with consequences ledgers, one
recommendation, overridable. The brief proposes; the Group decides.
Nothing in this item has been implemented — that is the point of this
sitting.

One correction is part of the evidence base: CRY-001 v0.1.0's surface
audit opened with a FALSE null finding ("ZERO outbound HTTP clients in
non-test backend code"), caught 2026-09-27 by re-running the audit and
corrected in CRY-001 v0.1.1 §2.1 (the 0.29.35 lesson applied to a
document, not a test). The corrected audit is what this brief stages.

## 2. The Verified Evidence (audited 2026-09-27)

**The corrected egress posture.** The platform is NOT egress-free —
that was the false v0.1.0 claim. `backend/container.py` carries four
outbound HTTP client sites, all predating the draft, and a 2026-09-27
whole-repo sweep confirms it is the only non-test file carrying egress
client code:

| Site | Introduced | Destination | Posture |
|---|---|---|---|
| `_send_webhook` — resource-usage webhooks, HMAC-signed POST | 2026-08-28 (`5585532`) | operator-configured URL | fires only after the operator registers a webhook |
| `registry_pull` / `registry_push` / `registry_catalog` — HTTP registry client | 2026-08-30 (`56de456`) | operator-configured `registry_url` | `registry_pull` is wired to IPC + `nyrqisctl` |
| health-check `http` type | — | `127.0.0.1:<port>` | loopback only — not egress |

**The corrected finding is narrower but still real:** the platform has
NO implicit or telemetry egress — no crash reporter, no metrics
pipeline, no phone-home. Every existing site is
operator-destination-configured or loopback. The live ISO boots
`-net none` in every smoke. NPS-019's enumeration does not cover a
daemon-side telemetry client (`SURFACE-NET-0001` is container egress),
so Option B below would still be a new surface CLASS:
incident-driven, payload-carrying, not purely operator-initiated.

**The existing substrate (what CRY-001 §2 builds on — verified):**

- Crash forensics: Plan §4.5 crash-recovery reporting — `_recover()`
  reports a previous daemon's pid, version, socket, and last-known
  container manifest, never auto-resumes.
- Local telemetry: `health`/`status` ops (dedicated health socket,
  ADR-0021) — uptime, serve-loop liveness, container counts, IPC
  registry size, vault aggregates.
- Log egress (local): `serve --syslog` mirrors daemon records to the
  system journal via `/dev/log`, best effort.
- Redaction discipline: DBG-001 Phase A — the incident bundle is
  redaction-default-on (vault aggregates stripped client-side,
  opt-out recorded per reply).
- Audit chain: ADR-0018's hash chain — every report-generation event
  can be chained exactly as the debug ops already are.
- Data separation: NPS-029 (Draft) defines the boundary telemetry
  payloads must respect.

**What does not exist:** a crash-report spool, a payload schema
beyond the §4.5 record, an inspectable consent state, any collector.

## 3. The Regulatory Frame (what any option must satisfy)

Per NPS-029, any payload carrying user-identifying material inherits
the redaction-default-on discipline and per-identity separation — the
bundle's `_redact_vault` pass is the proven mechanism. Per ADR-0018,
report-generation events are audit-chained (the
`create_audit_chain`/`append_audit_entry` pattern the debug op family
already uses, with the class/posture in the entry result). Option B
additionally triggers the NPS-019/NPS-020 pass: a daemon-side
telemetry client is a new entry in the attack-surface enumeration and
a STRIDE pass over the egress path — the same treatment every prior
new surface got. Option A requires NO new capability in NPS-011: the
`nyrqisctl crash` surface rides the existing operator-only CLI
authorization, exactly as `debug bundle` does.

## 4. Options and Consequences

### Option A — Local-only crash reporting (the draft's recommendation)

Crash reports spool to a local directory: the §4.5 recovery record,
the fault-handler trace, and a container-manifest snapshot — redaction
applied, generation events audit-chained — surfaced through
`nyrqisctl crash list/show/purge`, modeled on the debug bundle's
client-side composition. Nothing leaves the machine.

Consequences:

- No new threat surface: no egress code, no collector, no PII
  transfer; the "no phoning home" property users can take for granted
  is untouched (and now honestly documented — see §2).
- Fully answers "crash reporting" for an operator or user who owns
  the machine: post-crash forensics survive restart in inspectable,
  purgable form.
- Reuses proven machinery: state-file persistence, the redaction
  pass, the audit chain — the implementation is small and
  contract-pinnable (the DBG-001 Phase A shape).
- Cost: no fleet-wide visibility; the telemetry half of the item name
  is explicitly deferred, not solved.

### Option B — Local collection + explicit, audited, opt-in egress

Option A plus an operator/user-configured destination (self-hosted
collector; none shipped by this project), off by default, every
transmission audit-chained with the payload hash, redaction applied
before the wire, the payload schema reviewed under NPS-029.

Consequences:

- Real fleet telemetry for deployments that run collectors; the
  opt-in is meaningful and inspectable.
- BUT the project ships a telemetry EGRESS PATH even with no endpoint
  configured — the first incident-driven, payload-carrying egress,
  distinct in kind from the operator-configured webhook/registry
  clients that already exist. The NPS-019/NPS-020 pass, an AG-reviewed
  payload schema, and endpoint governance all gate any line landing.
- The honest sequencing: B's transmission half is a separate future
  decision package regardless of whether A lands first.

### Option C — Observational close (no code)

Record the audit: §4.5 + `--syslog` + health/status already answer
crash reporting for operators; close the item as covered.

Consequences:

- Zero surface, zero cost — an honest close, like the M11
  performance-budget precedent.
- No spooled forensics: a crash leaves only the state-file summary
  and whatever the journal kept; the telemetry half is left to a
  future note anyway.

## 5. Recommendation

**Option A**, clearly overridable. It is the only option that adds
real post-crash forensics without introducing a new surface class,
it reuses machinery the codebase already trusts (state-file
persistence, redaction-default-on, the ADR-0018 chain), and its cost
is a small, contract-pinnable implementation — not a new class of
risk. Option C is an honest close if the Group judges the spool not
yet worth its surface; Option B's transmission half is a separate
future package in every scenario.

**The one open mechanism question this brief flags:** the spool
DEFAULT (ON vs OFF) if A is accepted. ON is defensible — local-only,
inspectable, redaction applied, generation events audited; OFF is the
conservative choice (a machine owner may not want crash manifests
accumulating on disk at all). The brief leans ON for symmetry with
redaction-default-on (both are "collect locally, surface and purge
on demand"), but this is exactly the kind of judgment the Group
owns — both defaults are recorded as defensible in CRY-001 §6.2.

Downstream if A is accepted: the roadmap item strikes (M14 Phase 4's
first entry closed); `nyrqisctl crash list/show/purge` + the spool
land pinned by contract tests (the Phase A shape: fail-closed,
redaction-default-on, audit-chained); CRY-001 moves to an as-built
revision; NPS-029 gains the payload note. If B: the NPS-019/NPS-020
pass lands FIRST, then the schema review, then the transmission
package. If C: the observational-close wording lands and the item
closes with the decision recorded.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-09-27 | Initial brief — the corrected surface audit (v0.1.1 corrigendum included), regulatory frame, three options with ledgers, recommends Option A with the spool default flagged as the open mechanism question |
