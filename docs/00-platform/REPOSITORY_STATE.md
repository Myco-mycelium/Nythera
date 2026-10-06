# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nyrqis repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-10-06, 0.29.41: the ISO's missing real-hardware boot components
shipped — and proven end-to-end on the tagged release. Every boot
smoke had been passing because QEMU's virtio/bochs devices need NO
firmware — a real AMD/Intel/NVIDIA GPU machine would black-screen
and Wi-Fi/Realtek NICs would never come up, because debootstrap
defaults to main only and the image carried zero non-free firmware.
The builder, rootless driver and both CI workflows now debootstrap
with --components=main,contrib,non-free,non-free-firmware and
include firmware-linux-nonfree, firmware-misc-nonfree,
firmware-linux-free, firmware-linux (all Architecture: all, one set
for both arches) plus amd64-microcode + intel-microcode on amd64
(early microcode rides the regenerated initrd); the chroot top-up
runs on EVERY rootfs acquisition path and a fail-closed gate refuses
an unequipped rootfs; ALL FIVE rootfs cache keys bumped v1→v2 (the
rootless workflow's own nyrqis-rootless-rootfs-* keys were found the
hard way in CI: the v1 hit restored a main-only rootfs whose stamp
made acquisition a no-op and the new firmware gate refused it —
fixed in 16ba0f8 with its own pin). Same round: NyFS at-rest GC
finally wired — gc_blocks() was implemented, §27-audited and pinned
but had no production caller, so CoW orphans persisted at rest
forever; the idle watcher now GCs every compaction interval
(auto_gc=True), unmount()/shutdown() run a final pass after their
save, and the new gc_grace_seconds (default 3600) makes age — not
mere unreferenced-ness — the at-rest safety criterion (0 restores
the old immediate-reclaim semantics, which the pre-existing pins now
state explicitly). 11 new pins (7 TestBootFirmwareContract + 4 GC);
boot+rootless contract suites 136 green; 2,555 + 6,711 tests green.
Size honesty: the firmware set added ~65 MB amd64 / ~44 MB arm64 (not
the ~10-15 MB first estimated) — measured 418.7 MB amd64 / 394.8 MB
arm64 on the shipped assets; ceiling 500→600 MB with the real
numbers recorded. Verification: all five CI runs on the tag SHA
green (tag-amd64 ~15 min via the push-populated v2 cache; tag-arm64
~45 min on the emulated v2 miss), release assets downloaded and
sha256-matched against the GitHub API digests, and BOTH release
smokes PASS (direct amd64 + menu arm64). Drill: steps 1-3 re-proven
(identity, repo 200, dispatch 204); Issues:write still 403 — the
six-step drill pass remains the one owner decision.
2026-10-05, 0.29.40: the §36 read anomaly was ROOT-CAUSED and FIXED
(same-day). cProfile put ~98% of the ON-leg read time in
_normalize_blocks' uniformity check — it ran on EVERY read/write and
decompressed (originally) / SHA256-hashed (after the first patch)
every block of the inode, so each 128 KiB kernel read paid the whole
file in codec work. Fix: _block_len is plaintext-length metadata only
(integrity stays at read time via the checksum), _decompress_verified
memoizes the verified plaintext of loaded blocks ONCE, and
NyFSBlock.decompress gained a checksum-verified plaintext fast path.
Encrypted volumes are handled: the AEAD envelope lives in block.data,
so neither the length shortcut nor the memoization may touch them
(pinned by test). Result: live-mount reads 0.89–0.91× of the
uncompressed leg (58.5 vs 64.2 MB/s 1 MiB), the 16 MiB ON-leg wedge
RESOLVED by the same fix (it was downstream O(file) pressure, not an
independent FUSE defect), and unmount(save=True) exposes the ADR-0019
dirty-gated commit (at-rest probe went live: 1,052,778 bytes for the
4 MiB pattern set). 4 regression pins (TestNyFSMetadataWalkCost);
2,552 pytest + 6,706 tests/ + canonical runner all exit 0. Tag
v0.29.40 pushed; both arch assets ship automatically. Drill status:
Issues:write still 403 — the six-step pass remains the one owner
decision.
2026-10-05, §36 + v0.29.39 verification round: BOTH v0.29.39 release
assets digest-matched and passed the direct boot smoke locally (all
gates green incl. CTL_PING; the menu path was CI-proven on these same
ISO bytes by the tag's dispatch-only menu job). BENCHMARK_PLAN §4 is
now fully measured: the compression ON/OFF live-mount A/B ran as §36
(--nyfs-mount-nocompress, identity-codec stub in the isolated child,
no product change) — writes 5.9×/20.6× slower with zstd-3, but the
ON-leg READ gap (~180×) is flagged as a compressed-read-path anomaly
needing a profile; the OFF leg streams 420 MB/s through the same
mount so FUSE itself is exonerated. Open repro recorded: the 16 MiB
ON leg wedged 4/4 (main thread D-state in request_wait_answer with
the fusepy daemon thread gone; SIGKILL leaves an unkillable corpse —
§19 class, root abort needed; 4 MiB completes clean 3/3 and the 16
MiB OFF leg is clean). Runner hardening shipped: parent scratch-dir
cleanup on ALL exit paths + parent-side at-rest probe (child-side
walked pre-commit and read ~0 — honest negative: NyFSMount.unmount()
does not save(); at-rest cost stays §7/§9's data). Roadmap's
four-benchmark pass is now fully measured. Commit 580e851 pushed;
ci+docs green (live-iso path-filtered out). Drill status unchanged:
Issues:write still 403 — the six-step pass remains the one owner
decision.
2026-10-05, v0.29.39 RELEASED (same-day follow-through): the
release-ISO verification matrix is COMPLETE — both v0.29.38 assets
boot green under BOTH smoke paths (direct + menu, amd64 + arm64,
4/4 PASS, CTL_PING gate green throughout). The staged drill re-ran
end to end: grants check passed, dispatch 204, run 37296270688
collected via the FIXED run-selection (first live exercise —
SUCCESS) before DRILL_STOP at the issue-close 403 as designed
(Issues:write still absent from the PAT — the only remaining owner
decision). 0.29.39 tagged and released: both arch assets shipped
AUTOMATICALLY from the tag trigger (no manual arm64 dispatch needed,
unlike v0.29.38), hashes amd64 a6bdfd77…b65b / arm64 2309d66a…c11f,
release body = boot-smoke summary. All five CI runs on the round
green: main-ci, main-live-iso, the drill's rootless run, tag-amd64,
tag-arm64. Menu-smoke evidence: ~/nyrqis-work/logs/menu-release-
{amd64,arm64}.log.
2026-10-05, release-ISO verification round: both v0.29.38 release
assets downloaded, SHA-256 matched GitHub's published digests
(amd64 e4d84e87…c377, arm64 5fa39083…5d08), and both booted GREEN
under the direct smoke incl. the CTL_PING wrapper-runs gate
(logs in ~/nyrqis-work/logs/smoke-release-{amd64,arm64}.log). Along
the way the pytest exit-1 mystery was ROOT-CAUSED and fixed: the
ERR-pipe child test ran _direct_launch_child(4, …) in-process and
left os.close real — the child's os.close(write_fd) killed pytest's
stdin-capture tmpfile fd (fd 4), pytest_unconfigure hit EBADF after
a fully green run. Found via a class bisect (only the five
direct_child tests repro'd, and only as a group — fd-number
interplay) plus an os.fstat(fd4) probe plugin; os.close emits no
Python audit event in 3.12, which is why the audit hook saw nothing.
Fix: mock backend.container.os.close like the sibling tests →
test_backend.py exits 0 under BOTH runners (pytest 2548 passed,
PYTEST_EXIT=0; canonical unittest runner exit 0) — commit d8c14c5.
Also learned: boot smokes serialize on a BUSY lock (issue #3
hardening) — run them one at a time; and /tmp is periodically
cleaned — keep all evidence logs under ~/nyrqis-work/. Issues:write
still absent from the PAT (403), so the full six-step drill pass
remains owner-blocked.
2026-10-04, v0.29.38 RELEASED: tag v0.29.38 pushed → the tag
trigger auto-built the amd64 ISO and published the GitHub release
(nyrqis-live.iso, 342.5 MB); the arm64 asset shipped via dispatch
(live-iso-arm64.yml, run 37202478328 SUCCESS) → nyrqis-live-arm64.iso
350.8 MB attached — the release carries both arches as v0.29.36 did.
Full suite green before tagging: 6,706 passed + 4 skipped (tests/),
2,548 via the CANONICAL test_backend runner (python3 test_backend.py,
exit 0), root tests 54. One quirk documented, NOT a failure: running
test_backend.py under pytest exits 1 after a fully green 2,548-pass
run — code under test closes root logging handlers and
pytest_unconfigure hits EBADF closing the logging-plugin handler
(pre-existing fd hygiene; CI runs selected classes only, unittest
runner unaffected). Issues write is still absent from the PAT → the
full six-step drill pass remains owner-blocked (steps 1–3 proven
live). Earlier 0.29.38 entries below.
2026-10-04, 0.29.38 round (PAT-tooling contracts + CI re-proof: the
staged drill RAN its CI core end to end — the grants check passed
with the FIXED probe (its own 422 would have died there a day
earlier), dispatch 204, run 37198401463 on tip 41b5916 completed
SUCCESS with all three jobs green and the WARM-CACHE proof on
record: arm64 Restore 7 s + Acquire 5 s (a logged no-op) vs ~25 min
cold the run before. The drill itself still died at the issue
steps — issue #1 close 403: the re-flipped token lacks Issues write
(steps 1–3 verified live + 6 idempotent; owner decision: add Issues
RW for the full six-step pass) — and its first attempt exposed the
stale-run grab, now fixed: selection pins head_sha AND created_at
>= the dispatch instant (captured before the POST). Both
regressions pinned by the new tests/test_pat_grants_contract.py
(6 pins; 136 contract tests green across the three contract
files). Round opens: version 0.29.38, CHANGELOG +
IMPLEMENTATION_STATUS entries)

2026-10-04, followups round (three close-outs on the CI evidence:
(1) verify_pat_grants.sh's actions probe FIXED — it dispatched with
no "ref" field, which the endpoint rejects with 422 "ref wasn't
supplied", so Actions write read as MISSING regardless of the token
(a false negative that would also kill the staged drill); the payload
now carries {"ref":"main"} and the api error body prints inline on
failure — end-to-end rerun: ALL GRANTS PRESENT; (2) the 02937 CI
evidence archived to ~/nyrqis-work/logs/2026-10-04-02937-ci-evidence/
— local driver + serial logs for all four smokes plus the serial-log
artifacts downloaded from CI runs 37192020596 and 37193858242
(sha256sums included; the CI amd64-direct serial carries
NYRQIS_BOOT_SMOKE_CTL_PING=1 — the gate is proven in CI evidence
itself); (3) the arm64 rootfs cache VERIFIED already in place — the
arm64 job's actions/cache step existed from the start, and today's
dispatch (the first arm64 CI success) populated it:
nyrqis-rootless-rootfs-arm64-… 580 MB now sits beside the amd64
523 MB, so the next dispatch's acquire phase is a logged no-op)

2026-10-04, dispatch round (the arm64 CI leg CLOSED — the 0.29.37
round is now proven end to end in CI on BOTH arches, no caveats:
grants flipped, verify_pat_grants.sh reads variables OK (its actions
probe still prints MISSING only because pat-expiry-watch.yml 422s —
the REAL dispatch to live-iso-rootless.yml returned 204); run
37193858242 on tip bd44ed6 completed SUCCESS in ~50 min with ALL
THREE jobs green — amd64 direct+menu TCG smokes, arm64 cross build
(zig guest shim) incl. the direct ttyAMA0 TCG smoke, and the
dispatch-only menu-path GRUB/UEFI job; serial logs + ISO artifacts
uploaded)

2026-10-04 (CI round on the pushed 0.29.37 tip: git push
3e2165d..00f23f3 auto-triggered live-iso-rootless — run
37192020596 completed SUCCESS in ~8 min on the stock runner
(usrns posture OK, cached-rootfs restore, rootless debootstrap
acquire + unmodified-builder build + no-root proof + BOTH TCG boot
smokes green, serial logs + ISO uploaded); the arm64 cross-build
job stayed workflow_dispatch-only as designed, and the dispatch
POST returned 403 — the workstation PAT lacks Actions/Variables
write (verify_pat_grants.sh: GRANTS MISSING; Workflows RW is the
sibling grant the dispatch endpoint needs), so the arm64 CI leg
is BLOCKED ON THE OWNER — flip the fine-grained token's
Actions+Workflows+Variables to RW (or re-mint per the script
recipe: Contents, Workflows, Actions, Variables, Pull requests
all RW) then dispatch {"ref":"main","inputs":{"with-arm64":
"true"}}; the local arm64 evidence stands: all four smokes PASS
with the CTL_PING gate, hashes f8e62ec8…4ede / 6cf60582…ca88)

2026-10-03→04 (the 0.29.37 ISO round — BOTH arches REBUILT at tip
(commit 24ead53) through the rootless two-phase pipeline exactly as CI
does, and ALL FOUR boot smokes PASS with the NEW wrapper-runs gate
green. Code round first: parent-relative shell rendering in BOTH
compositors + desktop session, repo-less `packages status`, demo
slow-host budgets (NYRQIS_DEMO_DAEMON_WAIT_S/NYRQIS_DEMO_CTL_TIMEOUT_S
with timeout→FAIL verdict), the wrapper-runs contract (builder
IMG_OPT=/opt/nyrqis/nyhal-linux-backend + $ROOTFS_SRC leak die-gate,
overlay CTL_PING marker, boot_smoke.py hard-fails without it), and
first-boot daemon self-provisions the vault dir — suite green: 6,700
pytest + 2,548 backend, demo 18/18, root tests 54. Then the builds:
amd64 acquisition stamp reused, arm64 fresh 924 MB emulated
acquisition (zig 0.13.0 compiled the aarch64 guest shim fine — the
old "0.13 segfaults" note is stale on this CPU), wrappers
byte-verified inside the squashfs (every entry point execs
/opt/nyrqis/nyhal-linux-backend/…, zero build-host refs). dist/:
amd64 `dist/nyrqis-live-rootless.iso` 447 MB, sha256 f8e62ec8…4ede;
arm64 `dist/nyrqis-live-arm64-rootless.iso` 438 MB, sha256
6cf60582…ca88 (gitignored). Smokes (TCG for arm64): amd64 direct +
menu PASS (Oct 3), arm64 direct + menu PASS (Oct 4, 1,680 s budgets) —
READY/PONG/PKGS plus the new NYRQIS_BOOT_SMOKE_CTL_PING=1: the PATH
wrapper answered a ping of its own on both arches)

2026-09-30 (late evening — BOTH live ISOs REBUILT at tip and
boot-verified, rootless end to end. amd64: direct + GRUB menu smokes
PASS on a clean second loop, desktop-path smoke with virtio-gpu
reached the DRM probe (the demo's documented VM warn outcome), stale
Sep 24 image removed — dist/ holds only the verified builds. arm64:
caught a swept-tree/stamped-survived hazard (stamp removed, fresh
813 MB emulated acquisition), zig 0.13.0→0.12.0 fallback (0.13
segfaults on this CPU; downloads needed curl-resume; cross-compile
proven before use), assembly all-gates-green, output
`dist/nyrqis-live-arm64-20260930.iso` = workroot canonical: 351 MB,
sha256 437644cf…d907, BOTH smokes PASS fully emulated (qemu-system-
aarch64 TCG). dist/: amd64 359 MB eb618087…01f7 + arm64 351 MB
437644cf…d907. No repo-code changes — dist/ gitignored)

2026-09-30 (evening — the live ISO REBUILT at tip (owner-directed
work): the dist ISO was stale (Sep 24, pre-CRY-001/UPD-001/
demo-tree), so a full rootless two-phase build ran on the reference
machine — debootstrap bookworm/amd64 acquisition (resumable, stamp
`~/nyrqis-work/lr.complete`) then the unmodified builder under the
userns+shim with all gates green (dpkg audit, probe parity,
byte-compile, initrd verify, size gate, ownership proof). New
canonical `dist/nyrqis-live-rootless.iso` = the dated
`…-20260930.iso`: 359 MB, sha256 eb618087…01f7 (+186 KB over Sep 24
— the CRY-001/UPD-001/demo content). BOTH boot smokes PASS on the
fresh image (TCG): direct (daemon ping answered, probe packages
complete, nyrqisctl on PATH) and GRUB menu path; serial evidence
kept in the workroot tmp. No repo-code changes — dist/ gitignored;
recorded in NEXT_SESSION_PLAN v6.50.0)

2026-09-30 (`scripts/run_staged_drill.sh` — the post-rotation owner
session became ONE COMMAND, the band-checker precedent: grants check →
dispatch live-iso-rootless (204) → watch to completion → close issue
#1 with the row-D1 decision record as the closing comment (200/201) →
issue #3 close-out comment (201) → PAT_EXPIRES_AT (idempotent);
asserts every expected outcome, `--dry-run` proven against the red
gate — stops cleanly with DRILL STOP; the owner session is now:
flip the grants, run the script, record the verdict)

2026-10-01, second round (the Android-surfaces + Cupertino wave:
QuickSettings renders M3's tile grid and the AppGrid a home-screen
icon grid — both token-gated (tiles.grid/apps.grid), implemented in
BOTH compositors (SDL gained per-document token parsing), token-less
documents pixel-safe; the INTERACTIVE STRESS passed on the real
DesktopSession pipeline (drag/click-focus/minimize/close/workspace/
Ctrl+W/undo/theme-behavior click, all verified); cupertino variant
landed (restyle-never-fork + GRUB/isolinux entry, non-default) and
verified live — cupertino direct boots with the desktop STARTING on
both arches (151 s amd64 / 258 s arm64 TCG); full sweep 9336 passed /
4 skipped; ISOs rebuilt: amd64 447 MB 1fea6d62…db9a, arm64 438 MB
1dd7123b…4dea, DIRECT+MENU+desktop matrix green on both arches and
both variants)

2026-10-01 (the Material-UI wave: owner direction "the UI should
resemble Android while still following Apple HIG" executed end to end —
TWO new brand themes in both compositors (**Material** = M3 baseline
dark, tonal surfaces, primary #D0BCFF, text/bg contrast 14.45:1 AAA;
**Cupertino** = Apple dark system grays + systemBlue, label contrast
18.71:1), registered in the theme engine and the settings panel's
wheel; token-gated chrome (`window.style` material|cupertino,
`start.pill`) with the no-tokens default pixel-compatible;
**`shell/variants/material.nstudio`** — the Android restyle of the
stock shell, restyle-never-fork pinned; `material` joined
KNOWN_SHELL_VARIANTS and the panel picker; **Material is the default
GRUB/isolinux boot entry on both arches**; and the DESKTOP-START BUG
from the Sep 30 stress rounds FIXED — the demo no longer kills healthy
sessions with `timeout 30`: it starts nyrqis_init in the background,
polls the new readiness marker, and leaves the session up. Builder now
ships the desktop stack (python3-sdl2/python3-pil/fonts-dejavu-core/
zstd/xz-utils) via include + ensure-top-up with the rootless-safe
APT::Sandbox::User=root fetch. Full sweep 9333 passed / 4 skipped /
0 failed (baseline 9317 + 16 new tests); BOTH ISOs rebuilt rootless —
amd64 447 MB sha256 a0a91ae3…769b, arm64 438 MB sha256
ae2d395a…11c7; boot matrix DIRECT+MENU PASS on both arches with the
Material entry as the starred GRUB default, and desktop-path boots
PASS on both ("desktop session started" at 145 s amd64 / 256 s arm64
under TCG, session left running) — the first arch-native start
verdicts in the repo's record)

2026-09-30 (the rotation/drill wave v6.50.0: the credential store was
rotated ~13:44 UTC (single token, fresh mtime — owner-reported); the
staged post-rotation drill FIRED as designed and returned **GRANTS
MISSING** — identity OK (Myco-mycelium) and the Contents-RW push path
VERIFIED LIVE (the records commit `13f8f18` went through the new
token), but variables-write and actions-write both 403, so the
dispatch drill, PAT_EXPIRES_AT set, issue #1 close, and issue #3
comment stay gated on the owner flipping the token's Actions/
Variables/Workflows/Pull-requests permissions to RW (or re-minting);
six grants checks over the afternoon all identical — the session
closed cold at `825dc53` rather than loop. Full-suite re-verification
at the new tip: pytest **9313 passed, 4 skipped, 0 failed** (+3
subtests, ~5 min) — 9313+4 = **9317 = the recorded unittest baseline
count digit-for-digit**, zero regression; the unittest binary aborts
exit-141 early in this environment (a local artifact — CI runs that
channel green on the same commits); the digest for the whole day
(`SESSION_DIGEST-2026-09-30.md`) landed at `8a6c390`)

2026-09-30 (the capture-method wave: the ADR-0019 real-hardware
follow-up became EXECUTABLE — `tests/bench_watcher_profile.py`, the
device-labeled harness for §3's named method (§14 pass re-run + idle-
RSS soak with the watcher live + wake jitter), proven end-to-end on
the dev VM via --quick: first real pass 26.9 ms/block (inside §14's
~27 ms anchor band), RSS drift 4 KiB over the 90 s soak, wake jitter
p95 +4.2 ms at the 1 s-scaled cadence; one command on target hardware
now produces the paste-ready labeled record — a tuning follow-up, not
a gate)

2026-09-30 (the Bundle D followup wave: issue #1's close PROBED —
HTTP 403 point-in-time, "Resource not accessible by personal access
token", exactly as the records predicted; the close stays a manual
owner step while the resolution-by-decision stands. The ADR-0019
tuning follow-up got its FIRST measurement: DAEMON_LIFECYCLE §3
gained a Resource-profile section — the idle no-op wakeup measured at
24.71 µs (120k-call sample, journal below the half-threshold),
~35.6 ms CPU/day at the shipped 60 s cadence (~0.0004% duty), ~5 KiB
traced loop allocations, the active pass kept bounded by §14 and the
lock serialization, the stop path confirmed (join(5 s), never blocks
exit); the remaining REAL-HARDWARE capture is named precisely (SSD vs
HDD §14 re-run, a 24 h idle-RSS soak, wake jitter under load) with
the method — explicitly a tuning follow-up, not a gate)

2026-09-30 (Bundle D DECIDED — ADR-0019 RATIFIED AS-IMPLEMENTED — and
the pre-staged §6 Variant-1 disposition LANDED the same day (owner
direction via the recorded session, the E1/F1 same-day shape; the
three ledgers from issue #1 resolved per AG_BRIEF_ADR0019's
recommendation: the fixed 60 s watcher cadence retained as a tuning
knob; `auto_compact=True` ratified as the shipped default — the
ADR-0022 as-implemented shape, six weeks de-facto with the 60–70×/61×
commit wins on record; the shutdown ordering confirmed — stop watcher
→ dirty-gated final save → unmount; the watcher's real-hardware
resource profile recorded as a tuning follow-up, NOT a blocker): ADR-0019
frontmatter v1.1.0 → Accepted, its Status section carries the
ratification paragraph with the staged draft marked superseded; the
ADR index (1.23.0), the spec index (1.51.0), adr/README.md, and
tests/BENCHMARK_PLAN.md reconciled; DAEMON_LIFECYCLE.md's §4/§5
tuning-review caveats struck with the decision note; AG_BRIEF_ADR0019
v1.2.0 → Superseded; AG_AGENDA v2.4.0 — decision-log row D1, the
Bundle D block marked DECIDED — NO OPEN BUNDLES REMAIN; issue #1
resolved by the decision (the close is a manual owner step — the PAT
cannot close issues); the ruling gives the 2026-09-06 unsanctioned
self-flip (reverted 2026-09-20) the Group record it lacked — this is
the sanctioned flip the sitting existed to produce; gates: premises
58/58, cycles 0, mkdocs strict clean, version drift OK)

2026-09-27 (evening V — Bundles E and F DECIDED (owner direction
2026-09-27) and BOTH Option A implementations LANDED the same day:
CRY-001 v0.4.0 — `backend/crash_spool.py` (write-time redaction of
vault aggregates and per-container *_bytes, audit-chained
generation/eviction/purge, fail-closed write that never breaks §4.5
recovery, write-then-rename, retention 20 reports / 32 MiB) + the
§4.5 integration (`service serve --crash-spool <dir>`, service
default disabled — the conservative posture) + `nyrqisctl crash
list/show/purge` (--yes-gated purge), 16 contract pins incl. the
no-egress assertion; UPD-001 v0.3.0 — `backend/update_orchestrate.py`
(resolve on the fully-verified signed index, verify BEFORE restore
point BEFORE apply, a failed restore point refuses the apply,
operator-invoked-only rollback behind the validate_rollback posture —
Q4 resolved by construction; JSONL history; audit chain with the
delta checksum) + `nyrqisctl packages verify/update/rollback/status`
(client-side composition, zero new daemon surface, no CLI network
I/O), 16 contract pins incl. the no-direct-egress assertion; the
demo tree (`demo/run_demo.sh` + two guides — an 18-check five-act
operator session on a real daemon and a real signed repo, incl. the
tampered-delta refusal and a real restart-driven crash spool; the
build stages it onto the live ISO); the upd001-audit-rollback-pin /
upd001-audit-unwired-pin fired on the landing tree exactly as their
fail-on-change descriptions prescribe and were updated per protocol
(nyrqisctl rollback 71 → 79; update_orchestrate.py recorded as the
first non-test apply_delta_update consumer); test_backend.py's two
CLI-wiring doubles reconciled with the new crash_spool_dir argument;
premises 58/58; full sweeps unittest 9317 OK (skipped=4) + pytest
6664 passed (= the recorded baselines + 32, zero regression);
mkdocs strict clean; CHANGELOG 0.29.36 + pyproject bumped (drift
OK); spec index 1.50.0; roadmap Phase 4 items struck; AG_AGENDA
v2.3.9 decision-log rows E1/F1, both bundles closed as implemented —
Bundle D (ADR-0019) remains the only open decision item)

2026-09-27 (evening IV — the Bundle D pre-read: AG_BRIEF_ADR0019
v1.0.0 registered per the AG_BRIEF precedent — the measured case
(11–15 s/123 s per-block-fsync commits → 0.20 s/2.0 s under the
journal default, ~0.3% overhead, the deferred ~27 ms/block compaction
cost bounded by journal_compact_bytes) tabled alongside the as-built
mechanism (auto_compact default + dedicated pin + dirty gate +
shutdown ordering + crash-atomicity preservation, claims pinned
re-runnably), the three decision ledgers from issue #1, recommends
RATIFY-AS-IMPLEMENTED on all three (the ADR-0022 shape) with the
watcher's real-hardware resource profile as the open mechanism
question — not an acceptance blocker; the 09-06 unsanctioned self-flip
named as the cautionary precedent this sitting resolves; registered on
AG_AGENDA v2.3.8 D1, mkdocs nav, spec index 1.49.0, premise
adr0019-brief-registered (58/58); with it, ALL THREE bundles (D/E/F)
carry design-note-or-ADR + pre-read brief + pre-staged build plan —
the decision package is complete; cycles 0 across 90 docs, mkdocs
strict clean)

2026-09-27 (cold close — housekeeping pre-flight RAN CLEAN and the
day's workspace restored to baseline: no stray qemu/smoke processes,
no PID markers anywhere, clean tree; the workroot's unrecorded 692 MB
delta was fully accounted — release-verify/ held the v0.29.35
verification ISOs whose on-disk sha256 values matched the recorded
verification record byte-identically before removal, and tmp/ held
four completed keep-logs smoke-evidence dirs with no live markers —
the ISOs removed hash-verified and the tmp dirs swept by the
sanctioned clean-smoke-tmp.sh --yes (verdict DONE); workroot back to
the recorded 712 MB (the two kept ISOs + wrapper tooling + logs),
tmp/ empty, /tmp 4.5 MB OS-owned plus one 28 KB empty mktemp left to
its owner (mtime minutes old — the runner-bug-2 hazard-class
discipline); also the audit-chain CURRENT-STATE cell landed on
AG_AGENDA v2.3.7 (both families salted scheme-2, snapshot persistence
implemented, package_pki.py a second scheme-2 consumer) pinned
re-runnably as agenda-audit-chain-state-pin; premises 57/57)

2026-09-27 (evening III — the Bundle F pre-read + the B1 pins:
AG_BRIEF_UPD001 v1.0.0 registered per the AG_BRIEF precedent — the
corrected audit restated with the wired/unwired split tabled, the
regulatory frame (NPS-027/028 promise the surface; ADR-0018 chaining;
A needs no new NPS-011 capability; B triggers the NPS-019/NPS-020 pass
as a precondition), three options with ledgers, recommends Option A
compose-first with the rollback trigger/health contract (UPD-001 §5
Q4) flagged as the open mechanism question and the note that A is safe
to accept before Q4 is answered; registered on AG_AGENDA v2.3.6 F1,
mkdocs nav, spec index 1.47.0, premise upd001-brief-registered. Also:
the AG_AGENDA 09-19 pre-flight's B1 current-state mechanism claims
pinned re-runnably (three regex_counts entries: auto_compact default +
resurface, the dedicated test, the anchored 256/64 defaults +
FairTokenBucket — the dated 09-19 cells stay as the historical record);
premises 56/56, cycles 0 across 89 docs, mkdocs strict clean)

2026-09-27 (evening II — the audit-claim sweep + re-runnable pins:
every search-based audit claim in the decision-critical notes re-probed
untruncated — DBG-001's pinned test counts verified digit-for-digit
(9/28/17, zero drift); CRY-001's exhaustiveness claim ("container.py is
the only non-test egress file") caught FALSE AS STATED:
tools/compare_benchmarks.py (the CI benchmark-artifact downloader,
fixed api.github.com destination, operator-authenticated, CI-side
tooling) is a second non-test egress site — corrected in CRY-001 v0.3.0,
AG_BRIEF_CRY001, and AG_AGENDA v2.3.5's Bundle E pre-flight, while the
narrow finding (no implicit/telemetry egress) survives; the failure
mode (truncated search output) made structural: check_doc_premises.py
gained the generic regex_counts checker — per-file regex match counts
+ scan-for-unrecorded-files, fail-on-change, checker source
self-excluded — with three pins (cry001-egress-audit-pin:
container.py 16 + compare_benchmarks.py 5; upd001-audit-rollback-pin:
the wired rollback family per-file 110/50/71; upd001-audit-unwired-pin:
verify/apply call sites, fails when a consumer wires in); both failure
paths verified on synthetic data; premises 49 → 52)

2026-09-27 (evening: UPD-001 v0.2.1 corrigendum — the re-probe
discipline (the 0.29.35 lesson: re-probe hardest the claim that cannot
fail) applied to the note the SAME DAY it was written: the v0.2.0
surface audit had been recorded from truncated search output
(head-capped) and an under-scoped importer sweep, so it understated
the wired surface — the untruncated whole-repo re-run found
deployment/snapshot-scoped rollback ALREADY WIRED end-to-end
(`rollback_to_snapshot` dry-run-default + deployment version rollback
in container.py; five rollback IPC dispatch arms with five matching
`nyrqisctl rollback-*` verbs) and the delta GENERATION half wired
(`nyrqisctl_repo publish-delta`); the corrected load-bearing finding:
the gap is the SIGNED-PACKAGE verify/apply path (`UpdateVerifier`/
`validate_rollback`/`apply_delta_update` — library-complete,
user-unreachable) plus the policy questions — not cryptography, not
the rollback concept; AG_AGENDA v2.3.4 Bundle F pre-flight corrected
to match; options, recommendation, open questions, and the §7 plan
unchanged in scope; full verification wave the same session: unittest
full sweep 9285 OK (skipped=4), pytest 6632 passed + 4 skipped, both
matching the last recorded counts — zero regression)

2026-09-27 (late afternoon: UPD-001 v0.2.0 + AG_AGENDA v2.3.3 Bundle F —
UPD-001 §7 pre-stages the Option A implementation plan (the CRY-001
§7 precedent: acceptance converts to landed work without re-planning):
`backend/update_orchestrate.py` composing the shipped primitives —
`package_repo.load_index`, `UpdateVerifier`, `apply_delta_update`, the
SDK RestoreManager restore point, the ADR-0018 chain — with ordering
pins (verify BEFORE restore point BEFORE apply), operator-invoked-only
rollback behind `validate_rollback`, `nyrqisctl packages
update/rollback`, and contract pins including a no-direct-egress
assertion; the same session staged the decision as AG_AGENDA v2.3.3
Bundle F1 with a tree-verified pre-flight (five questions per UPD-001
§5, decision-ready); spec index 1.44.0; premise description updated,
still 49/49; cycles 0 across 88 docs; mkdocs strict clean)

2026-09-27 (afternoon: UPD-001 v0.1.0 — the M14 Phase 4
"Automatic updates with rollback" design note drafted first per the
DBG-001/CRY-001 discipline (docs/00-platform/UPDATE_ROLLBACK_SPEC.md):
the surface audit's load-bearing finding is that the signed-update
machinery — backend/update_signing.py (FULL/DELTA/PATCH verification +
validate_rollback) and backend/delta_update.py (signed delta
generate/apply, fail-closed without PyNaCl) — is shipped and tested
but consumed only by its own tests; no IPC op or CLI wires
fetch→verify→apply→audit, and PackageManager.update_package (the
2026-09-23 store wiring) verifies the delta without applying it, so
the honest gap is WIRING and POLICY, not cryptography; three options
(A compose-first operator-invoked update surface, recommended;
B A + opt-in automaticity, triggering the NPS-019/NPS-020 pass for a
daemon-initiated fetch; C observational close), five open questions
(option choice, package-vs-platform scope, automaticity posture, the
rollback trigger/health contract, restore-point retention); the CI
verdict on the morning's cold-close records commit f6157f9 was also
captured (35/35 success — the every-commit-verified property extends
through it); registered: spec index v1.43.0, mkdocs nav, premise
upd001-design-note (49/49), cycles 0 across 88 docs, mkdocs strict
clean; roadmap item stays [ ] — the draft proposes, the Group decides)

2026-09-27 (fourth wave: the extended re-probe audit — the AG_AGENDA
09-19 pre-flight's frontmatter cells marked as a dated snapshot via an
inline RE-PROBED marker (the sanctioned 09-19/09-20 reconciliation has
since landed: ADR-0007/0009/0013/0016/0018/0022/0023 Accepted in both
frontmatter and index; ADR-0024 still Proposed) while every underlying
mechanism claim re-verified — auto_compact default + pin + shutdown
ordering, the shipped container defaults, FairTokenBucket; zero false
claims found; AG_AGENDA v2.3.2)

2026-09-27 (third wave: DBG-001 v0.8.1's re-probe audit — 12/12
as-built claims verified against the current tree, zero drift, the
pydevd authentication nuance adopted into §4.2 lesson 3 — and CRY-001
v0.2.0's §7, the Option A implementation plan pre-staged so an
acceptance ruling converts directly to landed work, contract pins
including a no-egress assertion; Sunday's dailies verdict still
pending the 10:02–11:49 UTC band at record time)

2026-09-27 (the CRY-001 decision package completed: AG_BRIEF_CRY001
v1.0.0 — the Bundle E pre-read per the AG_BRIEF precedent, recommending
Option A local-only with the spool default flagged as the open
mechanism question — registered on AG_AGENDA v2.3.1 E1, mkdocs nav,
spec index 1.41.0, premise cry001-brief-registered (48/48);
SESSION_DIGEST-2026-09-27.md written per the 09-25 convention; the
corrigendum/staging commit 75d6492 CI-verified green 35/35; Sunday's
dailies verdict pending the 10:02–11:49 UTC band at record time)

2026-09-27 (CRY-001 v0.1.1 corrigendum + AG_AGENDA v2.3.0 Bundle E — the
crash-reporting design note's v0.1.0 surface-audit null finding ("ZERO
outbound HTTP clients in non-test backend code") was caught FALSE the
next session by re-running the audit: `backend/container.py` carries
four outbound client sites predating the draft — `_send_webhook`
(2026-08-28, 5585532), registry_pull/push/catalog (2026-08-30, 56de456;
pull wired to IPC + nyrqisctl), and a loopback-only health-check HTTP
type; the corrected finding is NO implicit/telemetry egress (every site
operator-configured or loopback; the live ISO boots -net none), so
Option B's transmission would still be the first telemetry-class egress
surface (see CRY-001 §2.1); the corrected note is staged for the Group
as Bundle E (decision-ready, four questions: option choice, spool
default, schema floor, retention/purge); roadmap note corrected, spec
index 1.40.0, premise registry updated; CI on the CRY-001 draft commit
46dbc46 green (35/35), scheduled-runs checker exit 0 point-in-time
2026-09-27 ~07:36 UTC; the two parked triggers unchanged (owner-reported
PAT rotation; Monday's dailies band check))

2026-09-26 (v0.29.35 RELEASED and VERIFIED END-USER-STYLE — the
determinism fix tagged (annotated, a30098c), release 397286940 created
with the boot-smoke line withheld, both tag workflows success
(live-iso ~9 min, live-iso-arm64 ~38 min), both assets downloaded
unauthenticated byte-exact (258340864 / 267296768; sha256
06e45f60…76f7ef1 / 2c8395fd…2c713) and ALL FOUR boot paths PASS on the
downloaded ISOs, verdicts captured to files this time after a tool
timeout orphaned one early run (the killed run left a stale marker and
no verdict — re-run cleanly rather than counted); boot-smoke line
PATCHed into the release only after the proof; dailies in-band (checker
exit 0). The 0.29.35 tag closes the drift gap the fix opened)

2026-09-26 (v0.29.34 RELEASED and VERIFIED END-USER-STYLE — commit 9a9ca20
(the .vsix build half) tagged v0.29.34 and pushed; release created via API
(ID 397251990) with the boot-smoke line WITHHELD until proven — the v0.29.33
lesson applied — then PATCHed in after all four paths passed; both
tag-triggered workflows success (live-iso #36246338154 ~8 min,
live-iso-arm64 #36246338155 ~38 min); both assets downloaded
unauthenticated byte-exact vs the API sizes (258342912 / 267290624;
sha256 5ba9470f…681c392c / f867ceb8…1c01a22e) and ALL FOUR boot paths PASS
on the DOWNLOADED ISOs, run serially. Dailies in-band the same day
(scripts/check_scheduled_runs.sh exit 0); PAT rotation still absent
(verify_pat_grants.sh: GRANTS MISSING). The 0.29.34 tag also closes the
version-drift loose end the .vsix commit opened)

2026-09-26 (the .vsix BUILD HALF of the IDE-integration item LANDED —
tools/build_vsix.py packages the vscode-nyrqis extension into a real,
installable .vsix with the Python standard library alone (no vsce, no
npm registry, no network), fail-closed and byte-identical-reproducible,
pinned by 12 contract tests in test_build_vsix.py and verified beyond
the tests with REAL VS CODE (--install-extension success, the extension
listed as nyrqis.vscode-nyrqis in a throwaway extensions dir); the
roadmap IDE item keeps [~] with the build half struck and only the
owner-side marketplace publish open; CHANGELOG 0.29.34 + pyproject
bumped (drift OK); sweep 9284 OK (skipped=4), pytest 6631 passed)

2026-09-26 (v0.29.33 RELEASED and VERIFIED END-USER-STYLE — the D7 wrap
commit 28ae6b2 tagged v0.29.33 (annotated, pushed), release created via the
API (ID 397200903, "Nyrqis 0.29.33") with the D7 attach-UX body; CI on
28ae6b2 all green BEFORE the tag (ci, live-iso, arm64-conformance, docs);
both tag-triggered workflows success (live-iso #36237180631 ~11 min,
live-iso-arm64 #36237180628 ~46 min); both ISO assets attached automatically
and verified the 0.29.32 way — unauthenticated download byte-exact against
the API asset sizes (258312192 / 267268096 bytes; sha256 9cef5320…c28a8c70 /
aa81e8aa…57738635) and ALL FOUR boot paths PASS on the DOWNLOADED ISOs (amd64
direct+menu, arm64 direct+menu — exit 0 each, run serially under the
PID-marker guard). Operational lesson recorded: unauthenticated
api.github.com polling exhausted the 60/h quota (403) mid-watch; the git
credential fill token lifts API polling to the authenticated 5000/h while
downloads stay unauthenticated. The release body's boot-smoke line,
published ahead of the re-verification, is now independently confirmed.
Today's dailies ran in-band (pat-expiry-watch 10:08:33Z,
scheduled-runs-watch 10:19:32Z, both success — the SIXTH consecutive
in-band day; scripts/check_scheduled_runs.sh exit 0). Parked threads
unchanged: the owner-side PAT rotation (dispatch + issue #3 comment),
Monday's dailies check, and the AG's Bundle D ruling)

2026-09-26 (M14 Phase 3 CLOSED — the LAST D7 work item, the interactive
attach UX, LANDED and END-TO-END PROVEN on a live debug-class container:
debug_attach.py + `nyrqisctl debug attach/detach/dap-bridge` — client-side
composition only per the Phase A discipline, the audit-chained
container_debug markers + container_list posture + the manifest command as
the debugged program, NO new daemon op; the DAP bridge is a framing-only
byte pipe between IDE stdio and the staged loopback endpoint so VS Code and
any DAP client attach without this CLI in the data path; container_run
gained --debug-class/--rootfs and the post-spawn grant of manifest-requested
class-conditional caps through the class-gated path (NPS-011 §4.4);
container_list entries carry the network posture + class. The proof:
manifest command `python3 -Xfrozen_modules=off -m debugpy --listen
127.0.0.1:5678 app.py` → attach marker → DAP handshake over the KEPT socket
(pydevd binds to the FIRST accepted connection — a connect-and-close probe
wedges it; three transport lessons pinned in DBG-001 v0.8.0 §4.2 and the
module docstring: the socket-family capabilities CAP_NETWORK_SOCKET/BIND
the in-container listener needs, the kept-client-slot rule, pydevd's
arguments-key + initialize→attach→initialized→setBreakpoints→
configurationDone sequencing) → breakpoint verified=True → stopped
(reason=breakpoint) → variable inspection x==40 (paused BEFORE x+=2) →
continue → AFTER_BP 42 → detach → hash chain verified with
debug_class=true in every entry; own-netns containers REFUSED per §5.5
req 4 with the marker released; -Xfrozen_modules=off required (frozen
modules make the adapter go silent under the container's seccomp posture).
Pinned by tests/test_debug_attach_ux.py (17 tests); roadmap strikes
[~]→[x]; spec index v1.38.0; DBG-001 v0.8.0; CHANGELOG 0.29.33 + pyproject
bumped (drift OK); sweep 9272 OK (skipped=4), pytest 6619 passed; three
test_backend.py ControlService tests reconciled to the new container_list
shape and the getattr-guarded grant block. The parked threads are unchanged:
the owner-side PAT rotation (dispatch + issue comment), Monday's dailies
check, and the AG's Bundle D ruling)

2026-09-25 (v0.29.32 RELEASED — the drafted entry became the release: annotated
tag pushed, release created via API with the 0.29.32 changelog body; both
tag-triggered ISO workflows green (live-iso ~11 min, live-iso-arm64 ~38 min);
both assets attached and verified END-USER-STYLE — unauthenticated download,
sizes match the API (258263040 / 267214848 bytes), sha256 a6fe2556…b45f6cb /
f0efecb6…f4e3fc66 recorded, and ALL FOUR boot paths PASS on the downloaded
ISOs (amd64 direct+menu, arm64 direct+menu); the new cleanup tool swept its
own verification evidence (~117 MB plus the byte-verified download dupes);
issue-comment probe still 403 — the arm64 rootless dispatch and the issue
comment both remain parked on the owner-side PAT rotation)

2026-09-25 (issue #3 COMPLETED end to end — the tooling half landed in
scripts/clean-smoke-tmp.sh (3f85979): the sanctioned sweeper for
nyrqis-boot-smoke-* dirs refuses (exit 3, removes NOTHING) while any PID-file
liveness source (the drivers' two markers or the local wrapper's smoke.pids)
names a live process via /proc existence checks — never pgrep — with a
dry-run default, --yes-gated removal, and a flat scan scoped to the smoke
namespace; 7 contract tests (live-boot contract 86; pair 120 OK); CI green
on 3f85979 (docs, ci, live-iso) and on 21d9d62 (docs, ci); the tool's first
real sweep removed exactly the 5 kept smoke-evidence dirs (~118 MB, nothing
else); issue-comment POST still 403 (three probes) and dispatch probe #9
still 403 — the PAT rotation remains the single open item, owner-side)

2026-09-25 (issue #3 IMPLEMENTED + full local/CI verification GREEN — both
boot smokes now guard concurrent runs (PID marker under the temp dir, exit 2
+ "refusing to race it" while a live instance holds it; stale/garbage markers
taken over; liveness via os.kill(pid,0) with EPERM alive — pgrep banned;
distinct per-driver tags) and self-heal their tmpdirs (rmtree replaces the
empty-dir-only rmdir that leaked kernel/initrd debris every run); 12 contract
tests + CLI-level BUSY proof; commit d2b2a81: ALL FOUR CI workflows success
incl. live-iso-rootless (guarded smokes runner-proven) and the root-built
live-iso-arm64 (build 46 min + menu-path UEFI smoke success, re-attach
skipped as tag-gated); local wrappers surface rc=2 as a distinct BUSY
verdict; full local smoke sweep PASS ×4 (amd64 direct+menu, arm64
direct+menu on the kept ISOs — arm64 pair needed a redo after the guard
correctly BUSY-refused its own concurrent launch); dailies PASS 5th
consecutive in-band day (10:28:46 + 10:36:16 UTC, checker exit 0);
dispatch probe #8 still 403 — PAT rotation remains the single open item,
owner-side)

2026-09-25 (v0.29.31 RELEASED — tagged, both ISOs attached to the
published GitHub release and verified end-user-style: sha256 digests
match the API values, both assets boot-smoked locally (amd64 direct,
arm64 GRUB/UEFI menu) after download; the first amd64 attempt failed
from a self-inflicted harness fault (concurrent cleanup deleted the
live smoke's tmpdir) — standing rule recorded and filed as issue #3:
cleanup must check smoke liveness (PID-file based, never self-matching
pgrep -f) before touching tmp dirs; artifact cleanup executed per user
choices (~2.8 GB freed, originals kept); sixth dispatch probe 403 —
the PAT is fine-grained WITHOUT Actions:write (confirmed by the absent
X-OAuth-Scopes header), so the rootless arm64 CI dispatch remains the
single open item, owner-side)

2026-09-25 (shim staging consolidated into ONE atomic helper + a
per-minute re-stage watchdog covering BOTH phases (the local /tmp wipe
hazard is time-based); dead per-phase helper removed; contract at 100
tests with atomicity pinned inside the helper; refactor re-validated
END TO END locally — full acquire+build exit 0 and BOTH boot smokes
PASSED on the resulting ISO; arm64 dispatch re-attempted, 403 again
(pre-rotation signature))

2026-09-25 (rootless CI GREEN on GitHub's runner after two runner-only
shim fixes — the canonical preload path (one name on both chroot sides,
immune to PATH-sanitizing debootstrap) and tmp+mv shim staging (cp -f
onto a mapped .so truncates it and running processes execute zeros —
SIGSEGV, reproduced locally via systemd-coredump); both re-validated
end-to-end locally before push; run 36122698939: acquire + build +
ownership proof + BOTH boot smokes all success with zero sudo; arm64
dispatch honestly 403 (pre-rotation PAT signature, carried trigger);
rootless contract 33 tests / 99 green)

2026-09-25 (rootless CI first run: the runner-environment validation
EARNED ITS KEEP — the amd64 job failed in the userns-probe step on the
runner exactly as designed: GitHub's ubuntu-24.04 image ships
apparmor_restrict_unprivileged_userns=1, blocking unprivileged userns
creation; pre-flight now normalizes the sysctl posture (environment
setup, sudo-free pipeline unchanged) and the probe is VERBOSE (sysctl
values + unshare rcs in every log); the arm64 rootless path gained the
split menu-boot job (GRUB/UEFI via downloaded artifact, same pattern as
the root-built workflow); rootless contract at 31 tests / 99 green)

2026-09-25 (rootless CI validation job landed; full backend sweep green —
new `.github/workflows/live-iso-rootless.yml` mirrors the reference
machine's constraints on a stock runner: amd64 job gates pushes with the
full rootless loop (acquire → build → BOTH boot smokes under TCG) plus
an ownership proof (stat -c %u ≠ 0 — sudo appears ONLY in pre-flight
steps, pinned by test), arm64 job is dispatch+input-gated and
cross-builds with the USER-SPACE zig tarball (asserted: no
gcc-aarch64-linux-gnu anywhere) + foreign acquire + emulated build +
1680 s smoke; rootfs cache covers the PARENT dir so tree + .deb cache +
completion stamp travel together; rootless contract grew to 30 tests
(workflow pins: no-sudo-in-pipeline, job timeouts, smoke budgets,
input gate); full `python3 -B -m unittest discover` sweep: 9212 tests
OK (skipped=4) — the runner-coverage lesson applied); full backend sweep

2026-09-25 (rootless arm64 live-ISO UNBLOCKED and boot-proven — the
previously blocked cross build now works on the reference machine with
NO root and NO system packages added: a user-space zig tarball
(~/.local/opt/zig) cross-compiles the LD_PRELOAD shim as an AARCH64
shared object (zig cc -target aarch64-linux-gnu) because the emulated
debootstrap second stage runs arm64 ELF whose loader refuses an amd64
.so; the acquire phase gained --foreign + an emulated second stage
(chroot wrapper re-execs with ONLY the in-target preload — the shim
class boundary: staging on BOTH sides of the chroot boundary kills the
loader warnings that polluted the builder's captured dpkg --audit gate);
fixed a real latent builder bug (qemu-$DEB_ARCH-static produced the
nonexistent qemu-arm64-static — binfmt registers the QEMU arch, now
$QEMU_STATIC); all driver temp artifacts moved to ~/.cache after
systemd-tmpfiles emptied /tmp MID-BUILD twice (the vanished shim
unloaded debootstrap's tar and resurfaced the exact chown-EINVAL class
the shim exists to prevent); the builder gained NYRQIS_CDYLIB_ARCH
to skip host-arch Rust cdylibs in a cross image (18 skipped, demo
reports 0/0 honestly); the 351 MB arm64 ISO (~/nyrqis-work/)
PASSED BOTH boot smokes under TCG — direct ttyAMA0 handshake AND the
GRUB/UEFI menu path — daemon pong + ctl round-trip + probe parity all
green; driver contract grew to 21 tests incl. a zig-cross-compile test
verifying e_machine=0xB7; 88 contract tests green)

2026-09-24 (rootless live-ISO build landed — the full ISO pipeline now
runs WITHOUT root on the reference dev machine (no sudo/docker/KVM,
user namespaces only): new `packaging/live/build-live-iso-rootless.sh`
drives the UNMODIFIED `build-live-iso.sh` inside
`unshare -Urmpf --mount-proc` with an LD_PRELOAD shim
(`rootless-syscall-shim.c`; chown→no-op success, mknod→placeholder
files — fakeroot cannot substitute, its daemon propagates EINVAL from
unmapped-gid chowns); the 359 MB amd64 ISO it produced **passed both
boot smokes locally** (direct + GRUB menu path, daemon pong + probe
parity green); the first boot exposed a real latent bug —
`nyrqis-demo`'s bare `$LD_LIBRARY_PATH` expansion under `set -u`
(getty respawn loop; CI never hit it because the image shipped no
cdylibs, so the branch never executed) — fixed with the guarded form
and pinned in `tests/test_rootless_build_contract.py` (14 tests,
including a real-userns tar-extraction shim test); builder gained
env-gated `NYRQIS_SQUASHFS_FORCE_ROOT`/`NYRQIS_SQUASHFS_PSEUDO`
(force 0:0 ownership with `/home/demo` kept 1000:1000 — root path
untouched); arm64 via this path honestly blocked (binfmt +
qemu-aarch64-static work — a real arm64 busybox executed — but the
emulated debootstrap stage needs an aarch64 preload shim and no
cross-compiler is installed; nested full-range uid maps are
kernel-EPERM; CI's root-built arm64 unaffected); live README
documents the rootless + two-phase resumable flow)

2026-09-23 (second-fire standing item CLOSED — PASS, 10:22 UTC; Thursday's
third-fire item registered; M11's "remaining" backlog reconciled — all four
deliverables landed by 2026-09-06; the BUILD-001/BUILD-ARCH duality registered
on AG_AGENDA v1.8.0 and briefed; NPS-029 Identity drafted — NPS-025 §4.14's
placeholder resolved; three new architecture diagrams; the real package-manager
store wiring landed; SDK scaffolding + hot reload struck done after a green
test audit;`nyrqisctl debug bundle` Phase A + Phase C rider landed per
DBG-001 v0.4.0 (redaction default-on, per-container audit trails,
`--chain-id` capture); **the AG sitting decided D5–D7** — NPS-028
Accepted with amendments, BUILD-ARCH canonical (BUILD-001 absorbed +
removed, citations re-pointed), debug attach via developer-mode
manifests (implementation to follow NPS-021 addendum + NPS-011 v1.4.0);
the IDE-integration prototype landed (`ide/vscode-nyrqis/`) with the
.nstudio design gate in CI; session records pushed to origin main — see
`NEXT_SESSION_PLAN.md`)

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus an externally-contributed,
independently-verified Linux Backend implementation
(`source/nyhal-linux-backend/`, v0.28.0 — 6,133 Python tests passing +
35 skipped, 275 tests across 18 Rust crates). Milestone 12 — the
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
Trust Model, `NPS-027`) landed 2026-08-12 and was **Accepted
2026-09-21** (AG decision log D2), formally completing the threat
model's planned phase list (see `docs/reference/security/README.md`).
The 2026-09-21 Architecture Group session then decided all three
registered standing agenda items — D1 (dynamic-shares default:
static retained), D2 (NPS-027 acceptance), D3 (publisher key trust:
the ADR-0014 mirror, landed as NPS-026 v1.2.0 §6.3) — leaving
`AG_AGENDA.md` v1.5.0 with no standing items. A docs-backlog pass
(2026-08-12)
started Milestone 11's gap categories: the Object Registry (NPS-025),
Public API (API-001), ABI (ABI-001), and Package Format (NPS-026,
including the digital-signature design closing `FIND-PACKAGE-001`) now
exist as `Draft` documents; first Tutorials and How-To guides are
published; and every stale category/reference index placeholder has been
replaced with a real index. ~~Still remaining from Milestone 11's
prioritized backlog: governance expansion, build architecture docs,
performance budgets, and developer onboarding~~ — **reconciled 2026-09-23:
all four landed by 2026-09-06** (`NPC-010`, `BUILD-001`, `PERF-001` v1.1.0,
`TUT-003`; the roadmap's M11 items 8–11 struck — the performance-budget
numbers themselves still await real hardware). One open finding from the
same pass: TWO divergent build-architecture documents exist (`BUILD-001`
Draft in `docs/reference/build/` vs `BUILD-ARCH` Accepted in
`docs/00-platform/`, both claiming NPC-007 gap 9) — the canonical-ID choice
is recorded for the Architecture Group, not made unilaterally (see
`NEXT_SESSION_PLAN.md`).

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
22 accepted, 4 held (ADR-0014/0015/0019/0024 — each with its blocker
named), 1 rejected. The 2026-09-19 Architecture Group decisions
(recorded in `AG_AGENDA.md`'s decision log) accepted ADR-0007,
0009, 0013, 0016, and 0018's close-out, and ratified ADR-0022/0023.

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Accepted
- [x] ADR-0003 Game disk images with overlay — Accepted
- [x] ADR-0004 Containerized execution model — Accepted
- [x] ADR-0005 Windows compatibility translation layer — Accepted
- [x] ADR-0006 Hybrid microkernel as kernel base — Accepted
- [x] ADR-0007 Zstandard as default compression codec — **Accepted** (2026-09-19): default level 3 per NPS-005 §3; data §31
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Accepted
- [x] ADR-0009 Per-container token-bucket IPC rate limiting — **Accepted** (2026-09-19): mechanism + §4 defaults as shipped; static `fair_shares` default with dynamic opt-in; data §32a–e. **Follow-on reconciled 2026-09-20**: the accepted posture is normative in NPS-010 §7.1.1 (v1.7.0 — dynamic shares operator-permitted, guarantee mode-independent); dynamic-as-DEFAULT registered as the agenda's one standing item, and its decision ledger **completed the same day** (§32f: lone sender 313/s static vs 2,063/s dynamic, 8 senders 250/s each in both modes, legit client fully protected either way) — the standing item is decision-ready, pure policy judgment. **Decided 2026-09-21 (AG decision log D1): static default retained** — nothing remains open in the ADR. An earlier same-day claim that the dynamic-mode adversarial data was missing was wrong (§32e has existed since 2026-09-10) and is corrected here
- [x] ADR-0010 Vulkan as native graphics API foundation — Accepted
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Accepted
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Accepted
- [x] ADR-0013 EEVDF-derived scheduler with real-time priority class — **Accepted** (2026-09-19): Linux-6.6 weight table, RT reserve ≤ ~60–70%, small-request requirement; data §33
- [ ] ADR-0014 UEFI Secure Boot with user-enrollable keys — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0015 Shared dynamic binary translation for ARM/x86 — **Proposed**, approach decided; performance validation blocked on benchmark data
- [x] ADR-0016 NyFS Linux Backend as user-space FUSE filesystem — **Accepted** (2026-09-19): FUSE-first confirmed; kernel-module fallback = named reopen criterion; data §5–§15
- [x] ADR-0017 Reject domain-grouped NPS renumbering — **Rejected** (the project's first; considered and explicitly declined, not left unresolved)
- [x] ADR-0018 Hash-chained append-only log for capability audit records — **Accepted, review CLOSED** (2026-09-19): status reconciled to Accepted everywhere; tamper-scope fix DIRECTED and landed (scheme-2 hashing, details covered, 22.5 µs/event; merged from `audit-b1-hardening`); dual mechanisms consolidated behind one hasher; persistence requirement set (opt-in JSONL snapshots, daemon path wired) — all six sign-off checklist items ticked in the review package
- [x] ADR-0019 Journal commit as the default NyFS save() mode — **Accepted, review CLOSED** (2026-09-30): RATIFIED AS-IMPLEMENTED, Bundle D decided (AG decision-log row D1, owner direction via the recorded session, the E1/F1 same-day shape) — all three issue-#1 ledgers resolved per AG_BRIEF_ADR0019's recommendation (60 s cadence retained as a tuning knob; `auto_compact=True` ratified, the ADR-0022 as-implemented shape; shutdown ordering confirmed), the watcher's real-hardware resource profile a tuning follow-up, not a blocker. The daemon lifecycle design note (`source/nyhal-linux-backend/DAEMON_LIFECYCLE.md`) answered open question 1; the 2026-09-30 ruling supplies the Group record the 2026-09-06 unsanctioned self-flip (reverted 2026-09-20) lacked — the sanctioned flip. The ADR-0025/0026 index flag cells keep the historical 09-06 note
- [x] ADR-0020 Implementation languages and the platform boundary — **Accepted** (v2.0.0, 2026-08-13), canonical language matrix (Rust/C++/C platform languages; NyHAL resolved Rust-first) + platform-boundary principle: platform-critical execution paths must not depend on the Python interpreter; supersedes v1 (Python + Rust, 2026-08-12); Architecture Group acceptance recorded in issue #2 (closing the issue itself is a manual step — the PAT cannot comment/close issues)
- [x] ADR-0021 NyRuntime direction — IPC serving loop behind the FFI boundary — **Accepted** (2026-08-15), close gate met (wire p50 82–95 µs vs <100 µs target, §22)
- [x] ADR-0022 NyVault — storage as a daemon-hosted service on the IPC transport — **Accepted, RATIFIED** (2026-09-19): the Group CONFIRMED the 2026-09-06 acceptance (`3262618`) as sanctioned and ratified as-implemented (the §27/§29 performance record is the known-cost ledger); the stale index/body statuses were the discrepancy
- [x] ADR-0023 NyVault key manager — envelope encryption with Rust-held key custody — **Accepted, RATIFIED** (2026-09-19): confirmed with ADR-0022; as-implemented ratification
- [ ] ADR-0024 Streaming data plane — chunked framing for large CALL payloads — **Proposed** (2026-08-16) with TWO increments already implemented the same day and the evidence run complete (§29: streamed 1 MiB writes 5.6× plaintext / 6.6× encrypted vs paging; reads already AEAD-bound, ~1.02–1.08×): 0.14.20 service-level streaming (chunk envelope over ordinary CALLs, codec untouched) and 0.14.21 wire-level STREAM_CHUNK framing on both serving paths (Rust loop reassembles; close-race fix rode along); the Rust client half's streaming remains the documented follow-on. Review input prepared 2026-09-18 (see the ADR's status block): two structured caveats — the read-path result scopes the win to writes + single dispatch (do not review it as a general I/O accelerator), and §27's absolute numbers are pre-batching/pre-streaming baselines (a post-0.14.21 FUSE-mount re-benchmark is the one missing evidence artifact, not yet collected — §27-style run wedged twice in the child on 2026-09-18, matching the suite's documented environmental skips). **The missing artifact was collected 2026-09-20** (BENCHMARK_RESULTS.md §27 re-measurement: wire-streamed 1 MiB writes 9.58–10.47 MB/s, reads 6.30–6.34 MB/s under a real kernel mount) after the wedge's root cause was found and fixed — see the 2026-09-20 note below)

## Specifications (NPS)
15 accepted, 13 held (4 named benchmark/dependency blockers, plus
NPS-018..NPS-024 — threat-model phase documents, Draft pending
Architecture Group sign-off — and NPS-025, NPS-026, NPS-028 — Draft
documents from the 2026-08-12 Milestone 11 backlog pass, with NPS-028
added 2026-09-21 as the accepted trust model's implementation surface;
NPS-027 left this column 2026-09-21 on its D2 acceptance).

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
- [x] NPS-025 Object Registry — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 2; 14 object types catalogued, ~~Identity flagged pending its own NPS~~ resolved 2026-09-23 by NPS-029 v1.0.0, whose User/Session types join the catalogue as v1.1.0)
- [ ] NPS-029 Identity and User Data Separation — **Draft** (2026-09-23, closing the NPS-025 §4.14 gap and the external review's identity-surface finding): User/Session objects, authentication rules (unspoofable login surface per NPS-015 §5.2's class, vault-custody credential storage per ADR-0022/0023, audited failures), per-user data separation via ownership stamps + per-User NyFS volumes — deny-by-default cross-User access through ordinary capability grants only; no new kernel surface; three candidate surfaces/findings recorded for the threat model's next pass
- [x] NPS-026 Package Format (.nypkg) — Draft (2026-08-12 backlog pass, closing Milestone 11 gap category 7 and FIND-PACKAGE-001; signed manifests + integrity trees proposed, concrete crypto scheme pending dedicated human review per NPC-002 §6.2). **v1.1.0 (2026-09-18)**: §13 records the implementation findings from ADR-0022/0023 (NyVault) — volumes are NyFS images, integrity trees (plaintext) compose with vault AEAD (at-rest) without re-encryption, streaming install into vaults inherits 32 KiB CALL paging and is commit-bound until write batching, uninstall maps onto creator-scoped volume lifecycle; §14 adds the hardware-root-convergence and registry-vocabulary open questions. Closes the M14 Phase 1 package-format-update item. **v1.2.0
(2026-09-21)**: §6.3 expanded from the ADR-0014 pattern into the
decided mechanism (AG decision log D3 — bundled platform root set,
protected-confirmation enrollment, revocation inputs + expiry, the
advisory/block propagation split, cross-signature rotation), closing
`REQ-SEC-0004`; the concrete crypto scheme stays reserved per
NPC-002 §6.2. **v1.3.0 (2026-09-22)**: §6.7 (new) — the NPC-002 §6.2
reserve concluded (AG decision log D4, the dedicated human review sat
with the brief's tree claims re-verified): primitives per role
(Ed25519 + SHA-256; root-set-signed revocation lists; ADR-0023
envelope encryption for the key store; image-anchored root set;
RSA/ECDSA/novel constructions explicitly rejected), the key
fingerprint decided as SHA-256(public key) displayed in FULL 64
lowercase hex (the shipped 8-byte `key_id` migrates with a version
marker), the canonical serialization explicitly deferred to §9,
quorum confirmed (single-root MUST verify, any-root MAY sign), and
the frozen/deferred parameter split recorded; §6.3.6 re-points at
§6.7. REQ-SEC-0003's implementation gate opens
- [x] NPS-028 Package PKI Implementation Surface — **ACCEPTED 2026-09-23 (AG decision log D5, v1.0.0) with amendments**: §5.3's stale-list deferral tightened to a named thaw trigger (bounds enter when a real revocation channel runs in production, derived from its measured cadence) and the decision-day evidence recorded in §1; the NPS-026 §9 canonicalization fence remains recorded — per D5 it gates §6.7.3's wording, not the accepted mechanisms. (Draft 2026-09-21, the NPS-027 residual: key store, verification pipeline, revocation channel, enrollment flow, audit trail, SURFACE-PKI-0001..0004; the scheme is decided — NPS-026 v1.3.0 §6.7, D4 — and this document's fence narrows to the §6.7.3 canonical-serialization deferral). **v0.2.0 (2026-09-22)**: implementation STARTED — `backend/package_pki.py` implements the §3 key store (three collections, §3.3 field set, §3.5 uninstall-as-revocation), the §4 verification pipeline (one ordered path, per-stage outcomes, TOFU fail-closed, the §6.3.4 advisory/block split, §7.2 non-blocking audit sink), the §5 revocation list (root-signed, monotonic sequence, replay-refusing, atomic apply), and §6 enrollment + cross-signed rotation, with the §6.7.2 fingerprint spelling throughout (26 tests); remaining named increments: §3.2 daemon-authority enforcement, §7 ADR-0018 wiring, §5.1 transport, §5.3 bounds — and §3.4 custody LANDED the same day (`save_locked`/`load_locked`: ADR-0023 envelope encryption, Argon2id-derived KEK never persisted in plaintext, AEAD contexts bound to the format magic, crate custody when present, fail-closed; plaintext persistence demoted to the marked dev/test path; 6 custody tests, 32 total; NPS-028 v0.3.0) and §3.2's store-layer authority enforcement landed the same session (every store written 0600 via atomic temp-rename — no world-readable intermediate ever exists — and loads refuse group/world-readable stores fail-closed, naming §3.2 and the fix; 4 authority tests, 36 total; NPS-028 v0.4.0; the container-side half — hosting the store behind the daemon's IPC — remains the daemon integration's named item) — and §7 + §5.1 landed the same session as well (NPS-028 v0.5.0): `PackageAuditChain` reuses ContainerManager's scheme-2 chain byte-for-byte (differentially pinned against `_audit_event_content`), `make_sink` wires it to the pipeline under §7.2, the §5.1 fetcher's channel is independent of the package feed, and the store persists its `revocation_sequence`; 15 new tests, 51 total — and §3.2's daemon-side API half landed too (NPS-028 v0.6.0, SURFACE-PKI-0001's first build): `DaemonAuthority` is unforgeable (direct construction raises; `mint()` is the daemon's only entry) and `PkiDaemonService` is the store's only supported interface — every read/write demands a valid (and once bound, matching) authority, fails closed without one, exposes no store/enumeration path, and audit-chains every mutation; 9 new tests, 60 total — and §3.2's physical IPC transport completed the surface the same session (NPS-028 v0.7.0, SURFACE-PKI-0001 complete at API+transport): `PkiIpcServer`/`PkiIpcClient` over a Unix socket, one server-minted authority (connections are wires, not identities), an explicit op allowlist that excludes key-material reads (verification runs daemon-side; packages have no right to export store contents), unknown/private ops refused, wire mutations audit-chained; 8 new tests, 68 total — and the deployment hardening landed in the transport the same session (NPS-028 v0.8.0): 0600 socket mode at bind, fail-closed refusal of group/world-writable socket directories naming the fix, SO_PEERCRED daemon-uid-or-root dropping at setup with the 0600 mode as the documented floor, idempotent stop; 4 new tests, 72 total — and the daemon's production process model landed the same session (NPS-028 v0.9.0): `PkiDaemonRunner` (custody MANDATORY on the production path — no unlock secret, no runner; store unlocked at boot, §7 chain resumed from its salt header, clean stop persists custody + chain exactly once) + the `pki serve` CLI subcommand (signal-flag polling, not `signal.pause()`, so the first SIGTERM persists instead of dying mid-handler) + the shipped `nyrqis-pki.service` systemd unit (DynamicUser, NoNewPrivileges, StateDirectory custody store + audit chain, unlock secret from the optional EnvironmentFile — the daemon exits without it); install.sh deploys the unit, the root-tree mirror is restored, and the wiring is contract-pinned; 5 new tests, 84 total — and the end-to-end daemon drill the same session (NPS-028 v0.9.1) found what the transport's own tests had missed: no test had ever exercised `enroll` over the wire, and JSON carries no bytes (the 64-char hex string arrived where the service demands 32 raw bytes; the §6 confirmation dataclass arrived as a plain dict). The binary-over-JSON conventions are now explicit and fail-closed — named hex params decode to bytes server-side (malformed hex = request failure), dataclass params rebuild from their field mapping (unknown fields = request failure), `PkiIpcClient` hex-encodes bytes args on send — and the drill passed end to end (custody-mandatory boot, IPC enrollment, spoof/hex/shape refusals, SIGTERM persistence, restart survival); 5 new wire-convention tests, 89 PKI total — and §5.1's daemon wiring landed the same session (NPS-028 v0.9.2): `PkiDaemonService.refresh_revocations` (the daemon's own authority drives it, callers cannot smuggle a fetcher through the allowlist, outcomes audit-chained — applied lists as `pki_apply_revocations` with an out-of-band marker, rejections as evidence) plus the runner's background refresh loop (channel-configured `FileRevocationFetcher`, fail-open per §5.1, joinable at stop), exposed via `pki serve --revocation-channel/--refresh-interval` and shipped in the unit (disabled by default — an absent channel is an operator decision); 8 new tests, 97 PKI total — and the implementation-validation pass recorded the same session (NPS-028 v0.9.3, §10): a mechanical 15-claim probe verifying the document's normative statements against the shipped tree (fingerprint spelling, three collections, 0600 writes + fail-closed loads, custody envelope with no secret at rest, unforgeable authority, key-read exclusion from the allowlist, the ordered §4 path, §5.1 store-untouched-on-failure, §5.2 replay refusal, §6.2 gates in-process AND over the wire, §7 tamper evidence, §7.2 sink rule) — 15/15 after correcting one probe bug; package-security set 217 green in one run; PKI module verified byte-identically from a clean worktree checkout; NPS-028's remaining Draft dependency is now only NPS-026 §9's canonicalization decision plus the Group's acceptance review
- [x] NPS-027 Package Trust Model — **Accepted** (2026-09-21; Threat Model Phase 7, 2026-08-12, completing Milestone 12; disposition of FIND-PACKAGE-001 plus 4 new findings closed via NPS-006 §6 amendment and REQ-SEC-0003..0006). **Review REGISTERED 2026-09-20, DECIDED ACCEPTED 2026-09-21 (decision log D2)** — second standing item on `AG_AGENDA.md` v1.3.0 for the next session; the spec existed since 2026-08-12 but was never scheduled (the next-actions audit found items 18/20 still calling for what it already is); acceptance closes the planned threat-model phase list and unblocks item 18's PKI-implementation residual; **the routed FIND-PACKAGE-003 key-trust design was decided the same day (D3 — the ADR-0014 mirror)**, landing as NPS-026 v1.2.0 §6.3 and closing REQ-SEC-0004

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

## Package PKI Implementation Surface
Draft: [`NPS-028`](../reference/security/NPS-028-package-pki-implementation-surface.md)
— the buildable surface for the accepted package trust model (NPS-027,
NPS-026 §6.3): key store, verification pipeline, revocation
distribution, enrollment flow, audit trail, and the four new attack
surfaces (SURFACE-PKI-0001..0004) enumerated for the threat model's
next pass. The concrete crypto scheme was decided 2026-09-22 (AG
decision log D4) and is normative in NPS-026 v1.3.0 §6.7; the document
exits Draft on implementation validation.

**The signing half already ships** (found while drafting the crypto
review package, 2026-09-21): Ed25519 package/delta signatures and a
signed repository index (`backend/package_signing.py`,
`backend/update_signing.py`, `backend/package_repo.py`; 37 tests,
fail-closed with no stub fallback). **The trust machinery's
implementation started 2026-09-22** (`backend/package_pki.py`): the
§3 key store, §4 verification pipeline, §5 revocation-list
verification, and §6 enrollment + cross-signed rotation, all speaking
the §6.7.2 fingerprint — and §3.4 custody landed the same day
(`save_locked`/`load_locked`, ADR-0023 envelope encryption, the KEK
never persisted in plaintext) and §3.2's store-layer authority
enforcement (0600 atomic writes, loads refuse over-open stores) and
the §7 ADR-0018 audit wiring (`PackageAuditChain`: the scheme-2 chain
byte-identical to ContainerManager's and differentially pinned against
it in the tests; §7.1 records carry the package identity + verdict +
stages; JSONL persistence with the salt in the header; `make_sink`
attaches it to the pipeline under the §7.2 never-changes-a-verdict
rule) and §5.1's out-of-band transport (`RevocationFetcher` +
`refresh_revocations`: the channel is independent of the package feed
— feed compromise cannot suppress revocation delivery — and fetch
failure/replay/unauthentic lists leave the store untouched; the store
now persists its §5.2 sequence) and §3.2's daemon-side API half
(`DaemonAuthority` + `PkiDaemonService`: the store's only supported
interface, guarded by an unforgeable authority token only the daemon
can mint — package code can present no authority, the service exposes
no enumeration, mutations are audit-chained) plus the physical IPC
transport (`PkiIpcServer`/`PkiIpcClient`: JSON-lines over a Unix
socket, one server-minted authority — connections are wires, not
identities — explicit op allowlist that excludes key-material reads,
0600 socket mode, fail-closed refusal of group/world-writable socket
directories, SO_PEERCRED daemon-uid-or-root policy where the OS
exposes it) — and the daemon's production process model (`PkiDaemonRunner`
+ `pki serve` + the `nyrqis-pki.service` unit: custody mandatory at
boot, clean-stop persistence, deployment wiring contract-pinned)
— the one remaining named increment: §5.3 bounds (frozen by design). The propose-
side review package for the §6.2-reserved scheme is at
`AG_BRIEF_NPS026_CRYPTO_SCHEME.md` — reviewed and **ACCEPTED**
2026-09-22 (AG decision log D4: scheme accepted, G1's fingerprint
adopted with the display form amended to full 64-hex, G3's
canonicalization deferred to §9, G4's quorum confirmed); the scheme
text landed as NPS-026 v1.3.0 §6.7 the same day and the §6.2 reserve
is removed.

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

  **2026-08-16 (0.14.22): the NUI (.nstudio) runtime consumption lands
  (ADR-0025).** The Nyrqis side of the NyForge ↔ runtime pipeline:
  `ui/nstudio.py` (pure-Python reference floor — parse, contract
  validation, `$state:` substitution, layout render, text preview),
  `rust/nyui/` (the Rust import gate, ABI 1.0.0 — the UI layer's first
  compiled artifact, per ADR-0020), `ui/nstudio_codec.py` (the standard
  FFI loader), the four NyForge example designs as fixtures under
  `tests/fixtures/nstudio/` (including the 1440×900 `nyrqis-shell` UI
  draft), and `TestNstudioImport` + `TestNstudioCodecConformance` (32
  tests; differential messages byte-identical floor↔crate). CI gains
  `rust-nyui` + `rust-nyui-conformance` (required gate).

  **2026-08-16 (0.14.23): the import gate rides the control plane.**
  `NuiService` (`ui/service.py`) exposes `nui_validate` / `nui_load` /
  `nui_current` over the datagram control plane — operator-only
  (registered containers refused), per-call document budget, `nui_load`
  persists the design as the daemon's shell UI, `nui_current` surfaces
  what is loaded (re-imported through the gate on every call; stale
  persisted designs reported honestly as `valid: false`) — with
  `nyrqisctl nui validate|load|current` as the CLI (e2e verified
  against a live daemon with the Rust crate as the engine). The
  Security Center (`security-center.nstudio`) and Vault Workspace
  (`vault-workspace.nstudio`) screens — the second and third NyForge
  designs, 71 components / 4 behaviors / 1 binding each — join the
  fixtures with shape + `$state:` tests. `tests/benchmarks.py --nui`
  (§30) A/Bs the gate floor-vs-crate: crate ~2.1× faster at the median
  (242 µs vs 502 µs p50). Suite 524 → **538**.

  **2026-08-17 (0.14.24): the Nyrqis API Registry lands (one
  machine-readable contract, three consumers).** The NUI component
  vocabulary now lives in `ui/contracts/nui-api-v1.json` — the Python
  floor loads its tables from it at import time, the Rust crate embeds
  the same file (`include_str!` → `OnceLock<Registry>`), and Nyforge
  regenerates its C# tables from a vendored copy. `TestNstudioCodecConformance`
  passes unchanged (floor↔crate cannot diverge — same file). Full
  suite: **538** (unchanged — the migration is behavior-preserving).

  **2026-08-17 (0.14.25): the first real Shell component set.** The
  registry grows to 63 components across five new categories — Shell,
  Data, Form, Media, Developer — each with a real semantic contract
  (Taskbar position/alignment/autoHide/…, WindowFrame
  Minimize/Maximize/Restore/Close, …). All three consumers pick it up
  automatically; import-gate tests that used `Taskbar` as the unknown-type
  example now use `BogusWidget`. Suite stays 538.

  **2026-08-17 (0.14.26): the real desktop shell screen.**
  `desktop.nstudio` — a 1440×900 desktop (DesktopSurface/DesktopIcons,
  Taskbar, StartMenu, CommandPalette, NotificationCenter, QuickSettings,
  WorkspaceSwitcher) plus a `lock` screen (LockScreen) — 30 components, 8
  behaviors, 6 bindings, authored with the shell vocabulary and accepted
  by the floor, the Rust crate, and Nyforge's own serializer. Suite
  538 → **539**.

  **2026-08-17 (0.14.27): the window system + power UI.**
  `windows.nstudio` — WindowFrame/WindowControls driving
  component-targeted actions (Minimize/Maximize/Close), stacked
  windows, and a PowerMenu with Sleep/Restart/Shutdown — 21 components,
  8 behaviors, 1 binding across 2 screens; accepted by the floor, the
  crate, and Nyforge's serializer. Suite 539 → **540**.

  **2026-08-17 (0.14.28): widgets + OSD + login.** `WidgetHost`,
  `OSD`, `Login` join the registry (66 components); `widgets.nstudio`
  — WidgetHost cards, a volume OSD, a Login form  — 19 components, 5
  behaviors, 2 bindings across 3 screens; accepted by floor, crate,
  and Nyforge's serializer. Suite 540 → **541**.

  **2026-08-17 (0.14.29): typed property metadata in the registry.**
  `properties` become metadata objects (name/type/default/bindable/
  required + min/max/enumValues/units where meaningful); vocabulary
  unchanged. Floor parses names, the crate's serde structs carry the
  full PropertyDefinition, Nyforge regenerates ComponentContracts.cs
  + the new PropertyDefinitions.cs. Suite stays 541.

  **2026-08-17 (0.14.30): reusable component masters (NFS-006 §9).**
  `components[]` holds reusable masters; instances declare
  `componentRef` + `overrides` and omit `type` (both gates reject an
  instance with its own type; overrides must fit the master's contract)
  — enforced identically by the floor and the crate (differential
  tests). The `desktop.nstudio` taskbar is built from one
  `TaskbarButton` master with two instances; Nyforge materializes
  instances via `ReusableComponentResolver`. Suite 541 → **546**; Nyforge
  71/71.

  **2026-08-17 (0.14.31): responsive layout constraints (NUI-SCHEMA
  §4.1).** `layout` gains optional anchors (all default false), min/max
  bounds, and `aspectRatio`, validated identically by both gates
  (differential). `resolve_layout()` adapts any container size (stretch
  on both-horizontal anchors, bottom-dock, aspect derivation) and
  `text_preview()` shows adapted bounds. The desktop shell's taskbar
  stretches and docks itself; an icon carries `aspectRatio: 1.0`.
  Suite 546 → **562**; Nyforge 118/118.

  **2026-08-17 (0.14.32): localization (NUI-SCHEMA §8.1).** A document's
  `locales` section (`active` + per-locale string tables) resolves
  `$localize:key` references in component properties, reusable
  overrides, and behavior arguments; refs must exist in the active
  locale's table, enforced fail-closed by both gates with byte-identical
  messages. `resolve_text()` resolves them. The shell fixture's search
  label and DND message are localized (en/af).  Suite 562 → **573**;
  Nyforge 127/127.

  **2026-08-17 (0.14.33): resources — the managed asset catalog
  (NUI-SCHEMA §8.2).** A document's `resources` section (unique ids,
  allowed kinds, non-empty paths, optional 64-hex sha256) is validated
  by both gates; `$asset:id` references in properties and overrides
  must name a declared resource (fail-closed, byte-identical messages).
  The shell fixture's wallpaper is a declared image asset referenced
  via `$asset:wallpaper`. Suite 573 → **585**; Nyforge 135/135.

  **2026-08-17 (0.14.34): the NUI expression language (NUI-SCHEMA
  §7.2).** `ui/nexpr.py` is the deterministic expression language
  (`state.name` refs, comparisons, `&&`/`||`/`!`, and
  `if`/`min`/`max`/`contains`/`format`) with position-tagged syntax
  errors. `$expr:` values (properties, overrides, action arguments) and
  condition `expression` fields (superseding the legacy equality form)
  are validated fail-closed by **both gates** with byte-identical
  messages and evaluated at resolution time (`resolve_action` /
  `resolve_condition`). `rust/nyui/src/nexpr.rs` is the byte-for-byte
  Rust mirror (differential-tested; crate 9 → 13 unit tests). The shell
  fixture's DND condition is `state.doNotDisturb == true` and its
  notification title is `$expr:format(state.clockTime, "{0}")`; Nyforge
  mirrors the gate as ER-NUI-021 before Preview (one semantics across
  Nyforge / floor / crate). Suite 585 → **604**; Nyforge 163/163.

  **2026-08-17 (0.14.35): declarative animations (NUI-SCHEMA §8.3).**
  The document's `animations` section — unique ids, a target that must
  name an existing component, a non-empty property, and timing
  (duration/delay/repeat non-negative; easing linear|ease-in|ease-out|
  ease-in-out|steps; direction forward|reverse|alternate) — is
  validated identically by both gates. The registry gains the
  `Nyrqis.Animation.Play` system action; a behavior using it must
  reference a declared animation (byte-identical messages,
  differential). The desktop shell's Start menu fade plays on toggle;
  Nyforge mirrors the gate as ER-NUI-022 (contracts regenerated from
  the registry). Suite 604 → **619**; Nyforge 173/173. Keyframes are
  the documented follow-on.

  **2026-08-17 (0.14.36): state scopes (NUI-SCHEMA §8.4).** A
  document's `stateScopes` section carries the five named state tables
  (global/screen/component/session/persistent) referenced as dotted
  `scope.key` names in expressions, conditions, bindings, and `$expr:`
  arguments; `global` is the named form of the flat `states` section
  (a bare reference resolves against `states` first, then `global`).
  `resolve_state`/`resolve_states` resolve dotted references through
  the declared tables (flattened view, flat wins on collision);
  `_state_known` gates conditions and bindings; expression validation
  is scope-aware. Unknown scope names and non-object tables are
  rejected by **both gates** with byte-identical messages, and dotted
  references to undeclared scoped keys are unknown-state errors
  (differential). The shell fixture's theme is a `persistent` state
  and its clock a `session` state; Nyforge mirrors the section check
  as ER-NUI-023 and threads scope-awareness through its expression /
  condition / binding checks (FlattenedStates/IsStateKnown mirror the
  floor). Suite 619 → **635**; Nyforge 187/187. Scope lifecycle (what
  persists) is the runtime's follow-on.

  **2026-08-18 (0.14.37): the extended Shell vocabulary — AppGrid,
  Clock, Dock, TitleBar.** Four more desktop-specific primitives
  (doc #15's list is now complete: 24 Shell types) join the registry
  with typed semantic contracts; the crate embeds the same file and
  Nyforge regenerates its C# tables. The desktop fixture exercises
  them: the taskbar clock is a real `Clock` bound to a `clockFormat`
  state, a `Dock` sits on the desktop, a `Launcher` hosts an `AppGrid`,
  and a Files window is framed by `WindowFrame` + `TitleBar` +
  `WindowControls` (close → `Close` action) — 37 components / 10
  behaviors / 6 bindings, accepted by the floor, the crate, and
  Nyforge's serializer. New `TestShellComponents` (5) + 2 conformance
  cases; unknown properties/events fail both gates byte-identically.
  Suite 635 → **642**; Nyforge 195/195.

  **2026-08-18 (0.14.38): animation keyframes (NUI-SCHEMA §8.3).** An
  animation may carry an optional `keyframes` list — `[{"offset":
  0.0–1.0, "value": …}]` stops with strictly increasing offsets and a
  number/string/boolean value — the multi-point curve the runtime
  interpolates between (absent = single-segment transition). Both gates
  validate the shape fail-closed with byte-identical messages (list,
  object entries, numeric offset in [0, 1], present value, strictly
  increasing); Nyforge mirrors as ER-NUI-022. The Start menu fade in
  `desktop.nstudio` is now a 3-keyframe curve played by
  `behavior_start_toggle`. Floor `TestAnimations` +6, crate unit tests
  13 → 16, crate conformance +3. Suite 642 → **651**; Nyforge 200/200.
  **2026-08-18 (0.14.39): behavior logic graphs (NUI-SCHEMA §7.3).** A
  behavior's `condition` is a leaf or a recursively-nested `logic:
  and|or` group (non-empty `conditions` list, each entry a leaf or a
  group) and its `DO` is exactly one of a single `action` / a non-empty
  `actions` chain run in order — both or neither rejected at parse.
  Groups evaluate with all/any recursion; `resolve_actions` returns the
  chain with per-step `$state:`/`$expr:` substitution. Both gates
  validate the shapes fail-closed with byte-identical messages
  (unknown logic, empty/non-object entries, unknown states in nested
  groups, both-or-neither action forms); Nyforge mirrors as ER-NUI-024
  / ER-NUI-005 and evaluates groups identically. `desktop.nstudio`
  exercises a real 2-action theme chain and an AND quiet-hours guard.
  Floor `TestBehaviorLogicGraphs` +11, crate conformance +4. Suite
  651 → **666**; Nyforge 213/213.

  **2026-08-18: the Nyrqis UI Runtime lands (`ui/runtime.py`).**
  `NyrqisRuntime` wraps a loaded `NstudioDocument` and provides the
  real OS runtime operations: state management (`set_state`/
  `resolve_state`/`resolve_states`), event dispatch (`fire_event` —
  find behavior, evaluate condition including AND/OR groups, execute
  action chain), binding application (`apply_binding`/
  `apply_all_bindings`), and action execution (system actions:
  `Theme.Set`/`Animation.Play`/`Notification.Show`; component actions:
  `Open`/`Close`/`Toggle`). This is the Nyrqis-side counterpart of
  Nyforge's `ForgePreviewRuntime` — both implement the same semantics
  (NUI-SCHEMA §7.3, §8.4). 26 new tests (`tests/test_runtime.py`).
  Suite 666 → **692**.

  **2026-09-06: Multi-monitor support lands (M13 Phase 3, ABI 1.2.0).**
  The Wayland crate gains proper multi-output support: output enumeration
  with global deduplication, wl_output listener callbacks (geometry, mode,
  done, scale) with output ID data, primary output detection/setting,
  per-surface buffer scale for HiDPI, output info query by ID, output
  count per connection, and change sequence tracking for hot-plug detection.
  The Python codec (`ui/wayland_codec.py`) exposes all new FFI functions;
  `WaylandDisplay` (`ui/wayland_display.py`) gains `primary_output`,
  `set_primary_output()`, `set_buffer_scale()`, `get_output_info()`,
  and updated `check_output_changes()`. 18 new tests
  (`tests/test_wayland_multimonitor.py`).  NPC-007 §M13 updated.

  **GPU acceleration and custom compositor infrastructure verified.**
  The GBM crate (`rust/gbm/`, ABI 1.0.0) provides device opening,
  surface creation, buffer locking, and buffer info query via `libgbm`
  dlopen.  The DRM crate (`rust/drm/`, ABI 1.0.0) provides device
  enumeration, connector detection, and atomic modesetting via DRM
  ioctls.  Python codecs (`ui/gbm_codec.py`, `ui/drm_codec.py`) and
  `WaylandDisplay` integration are in place.  The compositor crate
  (`rust/compositor/`, ABI 0.1.0) provides client/surface/output
  management, XDG shell protocol, frame callbacks, and SHM buffer
  handling — full event loop and protocol message parsing remain as
  follow-on work.  NPC-007 §M13 updated.

  **2026-09-06: Documentation gaps addressed (M14 Phase 1).**
  Three new documents landed: governance expansion (`NPC-010`, Draft)
  covering RFC process, release process, deprecation policy, versioning,
  branching strategy, commit conventions, and ADR workflow; build
  architecture (`BUILD-001`, Draft) covering toolchain, build graph,
  cross-compilation, reproducible builds, CI stages, and artifact
  signing; developer onboarding tutorial (`TUT-003`, Draft) covering
  prerequisites, first build, coding standards, repository tour, first
  contribution, debugging, testing, and documentation style.  M14
  (Production Readiness) milestone added to NPC-007 with four phases:
  Documentation & Governance, Hardware Compatibility, Developer
  Experience, and Production Hardening.

  **2026-09-06: SDK and production hardening (M14 Phases 3 & 4).**
  Developer SDK (`sdk/nyrqis_sdk/`) lands with: project scaffolding
  (`scaffold.py`) supporting app, shell, and rust templates; CLI
  (`cli.py`) with nyq command for new, build, test, preview, and pkg
  operations; package manager integration (install, remove, update,
  search, list, stats); hot reload (`hotreload.py`) for .nstudio file
  watchers with SHA-256 change detection; telemetry (`telemetry.py`)
  for opt-in crash reporting and metrics (no PII); performance
  monitoring (`performance.py`) with Timer, MemoryTracker,
  FrameRateMonitor, and PerformanceBudget; restore points
  (`restore.py`) for system snapshots.  46 new tests.  Suite 2619
  tests passing.

  **2026-09-09: the compositor wire event loop gets its host half
  (M14 follow-on, backend 0.27.0).**
  `ui/compositor_host.py` bridges the socket transport
  (`ui/wayland_socket.py`) to the Rust wire-format event loop
  (`rust/compositor`, ABI 0.2.0) through the FFI loader: client bytes
  are fed to the crate on message boundaries (partial messages
  reassembled across `recv()` calls), the crate parses the protocol
  and maintains the object table, and its response events are drained
  back onto the socket. When wired, the wire loop owns dispatch (the
  legacy Python dispatch double-responded and is skipped); without the
  crate the Python path remains the fallback and the host fabricates
  nothing (fail-closed). Two protocol-state fixes landed in the crate:
  `nyrqis_compositor_start`/`stop` now reset the object table + queues
  (a restart previously collided with stale object ids), and
  `wl_display.sync` is served. `NyrqisCompositor` wires the bridge
  automatically and reports host-half counters in `get_stats()`.
  End-to-end verified over a real Unix domain socket: get_registry →
  bind → create_surface → frame → commit returns 5 globals + a
  frame-done stamp. Tests: `tests/test_compositor_host.py` (8). CI
  gains `rust-compositor` (the crate had never been compiled in CI —
  its 48 tests ran only on dev hosts) and the required
  `compositor-host` gate.

  **2026-09-09: delta update generation (NPS-026 §6 generation
  half, backend 0.27.0).**
  `backend/delta_update.py` produces what `backend/update_signing.py`
  verifies: `diff_packages` diffs two payload directories into
  deterministic add/modify/remove ops (`.nypkg` layout normalized),
  `create_delta_update` emits a canonical-checksum document optionally
  signed with Ed25519 (the same payload form the shipped verifier
  checks), and `apply_delta_update` verifies the signature BEFORE any
  filesystem mutation with a per-op path-traversal guard and a
  fail-closed refusal of unsigned deltas when a trust store is
  supplied. Cross-verified in both directions:
  `TestDeltaPassesShippedVerifier` proves a generated delta passes
  `UpdateVerifier.verify_delta_update` unmodified and a tampered op
  list fails it. Tests: `tests/test_delta_update.py` (18).

  **2026-09-10: the display half of the compositor lands, the package
  repository ships, and the DRM backend is rewritten to the real
  kernel UAPI (backend 0.28.0, tagged + released).**
  Sixteen UI applications were brought up to their test specifications
  (packet analyzer, virtual keyboard, disk health, calendar, markdown
  editor, network monitor, password manager, screen recorder, audio
  mixer, font manager, and their test groups), taking the Python suite
  from 2,532 to 6,133 passing tests. `ui/compositor_presentation.py`
  completes the presentation half: DRM device detection, DRMBackend
  attach, honest software fallback, and frame lifecycle statistics.
  `backend/package_repo.py` + `nyrqisctl_repo.py` ship the package
  repository (signed index, publish/verify/download). A long-lived-
  process bug in the Rust compositor was fixed: `start` now tears down
  previous-session clients/surfaces/outputs (outputs previously
  accumulated until MAX_OUTPUTS was exhausted, breaking `add_output`
  after 16 start/stop cycles). Hardware verification
  (`verify_presentation.py`, `run_tests.sh --gpu`) then exposed that
  `ui/drm_backend.py` spoke no real DRM UAPI: query ioctls passed
  immutable buffers the kernel cannot write into (EFAULT →
  `detect_connectors()` silently returned `[]` on every real machine),
  several ioctl numbers encoded wrong struct sizes, and `set_mode
  (fb_id=0)` would have disabled scanout rather than present. The
  module was rewritten to the real kernel UAPI (two-call query
  protocol with pointer arrays, correct ioctl numbers per
  `drm_mode.h`, dumb-buffer → ADDFB2 → SETCRTC presentation, honest
  failure without DRM master) and verified on real Intel hardware
  (`/dev/dri/card1`): 2 CRTCs, 3 connectors, 3 encoders, connector 64
  (CRTC 47) enumerated with 5 real modes, byte-exact composite output,
  kernel EPERM without DRM master handled with honest software
  fallback. Version first-party packaging aligned (`pyproject.toml`
  0.28.0).

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
**All pending Architecture Group decisions are consolidated on one
agenda: `docs/00-platform/AG_AGENDA.md` (2026-09-18)** — the review
session can work from that document alone; the per-item state below
is the standing record.

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
   A/B, BENCHMARK_RESULTS.md §20): the v1 FFI surface (per-recv
   malloc) was SLOWER than the floor (wire p50 ~426 µs Rust vs ~231 µs
   floor). FFI surface v2 (ABI 2.0.0, same day) removes the
   allocation — recv writes directly into the caller's reusable
   buffer, send is zero-copy — and measured wire p50 307–357 µs
   (~28% under v1, ~1.6× the ~200 µs floor) with the residual being
   the ctypes boundary tax, not a bug. The migration stands on the
   boundary rule + the byte-identical conformance gate; NPS-003 stays
   Draft, gate open (closing it needs the serving loop behind the
   boundary — the NyRuntime direction).**
2. ~~Benchmark default IPC token-bucket parameters (unblocks ADR-0009,
   then NPS-010 §7.1).~~ **First-pass data collected 2026-08-12** — the
   default bucket (100 burst, 50/s refill) sustains only ~99.5 calls/s
   on a client→endpoint path and throttles ~18.9k calls/s at full speed;
   the defaults are demonstrably too low for this workload shape.
   **Sweep + adversarial interference collected 2026-09-10**
   (`tests/BENCHMARK_RESULTS.md` §32, `tests/benchmark_bucket.py`):
   steady-state throughput ≈ refill rate at every burst capacity (burst
   only shapes spike absorption — refill is the knob that decides
   throughput); the SHIPPED manager default (burst 200, 500/s) caps a
   path at ~4.5% of its unthrottled capacity (13.3k calls/s floor);
   refill ≥ ~20k/s reaches the "not the bottleneck" regime; and the
   adversarial run shows a naive shared bucket STARVES a legitimate
   250 Hz client under a full-speed flood (9 admitted/s vs 250
   requested while the flood still passes ~1,025/s) — the quantitative
   case for per-sender fairness as a mechanism change in NPS-010 §7.1.
   ADR-0009 review package ready.
   **Fair-bucket defaults data + spec adoption collected 2026-09-10**
   (§32c–d, `ADR-0009-review-package.md`): the fairness mechanism is
   implemented (`FairTokenBucket`) and normative (NPS-010 §7.1.1);
   sized to demand, every sender meets its rate under a flood while
   the flooder is confined to its share — and the honest cost of
   static shares is measured (a lone sender is capped at
   `sender_burst + envelope/shares`), leaving static-vs-dynamic shares
   as the one open mechanism question for the review.
3. ~~Benchmark Zstd compression levels, install size vs. load time
   (unblocks ADR-0007, then NPS-005).~~ **First-pass data collected
   2026-08-12** — level sweep on a synthetic corpus (overall ratio 2.54
   at levels 1–5 vs 3.17 at ≥7; compression 0.6–3.5 GB/s). **Close-out
   data collected 2026-09-10** (`tests/BENCHMARK_RESULTS.md` §31,
   `tests/benchmark_adr0007.py`): real-asset level sweep (ratio flat
   ~1.07 at every level on already-compressed /usr/share data — levels
   ≥7 buy ≤2% ratio for 60× less compression throughput), the real LZ4
   fast path (lz4.frame now available; ~2.7× zstd-1 compression speed
   at equal ratio on real data — the §2 zlib approximation is retired),
   and concurrent scaling (2.2× aggregate at 8 threads — zstandard
   partially releases the GIL). The level-choice data is now complete;
   the default-level decision belongs to Architecture Group review.
4. ~~Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning~~
   **Data collected 2026-09-10** (`tests/BENCHMARK_RESULTS.md` §33,
   `tests/benchmark_adr0013.py` — discrete-event EEVDF simulation, the
   relative-choice instrument; BENCHMARK_PLAN §5 documents the method):
   interactive latency is governed by the request size the interactive
   task itself submits (≤1.5 ms requests → zero overruns under 3
   background hogs; 12 ms → 80% of periods missed) — there is no
   separate "interactive boost" knob to tune; the Linux 6.6 weight
   table is the recommended curve (tail isolation ~35% better than
   linear at high nice, share accuracy within 1–2% on all curves); and
   with NO admission control, 100% RT utilization starves the fair
   class completely with zero RT misses — the reserve is not optional
   (admission ≤ ~60–70% keeps the fair tail ≤ ~15 ms in the model).
   ADR-0013's review package is ready; the defaults themselves are an
   Architecture Group decision.
5. ~~Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).~~ **Data collected 2026-09-18** (`tests/BENCHMARK_RESULTS.md` §35, methodology in BENCHMARK_PLAN §7, real cgroup-v2 enforcement via the user manager's delegated subtree): representative shapes peak 3.2–9.0 MB (256 MB default = 28–80× floor headroom); quota throttling is a TAIL phenomenon (20% quota → p50 unchanged, p95 +8× — monitor p95/`nr_throttled`, not mean usage); 64-PID default sits 1.5× above a modest supervisor shape. **The §9 SUSPENDED-accounting question is answered with data:** frozen containers hold 100% of memory, consume 0% CPU, and stay kernel-reclaimable via `memory.high` — the consistent model is full memory accounting, zero CPU accounting. Default VALUES remain an Architecture Group decision.
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
7. ~~Benchmark hash-chain computation/verification overhead before ADR-0018 exits Proposed~~ **Data collected 2026-09-18** (`tests/BENCHMARK_RESULTS.md` §34, methodology in BENCHMARK_PLAN §6, real implementation benchmarked): append ~6.4 µs p50 / ≈100 k events/s sustained, verify O(n) with a stable ~3.3–3.5 µs/event constant through 100 k events, the hash itself only ~19% of the append cost, 2–8% overhead against the audited IPC ops — the "negligible" expectation is confirmed as measured fact. **Scope finding (§34e): the chain hash covers salt + prev_hash + op + timestamp but NOT the `details` payload — rewriting a stored event's details is undetectable by `verify_audit_integrity`** (demonstrated on the real code; recorded in ADR-0018's status as an open spec decision).

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
17. ~~Wire `tools/check_depends_on_cycles.py` into `.github/workflows/docs.yml`
    as a CI step. It found 4 real circular dependencies this pass
    (NPS-001↔ADR-0012, NPS-001↔ADR-0013, NPS-001↔ADR-0014,
    NPS-007/008↔ADR-0015 — each individually reasonable when added, only
    circular together) that had been sitting in already-committed,
    already-pushed documents undetected. Running it by hand caught them
    this time; it should run automatically going forward.~~ **Done — and
    this item itself went stale past its own wiring**: the step is
    already in `docs.yml`'s build job (found 2026-09-20 while wiring
    item 17b).
17b. ~~Run `tools/check_doc_premises.py` (added 2026-09-20) on the docs
    regularly — ideally the same CI step as the item above.~~ **Done
    2026-09-20** — it runs as the `Check recorded doc premises` step in
    `docs.yml`'s build job, right after the cycles check and before the
    strict build. It mechanizes
    the premise audit: an explicit CLAIM REGISTRY re-verifies recorded
    load-bearing claims ("evidence is BENCHMARK_RESULTS §32e", "the
    crate is shipped", "the rename is still pending") against current
    reality. Add a registry entry whenever a document records a new
    load-bearing premise — including this document's own evidence
    citations.
18. ~~Elevate priority on Milestone 11's package-format gap category
    (specifically digital signatures) — Phase 2's `FIND-PACKAGE-001`
    found that `.nygi` integrity currently relies on checksums alone,
    which don't establish publisher authenticity; an attacker can tamper
    with an image and simply recompute a valid checksum. Not fixable by a
    quick amendment; needs a real package-signing/PKI specification.~~
    **Done 2026-08-12 — superseded by `NPS-027`** (Package Trust Model,
    threat model Phase 7): it deepens `FIND-PACKAGE-001`'s disposition
    (publisher authenticity requires signatures; checksums detect
    corruption, not tampering) and specifies the signature block,
    publisher identity, and the verification boundary. Residual:
    `NPS-027` is **Accepted** (2026-09-21, AG decision log D2), and no PKI
    implementation exists (the package manager itself is still future) —
    the implementation surface is drafted as `NPS-028` (2026-09-21).
    Found while auditing this list 2026-09-20: the item was left open
    after its deliverable landed.
19. ~~Continue Milestone 11's remaining prioritized backlog
    (`007-PROJECT_ROADMAP.md`) — diagrams, API reference, ABI
    specification, object registry, and package format are now `Draft`
    (2026-08-12 pass); governance expansion, build architecture docs,
    performance budgets, and developer onboarding remain. Each is
    roughly the size of a prior milestone on its own.~~ **Done — all
    four "remain" items landed**: governance expansion (`NPC-010`),
    build architecture (`BUILD-001`), developer onboarding (`TUT-003`,
    `docs/tutorials/developer-onboarding.md`), and performance budgets
    (`PERF-001` v1.0.0, 2026-09-06). The roadmap's own M14 Phase 1
    checklist records completion (its 2026-09-18 note); this list item
    was never struck. Residual: ~~`PERF-001` predates the §35
    container-resource-limit data and should absorb it.~~ **Done
    2026-09-20** — `PERF-001` v1.1.0 adds §2.3 (the §35 findings),
    recomputes §4.2's vault-mount overhead from §27's post-fix source
    columns (the old ~25x/~15x were misread improvement factors), and
    marks §10.1's benchmark cadence aspirational with current practice
    stated.
20. ~~Continue the threat model (Milestone 12, `docs/reference/security/`):
    Phase 7 (Package Trust Model, extending NPS-006, already well-motivated
    by `FIND-PACKAGE-001`) is the last planned phase.~~ **Done 2026-08-12**
    — Phase 7 shipped as `NPS-027` (`TB-PACKAGE`, extends NPS-006/NPS-026,
    dispositions `FIND-PACKAGE-001`/`FIND-PACKAGE-004`); the planned
    phase list is complete. Residual resolved 2026-09-21: `NPS-027` is
    **Accepted** (AG decision log D2). Found stale in the same 2026-09-20 audit as
    items 18 and 19.
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
- 2026-09-22 (**the crypto review concluded — D4 — and the trust machinery started building the same day**): the NPC-002 §6.2-reserved review sat with the repo operator (the D1–D3 recorded-session precedent) working from `AG_BRIEF_NPS026_CRYPTO_SCHEME.md`, whose tree claims were re-verified before it sat (G1's `[:8].hex()` key_id, the 10/16/11 test counts, the fail-closed posture, the corrected NPS-028 fence). Decisions: scheme ACCEPTED — Ed25519 + SHA-256 per role with RSA/ECDSA/novel constructions explicitly rejected; **G1 adopted with the display form amended to the FULL 64 lowercase hex** SHA-256 fingerprint (no truncation anywhere — the review's one substantive deviation from the brief's 32-hex proposal); **G3 deferred to §9** (canonical byte form implementation-local until serialization is decided; no interoperability claims until frozen); **G4 confirmed** (single-root MUST-verify, any-root MAY sign); freeze/defer split adopted. Landed: NPS-026 v1.3.0 §6.7 + §6.3.6 re-point; NPS-028's fence narrowed and §3.3 pinned to the decided spelling; AG_AGENDA v1.6.0 (D4 row, standing item disposed); spec index, security README, and this file reconciled; two new premise pins (22/22 → later 23/23). Then **the trust machinery started building** (`backend/package_pki.py` v0.2.0 per NPS-028): §3 key store, §4 verification pipeline, §5 revocation-list verification, §6 enrollment + cross-signed rotation, the §6.7.2 fingerprint throughout — with the G1 migration landed in `package_signing.py` the same session (fingerprint field + version marker, legacy 8-byte `key_id` kept as a `LegacyKeyAliasWarning`-guarded alias, pre-D4 persisted stores re-keyed transparently on load). 36 new tests (fingerprint migration 10, PKI 26); package-security surfaces 91/91 green; full suite 6,420 OK / 4 environmental skips (one pill-variant render flake under full-suite load passed twice in isolation and on re-run); `run_tests.sh` unaffected paths green. Honest scope on NPS-028: custody (§3.4 ADR-0023) and §3.2's store-layer authority enforcement (0600 atomic writes, fail-closed loads) landed the same day; §3.2's daemon-side half (hosting the store behind the daemon's IPC), §7 audit wiring, §5.1 transport, and §5.3 bounds remain named increments.
- 2026-09-22 (**the watcher's own first fire: the interim check exposed that the watcher never watched itself — closed the same morning**): while recording the interim verdict on `scheduled-runs-watch.yml`'s first fire (due 05:52 UTC, still zero runs of any event at 08:02 UTC — within the 8 h grace Monday's data set; the PAT watcher's daily 05:37 fire was also missing through 09:04 UTC, the corroboration for the scheduler-backlog hypothesis — an earlier note here and in the plan wrongly cited the Monday-only amd64 cron as due today, corrected in the plan at 09:15 UTC), the check found a structural gap: `check_scheduled_runs.sh`'s `WORKFLOWS` list did not include `scheduled-runs-watch.yml` itself — the watcher's own cron could die silently while the script reported OK. Closed same session: the workflow list includes it; the script's own in-progress run is excluded via `GITHUB_RUN_ID`; and run judgement is staleness-aware — the latest completed scheduled run must cover the most recent expected fire whose 8 h grace has expired (a cron that fires once and dies previously stayed green on a stale success forever, because only the latest run's conclusion was checked). While the expected fire is still within grace the threshold falls back to the fire before it (walked back from the expected fire, not recomputed from now), so a self-check mid-cadence does not false-alarm on its own prior run. `TestScheduledRunsWatchContract` gains `test_watcher_covers_itself_and_detects_a_dead_schedule` (file 66 → 67); verified live (checker exit 0 against real state) and the dead-schedule rule exercised on synthetic data. First-fire self-check with no prior run reports honestly instead of failing. Full-suite regression check the same morning: `unittest discover` over `tests/` **6,384 tests OK, 4 environmental skips, exit 0**, plus `run_tests.sh` **19/19 suites green**. The dispatch drill was re-executed and failed exactly as documented (HTTP 403, "Resource not accessible by personal access token") — the workflow_dispatch recovery path stays gated on the owner minting a fine-grained PAT with Actions/Variables RW in the browser (a step that must never pass through an agent: `rotate_push_pat.sh` handles everything after the paste). Final first-fire verdict: **PASS — closed 10:23 UTC** (fire landed 4 h 31 m late, run success in 8 s on `0bebd1a`, live-verifying the self-coverage fix; the PAT watcher's fire 10:15:16, 4 h 38 m late, confirmed the shared backlog releasing — see `NEXT_SESSION_PLAN.md`).
- 2026-09-21 (**the arm64 cron's first fire: the alert chain was built, calibrated, and vindicated in one morning**): the arc ran missed-finding → structural fix → false alarm → recalibration → pass. The fire landed 11:49:45 UTC (5 h 49 m late) and run #25 completed success: build ✓, **menu-path GRUB-UEFI boot smoke ✓**, re-attach correctly skipped on its tag gate — the first scheduled arm64 verification is a pass. The 09:05 "missed" verdict was a false alarm the day's own data corrected: the amd64 cron fired 5 h 45 m late and the PAT watcher 5 h 24 m late (one scheduler-wide backlog), so the grace window was recalibrated 3 h → 8 h on measurement rather than doc quotes — same-day alerting preserved by the daily CI job, which is window-size-independent. Honesty note: the fixed 3 h window cried wolf for ~1 h before the amd64 datum arrived; recorded because the watcher's credibility depends on its false-alarm record being public. The cron string itself was never wrong, the workflow was never disabled, and the first scheduled boot-smoke artifact now exists.
- 2026-09-21 (**the missed-cron gap closed the same day it was found — the silence class is now loud**): the arm64 finding got a structural fix, not just a record. `scripts/check_scheduled_runs.sh` gained missed-fire detection: it parses each workflow's declared cron, computes the EXPECTED fire time (forward/backward minute-walk, pure python3, no dependency), and fails with `::error::` when a fire is overdue past a 3 h grace window — anchored on the expected time, not the last run's `created_at`, because a weekly cadence would otherwise let every late run push its own alarm out by a week. Imminent-fire suppression handles the pre-fire CI run. Two real bugs were caught before commit: the cron expression was word-split so `*` fields glob-expanded into repo filenames (fixed with line-by-line process substitution), and the rewrite initially dropped the `WORKFLOWS` list (caught by running it, not by reading it). A new `scheduled-runs-watch.yml` runs the checker daily at 05:52 UTC — after the PAT watcher's 05:37 fire, before the 06:00 ISO fires — so a stopped schedule is a visible red run within a day; `TestScheduledRunsWatchContract` (3 tests) pins the script's fail-closed shape and the CI wiring, per the race-harness no-transcription lesson. Verified live against today's actual state: within-grace OK, overdue (tightened grace) exits 1, imminent suppression fires correctly; full contract file 66/66 green.
- 2026-09-21 (**all three standing AG items decided in one sitting; the tree reconciled; the arm64 cron's first fire recorded missed-or-delayed**): the 2026-09-21 Architecture Group session worked from the three registered pre-reads and decided every standing item per its recommendation — **D1**: dynamic shares do NOT become the default (static `fair_shares` retained, dynamic opt-in normative — the adversarial-optimal posture on the completed §32e/§32f ledger; ADR-0009 v1.3.2 now records "nothing remains open in this ADR"); **D2**: NPS-027 **Accepted** — the planned threat-model phase list (1a/1b/2/3/4/5/6/7) formally closes, and the spec index, security README, and this file's items 18/20 residuals were reconciled; **D3**: the publisher key-trust mechanism adopted as the full ADR-0014 mirror, landing verbatim as NPS-026 v1.2.0 §6.3 (6.3.1 bundled platform root set + TOFU rejection, 6.3.2 protected-confirmation enrollment, 6.3.3 revocation inputs, 6.3.4 the advisory/block propagation split — the one policy call, 6.3.5 cross-signature rotation, 6.3.6 crypto reserved per NPC-002 §6.2), closing `REQ-SEC-0004`. `AG_AGENDA.md` v1.5.0 carries all three decision-log rows and no standing items. Verified same session: premises 15/15 OK, depends-on cycles 0 across 80 documents, `mkdocs build --strict` clean. **Unresolved finding (honest record):** the arm64 cron's first scheduled fire (due Mon 06:00 UTC) had not appeared by 07:35 UTC — the workflow is `active`, the cron string `0 6 * * 1` is pinned by contract tests, and GitHub schedule delays are documented; recorded missed-or-delayed, re-check `scripts/check_scheduled_runs.sh` — recovery paths are the manual workflow_dispatch (still gated on the PAT Actions-write edit) or next Monday's fire.
- 2026-09-20 (**session summary: the wedge day — two verified releases, the premise machinery, and both AG items packaged**; 19 commits, in six arcs):
  (1) **The §27 live-mount wedge root-caused and fixed** (``312c03e``): the
  45 s faulthandler autopsy caught two libfuse workers inside
  ``client_call`` on one shared ``IPCClient`` — reply theft, not a muted
  serve loop; the client now serializes its call exchange. The blocked
  streaming re-measurement then ran for the first time since 2026-08-15.
  (2) **v0.29.29 shipped and verified** (``9d150e6``/``28f2120``/``52eb2d2``):
  ADR-0024 evidence reconciled, the ``gc_blocks`` lock + audit findings,
  a deterministic gc-race demonstration, release digests pinned and all
  three feasible boot paths green.
  (3) **The rate-limiting ledger completed** (``931551d``/``f19da9d``/``5a22d7a``):
  ADR-0009's stale lines corrected, NPS-010 v1.7.0 states the accepted
  posture, the D1 standing item's wrong premise ("data missing") was
  itself corrected and §32f's lone-sender cell completed the
  static-vs-dynamic ledger, packaged as the D1 pre-read brief.
  (4) **v0.29.30 shipped and verified** (``e98b150``/``0b6124b``/``67a7888``):
  the rate-limit operations runbook, limiter overrides persisting across
  daemon restart (the restart test caught the state-clobber bug before
  ship), digests + boots green — and the recorded lesson that boot
  verification is a one-guest-at-a-time operation on this host.
  (5) **The premise machinery** (``93b3cbb``/``30ffebc``/``772a26f``/``d23d5d4``/``d934e84``):
  the hand audit caught ADR-0018's missed status cells and ADR-0009's
  wrong wording; ``check_doc_premises.py`` mechanized it (claim registry,
  now 13 claims) and CI-wired it; the next-actions audit struck
  silently-completed items 18/19/20 (and found 17 long done); one
  honest stumble — a red CI push from cross-file evidence semantics —
  repaired same-hour with the tool fixed rather than the check muted.
  (6) **PERF-001 v1.1.0 and the AG packaging** (``ede2b47``/``e15cf87``/``294c49c``/``5f2010b``):
  §35's container-limit data absorbed, §4.2's misread overhead ratios
  recomputed from source, cadence marked aspirational; NPS-027's review
  registered as standing item D2 with its own pre-read brief; both
  briefs pinned in the registry. Standing threads: PAT grants unchanged
  all day (Actions/Variables write 403; drill fail-closed — the manual
  PAT edit remains the owner's step) and the arm64 cron's first fire is
  tomorrow 06:00 UTC.
- 2026-09-20 (**next-actions audit: three more silently-completed items struck**): the sweep for action-17-style staleness found items 18, 19, and 20 open-in-text with their deliverables long since landed — `NPS-027` (threat model Phase 7, `FIND-PACKAGE-001`'s disposition) has existed since 2026-08-12, and all four of item 19's "remaining" backlog docs (`NPC-010`, `BUILD-001`, `TUT-003`, `PERF-001`) shipped by 2026-09-06. Items 8/9/11/12/21 verified genuinely open (owners unassigned, LICENSE placeholder, contributor-count and concrete-need gates, future AI work). Residuals recorded on the struck items: `NPS-027` awaits Architecture Group review (resolved 2026-09-21 — Accepted, decision log D2); `PERF-001` should absorb the §35 container-resource-limit data (done same day). This is the drift class `tools/check_doc_premises.py` (now in CI) hunts — though TODO-list staleness itself still needs the human audit pass.
- 2026-09-20 (**v0.29.30 shipped: the limiter-persistence release verified end to end**): gates on the bumped tree — drift OK, suite 2,651 green, release-race harness ALL PASS, credential sweep tree + history clean; tag ``v0.29.30`` pushed (main at ``0b6124b``), both tag pipelines green (``live-iso`` and ``live-iso-arm64``, completed/success), release public with both ISOs; both assets re-downloaded anonymously, digest-matched byte-for-byte, and booted on this host — amd64 direct PASS, arm64 direct PASS (solo run; see the note below), arm64 GRUB-UEFI menu PASS; amd64 menu path again environment-blocked (no x86 OVMF firmware, no sudo), CI covered the amd64 direct smoke. Digests pinned at publish: amd64 254,261,248 B ``cd34c51c…``; arm64 268,861,440 B ``81974a43…``. Verification honesty note: the FIRST arm64 direct attempt ran three guests in parallel and FAILED on the 780 s wall — the serial log shows the guest reached the smoke banner and the daemon, it was starvation from guest contention, not an ISO defect; re-run solo it PASSES. Boot verification is a **one-guest-at-a-time** operation on this host; that is now recorded practice. Scheduled-runs watcher OK (arm64's first weekly cron fire remains Mon Sep 21 — tomorrow). The PAT grants remain unchanged (Actions write + Variables write still 403; the drill keeps failing closed at its gate until the manual PAT edit).
- 2026-09-20 (**v0.29.30: limiter overrides persist across a daemon
  restart — the runbook's scope note became a fix**): every
  ``configure_endpoint_rate_limit`` retune records the posture in the
  daemon state file (``endpoint_limit_overrides``, riding EVERY state
  write so a plain save cannot clobber the map — the first test draft
  caught exactly that clobber), and a restarted host re-applies the
  stored posture before serving (unknown ephemeral ids skipped,
  corrupt entries skipped with a warning, the report logged).
  State-file-less hosts are unchanged (fresh defaults; the manual
  re-apply note stands there). 2 new tests (service-level restart
  round-trip + the full host restart shape); suite 2,651 green; the
  runbook scope note updated to describe the persisted behavior.
- 2026-09-20 (**v0.29.29 shipped: the §27 fix + audit ride a verified
  release**): gates on the bumped tree — drift OK, full suite green in
  three chunks (2,649 core + 6,380 tests/ + 101 GPU/installer/SDK
  modules, 4 env skips), release-race harness ALL PASS, credential
  sweep tree + history clean; tag ``v0.29.29`` pushed, main at
  ``9d150e6``; both tag pipelines green in one pass (amd64 build +
  direct smoke; arm64 build + UEFI menu smoke), release public
  immediately with both ISOs; then BOTH assets were re-downloaded
  anonymously, digest-matched, and booted on this host — amd64 direct
  PASS, arm64 direct PASS, arm64 GRUB-UEFI menu PASS; the amd64 menu
  path is **environment-blocked on this host today** (no x86 OVMF
  firmware installed, no sudo) — three of the four local paths ran,
  CI covered the amd64 direct smoke. Digests pinned at publish:
  amd64 254,257,152 B ``3468f7b0…``; arm64 268,853,248 B
  ``2a55d0cb…``. Scheduled-runs watcher OK (arm64's first weekly cron
  fire remains Mon Sep 21, the standing Monday item). Same-session
  followup round: the gc_blocks audit finding now has a **deterministic
  failure demonstration** — the unlocked pre-fix body, pinned by event
  barriers to the losing interleaving (set computed → write+save lands
  a block file → unlink pass), deletes 4 referenced block files and
  the reload fails (``missing block file …``) — reproduced once on
  this host and then removed with its scratch script (the shipped
  lock + stress regression test remain the fix). The PAT grants are
  re-probed and unchanged: identity + Contents OK, Actions write and
  Variables write still 403, the dispatch drill fails closed at its
  403 gate as designed — ungating it remains the manual PAT edit
  (add Actions RW + Variables RW, then ``scripts/verify_pat_grants.sh
  --drill`` runs the round-trip unattended). The Monday cron was
  pre-verified as far as possible without the fire: arm64 cron
  ``0 6 * * 1`` in the YAML, all three scheduled workflows ``active``
  via the API, and pat-expiry-watch already fired green today; the
  arm64 fire itself is tomorrow's check. Reconciliation round: ADR-0009's
  stale "spec-side adoption is the remaining step" line corrected
  (NPS-010 §7.1.1 has been normative since 2026-09-10); NPS-010 v1.7.0
  notes the accepted static-default/dynamic-opt-in posture in §7.1.1;
  the ADR-0009 review package's §5.1 checklist item ticked with the
  decision reference; and ``AG_AGENDA.md`` v1.2.0 carries one standing
  item for the next session — dynamic shares as the DEFAULT, gated on
  the dynamic-mode adversarial re-benchmark that does not exist yet.
- 2026-09-20 (**v0.29.29: the §27 live-mount wedge root-caused — the
  defect was client-side reply theft, not a muted serve loop — and the
  blocked streaming re-measurement collected**): the hunt the 45 s
  faulthandler autopsy armed succeeded, and it corrected the previous
  session's reading. A mid-wedge stack dump caught TWO libfuse worker
  threads simultaneously inside `read → _call → client_call` on ONE
  shared `IPCClient` while the daemon's serving-loop thread stepped
  healthily in `_drive_main_loop` — the passthrough mounts through
  fusepy with libfuse's multithreaded session (`nothreads=False`), so
  kernel ops (readahead pipelines several 128 KiB reads) dispatch
  concurrently, and the IPC client's reply correlation — a
  single-consumer protocol on one socket — let one worker's `recvmsg`
  consume the other's reply (dropped as uncorrelated). The loser timed
  out and every fallback (wire-streamed → streamed → paging) raced the
  same shared socket the same way: "no reply from the storage service"
  three times while the daemon delivered every reply it was asked for.
  Fix: `IPCClient` serializes the whole call exchange per client
  (`call`/`call_stream_write`/`call_stream_reply` under a per-client
  lock — the service side is sequential anyway, one dispatcher
  thread, so this costs nothing); `TestClientConcurrentCalls` pins the
  invariant on both client halves and was verified to fail pre-fix
  with exactly the theft signature. Same-day payoff: the §27 re-run
  completed for the first time since 2026-08-15 — wire-streamed 1 MiB
  writes 9.58–10.47 MB/s (~3× the pre-streaming baseline), 1 MiB reads
  6.30–6.34 MB/s (~3×), 4 KiB/small-file figures host-state-sensitive;
  no gate declared met. This is the post-0.14.21 FUSE-mount evidence
  ADR-0024's review input named as its one missing artifact. Full
  suite green (2,648 tests, `test_backend.py`); repro scratch removed.
- 2026-09-19 (**v0.29.28: the AG decision package shipped; the local
  four-path proof is now standing practice**): gates on the bumped
  tree (drift OK, full suite green, race harness 4/4, credential
  sweep clean), tag ``v0.29.28`` pushed, both pipelines green in one
  pass (amd64 build + smoke; arm64 build + UEFI menu smoke), release
  public immediately with both ISOs — then BOTH assets were
  re-downloaded anonymously, digest-matched, and booted on this host
  on all four paths (2 arches × direct + menu), all PASS — the third
  release verified this way in one day. Digests pinned at publish:
  amd64 243 MB ``ae89763c…``, arm64 256 MB ``49a3d529…``. The
  release carries the audit-log hardening (scheme-2 details
  coverage, one hasher, opt-in persistence), the accepted
  ADR-0007/0009/0013/0016, the ratified ADR-0022/0023, and NPS-010
  v1.6.0 with normative §9 defaults.
  precise**): the Architecture Group decisions were recorded
  (``AG_AGENDA.md``'s decision log, all 13 rows) and applied to the
  tree — ADR-0007/0009/0013/0016 Accepted, ADR-0018's review CLOSED
  (all six sign-off items ticked; the ``audit-b1-hardening`` branch
  merged: scheme-2 hashing, one hasher, opt-in persistence),
  ADR-0022/0023 confirmed + ratified (the stale 2026-08-15 body
  blockquotes were the real polarity source — the frontmatter had
  said Accepted all along), NPS-010 v1.6.0 Accepted with the §9
  defaults normative and the SUSPENDED rule adopted now. Ledger:
  18 ADRs accepted, 4 held, 1 rejected. The replacement PAT arrived
  the same day and was probed: identity + Contents OK (pushes work)
  but **Actions write and Variables write are still 403** — the mint
  omitted exactly the two permissions the drill needs (edit the
  fine-grained PAT to add Actions RW + Variables RW, then
  ``scripts/verify_pat_grants.sh --drill`` runs the whole round-trip
  unattended); the drill failed closed at the 403 gate as designed.
- 2026-09-19 (**the AG decisions applied; the drill's gate made
- 2026-09-19 (**v0.29.27: the demo proven on the machine before the
  release, and the release proven on the machine after**): pre-ship,
  both v0.29.26 assets were downloaded anonymously, digest-pinned,
  and booted on this host — **all four paths** (both arches × direct
  kernel + GRUB menu) green under TCG (no KVM, no sudo here), the
  first time the live demo was verified on the dev host rather than
  only in CI. 0.29.27 ships the docs/benchmarks round (ADR-0018 §34,
  NPS-010 §35, ``AG_AGENDA.md``, the ADR-0018 review package + v2.0.0
  pre-draft, ADR-0024 review input, NPS-026 v1.1.0, and the
  watcher-aware ``check_scheduled_runs.sh``); gates: drift OK,
  contract tests 76 OK (1 env skip), generators 36/36, both new
  benchmark modules re-run green on-host, suite green, race harness
  4/4 on the real attach script, credential sweep tree+history
  clean. Tag ``v0.29.27`` pushed: both tag pipelines green (amd64
  build + direct smoke; arm64 build + UEFI menu smoke; re-attach jobs
  correctly skipped), release public immediately with both ISOs and
  server digests — then BOTH new assets were re-downloaded
  anonymously, digest-matched, and booted again on this host (all
  four paths pass on the shipped bytes). Post-ship,
  ``check_scheduled_runs.sh`` caught two defects in its own new
  watcher check — Python's float ``timestamp()`` prints ``…​.0``
  which bash arithmetic rejects (the >48h silent-death check could
  never run), and the crash fell through into the 120-min watch loop
  — fixed (``int()`` cast, ``--max-time`` on every curl); the check
  then reported OK: live-iso cron green, ``pat-expiry-watch`` fired
  today (the push PAT proven alive on release day), arm64's first
  cron fire expected Mon Sep 21.
  (Digests pinned at publish time: amd64 242 MB ``cf6618b8…``, arm64
  256 MB ``24ce9df8…`` — server-computed fields, matched against the
  local sha256 of both re-downloaded assets.)
``scripts/verify_pat_grants.sh`` probed the same day: identity OK,
  variables-write and actions-write still MISSING (HTTP 403) — the
  dispatch drill and PAT_EXPIRES_AT automation remain gated on a
  replacement PAT minted with Contents RW, Workflows RW, Actions RW,
  Variables RW. The Monday arm64 cron was pre-verified as far as
  possible without the fire: cron ``0 6 * * 1`` in the YAML, workflow
  state ``active`` via the API. The AG review kickoff produced
  ``AG_DECISION_BRIEF.md`` (AI-drafted per-item pre-read with
  recommendations and a consequences ledger — suggest-side only) and
  agenda v1.1.0's pre-flight block, after the sweep found C1's
  premise wrong (ADR-0022/0023 Accepted-in-text since 2026-09-06,
  index stale) and re-verified all of B1's claims in the audit code;
  ``verify_pat_grants.sh --drill`` was pinned to v0.29.27 with its
  fail-closed 403 behavior verified live. The B1 hardening was then
  staged as branch ``audit-b1-hardening`` (f577005 + f089cad, never on
  main): scheme-2 hashing (details covered, None vs {} preserved,
  scheme markers hashed), one hasher behind both families, opt-in
  JSONL snapshot persistence — 25/25 audit tests, full suite green,
  append 22.5 µs/event p50, persistence delta 122 µs/event O(1);
  its own benchmark killed the first full-rewrite design (3800 µs at
  n=200, O(n)). It merges only when the Group directs.
- 2026-09-18 (**v0.29.26: the process shipped a release first-try —
  rehearsed, then real**): the checklist added to CONTRIBUTING was
  validated twice the same day. First a full dress rehearsal: version
  bump + CHANGELOG (``300cfee``), tag ``v0.29.26-test``, both
  pipelines green (amd64 + arm64, direct and menu boot smokes),
  release created PUBLIC with both assets and server digests, then a
  clean rollback — release deleted BEFORE the tag (the draft-zombie
  lesson, applied), tag deleted, main force-with-lease reset, v0.29.25
  verified byte-untouched via its pinned digests. Then the real ship:
  the bump commit was cherry-picked (tree byte-identical, so the
  warm arm64 rootfs cache applied), the checklist ran verbatim (drift
  OK, suite 9,013 OK / 3 env skips, race harness 4/4 on the real
  attach script, credential sweep tree+history clean), tag ``v0.29.26``
  pushed, both tag pipelines green in one pass, release public
  immediately with both ISOs (amd64 242 MB ``76f16a15…``, arm64
  256 MB ``2f49d484…``) and re-attach jobs correctly skipped.
  Contrast with v0.29.25: five rounds, three failure modes, a hidden
  draft, and manual self-serve recovery — v0.29.26 shipped in one
  round with zero interventions. Still gated on token grants
  (Actions/Variables RW): the dispatch drill and PAT_EXPIRES_AT
  automation; the daily ``pat-expiry-watch`` first fire and arm64's
  first cron fire (Mon Sep 21) are the next scheduled verifications.
- 2026-09-18 (**credential hygiene made operational; the rotation
  tool's first version failed its own negative probe — and that is
  why it has one**): the inline-URL PAT moved into a global
  store+cache credential helper (0600 ``~/.git-credentials-nyrqis``,
  credential-free remote URL, validated by real pushes), a daily
  ``pat-expiry-watch.yml`` runway check landed (repo variable
  ``PAT_EXPIRES_AT``: warn unset → warn ≤30 d → red ≤7 d; the PAT's
  true expiry is not API-visible), and ``scripts/rotate_push_pat.sh``
  does one-pass rotation (hidden stdin token, token never in process
  argv via 0600 netrc + 0700 askpass, ls-remote probe, expiry-variable
  upsert with UI fallback). v1 swapped the credential store BEFORE
  validating and a deliberately negative probe then left a FAKE token
  in the live store — recovered from the still-in-session URL, v2 now
  validates first (identity + auth) and the same probe proves the
  store is byte-untouched on rejection. The re-attach drill ran the
  shared script against the LIVE release (dispatch itself is
  actions:write-gated and this PAT lacks it): stale-asset delete →
  curl upload → draft-heal → replacement asset's server digest
  identical to the original — the round-trip is integrity-preserving.
  A pre-drill check also caught that the re-attach jobs read
  ``inputs.release-tag`` without a declared dispatch input (empty
  string → silent skip); declared + pinned. Schedule evidence: amd64's
  weekly cron fired and passed once under the old structure (Sep 14);
  arm64's first fire is Mon Sep 21 — all three crons are now pinned by
  contract tests until then. Suite: 63 boot-contract tests green.
- 2026-09-18 (**release tooling consolidated into one script; the
  race harness had silently drifted**): the v0.29.25 followups exposed
  a structural flaw — the race harness's ``job_body`` was a MANUAL
  TRANSCRIPTION of the workflow attach steps, and it had already
  drifted (still tested ``gh release upload --clobber`` +
  ``--generate-notes`` after the workflows moved to bounded-curl +
  ``--notes``, and its "race" rounds ran the two jobs against
  SEPARATE state dirs, so the concurrent-create path never actually
  raced). The attach logic now lives in exactly one place:
  ``scripts/attach_release_asset.sh`` — run verbatim by both
  workflows' attach steps, by NEW ``re-attach`` workflow_dispatch jobs
  in both workflows (manual recovery from uploads.github.com outages:
  re-attach the already-built ISO artifact; the GITHUB_TOKEN cannot
  re-run failed jobs and a dispatch on main with ``release-tag=v…``
  needs no rebuild), and by the harness itself against expanded fakes
  (``fake_gh.sh`` now models view/create/edit/api incl. the uploadUrl
  and asset-id lookups plus a deterministic create-race-loser mode;
  new ``fake_curl.sh`` keys assets by the URL's ``?name=`` like the
  real endpoint). The harness now shares ONE release state between
  both jobs — the loser path fires for real. Contract tests pin the
  script as an EXACT ``run:`` match (an inline body would regrow the
  twin) and pin the re-attach jobs' gating (``always()`` +
  ``startsWith(inputs.release-tag, 'v')`` + artifact→dist +
  20-min step budget). Also verified: both v0.29.25 release ISOs
  re-downloaded anonymously, local sha256 == server-pinned digest on
  BOTH (d961c51e… / 982878eb…); remote PAT audited — fine-grained,
  repo-admin (contents+workflows write, NO actions-write, which 403'd
  the rerun API), always-expiring, stored plaintext in .git/config and
  echoed to terminal output this session → rotation recommended.
  Suite: 9,011 tests, 3 environmental skips.
- 2026-09-17 (**the v0.29.25 release pipeline: three attach-step
  failure modes, ending in a hidden-draft release**): shipping the tag
  surfaced failures the boot smokes never could. (1) `gh release
  upload` stalled twice on the ~250 MB asset (once 28.5 min to a
  job-budget cancellation with zero evidence, once solo); uploads now
  go through `curl` against the release `upload_url` with a bounded
  `--max-time` per attempt, delete-before-upload idempotency, 3
  retries, and their own 20-min step timeout so a hang dies as a named
  step. (2) `--generate-notes` (server-side generation, a known stall
  point) was replaced with deterministic `--notes`. (3) The decisive
  one: after the tag was force-moved across commits, GitHub converted
  the existing release into a DRAFT — invisible to the anonymous API
  (404, release "gone") while authenticated `gh release view` still
  sees it, so CI happily uploaded both ISOs into a release nobody
  could download; every step "succeeded". The attach steps now
  self-heal with `gh release edit --draft=false` after the upload gate
  (ordering pinned by `TestReleaseUploadContract`), and the tag is
  never force-moved while its release exists. Post-hardening, a
  sustained `uploads.github.com` 5xx window (500/500/hang, then
  500/500/502 across two rounds — untracked on githubstatus, which
  stayed green) beat the in-step retries; recovery was self-serve:
  download the green build's ISO artifact with a PAT, re-upload on the
  endpoint's recovery, and verify the server-side sha256 digest
  against the local hash. Final state: both architectures' ISOs
  attached to a public v0.29.25, digest-verified.
- 2026-09-17 (**the arm64 CI boot smoke finally went green — four
  stacked root causes**): every `live-iso-arm64` run had failed since
  2026-09-14. The causes, in the order CI could not reveal them:
  (1) the `ci.yml` wayland-conformance gate — itself newly pushed —
  failed on bare runners because Pillow (the `WaylandSession` software
  renderer) was never installed; `test_session_render_frame` now skips
  honestly (env-renderer, register v1.3.0) and the gate installs
  `pillow` + `libwayland-dev` (the cdylib dlopens the system Wayland
  library at runtime). (2) A local pure-TCG measurement (banner ~2 min,
  PONG/PKGS/READY at ~685 s on a fast host) showed the old 15-min
  smoke budget — shared with apt install time — could not fit the
  handshake; apt moved to its own step, the driver timeout went to
  1680 s, and `TestJobTimeoutContract` now pins the arithmetic.
  (3) Still failing in 2 s with a 190-byte log, the drivers were
  instrumented to capture QEMU stderr (previously DEVNULL) and carry it
  into check annotations when the serial log compacts to nothing —
  which named cause (4) outright: `failed to find romfile
  "efi-virtio.rom"`. The virt machine's default virtio-net NIC needs
  ipxe-qemu's option ROM, absent under the workflow's
  `--no-install-recommends` apt line; every localhost boot passed only
  because ipxe-qemu was installed there. Both smokes now run `-net
  none` (the serial console is the only channel) and the contract pins
  the flag. First fully green arm64 round: direct smoke 173 s, menu
  path (GRUB UEFI) 108 s. Along the way the first canonical-XML opcode
  run disproved two memory-recalled constants (`WL_DISPLAY_DELETE_ID`
  2→1 — requests and events number in separate sequences; wl_shm has
  no destroy request, only `release`), and the opcode contract test now
  verifies against BOTH `wayland.xml` and `xdg-shell.xml` with full
  enum coverage. Suite: 6,371 tests, 3 environmental skips.
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
  Suite 295 → 299 (273 run + 26 skipped; the v2 transport conformance
  adds the embedded-NUL binary-payload regression test).
- 2026-08-15 (**ADR-0021 first increment: the Rust IPC serving loop**):
  new `rust/ipcd/` (ABI 1.0.0, `libc`-only) — the first
  NyRuntime-shaped artifact. The loop owns poll → recvmsg
  (`SCM_CREDENTIALS`) → wire parse → sender authorization → dispatch →
  reply inside the Rust process and crosses the FFI boundary once per
  *batch* (bounded drain per step), not once per message; the built-in
  `ping` op of the status service is byte-identical to the Python
  floor's reply; non-ping/malformed/forged/unknown senders drop at the
  trust boundary; authorization policy (pid→container, trusted uids,
  operator id) crosses as plain data at loop creation. New
  `ipc/loop.py` driver (established search/ABI/force loader contract),
  `TestRustIpcdLoader` (8) + `TestIpcdLoopConformance` (3), and a
  plan §4.5 restart-recovery e2e (real daemon subprocess recovers a
  stale state file, logs the orphans, atomically replaces the state
  with its own identity). `--ipcd` benchmark (§22): the loop beats
  the floor ~2.8× at the wire median (p50 ~136 µs vs ~387–394 µs,
  2026-08-15) — ADR-0021's differential gate GREEN; the <100 µs
  close gate stayed open (client-side Python cost was the residual,
  the next NyRuntime direction — **closed the same day, see the
  client-half bullet below**). CI: `rust-ipcd` build + required
  `rust-ipcd-conformance` gate. Suite 317 → **329** (300 run + 29
  skipped on crate-less hosts). **Wired into the daemon 2026-08-15:**
  `service serve --health-socket` binds a dedicated health-probe
  socket served by the loop (trusted-uid/operator policy; the floor
  when the crate is absent — byte-identical ping replies either way),
  so liveness probes never contend with container traffic on the main
  service socket and a probe round trip runs through the Rust loop
  (~2.8× faster at the median, §22). The systemd unit passes
  `--health-socket /run/nyrqis/health.sock`. New health-socket tests
  (real-host loop/floor paths + CLI wiring + unit flag). Suite 329 →
  **331**.
- 2026-08-15 (**ADR-0021 per-container pid-table refresh**):
  `nyrqis_ipcd_loop_set_policy` (the policy refresh FFI entry — the
  pid→container/trusted-uid/operator policy behind a `Mutex`, safe to
  refresh while the drive thread is stepping), `IpcdLoop.set_policy`
  in the driver, `ContainerIpcRegistry.set_on_change` (fires after
  every register/unregister mutation; failures swallowed so a policy
  push can never break container lifecycle), and the host's
  `_refresh_health_policy` hooked to the registry — a container whose
  pid enters the registry can now probe the health socket as itself
  (trusted-uid operator policy PLUS the live pid table, re-pushed on
  every spawn/terminate; the floor path reads the registry live and
  needed no change). New tests: registry hook (2), driver-level
  refresh (1), host end-to-end  refresh (1 — registered pid answered
  as its container, removed pid falls back to the operator path,
  identical in both backends). Suite 331 → **335**.
- 2026-08-15 (**ADR-0021 decision point 1 — the non-ping dispatch
  handoff**): `rust/ipcd/` queues authorized non-ping CALLs (bounded,
  fail-closed) and gains `nyrqis_ipcd_loop_drain_requests` (plain-data
  `[u32 len][wire]` records, `-ENOBUFS` contract),
  `nyrqis_ipcd_loop_enqueue_replies` (routes each reply wire to the
  RECORDED sender address captured at recv — the reply routing never
  trusts the wire), and `nyrqis_ipcd_loop_discard_requests` (reaps
  unanswered). New `ipc/dispatch.py` — `IpcdLoopDispatcher` drains the
  batch, dispatches through a `ServiceRouter` into a `_LoopReplySink`
  (services reply exactly as through an `IPCDatagramServer`), enqueues
  the reply wires (built with the floor's own codec, byte-identical),
  and discards the rest; it mirrors the floor's `CAP_IPC_SEND` gate
  for container senders. The health socket now serves `status`/`health`
  through the loop (dedicated status service + router; control ops
  stay off the health socket), with a real-container e2e
  (`test_container_probes_health_socket`) proving the whole chain:
  spawn → auto-registry → change hook → policy refresh → loop →
  dispatch → reply with the container's own identity + grants. The
  reusable drain buffer fixed a 1933 µs → 490 µs dispatch regression
  (per-step 4 MiB allocation). Benchmark §23: dispatch reaches close
  parity with the floor (~490 vs ~405 µs p50 — the Python handler
  cost is inherent per ADR-0021), ping stays ~2.8× faster, the
  pid-table refresh costs ~9.6 µs p50. New tests: dispatch conformance
  (2), loader routing + ENOBUFS retry  (2), host status-via-dispatch +
  control-denied (2), real-container health probe (1). Suite 335 →
  **342**.
- 2026-08-15 (**ADR-0021 close gate MET — the client half of the
  loop + the client-side Python elimination**): `nyrqis_ipcd_client_call`
  (the client half, one FFI call per round trip — sendto → poll →
  recvmsg → correlation in Rust, thread-local reply buffer) wired into
  `IPCClient.call` (Python floor loop = crate-less fallback; a timeout
  never re-sends the CALL). The remaining client-side Python was then
  measured and eliminated piece by piece: the codec's per-field
  `create_string_buffer` marshalling (encode 31.6→8.1 µs, decode
  18.3→13.4 µs, byte-identity preserved), the per-call
  `json.dumps({})` metadata round trip (constant `b"{}"`), the
  per-call 64 KiB reply-buffer allocation (thread-local reuse +
  `string_at`), and the ~6 µs `uuid4` message-id (48-bit CSPRNG
  `os.urandom(6).hex()` — opaque on the wire, excluded from the
  differential, still unguessable). Benchmark §22 re-run: the loop's
  wire p50 is **82–95 µs vs the floor's 263–274 µs** — the close gate
  (beat the floor in the same-session A/B AND <100 µs median) is
  **MET**, and **ADR-0021 moved to Accepted** (its gate language:
  "stays Proposed until the close gate is met"). §23 re-run: dispatch
  ~304–314 vs ~267–272 µs p50 (close parity holds), refresh
  ~8.4–8.7 µs. New tests: client-half loader routing (fake lib),
  conformance (Rust client vs floor server, timeout-without-resend),
  floor fallback path. Suite 342 → **342** (new tests slot into
  existing classes).
- 2026-08-15 (**ADR-0021 main-socket move — the daemon's PRIMARY
  service socket (status + control) is served by the Rust loop**):
  `StatusServiceHost.start()` serves `--socket` through the loop when
  the crate is present — the loop takes the bound fd, the policy
  starts from the live registry snapshot, and the FULL router (status
  + control) is driven by the dispatch handoff, exactly like the
  floor branch's router; the `IPCDatagramServer` floor is the
  crate-less fallback (the router attaches to whichever backend is
  active — exactly one). Control ops (container_run/list/kill) cross
  the loop's batch boundary; the registry change hook is set once and
  refreshes EVERY active loop (`_refresh_loop_policies` — main +
  health). Verified end-to-end by the existing real-container control
  test (now the loop path) and three new host tests (backend
  selection, control dispatch, container-control denial). Suite 347 →
  **350**, green on both paths (crate: loop; crate-less: floor).
- 2026-08-15 (**Rust child entry point — zero Python between clone and
  exec**): `rust/syscalls` gains a real `clone(2)` FFI
  (`nyrqis_clone` — per-call mmap'd child stack, since glibc's clone
  wrapper switches the child's stack pointer even without CLONE_VM)
  and the Rust-native child entry (`nyrqis_launch_child`): the child
  unshares (NEWUSER|NEWNS|NEWPID|NEWNET), writes the uid/gid maps,
  mounts proc, brings up loopback, sets the hostname, closes the sync
  pipe, and execs the launcher — no Python in the setup path.
  `container.py` `_spawn_direct` branches to the clone path when the
  crate is present (`backend/rust_syscalls.py` `clone()`/`launch_child()`
  behind the established loader contract); the Python fork child stays
  as the crate-less fallback. Two ctypes landmines found by the e2e
  and fixed: `c_char_p` array construction from bytes yields
  shared/GC'd pointers (EFAULT in execv — the argv array is now a
  `c_void_p` array of raw addresses into `create_string_buffer`
  keepers), and execv needs a NULL argv terminator (the kernel scans
  past the array end otherwise). Also fixed the SIGTERM-forwarding
  flake: PID-1 semantics discard signals sent before the handler
  install, so `test_init_forwards_sigterm_to_command` now waits for
  the init's `SigCgt` mask to include SIGTERM before signaling. New
  clone-path unit tests + loader marshalling tests (fake-lib,
  function-pointer guard) + real-launch e2e on both paths. Crate
  tests 14/14; suite → **358** OK on both paths (crate: clone;
  crate-less: fork, 35 expected skips). Benchmark §24: main-socket
  control-op A/B — floor ~290 µs vs loop ~336–342 µs p50 (+16–18%):
  close parity, the Python status handler dominates both sides. The
  launcher-init port (seccomp install, cgroup hardening in Rust)
  remains the next NyRuntime step.
- 2026-08-15 (**operator CLI — `nyrqisctl`, the user-facing surface of
  the daemon's control plane**): `nyrqisctl.py` (backend root, beside
  `nyrqis_backend.py`) drives a running daemon's main service socket
  over the IPC transport claiming the operator identity
  (`host-operator`, kernel-attached uid): `ping`/`status`/`health`
  (status service) and `containers list|run|kill` (control service) —
  human-readable output by default, `--json` for raw replies,
  `--socket` to point at the daemon (`/tmp/nyrqis-status.sock`
  default; the systemd unit serves `/run/nyrqis/status.sock`), exit
  0/1/2 (ok / daemon unreachable or op failed / usage). A missing or
  closed daemon socket fails cleanly on BOTH client halves (the floor
  returns `None`, the Rust client half raises `ENOENT`/`ECONNREFUSED`
  — both map to the same "no reply from the daemon" error). The
  status service gained the **operator carve-out**: `status`/`health`
  were `CAP_SYSTEM_INFO`-gated with no operator path, so the daemon's
  own user could not read its own health through the wire — the
  operator (a trusted-uid process the transport already authenticated,
  with full control of the daemon anyway) is now authorized outright,
  the same model the control service uses (container callers stay
  capability-gated fail-closed). New `TestOperatorCli` (10 tests:
  hermetic payloads + formatting + the `run`-positional regression;
  e2e through a REAL daemon — operator ping/status/health answered,
  containers list, and a real-container run→list→kill loop,
  userns-gated). Suite 358 → **368**.
- 2026-08-15 (**the container's PID-1 is now a compiled binary — the
  launcher-init behind the platform boundary (ADR-0020)**): new
  `rust/launcher/` (`nyrqis-launcher`, a Rust BINARY, `libc`-only —
  not a cdylib) does everything `launcher.py` did: sethostname (+prctl
  fallback), cgroup-mount hardening, loopback bring-up,
  SIGPIPE/SIGXFSZ reset, fork + **seccomp install via prctl** +
  `execvp`, signal forwarding, reaping, signal-death propagation
  (128+n), orphan sweep. The seccomp POLICY COMPILATION stays in the
  backend (the allowlist tables live there); the manager serializes
  the compiled classic-BPF program to a `--bpf-file` (`_write_bpf_file`,
  little-endian `<HBBI` sock_filter records — byte-matched to
  `rust/launcher`'s `parse_bpf`) that the binary installs.
  `backend/rust_launcher.py` is the locator (`$NYRQIS_LAUNCHER` →
  crate `target/release/` → PATH; `NYRQIS_LAUNCHER_FORCE=1` for the
  gate) and is deliberately UNCACHED — a stale cached path would make
  spawns exec a dead binary (exit 126; pinned by a regression test).
  `_launcher_exec` hands the container the compiled binary when
  available, `launcher.py` otherwise (the crate-less fallback — the
  fork-path unit tests still pin the Python argv via
  `available()→False`). CI: `rust-launcher` build job (10 unit tests)
  + the required `rust-launcher-conformance` gate. Verified e2e
  through REAL containers: exit status 7 propagated, UTS hostname set,
  the container's seccomp filter ACTIVE (a default-cap file create
  denied → exit 9), SIGTERM to the init forwarded (wait → 128+15),
  network path. Suite 368 → **382**. **Same day:** `nyrqisctl
  --health-socket` (ping/status/health on the ADR-0021 health socket;
  control commands refuse it — exit 2), packaging (man page
  `packaging/man/nyrqisctl.1` + bash/zsh completion
  `packaging/completions/`, install steps in `packaging/README.md`),
  and **ADR-0022 drafted (Proposed)** — NyVault as a daemon-hosted
  storage service on the IPC transport (capability-gated volume
  handles, FUSE passthrough byte path, key management + hardware
  integration deferred to follow-on ADRs).
- 2026-08-15 (**strict seccomp + the NyVault storage service first
  increment + the cold-start benchmark + ADR-0023**):
  `ContainerConfig.strict_seccomp=True` installs the container's
  filter without `SECCOMP_FILTER_FLAG_LOG` (a violation is a hard
  kill, not logged-and-continue), wired through BOTH launcher paths
  (`--strict-seccomp` on the compiled init and `launcher.py`), riding
  only when a filter is actually installed; new test pins both argv
  paths. **NyVault storage service LANDED (ADR-0022 first increment):**
  `ipc/storage.py` (`StorageService`) on the daemon's router —
  first-increment lifecycle ops `volume_create/open/list/close/info`
  (the ADR's byte-path read/write/snapshot ops are the next
  increment), every op gated on the new **`CAP_STORAGE_VOLUME`**
  capability at the same enforcement point as `CAP_SYSTEM_INFO`
  (fail-closed), a per-creator volume registry, and REAL NyFS backing
  (`volume_create` constructs a `NyFSFilesystem` root). Wired into the daemon host alongside
  status/control (operator authorized outright, containers
  capability-gated — the ADR-0022 trust model); `TestStorageService`
  (7 tests: operator lifecycle with NyFS backing, container
  capability gate, fail-closed, duplicate/unknown rejection, creator
  scoping, floor + loop serving paths). Suite 382 → **390**.
  **Container cold-start A/B measured (BENCHMARK_RESULTS.md §25,
  `--launcher-coldstart`):** the compiled init is faster in every run
  and at every percentile — Python p50 stable at 152–157 ms, compiled
  p50 6.3–53.7 ms (userns-clone/scheduler noise; p95 ~55 ms in every
  run, ~3× faster than the Python p50). **ADR-0023 drafted
  (Proposed):** NyVault key manager — envelope encryption (per-volume
  XChaCha20-Poly1305 DEKs wrapped by a daemon-held KEK), KEK never
  stored in plaintext (Argon2id passphrase unlock default, TPM2/
  PKCS#11 hardware backends behind a Rust trait deferred),
  crypto-shredding revocation, rotation without re-encryption, and
  key custody in a Rust crate behind the FFI boundary (Python holds
  opaque handles only, never plaintext keys); approves libsodium as
  the first non-libc dependency for the keys crate.
- 2026-08-15 (**NyVault byte path + operator vault CLI + the key
  manager — ADR-0022/0023 first increments**): the storage service
  gained `volume_write`/`volume_read`/`volume_snapshot`/`volume_snapshots`
  — REAL NyFS I/O through the capability-gated creator-scoped handles
  (create-on-write + mkdir -p blob semantics, offset writes overwrite
  in place, reads page with offset/size, snapshots ride NyFS CoW,
  `..`/trailing-slash paths rejected, 32 KiB per-call payload cap for
  the 64 KiB datagram budget, registry-only volumes refuse byte ops,
  `volume_open` accepts a name). `nyrqisctl vault`
  (`create|open|list|close|write|read|snapshot|snapshots`) drives it
  all — write from `--file`/stdin, read raw bytes to stdout or
  `--output` — verified e2e against a REAL daemon. **The key manager
  landed (ADR-0023):** `backend/keys.py` = PyNaCl floor (Argon2id
  KEK derivation at p=1, XChaCha20-Poly1305 envelope, 110-byte KEK
  envelope with AEAD check value, DEK wrap/unwrap, fail-closed on
  wrong secret + tampering) + loader (ABI 1.0.0 gate, `NYRQIS_KEYS_LIB`
  override, `NYRQIS_RUST_FORCE=1` gate, floor fallback); `rust/keys/`
  = the custody boundary — same construction in Rust (RustCrypto
  argon2 + chacha20poly1305, the ADR's approved non-libc deps), the
  KEK held ONLY in the crate's handle table (unlock → opaque u64
  handle, plaintext never crosses FFI). **Differential conformance
  verified:** KDF + wrapped-DEK bytes identical, cross-interop both
  ways, wrong-secret/tamper rejected on both, shred invalidates.
  New CI jobs `rust-keys` + required `rust-keys-conformance`. Suite
  390 → **412**. KEK wiring into `volume_create` is the next increment
  (at-rest encryption NOT yet claimed).
- 2026-08-15 (**NyVault at rest + the FUSE passthrough — 0.14.5**):
  the encrypted-vault lifecycle is complete (ADR-0023's core claim) —
  `nyrqisctl vault init` writes the Argon2id KEK envelope; the daemon
  serves with `--vault-key-file` + passphrase (unlock at serve time,
  fail-closed on a wrong secret); `volume_create` wraps a fresh
  per-volume DEK with the KEK; and the **block layer is
  AEAD-encrypted** — `rust/keys` + the PyNaCl floor gain
  `block_encrypt`/`block_decrypt` and `NyFSFilesystem(dek=...)`
  threads the DEK through the single write/read funnels (`_make_block`
  / `_decompress_verified`), so every block at rest is
  `nonce ‖ ciphertext ‖ tag` and no plaintext exists under the vault
  dir (verified). `volume_delete` crypto-shreds; the registry + wrapped
  DEKs persist across a daemon restart. **The NyVault FUSE passthrough
  (ADR-0022's data-plane mount) landed:** `fuse/vault_mount.py` —
  `NyVaultOperations` are FUSE ops whose handlers are storage-service
  CALLs (getattr/readdir/read/write/mkdir/mknod/unlink/rmdir/rename/
  truncate/statfs/fsync), paging the 32 KiB per-call byte path, with
  errno propagation; `NyVaultMount` mirrors `NyFSMount` (honest
  deferral without fusepy); the service's generic file surface
  (`volume_getattr`/`volume_readdir`/`volume_mkdir`/...) sits behind
  the same capability + handle + path gates; `nyrqisctl vault mount`.
  **§26 vault-io benchmark:** the durable `save()` commit dominates
  writes (~86 ms p50 — one fsync per transaction, the §9/§15 finding
  again), reads run at 1.6–2.8 ms p50 flat across payloads, and the
  block AEAD adds ~0.5 ms on 32 KiB reads. Suite 412 → **427**.
- 2026-08-15 (**KEK rotation + the encrypted vault VERIFIED through a real
  kernel FUSE mount + systemd vault wiring — 0.14.6**): `volume_rekey`
  (OPERATOR-ONLY) rotates the KEK without re-encrypting any block
  (unwrap with the current KEK, re-wrap with the new one, persist; the
  reply carries the matching new envelope — `nyrqisctl vault rekey
  --new-passphrase Q --new-key-file F`, restart under the new key;
  verified: data reads back under the new key, the old key fails closed
  with an honest "vault key mismatch"). **The encrypted vault was mounted
  LIVE and verified** (first live verification of the data-plane mount):
  kernel write/fsync/read/mkdir/root-readdir/stat through `nyrqisctl
  vault mount`, no plaintext under the vault dir. The live attempt found
  and fixed two real bugs: `_check_path` rejected the volume root `/`
  (breaking `readdir("/")`), and the CLI's background-thread mount died
  with the exiting process (the CLI now serves the FUSE loop in its
  foreground until unmounted). `volume_open` canonicalizes id-or-name
  resolution. New `TestNyVaultLiveMount` (2, skip-gated). systemd unit:
  `StateDirectory=nyrqis` + `--vault-dir`/`--vault-key-file`/optional
  `EnvironmentFile` passphrase. Suite 427 → **432**.- 2026-08-16 (**the streaming data plane — ADR-0024 first increment, 0.14.20**):
  large passthrough writes/reads ride ONE pipelined stream instead of
  N sequential ≤32 KiB CALLs — chunks are ordinary capability-gated
  `volume_write` CALLs with a `stream_id`/`stream_index`/`stream_count`
  envelope + per-chunk SHA-256; the service reassembles (out-of-order
  OK, bound to the first chunk's sender, ≤512 chunks / 30 s TTL,
  duplicate/mismatch/checksum failure reject the stream) and performs
  ONE write/quota-check/accounting/commit on the final chunk;
  streamed reads page in-process and return correlated ≤32 KiB REPLY
  pieces the client reassembles by index. The wire codec is untouched
  (byte-identical gate green; the Rust loop needs no change) — the
  ADR's wire-level framing (codec flag + Rust loop reassembly) is the
  documented follow-on. `volume_open` advertises `stream: true`;
  older peers keep paging (the paging path stays forever). Client
  halves: `call_stream_write` + `call_stream_reply` (floor path).
  **Measured §29 (`--vault-stream`)**: 1 MiB writes 5.6× / 6.6×
  (plaintext/encrypted) faster than paged; reads ~1.04× (already
  flat). Suite 466 → **479**.
- 2026-08-16 (**wire-level streaming — the ADR-0024 follow-on, 0.14.21**):
  STREAM_CHUNK is a first-class wire message type (5) in the codec on
  BOTH halves (rust/ipc + `ipc_codec.py`, byte-identical,
  differential-gated) — the envelope (`version ‖ stream_id ‖ call_id ‖
  index ‖ count ‖ payload ‖ sha256`) rides the payload field and the
  codec's `reply_to` carries chunk correlation. **Both serving paths
  reassemble**: the floor transport (window/TTL/sender-bind,
  chunked REPLYs via `build_reply_wires`) and the Rust serving loop
  (rust/ipcd: per-chunk SHA-256, rebuilt CALL wire to pending,
  chunked reply routing without consuming pending — the loop serves
  the daemon's socket in production, so loop reassembly is what makes
  the path real). The client gains `wire_stream=True` (chunked send +
  chunked-reply reassembly, floor path); the service's plain
  write/read accept the wire-stream DATA budget and `volume_open`
  advertises `stream_ver: 2`, with the service-level envelope +
  paging staying for old peers; a payload beyond the 512-chunk window
  is refused client-side immediately. **Also fixed the transport
  close-race the wire-level path exposed**: `close()` now joins the
  serve loop before releasing the socket — a server torn down with
  `stop.set(); close()` left its serve thread mid-poll, the next bind
  reused the freed fd, and the stale poll stole ONE datagram from the
  new socket (a lost STREAM_CHUNK left reassembly one chunk short;
  the caller timed out). close() is synchronous and safe (path
  unlinked before it returns; serve-after-close returns immediately).
  Suite 479 → **492** (both crate paths green).
- 2026-08-16 (**ADR-0024 drafted (Proposed) — the streaming data plane**):
  the documented next step of ADR-0022's data plane — chunked
  framing for CALL/REPLY payloads beyond the single-datagram budget
  (stream_id + ordered, checksummed 32 KiB chunks; receiver-side
  reassembly bound by an in-flight window and a TTL; per-datagram
  kernel identity and the ADR-0009 token bucket still apply; ≤32 KiB
  calls byte-identical — back-compat first-class; the paging loop
  collapses to one streaming CALL per kernel request). Written before
  implementation per the evidence-first rule; the §29 `--vault-stream`
  benchmark is the acceptance gate. Registered in the ADR index
  (README + NPC-005 + mkdocs nav — the nav and NPC-005 also gained
  the missing ADR-0022/0023 rows).
- 2026-08-16 (**per-subtree quotas — 0.14.19**): `volume_quota_set`
  gains a `path` scope — the quota becomes an ADDITIONAL cap on
  writes under that scope; every applicable cap (whole-volume AND
  each scoped quota containing the path) must pass, so nested scopes
  overlap by design. Fail-closed EDQUOT before the tree is touched;
  the scoped EDQUOT carries its scope in the error and the event
  ring. Scoped usage billed incrementally between commits and
  re-derived from the tree at each commit (delete re-accounts it
  away); quotas + usage persist with the registry. `quota-get` rows
  gain a `scope` column; `usage` reports `scope_usage`; CLI `vault
  quota-set --path /assets`. Verified e2e against a real encrypted
  daemon. Advisory warnings stay whole-volume-only; scoped quotas
  enforce the hard stop. Suite 464 → **466**.
- 2026-08-16 (**the event ring survives a restart — 0.14.18**): the
  ring persists with the registry at every commit — grant/revoke and
  quota-transition events ride the same registry write, so the
  operator's recent history survives a daemon restart (tested). It
  stays bounded diagnostics (64, newest first); the registry is still
  the source of truth for current state. Honest boundary: the FUSE
  kernel mount is operator-only and the operator is never
  path-restricted, so a scoped grant's EACCES is exercised by the
  grantee's own data plane (0.14.16), never through a kernel mount.
  Suite 463 → **464**.
- 2026-08-16 (**the access matrix joins the event ring — 0.14.17**):
  the ring records grant/revoke actions alongside the quota signal —
  a `grant` logs who, when, and how wide the scope; a `revoke` logs
  what was actually withdrawn (the scope the grantee held). Events
  carry a `kind` (`grant`/`revoke`/`quota`); quota events keep
  level/usage/quota, grant events carry scope. Ring stays bounded
  (64), newest-first, in-memory, OPERATOR-ONLY. `vault events` prints
  the kind column (grant/revoke rows `scope=...`); verified e2e
  against a real daemon. Suite 462 → **463**.
- 2026-08-16 (**path-scoped grants e2e + the honest EACCES — 0.14.16**):
  the grant-scope rejection now rides the CALL reply with errno 13
  (EACCES — a permission denial, not a generic EIO), so the FUSE
  passthrough surfaces it to the kernel. Verified through a REAL
  seccomp container with a path-scoped grant (`/assets`) on an
  encrypted volume: in-scope write lands, out-of-scope write AND read
  are denied with EACCES, and the operator confirms the rejected path
  never reached the tree. Suite 461 → **462**.
- 2026-08-16 (**path-scoped grants + admin-op tightening — 0.14.15**):
  a grant may now carry a `path` scope (`/subtree`) — the grantee
  opens the volume but every data-plane op outside the subtree is
  rejected fail-closed (write, read, rename — BOTH sides must stay in
  scope — and truncate); a bare grant stays whole-volume, persisted
  back-compatibly as `True` (0.14.8 shape), a scoped grant as
  `{"path": ...}` (restart-tested). The creator/operator are never
  path-restricted. **Admin-op tightening**: snapshot / restore /
  snapshot-delete rewrite or capture the WHOLE tree, so they are now
  CREATOR/OPERATOR-ONLY (a grantee fails closed with "creator or the
  operator" even with a valid handle).  CLI: `vault grant --path
  /assets`; `vault grants` prints scoped grants as `container@path`.
  Suite 458 → **461**.
- 2026-08-16 (**the quota-event ring — 0.14.14**): `volume_events` (OPERATOR-ONLY) + `nyrqisctl vault events` expose
  the in-memory quota-event ring (bounded at 64, newest first):
  warning-level transitions (near/at/over — the same points the log
  lines fire) and every EDQUOT rejection (the hard stop, the most
  actionable event). Honest scope: the ring is diagnostics, never
  persisted — the ledger is the durable source of truth. A container
  is refused the op even with the storage capability. Suite 456 →
  **458**.
- 2026-08-16 (**the vault at a glance — 0.14.13**): `status` and
  `health` now report the vault aggregate (volumes, total logical +
  physical bytes, warned containers) from the CACHED ledger figures
  — no tree walk, so status stays O(volumes) (the §28 refresh is
  what `volume_summary` is for). The status service already holds the
  daemon reference, so the block rides both the main-socket and
  health-socket status services with zero host wiring; a bare service
  reports `vault: null`. `nyrqisctl status`/`health` print the line.
  Warning levels verified through a REAL kernel mount (a kernel write
  past 80% commits at fsync → `near` surfaces in `vault quota-get`).
  Suite 454 → **456**.
- 2026-08-16 (**quota warnings — 0.14.12**): warning levels
  (`near` ≥ 80%, `at` ≥ 95%, `over` > 100%) computed at every ledger
  refresh, logged only on a level transition (no spam), persisted
  with the registry. `over` is unreachable by writing (the write path
  rejects it) — only via re-derivation (quota set below existing
  usage, or a restore to a larger snapshot; both tested). Surfaced in
  `volume_quota_get` rows, `volume_usage` warnings, `volume_summary`
  `warning_count`, and the write REPLY (`nyrqisctl vault write`
  prints `(quota warning: near)` at the point of action). Clearing a
  quota drops the signal. Suite 452 → **454**.
- 2026-08-16 (**the operator's vault view — 0.14.11**):
  `volume_usage` also reports the volume-wide PHYSICAL figure (the
  on-disk state footprint, compressed + CoW-deduped, cached with the
  ledger at each commit) — volume-wide, never per-container (CoW
  sharing makes per-container physical attribution load-dependent;
  honest in the ADR + runbook). Verified: 9 KiB compressible →
  logical 9000, physical 902. `volume_info.bytes_persisted` now uses
  the same helper (it previously counted only the post-compaction
  `blocks/` dir and reported 0 for journal-resident state).
  **`volume_summary` (OPERATOR-ONLY) + `nyrqisctl vault summary`**: the
  whole-vault aggregate — volume count, total logical/physical bytes,
  per-volume rows (logical, physical, consumers), re-derived fresh;
  a granted container is refused even with the capability. **§28
  benchmark (`--ledger-refresh`)**: the per-commit usage refresh
  measures 0.53–0.67 ms @ 1 k files, 7.79–8.93 ms @ 10 k — a rounding
  error next to the ~110 ms durable save it rides on. Suite 450 →
  **452**.
- 2026-08-16 (**per-container quota & accounting — 0.14.10**):
  ADR-0022's follow-on design is implemented. Every volume accounts
  bytes per container (`volume_usage`), billed to the WRITING
  container at `volume_write`; reads are free and `volume_truncate`
  credits the owner the size delta. Attribution is a per-path
  last-writer map (`owners`); the ledger is a cache re-derived from
  the NyFS tree at every commit (fsync / interval / close / restore —
  NyFS gains a public `walk()`), so deletes/truncates/renames/
  restores re-account exactly what the tree holds (verified: delete
  100 → usage 50; restore to the 100-byte snapshot → usage 100).
  Logical bytes (sum of file sizes) is the operator contract;
  physical block bytes (CoW/compression) are deliberately not billed
  — stated honestly in the ADR. `volume_quota_set`
  (CREATOR/OPERATOR-ONLY) sets a per-container byte quota (`bytes:
  null` clears; unlimited default); the write path rejects
  fail-closed with **EDQUOT (errno 122)** before touching the tree,
  the errno riding the reply to the FUSE passthrough — **verified
  through a real kernel mount** (an over-quota write on the live
  encrypted mount raises EDQUOT at the syscall, not a generic EIO;
  the fail-closed rejection does not wedge the volume). Quotas +
  usage + attribution persist in the registry at every commit
  (restart-safe). CLI: `vault quota-set/quota-get/usage`, verified
  e2e against a real daemon. Suite 440 → **450**.
- 2026-08-15 (**group commit + the granted-container data plane +
  the quota design — 0.14.9**): the FUSE `flush` handler is no longer
  a durability boundary (POSIX: close ≠ durable — fsync is the
  contract); `volume_flush` is a group-commit opportunity and the
  service persists the deferred batch at the commit-interval tick
  (`--commit-interval`, default 5 s; 0 = fsync/close only) — a burst
  of short-lived files pays ONE save per interval instead of one per
  close. Verified: flush defers, fsync/close/interval commit. §27
  re-bench adds a small-files burst pattern (~260 files/s through the
  encrypted passthrough vs ~11–21 k native — the per-op CALL +
  AEAD cost, not commits). **Granted-container e2e**: a real seccomp
  container with an explicit volume grant opens an ENCRYPTED volume
  by name and drives the passthrough's ops over the wire (the kernel
  mount is operator/host-only by design — `mount` is in seccomp's
  always-deny set; documented in the runbook). CLI e2e: `vault
  snapshot-delete`. ADR-0022 gains the quota & accounting follow-on
  design (per-container bytes, billing the writer, fail-closed
  EDQUOT — design only). Suite 437 → **440**.
- 2026-08-15 (**write-commit batching + the cross-container grant
  matrix + snapshot deletion — 0.14.8**): `volume_write` defers the
  durable commit (in-memory dirty blocks) and
  `volume_fsync`/`volume_flush`/`volume_close` anchor it — a kernel
  write pays ONE save() at the flush boundary instead of one per CALL;
  the passthrough gained the `flush` handler. §27 re-bench: streaming
  writes **0.28 → 3.17 MB/s (11×)**, 4 KiB syscalls **0.04 → 0.78
  MB/s (19×)**; §26 byte-path writes ~86 ms → ~2.2 ms p50 (~40×).
  **Cross-container grants (ADR-0022's access matrix landed):**
  `volume_grant`/`volume_revoke`/`volume_grants` (CREATOR/OPERATOR-
  ONLY) + `nyrqisctl vault grant/revoke/grants` — grants are
  per-container, persisted, and never imply `CAP_STORAGE_VOLUME`;
  `volume_open`/`volume_list` honor them; revoke gates future opens
  while a live handle keeps working. **Snapshot deletion:**
  `NyFS.delete_snapshot` + `volume_snapshot_delete` +
  `nyrqisctl vault snapshot-delete` (missing snapshot fails honestly).
  Runbook §3b. Suite 434 → **437**.
- 2026-08-15 (**snapshot restore + the live encrypted-mount benchmark
  (§27) + the vault runbook — 0.14.7**): `volume_restore` +
  `nyrqisctl vault restore` (snapshot table unchanged; the restored
  tree is what save() persists), verified over the wire and through the
  live encrypted mount (kernel write → snapshot → kernel overwrite →
  restore). **§27 (`--vault-mount-io`):** the durable per-CALL commit
  dominates writes (1 MiB ≈ 32 CALLs ≈ 110 ms each → 0.28 MB/s vs
  native ~1,700 MB/s; reads ~2.1 MB/s). The benchmark exposed a real
  bug: the passthrough adapter never registered an `init` marker, so
  the write-batching INIT negotiation silently never ran (4 KiB
  requests → 0.04 MB/s); fixed (marker + shared BIG_WRITES/WRITEBACK
  negotiation) — **7× on streaming writes**. Next step: write-commit
  batching (`volume_fsync` anchors it). Operator runbook:
  `docs/how-to/operate-the-vault.md`. Suite 432 → **434**.
- 2026-08-14 (**plan §4.5: persistent state + health checks + syslog**):
  new `backend/daemon_state.py` (`DaemonStateFile` — versioned,
  atomically-written JSON: daemon identity + last-known container
  manifest; recovery is reporting, never resumption); the status
  service serves a `health` op (liveness, container load, registry
  size, `state_persisted`, crash-recovery record — gated on
  `CAP_SYSTEM_INFO`, fail-closed); `setup_logging(syslog=True)`
  mirrors to the journal via `/dev/log`; `service serve` gains
  `--syslog --state-file` and the systemd unit passes both (state in
  the `RuntimeDirectory`); mutating control ops refresh the manifest
  via a `state_saver` hook. New `TestDaemonState` (11) +
  `TestLoggingConfig` (3) + health-op tests + control saver test;
  `TestSystemdUnit` asserts the new flags. Suite 299 → 317 (291 run +
  26 skipped).
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
  **FFI surface v2 (ABI 2.0.0, 2026-08-14): caller-supplied buffers —
  recv recvmsgs directly into the caller's reusable wire buffer (zero
  malloc/free, `nyrqis_transport_free` gone), send is zero-copy;
  measured wire p50 307–357 µs (~28% under v1's ~426 µs, ~1.6× the
  ~200 µs floor) with the residual the ctypes boundary tax. Gate
  stays open; the migration stands on the boundary rule +
  byte-identical conformance.**
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
  flip's governance review package is **ADR-0019** — Accepted
  2026-09-30 (RATIFIED AS-IMPLEMENTED, Bundle D).
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
