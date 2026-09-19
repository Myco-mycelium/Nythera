---
title: ADR-0018 Review Package — Hash-Chained Audit Log Close-Out
document_id: ADR-0018-REVIEW
version: 1.0.0
status: Informational
owners: [Nyrqis Architecture]
created: 2026-09-18
ai_assisted: true
depends_on: [ADR-0018, NPS-010, NPS-021]
---

# ADR-0018 Review Package — Architecture Group Sign-Off

Everything the Architecture Group needs to move ADR-0018 out of
`Proposed` — or, more precisely, to reconcile its status and accept its
measured record — consolidated from the benchmark data, the shipped
implementation, and three implementation findings this close-out pass
surfaced. All numbers cite their section in `tests/BENCHMARK_RESULTS.md`;
the instruments are re-runnable.

## 1. What is being asked

1. **Reconcile the status**: the ADR's own prose says `Accepted`, its
   frontmatter says `Accepted`, but the indexes
   (`005-ADR_INDEX.md`, `004-SPECIFICATION_INDEX.md`,
   `docs/reference/adr/README.md`) say `Proposed` — see §2 below.
2. **Accept the measured record**: the "negligible overhead" premise in
   ADR-0018 §"Consequences" is no longer an expectation — it is
   measured (§3 below).
3. **Decide the tamper-scope question**: the shipped chain hash does
   NOT cover the event's `details` payload — the single most
   tamper-worthy field — so the "tamper-evident" guarantee has a hole
   (§4). Accept as a recorded limitation, or direct the fix (cost
   measured: §4.2).
4. **Decide the two structural caveats**: a second, parallel chain
   mechanism exists with the same class of gap (§5.1), and neither
   chain survives process restart (§5.2) — both sit oddly under the
   threat model the ADR was written to close (`FIND-CAPABILITY-002`,
   NPS-021 §5.2).

## 2. Status discrepancy (found during this close-out)

| source | says |
|---|---|
| ADR-0018 frontmatter (`status:`) | `Accepted` |
| ADR-0018 prose (`## Status`) | `Accepted — implemented …` |
| `005-ADR_INDEX.md` | `Proposed` |
| `004-SPECIFICATION_INDEX.md` | `Proposed` |
| `docs/reference/adr/README.md` | `Proposed` |
| `REPOSITORY_STATE.md` (ADR list) | `Proposed`, "pending Architecture Group review (not benchmark-blocked)" |

The indexes are the more conservative and more recently maintained
record; the ADR document itself appears to have been written as if
acceptance were already granted (it does say "Benchmark pending", so
the author knew the gate was open). **The review should end with one
status everywhere.** If accepted: update the three indexes and the
ADR's `updated:` field; if not: correct the ADR's own status line.

## 3. Benchmark record (complete)

ADR-0018's only open gate was NPC-002 §5.2 ("not asserted without a
benchmark"). That gate closed 2026-09-18:

| instrument | finding |
|---|---|
| `tests/benchmark_adr0018.py` §34a (append, 100k-event chain, real methods) | p50 **6.4 µs**/event, p95 12.3 µs, throughput **≈100k events/s** sustained; GC-on steady state identical (one collection pause at max, not per-event cost) |
| §34b (verify scaling, 1k→100k) | **O(n) with a stable constant**: 3.33–3.54 µs/event (6% spread across two orders of magnitude — no superlinearity); verify is out-of-band, never on the grant/revoke hot path |
| §34c (cost decomposition) | raw sha256 = **19%** of the append cost; the rest is Python bookkeeping (dict/list machinery, the duplicate `_audit_trail` write) — any future "chain too slow" conclusion should target bookkeeping, not crypto |
| §34d (overhead vs audited ops) | **2.0–2.3%** of the wire call p50 (§20, ABI 2.0.0), 7.6% of the in-process call p50 — "negligible" confirmed as measured fact |

Re-run: `python3 tests/benchmark_adr0018.py` (Linux 6.14, x86_64,
CPython 3.12; calls the exact `backend/container.py` methods the IPC
control plane invokes per grant/revoke — the honesty notes in the
benchmark docstring apply).

**Verdict this record supports**: the mechanism's cost profile is a
non-issue at any plausible audit volume. No performance dimension
should block acceptance.

## 4. The tamper-scope question (the substantive decision)

### 4.1 The finding (§34e, demonstrated on the real code)

The hashed content is `salt + prev_hash + op + timestamp`. The
`details` payload — *which capability was granted, to whom, by whom* —
is stored alongside the chain but is **not hashed and not covered by
`verify_audit_integrity`**:

| mutation of a stored event | detected? |
|---|---|
| `details` (the capability granted, to whom) | **NO — verify still reports `valid=True`** |
| `op` | yes |
| `timestamp` | yes |

So a tamperer with write access to the running manager's state can
rewrite *what was granted* — the exact payload `FIND-CAPABILITY-002`
(NPS-021 §5.2) said the log must protect — while the chain reports
full integrity. The chain still proves op/sequence/timestamp history;
it does not prove payload history. The duplicate `_audit_trail`'s
`integrity_hash` field references the event hash but does not cover
the details either.

ADR-0018's decision language promises a "tamper-evident" record;
§4.2's NPS-010 amendment (v1.1.0) requires the audit log be
"tamper-evident". Under the plain meaning of the threat model, the
shipped scheme does not yet meet its own decision's promise for the
payload dimension.

### 4.2 The fix and its measured cost

Hash a canonical serialization of the details with the event:
`sha256(salt + prev_hash + op + ts + json.dumps(details, sort_keys=True,
separators=(',',':')))`. Cost, measured on this host (200k iterations,
CPython 3.12):

| scheme | hash+serialize per event |
|---|---|
| current (`salt+prev+op+ts`) | 3.25 µs |
| with canonical details JSON | 13.26 µs (**4.1× the hash work**) |

In context (§34d): the fixed scheme's append lands around
`~7 µs − 3.25 + 13.26 ≈ 17 µs`/event — still ≈35k events/s sustained
and still ~5% of the wire call p50 it audits. **The fix is cheap at
the platform's scale; it is not a performance question.**

**Prototype result (2026-09-18, uncommitted by design):** the fix was
prototyped end-to-end and measured against the real suite — a
`scheme` marker per event, canonical-JSON details in the hashed
content, and a verify path that recomputes from exactly what append
hashed (None vs {} preserved, not coerced). Measured: append
**19.0 µs/event** with details (6.9 µs when details=None — the v1
content form), verify **10.1 µs/event** O(n)-preserved (110k mixed
events in 2.1 s) — the §34e mutation table flips to all-detected,
full `test_backend.py` suite passes (2532 OK). The prototype's diff
was deliberately reverted pending this Group's decision; the numbers
here are its record. (Estimate check: the ~17 µs prediction above
was within ~12% of the measured 19 µs.)

Back-compatibility note for the implementer: verify must accept both
schemes during a transition (or the chain must be versioned per
event) — the prototype's per-event `scheme` marker is the working
demonstration; legacy v1 events verify under the op+timestamp rule.

**Branch staged 2026-09-19 (`audit-b1-hardening`, NOT on main):** the
full B1 hardening exists as a ready-to-apply branch, validated on the
tree it changes — 25/25 audit tests (15 existing contract tests stay
green + 10 new tests pinning the review decisions: chain-level details
tamper detection, scheme-marker stripping detected, unknown-scheme
reporting, scheme-1 legacy events still verifying *with their
documented hole intact*, chain-2 result coverage, chain-break
detection, restart-preservation, snapshot-tamper rejection,
persistence off by default, env/override semantics), FULL backend
suite green. Measured on that branch: append **22.5 µs/event** p50
under scheme 2 (vs 6.4 µs scheme 1 — the details coverage cost,
consistent with §4.2's estimate), verify linear ~15 µs/event, and the
§34e mutation table now reports chain-level details tamper DETECTED
plus scheme-strip detected. B1.4 persistence ships there as an
opt-in (`audit_snapshot_dir` / `NYRQIS_AUDIT_SNAPSHOT_DIR`, off by
default): per-append JSONL delta lines (measured **122 µs/event,
O(1)**) with an atomic compaction rewrite — the branch's own history
records a first full-rewrite-per-append design measuring 3800 µs/event
at n=200 (O(n)) being killed by its benchmark. The branch merges only
when this Group directs the fix; main carries the decision, not the
change.

### 4.3 Options for the Group

- **(a) Accept the limitation as recorded** — argue the log's primary
  threat is op/timestamp falsification, details live in user-visible
  audit tooling anyway. This is a *weakening* of the ADR's own
  decision language; if chosen, ADR-0018 and NPS-010 §8.1 wording
  should be amended to scope the guarantee honestly ("tamper-evident
  for op, sequence, and timestamp; payload integrity is best-effort").
- **(b) Direct the fix** (recommended): hash canonical details JSON,
  version the event hash scheme, keep the §34 cost budget (<20 µs/event
  measured headroom). Small change in `append_audit_event` +
  `verify_audit_integrity` + a regression test pinning the §34e
  mutation table to all-yes.

## 5. Structural caveats (surfaced during close-out)

### 5.1 A second, parallel chain mechanism

`backend/container.py` contains TWO hash-chain implementations:

1. `initialize_audit_integrity` / `append_audit_event` /
   `verify_audit_integrity` — per-container, on `Container` state
   (`container._audit_hash_chain`), driven by the CLI
   (`nyrqisctl append-audit-event`, `verify-audit-integrity`) and the
   control plane (ipc/control.py ~8553). This is the one ADR-0018's
   status line names and the one §34 measured.
2. `create_audit_chain` / `append_audit_entry` /
   `verify_audit_chain` / `export_audit_chain` — a manager-level
   `self._audit_chains` dict keyed by `chain-<container-id>`, with its
   own control-plane ops (ipc/control.py ~2241). Its entries carry
   `prev_hash`/`hash`, but **its verify does not recompute the hash
   from entry content** — it compares each entry's `prev_hash` against
   the prior entry's `hash` (a linkage check) and, per the export
   path, the chain carries a single `last_hash` — so a same-shaped
   details/op rewrite is at least as undetectable there.

The Group should either consolidate on one mechanism or explicitly
scope each. Two chains with different guarantee levels is worse than
one weaker chain: an integrator cannot tell which guarantee applies.

### 5.2 Neither chain persists

Both mechanisms are **memory-only** — no persistence writer touches
`_audit_hash_chain` or `_audit_chains` (grepped the backend's
serialization/persistence paths). Consequences:

- A manager restart (or container termination) **destroys the audit
  record** — the tamper-evident property protects only history that
  survives, and none of it survives.
- Worse for the threat model: the tamperer's cheapest attack is not
  rewriting entries, it is **restarting the process** (or terminating
  the container) with a fresh, clean chain.

ADR-0018 §"Alternatives Considered" explicitly rejected an
external/remote log to stay offline-first, and the decision text
contemplates a "single-device local audit log" — but it does not
address persistence, and the implementation read "local, in-memory"
as the whole requirement. **The Group should decide the persistence
requirement**: e.g. append-only JSONL file under the container's state
dir (chain-verified on load, per NPS-023's measured-boot posture),
which stays offline-first and makes the restart attack detectable
rather than free.

## 6. Sign-off checklist

- [x] Status discrepancy resolved (§2) — reconciled to Accepted across ADR, index, and REPOSITORY_STATE (2026-09-19)
- [x] Benchmark record accepted (§3); "negligible" premise closed as measured (2026-09-19)
- [x] Tamper-scope decision made: (b) fix DIRECTED (2026-09-19) — landed via `audit-b1-hardening` merge, scheme-2 hashing, 22.5 µs/event
- [x] Dual-mechanism question resolved (§5.1): CONSOLIDATED — both families behind the scheme-2 hasher (2026-09-19)
- [x] Persistence requirement decided (§5.2): opt-in JSONL snapshot persistence REQUIRED for the daemon path (state-file dir), off by default elsewhere — the restart attack is now detectable (2026-09-19)
- [x] ADR-0018 → Accepted (final); NPS-010 §8.1 consistent — the scheme-2 verifier is the tamper-evident mechanism §8.1 requires (2026-09-19)

## 7. Draft sign-off request (for the Architecture Group agenda)

> **Subject: Review request — ADR-0018 (hash-chained audit log)
> close-out; tamper-scope decision; two structural caveats**
>
> ADR-0018's review package is complete
> (`docs/reference/adr/ADR-0018-review-package.md`, this document).
> The benchmark gate (NPC-002 §5.2) closed 2026-09-18: the mechanism
> costs ~6.4 µs/event to append (≈100k events/s sustained), verifies
> O(n) at a stable ~3.5 µs/event, and taxes the operations it audits
> by 2–8% — the "negligible" premise is measured fact
> (BENCHMARK_RESULTS §34).
>
> Four decisions are requested:
>
> **1. Status** (§2): the ADR document says Accepted; every index says
> Proposed. Reconcile in whichever direction the Group intends.
>
> **2. Tamper scope** (§4): the shipped hash covers op/timestamp/
> prev_hash but NOT the details payload — rewriting which capability
> was granted, to whom, is undetectable by the shipped verifier
> (demonstrated empirically). The fix (hash canonical details JSON)
> costs ~4× the hash work per event — prototyped end-to-end at
> ~19 µs/event with the full suite passing (see §4.2) — and is
> recommended.
>
> **3. Dual mechanisms** (§5.1): a second chain family
> (`create_audit_chain`/`verify_audit_chain`) exists in the same file
> with weaker guarantees. Consolidate or scope explicitly.
>
> **4. Persistence** (§5.2): both chains are memory-only; a process
> restart destroys the record — making restart the cheapest tamper.
> The Group should set a persistence requirement (offline-first
> append-only local file, verified on load) or amend the ADR to scope
> the guarantee to a single manager lifetime.## 8. Appendix — pre-drafted ADR-0018 v2.0.0 (merge-ready if §4/§5 direct the fix)

Prepared 2026-09-18 so a "direct the fix" decision can be applied in
one pass without a drafting round. The prototype behind these numbers
and diff shapes was built, measured (19.0 µs/event append, suite 2532
OK, mutation table all-detected), and reverted — nothing below is
untested intent. If the Group instead chooses §4.3(a) (honest
re-scoping), this appendix is discarded and only the status
reconciliation (§2) applies.

**Amendment text (would become ADR-0018 v2.0.0, `updated:
2026-09-XX`):**

> ## Decision (v2.0.0 amendments)
>
> **1. Payload coverage (closes the §34e gap).** Each event's hash
> covers a canonical serialization of its `details` payload:
> `sha256(salt ‖ prev_hash ‖ op ‖ timestamp ‖ json(details))` with
> `json()` = UTF-8 `json.dumps(details, sort_keys=True,
> separators=(',',':'))`. Events carry a `scheme` marker (2); events
> without it (and events appended with `details=None`) verify under
> the v1 op+timestamp rule, so chains created before this amendment
> remain verifiable (mixed chains verified in the prototype). `None`
> and `{}` are distinct: `None` hashes the v1 form, `{}` hashes the
> v2 form with `'{}'` — append and verify must agree on this
> exactly.
>
> **2. Persistence (closes the restart attack).** Each container's
> chain MUST be appended to an append-only JSONL file under the
> container's state directory at or before the durability point of
> the operation the event records; the chain MUST be re-verified on
> load and a verification failure MUST fail closed (the affected
> container's capability grants are suspended pending operator
> review, per NPS-010 §4.2's fail-closed posture). The file is the
> canonical record; the in-memory chain is a cache. This keeps the
> offline-first property (ADR-0018's original constraint) while
> making process restart survivable and restart-with-rewrite
> detectable.
>
> **3. Single mechanism.** The `create_audit_chain` /
> `append_audit_entry` / `verify_audit_chain` family is deprecated
> and MUST be removed or consolidated onto the
> `initialize_audit_integrity` path in the same change that ships
> this amendment — two chain families with different guarantee
> levels must not coexist (NPS-018 §8's no-duplication rule applied
> to mechanisms).
>
> ## Consequences (additions)
>
> - Measured cost of payload coverage: append 19.0 µs/event with
>   details (6.9 µs with `details=None`), verify 10.1 µs/event,
>   O(n) preserved at 110k events — unchanged conclusions from the
>   §34 record ("negligible" holds).
> - Verification now covers what an attacker actually wants to
>   rewrite (which capability was granted, to whom) — closing the
>   gap demonstrated in `tests/BENCHMARK_RESULTS.md` §34e.
> - The persistence requirement gives the restart attack (§5.2 of
>   the review package) a detection path instead of a free pass.
>
> ## Status
>
> Accepted (v2.0.0, 2026-09-XX, Architecture Group) — supersedes the
> v1.0.0 decision; the v1 scheme remains verifiable for existing
> chains.

**Implementation checklist if directed (one change, three files):**

1. `backend/container.py`: scheme constant + `_audit_event_content`
   + scheme-marker append + dual-rule verify (the prototype diff);
   plus JSONL persistence writer on the event path and
   verify-on-load in the manager's container-restore path.
2. `test_backend.py` (`TestAuditIntegrity`): pin the §34e mutation
   table to all-detected; pin mixed-chain verification; pin the
   None/{} distinction; add a persistence round-trip (append →
   restart → verify → tamper-file → load fails closed).
3. `tests/BENCHMARK_RESULTS.md` §34: replace the "prototype" note
   with the shipped-scheme numbers (re-run `benchmark_adr0018.py`).
4. Index/README/REPOSITORY_STATE status flips (the §2 reconciliation,
   applied for real this time).

---
**End of Document**
