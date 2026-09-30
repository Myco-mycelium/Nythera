# NyFS Mount Daemon — Lifecycle and Compaction Scheduling (design)

> **Bundle D DECIDED 2026-09-30 (RATIFY-AS-IMPLEMENTED, AG decision-log
> row D1, owner direction via the recorded session):** §4 item 3's
> "still open" caveat and §5's "Architecture Group tuning review" open
> item are struck with the decision note — the interval/threshold
> defaults are ratified as tuning knobs, not correctness properties;
> the watcher resource profile on real hardware remains a documented
> follow-up (first measurement recorded in §3's Resource profile;
> real-hardware capture is the open part). The §2 shutdown contract
> and §4 default flip are now fully sanctioned by ADR-0019's
> acceptance.

**Status: design note (not a normative spec) — partially implemented
2026-08-12.** Written to answer ADR-0019's open question 1 — *should
`auto_compact` become the default?* The shutdown contract (§2) and the
`auto_compact` default flip (§4) are now **implemented**
(`NyFSMount.shutdown()`, signal handlers in `mount(blocking=True)`,
`NyFSFilesystem.dirty` gate, `mount(auto_compact=True)` default),
with tests (`test_shutdown_commits_dirty_state`,
`test_auto_compact_is_the_mount_default`, `test_dirty_flag_tracking`).
The strict async-signal-safety refinement remains open (§5); the
Architecture Group tuning review closed 2026-09-30 with ADR-0019's
ratification — the interval/threshold defaults stand as tuning knobs.

## 1. Process lifecycle today

- `NyFSMount.mount(blocking=…)` → `NyFSOperations` wired to a real
  kernel FUSE mount. `fsync` on any open file commits the filesystem
  (`NyFSOperations.fsync` → `NyFSFilesystem.save()`), which is the
  durability contract (NPS-004 §7).
- `unmount()` stops the background compaction watcher (if running),
  then unmounts via fusepy or `fusermount -u`.
- There is no signal handling and no lifecycle state machine today:
  the daemon is whatever the embedding process makes of it.

## 2. Shutdown contract

**Implemented 2026-08-12** (`NyFSMount._install_signal_handlers` /
`shutdown`, enabled by `mount(handle_signals=True)`, the default in
blocking mode). The daemon installs handlers for `SIGINT`/`SIGTERM`
that run an orderly shutdown:

1. Stop accepting new FUSE requests (unmount the kernel side; pending
   requests drain).
2. Stop the compaction watcher (already implemented: `unmount()`
   signals and joins it).
3. Commit once more (`save()`) if there is uncommitted state, so an
   orderly stop never loses acknowledged-but-unfsynced work.
4. Unmount.

A `SIGKILL` (or crash) at any point is already safe by design: the
metadata swap is the commit point and the journal is append-only and
torn-tail tolerant, so recovery is `load()` — no journal replay step is
needed beyond what `load()` already does.

Ordering requirement: the final `save()` **MUST** happen before the
unmount in step 4 (the unmount itself does not save; today a daemon
that relied on unmount for durability would be wrong).

Implementation notes (2026-08-12): the dirty gate exists — every
mutating operation sets `NyFSFilesystem._dirty`, `save()` clears it,
and `shutdown()` commits only when `fs.dirty` is True. The signal
handler runs Python-level work (watcher stop, final save, unmount) in
CPython's main thread between bytecodes — reliable in practice for
this daemon; strict POSIX async-signal-safety (self-pipe + main-loop
check) remains listed under §5 open items.

## 3. Compaction scheduling

Two mechanisms exist:

- **save-time (inline):** after each commit, `save()` compacts when the
  journal exceeds `journal_compact_bytes` (default 64 MiB). A
  transaction that crosses the threshold pays the materialize pass
  inline (§14: ~27 ms/block).
- **background (idle):** `mount(auto_compact=True)` runs a daemon
  thread calling `maybe_compact(threshold=…)` every
  `compact_interval` seconds. The default threshold is *half* of
  `journal_compact_bytes`, so the journal is trimmed well before the
  save-time threshold and inline compaction stalls become rare.

Design intent: compaction is *amortized background work*, not a
transaction cost. The journal grows between compactions by design; the
two thresholds bound that growth (inline at 64 MiB, background at
~32 MiB).

### Resource profile (the ADR-0019 tuning follow-up)

First measurement 2026-09-30, dev-VM class — real-hardware numbers
remain the open follow-up (capture method below):

- **Idle (no-op) path — measured:** under the background threshold,
  `maybe_compact` does a journal-bytes check under the filesystem lock
  — no I/O. **24.71 µs/wakeup** over a 120,000-call sample (warm,
  journal below 32 MiB), i.e. ~**35.6 ms CPU/day** at the shipped 60 s
  cadence (~0.0004% duty cycle). Wake is `Event.wait(60.0)` — a timer,
  not busy-polling.
- **Loop machinery memory — measured:** ~5 KiB current / ~6 KiB peak
  traced allocations for the watcher loop itself (thread object +
  Event + frame; excludes the interpreter's thread stack).
- **Active pass — bounded, not re-measured:** a real pass is §14's
  interleaved save of referenced blocks (~27 ms/block), bounded by the
  background threshold (~32 MiB default) and serialized with saves
  under the filesystem lock; §14's 11.2 s / 417-block figure is the
  scaling anchor.
- **Stop path — implemented:** `Event.set()` + `join(5.0)`; a pass in
  flight completes and the daemon thread exits at the next interval —
  it never blocks process exit (the §2 shutdown contract).
- **What real hardware still must show** (the follow-up this records):
  (1) the §14 pass cost re-run on target-class storage (SSD vs HDD);
  (2) a 24 h idle-RSS soak of a live mount (thread-stack residency is
  not visible to `tracemalloc`); (3) wake-to-wake jitter under
  concurrent load. **Method (executable): `python3
  tests/bench_watcher_profile.py`** — device-labeled, self-checking;
  `--quick` is a ~2 min smoke, the default runs the full 24 h soak.
  Proven end-to-end on the dev VM 2026-09-30 (first real pass 26.9
  ms/block — inside §14's ~27 ms band; RSS drift 4 KiB over 90 s with
  the watcher live; wake jitter p95 +4.2 ms at a 1 s-scaled cadence).
  Nothing here is a gate — the defaults are ratified as tuning knobs
  (§4).

## 4. Making auto_compact the default

`auto_compact` **SHOULD** become the mount default (`True`) once the
following are true — this is the recommendation for ADR-0019 open
question 1:

1. **Section 2's shutdown contract is implemented** — **DONE
   2026-08-12**: signal handlers + dirty-gated final commit + ordering,
   watcher always stopped cleanly.
2. **The watcher's resource profile is documented:** it is a daemon
   thread holding a reference to the filesystem; on unmount it is
   joined (5 s) and, if still mid-pass, finishes at its next interval
   (it never blocks process exit). A long-running daemon sees one
   ~11 s pass per ~2.5 MB of new blocks (§14).
3. **Default tuning is reviewed by Architecture Group:** interval
   60 s and half-threshold are starting points, not measurements —
   **DONE 2026-09-30**: the review sat as AG_AGENDA Bundle D and
   RATIFIED the shipped defaults AS-IMPLEMENTED (AG decision-log row
   D1) — the values stand as tuning knobs, not correctness
   properties; the watcher's real-hardware resource profile remains a
   documented follow-up, not a gate.

**2026-08-12 status:** items 1 and 2 are satisfied, so the default
flip was made (`NyFSMount.mount(auto_compact=True)`). **2026-09-30:
item 3 closed** — the formal gate is satisfied by the ratification;
nothing remains open in §4.

## 5. Open items

- A lifecycle state machine (STARTED → LIVE → DRAINING → STOPPED) to
  make the shutdown ordering explicit and testable end-to-end.
- Strict POSIX async-signal-safety for the signal path (self-pipe +
  main-loop check) instead of Python-level work inside the handler.
- Measure the watcher's idle-CPU cost (one `stat()` + possibly one
  compaction per interval — expected negligible, but the plan's
  pending concurrent-load CPU measurement (§2/§4) can quantify it).
- ~~Architecture Group tuning review of the `compact_interval` /
  half-threshold defaults~~ **CLOSED 2026-09-30** — ratified
  AS-IMPLEMENTED with ADR-0019's acceptance (Bundle D, decision-log
  row D1); only the real-hardware resource-profile documentation
  remains, as a tuning follow-up rather than an open decision.

## References

- `fuse/nyfs.py` — `NyFSFilesystem.save/maybe_compact/compact_journal`,
  `NyFSMount.mount(auto_compact=…)`, `unmount()`.
- `tests/BENCHMARK_RESULTS.md` §14 (compaction cost), §9 (journal
  commit), ADR-0019 (journal default review package — open question 1
  is this document's reason to exist).
