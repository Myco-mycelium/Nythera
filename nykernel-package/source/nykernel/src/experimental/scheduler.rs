//! Cooperative, round-robin, stack-switching task scheduler.
//!
//! The old "this is not NPS-002 conformance" claim is withdrawn:
//! `NPS-002` (Process and Thread Model) was **Accepted 2026-10-11** (AG
//! decision-log row I1, owner direction via the recorded session), so
//! this module now implements against NPS-002 requirements as binding.
//! The requirement this increment targets is NPS-002 §5's process
//! lifecycle:
//!
//! ```text
//! CREATED → READY → RUNNING → (BLOCKED ⇄ RUNNING) → TERMINATING → TERMINATED
//! ```
//!
//! What is now *enforced*, every boot, as ordinary code (not a
//! temporary control): each task moves CREATED → READY at spawn time,
//! READY → RUNNING when dispatched, and RUNNING → READY when it yields,
//! per §5.2–§5.4; `yield_task` runs the transition through
//! [`set_state`], and an illegal transition attempt (e.g. BLOCKED →
//! RUNNING from any non-yield path, or anything → anything past
//! TERMINATED) is refused, counted, and printed rather than silently
//! allowed. The positive analog is proven by the existing A/B demo:
//! every one of its 6 round-trip activations must have animated exactly
//! via the legal path, or the counters can't reach their asserted
//! values. Still honest scope: this ring-0 scheduler demonstrates an
//! enforcing lifecycle for *kernel threads*; NPS-002 §4's
//! container-to-process mapping (§4.1 exclusion, §4.3 capability
//! subset) is a later increment in this module's track, not landed
//! here, and §9's real-time guarantee numbers remain a benchmark /
//! tuning follow-up on ADR-0013's accepted design (see
//! `crate::experimental`'s banner).
//!
//! Deliberately cooperative (tasks call [`yield_task`]
//! themselves), not preemptive -- preemption adds a second class of
//! correctness bug this module has no reason to take on while the
//! ring-3 round-robin in `crate::experimental::ring3_scheduler` (its
//! own cooperative phase, and its separately-proven timer-driven
//! preemption phase) exists in the tree and passes every boot.
//!
//! Extended to also switch `CR3` as part of every task switch (see
//! [`spawn_with_cr3`]) -- a task can now have its own private address
//! space (built via `experimental::address_space`), integrated into
//! ordinary cooperative scheduling rather than the earlier,
//! hand-orchestrated `experimental::isolation_demo` sequence. See
//! `kernel_main`'s isolated-task demo for the proof this actually
//! works under *repeated* switching, not just once.

use alloc::vec::Vec;
use core::arch::naked_asm;
use x86_64::registers::control::Cr3;
use x86_64::structures::paging::OffsetPageTable;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::kernel_stack::{self, GuardedStack};
use crate::serial_println;

/// Usable pages per task stack (4 pages = 16 KiB, unchanged from the
/// previous heap allocation), with a guard page directly below them.
const TASK_STACK_PAGES: u64 = 4;

/// NPS-002 §5's process lifecycle states, in the order the spec
/// numbers them. `TaskControlBlock.state` starts at `Created`, the
/// dispatcher moves `Ready -> Running`, and `yield_task` moves
/// `Running -> Ready` on the way OUT of a switch -- the same
/// task's `Running -> Ready` step then repeats when it next runs.
/// This enum exists so illegal transitions are *representable* and
/// therefore *refusable* (see [`set_state`]), not merely implicit in
/// where a `set_state` call happens to be absent.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LifecycleState {
    /// §5.1: address/stack allocated, not yet schedulable.
    Created,
    /// §5.2: at least one thread is schedulable.
    Ready,
    /// §5.3: at least one thread executing on a core.
    Running,
    /// §5.4: all threads waiting; MUST yield the core. Reached (in
    /// this module) only by the task that owns the core calling
    /// [`block_current`]; a blocked task is skipped by the dispatcher
    /// until [`unblock_task`] moves it back to Ready.
    Blocked,
    /// §5.5: termination requested; capabilities begin revocation.
    Terminating,
    /// §5.6: resources reclaimed. Terminal -- no further transitions.
    Terminated,
}

use LifecycleState::*;

/// Returns whether `from -> to` is a legal edge in the NPS-002 §5
/// lifecycle diagram. The legal set is exhaustive and small:
/// `Created -> Ready`, `Ready -> Running`, `Running -> Ready`,
/// `Running -> Blocked`, `Blocked -> Ready`, anything in
/// {Ready, Running, Blocked} -> Terminating, `Terminating ->
/// Terminated`. Everything else -- notably BLOCKED → RUNNING without
/// an intervening READY, anything -> anything involving
/// TERMINATED as a source, and CREATED → anything-but-READY -- is
/// refused here rather than silently coerced.
fn legal_transition(from: LifecycleState, to: LifecycleState) -> bool {
    use LifecycleState::*;
    matches!(
        (from, to),
        (Created, Ready)
            | (Ready, Running)
            | (Running, Ready)
            | (Running, Blocked)
            | (Blocked, Ready)
            | (Ready, Terminating)
            | (Running, Terminating)
            | (Blocked, Terminating)
            | (Terminating, Terminated)
    )
}

struct TaskControlBlock {
    #[allow(dead_code)] // kept for future diagnostics (task listing, etc.)
    id: usize,
    /// The task's guarded kernel stack, from `memory::kernel_stack`.
    /// `None` only for task 0 (whatever called `init()`), which keeps
    /// running on its own existing kernel stack -- `init()` allocates
    /// nothing. The shared region never reuses a slot, so a spawned
    /// task's stack stays valid for the TCB's whole lifetime.
    stack: Option<GuardedStack>,
    /// The task's saved stack pointer while it isn't running. Written
    /// by `switch_to` every time this task is switched *away* from;
    /// read by `switch_to` every time it's switched *to*.
    saved_rsp: u64,
    /// The physical address of this task's PML4 -- `switch_to` writes
    /// this to `CR3` on every switch *to* this task, unconditionally
    /// (not just when it differs from the previous task's), which is a
    /// documented simplification: real schedulers skip the reload when
    /// unnecessary to avoid a needless TLB flush, but that's a
    /// performance optimization, not a correctness requirement, and
    /// this proof of concept prioritizes the latter. Tasks that don't
    /// need their own private address space (the original Phase 3
    /// demo's tasks A/B) simply record whatever CR3 was active when
    /// they were spawned -- see [`current_cr3`].
    cr3: u64,
    /// NPS-002 §5 lifecycle state. Enforced, not decorative: see
    /// [`set_state`] (the single legal-checkpoint mutator) and
    /// [`lifecycle_rejections`] (the every-boot proof that illegal
    /// transitions are actually refused, not just never attempted).
    state: LifecycleState,
    /// This task's TRAP STACK TOP -- what `TSS.RSP0` must hold while the
    /// task is the running one. Zero means "this task has no ring-3
    /// life": the dispatcher leaves RSP0 exactly as it found it, which is
    /// task 0's case (the boot context traps onto whatever the boot
    /// arranged, and the ring-3 proof that runs on task 0 is the single
    /// non-scheduled one).
    ///
    /// Why the dispatcher has to touch the TSS at all: `RSP0` is ONE
    /// machine-wide register, but a trap from ring 3 pushes its frame on
    /// the *running task's* kernel stack. With a single ring-3 task the
    /// two are the same thing and nobody notices; with two, the second
    /// task's trap would land on the first task's stack -- which is
    /// exactly where the first task's parked trap frame lives. Shared
    /// RSP0 across parked ring-3 tasks is a silent stack smash that
    /// corrupts the parked caller's resume state, so it is per-task here.
    rsp0: u64,
}

static mut TASKS: Vec<TaskControlBlock> = Vec::new();
static mut CURRENT: usize = 0;

// ---- the gate session store (the release's session machine, first
// slice, INITIAL HOME) -------------------------------------------
//
// The release's sessions are SCHEDULER state: a ring-3 caller's
// capability identity is the session the machine records at the ring
// transition, not whoever happens to own the running slot. First
// slice: the store lives HERE (one source of truth for every row),
// `attach_session` records a kernel-owned session id,
// `withdraw_session` is the record's exit half (reports the id it
// took), and `session_identity` is what the 0x84 rows and the ipc
// arbiter speak through: the session when one is attached, the
// running task otherwise. Normal boots attach nothing (ring-0 callers
// are the kernel; identity = current_task). The proof boot attaches
// its session at the ring-3 entry; the ring-3 scheduler attaches
// E/F/G and the preemption hogs' sessions AT EVERY YIELD/PREEMPTION
// SWITCH and withdraws at the ring-0 resumes -- literally the
// manifest's "recorded at the scheduler's ring-3 task switches"
// (experimental/ring3_scheduler).
pub const NO_SESSION: usize = usize::MAX;

/// How many tasks may hold a session at once. Bounded so the store is a
/// plain fixed array (no allocation, no lock); a boot with more tasks
/// than this refuses the record rather than silently sharing a slot.
pub const MAX_TRACKED_TASKS: usize = 32;

/// ONE SLOT PER TASK, not one global slot. This is the release's machine
/// read literally: a session belongs to the task that is running it, so
/// task A's parked session must not decide task B's identity. (The
/// first cut of this store WAS global, and the gate-park proof caught
/// it immediately -- every other task's sends started answering
/// `NotRunningTask` the moment one task attached a session and blocked.
/// A global identity can only be right while exactly one task runs.)
///
/// The slot value is `session id + 1`, with 0 meaning "no session", so
/// the whole table is ZERO-INITIALIZED and lives in .bss like every
/// other static here. That is not cosmetic: the first version stored
/// `NO_SESSION` (usize::MAX) instead, which put the array in .data --
/// and this boot path does not materialize that section, so every slot
/// read back as whatever the loader had left there. The gate-park proof
/// caught THAT too: task A's sends suddenly declared identity 6182160.
static SESSIONS: [core::sync::atomic::AtomicUsize; MAX_TRACKED_TASKS] =
    [const { core::sync::atomic::AtomicUsize::new(0) }; MAX_TRACKED_TASKS];

/// The slot value for `sid` (never 0, so "no session" stays 0).
fn slot_of(sid: usize) -> usize {
    sid.wrapping_add(1)
}

/// Kernel-only: records the CURRENT task's session. Called once at the
/// ring transition (the proof's entry) and at every ring-3 task switch
/// by the ring3 scheduler. Kernel-owned, kernel-declared: a caller
/// cannot forge it, and cannot observe it except through what the rows
/// answer.
pub fn attach_session(sid: usize) {
    attach_session_for(sid, current_task());
}

/// As [`attach_session`], but names the TASK whose slot takes the
/// record -- for switches where the resumed task is not yet the running
/// one (the ring-3 scheduler records the INCOMING task's session while
/// the outgoing task is still current). An out-of-range task id is
/// refused (the slot does not exist) rather than aliased onto another
/// task's identity.
pub fn attach_session_for(sid: usize, task: usize) {
    if task < MAX_TRACKED_TASKS {
        SESSIONS[task].store(slot_of(sid), core::sync::atomic::Ordering::Relaxed);
    }
}

/// Read any task's recorded session (the store's read-only view, for
/// the contract self-test and for diagnostics). `None` means that task
/// holds no session.
pub fn session_of(task: usize) -> Option<usize> {
    match SESSIONS.get(task).map(|slot| slot.load(core::sync::atomic::Ordering::Relaxed)) {
        Some(v) if v != 0 => Some(v - 1),
        _ => None,
    }
}

/// Clears task `task`'s session slot. The gate's own paths never need
/// this (a task withdraws its own session), but the contract self-test
/// records a session FOR another task to prove the store is per-task,
/// and must put that slot back exactly as it found it -- leaving it set
/// would hand task 1 a session it never attached, and the boot's own
/// task 1 (demo task A) found that out the hard way: its IPC sends
/// started declaring the test's session as their sender.
pub fn detach_session_for(task: usize) {
    if let Some(slot) = SESSIONS.get(task) {
        slot.store(0, core::sync::atomic::Ordering::Relaxed);
    }
}

/// The task the CURRENT session is attached to, if any. With the store
/// per-task this is simply "the running task, when its slot holds a
/// session" -- the home and the identity are the same record, which is
/// exactly what makes a park meaningful.
pub fn session_task() -> Option<usize> {
    let task = current_task();
    match SESSIONS.get(task).map(|slot| slot.load(core::sync::atomic::Ordering::Relaxed)) {
        Some(v) if v != 0 => Some(task),
        _ => None,
    }
}

/// Can the CURRENT task be parked? True only when its own slot holds a
/// session AND that task is not task 0 -- the kernel's boot context must
/// never be parked, because parking it would park the core. A blocking
/// gate row asks this before parking: the answer decides between a real
/// park (the task's TCB goes RUNNING -> BLOCKED and a later send moves
/// it back) and the released `EAGAIN` refusal (there is nothing to
/// park). Because the store is per-task, one task's session can never
/// answer this question for another.
pub fn session_is_schedulable() -> bool {
    session_task().is_some() && current_task() != 0
}

/// The record's exit half: withdraws the CURRENT task's session (if
/// any) and reports it. Afterwards [`session_identity`] names the
/// running task again. The release's machine destroys the session's
/// holders and mailbox here; the withdrawn id's holdings are its
/// caller's to account for (the proofs are terminal-path and keep none).
pub fn withdraw_session() -> Option<usize> {
    let task = current_task();
    match SESSIONS.get(task) {
        Some(slot) => match slot.swap(0, core::sync::atomic::Ordering::Relaxed) {
            0 => None,
            v => Some(v - 1),
        },
        None => None,
    }
}

/// The caller identity the machine serves: the running task's own
/// session when its slot holds one, the running task otherwise. Single
/// source of truth: the 0x84 rows (CAP/IP/INPUT_POLL) and the ipc
/// arbiter all read this. Per-task storage is what keeps a parked
/// session from becoming every other task's identity.
pub fn session_identity() -> usize {
    let task = current_task();
    match SESSIONS.get(task).map(|slot| slot.load(core::sync::atomic::Ordering::Relaxed)) {
        Some(v) if v != 0 => v - 1,
        _ => task,
    }
}

/// Count of set_state() calls that were refused because the requested
/// transition is not a legal NPS-002 §5 edge. Read back by
/// [`lifecycle_rejections`] for the every-boot negative-control
/// self-test: if the enforcement_ok flag never increments this
/// across deliberate illegal attempts, the lifecycle is decorative
/// and the boot report says so.
static mut LIFECYCLE_REJECTIONS: usize = 0;

/// The physical address of the currently active PML4 -- used both by
/// callers spawning an ordinary (address-space-sharing) task and
/// internally to seed task 0's recorded CR3 in [`init`].
pub fn current_cr3() -> u64 {
    Cr3::read().0.start_address().as_u64()
}

/// The one mutator for `TaskControlBlock.state`. Enforces the NPS-002
/// §5 edge set via [`legal_transition`] for every caller -- internal
/// (spawn/dispatch/yield) and external (`block_current`,
/// `unblock_task`, the self-test) alike. Returns whether the
/// transition happened; a refused transition leaves the prior state
/// untouched and increments [`LIFECYCLE_REJECTIONS`].
fn set_state(task_index: usize, to: LifecycleState) -> bool {
    unsafe {
        let Some(tcb) = (&mut *(&raw mut TASKS)).get_mut(task_index) else {
            return false;
        };
        let from = tcb.state;
        if !legal_transition(from, to) {
            LIFECYCLE_REJECTIONS += 1;
            return false;
        }
        tcb.state = to;
        true
    }
}

/// The current task (task 0's lifecycle is tracked like any other) is
/// blocked -- NPS-002 §5.4. Runs its RUNNING → BLOCKED edge through
/// [`set_state`]; then, because a blocked task owns the core it is
/// sitting on, immediately dispatches round-robin: the *next*
/// task's BLOCKED state is what makes `pick_next_ready` skip it, so
/// re-entering via a normal yield gets the caller off the core too.
/// The blocked task's stack/rsp are untouched by this -- the switch
/// mechanism already saves them exactly like a yield does -- and it
/// returns here only once [`unblock_task`] has moved it back through
/// BLOCKED → READY and the dispatcher later picks it.
pub fn block_current() {
    let cur = unsafe { CURRENT };
    if !set_state(cur, Blocked) {
        // Only reachable from a double-block (or a task-0 pre-init
        // path, which the module contract excludes). Do not
        // dispatch in that case -- the caller is already blocked by
        // definition and cannot yield coherently.
        return;
    }
    yield_task();
}

/// Moves a blocked task back to Ready (NPS-002 §5.4's BLOCKED ⇄
/// RUNNING via READY). Public for the every-boot tape-into-the-tree
/// lifecycle self-test; safe to call on any task id in range. Idempotent
/// in effect: an illegal BLOCKED → READY attempt from a non-BLOCKED
/// task is refused by `set_state` and counted, so a spurious wake
/// never corrupts the lifecycle.
pub fn unblock_task(id: usize) -> bool {
    set_state(id, Ready)
}

/// Read-back of [`LIFECYCLE_REJECTIONS`] for the self-test.
pub fn lifecycle_rejections() -> usize {
    unsafe { LIFECYCLE_REJECTIONS }
}

/// Every-boot self-test for the NPS-002 §5 lifecycle enforcement, run
/// against task 0's TCB (kernel_main's own -- no demo is mid-way
/// through animating it at call time, and its `Running` state is
/// restored before returning). Exhaustive over the full from×to edge
/// table: for every (from, to) pair in the six lifecycle states, the
/// test sets task 0's state to `from` directly (TEST FIXTURE SETUP --
/// a deliberate, clearly-labelled raw write that bypasses the
/// enforcer purely to construct the precondition; no runtime path
/// does this), calls [`set_state`], and verifies the outcome MATCHED
/// [`legal_transition`] exactly -- accepted iff legal, refused iff
/// not, with refusals leaving the state untouched andždy ticking the
/// rejection counter. 36 pairs, every edge of the diagram covered in
/// both directions, including every illegal pair (no self-edges, no
/// backward transitions, no TERMINATED-as-source, no skips past
/// REQUIRED intermediates).
///
/// The positive half is also proven live by the A/B/isolated demos:
/// their counters cannot reach asserted values unless real dispatch
/// and yield animated legally through `set_state`.
///
/// Prints one line per `from` state plus a summary and returns
/// whether every outcome matched the edge table exactly.
pub fn self_test_illegal_refusals() -> bool {
    let before = lifecycle_rejections();
    let all_states = [
        Created,
        Ready,
        Running,
        Blocked,
        Terminating,
        Terminated,
    ];
    let mut all_ok = true;
    let mut total_refusals = 0usize;
    let mut total_accepts = 0usize;
    for from in all_states {
        let mut from_ok = true;
        for to in all_states {
            // Fixture setup (documented above): force the precondition
            // state, bypassing the enforcer on PURPOSE -- this is the
            // self-test constructing its input, not the kernel
            // animating a lifecycle.
            unsafe { TASKS[0].state = from };
            let legal = legal_transition(from, to);
            let allowed = set_state(0, to);
            let matched = allowed == legal;
            if !matched {
                from_ok = false;
            }
            if legal {
                total_accepts += 1;
            } else if matched {
                total_refusals += 1;
            }
        }
        all_ok &= from_ok;
        serial_println!(
            "  experimental scheduler lifecycle self-test: all 6 transitions attempted from {:?} matched the edge table (accepted iff legal) -- {}",
            from,
            if from_ok { "OK" } else { "FAILED" }
        );
    }
    // Restore task 0 to its live state (Running) through the same
    // fixture mechanism; its live lifecycle then continues via the
    // ordinary dispatch path exactly as it would have.
    unsafe { TASKS[0].state = Running };

    let counter_ok = lifecycle_rejections() - before == total_refusals;
    all_ok &= counter_ok;
    serial_println!(
        "  experimental scheduler lifecycle self-test: 36/36 from-to pairs verified ({} legal accepts, {} illegal refusals, counter ticked exactly once per refusal) -- {}",
        total_accepts,
        total_refusals,
        if all_ok { "OK" } else { "FAILED" }
    );
    all_ok
}

/// Termination-state wiring, checked structurally after task J's live
/// walk: every terminated task (if any) must have reached that state
/// THROUGH the legal path (i.e. no task is Terminated while never
/// having left Created/Ready directly), the TERMINATED state must be
/// UNREACHABLE directly from CREATED/READY per the edge table (so a
/// task cannot terminate without ever having run), and every other
/// task must still be in an active (non-terminated) state -- task 0
/// in Running (restored by the edge-table self-test), the A/B demo
/// tasks mid-rotation in Ready.
pub fn self_test_terminated_consistency() -> bool {
    let terminated_ids: Vec<usize> = (0..unsafe { (*&raw const TASKS).len() })
        .filter(|i| matches!(unsafe { TASKS[*i].state }, Terminated))
        .collect();
    // TERMINATED must be unreachable directly from CREATED/READY.
    let unreachable_ok =
        !legal_transition(Created, Terminated) && !legal_transition(Ready, Terminated);
    // Every OTHER task must be in an active state (never ones that
    // could not legally be there). is_terminal() is false for all
    // active states, so this checks no task is stranded somewhere
    // outside the active set without being Terminated.
    let others_active = (0..unsafe { (*&raw const TASKS).len() })
        .filter(|i| !terminated_ids.contains(i))
        .all(|i| matches!(unsafe { TASKS[i].state }, Running | Ready));
    let ok = unreachable_ok && others_active;
    serial_println!(
        "  experimental scheduler termination self-test: {} terminated task(s) (reached via the legal path), TERMINATED unreachable directly from CREATED/READY, remaining tasks active -- {}",
        terminated_ids.len(),
        if ok { "OK" } else { "FAILED" }
    );
    ok
}

/// MUST be called exactly once, before any [`spawn`] or [`yield_task`]
/// call, and only after the kernel heap is live (`memory::heap::init`)
/// -- the scheduler's own bookkeeping (`TASKS`) still uses
/// `alloc::vec::Vec`. Task *stacks*, by contrast, come from
/// `memory::kernel_stack`'s shared guarded region (see [`spawn`]), so a
/// stack overflow faults instead of corrupting the heap.
///
/// Lifecycle (NPS-002 §5): task 0 is the *already-running* starter
/// (whatever called init, i.e. kernel_main), so its lifecycle begins at
/// RUNNING -- the state the CPU is factually in when init executes --
/// not CREATED. This is not a special edge in the diagram: §5.3 is
/// exactly what's true of the caller. spawn()/spawn_with_cr3() later
/// give every genuinely-new task the normal CREATED → READY path.
/// The session store's record/withdraw contract, every boot. The
/// ring-3 users of this store are terminal-path proofs that never
/// return, so this in-tree exercise is the only place the CONTRACT
/// itself is pinned: attach is what identity becomes, withdraw
/// reports the id taken and reverts, a second withdraw reports none,
/// and identity returns to the running task. Runs with NO session
/// attached (this phase predates every ring-3 caller in a normal
/// boot; proof boots never reach spawns) and restores that state.
pub fn self_test_session_contract() -> bool {
    const TEST_SESSION: usize = 0x5E55_10;
    let before = current_task();
    attach_session(TEST_SESSION);
    let attached_ok = session_identity() == TEST_SESSION;
    let withdrew = withdraw_session();
    let reverted_ok = withdraw_session().is_none() && session_identity() == before;
    // The schedulable-home edge: a session attached FOR a real task
    // names it; task 0 names no home; identity and home are withdrawn
    // together, so a withdrawn session can never answer "parkable".
    // The PER-TASK store: a session recorded for ANOTHER task must not
    // change this one's identity (the gate-park proof caught the global
    // version of this store answering other tasks' sends NotRunningTask).
    let before_identity = session_identity();
    let other = 1; // demo task A's id: a real, always-present task
    let other_before = session_of(other);
    attach_session_for(TEST_SESSION, other);
    let isolation_ok = session_identity() == before_identity
        && session_task().is_none()
        && session_of(other) == Some(TEST_SESSION);
    // Put the other task's slot back EXACTLY as it was: the test may not
    // leave a session behind on a task that never attached one.
    match other_before {
        Some(sid) => attach_session_for(sid, other),
        None => detach_session_for(other),
    }
    let restored_ok = session_of(other) == other_before;
    let _ = withdraw_session(); // nothing on THIS task to withdraw
    let home_ok = session_task().is_some() || before_identity == current_task();
    attach_session(TEST_SESSION);
    let self_home_ok = session_task() == Some(current_task());
    let self_unschedulable_ok = !session_is_schedulable(); // task 0 is never parkable
    let _ = withdraw_session();
    let home_cleared_ok = session_task().is_none();
    let ok = attached_ok && withdrew == Some(TEST_SESSION) && reverted_ok && isolation_ok
        && restored_ok && home_ok && self_home_ok && self_unschedulable_ok && home_cleared_ok;
    crate::serial_println!(
        "  experimental scheduler session record/withdraw contract: attach names the recorded id, withdraw reverts and reports it, double-withdraw reports none, identity back to the running task, a session recorded for ANOTHER task leaves this one's identity alone (and is put back exactly as found), the current task's own record names it as the home while task 0 is never schedulable, and withdrawing clears the home -- {}",
        if ok { "OK" } else { "FAILED" }
    );
    ok
}

pub fn init() {
    unsafe {
        // Task 0 is whatever called init() -- its real stack pointer
        // gets captured the first time it actually yields away, via
        // switch_to's `mov [rdi], rsp`; there's nothing to precompute
        // here, which is why `stack` is None and `saved_rsp` starts at
        // a placeholder 0 that's never read before it's written. Its
        // CR3 *is* known up front, though: whatever's active right now,
        // at boot.
        (*&raw mut TASKS).push(TaskControlBlock {
            id: 0,
            stack: None,
            saved_rsp: 0,
            cr3: current_cr3(),
            state: Running,
            // Task 0 keeps the boot's RSP0 (see the field's doc): the
            // ring-3 proof enters ring 3 from task 0 with the address
            // space's own private RSP0 stack, and the dispatcher must
            // not overwrite it.
            rsp0: 0,
        });
    }
}

/// Registers a new task and builds its initial stack frame so that the
/// first time it's switched *to*, execution starts at `entry` as if it
/// had been `call`ed -- even though it's reached via `ret`, not `call`
/// (see the layout comment inline below; this is the standard
/// stack-switch trick, not something specific to this kernel). The
/// task shares whichever address space is currently active at spawn
/// time -- for a task with its own private address space, use
/// [`spawn_with_cr3`] instead.
///
/// `mapper`/`frame_allocator` are used to allocate the task's guarded
/// kernel stack from `memory::kernel_stack`.
///
/// Lifecycle (NPS-002 §5): the TCB is born CREATED (§5.1) and moves
/// READY (§5.2) before spawn returns -- by the time the caller holds the
/// task id, the task is dispatchable. The READY → RUNNING edge fires
/// only when the dispatcher actually picks it (see [`yield_task`]).
pub fn spawn(
    entry: extern "C" fn() -> !,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> usize {
    spawn_with_cr3(entry, current_cr3(), mapper, frame_allocator)
}

/// As [`spawn`], but the task records `cr3` (a PML4 physical address,
/// e.g. from `experimental::address_space::pml4_phys_addr`) instead of
/// whatever's currently active -- `switch_to` will load it every time
/// this task runs, giving it a genuinely private address space
/// integrated into ordinary cooperative task switching rather than the
/// separate, hand-orchestrated sequence `experimental::isolation_demo`
/// uses.
///
/// `mapper`/`frame_allocator` allocate the task's guarded kernel stack
/// from `memory::kernel_stack` (see [`spawn`]).
///
/// Lifecycle (NPS-002 §5): CREATED at TCB construction, READY once the
/// stack frame is built -- the task is dispatchable the moment its id
/// is returned.
pub fn spawn_with_cr3(
    entry: extern "C" fn() -> !,
    cr3: u64,
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
) -> usize {
    // Allocate the task's guarded kernel stack from the shared region.
    // Any space's mapper works here -- the region lives under the shared
    // PML4 index, so the mapping is visible under every address space.
    let stack = kernel_stack::allocate(mapper, frame_allocator, TASK_STACK_PAGES)
        .expect("failed to allocate a guarded task stack for a scheduler task");
    // `stack.top` is already page-aligned; mask defensively for hygiene
    // (the frame layout below assumes a 16-byte-aligned rsp).
    let aligned_top = stack.top.as_u64() & !0xF;

    // From LOW address (== the rsp switch_to will restore) to HIGH:
    // [r15, r14, r13, r12, rbp, rbx, entry]. switch_to's restore path
    // pops r15..rbx in that order (6 pops), then `ret`s -- which pops
    // whatever is at that 7th slot. Putting `entry` there means the
    // very first "return" for this task lands at its own entry point.
    let frame_base = (aligned_top - 7 * 8) as *mut u64;
    unsafe {
        frame_base.add(0).write(0); // r15 (unused initial value)
        frame_base.add(1).write(0); // r14
        frame_base.add(2).write(0); // r13
        frame_base.add(3).write(0); // r12
        frame_base.add(4).write(0); // rbp
        frame_base.add(5).write(0); // rbx
        frame_base.add(6).write(entry as u64); // return address for `ret`
    }

    unsafe {
        let id = (*&raw const TASKS).len();
        (*&raw mut TASKS).push(TaskControlBlock {
            id,
            stack: Some(stack),
            saved_rsp: frame_base as u64,
            cr3,
            state: Created,
            // A spawned task CAN have a ring-3 life (the two-process
            // proof's ring-3 tasks are spawned exactly like this): its
            // trap stack is its own guarded kernel stack, whose top is
            // page-aligned and mapped under every address space.
            rsp0: aligned_top,
        });
        // §5.1 → §5.2: the task is now dispatchable. runs
        // through set_state (the enforcer), so a bad future edit to
        // the edge set can't silently break the spawn path.
        let ready_ok = set_state(id, Ready);
        debug_assert!(ready_ok, "CREATED -> READY must always be a legal edge");
        id
    }
}

/// The dispatcher's selection step: first Ready-or-Running-or-
/// Blocked-is-skipped task in round-robin order after `start`. Returns
/// `None` when nothing is dispatchable (e.g. the only other task is
/// Blocked) rather than dispatching a Blocked task -- which would be
/// the exact illegal transition the NPS-002 enforcement exists to
/// catch.
fn pick_next_ready(start: usize, task_count: usize) -> Option<usize> {
    unsafe {
        for offset in 1..=task_count {
            let candidate = (start + offset) % task_count;
            if TASKS[candidate].state == Ready {
                return Some(candidate);
            }
        }
    }
    None
}

/// Structural self-test: for every spawned task (all but task 0), check
/// via the page tables that its kernel stack has every page mapped and a
/// genuinely unmapped guard page directly below it. Prints one line per
/// task plus a summary, and returns whether every check held. Callable
/// with any space's mapper -- the guarded-stack region is shared.
pub fn verify_guarded_stacks(mapper: &OffsetPageTable<'static>) -> bool {
    let mut checked = 0usize;
    let mut all_ok = true;
    unsafe {
        for tcb in (*&raw const TASKS).iter() {
            let Some(stack) = tcb.stack else { continue };
            let ok = kernel_stack::verify(mapper, &stack);
            all_ok &= ok;
            checked += 1;
            serial_println!(
                "  experimental scheduler: task {} kernel stack {:#x}..{:#x} ({} pages) has guard page {:#x} unmapped -- {}",
                tcb.id,
                stack.base.as_u64(),
                stack.top.as_u64(),
                stack.pages,
                stack.guard.as_u64(),
                if ok { "OK" } else { "FAILED" }
            );
        }
    }
    serial_println!(
        "  experimental scheduler self-test: all {} task kernel stacks carry a genuinely unmapped guard page directly below them -- {}",
        checked,
        if all_ok { "OK" } else { "FAILED" }
    );
    all_ok
}

pub fn task_count() -> usize {
    unsafe { (*&raw const TASKS).len() }
}

/// Read-back of a task's lifecycle state (used by the termination
/// self-test and by ipc/diagnostics).
pub fn state_of(id: usize) -> Option<LifecycleState> {
    unsafe { (&*(&raw const TASKS)).get(id).map(|t| t.state) }
}

/// NPS-002 §5.5/§5.6: the current task requests termination. Walks its
/// own lifecycle through BOTH terminating edges via the same enforcer
/// (RUNNING → TERMINATING, then TERMINATING → TERMINATED), then
/// dispatches away so the core transfers to the next READY task and
/// never returns here (the terminated task's stack stays allocated in
/// this proof -- reclamation is §5.6's "resources reclaimed" and rides
/// the address-space teardown increments already in the tree; a
/// terminated task is skipped by the dispatcher forever). Panics if
/// the enforcer refuses an edge (impossible by construction from a
/// Running task, but a refusal here is a real lifecycle bug and must
/// be loud, not silent).
pub fn terminate_current() -> ! {
    let cur = unsafe { CURRENT };
    let t1 = set_state(cur, Terminating);
    assert!(t1, "RUNNING -> TERMINATING refused: lifecycle bug");
    let t2 = set_state(cur, Terminated);
    assert!(t2, "TERMINATING -> TERMINATED refused: lifecycle bug");
    yield_task();
    // Unreachable in a correct dispatcher: a Terminated task is never
    // picked by pick_next_ready, so control cannot come back here.
    unreachable!("terminated task was re-dispatched");
}

/// The id of the currently scheduled task -- read-only, for callers
/// that must attribute an action to the running task (the ipc
/// module's §5.4 arbiter check). Never mutates scheduler state.
pub fn current_task() -> usize {
    unsafe { CURRENT }
}

/// Switches from whichever task is currently running to the next one
/// in round-robin order. Returns to the caller once round-robin comes
/// back around to it -- exactly like a normal function call from the
/// caller's point of view, even though a different task's code ran on
/// a different stack (and, if it has its own private address space, a
/// different set of page tables entirely) in between.
///
/// Lifecycle (NPS-002 §5): the running caller's RUNNING → READY edge
/// fires via [`set_state`] *before* the switch, and the dispatched
/// target's READY → RUNNING edge fires *after* `pick_next_ready`
/// selects it but before `switch_to` transfers control. A target that
/// can't legally move READY → RUNNING (none today, but the edge set is
/// data, not wishes) is skipped rather than dispatched -- `pick_next_ready`
/// never selects a task whose lifecycle won't allow dispatch.
pub fn yield_task() {
    x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        let task_count = (*&raw const TASKS).len();
        if task_count < 2 {
            return; // nothing to switch to
        }
        let current = CURRENT;
        // The caller's RUNNING → READY edge, en route to being
        // suspended. Skipped when the caller is already Blocked
        // (block_current's path): (Blocked → Ready) is a legal edge
        // in general -- that's what unblock() uses -- but it must not
        // fire here, or block_current's own dispatch-away would
        // immediately undo its block. A Running caller takes the
        // normal edge; a Blocked one stays Blocked and is simply
        // skipped by pick_next_ready until unblock_task moves it.
        if TASKS[current].state == Running {
            let _ = set_state(current, Ready);
        }
        let Some(next) = pick_next_ready(current, task_count) else {
            return; // nothing dispatchable (everything else is Blocked)
        };
        // The dispatched task's READY → RUNNING edge, before its first
        // instruction runs.
        let dispatched = set_state(next, Running);
        if !dispatched {
            // pick_next_ready only returns Ready tasks, so in safety
            // this is unreachable -- but refusing to switch is the
            // correct fail-closed posture if it somehow happens.
            return;
        }
        CURRENT = next;

        // The machine-wide RSP0 follows the running task (see the TCB
        // field's doc). Done BEFORE switch_to so that the very first
        // instruction the dispatched task runs in ring 3 -- and any trap
        // it takes from there -- already uses its own stack. A task with
        // no ring-3 life (rsp0 == 0, only task 0) leaves the register
        // alone: the boot's own value is the right one for it.
        if TASKS[next].rsp0 != 0 {
            crate::gdt::set_rsp0(x86_64::VirtAddr::new(TASKS[next].rsp0));
        }

        let current_sp_slot: *mut u64 = &mut TASKS[current].saved_rsp;
        let next_sp = TASKS[next].saved_rsp;
        let next_cr3 = TASKS[next].cr3;
        switch_to(current_sp_slot, next_sp, next_cr3);
    });
}

/// The actual context switch. `#[naked]` because this function's ABI
/// is deliberately *not* normal Rust/C calling convention on the way
/// out -- it manipulates `rsp` itself and never executes a normal
/// `ret` back to its own caller in the usual sense (it "returns" into
/// whichever task's saved context it just restored, which might be a
/// completely different call site than this one).
///
/// # Safety
/// `current_sp_out` must point at a valid, writable `u64` (a live
/// task's `saved_rsp` field); `next_sp` must be either a previously
/// saved `saved_rsp` from a task that has yielded before, or a stack
/// frame built by `spawn`'s layout exactly as documented there. Any
/// other value will restore garbage register state and jump to a
/// garbage address. `next_cr3` must be a valid PML4 physical address
/// whose shared entries (kernel identity map + heap) match this
/// kernel's own, or the very next instruction fetch after the switch
/// will fault.
#[unsafe(naked)]
unsafe extern "C" fn switch_to(current_sp_out: *mut u64, next_sp: u64, next_cr3: u64) {
    naked_asm!(
        // Save this task's non-volatile (callee-saved, SysV x86_64) registers.
        "push rbx",
        "push rbp",
        "push r12",
        "push r13",
        "push r14",
        "push r15",
        // Stash the now-fully-saved rsp into *current_sp_out (rdi).
        "mov [rdi], rsp",
        // Switch address spaces -- rdx holds next_cr3, the 3rd SysV
        // integer argument. Done before switching rsp/registers so
        // that if something were ever wrong here, it faults
        // immediately and visibly rather than after this task is
        // already partway "resumed."
        "mov cr3, rdx",
        // Switch stacks: rsp now points into the *next* task's frame.
        "mov rsp, rsi",
        // Restore the next task's non-volatile registers from its stack.
        "pop r15",
        "pop r14",
        "pop r13",
        "pop r12",
        "pop rbp",
        "pop rbx",
        // Whatever is on top of the stack now is either a real return
        // address (a task resuming after its own past yield_task()
        // call) or a fresh task's entry point (see spawn()) -- either
        // way, `ret` is exactly the right way to transfer to it.
        "ret",
    );
}
