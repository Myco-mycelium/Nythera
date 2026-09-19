---
title: Architecture Group Decision Brief — Pre-Read for the Pending Agenda
document_id: AG-BRIEF-2026-09
version: 1.0.0
status: Informational
owners: [Nyrqis Architecture]
created: 2026-09-19
ai_assisted: true
depends_on: [AG-AGENDA-2026-09, ADR-0007, ADR-0009, ADR-0013, ADR-0016, ADR-0018, ADR-0022, ADR-0023, NPS-010, NPS-026, NPC-001]
---

# Architecture Group Decision Brief — Pre-Read

**What this document is:** an AI-drafted pre-read for the session that
disposes of `AG_AGENDA.md` (v1.1.0). Every factual claim was
mechanically verified against the tree on 2026-09-19 (code reads, git
history, frontmatter sweeps — see the agenda's pre-flight block).

**What this document is not:** a decision. Per NPC-001 §11.1 the
suggest-vs-act boundary applies: every recommendation below is
overridable Group judgment, and nothing in the tree changes until the
Group's decision log is filled.

---

## Bundle A — Accept the measured pipelines

### A1. ADR-0009 (IPC rate limiting)

| Decision | Options | Brief's recommendation |
|---|---|---|
| Mechanism + §4 defaults | accept / amend | **Accept as shipped.** Data §32a–e complete; `FairTokenBucket` live; NPS-010 §7.1.1 already normative. Amending now would move a shipped mechanism without new data. |
| `fair_shares` static vs dynamic | static default / dynamic | **Static default + dynamic opt-in** — the shipped posture. §32d shows static shares=8 meets the 250/s guarantee; dynamic adds a tuning surface the data does not yet justify. |

### A2. ADR-0007 (zstd default level)

**Recommend: accept, default level 3** (NPS-005 §3's low-single-digits
row). The data is unusually one-sided: ratio is flat ~1.07 at every
level on real assets; level ≥7 buys ≤2% ratio for ~60× compute; LZ4
fast path covers the latency-sensitive class. Record the reopen
condition (a workload with compressible-but-cold data where 2% matters).

### A3. ADR-0013 (EEVDF scheduler)

**Recommend: accept with all three sub-decisions as proposed** —
Linux-6.6 weight table (best tail isolation, share accuracy 1–2% in
§33), RT admission reserve ≤ ~60–70% (100% RT utilization starves the
fair class with zero RT misses — the reserve is not optional), and the
small-request requirement for input/audio classes (NPS-012 §6.1 shape;
≤1.5 ms requests → zero overruns under hogs).

### A4. ADR-0016 (NyFS FUSE backend)

**Recommend: confirm FUSE-first and name the reopen criterion rather
than a workload class today.** §5–§15 measured the live mount
(writeback-cache negotiation ~25× write improvement; commit-cost levers
characterized). The kernel-module fallback stays a documented escape
hatch, reopened only by a named workload demonstrating FUSE overhead
beyond its threshold — none measured so far.

---

## Bundle B — Decide the recorded caveats

### B1. ADR-0018 (hash-chained audit log) — four decisions

1. **Status:** recommend **index → Accepted** (reconcile to the ADR's
   own text, which has said Accepted since it was written; the review
   package and §34 benchmark record exist; nothing in the tree
   implements a half-adopted variant).
2. **Tamper scope:** recommend **direct the fix** (option b — the
   review package's recommendation). The §34e demonstration shows
   `details` (the payload a tamper would most want to edit) is outside
   the hash. The prototyped fix is measured (19.0 µs/event — ~3× base,
   still ~7% of a wire call p50), full suite green, §34e mutation
   table all-detected. Accepting the hole makes the log a speed bump
   against exactly the attack it exists for.
3. **Dual mechanisms:** recommend **consolidate** — re-root the
   `create_audit_chain` family on the fixed hasher (or deprecate its
   API behind the primary path). Two chain families with different
   guarantees in one file is the drift class ADR-0027's lesson warns
   about; compat callers keep working if the family delegates.
4. **Persistence:** recommend **set the requirement**: the chain
   persists across restart (snapshot on append or batched interval,
   restored on manager start). Until shipped, scope the guarantee
   honestly to one manager lifetime in both the ADR and the index.
   Restart is the cheapest tamper; memory-only persistence is the
   finding that makes decisions 2–3 urgent rather than cosmetic.

### B2. NPS-010 §7.2/§9 (container resource-limit defaults)

**Recommend: accept all three** — keep `memory_mb=256` /
`pid_limit=64` / `cpu_quota=None` (§35: 28–80× footprint headroom; the
quota throttle tail argues for leaving quota unlimited), adopt the
standing rules (explicit PID raises for supervisor shapes; quotas ≥
~2.5× average demand, monitored via p95 + `nr_throttled`), and make
the SUSPENDED-accounting rule normative at the next §7 amendment
(full memory, zero CPU — suspension is not budget relief).

### B3. NPS-026 v1.1.0 — no decision required. Carry the hardware-root
question into the NPS-026 §6 human crypto review (NPC-002 §6.2), which
is also the gate that keeps NPS-026 at `Draft`.

---

## Bundle C — Ratify what is already running

### C1. ADR-0022 / ADR-0023 (NyVault)

**Recommend: confirm the 2026-09-06 acceptance, then ratify
as-implemented.** The acceptance commit (`3262618`) matches the
implementation record (0.14.5–0.14.19) and the ADRs' own status
sections; the thing that drifted is the index. Voiding would revert a
correct description of shipped, reviewed-against-the-tree behavior
without a data reason. Ratification ledger: the §27/§29 performance
record. Consequence: index rows 0022/0023 → `Accepted`.

Note for the session: ADR-0024 (streaming data plane) is the drafted
next step gated on C1 plus the `--vault-stream` evidence run — it
stays `Proposed` today regardless.

### C2. Mechanics (for the record)

- A1 acceptance transitively un-blocks NPS-010 → `Accepted`.
- NPS-026 stays `Draft` pending the §6 human crypto review.
- The B1 reconciliation rule (implementation + benchmark records are
  ground truth) is already applied by this brief's fact base.

---

## Consequences ledger — what each acceptance mechanically triggers

| Acceptance | Immediate tree edits | Follow-up work item |
|---|---|---|
| A1 | index row 0009 → Accepted; NPS-010 → Accepted | none (mechanism shipped) |
| A2 | index row 0007 → Accepted; NPS-005 §3 default marked | none |
| A3 | index row 0013 → Accepted; NPS-012 §6.1 amendment | scheduler defaults land with the kernel work |
| A4 | index row 0016 → Accepted | reopen criterion recorded in the ADR |
| B1.1 | index row 0018 → Accepted | — |
| B1.2–4 | review package §4.2 disposition notes | **already staged**: branch `audit-b1-hardening` (NOT on main) implements all three — scheme-2 details coverage, one hasher, opt-in snapshot persistence (JSONL deltas, 122 µs/event O(1)); 25/25 audit tests + full suite green; merges the moment the Group directs |
| B2 | NPS-010 §9 proposal table → normative; §7 SUSPENDED amendment | none |
| C1 | index rows 0022/0023 → Accepted | `--vault-stream` evidence run feeds ADR-0024's review |

All edits happen in the decision-log commit, per the C2 rule.

---

## Open questions the brief deliberately does not answer

1. If A1 opts for dynamic shares eventually: the tuning parameter set
   and its observation window.
2. B1-4's persistence format and location (container state dir vs a
   dedicated audit store) — implementation judgment, not Group policy.
3. If C1 is voided instead of confirmed: whether `3262618` is reverted
   or superseded by a forward correction (the B1 lesson suggests
   forward correction with the reconciliation recorded).

---

**End of Document**
