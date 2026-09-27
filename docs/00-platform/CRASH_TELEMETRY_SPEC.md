---
title: Opt-in Crash Reporting and Telemetry — design note for the M14 Phase 4 item
document_id: CRY-001
version: 0.4.0
status: Accepted (Option A, owner direction 2026-09-27 — implemented; see §7.1)
classification: Informative
owners:
  - Nyrqis Engineering
created: 2026-09-26
updated: 2026-09-27
ai_assisted: true
review_cycle: As needed
depends_on: [NPS-029, NPS-019, ADR-0018, DBG-001, NPC-001]
---

# CRY-001 — Opt-in Crash Reporting and Telemetry

## 1. Status of This Document

This is the design note for the roadmap's M14 Phase 4 "Crash reporting
and telemetry (opt-in)" item, recorded here because the honest scoping
question comes **before** any code. **v0.1.1 correction:** v0.1.0 opened
with a false null finding — "the platform has no outbound network path
at all" — caught the next session by re-running the audit (the 0.29.35
lesson: re-probe hardest the claim that cannot fail). The corrected
finding is the narrow one: the platform has no IMPLICIT or TELEMETRY
egress — no crash reporter, no metrics pipeline, no phone-home — but
operator-configured outbound HTTP client code has existed since
2026-08-28 and is enumerated in §2.1. What the item's default answer
would introduce is the first TELEMETRY-class egress: incident-driven,
payload-carrying, not purely operator-initiated. This document is
**informative** — it proposes, it does not decide. The transmission
question (and therefore which option is taken) belongs to the
Architecture Group.

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

### 2.1. The surface audit (corrected v0.1.1)

**The v0.1.0 null finding was FALSE.** It claimed a repo-wide search for
outbound HTTP clients (`urllib.request`, `requests`, `http.client`,
`httpx`) across non-test backend code returns zero results. Re-running
that same search the next session returns hits in
`backend/container.py`, and a 2026-09-27 whole-repo sweep confirmed
`backend/container.py` as the only non-test file carrying egress client
code. **v0.3.0 corrigendum: that "only file" claim was FALSE as
stated** — the 0.29.35 lesson (re-probe hardest the claim that cannot
fail) applied to it the same day, and the untruncated re-run found a
second non-test site: `tools/compare_benchmarks.py` (the CI
benchmark-artifact downloader) carries outbound HTTP client code to a
FIXED destination (`api.github.com`), operator-authenticated,
CI-side tooling — not platform runtime, and not incident-driven or
telemetry-class. The narrow finding SURVIVES unchanged: the platform
still has NO implicit/telemetry egress; what the corrigendum fixes is
the enumeration. The corrected, complete site list (pinned re-runnable
by the registry's `cry001-egress-audit-pin`, per-file counts):

| Site | Introduced | Destination | Posture |
|---|---|---|---|
| `_send_webhook` — resource-usage webhooks, HMAC-signed POST | 2026-08-28 (`5585532`) | operator-configured URL | fires only after the operator registers a webhook |
| `registry_pull` / `registry_push` / `registry_catalog` — HTTP registry client | 2026-08-30 (`56de456`) | operator-configured `registry_url` | `registry_pull` is wired to IPC + `nyrqisctl` (`op: registry_pull`) |
| health-check `http` type | — | `127.0.0.1:<port>` | loopback only — not egress |

**The corrected null finding is narrower but still real:** the platform
has NO implicit or telemetry egress — no crash reporter, no metrics
pipeline, no phone-home. The live ISO still boots `-net none` in every
smoke, and every outbound call above requires the OPERATOR to configure
the destination first. Option B (telemetry transmission) would still be
a new surface class for NPS-019/NPS-020 — incident-driven,
payload-carrying, not purely operator-initiated — and NPS-019's
enumeration (`SURFACE-NET-0001`, outbound connections from a
CAP-NETWORK container) does not cover a daemon-side telemetry client.
The "no phoning home" property users can take for granted survives
intact — but the record now carries the whole truth.

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
- Cons: the project ships a telemetry EGRESS PATH even if no endpoint
  is configured — distinct from the operator-configured webhook/registry
  clients that already exist, and the first incident-driven,
  payload-carrying egress; needs the NPS-019/NPS-020 pass and an AG
  decision on payload schema before a line lands.

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

## 7. Pre-staged implementation plan (if the Group accepts Option A) — 2026-09-27

Recorded so acceptance converts to landed work without a re-planning
session (the D7 precedent: DBG-001 §4 carried its work-item list the
same way). Ground rule throughout: the DBG-001 Phase A discipline —
client-side composition, redaction-default-on, audit-chained,
fail-closed, contract-pinned.

1. **`backend/crash_spool.py`** (new module): spool directory under
   the daemon state root, beside the §4.5 state file;
   `spool_report()` composes the §4.5 recovery record + the
   fault-handler trace + a container-manifest snapshot; REDACTION
   APPLIED at write (the bundle's `_redact_vault` discipline); the
   generation event is audit-chained (`create_audit_chain`/
   `append_audit_entry`) with the report id in the entry result;
   fail-closed in the recovery path — a spool failure never breaks
   §4.5 recovery itself.
2. **Retention cap** per the Group's §6.4 answer (count or bytes);
   enforced at write time; purge audit-chained if the Group says so.
3. **Spool reads — the one implementation choice this plan flags:**
   direct operator-CLI file access (the spool is operator-local
   state, like the bundle's `--out` directory; nothing new to
   authorize) vs a minimal read op. DEFAULT: direct file access —
   no new daemon surface.
4. **CLI:** `nyrqisctl crash list` (ids + timestamps),
   `crash show <id>` (redaction-view default), `crash purge <id|--all>`
   (confirmation required).
5. **Spool default** per the Group's §6.2 answer (the brief
   recommends ON).
6. **Schema floor** per the Group's §6.3 answer (the §4.5 record is
   the floor; fault trace + manifest snapshot are the candidates).
7. **Contract pins (the test plan):** fail-closed spool write;
   redaction-default-on in show; audit chain present with the report
   id; purge refusal semantics; cap enforcement; and — new class of
   pin the corrected audit makes possible — **a no-egress assertion**:
   the module imports no HTTP client, so "local-only" stays a tested
   property, not a prose claim.
8. **On landing:** CRY-001 as-built revision; roadmap strike;
   NPS-029 payload note; CHANGELOG + pyproject at the release point.

## 7.1. As-built (v0.4.0, 2026-09-27 — Option A accepted and landed)

Option A was accepted by owner direction on 2026-09-27 (decision
input: the repo operator via the recorded session — the same
acceptance shape every AG_AGENDA decision-log row records; the rows
are on AG_AGENDA v2.3.9) and the §7 plan executed the same day. What
the tree carries:

- **`backend/crash_spool.py`** — `CrashSpool` (write / list / read /
  purge) + `redact_recovery_record` + `spool_from_state_file` (the
  §4.5 integration). Redaction-default-on AT WRITE: vault aggregates
  and per-container `*_bytes` figures never reach the spooled bytes;
  `--no-redact` is a READ-view choice only. Audit-chained generation,
  eviction, and purge (best-effort — a chain failure never breaks the
  spool). Fail-closed: `spool_report` returns the id or `None` and
  never raises, so §4.5 recovery survives a broken spool.
  Write-then-rename keeps partial reports unlisted. Retention: 20
  reports / 32 MiB, oldest-first by mtime. Report ids
  `crash-<utcstamp>-<rand4>`, validated on read (no traversal).
- **The §4.5 integration** — `nyrqis_backend.py` spools via
  `spool_from_state_file` during dead-daemon recovery when
  `--crash-spool <dir>` is configured, and logs the report id. The
  shipped default is NOT to spool (the flag defaults to disabled) —
  the conservative posture for §6.2's open ON/OFF question; the brief
  leaned ON, and flipping the service default is a one-line change if
  a later sitting wants spooling unconditionally.
- **The operator surface** — `nyrqisctl crash list|show|purge`
  (`--spool-dir`, default `/var/lib/nyrqis/crash-spool`; `purge`
  requires `--yes` and takes an id or `--all`, never both). Direct
  file access is the read path — zero new daemon surface, per §7
  item 3's default.
- **Contract pins** — `tests/test_crash_spool.py` (16 tests): the
  fail-closed write, write-time redaction (including a raw-bytes
  assertion), the audit chain with the report id in the entry result,
  purge/eviction chaining, id-validation refusals, retention caps,
  and the NO-EGRESS assertion — the module imports no HTTP client, so
  "local-only" is a tested property, not prose.
- **Beyond the pins:** `demo/run_demo.sh` Act IV drives the real §4.5
  recovery path end-to-end (a live daemon restart spools a redacted
  report the operator then lists and shows).

## 8. Revision History

| Version | Date | Change |
|---|---|---|
| 0.4.0 | 2026-09-27 | Option A ACCEPTED by owner direction and LANDED — §7.1 records the as-built (`backend/crash_spool.py` + the §4.5 `--crash-spool` integration + `nyrqisctl crash list/show/purge`, 16 contract pins incl. the no-egress assertion); spool default as-built: operator-configured `--crash-spool` (shipped default disabled — the conservative §6.2 posture); status Draft → Accepted; AG_AGENDA v2.3.9 decision-log row |
| 0.3.0 | 2026-09-27 | Corrigendum: the "container.py is the only non-test egress file" claim was FALSE as stated — tools/compare_benchmarks.py (CI artifact downloader, fixed api.github.com destination, operator-authenticated, CI-side) is a second site; the narrow finding (no implicit/telemetry egress) survives; enumeration now pinned re-runnable (registry `cry001-egress-audit-pin`: container.py 16 + compare_benchmarks.py 5 pattern matches, scan-for-unrecorded) |
| 0.2.0 | 2026-09-27 | §7 added: the Option A implementation plan pre-staged (module, retention, CLI, spool default, schema floor, contract pins incl. the no-egress assertion) — acceptance converts to landed work without re-planning; content unchanged otherwise |
| 0.1.1 | 2026-09-27 | Corrigendum: the v0.1.0 surface-audit null finding ("ZERO outbound HTTP clients in non-test backend code") was FALSE — `_send_webhook` (2026-08-28) and the registry pull/push/catalog family (2026-08-30) predate this note and return hits on the same search; corrected finding: no implicit/telemetry egress, all four sites operator-destination-configured or loopback (§2.1); options, recommendation, and open questions unchanged — the Group framing now rests on the corrected audit |
| 0.1.0 | 2026-09-26 | First draft: surface audit (recorded as zero egress clients — corrected in 0.1.1), three options, recommendation A, four open questions |
