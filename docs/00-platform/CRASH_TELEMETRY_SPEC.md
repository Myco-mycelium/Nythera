---
title: Opt-in Crash Reporting and Telemetry — design note for the M14 Phase 4 item
document_id: CRY-001
version: 0.1.0
status: Draft
classification: Informative
owners:
  - Nyrqis Engineering
created: 2026-09-26
updated: 2026-09-26
ai_assisted: true
review_cycle: As needed
depends_on: [NPS-029, NPS-019, ADR-0018, DBG-001, NPC-001]
---

# CRY-001 — Opt-in Crash Reporting and Telemetry

## 1. Status of This Document

This is the design note for the roadmap's M14 Phase 4 "Crash reporting
and telemetry (opt-in)" item, recorded here because the honest scoping
question comes **before** any code: the platform today has *no* outbound
network path at all, so the item's default answer would quietly introduce
the first egress surface the threat model has ever had to reason about.
This document is **informative** — it proposes, it does not decide. The
transmission question (and therefore which option is taken) belongs to
the Architecture Group.

## 2. What already exists (the audit, 2026-09-26)

The item name suggests a greenfield telemetry stack. In fact the
platform already ships most of the *hard* parts, locally:

| Layer | Surface | Already answers |
|---|---|---|
| Crash forensics | Plan §4.5 crash-recovery reporting: `_recover()` reports a previous daemon's pid, version, socket, and last-known container manifest — never auto-resumes | What crashed, when, with what state |
| Local telemetry | `health`/`status` ops (dedicated health socket, ADR-0021): uptime, serve-loop liveness, container counts, IPC registry size, vault aggregates | What the metrics would be |
| Log egress (local) | `serve --syslog` mirrors daemon records to the system journal via `/dev/log` (best effort) | Where logs already go without any new egress |
| Redaction discipline | DBG-001 Phase A: the incident bundle is **redaction-default-on** (vault aggregates stripped client-side, opt-out flag recorded per reply) | The privacy posture to inherit |
| Audit chain | ADR-0018 hash-chained audit log; DBG-001's debug sessions are audit-chained | Tamper-evident record of *what was reported* |
| Data separation | NPS-029 (Identity and User Data Separation, Draft) | The boundary telemetry payloads must respect |

**The null finding that shapes everything:** a repo-wide search for
outbound HTTP clients (`urllib.request`, `requests`, `http.client`,
`httpx`) across non-test backend code returns **zero** results. Nyrqis
today makes no outbound network connections by design — the live ISO
even boots with `-net none` in every smoke. Any option that transmits
would be the platform's first egress path, a new class of surface for
NPS-019/NPS-020, and a change to the "no phoning home" property users
can currently take for granted.

## 3. What "crash reporting" needs that does not exist

1. **A payload schema** — what a crash report contains, beyond the §4.5
   recovery record (stack traces from Python fault handlers, container
   manifest snapshot, backend version, OS/arch).
2. **A collection point** — local spool (a directory), vs a remote
   endpoint. Locally, the crash-recovery state file already demonstrates
   the persist-and-report pattern.
3. **Consent state** — an explicit opt-in that survives reboots, is
   inspectable, and is revocable (NPS-029 separation: per-identity?).
4. **(Only if transmitting) an egress path + destination** — the entire
   NPS-019/NPS-020 analysis for a new outbound surface, endpoint
   governance, payload PII review, and a "who operates the collector"
   answer that does not exist today.

## 4. Options

### Option A — Local-only crash reporting (recommended)

Everything the item needs, with **no egress**: crash reports spool to a
local directory (the §4.5 recovery record + fault-handler trace +
redaction applied), surfaced through a `nyrqisctl crash list/show/purge`
surface modeled on the debug bundle's client-side composition and
redaction-default-on discipline. Consent question dissolves for
transmission (nothing leaves the machine; the "opt-in" that remains is
whether spooling is on at all — default ON is defensible because it is
local-only and inspectable, but default-OFF is the conservative choice
recorded here for the Group). Reuses: state-file persistence, the bundle
redaction pass, the audit chain (report-generation events are audited).

- Pros: no new threat surface, no collector to operate, no PII
  transfer, honest to the platform's no-egress property; still fully
  answers "crash reporting" for an operator/user who owns the machine.
- Cons: no fleet-wide visibility; the telemetry half of the item name
  is explicitly deferred, not solved.

### Option B — Local collection + explicit, audited, opt-in egress

Option A plus: an operator/user-configured destination (self-hosted
collector; none is shipped by this project), off by default,
`--telemetry-endpoint` explicitly provided, every transmission
audit-chained with the payload hash, redaction applied before the wire,
and a documented payload schema reviewed under NPS-029.

- Pros: real fleet telemetry for those who deploy collectors; the
  opt-in is meaningful and inspectable.
- Cons: the project ships egress code even if no endpoint; first
  outbound surface in platform history; needs the NPS-019/NPS-020 pass
  and an AG decision on payload schema before a line lands.

### Option C — Observational close (no code)

Record the audit: §4.5 + syslog + health already answer crash
reporting for operators; close the item as covered.

- Pros: zero surface, zero cost.
- Cons: no spooled forensics (a crash leaves only the state-file
  summary and whatever the journal kept); the item's telemetry half is
  left to a future note anyway.

## 5. Recommendation

**Option A** for this item, with Option B's transmission half split
into its own future decision package (it is a separate AG question:
egress policy, endpoint governance, NPS-029 payload review). If the
Group accepts A, the spool default (ON vs OFF) is the one open
mechanism question this note carries forward.

## 6. Open questions for the Group

1. Accept Option A, or prefer B (accepting the egress-surface cost)?
2. Spool default: ON (inspectable, local-only) or OFF (conservative)?
3. If A: which schema fields are mandatory in a spooled report (the §4.5
   record is the floor; fault-handler traces and the container manifest
   are the candidates)?
4. Retention/purge: a cap (count or bytes) on the spool directory, and
   does purge need to be audit-chained?

## 7. Revision History

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-09-26 | First draft: surface audit (zero egress clients; §4.5/syslog/health/redaction as the existing substrate), three options, recommendation A, four open questions |
