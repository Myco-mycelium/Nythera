---
title: AG Review Input — BUILD-001 vs BUILD-ARCH (the build-architecture duality)
document_id: AG-BRIEF-2026-09-BUILDARCH
version: 1.0.1
status: Suggest-side — awaiting the Architecture Group decision (registered AG_AGENDA v1.8.0, 2026-09-23); v1.0.1 corrects the Python-floor row after the shipped-metadata check (pyproject >=3.10; CI runs 3.11+3.12) and adds the verified merge kit
owners: [Nyrqis Architecture]
created: 2026-09-23
ai_assisted: true
depends_on: [AG-AGENDA-2026-09, NPC-007, NPC-003, BUILD-001, BUILD-ARCH]
---

# AG Review Input — BUILD-001 vs BUILD-ARCH

**What this document is:** the suggest-side pre-read for the
build-architecture duality registered as the agenda's second standing
item (`AG_AGENDA.md` v1.8.0). It verifies the two documents' content
(where they agree, where they contradict), lays out the disposition
options with a consequences ledger, and makes one recommendation. It
follows the format of the D3/D4 pre-reads (`AG_BRIEF_NPS026_KEY_TRUST.md`,
`AG_BRIEF_NPS026_CRYPTO_SCHEME.md`).

**What this document is not:** a decision. NPC-001 §11.1 stands — every
recommendation here is overridable Group judgment, and the reviewer is
expected to attack it.

---

## 1. The question

The build-architecture deliverable (M11 gap category 9) exists **twice,
under two different document_ids**, with materially different bodies:

| | `docs/reference/build/BUILD_ARCHITECTURE.md` | `docs/00-platform/BUILD_ARCHITECTURE.md` |
|---|---|---|
| document_id | **BUILD-001** | **BUILD-ARCH** |
| status | **Draft** | **Accepted** |
| created | 2026-09-06 | 2026-09-01 |
| depends_on | ADR-0020, NPC-003 | ADR-0012, ADR-0020, NPS-017 |
| gap-9 claim | closing line | front-matter `satisfies:` |
| cited by | roadmap item 9, TUT-003 `depends_on`, `sdk/README.md`, `sdk/nyrqis_sdk/cli.py` | nothing in the tree |

The Group must pick the canonical ID, dispose of the other copy, and
decide whether the Accepted marking has (or needs) a decision record.

## 2. What the two documents actually contain (verified 2026-09-23)

The bodies are not duplicates that drifted — they have **different
centers of gravity**:

- **BUILD-001** (Draft, 235 lines) reads as *policy*: toolchain
  selection per ADR-0020's language matrix (Rust/C++/C/Python roles),
  cross-compilation targets, reproducible-build **MUSTs** (bit-for-bit
  determinism, hermetic builds, pinned lockfiles, a double-build
  hash-compare verification recipe), CI stages with explicit timeout
  budgets, and an artifact-signing section. It is the copy whose shape
  matches the roadmap's gap-9 wording ("toolchain, build graph,
  cross-compilation, reproducible builds, CI stages, artifact
  signing") — every listed concern has a section.
- **BUILD-ARCH** (Accepted, 308 lines) reads as a *practical reference*
  for the Linux Backend as built: per-crate toolchain tables (rustc ≥
  1.75, system-library packages), an ASCII crate-dependency graph, a
  per-crate ABI-version/output/test-count table, and copy-paste build
  commands (dev, release, cross) keyed to `source/nyhal-linux-backend/rust/`.
  Its `satisfies: [NPC-007 gap 9]` front-matter claims the same gap.

**Where they contradict (selected, verbatim-checked):**

| Topic | BUILD-001 | BUILD-ARCH |
|---|---|---|
| Python floor | `python3.12+` | `python3 ≥ 3.10.0` |
| Virtual environments | "no virtual environments for production" | documents venv as built-in support |
| Windows target | `x86_64-pc-windows-msvc` (translation layer, future) | absent from the target table |
| Crate inventory | prose list, no versions | 18-crate table with ABI versions + test counts (2026-09-01 vintage — per-crate counts no longer match the tree: grep-level `#[test]` total is **305 today vs the table's 121**; compositor alone is 67 vs its table row of 8) |

**Correction (v1.0.1 — the evidence pass caught this brief's own first
draft):** on the Python floor, the shipped metadata sides with
**BUILD-ARCH**: `source/nyhal-linux-backend/pyproject.toml` declares
`requires-python = ">=3.10"`, and CI itself runs **3.11 and 3.12**
(`ci.yml` lines 102/111/637/664) — so BUILD-001's `python3.12+` is
wrong as a *floor* (3.11 is exercised in CI right now), while
BUILD-ARCH's `≥ 3.10.0` matches `pyproject.toml` verbatim. The venv
row downgrades to reconcilable: BUILD-001 states a *production*
posture; BUILD-ARCH's table is a *development toolchain* listing —
not a real conflict. Lesson recorded: a pre-read's contradiction
table is itself a claim set and must be verified against the tree
like any other — the first draft asserted the opposite on its
strongest-looking row.

Neither document cites the other. A reader following links from the
sdk or TUT-003 lands on BUILD-001 and never learns BUILD-ARCH exists;
a reader browsing `docs/00-platform/` gets the opposite experience —
with a status of **Accepted** that nothing in the governance record
explains.

## 3. The Accepted marking (the secondary question)

No Architecture Group decision record was found for BUILD-ARCH's
`status: Accepted`. This matches the **unsanctioned-flip class** the
project has already adjudicated twice:

- ADR-0019: a 2026-09-06 edit flipped it to Accepted with no Group
  record; REPOSITORY_STATE line 929-era audit caught it and the
  front-matter was **reverted** 2026-09-20, with the index carrying
  the correction.
- ADR-0022/0023: flipped 2026-09-06, but the Group then **ratified
  as-implemented** on 2026-09-19 — sanctioning retroactively.

So there are two established remedies: revert to Draft, or obtain a
retroactive Group record. The same choice applies here, *conditioned on
which document_id survives as canonical*.

## 4. Options and consequences

**Option A (recommended): BUILD-001 canonical; absorb BUILD-ARCH's
practical content; remove the BUILD-ARCH copy.**

| Consequence | Assessment |
|---|---|
| Zero citation breakage | the roadmap, TUT-003, and the sdk already cite BUILD-001 |
| The practical reference (crate graph, per-crate tables, build commands) is real value that should not be lost | fold it into BUILD-001 as new sections (verified against the tree at merge time — the stale test counts are corrected by the act of merging) |
| The contradictions resolve in one direction — but NOT the one v1.0.0 drafted | the shipped metadata (corrected above) means the merge must **correct BUILD-001's toolchain table from `pyproject.toml`** (`>=3.10`, CI 3.11/3.12), not absorb BUILD-ARCH's rows wholesale; the practical reference proved the more current source on the floor |
| The Accepted marking disappears with the file | no retroactive record needed for a deleted document; if the merged content deserves Accepted, the Group grants it to BUILD-001 forward, on its own evidence |
| Cost | one merge pass + one file deletion; the `build-architecture-dual-doc` premise pin is updated to assert the duality is *resolved* (single document_id) |

**Option B: BUILD-ARCH canonical (it claims Accepted and is the more
detailed reference); demote or delete BUILD-001.**

| Consequence | Assessment |
|---|---|
| The largest body survives as-is | but it is the copy nothing cites — four surfaces must be re-pointed (roadmap, TUT-003, sdk ×2) |
| Accepted needs a retroactive record | same unsanctioned-flip remedy as ADR-0022/0023 — the Group would have to ratify a document it has never reviewed, whose test counts are stale |
| The policy prose (reproducibility MUSTs, CI budgets, signing) risks being lost unless also merged | the merge cost of Option A appears here anyway |
| The 3.10/venv toolchain text becomes normative-by-default | contradicts ADR-0020's matrix as cited elsewhere; would need amendment first |

**Option C: keep both, one ID, split scopes** (e.g. BUILD-001 = policy
in `docs/reference/build/`, BUILD-ARCH renamed to a backend-local
reference doc under `source/nyhal-linux-backend/`).

| Consequence | Assessment |
|---|---|
| No content is discarded and the citation graph heals | but two documents on one topic is exactly the drift class this finding records; each future toolchain change must land twice (the two-tree lesson, applied to prose) |
| The gap-9 double-claim is resolved only by renaming scope | the Group must then also re-word the roadmap item to cover two deliverables |
| Lowest immediate effort, highest permanent cost | — |

## 5. Recommendation

**Option A.** It is the only option with zero citation breakage, it
resolves the contradictions in the direction ADR-0020 already points,
it converts the merge into the mechanism that refreshes the stale
practical content, and it dissolves the Accepted-marking question
rather than requiring a retroactive record for an unreviewed document.
The merged BUILD-001 should remain Draft and earn Accepted through the
same review path as its sibling reference docs (PERF-001 et al.).

**Suggested Group record (if A is accepted):** one decision-log row —
"BUILD-001 canonical; BUILD-ARCH's practical sections merged and the
copy removed; no decision record existed for BUILD-ARCH's Accepted
marking; the marking is dissolved with the file" — plus the premise-pin
update.

## 6. The merge kit (verified 2026-09-23 — ready only if Option A is accepted)

Not executed here: the merge is the Group's decision to unleash, and
pre-empting it would repeat the unsanctioned-flip class on the other
side. What this section adds is the verification work the merge will
need, done once so the decision session can size the whole item:

- **The per-crate test table, corrected to today** (grep-level
  `#[test]` counts, 2026-09-23): compositor 67, wayland 27, nyui 26,
  ipcd 24, egl 16, seccomp 15, container 14, nyruntime 14, syscalls
  14, gbm 15, drm 8, keys 8, nycore 8, launcher 10, nyfs 10, ipc 11,
  transport 5, vulkan 13 — total **305** vs the 2026-09-01 table's
  121. Exact counts belong to the merge pass (the table should cite
  the contract suite, not a grep).
- **The toolchain rows to correct in BUILD-001**: Python floor
  `>=3.10` per `pyproject.toml` (CI exercises 3.11 + 3.12); the
  no-venv production posture stays as written — it is policy, not a
  contradiction.
- **A host-hygiene find the practical doc's own commands exposed:** a
  literal `*` directory exists at `source/nyhal-linux-backend/rust/*`
  (empty, untracked, created 2026-08-16 — a failed shell expansion).
  BUILD-ARCH's documented `for crate in */` build loops would descend
  into it and abort at `cargo build` — the practical reference's
  commands fail on this host today. The merge pass must re-verify
  every documented command; the directory is removed in this commit.

## 7. Downstream touch points if the decision lands

Whichever option the Group picks, these follow automatically and are
listed so the decision session can size the work: the roadmap's M11
item 9 text (it currently names the finding), the spec index v1.29.0
duality row, TUT-003's `depends_on` (only if B), the sdk's two
mentions (only if B), the `build-architecture-dual-doc` premise pin in
`tools/check_doc_premises.py`, and `NEXT_SESSION_PLAN.md`'s session
item. None blocks the decision itself.

## 8. What the reviewer is asked to decide

1. Canonical document_id (the options ledger above).
2. The other copy's disposition (merge-and-remove / re-point / split).
3. Whether the Accepted marking ever had a sanction, and the remedy if
   not (revert vs ratify — the ADR-0019 vs ADR-0022 precedents).
4. Whether the merged document proceeds as Draft toward the standard
   reference-doc review path.

No measurement, implementation, or drafting item stands ahead of the
decision; Option A's merge is the only follow-up work item and it is
bounded to one file pair.
