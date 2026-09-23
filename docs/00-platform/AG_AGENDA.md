---
title: Architecture Group Agenda — Pending Decisions
document_id: AG-AGENDA-2026-09
version: 1.9.0
status: Three standing items registered (NPS-028 acceptance review 2026-09-22 — implementation complete + validated, §10 evidence attached; BUILD-001/BUILD-ARCH document duality 2026-09-23 — the build-architecture deliverable exists twice under two document_ids; debug attach 2026-09-23 — DBG-001 Phase B's CAP-DEBUG-ATTACH container-attach channel, briefed with three options). All prior items decided (D1 static default retained; D2 NPS-027 Accepted; D3 the ADR-0014 mirror adopted; D4 the concrete crypto scheme accepted, landed as NPS-026 v1.3.0 §6.7)
owners: [Nyrqis Architecture]
created: 2026-09-18
ai_assisted: true
depends_on: [ADR-0007, ADR-0009, ADR-0013, ADR-0016, ADR-0018, ADR-0022, ADR-0023, NPS-010, NPS-026, NPS-027, NPC-001]
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
| D1 dynamic shares as the default | Static default retained, dynamic opt-in normative (Option A per the pre-read) — nothing remains open in the rate-limiting space | Architecture Group (decision input: repo operator via recorded session) | 2026-09-21 |
| D2 NPS-027 Package Trust Model | **Accepted** (Option A per the pre-read) — closes the planned threat-model phase list formally; FIND-PACKAGE-003's design decision owned by NPS-026's review and exercised the same day (D3) | Architecture Group (decision input: repo operator via recorded session) | 2026-09-21 |
| D3 publisher key trust (REQ-SEC-0004) | The full ADR-0014 mirror adopted (Option A per the pre-read): bundled platform root set, protected-confirmation enrollment, revocation via platform list + expiry with the advisory/block propagation split (advisory at launch, hard block at install/update/verify), cross-signature rotation; concrete crypto scheme stays reserved per NPC-002 §6.2. Draft §6.3 amendment text reviewed and landed as NPS-026 v1.2.0 §6.3 the same day | Architecture Group (decision input: repo operator via recorded session) | 2026-09-21 |
| D4 concrete crypto scheme (the NPC-002 §6.2 reserve) | **Accepted** — the dedicated human review sat 2026-09-22 (decision input: repo operator, working from `AG_BRIEF_NPS026_CRYPTO_SCHEME.md` with its tree claims re-verified that day) and decided: primitives per role as proposed (Ed25519 + SHA-256; root-set-signed revocation lists; ADR-0023 envelope encryption for the key store; image-anchored root set; RSA/ECDSA/novel constructions explicitly rejected); **G1 adopted with the display form amended — fingerprint = SHA-256(public key) displayed in FULL 64 lowercase hex** (no truncation anywhere; the shipped 8-byte `key_id` migrates with a version marker); **G3 deferred to NPS-026 §9** (canonical byte form stays implementation-local until serialization is decided; no interoperability claims until frozen); **G4 confirmed** (single-root MUST-verify for revocation authenticity, any-root quorum MAY sign); the freeze/defer split adopted (freeze: primitive set, fingerprint, fail-closed posture, one-pipeline rule; defer: list cadence, root membership, rotation cadence, serialization). Scheme text landed as NPS-026 v1.3.0 §6.7 the same day; §6.3.6 re-points at §6.7; NPS-028's fence narrows to the §6.7.3 deferral; REQ-SEC-0003's implementation gate opens | Architecture Group (decision input: repo operator via recorded session) | 2026-09-22 |

---

## Standing items

**Registered 2026-09-21 (after the D1/D2/D3 session) — DECIDED 2026-09-22 (D4):**

- **NPS-026 §6.2 — the concrete crypto-scheme review (review input
  READY → review CONCLUDED, scheme ACCEPTED).** The propose-side
  package is at
  `AG_BRIEF_NPS026_CRYPTO_SCHEME.md` (v1.0.0): Ed25519 + SHA-256 per
  role, grounded in the Ed25519 signing half that already ships
  (`package_signing.py`/`update_signing.py`/`package_repo.py`, 37
  tests), with four recorded gaps (G1 fingerprint definition being the
  real decision) and an explicit freeze/defer split. NPC-002 §6.2
  required **dedicated human expert review** — that review sat
  2026-09-22 (repo operator via recorded session, tree claims
  re-verified before it sat) and decided per decision-log row D4:
  scheme accepted, G1 adopted with full-64-hex display, G3 deferred to
  §9, G4 confirmed. Landed as NPS-026 v1.3.0 §6.7 the same day; the
  reserve is removed and NPS-026's remaining gate to `Accepted` is
  implementation validation only.

**Decided 2026-09-21 — the session that worked from the registered list below:**

All three registered standing items were decided in the 2026-09-21
session (decision log rows below); the registered text is retained for
the record, each item marked with its outcome.

- **ADR-0009 follow-on — dynamic shares as the DEFAULT (policy; data
  COMPLETE).** **DECIDED 2026-09-21 (D1): static default retained —
  Option A per the pre-read.** The item originated as the The 2026-09-19 session decided A1's mechanism
  question: static `fair_shares` default with dynamic opt-in. Making
  dynamic mode the default is the one remaining policy question in
  the rate-limiting space. Correction (2026-09-20): this item was
  registered claiming the dynamic-mode adversarial data did not exist
  — that was wrong; §32e (2026-09-10) measured it, and §32f
  (2026-09-20) completed the ledger with the missing lone-sender
  dynamic number (lone sender: 313/s static vs 2,063/s dynamic;
  8 senders: 250/s each in both modes; flood + legitimate: legit
  fully protected either way, the flooder's take 271/s static vs
  1,022/s dynamic). Every cell now has a number; **the item is
  decision-ready** — purely the policy question §32e framed: is the
  larger low-occupancy abuser take acceptable platform-wide? No
  measurement remains to collect; a session can decide it any time.
  Pre-read: `AG_BRIEF_DYNAMIC_SHARES.md` (AI-drafted, suggest-side —
  three options with a consequences ledger; recommends retaining the
  static default). **Outcome: the recommendation was followed; nothing
  in the rate-limiting space remains open.**

- **NPS-027 — Package Trust Model (threat model Phase 7; Draft →
  review for Acceptance).** **DECIDED 2026-09-21 (D2): Accepted —
  Option A per the pre-read; FIND-PACKAGE-003's design decision
  formally owned by NPS-026's review (exercised the same day as D3).**
  Found during the 2026-09-20 next-actions
  audit: REPOSITORY_STATE items 18/20 still asked for "a real
  package-signing/PKI specification" and called Phase 7 "the last
  planned phase" — but Phase 7 shipped as `NPS-027` on 2026-08-12 and
  has been awaiting this review ever since. What the session reviews:
  the spec's dispositions of `FIND-PACKAGE-001` (checksums detect
  corruption, not tampering; publisher authenticity requires the
  signature block) and `FIND-PACKAGE-004` (overlay content as
  user-installed mods), the publisher-identity model, and the
  verification boundary — it extends NPS-006/NPS-026 and leans on
  ADR-0014 (Secure Boot trust anchor) and ADR-0018 (tamper-evident
  records). Honest scope: like Phases 3 and 6, it reasons from the
  specified guarantees of NPS-006/NPS-026, not from code — no package
  manager exists yet. Acceptance here unblocks item 18's residual
  (PKI implementation) and closes the planned threat-model phase list
  formally.  Adjacent to B3 (NPS-026 v1.1.0, already informational).  Pre-read:
  `AG_BRIEF_NPS027_PACKAGE_TRUST.md` (AI-drafted, suggest-side —
  findings ledger, three options, recommends accepting NPS-027 and
  routing FIND-PACKAGE-003's design decision to NPS-026's review).
  The routed design decision now has its own pre-read too:
  `AG_BRIEF_NPS026_KEY_TRUST.md` (the publisher-key trust model,
  recommendating the ADR-0014 mirror; **v1.1.0 appends the draft §6.3
  amendment text** so the Group reviews the decision and its normative
  wording in one sitting) — prepared so NPS-026's path to `Accepted` is
  not blocked by an undesigned REQ-SEC-0004.
  **Registered as standing item D3 below** (2026-09-20).

- **REQ-SEC-0004 — Publisher key trust (the mechanism design for
  NPS-026 §6.3; analysis COMPLETE, decision-ready).** **DECIDED
  2026-09-21 (D3): the ADR-0014 mirror adopted — Option A per the
  pre-read, including the advisory/block revocation-propagation split;
  the draft §6.3 amendment text landed as NPS-026 v1.2.0 §6.3 the same
  day.** `FIND-PACKAGE-003`
  (routed here by NPS-027) requires the enrollment/revocation design
  §6.3's pattern clause defers. The pre-read
  (`AG_BRIEF_NPS026_KEY_TRUST.md`) frames the four sub-decisions —
  initial trust distribution, enrollment UX, revocation propagation,
  rotation — and ledgers three options, recommending the full ADR-0014
  mirror (bundled platform root set; protected-confirmation enrollment;
  revocation advisory at launch, blocking at install/update;
  cross-signature rotation). What the session judges: the sub-decisions,
  with the revocation-propagation split as the one genuinely
  policy-shaped call. The concrete crypto scheme stays reserved per
  NPC-002 §6.2, and the mechanism text lands via NPS-026's amendment on
  its path to `Accepted` — so this decision and D2's unblock each other.

---

## Standing items — registered 2026-09-22 (evening session)

- **NPS-028 — Package PKI Implementation Surface (Draft → review for
  Acceptance; implementation COMPLETE + VALIDATED).** Registered after
  the 2026-09-22 implementation session landed the surface in full:
  the §3 key store, §4 verification pipeline (TOFU fail-closed, one
  ordered path), §5 revocation list + the §5.1 out-of-band daemon
  wiring, §6 enrollment + cross-signed rotation, §7 ADR-0018 audit
  chain, §3.2's authority/IPC/deployment stack (the
  `nyrqis-pki.service` unit), and §3.4 custody — with an end-to-end
  daemon drill and a mechanical validation probe on record. The
  evidence the Group reviews: **NPS-028 §10** (the 15-claim
  normative-claims table, 15/15, every row backed by an importable
  assertion or a named test module), the suite record (package-security
  set 217 green in one run; PKI module 97, byte-identically verified
  from a clean worktree checkout), and the drill/v0.9.1 revision entry
  (a real wire gap found and closed — the transport's write path was
  never wire-tested until the drill caught it). What the session
  judges: (1) Accept the validated surface (or name amendments); (2)
  confirm the §5.3 stale-list deferral stays frozen-by-design until
  implementation validation needs it; (3) note that the document's
  one remaining substantive dependency — NPS-026 §9's canonicalization
  decision (G3) — gates the §6.7.3 fence, not this surface's
  mechanisms. Interlock: NPS-026 exits Draft through the same review
  sitting once on both documents (its §6 machinery and this §3–§7
  implementation are two halves of one acceptance). **NPS-026-side
  evidence (same session):** its §6.7 scheme, §6.2 coverage rule, and
  the signed-index/delta distribution machinery are implemented
  (`package_signing.py`/`package_repo.py`/`update_signing.py`; 64
  module tests) and verified by mechanical probe — 7/7 repo-half
  claims (tamper-evident index refusal, untrusted-key refusal,
  payload-swap detection, §9 delta publication) recorded in NPS-026
  §1 + v1.3.1. What remains open there is exactly what its text
  defers: §9.2/§9.3 serialization (§6.7.3), §10–§12's
  transaction/streaming/rollback halves, §6.7.5's deferred
  operational parameters —  the Group may accept the §6/§7 machinery
  while carrying those sections forward. DECISION-READY —
  no measurement, implementation, or drafting item remains ahead of
  the review.

---

## Standing items — registered 2026-09-23 (docs-backlog session)

- **BUILD-001 vs BUILD-ARCH — the build-architecture deliverable exists
  twice under two document_ids; pick the canonical one and dispose of
  the other copy.** Found by the 2026-09-23 docs-backlog pass while
  striking the stale M11 "remaining" list (both files verified on
  disk; finding recorded in `NEXT_SESSION_PLAN.md`'s session item and
  pinned by the `build-architecture-dual-doc` premise so it cannot go
  quiet again):

  - `docs/reference/build/BUILD_ARCHITECTURE.md` — `document_id:
    BUILD-001`, **status: Draft**, created 2026-09-06, `depends_on:
    [ADR-0020, NPC-003]`. The copy the rest of the tree actually
    cites: the roadmap's struck M11 item 9, TUT-003's `depends_on`,
    and the sdk docs.
  - `docs/00-platform/BUILD_ARCHITECTURE.md` — `document_id:
    BUILD-ARCH`, **status: Accepted**, created 2026-09-01,
    `depends_on: [ADR-0012, ADR-0020, NPS-017]`, front-matter claims
    `satisfies: [NPC-007 gap 9]`. Cited by nothing else in the tree.

  The bodies diverge materially (531 diff lines across 308 vs 235
  lines) and BOTH claim NPC-007 gap 9. The Group's two sub-decisions:
  (1) which document_id is canonical, and the other copy's
  disposition — delete, redirect-stub, or merge; (2) the status of the
  Accepted marking: no Architecture Group decision record was found
  for it, the same shape as the unsanctioned-flip class already
  flagged on ADR-0019/0025/0026 — if BUILD-ARCH is canonical, its
  Accepted status needs either a Group record or a revert to Draft; if
  BUILD-001 is canonical, the divergent Accepted twin should not
  remain as-is either way.  NOT in dispute: the deliverable itself
  exists and satisfies M11 gap 9 (PERF-001, same directory and era,
  is unambiguous). Pre-read:
  `AG_BRIEF_BUILD_ARCH_DUALITY.md` (AI-drafted, suggest-side —
  verified content analysis of both bodies with the contradiction
  table, three options with consequences ledgers, downstream
  touch-points sized; recommends Option A: BUILD-001 canonical,
  absorb BUILD-ARCH's practical sections, remove the copy, dissolve
  the unsanctioned Accepted marking with the file).
  DECISION-READY — purely judgment; no measurement,
  implementation, or drafting item stands ahead of it.

## Standing items — registered 2026-09-23 (debug-tooling session)

- **DBG-001 Phase B — the container debug attach channel: accept the
  `CAP-DEBUG-ATTACH` capability and the launcher-mediated attach
  design, or close the item as observational-only.** The M14 Phase 3
  debug-tooling item's last ungated piece: Phase A (incident bundle)
  and the Phase C rider landed 2026-09-23 client-side with no new
  daemon surface; Phase B is the part that genuinely needs the Group,
  because an attach path is a deliberate entry into the namespace
  isolation that NPS-017 §4 and NPS-021 have already paid to
  harden. The pre-read (`AG_BRIEF_DEBUG_ATTACH.md` v1.0.0 —
  AI-drafted, suggest-side, D3/D4 format) carries the verified
  evidence (the only entry today is host-side `container_exec` via
  nsenter; no `CAP-DEBUG-ATTACH` in NPS-011 §3's registry; the
  seccomp profile denies ptrace by design), the regulatory frame any
  option must satisfy (NPS-011 §4.3/§5, ADR-0018 chaining, an
  NPS-021 escalation pass), and three options with consequences
  ledgers: **A** launcher-mediated attach, operator-only, denied by
  default, debugger staged from the host (recommended — the
  isolation boundary moves host-ward, where the operator already
  sits; reuses the `container_exec` mediation pattern); **B**
  developer-mode sidecar with debugpy/gdbserver inside the container
  (rejected in the brief: requires ptrace relaxation, puts debugger
  binaries inside the boundary, and creates debug-vs-prod image
  divergence — the trade the threat model exists to scrutinize);
  **C** no attach — extend Phase A's observational surface and close
  the item honestly. Downstream if accepted: NPS-011 v1.4.0, an
  NPS-021 addendum, a new authority-guarded op family, and the
  roadmap item strikes as done. DECISION-READY — purely judgment.

---

**End of Document**
