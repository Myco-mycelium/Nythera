# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nyrqis repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-08-13

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus an externally-contributed,
independently-verified Linux Backend implementation
(`source/nyhal-linux-backend/`, 64/64 tests passing). Milestone 12 — the
phased security threat model — is **complete**: Phases 1–7 are done
(`NPS-018` methodology, `NPS-019` attack surface enumeration, `NPS-020`
STRIDE analysis, `NPS-021` privilege/escalation analysis, `NPS-022`
container escape analysis, `NPS-023` secure boot, `NPS-024` AI,
`NPS-027` package trust). Phase 4
found the most severe issue in the threat model to date (capability
enforcement covers IPC only, not direct syscalls); Phase 6 found the
suggest-vs-act boundary NPC-001 §11.1 depends on has no requirement that
its confirmation UI actually be unspoofable — meaning even a
perfectly-implemented assistant following the spec as written wouldn't
have closed the gap. Across all seven phases, every finding recorded has
a disposition — no bare observations left dangling. Phase 7 (Package
Trust Model, `NPS-027`) landed 2026-08-12, completing the threat model
(see `docs/reference/security/README.md`). A docs-backlog pass
(2026-08-12)
started Milestone 11's gap categories: the Object Registry (NPS-025),
Public API (API-001), ABI (ABI-001), and Package Format (NPS-026,
including the digital-signature design closing `FIND-PACKAGE-001`) now
exist as `Draft` documents; first Tutorials and How-To guides are
published; and every stale category/reference index placeholder has been
replaced with a real index. Still remaining from Milestone 11's
prioritized backlog: governance expansion, build architecture docs,
performance budgets, and developer onboarding — see
`007-PROJECT_ROADMAP.md`.

## Governance Documents

- [x] NTM-000 The Nyrqis Manifest — Accepted
- [x] NPC-001 Project Constitution — Accepted
- [x] NPC-002 AI Collaboration Protocol — Accepted
- [x] NPC-003 Engineering Handbook — Accepted
- [x] NPC-004 Specification Index — Draft
- [x] NPC-005 ADR Index — Draft
- [x] NPC-006 Glossary — Draft
- [x] NPC-007 Project Roadmap — Draft
- [x] NPC-008 Subsystem Owners — Draft (all subsystems currently Unassigned)
- [x] NPC-009 Requirements Database — Draft (in response to external review feedback)

## Architecture Decision Records
11 accepted, 8 held (4 named benchmark blockers plus 4 decisions pending
Architecture Group sign-off, not benchmark-blocked), 1 rejected.

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Accepted
- [x] ADR-0003 Game disk images with overlay — Accepted
- [x] ADR-0004 Containerized execution model — Accepted
- [x] ADR-0005 Windows compatibility translation layer — Accepted
- [x] ADR-0006 Hybrid microkernel as kernel base — Accepted
- [ ] ADR-0007 Zstandard as default compression codec — **Proposed**, first-pass level-sweep data collected (2026-08-12, `tests/BENCHMARK_RESULTS.md` §2) plus an end-to-end NyFS compression ratio of 6.42 : 1 on a synthetic corpus (2026-08-12, §7, `--nyfs-persist`); default-level decision pending Architecture Group review
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Accepted
- [ ] ADR-0009 Per-container token-bucket IPC rate limiting — **Proposed**, first-pass bucket-parameter data collected (2026-08-12, `tests/BENCHMARK_RESULTS.md`); parameter sweep + Architecture Group review pending
- [x] ADR-0010 Vulkan as native graphics API foundation — Accepted
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Accepted
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Accepted
- [ ] ADR-0013 EEVDF-derived scheduler with real-time priority class — **Proposed**, algorithm family decided, tuning parameters blocked on benchmark data
- [ ] ADR-0014 UEFI Secure Boot with user-enrollable keys — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0015 Shared dynamic binary translation for ARM/x86 — **Proposed**, approach decided; performance validation blocked on benchmark data
- [ ] ADR-0016 NyFS Linux Backend as user-space FUSE filesystem — **Proposed**, initial strategy decided; kernel-module fallback blocked on FUSE-overhead benchmark data
- [x] ADR-0017 Reject domain-grouped NPS renumbering — **Rejected** (the project's first; considered and explicitly declined, not left unresolved)
- [ ] ADR-0018 Hash-chained append-only log for capability audit records — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0019 Journal commit as the default NyFS save() mode — **Proposed**, review package for the 2026-08-12 implementer default flip; daemon lifecycle design note (`source/nyhal-linux-backend/DAEMON_LIFECYCLE.md`) answers its open question 1, AG tuning review pending
- [x] ADR-0020 Implementation languages and the platform boundary — **Accepted** (v2.0.0, 2026-08-13), canonical language matrix (Rust/C++/C platform languages; NyHAL resolved Rust-first) + platform-boundary principle: platform-critical execution paths must not depend on the Python interpreter; supersedes v1 (Python + Rust, 2026-08-12); Architecture Group acceptance recorded in issue #2 (closing the issue itself is a manual step — the PAT cannot comment/close issues)

## Specifications (NPS)
13 accepted, 14 held (4 named benchmark/dependency blockers, plus
NPS-018..NPS-024 and NPS-027 — threat-model phase documents, Draft
pending Architecture Group sign-off — and NPS-025, NPS-026 — Draft
documents from the 2026-08-12 Milestone 11 backlog pass).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted (v1.2.0: GPU command buffer validation + submission timeout added, closing threat model findings FIND-KERNEL-001/003)
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking); v1.1.0 added a shared-memory zeroing requirement, closing threat model finding FIND-CONTAINER-003; v1.2.0 recorded first-pass in-process latency (p50 92 µs vs <100 µs target — tail exceeds); the real Unix-domain datagram transport shipped 2026-08-14 and its over-transport measurement landed the same day (BENCHMARK_RESULTS.md §20: p50 188.79 µs / p95 295.23 µs / p99 373.51 µs) — the §6.1 gate is NOT met at the median, so NPS-003 stays Draft with the ADR-0020 Rust transport as the documented close path
- [x] NPS-004 NyFS Filesystem Core — Accepted
- [ ] NPS-005 Transparent Compression Policy — **Draft**, transitively blocked on ADR-0007 (defines default levels tied to the still-Proposed codec ADR)
- [x] NPS-006 Nyrqis Game/Application Image Format (.nygi) and Overlay — Accepted
- [x] NPS-007 Windows Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-008 Android Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-009 Adaptive UI Shell — Accepted (VR resolved: explicitly deferred to a future milestone, not an open mode definition)
- [ ] NPS-010 Container Runtime — **Draft**, transitively blocked on ADR-0009 (§7.1 normatively requires its still-Proposed rate-limiting mechanism); v1.1.0 added atomic grant-check (§4.2) and tamper-evident audit log requirement (§8.1, per new ADR-0018), closing threat model findings FIND-CAPABILITY-001/002
- [x] NPS-011 Capability Registry — Accepted (27 capabilities registered: 25 through Milestone 10, minus 1 split into 3 this pass — `CAP-MEDIA-LIBRARY` → `CAP-MEDIA-IMAGES`/`CAP-MEDIA-VIDEO`/`CAP-MEDIA-AUDIO`, closing threat model finding FIND-CAPABILITY-004; still intentionally incomplete by design)
- [x] NPS-012 Controller and Input Subsystem — Accepted (VR capability formally deferred, not left ambiguous — §5.1)
- [x] NPS-013 GPU Feature Support — Accepted (§7.3 documents current FSR/XeSS/FSR4 vendor SDK status, verified 2026-07-13)
- [x] NPS-014 Emulator Hub — Accepted
- [x] NPS-015 Local AI Assistant — Accepted (v1.1.0: four amendments closing threat model Phase 6 findings — unspoofable confirmation UI, corrected file-search capability, persistence-mechanism exclusion, suggestion audit log)
- [x] NPS-016 Optional Cloud Synchronization — Accepted
- [x] NPS-017 NyHAL — Kernel Abstraction Layer and Backend Contract — Accepted
- [x] NPS-018 Threat Model Methodology and Trust Boundaries — Draft (Threat Model Phase 1a)
- [x] NPS-019 Attack Surface Enumeration — Draft (Threat Model Phase 1b, 24 surfaces catalogued)
- [x] NPS-020 STRIDE Analysis per Trust Boundary — Draft (Threat Model Phase 2, 10 boundaries, 3 findings drove real spec amendments this pass)
- [x] NPS-021 Privilege Boundaries and Capability Escalation Analysis — Draft (Threat Model Phase 3, 5 findings — 4 resolved, 1 governance-level recorded not technically fixed)
- [x] NPS-022 Container Escape Analysis and Runtime Isolation — Draft (Threat Model Phase 4, grounded in the real Linux Backend code; found capability enforcement covers only IPC send/call, not direct syscalls — the most severe finding to date, flagged as the implementation's top priority)
- [x] NPS-023 Secure Boot Threat Model — Draft (Threat Model Phase 5, first full pass on TB-BOOT; found zero Secure Boot status visibility on the Linux Backend and unvalidated boot-phase transitions; a measured-boot/TPM gap logged as not fixable by amendment)
- [x] NPS-024 AI Threat Model — Draft (Threat Model Phase 6, first full pass on TB-AI, no implementation exists yet; found the suggest-vs-act boundary's confirmation UI isn't required to be unspoofable — the most conceptually significant finding since Phase 4's capability-enforcement gap)
- [x] NPS-025 Object Registry — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 2; 14 object types catalogued, Identity flagged pending its own NPS)
- [x] NPS-026 Package Format (.nypkg) — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 7 and FIND-PACKAGE-001; signed manifests + integrity trees proposed, concrete crypto scheme pending dedicated human review per NPC-002 §6.2)
- [x] NPS-027 Package Trust Model — Draft (Threat Model Phase 7, 2026-08-12, completing Milestone 12; disposition of FIND-PACKAGE-001 plus 4 new findings closed via NPS-006 §6 amendment and REQ-SEC-0003..0006)

## Requirements Database
NPC-009 (Draft) + seed ledger at `docs/reference/requirements/REQUIREMENTS.md`:
40 requirements across all 17 domain prefixes. Nearly all traced to
`Accepted` specs; two (`REQ-IPC-0003`, `REQ-IPC-0004`) trace to
still-`Draft` NPS-003, called out explicitly rather than silently
overstating coverage quality. One entry (`REQ-NYHAL-0003`) marked
`Implemented (partial)`, referencing the `nyctr` PoC with an explicit
caveat about what it doesn't cover. Not full coverage of NPS-001..021 by
design (NPC-009 §7.3) — expand incrementally, and going forward new
normative additions should cite a
REQ ID from the start (NPC-009 §7.2).

## ABI / API References
Draft: [`API-001`](../reference/api/API-001-public-api.md) (Public API —
areas, layering, naming/versioning conventions, error model; exact
signatures deferred to implementation) and [`ABI-001`](../reference/abi/ABI-001-binary-compatibility.md)
(Binary Compatibility — compatibility rules, IPC wire format per NPS-003
§9's deferral, symbol/plugin/driver/runtime/backend ABIs).

## Package Format
Draft: [`NPS-026`](../reference/package-format/NPS-026-package-format.md)
(.nypkg — signed manifest, integrity trees, compression, delta updates,
streaming install, rollback, dependencies). This is the package-format
NPS deferred by NPS-006 §2/§9, and the response to `FIND-PACKAGE-001`
(checksums alone don't establish publisher authenticity).

## Object Registry
Draft: [`NPS-025`](../reference/object-registry/NPS-025-object-registry.md)
— every object type (Workspace, Window, Application, Package,
Capability, Game, Mod, Controller, GPU, Notification, AI Conversation,
Device, Service; Identity flagged pending its own NPS) with fields,
lifecycle, permissions, serialization rules, and relationships.

## Source Code
Two things now, not one:

- `source/nyhal-linux-backend/poc-container/` (`nyctr.py`) — the original
  spike: proves the most basic container primitive (PID/mount/UTS/user
  namespace isolation + a cgroup memory/pid limit) works on stock Linux.
  Superseded in scope by the item below but kept as the minimal reference
  it was designed to be.

- `source/nyhal-linux-backend/` — a substantially fuller Linux Backend
  implementation (`backend/container.py`, `backend/capability.py`,
  `backend/seccomp.py`, `backend/launcher.py`, `ipc/core.py`,
  `fuse/nyfs.py`, `boot/lifecycle.py`), contributed
  externally (not authored in this session — merged from the remote after
  a `git push` conflict surfaced it) and **independently verified before
  being documented here**: `python3 test_backend.py` passes
  54/54. Real cgroup v1/v2 detection and namespace usage confirmed by
  reading the code, not assumed from its own claims.

  Its own `IMPLEMENTATION_STATUS.md` (`document_id: IMPL-001`, v0.2.0)
  self-rates as **"Experimental Backend — Core Implementation Complete,
  Performance/Integration Work Pending,"** explicitly **not yet
  conformant** to NPS-017 §5: data-plane enforcement exists via an
  in-container seccomp-BPF filter (default-allow deny model; a
  default-deny allowlist posture is the strictly-stronger follow-up),
  `openat2` write-intent is not flag-filterable from classic BPF
  (documented residual gap), LSM integration is deferred, and no IPC
  latency, FUSE overhead, or compression benchmarks exist. That
  self-assessment reads as accurate against the code, not inflated —
  consistent with this project's existing discipline.

  **Reconciled with Phases 4 and 5 of the threat model** (`NPS-022`,
  `NPS-023`; the findings were recorded this session, and the fixes
  landed this session too):
  - `FIND-BACKEND-002` (the most severe finding to date — capability
    enforcement covered only IPC `send`/`call`, leaving direct syscalls
    unmediated) is **closed** by `backend/seccomp.py` +
    `backend/launcher.py`: capability sets compile to a cBPF filter
    installed inside the container before its command runs. Verified
    end-to-end on this host — a read-only container's write-capable
    `openat` is refused with `EPERM` at the syscall level.
  - `FIND-BACKEND-003` (cgroup v1 `release_agent` exposure) is **closed**
    by `notify_on_release=0` on the container's v1 cgroups plus
    best-effort unmount of leaking cgroup mounts in the launcher.
  - `FIND-BACKEND-004` (shell interpolation of container-supplied
    strings) is **closed** by the shell-free launcher: hostnames and
    commands are argv entries, and `sethostname(2)` is called directly.
  - `FIND-BOOT-001` (zero Secure Boot status visibility) is **closed** by
    `boot/lifecycle.py`'s efivars + mokutil probing (`secure-boot-status`).
  - `FIND-BOOT-002` (unvalidated boot-phase transitions) is **closed** by
    legal-transition validation in `boot/lifecycle.py`.
  - `FIND-CAPABILITY-004` (capability granularity mismatch) was already
    closed at the spec level by splitting `CAP-MEDIA-LIBRARY` into
    images/video/audio; the backend's `Capability` enum now reflects it.
  - IPC `receive` now checks the receiver holds `CAP_IPC_RECEIVE`
    (control-plane enforcement widened beyond `send`/`call`).
  - FUSE is no longer structural-only: `fuse/nyfs.py` gained a path API,
    full operation handlers, and `fusepy` mount wiring (ADR-0016).

  Since 2026-08-12, `backend/seccomp.py` also carries the **ADR-0020 FFI
  loader** for the first Rust migration: it locates the Rust seccomp
  cdylib (`$NYRQIS_RUST_LIB` → crate `target/release/` →
  `LD_LIBRARY_PATH`), ABI-version checks it, and routes
  `build_program`/`validate_program`/`simulate` through the FFI, falling
  back to pure Python on any failure. `NYRQIS_RUST_FORCE=1` turns
  failures into errors — the conformance gate CI watches.

## Build System
Started 2026-08-12. CI (`.github/workflows/ci.yml`) runs on every push/PR
and is the first place the Rust crate compiles (the dev host has no Rust
toolchain):

- `rust-seccomp` — builds and tests the ADR-0020 first-migration crate
  (`source/nyhal-linux-backend/rust/seccomp`, cargo build --release +
  cargo test).
- `rust-seccomp-conformance` — **non-blocking** gate that forces the
  full Python test suite through the Rust module via the FFI loader
  (`NYRQIS_RUST_FORCE=1`); it fails while the crate is scaffold-only and
  turns green automatically when the port lands.
- `backend` — the pure-Python test suite (`python3 -B test_backend.py`),
  the correctness floor.

`docs.yml` (docs site build + GitHub Pages deploy) remains separate.

## Documentation Site
Structure created; MkDocs Material configured with full nav (zero warnings
under `mkdocs build --strict`); CI workflow (`.github/workflows/docs.yml`)
builds and deploys to GitHub Pages on push to `main`. Version pinned via
`requirements-docs.txt` due to MkDocs Material's own public warning about
breaking, currently-unsuitable-for-production changes in MkDocs 2.0.

## Next Actions
Benchmark-gated (unblocks the 3 ADRs + 4 NPS documents held above).
First-pass data for four of the seven items landed 2026-08-12
(`tests/BENCHMARK_RESULTS.md`); the remaining items still have no
measurements:
1. ~~Benchmark IPC round-trip latency (unblocks NPS-003, transitively
   NPS-010's remaining path once ADR-0009 also clears).~~ **First-pass
   data collected 2026-08-12** — p50 92 µs / p95 157 µs / p99 213 µs,
   in-process only; the over-transport measurement landed 2026-08-14
   (BENCHMARK_RESULTS.md §20): p50 188.79 µs / p95 295.23 µs / p99
   373.51 µs vs 87.28 µs in-process — §6.1's <100 µs gate is NOT met
   at the median over the real transport. The Rust transport hot path
   (ADR-0020 migration #6, rust/transport) shipped the same day as the
   documented close path. **Delta measured 2026-08-14 (same-session
   A/B, BENCHMARK_RESULTS.md §20): the current Rust FFI surface is
   SLOWER than the floor at the median (p50 ~426 µs Rust vs ~231 µs
   floor; +23 µs per raw round trip isolated — per-recv malloc of the
   wire + sender-path buffers and per-message copies). The migration
   stands on the boundary rule + the byte-identical conformance gate;
   the §6.1 performance close is a second-pass FFI surface
   (caller-supplied/pooled buffers). NPS-003 stays Draft, gate open.**
2. ~~Benchmark default IPC token-bucket parameters (unblocks ADR-0009,
   then NPS-010 §7.1).~~ **First-pass data collected 2026-08-12** — the
   default bucket (100 burst, 50/s refill) sustains only ~99.5 calls/s
   on a client→endpoint path and throttles ~18.9k calls/s at full speed;
   the defaults are demonstrably too low for this workload shape.
   Parameter sweep, adversarial test, and Architecture Group review
   still pending.
3. ~~Benchmark Zstd compression levels, install size vs. load time
   (unblocks ADR-0007, then NPS-005).~~ **First-pass data collected
   2026-08-12** — level sweep on a synthetic corpus (overall ratio 2.54
   at levels 1–5 vs 3.17 at ≥7; compression 0.6–3.5 GB/s); a real asset
   corpus, the LZ4 fast-path comparison, and concurrent-load CPU
   measurement remain, and the default-level decision belongs to
   Architecture Group review.
4. Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning (unblocks ADR-0013 in full; algorithm family is already decided).
5. Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).
6. Benchmark FUSE overhead for NyFS's Linux Backend (ADR-0016;
   determines whether the FUSE decision holds or needs a kernel-module
   fallback). **Proxy data re-run 2026-08-12 after the per-block CoW
   rewrite** (`tests/BENCHMARK_RESULTS.md` §5): streaming 1 MiB-chunk
   writes ~162 MB/s (~4× the old whole-file 40.5 MB/s) vs 541–771 MB/s
   native; small 4 KiB ops are now dominated by per-call block compress
   + per-read SHA-256 verification (~3.6 MB/s write / ~2.8 MB/s read),
   with the checksum-verification read cost recorded as the key finding
   for Architecture Group review. **Live-mount first-pass data
   collected 2026-08-12** (`tests/BENCHMARK_RESULTS.md` §6): this host
   turned out to have fusepy + `/dev/fuse` all along, so the real
   kernel mount was measured — writes ~1.8–2.2 MB/s, bounded by the
   kernel's 4 KiB write batching × 64 KiB CoW blocks (256 requests per
   1 MiB write), reads ~25–37 MB/s (readahead-batched); durability and
   CoW snapshots verified end-to-end through the kernel path
   (`TestNyFSLiveMount`). **The write-batching limit was then fixed
   2026-08-12** by negotiating `FUSE_CAP_BIG_WRITES` +
   `FUSE_CAP_WRITEBACK_CACHE` + `FUSE_CAP_MAX_PAGES` in the INIT
   handshake (`NyFSMount` `writeback_cache=True`, default): writes now
   batch at 128 KiB and stream at ~40–46 MB/s (~25×). No gate declared
   met.
7. Benchmark hash-chain computation/verification overhead before ADR-0018 exits Proposed — expected to be negligible but not asserted as fact without a measurement, per NPC-002 §5.2.

Genuinely still open, not fabricable:
8. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) — requires actual contributors, not something to invent.
9. Choose a real license (`LICENSE` is still the Milestone 1 placeholder — "no rights granted... until a formal license is adopted"). This is a legal/business decision for the repository owner, not one to pick unilaterally on their behalf.
10. ~~Enable GitHub Pages with source "GitHub Actions" (Settings → Pages)
    so `.github/workflows/docs.yml`'s deploy step has somewhere to publish
    to — the workflow runs regardless, but won't be visibly served until
    this is set.~~ **Done 2026-08-12** — Pages is enabled with source
    `GitHub Actions` on `main`; the site is served at
    `https://myco-mycelium.github.io/Nythera/` (the URL will move to
    `.../Nyrqis` when the repository is renamed per `REBRAND_NOTICE.md`).
    The first deploy that ran before Pages was enabled failed only at the
    `actions/deploy-pages` step; the push carrying this status update
    re-triggers the workflow, which should deploy cleanly.
11. Revisit `NPC-008`'s "claim an Unassigned slot without a vote" design once the project has more than one active contributor — `FIND-CAPABILITY-005` (NPS-021 §5.4) flagged this as a soft privilege path, recorded against the governance document rather than given a runtime fix that wouldn't be the right tool for it.
12. Design a measured-boot/TPM attestation story once a concrete need justifies it (`FIND-BOOT-003`, NPS-023 §4) — not fixable by a quick amendment, same category as the package-signing gap.

Implementation now needs to catch up to what the threat model has already
decided at the spec level — none of these are documentation tasks:
- ~~Implement data-plane capability enforcement (seccomp/LSM) in
  `source/nyhal-linux-backend/backend/capability.py` — `FIND-BACKEND-002`
  (NPS-022 §4) found capability tracking exists but enforcement covers
  only IPC send/call, leaving direct syscalls completely unmediated.~~
  **Done this session** — `backend/seccomp.py` + `backend/launcher.py`
  install an in-container cBPF filter; verified end-to-end. Follow-ups:
  default-deny allowlist posture, LSM integration, and the documented
  `openat2` flag-inspection gap.
- ~~Wire the cgroup v1 `release_agent` hardening and shell-interpolation
  hygiene fixes (`NPS-017` §4.1) into `backend/container.py`.~~ **Done
  this session** (`notify_on_release=0`, launcher-level unmount,
  shell-free `sethostname`).
- ~~Implement Secure Boot status reporting (`REQ-BOOT-0004`, `NPS-017`
  §4.5) and boot-phase transition validation (`FIND-BOOT-002`, `NPS-001`
  §5) in `boot/lifecycle.py`.~~ **Done this session**
  (`secure-boot-status` CLI; legal-transition validation).
- ~~Once 13–15 land, correct `IMPLEMENTATION_STATUS.md`'s own conformance
  claims to reflect them rather than leaving it describing the
  pre-fix state.~~ **Done** — `IMPL-001` v0.2.0 (2026-08-12) now
  describes the enforced state and its residual gaps.

Process and tooling:
17. Wire `tools/check_depends_on_cycles.py` into `.github/workflows/docs.yml`
    as a CI step. It found 4 real circular dependencies this pass
    (NPS-001↔ADR-0012, NPS-001↔ADR-0013, NPS-001↔ADR-0014,
    NPS-007/008↔ADR-0015 — each individually reasonable when added, only
    circular together) that had been sitting in already-committed,
    already-pushed documents undetected. Running it by hand caught them
    this time; it should run automatically going forward.
18. Elevate priority on Milestone 11's package-format gap category
    (specifically digital signatures) — Phase 2's `FIND-PACKAGE-001`
    found that `.nygi` integrity currently relies on checksums alone,
    which don't establish publisher authenticity; an attacker can tamper
    with an image and simply recompute a valid checksum. Not fixable by a
    quick amendment; needs a real package-signing/PKI specification.
19. Continue Milestone 11's remaining prioritized backlog
    (`007-PROJECT_ROADMAP.md`) — diagrams, API reference, ABI
    specification, object registry, and package format are now `Draft`
    (2026-08-12 pass); governance expansion, build architecture docs,
    performance budgets, and developer onboarding remain. Each is
    roughly the size of a prior milestone on its own.
20. Continue the threat model (Milestone 12, `docs/reference/security/`):
    Phase 7 (Package Trust Model, extending NPS-006, already well-motivated
    by `FIND-PACKAGE-001`) is the last planned phase.
21. Once an AI assistant implementation begins, build it against the
    amended `NPS-015` from the start — a protected confirmation UI
    (`REQ-AI-0003`), suggestion audit logging via `ADR-0018`'s mechanism
    (`REQ-AI-0004`), and the corrected file-search capability scoping —
    rather than building the naive version and retrofitting these later.

Resolved earlier this session, kept here for a complete record:
- ~~Resolve shared ARM instruction-translation approach~~ — ADR-0015 (shared dynamic binary translation, JIT + hot-path cache).
- ~~Scope VR integration~~ — explicitly deferred to a future milestone (NPS-012 §5.1).
- ~~Evaluate vendor-neutral upscaling integration point~~ — NPS-013 §7.3, grounded in vendor SDK research.
- ~~Decide NyFS's Linux Backend implementation strategy~~ — ADR-0016 (FUSE first, kernel-module fallback open pending benchmark #6).
- ~~Decide secure boot key management~~ — ADR-0014 (UEFI Secure Boot, shim-equivalent chain, user-enrollable keys).
- ~~Configure CI build for the MkDocs Material site~~ — `.github/workflows/docs.yml`, verified locally with `mkdocs build --strict` before committing.
- Expand NPS-011 Android permission mapping — 8 new capabilities added; still intentionally incomplete per NPS-011 §6.

Documentation hygiene, fixed earlier this session:
- A prior review pass (Milestone 9) left several documents with a Markdown
  table-formatting bug: the row recording that milestone's own review
  had gotten separated from its revision-history table by a blank line.
  Affected all 13 `Accepted` NPS documents from that review; fixed and
  verified via `mkdocs build --strict` and a repo-wide grep, now clean.
- `mkdocs.yml`'s `repo_url` was still the bootstrap placeholder; corrected
  to the canonical GitHub repository `Myco-mycelium/Nythera` (the
  repository name is intentionally unchanged by the 2026-08-12 rebrand —
  see `REBRAND_NOTICE.md`).

## Documentation Hygiene Notes *(ongoing)*
- 2026-08-13 (**ADR-0020 migrations #1 and #2 implemented**): the
  `rust/seccomp` crate (policy compiler / validator / simulator) and the
  `rust/syscalls` crate (`sethostname`/`prctl`/`unshare` wrappers;
  `clone` deferred pending the direct-syscall child-entry-point design)
  are implemented, CI-built and unit-tested on every push, and held
  equal to the pure-Python implementations by golden byte-identical
  tests plus a seeded differential test
  (`test_rust_and_python_agree_differentially`). The forced-mode seccomp
  conformance gate (`NYRQIS_RUST_FORCE=1`, CI
  `rust-seccomp-conformance`) is **green and now a required job**;
  `backend/rust_syscalls.py` (the shared syscalls loader with ctypes
  fallback and force mode) is wired into `launcher.set_hostname`. The
  dev host still has no Rust toolchain — CI is the compiler. Test
  suite: 125/125 (113 + 12 new).
- 2026-08-13 (**direct-syscall launcher landed, ADR-0020 priority #2**):
  `container.py` now launches containers by default via direct
  `unshare(2)`/`fork(2)` syscalls (`_spawn_direct`): the manager forks a
  namespace-setup child that performs `unshare(CLONE_NEWUSER)` + root
  uid/gid maps (ids captured BEFORE the unshare — the classic 65534
  map-write failure), `unshare(NEWNS|NEWUTS|NEWIPC)`, `unshare(CLONE_NEWPID)`,
  then forks the container's PID-1 which mounts a hardened procfs via
  the new no-arg `mount_proc` FFI (post-fork, pre-exec — zero Python
  allocation) and execs the launcher. The setup child relays the
  container PID through a pipe and exits with its status (or dies by
  its signal), so `wait()` keeps Popen-compatible semantics. `rust/syscalls`
  is now ABI 1.1.0 (`sethostname`/`prctl`/`unshare`/`mount`/`mount_proc`);
  `unshare(1)` remains an opt-in legacy path (`use_direct_syscalls=False`).
  CI gains the required `rust-syscalls-conformance` gate (syscalls-facing
  test classes forced through the FFI). Verified on this host: hostile
  hostname `evil; rm -rf /` passed verbatim (FIND-BACKEND-004), PID-1 in
  the new PID namespace, seccomp filter active, suspend/resume/terminate
  lifecycle, legacy path still works. Test suite: **185/185 (169 run + 16
  skipped without the Rust crates: the seccomp differential and the
  syscalls/NyFS/IPC conformance classes — all RUN in CI where the
  crates are built)**.
- 2026-08-13 (**ADR-0020 migration #3: NyFS block codec in Rust**):
  `rust/nyfs/` (ABI 1.0.0) ships the storage hot paths — SHA-256
  per-block checksum (NPS-004 §4) and Zstandard compress +
  decompress-with-verify (ADR-0007) — behind the versioned FFI surface.
  `fuse/nyfs_codec.py` is the loader (search order, ABI check,
  hashlib/zstandard fallbacks, `NYRQIS_RUST_FORCE=1`; `-4097` checksum
  mismatch maps to the floor's `ValueError`); `NyFSBlock` routes
  `compute_checksum`/`compress`/`decompress` (now verified on read,
  NPS-004 §4.3) through it. Extraction  boundary set by the §5 benchmark evidence (read-path verification
  dominates NyFS read cost). New CI jobs: `rust-nyfs` (build + unit
  tests) and the required `rust-nyfs-conformance` gate — the
  differential test (Rust ≡ pure-Python floor on checksums,
  roundtrips, and integrity failures) runs forced through the FFI.
- 2026-08-13 (**ADR-0020 migration #4: IPC wire codec in Rust**):
  `rust/ipc/` (ABI 1.0.0, `libc` the only dependency) ships the binary message
  framing a cross-process transport will carry (NPS-003 §3) — a
  canonical length-prefixed format pinned byte-for-byte. `ipc/ipc_codec.py`
  is the loader (ABI gate, `struct` floor, `NYRQIS_RUST_FORCE=1`;
  `-4097` invalid-wire → the floor's `ValueError`), wired as
  `IPCMessage.to_wire()`/`from_wire()`. New CI jobs: `rust-ipc` (build
  + unit tests) and the required `rust-ipc-conformance` gate — Rust ≡
  Python floor, byte-identical wire and field-for-field decode, same
  malformed-input rejection.
- 2026-08-14 (**auto-maintained container sender registry**):
  `ipc/registry.py` (`ContainerIpcRegistry`) is the pid → container_id
  mapping the transport server authenticates against, kept in sync by
  the backend: `ContainerManager(ipc_registry=...)` registers each
  direct-syscall container's host pid at spawn (its command is exec'd
  as PID-1, so `container.pid` IS the kernel-attached sender pid) and
  drops it on terminate/wait. The legacy `unshare(1)` path is
  deliberately untracked (the command runs as a grandchild with a
  different pid; its datagrams fail closed — documented). The
  container→service e2e now runs with the auto-registry end-to-end;
  `TestContainerIpcRegistry` (6 tests) pins the registry and manager
  hooks. Suite 251 → 257 (231 run + 26 skipped).
- 2026-08-14 (**daemon control plane, operator-only over the same
  transport**): the server gains a trusted-uid operator path
  (`trusted_uids`/`host-operator`, container-FIRST resolution so
  daemon-spawned containers are never misattributed); `ServiceRouter`
  dispatches multiple services on one socket (payload `service`
  field, default `status`); `ControlService` (`ipc/control.py`) lets
  the daemon's own user spawn/list/kill containers through the
  daemon's `ContainerManager` — reached via `nyrqis_backend.py
  control container-run|container-list|container-kill`. Verified
  end-to-end: a real container is spawned and killed through the wire
  on the runnable daemon (`test_host_control_plane_runs_and_kills_container`).
  New `TestServiceRouter` (4) + `TestControlService` (6). Suite 276 →
  288 (262 run + 26 skipped).
- 2026-08-14 (**PID-1 launcher-init**): the launcher stays alive as the
  namespace's PID-1 and runs the container command as its plain child
  — Linux discards signals sent to a namespace PID 1 without a
  handler, so the old exec-into-PID-1 design always burned the full
  10s terminate window. The init forwards supervisor signals, reaps
  the command, and exits with its status; the seccomp policy is
  applied by the command child (the init is the trusted unfiltered
  supervisor, the model tini uses); the manager resolves the
  command's HOST pid via the init's /proc children file; both pids
  join the container cgroups; terminate escalation covers both and
  reaps the setup child. New `TestPid1Init` (7). Suite 288 → 295
  (26 skipped on hosts with working userns; on runners that block the
  uid_map write — e.g. GitHub Actions — the class probes with a real
  launch via `_direct_launch_supported()` and skips instead of failing,
  so the suite stays green there too).
- 2026-08-14 (**host integration, plan §4.5**):
  `packaging/systemd/nyrqis-backend.service` runs the backend daemon
  at boot (`service serve` on `/run/nyrqis/status.sock`) unprivileged
  (`DynamicUser` + `NoNewPrivileges` — containers launch through
  unprivileged user namespaces), `Restart=on-failure`, hardening,
  install steps in `packaging/README.md`; new `TestSystemdUnit` (3
  tests: daemon wiring, `systemd-analyze verify` on systemd hosts,
  unprivileged posture) — hermetic (reads the unit, installs nothing).
  Suite 295 → 298 (272 run + 26 skipped).
  (269 run + 26 skipped).
- 2026-08-14 (**runnable status-service daemon + auto capability
  lifecycle**): `nyrqis_backend.py service serve` runs a
  `StatusServiceHost` daemon — the container manager, transport sender
  registry, capability manager, server, and service share state, so a
  container spawned against the daemon is automatically registered AND
  granted (defaults at spawn, revoked on terminate — NPS-010 §5;
  `ContainerManager` gains `capability_manager=` mirroring the
  ipc-registry hooks) and can call the status service with zero manual
  bookkeeping. SIGINT/SIGTERM shut it down cleanly. The status e2e
  now proves the whole chain automatically; new
  `TestContainerCapabilityLifecycle` (6) + `TestStatusServiceHost` (5,
  incl. a real CLI subprocess that binds 0700 and exits 0 on SIGTERM
  and a REAL container spawned through the daemon's own manager that
  completes the status CALL against it — the operator flow
  end-to-end). Suite 265 → 276 (250 run + 26 skipped).
- 2026-08-14 (**first real backend service on the transport**):
  `ipc/service.py` (`BackendStatusService`) is a container-facing
  CALL/REPLY service attached to an `IPCDatagramServer`: `ping`
  verifies the whole chain (transport + kernel identity + reply
  path), and `status` — capability-gated on `CAP_SYSTEM_INFO` (a
  default grant), denied fail-closed without a `CapabilityManager` —
  reports the backend version, uptime, and the caller's own container
  id and capability set. The server's CALL dispatch now swallows
  handler exceptions (a service bug replies "internal error", never
  kills the serving thread). New `TestBackendStatusService` (7 tests)
  and `test_container_calls_status_service` — a REAL container
  completes a `status` CALL through the auto-registry + capability
  enforcement. Suite 257 → 265 (239 run + 26 skipped).
- 2026-08-14 (**ADR-0020 migration #6: IPC transport hot path**):
  `rust/transport/` (ABI 1.0.0, `libc` the only dependency) ships the
  per-message syscall half of the Unix-domain datagram transport —
  sendto, poll+recvmsg with MSG_DONTWAIT, and the SCM_CREDENTIALS
  parse yielding the sender's global (pid, uid, gid) and bound path —
  behind the versioned FFI surface. `ipc/transport_codec.py` is the
  loader (search order, ABI gate, BackendUnavailable → Python-floor
  fallback, NYRQIS_RUST_FORCE=1) wired into
  `UnixDatagramEndpoint.send`/`receive`; binding/0700/SO_PASSCRED
  stays on the floor. New CI jobs: `rust-transport` (build + unit
  tests) and the required `rust-transport-conformance` gate (transport
  loader + differential classes forced through the FFI; raw-wire only,
  so the separate ipc-codec loader's force check stays honest). Suite
  239 → 251 (225 run + 26 skipped without the Rust crates). The crate
  is the documented close path for the NPS-003 §6.1 latency gate.
  **Delta measured 2026-08-14 (same-session A/B, §20): the current
  FFI surface is slower than the floor at the median (p50 ~426 µs vs
  ~231 µs; +23 µs/round-trip isolated — per-recv malloc + copies), so
  the §6.1 performance close is a second-pass FFI surface
  (caller-supplied/pooled output buffers), not this crate as-is; the
  migration stands on the boundary rule + byte-identical conformance.**
- 2026-08-13 (**ADR-0020 migration #5: container launch-plan
  primitives in Rust**): `rust/container/` (ABI 1.0.0, `libc` the only
  dependency) ships the pure launch-plan computations the manager
  makes per launch — the launcher argv (FIND-BACKEND-004), the cgroup
  v1/v2 resource plan (FIND-BACKEND-003: `notify_on_release=0`), the
  `--map-root-user` uid/gid maps, and the NPS-010 §4 state machine.
  `backend/container_codec.py` is the loader (search order, ABI gate,
  byte-identical `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097`
  malformed flat → the floor's `ValueError`, `-4098` invalid
  transition → `False`), wired into `transition_to`,
  `_launcher_args`, `_cgroup_v1_plan`, `_setup_cgroups_v2`, and the
  direct-syscall child's root maps. New CI jobs: `rust-container`
  (build + unit tests) and the required `rust-container-conformance`
  gate — the container-facing classes, including the end-to-end
  launch tests that route through the codec, forced through the FFI.
  Test suite: **257/257 (231 run + 26 skipped without the Rust
  crates)**.
- 2026-08-13 (**ADR-0020 Accepted + syscalls scaffold + CI test fix + session §17**):
  ADR-0020 v2.0.0 **Accepted** by Architecture Group (issue #2; the
  acceptance text is recorded in the ADR's Status section — the PAT can
  create issues but not comment/close them, so closing #2 is a manual
  step). **Migration priority #2 scaffolded**: `rust/syscalls/`
  (clone/unshare/sethostname/prctl FFI wrappers behind ABI-001, stub
  entry points returning ERR_INTERNAL, conformance plan; CI builds it).
  **Real CI bug fixed**: the `backend` job was red since 2026-08-12 —
  `test_auto_compact_is_the_mount_default` and
  `test_auto_compact_failed_mount_leaves_no_watcher` mocked `_build_fuse`
  but not `attach()`, so they failed on CI (no fusepy) while passing on
  this host (fusepy present); both now mock `attach()`, verified by
  re-running the suite under a no-fusepy pre-import patch — 113/113,
  4 live-mount tests correctly skipped. The rust-seccomp job (crate
  build + tests) has been green in CI all along — the crate compiles.
  **Consolidated session §17** (2026-08-13): every recorded finding
  reproduced for a second session.
- 2026-08-13 (**ADR-0020 governance + terminology + plan reconciliation**):
  AG review opened as **issue #2** (mirroring #1 for ADR-0019): ADR-0020
  remains `Proposed` until Architecture Group acceptance per NPC-001 §6.4.
  Glossary (NPC-006) v1.2.0 gained **Platform Boundary** and
  **Platform-Critical Execution Path** entries. `docs/implementation_plan.md`
  reconciled: the two stale language signals — Python `ctypes` syscall
  wrappers and the `fusepy` FUSE path — are now marked Rust-first
  platform-critical paths behind ABI-001, with ADR-0020 added to its
  citations.
- 2026-08-13 (**ADR-0020 v2.0.0 — canonical language matrix + platform-boundary principle**):
  the recorded language strategy moved from "Python + Rust by component
  class" to the canonical matrix — Rust, C++, and C as the platform
  languages (NyHAL **Rust-first**; C++ primary for NyUI/NyShell/NyGame;
  C where hardware requires), Python unrestricted **above the platform
  boundary** (tooling, SDK bindings, tests, CI, research) and barred as
  an execution language for platform-critical paths. New normative
  rule: **platform-critical execution paths must not depend on the
  Python interpreter** — the all-Python Linux backend is now explicitly
  the *reference implementation* whose platform-critical modules
  (seccomp enforcement, FUSE ops, container launch, IPC core) carry a
  migration obligation to the rust/seccomp queue. Docs reconciled in
  the same pass: how-to language guide (boundary-first), sdk/README
  (Rust + C++ core, Python SDK binding), rust/README, IMPLEMENTATION_STATUS
  (113/113), NPC-005 v1.14.0, mkdocs nav.
  the ADR-0020 seccomp wire format is implemented and verified
  (`SeccompPolicy.to_json()` / `policy_from_json()`, round-trip tests)
  and the round-trip **found and fixed two real aarch64 syscall-table
  bugs** (`readlink: 76` → splice alias; `faccessat: 49` → should be
  48; verified against `/usr/include/asm-generic/unistd.h`), with a
  unique-numbers guard test. The Rust implementation itself stays
  blocked: rustup's toolchain download does not complete on this host
  (three attempts, 2026-08-12) — documented in `rust/README.md` and
  `rust/seccomp/README.md`. **Daemon lifecycle (DAEMON_LIFECYCLE.md)
  partially implemented:** dirty-flag tracking, `NyFSMount.shutdown()`
  (dirty-gated final commit → unmount), SIGINT/SIGTERM handlers in
  blocking mode, and `auto_compact` is now the mount default — items 1–2
  of the spec's §4 gate done; AG tuning review (item 3) pending.
  Tests 103 → **108**.
- 2026-08-12 (**first ADR-0020 migration scaffold + daemon lifecycle + consolidated session**):
  the seccomp policy compiler is scaffolded at
  `source/nyhal-linux-backend/rust/seccomp/` — FFI boundary contract,
  Cargo manifest, and conformance test plan; **unbuilt** (no Rust
  toolchain on the dev host; the Python implementation remains the
  only shipped one until the conformance suite passes through the
  FFI). New `docs/how-to/choose-an-implementation-language.md`
  (component → language map) and an SDK language-strategy section;
  `source/nyhal-linux-backend/DAEMON_LIFECYCLE.md` (design note)
  answers ADR-0019's open question 1: `auto_compact` SHOULD become
  the mount default once signal handling, a final-commit shutdown
  contract, and AG tuning review land. The consolidated session
  (BENCHMARK_RESULTS §16) reproduced every recorded finding and
  surfaced a runner bug — the `--nyfs-mount` 60 s watchdog was never
  cancelled (would kill any full `--all` run with exit 99) — fixed.
- 2026-08-12 (**implementation-language strategy; ADR-0020**): the
  first recorded language decision — Python for the user-space backend
  and tooling, Rust for kernel-adjacent/hot-path/security-critical
  components, with a versioned FFI boundary (ABI rule) and an
  evidence-gated migration rule (no rewrite without measured
  performance or a security finding). Proposed pending Architecture
  Group review; index NPC-005 v1.13.0. Also closed the last §9
  benchmark gap: journal × block-size interplay (§15) — block size is
  an interleaved-mode lever only (save time flat 0.18–0.25 s under
  journal; ratio 6.38 → 6.50).
- 2026-08-12 (**compaction API + background watcher; ADR-0019**):
  journal compaction is exposed to daemons as `journal_bytes()` /
  `maybe_compact()` / `compact_journal()`, and `NyFSMount` gained an
  opt-in `auto_compact` watcher (background thread, lower threshold)
  so the materialize pass runs during idle intervals instead of
  stalling a transaction. Tests 99 → **103** (public compaction API,
  crash-mid-materialize leaves the journal intact, live-mount watcher
  trims the journal below the save-time threshold, failed mounts leave
  no watcher running). Benchmarks:
  §13 mixed write/read/commit loop — journal commits ~3.7–4× faster
  (131 vs 504 ms) at identical write throughput; §14 compaction cost —
  the deferred pass is an interleaved save of referenced blocks
  (~27 ms/block; 11.2 s per 417-block / 2.5 MB journal). The default
  flip's governance review package is **ADR-0019** (Proposed).
- 2026-08-12 (**journal commit is now the default commit mode**):
  `save()` defaults to `use_journal=True` (one fsync per transaction;
  interleaved path kept as `use_journal=False`). The full suite passes
  99/99 with the default flipped, including the live FUSE mount
  durability test (the fsync handler now commits via the journal, and
  the test asserts a non-empty journal). Benchmark sections that
  document the interleaved durability baseline (§7 persist, §10 dedup,
  §12 real corpus) pin `use_journal=False` explicitly so their recorded
  numbers stay reproducible. Implementer decision 2026-08-12;
  Architecture Group review is the formal governance step.
- 2026-08-12 (**journal commit + benchmark followups**): `save()` gained
  `use_journal=True` — an append-only journal (`state/journal.bin`)
  fsynced once per transaction before the atomic metadata swap, with
  `load()` journal fallback, torn-tail tolerance, and compaction past
  `journal_compact_bytes`. Benchmark (`BENCHMARK_RESULTS.md` §9, §12):
  **~60–70× faster commit** (0.20 s vs 11–15 s on 17.1 MB/417 blocks;
  2.0 s vs 123 s on a 3,855-file real corpus) at ~0.3% on-disk
  overhead — the decisive commit-cost lever; whether it becomes the
  default is an Architecture Group decision. Three more benchmark
  sections: cross-snapshot dedup (§10 — CoW sharing costs ~2% of an
  independent copy for a 20%-churn snapshot, ~49×), zstd-3 vs zlib-6
  codec comparison (§11 — LZ4 approximated with zlib, python-lz4
  unavailable; zlib wins ratio 3.13 vs 2.54, zstd wins speed ~23× on
  incompressible data), and real-corpus ratio (§12 — **1.29 : 1** on a
  real /usr/share sample vs 6.42 : 1 synthetic, the honest §2 data
  point). Fixed a decompress-throughput measurement bug in
  `benchmark_zstd.py` (was measured against compressed input; §4 table
  re-run). Test suite 91 → 99.
- 2026-08-12 (**NyFS FUSE write-batching fixed**): `NyFSMount` now
  negotiates `FUSE_CAP_BIG_WRITES` + `FUSE_CAP_WRITEBACK_CACHE` +
  `FUSE_CAP_MAX_PAGES` in the FUSE INIT handshake (`writeback_cache=True`,
  default) — fusepy never registers `init` and drops the connection
  pointer, so stock mounts got page-granular 4 KiB write requests. Fix:
  expose `init` on the operations adapter and override fusepy's `FUSE`
  class to set `fuse_conn_info.want`/`max_pages` (ctypes, libfuse 2.9
  layout). Kernel writes now batch at 128 KiB (8 requests per 1 MiB vs
  256); streaming writes ~40–46 MB/s vs ~1.8 MB/s (~25×), recorded in
  `BENCHMARK_RESULTS.md` §6. Correctness under writeback caching pinned
  by `test_random_overwrites_through_mount_with_writeback_cache` and
  `test_truncate_and_write_ordering_under_writeback_cache`; test suite
  89 → 91.
- 2026-08-12 (**NyFS snapshot diffing**): `fuse/nyfs.py` gained
  `diff_snapshots(a, b)` / `diff_live(snap)` — path-level added/removed/
  modified with before/after sizes, compared via per-block checksums (no
  decompression), so identical content is never reported as modified.
  9 new tests (`TestNyFSSnapshotDiff`); test suite 80 → 89.
- 2026-08-12 (**NyFS live FUSE mount verified**): `fuse/nyfs.py`'s mount
  wiring was fixed against the installed fusepy's actual API (operations
  must be callable — dispatch is `operations(op, path, *args)` — and
  `FUSE.__init__` runs the event loop itself, so there is no `main()`;
  non-blocking mounts run the constructor in a daemon thread). A real
  kernel mount now works end-to-end: multi-block write through the
  mount, `fsync(2)` → `save()` durability, CoW snapshot + overwrite +
  commit, unmount, reload from disk with snapshot restore, re-mount and
  read-back — all verified (`TestNyFSLiveMount`, 80/80 tests; skipped
  where fusepy//dev/fuse/fusermount are absent). Live-mount first-pass
  benchmark data recorded in `BENCHMARK_RESULTS.md` §6 (key finding: the
  kernel splits writes into 4 KiB requests, each rebuilding a 64 KiB CoW
  block). `benchmarks.py` gained `--nyfs-mount`; `NyFSMount.mount()`
  now forwards FUSE options (`max_write`, …). Temp diagnostic scripts
  removed.
- 2026-08-12 (**NyFS durability**): `fuse/nyfs.py` gained explicit
  `save()`/`load()` persistence (NPS-004 §7) — inode tree + snapshots in
  `state/metadata.json`, immutable block files in `state/blocks/`,
  write-blocks-then-atomic-metadata-swap ordering so a crash leaves the
  last committed state (verified by a crash-mid-save test), corrupt
  metadata/tampered blocks surface errors instead of silent corruption,
  `gc_blocks()` reclaims CoW-orphaned block files and stale temp files.
  Hardened after code review: both containing directories are fsynced so
  the metadata swap is a durable commit point, already-present block
  files are skipped (immutable ⇒ re-save is idempotent, verified by
  test), and the FUSE `fsync` handler maps to `save()`. Test suite
  71 → 79.
- 2026-08-12 (**consolidated benchmark runner + NyFS re-run**):
  `tests/benchmarks.py` now runs all runnable plan sections in one
  reproducible script (`--all`, `--ipc`, `--bucket`, `--zstd`, `--nyfs`,
  importing the Zstd sweep). Proxy numbers re-run after the per-block
  CoW rewrite: streaming writes ~162 MB/s (~4× old path), small-op
  pattern dominated by per-call compress + per-read checksum verify
  (recorded in `BENCHMARK_RESULTS.md` §5; no gate met).
- 2026-08-12 (**threat model complete + benchmark data**): Phase 7
  Package Trust Model (`NPS-027`) landed, closing Milestone 12; NPS-006
  §6 amended (v1.1.0 — signature verification per NPS-026 §6, overlay/
  base provenance, package-event audit); REQ-SEC-0003..0006 added (ledger
  now 40 requirements); first-pass Zstd level-sweep data recorded in
  `tests/BENCHMARK_RESULTS.md` §2 (ratio/compute knee at levels 3–7).
- 2026-08-12 (**rebrand**): project name changed from **Nythera** to
  **Nyrqis** everywhere (docs, code identifiers, `NYTHERA_LOG_LEVEL` →
  `NYRQIS_LOG_LEVEL`, `nythera-policy-` → `nyrqis-policy-`, LICENSE
  placeholder, `nythera_backend.py` → `nyrqis_backend.py`,
  `000-THE_NYTHERA_MANIFEST.md` → `000-THE_NYRQIS_MANIFEST.md`). The `Ny`
  prefix is retained (now denotes Nyrqis: `nyhal`, `nyctr`, `nyfs`,
  `nygi`, `nypkg`). The repository directory and GitHub URL
  (`Myco-mycelium/Nythera`) keep the old name for now — the three URL
  references are the only remaining occurrences, documented in
  `REBRAND_NOTICE.md` (CR-0035). New commits are authored `Nyrqis
  Bootstrap <bootstrap@nyrqis.local>`. Name review (2026-08-12, recorded
  in `REBRAND_NOTICE.md`): `Nyrqis` shows no collisions (OS, software,
  trademark, domain) beyond a minor fictional location; the `Ny` prefix
  is low-risk as an internal architecture convention. The local directory
  is renamed to `Nyrqis/`; the GitHub repository rename
  (`Myco-mycelium/Nythera` → `Nyrqis`) is the one remaining manual step
  (needs a maintainer — no `gh`/credentials here); the three URL
  references are deliberately left pointing at the current repo name
  because GitHub redirects renamed repositories automatically.
  Registry-level trademark check (2026-08-12): no registered/pending/
  dead trademark for `Nyrqis` or variants (`Nyrquis`/`Nyrqys`) in any
  class across USPTO, EUIPO/TMView, and WIPO — recorded in
  `REBRAND_NOTICE.md`.
- 2026-08-12 (backend hardening): Linux Backend data-plane capability
  enforcement (seccomp-BPF installed in-container — `FIND-BACKEND-002`),
  shell-free launcher (`FIND-BACKEND-004`), cgroup v1 release_agent
  hardening (`FIND-BACKEND-003`), boot transition validation +
  Secure Boot status reporting (`FIND-BOOT-001/002`), real FUSE
  operations + fusepy wiring (ADR-0016), receive-side IPC capability
  check, and the `CAP-MEDIA-*` enum split. 54/54 tests pass; verified
  end-to-end container runs on this host (hostile hostname passed
  verbatim; read-only container's write-open refused with `EPERM`).
  Docs updated in the same pass: `IMPLEMENTATION_STATUS.md` v0.2.0,
  `README_IMPLEMENTATION.md`, `requirements.txt`, this file.
- 2026-08-12: replaced all remaining "Diátaxis category placeholder" and
  "will be added as specifications are accepted" READMEs with real
  indexes (tutorials, how-to, diagrams, assets, adr, nps, npc, abi, api,
  package-format, object-registry, all seven explanation subsystems);
  updated `source/README.md` (full Linux Backend) and `tests/README.md`
  (BENCHMARK_PLAN link); added Mermaid rendering to `mkdocs.yml` for the
  new diagrams.
- 2026-07-13: `tools/check_depends_on_cycles.py` added and run for the
  first time, surfacing 4 real cycles across documents committed in
  earlier sessions. All fixed by removing the back-reference that closed
  each loop, following the same rule documented in the script's own
  docstring: a document may cite something that depends on it in prose,
  but must not list it back in its own `depends_on` front-matter.
