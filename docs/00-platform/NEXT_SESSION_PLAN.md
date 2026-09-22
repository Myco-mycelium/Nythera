---
title: Next Development Session Plan
version: 6.16.1
date: 2026-09-19
---

# Next Development Session Plan

## STANDING ITEM — Wed 2026-09-23 05:37/05:52 UTC: the dailies' second fires — one-day backlog or a trend?

Tuesday's dailies both fired ~4.5 h late (PAT watcher 10:15:16, the
new watcher 10:23:02) — inside the 8 h grace but a worse median than
Monday's mixed evidence suggested for a quiet day. Wednesday answers
whether that was a one-day scheduler backlog or the new normal:
check both dailies (API `event=schedule` filter, or the checker —
which now covers all four schedules including itself) and record the
verdict here. Pass = both fired within grace, completed success, and
the checker reports SCHEDULED RUNS: OK. A missed fire now has three
independent catchers: the checker's expected-fire logic, its new
staleness rule, and the daily CI job itself.

**Carried trigger — post-rotation dispatch verification:** if the PAT
has been rotated by then (grants re-verified MISSING at 10:36 UTC
Tue), the first dispatch drill is the followup that verifies the
recovery path end to end — the 08:15 UTC drill's HTTP 403 should
become 204, and `PAT_EXPIRES_AT` should be set (the pat-expiry-watch
run may then honestly go red if the new expiry is ≤7 days out — set a
fresh date when rotating).

**Decided 2026-09-22 (D4) — no longer queued:** the NPC-002 §6.2
crypto review sat with the repo operator (per the D1–D3 precedent)
working from the verified brief, and ACCEPTED the scheme: primitives
as proposed (Ed25519 + SHA-256 per role, RSA/ECDSA/novel constructions
rejected), G1 adopted with the display form amended to the FULL
64-hex SHA-256 fingerprint (no truncation anywhere), G3's
canonicalization deferred to §9, G4's quorum confirmed (single-root
MUST verify, any-root MAY sign), freeze/defer split adopted. Landed as
NPS-026 v1.3.0 §6.7 the same day; §6.3.6 re-points at §6.7; NPS-028's
fence narrows to the §6.7.3 deferral; REQ-SEC-0003's implementation
gate opens. NPS-026's remaining gate to `Accepted` is implementation
validation only.

## STANDING ITEM — CLOSED — Tue 2026-09-22 05:52 UTC: scheduled-runs-watch's FIRST FIRE

**CLOSED — PASS.** The fire landed **10:23:02 UTC (4 h 31 m late)**
and run [#1](https://github.com/Myco-mycelium/Nythera/actions/runs/35715668768)
completed **success** by 10:23:16 UTC — an 8-second run, every step
green. The run executed `0bebd1a`, today's tip INCLUDING the
self-coverage fix pushed ~09:15 UTC, so the first fire live-verified
the self-checking checker in CI (exercising its "first fire of a new
watcher has nothing prior to judge" path honestly). The
scheduler-backlog hypothesis is confirmed: the PAT watcher fired
10:15:16 (4 h 38 m late) minutes earlier — the two dailies released
from one backlog together, the measured envelope grew slightly
(Monday's max was 5 h 49 m; both stayed inside the 8 h grace). The
interim "not yet fired" readings (08:02–09:04 UTC, six checks) were
calibration-correct, not findings.

**Interim verdict, 08:02 UTC (kept — it is what found the gap): NOT YET FIRED —
within grace, and the check itself found a gap that is now closed.**
The workflow is `active` on the default branch with **zero runs of any
event** (API, `event=schedule` and unfiltered both queried); due
05:52, now 2 h 10 m past — inside the 8 h window Monday's data set
(delays up to 5 h 49 m). Consistent with another scheduler-wide
backlog. The two weekly ISO crons and the PAT watcher still show
Monday's verified successes. The plan was to re-check after 13:52 UTC
and call a no-show a real finding — overtaken by events: the fire
landed at 10:23, 3 h 29 m before that deadline. Re-checked at 08:24,
08:33, 08:45, 08:55 and 09:04 UTC — still zero runs; the recovery
path (workflow_dispatch) was re-drilled 08:15 UTC and failed exactly
as documented: HTTP 403, "Resource not accessible by personal access
token".

**Correction (recorded 09:15 UTC, same day):** an earlier interim note
here and in the 09:08 hygiene entry called the amd64 cron "due 03:00
today" with an "11:00 UTC deadline" — wrong. `0 3 * * 1` is a MONDAY
cron and Monday's fire already passed verification; nothing weekly is
due today. The schedules actually due today are the two DAILY
watchers — pat-expiry-watch (05:37) and scheduled-runs-watch (05:52)
— and BOTH were unfired as of 09:04 UTC. That is the evidence for the
scheduler-wide-backlog hypothesis (weaker than Monday's three-schedule
consistency, but real: one of the two missing schedules is the very
workflow whose absence is under judgment). Both dailies then fired
within 8 minutes of each other at 10:15/10:23 UTC, closing the item
as PASS.

**The gap the interim check exposed (closed same day):**
`check_scheduled_runs.sh` never watched `scheduled-runs-watch.yml`
itself — the watcher's own cron could die silently while the script
kept reporting OK. The script's `WORKFLOWS` list now includes it; its
own in-progress run is excluded via `GITHUB_RUN_ID`; and run
judgement is staleness-aware: the latest completed scheduled run must
cover the most recent expected fire whose 8 h grace has expired
(previously only the latest run's conclusion was checked, so a cron
that fired once and died stayed green on a stale success forever).
While the expected fire is still within grace, the threshold falls
back to the fire before it — derived by walking back from the expected
fire, not recomputed from now — so the watcher checking itself
mid-cadence does not false-alarm on its own 24 h-old prior run.
`TestScheduledRunsWatchContract` pins the self-coverage
(`test_watcher_covers_itself_and_detects_a_dead_schedule`); contract
file 67/67. A first-fire run of the self-check has nothing prior to
judge and says so honestly instead of failing.

Original item text (for the final verdict's expected content): the
new daily watcher (`scheduled-runs-watch.yml`, pushed 2026-09-21 in
`7ceb2c3`) fires for the first time Tuesday morning. Check via the
API (`event=schedule` filter on the workflow's runs; the repo-side
`scripts/check_scheduled_runs.sh` reports all four schedules now) and
record the verdict here. Expected content: both weekly crons within
their 8 h windows (arm64 due 06:00, amd64 due 03:00 — both already
verified live Monday), PAT watcher fired 05:37, the watcher's own
fire landed (now that it is watched), verdict SCHEDULED RUNS: OK. A
red run Tuesday is a real finding: the watcher checks the state it
just verified, so a failure would mean the schedule broke *after*
Monday's confirmation.

## STANDING ITEM — CLOSED — Mon 2026-09-21 06:00 UTC: the arm64 cron's FIRST FIRE

**CLOSED — PASS.** The fire landed **11:49:45 UTC (5 h 49 m late)**
and run [#25](https://github.com/Myco-mycelium/Nythera/actions/runs/35596136800)
completed **success** by 12:45 UTC: `Build arm64 live demo ISO`
success (45 min), **`Menu-path boot smoke (GRUB via UEFI)` success**
—the ISO booted through its own GRUB under UEFI emulation—
`Re-attach ISO to the release` **skipped** (correct: the
`startsWith(inputs.release-tag, 'v')` gate refuses non-tag runs). The
first scheduled arm64 verification passed end-to-end.

**The false-alarm post-mortem (kept, it is the lesson):** the interim
"missed" reading at 09:05 UTC was wrong by calibration, not by fact —
the amd64 cron (due 03:00) fired 08:45 (5 h 45 m late) and the PAT
watcher's 05:37 fire landed 11:01 (5 h 24 m late): one scheduler-wide
backlog. The watcher's grace was recalibrated 3 h → 8 h on that
measured evidence (anchoring on expected-fire time was right; the
window size was the thing the day's data corrected). The fix chain:
expected-fire detection + daily CI job + 3 contract tests (`a045428`),
recalibration (`3ecb79d`).

---

### Original standing-item text (pre-fire)

`live-iso-arm64.yml` fires its first weekly scheduled build Monday
morning (cron `0 6 * * 1`; workflow state `active` — pre-verified via
the API on 2026-09-19). The manual-dispatch fallback is blocked by the
same missing Actions grant as the PAT drill, so this cron is the ONLY
path to the first scheduled arm64 verification. After it should have
fired, run:

    scripts/check_scheduled_runs.sh          # one-shot report
    scripts/check_scheduled_runs.sh --watch  # wait for it live

Pass = `live-iso-arm64.yml schedule: completed/success`. A failed fire
is self-describing (`::error::` annotations, no auth needed); re-fire
options are dispatch (needs Actions RW — currently 403) or a no-op tag
push, whichever the failure diagnosis supports.

## Session 13 (2026-09-22) — THE WATCHER WATCHES ITSELF: a first-fire interim verdict found the blind spot in the tool built to close the last one

| Item | Status |
|------|--------|
| **The interim check on the standing item did what the check exists to do — and caught the checker** | ✅ Recording the interim verdict on `scheduled-runs-watch.yml`'s first fire (due 05:52 UTC; zero runs of any event at 08:02 UTC — inside the 8 h grace Monday's data set) exposed that `check_scheduled_runs.sh` never watched its own workflow: the daily watcher's cron could die silently while the script reported SCHEDULED RUNS: OK forever. Closed the same morning (`e02528e`): `scheduled-runs-watch.yml` joined the `WORKFLOWS` list; the script's own in-progress run is excluded via `GITHUB_RUN_ID`; and run judgement went staleness-aware — the latest completed scheduled run must cover the most recent expected fire whose grace has expired, because conclusion-only checking passes forever on a stale success (cron fires once, dies, week-old green run keeps every later check green). The within-grace threshold falls back to the fire BEFORE the expected one — walked back from the expected fire via a `cron_epoch` anchor, not recomputed from now — so a self-check mid-cadence does not false-alarm on its own 24 h-old prior run (the exact class of misjudgement the arm64 post-mortem warned about) |
| Contract pinning and verification | ✅ `TestScheduledRunsWatchContract` gained `test_watcher_covers_itself_and_detects_a_dead_schedule` (file 66 → 67; all 4 in the class, 67 in the file). The dead-schedule rule was exercised on synthetic data (dead weekly cron → `::error::…a scheduled fire has no run`), the `cron_epoch` anchor verified against all three repo crons, created_at extraction + GNU date parse verified against a real API value, and the checker verified live (exit 0). Full suite re-run: `unittest discover` **6,384 OK / 4 environmental skips**, `run_tests.sh` **19/19 suites**; premises 20/20, `mkdocs build --strict` clean, drift OK |
| **The first fire itself: six spaced checks, still zero runs — final verdict time-gated, not concluded** | ✅ Checked 08:02, 08:24, 08:33, 08:45, 08:55, 09:04 UTC — the workflow never fired and today's amd64 fire (due 03:00) also has not landed: consistent with another scheduler-wide backlog (Monday ran ~5.5 h late), but NOT a verdict. The honest boundary: **the final verdict requires a check after 13:52 UTC** (the 8 h grace deadline). A no-show by then is a real finding; the recovery path is the manual dispatch, which was re-drilled this session and failed exactly as documented |
| The dispatch drill re-run (the third followup) | ✅ Executed 08:15 UTC: HTTP **403**, `"Resource not accessible by personal access token"` — byte-identical to the documented blocker. Grant state re-verified (`verify_pat_grants.sh`: Actions write MISSING, Variables write MISSING). The fix is one owner browser step (mint the fine-grained PAT with Contents/Workflows/Actions/Variables/PR = RW) followed by `scripts/rotate_push_pat.sh` (hidden-stdin paste, validate-then-swap ordering) — the token must never pass through chat, which is why this stays an owner action and why the drill stays a recorded 403 until then |
| The crypto review sat — D4, scheme ACCEPTED, §6.7 landed | ✅ Convened with the repo operator (the D1–D3 recorded-session precedent) after re-verifying the brief's tree claims. Decisions: primitives as proposed (Ed25519 + SHA-256; RSA/ECDSA/novel rejected); **G1 adopted with the display form amended to the FULL 64 lowercase hex** SHA-256 fingerprint (no truncation anywhere); **G3 deferred to §9**; **G4 confirmed** (single-root MUST verify, any-root MAY sign); freeze/defer split adopted. Landed as NPS-026 v1.3.0 §6.7 (§6.3.6 re-pointed, reserve removed); NPS-028's fence narrowed + §3.3 pinned; AG_AGENDA v1.6.0 (D4); spec index, security README, REPOSITORY_STATE reconciled; 2 new premise pins (23/23). REQ-SEC-0003's implementation gate opened |
| The G1 migration + the trust machinery's implementation start | ✅ `package_signing.py`: `key_fingerprint()` (SHA-256, full 64 lowercase hex), `SigningKeypair`/`PackageSignature` fingerprint fields, legacy 8-byte `key_id` kept as a `LegacyKeyAliasWarning`-guarded alias, serialized dicts version-marked (`key_id_version: 2`), `TrustStore` keyed by fingerprint with **transparent re-keying of pre-D4 persisted stores on load** (the fingerprint is derivable from the stored key — no migration tool, no stored id can contradict its key), `PackageSigner.generate_key` defaulting aliases to the fingerprint; `package_repo`/`update_signing` default to the fingerprint. Then `backend/package_pki.py` (NPS-028 v0.2.0→v0.3.0): §3 key store (three collections, §3.3 fields, §3.5 uninstall-as-revocation), §4 pipeline (one ordered path, per-stage outcomes, TOFU fail-closed — FIND-PACKAGE-003 dies here, §6.3.4 advisory/block split, §7.2 non-blocking audit sink), §5 revocation list (root-signed, monotonic, replay-refusing, atomic apply), §6 enrollment (spoof-refusing confirmation — the UI must show the ACTUAL fingerprint) + cross-signed rotation, and §3.4 custody (`save_locked`/`load_locked`: ADR-0023 envelope — per-file DEK AEAD-encrypts the store, wrapped by the Argon2id-derived KEK that is never persisted in plaintext; AEAD contexts bound to the format magic; crate custody when present; fail-closed; plaintext persistence demoted to the marked dev/test path) and §3.2's store-layer authority enforcement (every store written 0600 via atomic temp-rename — no world-readable intermediate ever exists — loads refuse group/world-readable stores fail-closed, naming §3.2 and the fix), and §7 + §5.1 (NPS-028 v0.5.0): `PackageAuditChain` reuses ContainerManager's scheme-2 audit chain **byte-for-byte** (differentially pinned against `_audit_event_content` in the tests — the nui floor↔crate lesson applied to a second reuse), §7.1 records carry package_id/version/verdict/reason/fingerprint/publisher/stages, the chain is salt-per-process with the salt persisted in the JSONL header so loaded chains re-verify and appends resume, `make_sink` attaches it under §7.2; `RevocationFetcher`/`FileRevocationFetcher`/`refresh_revocations` give §5.1 a channel configured independently of any package-feed object (fetch failure, replay, or unauthentic list leaves the store untouched), and the store persists its §5.2 `revocation_sequence` — and §3.2's daemon-side API half (NPS-028 v0.6.0, SURFACE-PKI-0001's first build): `DaemonAuthority` unforgeable (constructor raises; `mint()` is the daemon's only entry) + `PkiDaemonService` as the store's ONLY supported interface (authority-guarded fail-closed reads/writes, no store/enumeration path, mutations audit-chained); the physical IPC transport completed the surface the same session (NPS-028 v0.7.0, SURFACE-PKI-0001 complete at API+transport): `PkiIpcServer`/`PkiIpcClient` over a Unix socket — JSON-lines, one server-minted `DaemonAuthority` (connections are wires, not identities), an explicit op allowlist that excludes key-material reads (verification runs daemon-side; packages have no right to export store contents), unknown/private ops refused, wire mutations audit-chained) and the deployment hardening (NPS-028 v0.8.0): 0600 socket mode at bind, fail-closed bind refusal of group/world-writable socket directories naming the fix (`chmod go-w`), SO_PEERCRED daemon-uid-or-root policy where the OS exposes it (0600 mode as the documented floor), idempotent stop. 36 new tests (72 in the PKI module); full suite **6,466 OK / 4 environmental skips** |
| **The production process model closes NPS-028's §3.2/§3.4 assembly (NPS-028 v0.9.0)** | ✅ `PkiDaemonRunner` — the boot/serve/persist cycle as one unit: custody is MANDATORY on the production path (a runner without an unlock secret is a constructor error; a custody file at boot with no secret refuses to start), the store unlocks at boot (or is created on first start), the §7 chain resumes from its persisted salt header and re-verifies, and a clean stop persists custody + chain exactly once (idempotent — `test_stop_is_idempotent`, `test_stop_persists_and_restart_resumes`: revoke over IPC → stop → fresh runner on the same paths → the revocation survived, no duplicated audit entries on the second cycle). `nyrqis_backend.py pki serve` assembles it with **signal-flag polling, not `signal.pause()`** — the default disposition would kill the process on the first SIGTERM before the custody+audit persistence ran, exactly the class of silent-loss bug the §7 chain exists to prevent; handlers are restored in `finally`. Deployed as `packaging/systemd/nyrqis-pki.service` (DynamicUser, NoNewPrivileges, ProtectSystem=full + ReadOnlyPaths=/opt/nyrqis, StateDirectory for the custody store + audit chain, the unlock secret from the optional EnvironmentFile `/etc/nyrqis/pki.env` — the `-` prefix keeps the unit valid, but the daemon exits without the secret by design; the unit comment documents the first-boot custody setup). Session 10's two-tree rule honored: the root `packaging/systemd/` mirror restored byte-identical, `install.sh` now deploys the PKI unit alongside backend+desktop, and `TestPkiDeploymentWiring` (5 tests) pins the whole wiring — parsed, not transcribed: the unit's ExecStart paths, the CLI flags, the mirror equality, install.sh's list. 12 new tests (84 in the PKI module); `systemd-analyze verify` clean on both tree copies |

## Session 12 (2026-09-18) — THE MEASUREMENT PASSES CLOSE THE BACKLOG; THE REVIEW GETS ONE AGENDA

**Two more benchmark gates closed (§34–§35):** ADR-0018's hash-chain
audit log measured against the real implementation (~6.4 µs/event
append, O(n) verify, "negligible" premise confirmed) — which surfaced
the tamper-scope hole (details payload not hashed), a second parallel
chain mechanism, and memory-only persistence. NPS-010 §9's container
resource-limit defaults measured under real cgroup-v2 enforcement
(256 MB default = 28–80× footprint floor; quota throttling is a TAIL
phenomenon — p50 unchanged while p95 grows 8×; 64 PIDs = 1.5× a
modest supervisor; SUSPENDED = full memory, zero CPU, still
reclaimable). The §34e-style details-coverage fix was prototyped,
measured (19.0 µs/event, suite 2532 OK), recorded in the review
package, and reverted pending the Group — main carries the decision,
not the change.

**The docs caught up to the code:** ADR-0018 review package (four
decisions, including the Accepted-vs-Proposed index discrepancy);
NPS-010 §9 v1.5.0 proposed-defaults table; NPS-026 v1.1.0 NyVault
findings (M14 Phase 1 complete); and **`AG_AGENDA.md`** — every
pending Architecture Group decision on one document, in three
independent bundles with a decision-log table. The next session that
wants to move any ADR out of Proposed starts there.

**Also this round:** v0.29.26 shipped first-try on the rehearsed
checklist and was verified end-to-end (anonymous download → digest
match → boot on this host); the release tooling gained the
re-attach jobs, the rotation tool, the grant verifier, and the
watcher-aware Monday check.

## Session 11k (2026-09-16) — THE LESSONS GET ENFORCERS: the byref rule is scanned, the wayland crate is a CI gate, the race harness is a tool

**Byref rule (0.29.24):** ``test_ffi_byref_contract`` statically scans
every FFI wrapper module for ``ctypes.byref(struct.field)`` — the
pattern behind the 0.29.22 DRM crash — and fails listing call sites.
The DRM regression is separately pinned.

**Wayland crate in CI:** ``rust-wayland-conformance`` builds the
cdylib and forces the 18 multimonitor tests through it as a required
gate — the crate path can never again go unexercised the way DRM's
did.

**Race harness as a tool:** ``scripts/test_release_race.sh`` +
``fake_gh.sh`` permanently verify the concurrent-create release path
offline (both race orders, both jobs exit 0, both ISOs upload);
documented in ``packaging/live/README.md`` with a run-after-any-
upload-change rule.

## Session 11j (2026-09-16) — THE SKIP PURGE COMPLETES: 35 → 9; a real DRM bug falls out of asserting the crate path; the register is pinned

**The find of the round (0.29.22):** rewriting the DRM test skips as
both-mode assertions exposed a **latent `get_connector_info` FFI
crash** — `ctypes.byref(struct.field)` passes a plain int, not a
ctypes instance. Invisible for the crate's whole life because the
tests skipped whenever the crate WAS present. Fixed and verified
against the loaded cdylib.

**Purge:** 19 → 9 skips. DRM device-ops (6) now assert both stub and
crate paths; the compositor-host stub test patches codec
availability; the 3 boot-to-desktop render tests run the real
architecture (`Compositor.render_screen`, `NyrqisShell(doc).run()`).
Dead `except ImportError → skip` branches around first-party imports
removed — breakage fails loudly.

**Pinned:** `TestSkipRegister` (3 tests) — the register
(`TEST_SKIP_REGISTER.md`) exists and states the rule, no
first-party import-failure skip exists anywhere in the suite, and
the header counts match suite reality. Suite **8,986 passed,
9 skipped** (all environmental), leak-count 0.

## Session 11i (2026-09-16) — SKIPS ARE ANSWERS, NOT EXCUSES: 35 → 19; the zero-leak guarantee and the release path are now pinned

**Skip triage (0.29.21):** all 35 skips categorized. 16 were
actionable — ``TestSystemMonitor`` never ran because the spec's
``SystemSnapshot`` didn't exist; the implementation now meets its spec
(snapshot/latest/history/summary/search/top, ``include_processes``,
spec kwargs) with zero caller breakage. The remaining 19 are legitimate:
6+1 crate-vs-stub inversions, 2 Vulkan, 6 Wayland crate, 1 sandbox
SCM_RIGHTS, 3 real-display boot-to-desktop.

**Pinned:** the zero-leak guarantee (``TestTempFileHygiene`` — dir
removal, GC sweep of abandoned managers, idempotency, zero-new-
entries) and the release-upload path (tag gating, exact filename
parity with the builder output, ``--clobber``, race-safe concurrent
create, ``contents: write``) — the race would have failed one arch's
upload on a tag push. Suite **8,973 passed, 19 skipped**, leak-count 0.

## Session 11h (2026-09-16) — THE SUITE STOPS LITTERING: zero leaked /tmp dirs per run; ADR-0027 records the two retrospective lessons

**Temp-hygiene (0.29.20):** every suite run left hundreds of orphaned
`/tmp` entries (nyrqis-aa-*/nyrqis-se-* LSM mkdtemp trees, seccomp
policy and BPF files). Root causes closed at both levels: production
(``_cleanup_policy_files`` now removes the mkdtemp DIRS it only ever
unlinked files from; ``ContainerManager.__del__`` sweeps abandoned
managers; ``StatusServiceHost.stop`` cleans up) and tests (all
LSM/launcher/IPC tests register the sweep via ``addCleanup`` so it
runs on assertion failure too). Proof: full suite **8,949 passed,
35 skipped — leak-count 0** immediately after the run (was hundreds).

**ADR-0027:** the Wayland round's two lessons, formalized — protocol
constants written from memory are wrong until proven on the wire (a
real weston-simple-shm client disproved five opcode tables), and
build correctness claims must fail closed (the four ISO gates: dpkg
audit, probe parity, byte-compile, size).

Also in this round: arm64 ISO workflow ships the image on ``v*`` tag
releases (mirroring amd64), with ``contents: write`` granted.

## Session 11g (2026-09-16) — HANGS FAIL IN MINUTES: explicit budgets on all 33 CI jobs; size gate proven on a real build

| Item | Status |
|------|--------|
| **Timeout audit across all five workflows** | ✅ 30 previously-unbudgeted jobs (30 of 32 in ci.yml, 2 in arm64-conformance, 2 in docs) relied on GitHub's 6-hour default; all now carry explicit `timeout-minutes` sized per job type. Contract tests pin universal coverage + the boot-critical envelopes (44 total) |
| **The ISO size gate is proven on a real build** | ✅ Fresh from-scratch amd64 ISO: 355 MB, exit 0, every gate green — the gate passes known-good builds with headroom as designed |
| **The pipeline is documented** | ✅ `packaging/live/README.md` now maps the four-job verdict table, the four fail-closed gates in order, the rootfs cache contract, arm64 specifics, and manual run instructions |
| Suite | ✅ Python suite **8,854 passed, 29 skipped**; contract 44/44; all 5 YAMLs valid |

## Session 11f (2026-09-16) — BUILD-TIME SIZE GATE + PER-ARCH, PER-PATH JOB SPLIT: failures are diagnosable from the job list

| Item | Status |
|------|--------|
| **ISO size gate in the builder** | ✅ The build dies if the ISO exceeds 500 MB (known-good ~354 MB, duplication produces 700+ MB) — the 815 MB /opt-nesting bug would now abort at build time instead of shipping a bloated image. Contract-pinned with envelope bounds (40→42 tests) |
| **arm64 menu smoke is its own CI job** | ✅ `menu-boot-arm64` downloads the built ISO and boots through UEFI GRUB — mirrors the amd64 `menu-boot` split. A menu regression fails one named job while builder gates + direct smoke stay green; neither path masks the other. Contract tests pin the split for both arches (parsed-YAML checks, immune to header/trigger mentions) |
| Suite | ✅ Contract 42/42; YAML validated; builder syntax OK |

## Session 11e (2026-09-16) — CI STOPS REBUILDING THE IDENTICAL ROOTFS: cache keyed by the include list, contract-pinned

| Item | Status |
|------|--------|
| **Rootfs caching in both ISO workflows** | ✅ `actions/cache` stores the debootstrap rootfs keyed by the include list — a package-set change invalidates automatically; the chroot apt top-up runs on BOTH paths (a cached rootfs can never predate a top-up change); the arm64 workflow re-stages qemu-aarch64-static if the cached rootfs lost it; the unused 7-day tarball artifact retired. 4 contract tests pin the wiring (39 total) |
| **Session artifact hygiene** | ✅ ~7 GB of one-shot /tmp artifacts swept (3 rootfs workdirs, superseded + bloated ISOs, smoke logs, hundreds of test ephemeral dirs); the two final boot-verified ISOs kept |
| Suite | ✅ Python suite **8,849 passed, 29 skipped**; contract 39/39; YAML validated |

## Session 11d (2026-09-16) — BOTH ARCHITECTURES PROVEN ON REAL EMULATED MACHINES: the arm64 image completes its own boot smoke

| Item | Status |
|------|--------|
| **The arm64 image was cross-built from scratch and booted** | ✅ Foreign debootstrap under qemu-user + binfmt, GRUB arm64-efi from the Debian .deb (CI's exact method), 354 MB ISO assembled clean through every builder gate (dpkg audit, probe parity, initrd, squashfs) — then booted under `qemu-system-aarch64 -M virt` (edk2 firmware, TCG emulation, QEMU 8.2 handles Debian's zboot vmlinuz) |
| **The first arm64 boot exposed a real arm64-only bug — and the fix is proven on the machine** | ✅ The image booted, autologged in on ttyAMA0, then hung forever: the demo's smoke handshake matched only `/dev/ttyS0`, so the arm64 serial session took the non-serial branch and parked on `sleep infinity` before ANY marker printed (the amd64-first codebase never exercised this path). The handshake now matches both serial consoles; the SAME image then passed every marker (`READY`, `PONG=1`, `NYRQISCTL=1`, `PKGS=ok`) — the arm64 ISO can complete its own boot smoke |
| **The builder is idempotent against a reused rootfs (the CI cache path)** | ✅ Two rebuild defects found on the reused-rootfs path: `useradd demo` aborted the build (user already exists), and `cp -a src dst` NESTED a duplicate backend tree — the image grew 354 MB → 815 MB in one rebuild. Both fixed; the rebuilt image is back at its correct 354 MB and passes the smoke again |
| **amd64 reproducibility** | ✅ A fresh from-scratch amd64 ISO re-passed BOTH smokes — direct-kernel (READY/PONG/PKGS all green) and the GRUB-menu path (banner, daemon, PKGS) — on the same day as the arm64 proof |
| Suite | ✅ Contract tests 35/35; boot_init 26/26; version gate 0.29.16 |

## Session 11c (2026-09-15) — THE ISO BUILT AND BOOTED END-TO-END: the user's boot report reproduced, root-caused, and disproven

| Item | Status |
|------|--------|
| **The user's "python3 MISSING" boot report was reproduced at the source** | ✅ A full local build (debootstrap → squashfs → xorriso) died in second stage configuring python3-zstandard. Root cause: debootstrap's resolver cannot map VIRTUAL dependencies — python3-zstandard needs `python3-cffi-backend-api-min/max` (Provided by python3-cffi) and python3-pycparser needs `python3-ply-lex/-yacc-3.10` (Provided by python3-ply). Fixed by naming the real providers in every include list (builder + both CI workflows); verified empirically before the fix (exit 1, audit non-empty) and after (exit 0, audit empty, modules import) |
| **The complete image was built and booted on this machine** | ✅ 353 MB ISO assembled clean; boot smoke in QEMU passed every marker (`READY`/`PONG`/`PKGS=ok`); the in-image probe prints ✓ for python3, zstandard, PyNaCl, lz4, FUSE 3, nyrqisctl, entry points — only the hardware DRM lines stay machine-dependent, as designed |
| **Fail-closed hardening from the diagnosis** | ✅ A `dpkg --audit` gate aborts the build on ANY unconfigured package, on every rootfs acquisition path; `--keep-workdir` now actually keeps the rootfs (the trap was deleting the post-mortem artifact); 2 new contract tests pin the include list and the gate |
| Suite | ✅ Contract tests 31/31; version gate 0.29.15 |

## Session 11b (2026-09-15) — PRIORITY 8 DOUBLED DOWN: a second real client, CI ownership, and boot-time package parity

| Item | Status |
|------|--------|
| **weston-terminal (GTK class) runs its full session** | ✅ VTE spawns a real shell, draws real content through TWO SHM pools (window + cursor), binds every global at client-chosen versions (wl_output v2 exercises the version-gated `.done`/`.scale` path), survives with zero protocol errors. Pinned as `tests/test_weston_terminal.py`. Priority 8 now holds TWO independent real-client proofs — the minimal SHM path AND a GTK event-loop client with a spawned process |
| **The real-client run is CI's job now** | ✅ New `wayland-real-clients` job installs weston on every round and drives the four real-client test modules against the built crate — a wire-format regression fails CI in minutes instead of at the next manual client run |
| **The boot smoke verifies the probe's package class inside the image** | ✅ The demo prints `NYRQIS_BOOT_SMOKE_PKGS=ok\|missing:…` (the probe's required class: python3, zstandard, PyNaCl, fusermount3) and BOTH smoke drivers gate on it — an image whose probe would print MISSING fails CI at smoke time instead of booting to a sad first screen (the exact failure a real boot reported). Hardware items stay honest machine-dependent reports. Contract tests pin the chain end to end; the marker's ok and missing paths were both exercised locally |
| Suite | ✅ Crate 67/67; Python suite **6,311 passed, 29 skipped**; version gate 0.29.14 |

## Session 11 (2026-09-15) — PRIORITY 8 CLOSED AT THE PROTOCOL LEVEL: the wire loop survives a real client handshake

| Item | Status |
|------|--------|
| **The audit that found the gap** | Re-reading `rust/compositor/src/event_loop.rs` against what real clients do: the loop advertised five globals but serving ANY bind of `wl_output`/`wl_seat`/`xdg_wm_base` fell into the `unsupported opcode` arm — a protocol error that killed the connection. Every real client (weston-simple-shm, GTK4, Qt6) binds at least one of those at handshake: the compositor could hold a socket conversation with our own test client and NO ONE else. Same class: `wl_surface.damage`/`damage_buffer`/`set_*_region`/`set_buffer_scale` (every renderer sends them), `create_region`, `wl_buffer.destroy`, `wl_shm_pool.resize`, and no `wl_shm.format` events on bind (simple-shm cannot even pick a pixel format), and no initial xdg configure (xdg clients block forever) |
| **The client-compatibility surface (ABI 0x0000_0300 → 0x0000_0400)** | ✅ Bind responses for every advertised global: wl_shm formats (ARGB8888/XRGB8888), wl_output geometry/mode(current\|preferred)/done (real output geometry at 96-dpi physical size, `.done` gated to bound version ≥ 2 — never send an event a client's bound version cannot parse), wl_seat capabilities+name (version-gated) with device objects for get_pointer/get_keyboard/get_touch. xdg-shell: get_xdg_surface (validated against a real wl_surface — non-surface argument = the spec's defunct-surface error), get_toplevel/get_popup, pong, destroy, and the initial `xdg_toplevel.configure(0,0,[])` + `xdg_surface.configure(serial)` pair on first commit in the required order. The boring requests served instead of erroring: damage, damage_buffer, opaque/input regions, buffer scale/transform, create_region + region ops, buffer destroy, pool resize, null-buffer detach |
| **wl_display.error on every protocol error** | ✅ Clients learn WHY they were disconnected (message names the offending object) instead of a silent socket close; bind versions above the advertised version are refused with an error event |
| **Per-client object namespaces + disconnect teardown** | ✅ The object table is scoped by owning client (ids are per-connection namespaces — two clients may both use id 2); new `nyrqis_compositor_client_disconnected` FFI destroys the client's crate surfaces, drops its objects/roles/queue — a disconnect no longer leaks surfaces into the slot table (the Session 9 leak class, one layer up). `CompositorHost.on_client_disconnected` calls it fail-closed. A subtle lock-ordering hazard fixed in passing: `reset_state` held STATE while acquiring EVENT_LOOP (the dispatch path nests the other way) — deadlocked under the parallel harness; reset moved outside the lock |
| **The real client ran** — and audited every constant | ✅ Upstream `weston-simple-shm` (real libwayland, no fakes) connects, binds everything, acks the initial configure, maps its SHM pool, and runs its double-buffered frame loop (attach → damage → frame → commit → `wl_buffer.release` ×2) with zero protocol errors. Getting there caught FIVE wrong opcode tables that hand-built fixtures never hit: `xdg_wm_base.get_xdg_surface` is **2** (destroy=0, create_positioner=1, pong=3), `wl_shm_pool` is **create_buffer=0/destroy=1/resize=2**, `wl_surface` is **set_opaque=4/set_input=5/set_transform=7/set_scale=8**, `wl_output` events are **done=2/scale=3** (scale precedes done), and `wl_display.error` carries the **`ous`** signature. Every constant is now wire-verified against the canonical `wayland-client-protocol.h`. Lesson recorded: memory-recalled opcode tables are how this class of bug ships; trace the real client or read the generated header |
| **The socket layer silently discarded every client fd** | ✅ Found by the real client: `recvmsg(bufsize)` defaults `ancbufsize` to 0, which DROPS all ancillary data — a client's `wl_shm.create_pool` fd (its pixel memory) vanished in transit and `fds_received` stayed 0. The read loop now passes a real ancillary buffer size. Also: `wl_buffer.release` no longer retires the buffer object (release ≠ destroy; weston-simple-shm re-attaches released buffers — destroying them killed the client on its third frame) |
| Client-compat pinned as tests | ✅ `tests/test_wayland_client_compat.py` (8 tests): the real-client handshake over a real Unix socket through the full stack, asserting exact event bytes — format codes, geometry/mode/scale/done, capabilities, the xdg configure pair and its ordering, the pool→buffer→attach→frame→commit content path, error delivery, version-overrun refusal. `tests/test_weston_simple_shm.py`: the upstream binary against the full stack for ~3 s (must stay connected, drive the SHM fd through, register a surface, zero protocol errors). `tests/test_weston.py` rewritten — the old stubs ran without `XDG_RUNTIME_DIR` (libwayland refuses absolute `WAYLAND_DISPLAY`) against a bare socket server and asserted nothing. Crate tests 50 → **67** (17 wire-level tests). Test-writing lesson recorded: byte-level event accounting must count NEW events per recv, not cumulative stream positions — three flaky-looking failures were this, not the implementation |
| Honest limitations | Input events are still not fabricated (an honest silent seat beats a lying synthetic stream); popups join the table with no positioning logic; pixel content still flows host-side (the fd/SCM_RIGHTS path has its own tests); weston/GTK/Qt BINARY runs on hardware remain the true end-to-end proof — this session makes the protocol conversation survive their handshake |
| Suite | ✅ Crate 67/67; full backend suite **6,307 passed, 29 skipped** on the session run (includes the previously-skipped weston tests, now real); version gate 0.29.13 |

## Session 10 (2026-09-14) — PACKAGING PARITY PINNED; the arm64 ISO is real; two systemd trees become one

| Item | Status |
|------|--------|
| **The two systemd trees, reconciled** | ✅ Found in the follow-up audit: the repo carried TWO ``packaging/systemd/`` trees, each pinned by a DIFFERENT test, and ``install.sh`` deploys the BACKEND copy — which had drifted (``RestrictNamespaces=yes`` breaking the daemon's own userns containers; no vault wiring). The backend tree is now the single source of truth; the root copy mirrors it byte-for-byte; directive-aware drift-guard tests fail on divergence; desktop unit rewired off the user-scoped ``graphical-session.target`` (``systemd-analyze verify`` was clean for the first time). Released 0.29.11 |
| **The installed-system audit, pinned as tests** | ✅ ``tests/test_packaging_parity.py`` (7 tests): the deployed unit's exact ``ExecStart`` flags run against a live daemon in a sandbox — ping, status, ADR-0009 fair-share limits (2000/s envelope → 250/s per sender), the health socket, and a full vault roundtrip through the unit's vault wiring. The exercise that FOUND the drift now runs every CI round. Two real fixes went into the test itself: the vault ``open`` output's handle is token 2 ("handle <H> for volume <VID>" — taking the last token grabbed the volume id and the daemon honestly said "foreign handle"), and vault paths are volume-absolute (``/audit.txt``) |
| **The arm64 ISO exists and is boot-smoked in CI** | ✅ The documented amd64-only gap closed at the QEMU level: ``build-live-iso.sh --arch arm64`` cross-builds the rootfs (foreign debootstrap under ``qemu-user-static``; the emulator binary is staged for chroot steps and STRIPPED before mksquashfs), emits a UEFI-only GRUB image (no isolinux on arm64) with ``console=ttyAMA0,115200`` on every entry, and the new ``live-iso-arm64`` workflow boot-smokes BOTH paths weekly: direct kernel boot + the human menu path through the image's own GRUB (UEFI via ``-bios`` + ``qemu-efi-aarch64``). Both smoke drivers gained ``--arch`` (arm64 → ``qemu-system-aarch64 -M virt``, ``-cpu cortex-a57``, ttyAMA0). Caught before CI: the builder only shipped the ``serial-getty@ttyS0`` autologin drop-in — arm64 would have booted with NO demo banner; the ``ttyAMA0`` drop-in ships on every image now |
| Arm64 boot contract pinned | ✅ 8 new contract tests in ``test_live_boot_contract.py`` (16 → 18 total incl. amd64 guards): arm64 GRUB template coverage, builder end-to-end wiring, emulator stripping, driver ``--arch``/ttyAMA0/qemu defaults, UEFI ``-bios`` wiring, CI artifact globs, amd64 ttyS0 drop-in preservation |
| Suite | ✅ 8,955 Python OK (parity 7 + contract 8 + arm64 driver paths); full suite re-run green; version gate 0.29.12 |

## Session 9 (2026-09-14) — LIVE BOOT FIXED: the demo loop CI could not see; the slot-leak class ends; menu-path smoke

| Item | Status |
|------|--------|
| **Live boot: the real failure** | ✅ The ISO booted but the demo session never yielded a shell: `.bash_profile` execs `nyrqis-demo`, which ended with `exec bash -l` — the nested login shell re-read the profile and re-exec'd the demo, an infinite banner respawn. The CI smoke missed it twice over: its serial handshake parks on `sleep infinity` BEFORE that line, and tty1's identical loop is invisible to the ttyS0 driver. Fixed: the profile is guarded by `NYRQIS_DEMO_ACTIVE`, the demo hands off via `--noprofile -i`, and the loop is pinned by contract tests |
| Demo script cleanups found with the loop | ✅ The capability probe printed twice; tty1/ttyS0 both raced a second daemon onto the shared socket (EADDRINUSE loser's failure is what a log reader sees — CI round 18's mechanism). A `flock` singleton decides the starter; the loser adopts the winner via the ping path |
| **Slot-leak class, fully closed** | ✅ The Vulkan-crate slot leak (destroys set `active = false` but leave `Some(..)` while `alloc_slot` only reuses `None`) existed in FOUR more crates: **egl** (displays/contexts/surfaces), **gbm** (buffers/surfaces/devices), **drm** (devices), and — found by this session's stress pass — **compositor** (surfaces, would exhaust at 257). All destroys now `take()`; EGL `terminate` also frees the display's configs (no other destroy path exists for them) |
| EGL config leak found by the stress loop | ✅ The 50-cycle FFI loop caught `choose_config` failing at cycle 32 ("too many configs") — single-cycle unit assertions can never see it. 200-cycle loop green after the fix |
| Stale root-level `test_egl.py` | ✅ The codec grew a required `config_id` argument and the legacy file still called the old signature (3 suite errors). Updated |
| Crate-test flakes: the shared-STATE hazard | ✅ egl/gbm/drm/vulkan tests drive one global static in parallel threads; a long loop test widened the interleaving window and a mid-sequence `reset_state()` broke both (GBM `full_surface_lifecycle` saw surface −1). The compositor crate's poison-tolerant `TEST_LOCK` pattern now guards all four crates (52 tests wrapped) |
| **Menu-path boot smoke** | ✅ `tests/boot_smoke_menu.py` + a `menu-boot` CI job: the old smoke boots the kernel DIRECTLY with a hand-built cmdline — deliberately immune to bootloader problems, so a broken GRUB menu/default entry could ship green forever. The new job boots the ISO like a machine (el torito → GRUB menu → default entry) and asserts banner + daemon on serial. Every GRUB/isolinux entry now carries `console=tty0 console=ttyS0,115200` so the human path is serial-observable. Driver verdict paths verified against fake QEMU stand-ins (pass/pong-fail/dead, both drivers) |
| Live-boot contract tests | ✅ `tests/test_live_boot_contract.py` (10 tests, unit-speed, in the standard suite): no login-shell exec anywhere in `nyrqis-demo`; the guard + `--noprofile` handoff; the builder's `.bash_profile` guard; serial console on EVERY menu entry; the menu smoke's CI artifact glob matching its tmpdir prefix (a mismatch silently uploads nothing); `bash -n` syntax gate |
| Stress pass results | ✅ vulkan 100 FFI lifecycles; compositor 300 create/destroy cycles past the 256 bound; EGL 200 full lifecycles; 10 clean crate-test reruns per graphics crate; wayland verified `take()`-based (honest −1 without a socket) |
| **Release 0.29.10** | ✅ Tagged `v0.29.10` on `751562c`; live-iso green on the tag (Build ✓ + **Menu-path boot smoke ✓**); ISO auto-attached ([v0.29.10 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.10), 253 MB, unauthenticated fetch ✓). The release ISO ships with the boot fix verified on both boot paths |
| Suite | ✅ 8,930 Python OK (was 8,920; +10 contract); 18/18 Rust crates green (compositor 50, egl 16, gbm 15, drm 8, vulkan 13) |

## Current State (End of Session)

| Metric | Value |
|--------|-------|
| Total tests | **8,948+** (Python: 8,930 — + 37 hardware-skips — + Rust: 305 across 18 crates) |
| Rust crates | **18** (all built and verified) |
| Python codecs | **8** (wayland, gbm, drm, egl, vulkan, nstudio, compositor, shm) |
| GPU pipelines | **4** verified on real hardware (GBM, DRM, EGL, Vulkan) |
| Packaging | **pyproject.toml** + systemd units + install script |
| Shell designs | **2** (default-shell.nstudio, desktop.nstudio) |
| Init script | **nyrqis_init.py** (daemon → shell → session) |
| Render pipeline | **render_pipeline.py** (GBM + EGL + DRM connected) |
| Multi-monitor | **multi_monitor.py** (output detection, workspace binding, window migration) |
| SHM buffers | **shm_buffer.py** (memfd_create + mmap for Wayland surface content) |
| Wayland compositor | **nyrqis_compositor.py** (integrated socket + codec + render) |
| Wayland socket | **wayland_socket.py** (Unix domain socket for client connections) |
| Wayland protocol | **wayland_protocol.py** (encoder/decoder for wire format) |
| DRM backend | **drm_backend.py** (real kernel UAPI: two-call connector query, dumb-buffer ADDFB2, SETCRTC presentation, honest non-master failure) |
| Benchmarks | **benchmarks_full.py** + **benchmarks_software.py** (all display paths) |
| Compositor presentation | **compositor_presentation.py** (DRM-detect → DRMBackend attach → software fallback; frame lifecycle stats) |
| Package repository | **package_repo.py** (signed index, publish/verify/download) + **nyrqisctl_repo.py** CLI |

## Session 8 (2026-09-12) — LIVE ISO GREEN: the click-and-open demo ships; design-language phase 2.3b

| Item | Status |
|------|--------|
| **live-iso CI green** | ✅ Run 34691487383 on `97d0366`: build ✅, structure ✅, **boot smoke PASS** (`ready=True pong_ok=True pong_fail=False`), ISO artifact **241 MB** attached (expires 2026-10-12). The demo boots to systemd multi-user, autologs in as demo, the daemon answers ping on the serial console |
| The ten-round debugging chain | Each failure taught the diagnostic layer something: (1) guard `echo|grep -q` SIGPIPEd under pipefail → killed good initrds; (2) modprobe name `iso9660` ships as `isofs.ko` → guard accepted both spellings; (3) `unsquashfs -ls` prints `squashfs-root/` prefixes → content gate matched endings; (4) plain `debug` redirects init output into the VM → `debug=y` traces to console; (5) init-bottom pivot diagnostic proved the union populated except **`/sbin/init`** → root cause: `systemd-sysv` skipped by minbase (debootstrap includes fixed + build-time repair); (6) usr-merge keeps init at `usr/sbin/init` (no sbin/init ENTRY) → probe tries both; (7) PEP 701 f-string (3.12 host legal, 3.11 image SyntaxError) → build byte-compiles the tree with the image's own python; (8) tty1/serial console race for the daemon socket → smoke gates on the console. All gates stay in the build: they now prevent regressions |
| Rust compositor flake | ✅ `fresh_running_state()` left `running=true` in shared STATE → order-dependent `start()==-1`, and the panic poisoned TEST_LOCK (cascade of 4). Fixed: reset before start-expectations + poison-tolerant `crate::test_lock()`. Green across 1/2/4/8 threads (`af5a45b`) |
| Phase 2.3b — list/table group | ✅ Compositor List + MenuItem renderers tokenized (row pitch = `space.xl`, default 24 px, pixel-identical token-less) + selection contract (`selectedIndex`/`selected` accent highlight, radius.sm, contrast text, label position fixed). Package/process/network panels inherit. 4 new compositor tests (`b01cd27`) |
| Boot-smoke diagnostic capability | Full serial-log chunked `::error::` annotations (9×950 chars, tail-preserving) — CI failures are now self-describing via the API without auth; this is what cracked rounds 11-18 |
| Phase 2.3c — media/creative group + §7 closure | ✅ Assessment: the group's colors are DATA (paint palette = drawing colors, album art, calendar categories rendered as text metadata) — §7 binds chrome, which is already tokenized (2.1–2.3b). Landed: `Slider` reads `radius.sm` (pixel-identical via PIL clamp), `ProgressBar` gains `radius.control` (default 4 = historical); assessment pinned as tests incl. accents-never-in-palettes |
| **Release 0.29.1** | ✅ Tagged `v0.29.1` on `68ca9c4`: ci #454 ✅ + live-iso #25 ✅, ISO **published as a permanent release asset** ([nyrqis-live.iso](https://github.com/Myco-mycelium/Nythera/releases/download/v0.29.1/nyrqis-live.iso), 253,177,856 B, unauthenticated-download verified) on the [release page](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.1) |
| Pivot-diag false-MISSING fix | ✅ The v0.29.1 green run's serial log printed `MISSING: /root/sbin/init` on a successful boot: plain `-e` resolves usr-merge's absolute symlink against the initramfs root. Hook now resolves chains vs `rootmnt` (8-hop cap); verified vs usr-merge/dangling/loop layouts; fresh CI log shows `PRESENT` ×3 + `PONG=1`/`READY=1` (`0eedc66`, live-iso #26) |
| **Release 0.29.2** | ✅ Tagged `v0.29.2` on `c69649a`; **ISO-per-release automation verified end-to-end**: the live-iso run for the tag created the release page itself (`github-actions[bot]`) and attached the boot-smoked `nyrqis-live.iso` (241.5 MB, unauthenticated-download verified) — zero manual steps: [v0.29.2 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.2) |
| cornerRadius visual validation | ✅ Six-variant render strip: 0 = pixel-identical to default (30/30 corner-fill px), 4 squarer (36), 16 rounder (8), 64 clamps to 20 (1), junk → default (30) — semantics confirmed visually + numerically |
| NUI contract 1.0 → 1.1 | ✅ Phase-2 item 4 closed: `Button.cornerRadius` added registry-first (`versionHistory` + back-compat notes added to `nui-api-v1.json`); compositor honors per-instance override (0 = `radius.sm`, clamped); gate enforced per-type in Python + Rust; stale fixture registry copy re-synced with drift-guard test |
| Next: nui contract review | (closed — future property bumps follow the same versionHistory pattern) |
| cornerRadius real-shell experiment | ✅ Pill corners (64) injected on the desktop theme-switch buttons: gate-validated, compositor renders, 600 px delta confined exactly to btn_eclipse/btn_solar (x 16–352, y 160–200). **Recommendation: keep cornerRadius per-instance; do NOT restyle default-shell buttons** — the stock `radius.sm=8` matches the §2.2 token table and the dock/app-grid language; pills stay a designer's opt-in via Nyforge |
| **Release 0.29.3** | ✅ Tagged `v0.29.3` on `39ee537`; ISO auto-attached again ([v0.29.3 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.3), 241.5 MB). Boot-smoke serial log now proves the wrappers on the real image: `NYRQIS_BOOT_SMOKE_NYRQISCTL=1` alongside `PONG=1`/`READY=1` |
| Pill-variant shell | ✅ `shell/variants/pill.nstudio` — every Button a 64-px pill, generated as a pure restyle (structure-identity pinned), gate-validated, rendered on both themes; 6 new tests (`test_shell_pill_variant`) |
| Live-image entry points | ✅ Build now writes thin PATH wrappers (`nyrqisctl`, `nyrqis-backend`, `nyrqis-session`, `nyrqis-run`, `nyrqis-init`) — closes the probe's PATH/entry-point advisories; proven on the real image by the boot-smoke marker |
| Next plan cycle | Design-language phase 2 + registry/Nyforge contract work closed — every candidate shipped, including persistence. (a) real-hardware probe results → MISSING work items: THE standing item, needs a hardware run. Open next-thread candidates: (none — all in-repo candidates shipped) |
| Native Nyforge Contract panel (cross-repo) | ✅ Authorized + shipped in the sibling `Nyforge` repo (`bffb371`+`6bbbbf2`, CI green Win+Linux): Core `InspectorPreflight` parser (never throws — malformed payloads parse to Ok=false) with 6 xUnit tests; `ContractPreflightService` runs `nyforge_live --inspect-json` (stable-anchor script discovery, 20s timeout, honest failure states); `ContractPanelViewModel` + Contract tab refreshed on save/open/new; FEATURE_STATUS/README/ROADMAP gates updated together. Nyforge renders the verdict; classification authority stays in Nyrqis (differential-pinned Python==Rust). One CI-caught compile fix (missing using) — remote-compile verification worked exactly as designed |
| Per-screen variant mixing | ✅ `ui.variant_mix`: compose a shell document from several variants, screens as the unit (both shipped trees share screen structure — the pill is a pure restyle). Honest header contract: `requiresRegistry` = union over what the RESULT contains (zero-screen mixes inherit nothing); schema-mismatch/unknown-screen/structure refusals; differential-pinned (Rust==Python on the mixed doc); real headless boot through the daemon green (37 components, 13 behaviors, 1440×900 frame). 8 new tests (6,293 OK) |
| In-editor Inspector side panel | ✅ Within this repo's boundary: `nyforge_live --side-panel out.png` — document preview + verdict panel composed side by side (`compose_inspector_view`), honest degradation to panel-alone when the doc won't import, watch-mode live re-render on accepted reloads. The *native* C# Nyforge panel lives in the sibling `Nyforge` repo (outside this project) — recorded as a cross-repo item for the user |
| Rust/FFI parity for inspect_version | ✅ `inspect_version_report` in the nyui crate (JSON via two-call out-buffer FFI, `nstudio_codec.inspect_version_rust`, malformed JSON → `NstudioValidationError`), differential-tested against the Python bridge on the shared verdict keys. The differential suite caught a real divergence on run #1: Rust sorted `notYetInRegistry` lexicographically and kept duplicates — fixed to mirror Python's dotted-numeric total order + dedup. 9 tests (6,278 OK) + Rust 26/26; skips cleanly when the crate is unavailable |
| NYRQIS name expansion | ✅ *Nexus for Your Resilient Quantum-ready Integrated System* — canonical in NPC-006 v1.3.0 + printed under the live-demo banner |
| **Release 0.29.4** | ✅ Tagged `v0.29.4` (`b990607`); ISO auto-attached ([v0.29.4 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.4)) — ships the full-name banner + validated `versionHistory` |
| Nyforge Inspector preflight | ✅ `NyforgeBridge.inspect_version` reports a document's contract situation against `versionHistory` WITHOUT importing: schema support, `requiresRegistry` header, renderable change span, honest `anyDropped` verdict; losses classified (`notYetInRegistry` nameable incl. cleanly-newer unknowns via dotted-numeric compare, junk → `unknownDocRequirements`); exposed as `nyforge_live --inspect`; 14 new tests + Rust `VersionHistoryEntry` consistency (22/22) |
| 3.11 syntax gate fires for real | ✅ The Inspector commit's error message used a 3.12-only nested-quote f-string; `live-iso` failed fast in "Build the ISO" (runs #36–#38, ~2 min) instead of shipping a dead demo session — the gate's exact purpose. Fixed 3.11-safe; the two earlier failures (#34 attach-step, #36/#37 flake hypothesis) were me misreading partial timelines before the annotations settled it |
| Registry log baseline | ✅ `versionHistory` gained the 1.0 entry; loader enforces newest-entry == `registryVersion` (both drift polarities pinned, fail-closed); doc-header contract: documents may declare `requiresRegistry` — pill variant ships `["1.1"]` |
| Inspector "will lose" UX | ✅ Full-contract-context fix: a doc requiring a future-only registry now shows this build's ENTIRE `versionHistory` span (not an empty/truncated one) before the "WOULD DROP" verdict — decision context always present; pinned by `test_future_only_document_gets_full_change_context` (6,242 OK) |
| CI 3.11 syntax job | ✅ New `py311-syntax` ci job compiles the shipped tree under real Python 3.11 at unit-test time (the class of bug that cost three tag roundtrips now fails in seconds, not after a live-image build); the image-build gate stays authoritative |
| Host probe re-run | ✅ `nyrqis-demo --probe-only`: nothing MISSING — all criticals green; advisories are by-design (kvm), documented fallback (Rust → Python floor), or dev-host install notes the live image already closed |
| Runtime-selectable shell variants | ✅ `nyrqis_init --variant pill` / `NYRQIS_SHELL_VARIANT=pill` boots the pill reference design; resolution: explicit `--design` > named variant (`shell/variants/<stem>.nstudio`) > stock; unknown names = hard CLI error; pill file renamed `desktop-pill.nstudio` → `pill.nstudio` for the drop-in convention |
| Inspector panel | ✅ `ui/inspector_panel.py`: renders `inspect_version` reports as real UI — gate-valid NUI document (compositor-rendered on Eclipse/Solar) + plain RGBA render with pinned content parity; verdict gets a solid status badge (accent/red); CLI `python3 -m ui.inspector_panel <doc>`; 13 new tests (6,256 OK) |
| Stale-cdylib trap (real-boot find) | ✅ A headless `--variant pill` boot failed in the daemon: the Rust gate's `include_str!` registry is frozen at build time and the host's cdylib was 16 days old (registry 1.0) — it rejected registry-1.1 `cornerRadius` while the Python floor accepted it. Fixed: `nyrqis_nyui_registry_version[_len]` FFI symbols + a parity check in `nstudio_codec._load` that disables the crate on ANY mismatch (either direction, fail-closed incl. symbol-less crates); rebuilt cdylib; pill boots green end-to-end |
| Safe hot-reload | ✅ `NyforgeBridge.refresh()` preflights (Inspector verdict + gate) BEFORE teardown — a broken edit is rejected with the session kept and verdict attached (`reload_rejected` event); valid edits swap with the verdict on the result. 3 polarities pinned (17/17 in `test_nyforge_inspect`); suite 6,259 OK |
| **Release 0.29.5** | ✅ Tagged `v0.29.5` on `2788bd7`; ISO auto-attached ([v0.29.5 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.5), 241.5 MB, unauthenticated fetch ✓). Ships: runtime shell variants, Inspector panel, safe hot-reload, registry parity check |
| --watch end-to-end demo | ✅ Broke a document live under hot-reload: session survived (windows intact), `reload_rejected` fired; valid edit reloaded with the verdict. Rough edge found + fixed: persistent broken files spammed the callback every poll → rejections are now **transition-driven** (once per edit, cached verdict on re-polls); pinned (18/18 `test_nyforge_inspect`, 6,260 OK) |
| ISO boot-menu pill entry | ✅ GRUB + isolinux menus gained a "pill shell" entry (`nyrqis.variant=pill` on the kernel cmdline); the demo session parses it → exports `NYRQIS_SHELL_VARIANT` (`--design` still wins). Cmdline parse verified both polarities; ships in the next tagged image |
| Hot-swap into the live session | ✅ `NyforgeBridge.swap_document(path)`: preflight before teardown (target validated via Inspector verdict + gate; broken target keeps the session and names both docs); watch path follows the swap; `swap`/`swap_failed` events. Try-the-pill-without-restart is now a single call. 4 polarities pinned (22/22 `test_nyforge_inspect`, 6,264 OK) |
| Settings-panel variant picker | ✅ "Shell Variant" section ([V] cycles): request/consume/report protocol — the panel requests, the session consumes + calls `swap_document`, the panel records the result via `apply_swap_result` ("pill active" or the honest rejection; active never changes on a rejected swap); cycling onto the running variant cancels a pending swap. 4 new tests (58/58 `test_settings_tiling`, 6,268 OK) |
| Variant persistence | ✅ Found the loop broken: `nui_load`/swaps persisted to `<state_dir>/ui/shell.nstudio` (pinned by backend tests, referenced by nyrqisctl help) while `nyrqis_init` searched `<state_dir>/shell.nstudio` — a remembered design could never be found. Init now searches the daemon's real persist location: resolution = explicit `--design` > named variant > remembered > shipped trees; swap results now carry `doc_path` (the picker's key, previously hand-supplied by my smoke test). Pill swap in session N → pill boots in session N+1 |
| **Release 0.29.8** | ✅ Tagged `v0.29.8` on `bfed592`; ISO auto-attached ([v0.29.8 release](https://github.com/Myco-mycelium/Nythera/releases/tag/v0.29.8), 241.5 MB, unauthenticated fetch ✓). Ships: the in-editor Inspector side panel and Rust/FFI inspect parity (both engines now issue identical preflight verdicts) |

## Session 7 (2026-09-11) — release 0.29.0; design-language rollout phase 2.1 (compositor tokens)

| Item | Status |
|------|--------|
| Release 0.29.0 | ✅ Committed, tagged `v0.29.0`, pushed (main + tag); `live-iso` CI kicked off on the push |
| Phase 2.1 — compositor token layer | ✅ `NstudioDocument.design_tokens` (loader preserves `designTokens`; older loaders tolerate it) + `DESIGN_TOKENS` defaults in `ui/compositor`; merge at render time is **opt-in** — no tokens → pixel-identical render; radius applies to buttons/window frames, `surface.translucency` drives real alpha-blended taskbar translucency (crop-blend-paste); THEMES key sets unchanged (pinned by tests). 3 new tests (opt-in, radius merge, cross-document isolation); 27/27 compositor, 12/12 suites |
| Phase 2.2 — desktop.nstudio restyle | ✅ `shell/defaults/desktop.nstudio` carries the designTokens vocabulary (bar 0.85 alpha, radius tokens, 44-px targets under a 56-px bar, paired start-menu motion); ids/behaviors/bindings/locales preserved; fixture copy untouched. Verified through the real loader + token compositor (both screens render); 83 affected tests + 12/12 suites green |
| Next: phase 2.3 | The 16 finished apps, per the spec §7 adoption checklist, grouped: form-heavy → list/table → media/creative |
| CI first-real-build fixes | ✅ `live-iso` boot smoke could never succeed (no smoke flag on any kernel cmdline, no boot-entry selection) — now boots the ISO's kernel/initrd directly with `-append …NYRQIS_BOOT_SMOKE=1`; marker parse fixed after early qemu exit (4 paths verified vs a QEMU stand-in). Bench gate §31b de-corpus-ized (host-relative lz4 throughput + losslessness); §31a aggregate best-of-3 timing. Committed + pushed (`8cc98ed`) |
| CI re-run on fix commit | ❌ `8cc98ed` failed in the same two steps — which isolated the residual defects: (1) the image's autologin console stopped at a bare bash shell (no `.bash_profile` hook → no session → no markers, ever); (2) §31a's absolute bounds (flat < 5%, ≥ 20x) are properties of the recording host's real-corpus mix — the deterministic corpus legitimately measures 25% gain at l22 and 11–13x speed |
| Phase 2.3a — form-heavy surfaces | ✅ `ui/hig.py` token bridge (space/radius/target/motion/accent/surfaces = the .nstudio + compositor values, agreement-tested); settings panel retuned to brand tokens + 44-px rows (theme band 184 px); Dracula + generic Theme default untouched. New `tests/test_design_language.py` in `run_tests.sh` (13/13) |
| Phase 2.3b — list/table group | ✅ Compositor List + MenuItem renderers tokenized (row pitch = `space.xl` default 24 px, pixel-identical token-less) + selection contract: `selectedIndex`/`selected` accent highlight (radius.sm), contrast text, label position fixed. Package/process/network panels inherit at the compositor layer. 4 new compositor tests; suite 13/13 |
| Phase 2.3a remaining | Calendar/password string-renderer apps carry category colors (data, not chrome) — assess against §7 contrast rules; list/table + media/creative groups next |
| Second CI fix round | ✅ Build writes the demo `.bash_profile` (session owns every autologin console); smoke branch polls ping 30 s and always lands READY; driver prints the tail on every failure incl. `--keep-logs`; §31a re-derivable bounds (deterministic corpus: monotonic ratio, ≤1.35x same storage class at l22, ≥5x speed class; real-asset claim ≤1.5x host-sampled). 4 smoke paths re-verified vs fake QEMU; gate 10/10 local |

## Session 6 (2026-09-11) — ADR rot gates; live-demo ISO; design language; ep-limits loop-dispatch fix

| Item | Status |
|------|--------|
| Benchmark rot gates, all three close-out records | ✅ `tools/benchmark_gate.py` grew ADR-0007 (§31: ratio-curve flat, LZ4 fast-path premise, lossless round-trips) and ADR-0013 (§33: exact EEVDF re-derivation — request-size law, 10:1 share accuracy, RT-starvation row) alongside the existing ADR-0009 checks; 10 invariants, `--only adrNNNN` selector; CI benchmarks job + `run_tests.sh` |
| Real-daemon ep-limits bug found + fixed | ✅ The limiter ops resolved the manager off the attached server, but the Rust serving loop's dispatch handoff attaches services to a reply SINK — every `ep-limits` op failed on the packaged daemon while all unit tests (floor path) passed. `ControlService` takes an explicit `ipc_manager`; regression tests pin the sink wiring + a CLI-against-real-daemon e2e |
| `ep-limits metrics --watch` verified e2e | ✅ First real-daemon exercise: banner renders, Ctrl-C exits 0 |
| Design language | ✅ `docs/reference/design-language.md` (HIG structure + Material feel: tokens, motion vocabulary mapped to the NUI easing enum, 44 px targets, the Android-feel boundary); `shell/defaults/default-shell.nstudio` restyled as the reference implementation (designTokens, theme overrides, contract-valid properties only, paired enter/exit menu motion) |
| Live-demo ISO | ✅ `packaging/live/` — `build-live-iso.sh` (debootstrap/tarball/tree → zstd squashfs → hybrid UEFI+BIOS ISO), autologin `demo` user, `nyrqis-demo` session (daemon up, desktop attempt, capability probe printing exactly what the booted machine is missing); `live-iso` CI workflow builds + smoke-checks + publishes the artifact |
| Boot smoke (headless QEMU) | ✅ `tests/boot_smoke.py` — serial-console handshake (`NYRQIS_BOOT_SMOKE_PONG=1` = daemon answers ping); wired into CI with serial-log upload; pass/pong-fail/no-marker paths verified against a simulated QEMU. **Unverified: the first real chrooted build + boot (no sudo/xorriso/KVM on the dev host) — CI closes it** |
| Suite | ✅ 2,633 backend tests OK (full), 12/12 run_tests.sh suites |

### Carry-forward: design-language rollout (phase 2)

The spec + reference shell are done; the rest of the surface area is
not. Phased so each phase ships green independently:

1. **Compositor theme layer** — carry the tokens (spacing/radius/elevation/accent-as-action) into `ui/compositor`'s Eclipse/Solar renderers so `.nstudio` designs get token-driven polish for free; degrade per the renderer-honesty rule.
2. **`desktop.nstudio` (full shell)** — restyle the 30-component desktop: Dock + AppGrid + Launcher + window chrome to the §2/§4 tokens; note the tests/fixtures copy is pinned by schema tests and stays untouched.
3. **The 16 finished apps** — sweep per the spec §7 adoption checklist, grouped: form-heavy (settings, calendar, contacts-like) → list/table (package, process, network) → media/creative (paint, recorder, model viewer). Each group lands with its suite green.
4. **Contract additions where the language needs new properties** (e.g. `cornerRadius` on Button) — a deliberate `nui-api-v1.json` registry bump with back-compat notes, NOT ad-hoc properties; that is its own review.

## Session 5 (2026-09-10, later) — benchmark close-outs unblock 3 held ADRs; version-drift gate; vendor-agnostic GPU conformance

| Item | Status |
|------|--------|
| Version-drift CI gate | ✅ `tools/check_version_drift.py` — pyproject version must equal the newest CHANGELOG release (the 0.22.0-through-4-releases drift can't recur); new `version-drift` CI job |
| ADR-0007 close-out data | ✅ `tests/benchmark_adr0007.py` — real-asset zstd sweep (ratio flat ~1.07 at every level), real LZ4 fast path (2.7× zstd-1 at equal ratio; zlib approximation retired), concurrent scaling (2.2× @ 8 threads). BENCHMARK_RESULTS §31 |
| ADR-0009 close-out data | ✅ `tests/benchmark_bucket.py` — sweep (steady state ≈ refill; shipped default = 4.5% of capacity) + adversarial (shared bucket starves a 250 Hz client under flood → per-sender fairness is the missing mechanism). BENCHMARK_RESULTS §32 |
| ADR-0013 tuning data | ✅ `tests/benchmark_adr0013.py` — EEVDF discrete-event simulation: request size (not a slice knob) governs interactive latency, Linux-6.6 weight table recommended, RT reserve shown non-optional (100% RT starves fair class). BENCHMARK_RESULTS §33; BENCHMARK_PLAN §5 method |
| Vulkan FFI slot-leak fix | ✅ Found by the new conformance harness via test_boot_integration: the crate's destroy functions left `Some(..)` in the slot tables while `alloc_slot` only reuses `None` — 4 create/destroy cycles exhausted instances forever. All three destroys now `take()` the slot; crate tests 12 green |
| DRM driver identification | ✅ `query_driver()` (DRM_IOCTL_VERSION, native 64-byte layout) — identifies i915 1.6.0 "Intel Graphics" on this host; the vendor-matrix primitive |
| Vendor-agnostic GPU conformance | ✅ `tests/test_gpu_vendor_conformance.py` (9 tests) — identical assertions regardless of driver + matrix-row report; the suite AMD/NVIDIA hosts will run unchanged (M15 Phase 2 instrument; no AMD/NVIDIA hardware on this host, honestly skipped) |
| Suite | ✅ 6,139 passed + 38 skipped |

## Session 4 (2026-09-10) — v0.28.0 released: UI apps to spec, presentation + package repo, real DRM UAPI

### v0.28.0 Release (Tagged, GitHub release published)
| Milestone | Status |
|-----------|--------|
| 16 UI apps brought to test spec | ✅ packet_analyzer, virtual_keyboard, disk_health, calendar_app, markdown_editor, network_monitor, password_manager, screen_recorder, audio_mixer, font_manager + their test groups (6,133 Python tests passing, up from 2,532) |
| Compositor presentation half | ✅ `ui/compositor_presentation.py` — DRM-detect, DRMBackend attach, honest software fallback, frame lifecycle stats; end-to-end test suite |
| Package repository | ✅ `backend/package_repo.py` (signed index, publish/verify/download) + `nyrqisctl_repo.py` CLI |
| Rust compositor restart fix | ✅ `nyrqis_compositor_start` now tears down previous-session clients/surfaces/outputs (outputs previously accumulated until MAX_OUTPUTS exhausted in any long-lived process) |
| DRM backend rewritten to real UAPI | ✅ Query ioctls now use the kernel's two-call protocol with pointer arrays and correct struct-size ioctl numbers; presentation allocates a dumb buffer + ADDFB2 before SETCRTC (fb_id=0 would have disabled scanout); fails closed without DRM master |
| On-host hardware verification | ✅ `verify_presentation.py` (read-only DRM probe, byte-exact SHM compositing check, DRM-path posture; wired into `run_tests.sh --gpu`). On `/dev/dri/card1`: 2 CRTCs, 3 connectors, 3 encoders, connector 64 enumerated with 5 real modes, kernel EPERM without master handled honestly |
| Docs + CI | ✅ CI green on both pushes (29 jobs incl. rust-compositor + compositor-host gates); v0.28.0 annotated tag + GitHub release |

## Session 3 (2026-09-08) — CI green + compositor wire event loop

### First Green CI on GitHub Runners
| Item | Status |
|------|--------|
| rust/wayland ABI test | ✅ Fixed: asserted 0x0001_0100 after the multi-monitor bump to 0x0001_0200 (crate never compiled on the dev host — CI was the only compiler) |
| Container conformance gate | ✅ Fixed: `test_app_launch_creates_container` did a real namespace spawn without the `_direct_launch_supported()` probe guard; GitHub-runner kernels block the uid_map write (`root map write: Operation not permitted`), so the job had never passed there. Now skips like the PID-1-init tests. |
| Result | ✅ First green CI run in repo history (prior 568 runs red); push 6c6a48d |

### Compositor Wire-Format Event Loop (ABI 0.1.0 → 0.2.0)
| Item | Status |
|------|--------|
| `rust/compositor/src/event_loop.rs` | ✅ Standard Wayland wire-format request parser (object table, implicit wl_display id 1), dispatch of get_registry / bind / create_surface / attach / frame / commit into the crate state machine |
| Server→client events | ✅ `wl_registry.global` ×5, one-shot `wl_callback.done` on commit (stamped with commit count), `wl_buffer.release` retirement |
| FFI + Python | ✅ `handle_client_data` / `next_event` / `object_count` / `event_loop_last_error`; `ui/compositor_codec.py` ABI gate 0x0000_0200 |
| End-to-end | ✅ Python client → 84-byte wire stream → 5 globals + frame-done; surface lands in state machine with commit_count 1 |
| Tests | ✅ Compositor crate 37 → **47** (59 stable runs; crate-wide test lock unified) |

### Codec Stub Audit (gbm/drm/egl/vulkan/wayland)
| Item | Status |
|------|--------|
| Fail-closed posture | ✅ Already consistent: stub modes return honest failure sentinels (−1/False/None, version 0), `is_available()` truthful, callers fall back — no changes needed |

## Post-Plan Session (2026-09-08)

### Auto-Remediation Actions Completed
| Action | Status |
|--------|--------|
| `throttle` | ✅ Real: cgroups v2 cpu.weight −25% (floor 1), `nice` +5 fallback on v1, honest failure reporting |
| `migrate` | ✅ Real: checkpoint (stats + limits snapshot, `ckpt-` id) + terminate; restore = later spawn |
| Tests | ✅ 3 new (suite 2619 → **2622**) |

### Package Signing Fail-Closed (NPS-026 §6)
| Item | Status |
|------|--------|
| Full NPS-026 API restored | ✅ `SigningKeypair`/`sign_package`/`verify_package`/`PackageSignature`/`TrustStore` (a rewrite had dropped them; installer import was broken) |
| Forgeable stubs removed | ✅ Without PyNaCl everything raises `PackageSignError` — no deterministic keys, no hash "signatures" |
| `update_signing.py` real Ed25519 | ✅ Full/delta/rollback verify the signature itself; `re_sign_update` actually signs; forged 64-byte signature rejected (new test) |

### Compositor Stubs Implemented (rust/compositor)
| Item | Status |
|------|--------|
| `process_input` | ✅ Per-surface bounded queues (256, oldest-dropped) + global dispatch counter |
| `send_frame_callback` | ✅ Records delivery timestamp per surface |
| `commit_surface` | ✅ Per-surface commit counts; callback delivered on first commit |
| Introspection FFI | ✅ `input_queue_depth` / `total_input_dispatched` / `commit_count` / `last_frame_time` in `ui/compositor_codec.py` |
| Test stability | ✅ Parallel-harness interleaving fixed with a test lock; 37 tests, 12/12 clean runs |

### Live Installer
| Item | Status |
|------|--------|
| Completion summary bug | ✅ Dead-code condition fixed (summary now prints after install) |
| GUI narrow-width crash | ✅ `render_user_setup` side panel clamped (PIL ValueError at <1280px) |
| Smoke tests | ✅ New `tests/test_live_installer_smoke.py` (6 tests): all 16 steps registered, text mode completes, GUI renders all 15 screens |

## What's Been Completed This Session

### v0.23.0 Release (Tagged)
| Milestone | Status |
|-----------|--------|
| Python packaging (pyproject.toml) | ✅ 5 CLI entry points |
| Systemd integration | ✅ Backend daemon + desktop session units |
| Install script | ✅ System/user/dev modes |
| Unified init script | ✅ nyrqis_init.py boots daemon → shell → session |
| Compositor FFI | ✅ ui/compositor_codec.py with ABI gate |
| Default shell designs | ✅ shell/defaults/default-shell.nstudio + desktop.nstudio |
| GBM real hardware | ✅ Device → surface → buffer on Intel HD Graphics |
| DRM device auto-detect | ✅ Tries card0, card1, renderD128 |
| DRM ioctl fix | ✅ Corrected MODE_GETRESOURCES size (60 bytes) |
| Entry point tests | ✅ 8 new tests |
| Boot integration tests | ✅ 24 tests |

### Post v0.23.0 Session (19 commits)
| Milestone | Status |
|-----------|--------|
| Wayland compositor protocols | ✅ XDG shell, frame callbacks, output geometry, seat capabilities |
| Delta update signing | ✅ Full/delta/rollback verification |
| Init diagnostics | ✅ `nyrqis_init --diagnose` with 7 system checks |
| udev rules | ✅ DRM device access without root |
| GPU benchmarks | ✅ GBM/EGL/Vulkan/Compositor performance |
| nyrqis-ctl wrapper | ✅ Convenience CLI |
| GPU pipeline tests | ✅ 21 integration tests on real hardware |
| **EGL real hardware** | ✅ Real eglInitialize/eglChooseConfig/eglCreateContext via dlopen |
| **Vulkan real hardware** | ✅ Real vkCreateInstance via dlopen |
| **Render pipeline** | ✅ GBM + EGL + DRM connected |
| **Multi-monitor** | ✅ Output detection, workspace binding, window migration |
| **SHM buffer sharing** | ✅ memfd_create + mmap for Wayland surface content |
| **Wayland socket server** | ✅ Unix domain socket for client connections |
| **Wayland protocol codec** | ✅ Encoder/decoder for wire format messages |
| **DRM/KMS backend** | ✅ Connector detection + atomic modesetting |
| **Integrated compositor** | ✅ nyrqis_compositor.py combining all pieces |
| **E2E compositor tests** | ✅ 10 tests with mock Wayland client |
| **Software benchmarks** | ✅ PIL + raw pixel + SHM buffer baselines |
| **CHANGELOG.md** | ✅ Documents v0.14.0 through v0.23.0 |
| **getting-started.md** | ✅ Quick start, architecture, CLI, GPU, testing |
| **Release tag** | ✅ v0.23.0 tagged and pushed |

## What's Left

### Priority 1: Test with Real Hardware (COMPLETE)

**Status**: ✅ All verified on Intel HD Graphics
- GBM: device → surface → buffer (1920x1080 ARGB8888)
- DRM: device open with auto-detection
- EGL: display → config → context → make_current → swap_buffers
- Vulkan: instance → device → swapchain → acquire image

### Priority 2: Real GBM/DRM/EGL/Vulkan Integration (COMPLETE)

**Status**: ✅ All wired to real hardware
- GBM: Real gbm_create_device() and gbm_surface_create() via dlopen
- EGL: Real eglInitialize(), eglChooseConfig(), eglCreateContext() via dlopen
- Vulkan: Real vkCreateInstance() via dlopen
- DRM: Fixed ioctl number, auto-detect device paths

### Priority 3: Custom Wayland Compositor (COMPLETE)

**Status**: ✅ All implemented
- [x] wl_compositor, wl_shm, xdg_wm_base protocols
- [x] Input handling (wl_seat, wl_keyboard, wl_pointer)
- [x] Output management (wl_output)
- [x] Frame callbacks (wl_callback)
- [x] DRM/KMS backend for display output
- [x] Real Wayland socket for client connections
- [x] Surface buffer sharing via shared memory

### Priority 4: Package Update Signing (COMPLETE)

**Status**: ✅ Implemented
- Delta update signature verification
- Re-signing after local modifications
- Rollback signature validation

### Priority 5: Multi-Monitor Enhancements (COMPLETE)

**Status**: ✅ All implemented
- [x] Output-specific surface creation
- [x] Multi-surface rendering pipeline
- [x] Workspace-to-output binding
- [x] Window migration on output removal
- [x] Output hot-plug event handling (HotPlugMonitor with periodic DRM polling)

### Priority 6: Performance Benchmarks (COMPLETE)

**Status**: ✅ All display paths measured
- [x] GBM/EGL/Vulkan/Compositor performance metrics
- [x] Software rendering (PIL) baseline
- [x] Raw pixel operations baseline
- [x] SHM buffer operations baseline
- [x] SDL2 headless rendering (via sdl2_codec.py)

## Session 4 (2026-09-09) — Socket host half + delta generation

### Compositor Socket Host Half (M14 follow-on closed)
| Item | Status |
|------|--------|
| `ui/compositor_host.py` (`CompositorHost`) | ✅ Bridges `WaylandSocketServer` bytes ↔ Rust wire event loop via `compositor_codec`; drains response events back to the socket |
| Partial-message reassembly | ✅ Feeds the crate on message boundaries only — `recv()` fragmentation never becomes a truncated request |
| Single-dispatch ownership | ✅ Wire loop owns protocol dispatch when wired; legacy Python dispatch skipped (it double-responded); stub mode fabricates nothing (fail-closed) |
| Session-scoped protocol state (Rust) | ✅ `start`/`stop` reset the object table + queues — a restart no longer collides with stale object ids; `wl_display.sync` served |
| `NyrqisCompositor` wiring | ✅ Automatic bridge + `get_stats()` host counters (engine, bytes in/out, events, protocol errors) |
| Tests | ✅ `tests/test_compositor_host.py` (8): real-socket handshake, reassembly, per-client isolation, disconnect cleanup, stub fallback; CI `compositor-host` required gate |
| CI gap closed | ✅ `rust-compositor` job added — the 48-test crate had **never been compiled in CI** |

### Delta Update Generation (NPS-026 §6 generation half)
| Item | Status |
|------|--------|
| `backend/delta_update.py` | ✅ `diff_packages` (add/modify/remove, deterministic, `.nypkg`-normalized) + `create_delta_update` (canonical checksum, optional Ed25519) |
| `apply_delta_update` | ✅ Signature verified BEFORE filesystem mutation; per-op path-traversal guard; unsigned refused when a trust store is supplied |
| Cross-verified with the shipped verifier | ✅ Generated deltas pass `UpdateVerifier.verify_delta_update` unmodified; tampered op lists fail (`TestDeltaPassesShippedVerifier`) |
| Tests | ✅ `tests/test_delta_update.py` (18) |

## Next Priorities

### Priority 7: Real Hardware Testing (Week 1)
- Test on AMD Radeon GPU
- Test on NVIDIA (Nouveau driver)
- Test on ARM Mali GPU (Raspberry Pi)

### Priority 8: Wayland Client Compatibility (Week 2-3)
- Test with weston-simple-shm
- Test with weston-terminal
- Test with GTK4 applications
- Test with Qt6 applications

### Priority 9: Package Manager Integration (Week 4)
- Package signing with Ed25519 keys
- Delta update generation
- Repository management

### Priority 10: Desktop Environment (Week 5-6)
- Window manager integration
- Taskbar and system tray
- File manager
- Terminal emulator

## Timeline

| Week | Priority | Deliverable |
|------|----------|-------------|
| 1 | 1 | ✅ Test with real hardware (COMPLETE) |
| 1 | Packaging | ✅ pyproject.toml, systemd, install script (COMPLETE) |
| 1 | Init | ✅ nyrqis_init.py boot-to-desktop (COMPLETE) |
| 1 | GPU | ✅ GBM/DRM/EGL/Vulkan verified on hardware (COMPLETE) |
| 1-2 | 2 | ✅ Real GBM/DRM/EGL/Vulkan integration (COMPLETE) |
| 2-3 | Render | ✅ Render pipeline + multi-monitor (COMPLETE) |
| 3-4 | 3 | ✅ Custom Wayland compositor (COMPLETE) |
| 4-5 | 4 | ✅ Package update signing (COMPLETE) |
| 5-6 | 5 | ✅ Multi-monitor enhancements (COMPLETE) |
| 6-7 | 6 | ✅ Performance benchmarks (COMPLETE) |
| 8-9 | 7 | Real hardware testing |
| 10-12 | 8 | Wayland client compatibility |
| 13-14 | 9 | Package manager integration |
| 15-17 | 10 | Desktop environment |

## Success Criteria

| Metric | Target | Current |
|--------|--------|---------|
| Tests passing | 6,200+ | **6,133** (Python) + 275 (Rust crates) |
| GPU rendering | GBM/EGL/DRM/Vulkan path working on real hardware | ✅ Verified |
| Packaging | pip install + systemd | ✅ Implemented |
| Boot-to-desktop | nyrqis_init.py works end-to-end | ✅ Verified |
| Package signing | Update signing verified | ✅ Implemented |
| Multi-monitor | Per-output rendering working | ✅ Implemented |
| Benchmarks | All display paths measured | ✅ Implemented |
| Custom compositor | Automated CI testing | ✅ Implemented |
| Wayland clients | weston-simple-shm working | ✅ Verified (real upstream client, full frame loop, zero protocol errors — dev host; hardware/QEMU boot smoke remains the shipped-image proof) — **and weston-terminal (GTK class), pinned in CI (Session 11b)** |

## References

- ADR-0010: Vulkan as native graphics API
- ADR-0020: Implementation languages and the platform boundary
- ADR-0026: Wayland display-server integration
- NPS-017: NyHAL Kernel Abstraction Layer
- NPS-026: Package signing (§6)
- M14 Plan: `00-platform/M14_PLAN.md`
- M15 Plan: `00-platform/M15_PLAN.md`
