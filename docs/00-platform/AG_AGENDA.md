---
title: Architecture Group Agenda — Pending Decisions
document_id: AG-AGENDA-2026-09
version: 1.1.0
status: Disposed — decisions recorded 2026-09-19 (see the decision log)
owners: [Nyrqis Architecture]
created: 2026-09-18
ai_assisted: true
depends_on: [ADR-0007, ADR-0009, ADR-0013, ADR-0016, ADR-0018, ADR-0022, ADR-0023, NPS-010, NPS-026, NPC-001]
---

# Architecture Group Agenda — Pending Decisions

Every decision currently waiting on the Architecture Group,
consolidated onto one agenda with its data, its review package (where
one exists), and the specific decision required. Nothing here is
blocked on more benchmarking — the 2026-08-12 → 2026-09-18 passes
closed every measurement gate; what remains is judgment.

Reading order for a first session: the three bundles below are
independent; within a bundle the items are ordered. Time-boxed
suggestion: §A ≈ 45 min, §B ≈ 60 min, §C ≈ 30 min.

> **Pre-flight (2026-09-19, AI-verified against the tree — claims the
> session can take as ground truth):** every B1 technical claim was
> re-verified in `backend/container.py` and holds: (1) the primary
> chain's hash content is `salt‖prev_hash‖op‖ts` — `details` is NOT
> hashed (tamper-scope hole demonstrated); (2) the second family
> (`create_audit_chain`/`append_audit_entry`) hashes a canonical-JSON
> payload that DOES cover `result`, but is unsalted and rooted at an
> empty `prev_hash`; (3) both chains are plain in-process attributes —
> memory-only. ADR-0009's review package exists at the documented
> path. Frontmatter sweep: ADR-0007/0009/0013/0016/0024 `Proposed`;
> ADR-0018/0022/0023 `Accepted` in their own text; the ADR index
> (005-ADR_INDEX.md) still says `Proposed` for all three — see the
> corrected C1 below. An AI-drafted per-item pre-read with options,
> recommendations, and a consequences ledger exists at
> `AG_DECISION_BRIEF.md` — every recommendation there is overridable
> Group judgment (NPC-001 §11.1), not a decision.

---

## Bundle A — Accept the measured pipelines (data complete, mechanisms shipped)

### A1. ADR-0009 — IPC rate limiting (review package ready)

`docs/reference/adr/ADR-0009-review-package.md` · data §32a–e ·
mechanism shipped (`FairTokenBucket`, fair-by-default endpoints) and
adopted normatively in NPS-010 §7.1.1.

**Decisions:**
1. Accept the mechanism + §4 default parameters (or amend).
2. Static vs. dynamic `fair_shares` (the package's one open mechanism
   question; static default + dynamic opt-in is the recommended and
   shipped posture).

### A2. ADR-0007 — Zstd default compression (data complete)

Data §31: real-asset sweep (ratio flat ~1.07 at every level on
already-compressed data; ≥7 buys ≤2% for 60× compute), real LZ4 fast
path (2.7× zstd-1 at equal ratio), concurrent scaling (2.2× at 8
threads).

**Decision:** default level per NPS-005 §3's table (the data says:
low single digits; ≥7 is not justified by ratio on real data).

### A3. ADR-0013 — EEVDF scheduler (tuning data complete)

Data §33 (discrete-event simulation): request size governs
interactive latency (≤1.5 ms → zero overruns); Linux-6.6 weight table
recommended (best tail isolation, share accuracy 1–2%); RT reserve
non-optional (admission ≤ ~60–70%).

**Decisions:** adopt the Linux-6.6 table; set the RT admission
reserve; require input/audio classes to submit small requests
(NPS-012 §6.1 shape).

### A4. ADR-0016 — NyFS FUSE backend (partially measured)

Data §5–§15: live FUSE mount measured end-to-end (writeback-cache
negotiation: ~25× write improvement, ~40–46 MB/s), commit-cost levers
(journal commit ~60–70×), compaction, dedup, mixed workloads.

**Decision:** confirm FUSE-first holds (the kernel-module fallback
question) or name the workload class that reopens it.

---

## Bundle B — Decide the recorded caveats (new findings, 2026-09-18)

### B1. ADR-0018 — hash-chained audit log (review package ready)

`docs/reference/adr/ADR-0018-review-package.md` · data §34 · the
package's four decisions:

1. **Status**: ADR text says `Accepted`, all indexes say `Proposed` —
   reconcile in whichever direction is intended.
2. **Tamper scope**: the chain hash does NOT cover the `details`
   payload (§34e, demonstrated) — accept as a scoped limitation
   (amend the wording honestly) or direct the fix (prototyped
   end-to-end: per-event scheme marker + canonical-JSON details in
   the hashed content, measured 19.0 µs/event append with the full
   suite passing and the §34e mutation table all-detected — see the
   review package §4.2; recommended).
3. **Dual mechanisms**: a second chain family
   (`create_audit_chain`/`verify_audit_chain`) with weaker guarantees
   exists in the same file — consolidate or scope explicitly.
4. **Persistence**: both chains are memory-only; restart destroys the
   record (restart is the cheapest tamper) — set a persistence
   requirement or scope the guarantee to one manager lifetime.

### B2. NPS-010 §7.2/§9 — container resource-limit defaults (proposal staged)

Data §35 (real cgroup-v2 enforcement); the proposal table is in
NPS-010 §9 v1.5.0.

**Decisions:**
1. Keep `memory_mb=256` / `pid_limit=64` / `cpu_quota=None` as the
   shipped defaults (data: 28–80× footprint headroom at the floor;
   64 PIDs = 1.5× a modest supervisor; unlimited quota avoids the
   bimodal throttle tail).
2. Adopt the standing rules: supervisor shapes raise the PID limit
   explicitly; assigned quotas sized ≥ ~2.5× average demand and
   monitored via p95 + `nr_throttled` (mean-usage monitoring
   provably misses the tail).
3. Make the SUSPENDED-accounting rule normative at the next §7
   amendment: full memory accounting, zero CPU accounting; suspension
   is not budget relief.

### B3. NPS-026 v1.1.0 — package format × NyVault (informational, 2026-09-18)

§13 records the four implementation interactions (volumes are NyFS
images; integrity trees cover plaintext while vault AEAD covers
at-rest — they compose without re-encryption; streaming install into
vaults inherits the 32 KiB CALL paging and is commit-bound until
write batching; uninstall maps onto creator-scoped volume lifecycle)
and §14 adds two open questions (one hardware root for both the
package-signing trust anchor and vault KEK custody; registry
vocabulary following the manifest serialization decision).

**Decision:** none required now — but B3's hardware-root question
should be resolved alongside §B-item-adjacent crypto design (and it
feeds C1's review gate).

---

## Bundle C — Ratify what is already running

### C1. ADR-0022 / ADR-0023 — NyVault service + key custody (accepted in text, index stale, ratification unconfirmed)

**Corrected 2026-09-19 (pre-flight; this agenda v1.0.0 had the
polarity wrong):** both ADRs say `Accepted` in their own text since
2026-09-06 (`3262618` "Accept ADR-0022 and ADR-0023"), are fully
implemented (lifecycle ops, FUSE passthrough, at-rest encryption with
per-volume DEKs and Rust-held KEK custody, KEK rotation without
re-encryption, quotas, path-scoped grants) — and the ADR index still
says `Proposed` for both. Same polarity as B1: own text vs index.
What no record establishes is whether the 2026-09-06 acceptance was a
sanctioned Architecture Group decision — no sign-off record exists in
the tree.

**Decisions:**
1. Confirm or void the 2026-09-06 acceptance: confirm = it was a
   sanctioned decision and the index gets corrected to match;
   void = the flip was premature, the ADRs return to `Proposed`, and
   ratification happens in this session. Per C2's ground-truth rule,
   reconcile against the implementation record either way.
2. Ratify as-implemented (with the §27/§29 performance record as the
   known-cost ledger), or direct changes. Note ADR-0024 (streaming
   data plane) is the drafted next step gated on this review plus the
   `--vault-stream` evidence run.

### C2. Sign-off mechanics

- ADR-0009's acceptance transitively un-blocks NPS-010 → `Accepted`
  (its only remaining blocker).
- NPS-026 stays `Draft` until the §6 signature scheme passes the
  NPC-002 §6.2 dedicated human crypto review (unchanged by v1.1.0).
- Status reconciliation rule for the future (the B1 lesson): when an
  ADR's own text and the indexes disagree, the *implementation
  record* in `IMPLEMENTATION_STATUS.md` and the benchmark record in
  `tests/BENCHMARK_RESULTS.md` are the ground truth the review
  reconciles against — not either prose source.

---

## Decision log — filled 2026-09-19

| item | decision | owner | date |
|---|---|---|---|
| A1 mechanism + defaults | Accepted — mechanism + §4 defaults as shipped (recommendation followed) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| A1 static/dynamic shares | Static default, dynamic opt-in (shipped posture) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| A2 default level | Accepted — default level 3 (NPS-005 §3 low-single-digits row) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| A3 weight table + RT reserve | Accepted — Linux-6.6 table; RT admission reserve ≤ ~60–70%; small-request requirement | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| A4 FUSE-first confirmed | Confirmed — kernel-module fallback stays a named reopen criterion | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B1 status reconciliation | Reconciled to Accepted (ADR text was right; indexes corrected) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B1 tamper scope (a/b) | (b) fix DIRECTED — scheme-2 details coverage, merged from audit-b1-hardening | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B1 dual mechanisms | Consolidated — one scheme-2 hasher behind both families | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B1 persistence requirement | Set — opt-in JSONL snapshot persistence; daemon path wired to the state-file dir | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B2 defaults + standing rules | Adopted as proposed — 256 MB / 64 PIDs / unlimited quota + standing rules | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| B2 SUSPENDED normative | Adopted — full memory, zero CPU; suspension is not budget relief (NPS-010 v1.6.0) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| C1 confirm/void the 2026-09-06 acceptance | CONFIRMED — sanctioned decision; index rows corrected to match | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |
| C1 NyVault ratification | Ratified as-implemented (§27/§29 performance record = known-cost ledger) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-19 |

---

**End of Document**
