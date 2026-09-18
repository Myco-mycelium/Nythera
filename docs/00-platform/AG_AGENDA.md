---
title: Architecture Group Agenda — Pending Decisions
document_id: AG-AGENDA-2026-09
version: 1.0.0
status: Informational
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
   (amend the wording honestly) or direct the fix (measured:
   canonical-JSON hashing ≈ 4× hash work, ~17 µs/event total, still
   ~35k events/s; recommended).
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

### C1. ADR-0022 / ADR-0023 — NyVault service + key custody (implemented, unratified)

Both are **marked Proposed in frontmatter while fully implemented**
(lifecycle ops, FUSE passthrough, at-rest encryption with per-volume
DEKs and Rust-held KEK custody, KEK rotation without re-encryption,
quotas, path-scoped grants). This is the reverse of B1's discrepancy:
implementation has outrun the review.

**Decision:** ratify as-implemented (with the §27/§29 performance
record as the known-cost ledger), or direct changes. Note ADR-0024
(streaming data plane) is the drafted next step gated on this review
plus the `--vault-stream` evidence run.

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

## Decision log (fill at the session)

| item | decision | owner | date |
|---|---|---|---|
| A1 mechanism + defaults | | | |
| A1 static/dynamic shares | | | |
| A2 default level | | | |
| A3 weight table + RT reserve | | | |
| A4 FUSE-first confirmed | | | |
| B1 status reconciliation | | | |
| B1 tamper scope (a/b) | | | |
| B1 dual mechanisms | | | |
| B1 persistence requirement | | | |
| B2 defaults + standing rules | | | |
| B2 SUSPENDED normative | | | |
| C1 NyVault ratification | | | |

---

**End of Document**
