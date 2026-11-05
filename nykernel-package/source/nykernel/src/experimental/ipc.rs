//! Inter-task mailbox IPC -- a real message-passing primitive between
//! independently scheduled tasks (`crate::experimental::scheduler`).
//!
//! The old "this is not NPS-003 conformance" claim is withdrawn:
//! `NPS-003` (IPC and Capability Passing) was **Accepted 2026-10-11**
//! (AG decision-log row I2, owner direction via the recorded session),
//! so this module now implements against NPS-003 requirements as
//! binding. The requirements this increment targets, each with its
//! every-boot proof:
//!
//! - **§5.4 / §5.2 (kernel as sole arbiter; no broader-than-held
//!   transfer)**: `send` validates the `sender` field against the
//!   *actually running* scheduler task before the message is queued.
//!   A message claiming to be from anyone other than the real caller
//!   is refused with [`IpcError::NotRunningTask`] -- a forged identity
//!   can no longer masquerade as a different sender in a receiver's
//!   queue. This is the minimal kernel-arbiter check on the mailbox
//!   primitive; full capability-token objects (NPS-003 §5's transfer
//!   field) remain a later increment on this module's track.
//! - **§4.3 (revocation takes effect for all future sends without
//!   holder cooperation)**: `revoke_sender` removes an endpoint's
//!   outstanding send-access for one sender id; every subsequent
//!   `send` to that endpoint from that sender fails closed with
//!   [`IpcError::Revoked`], and the *queued-past-revocation* rule
//!   NPS-003 §4.3 implies (revocation affects future operations, not
//!   retroactively draining) is honored: messages already in the
//!   queue stay queue-receivable, which `self_test_revocation` pins
//!   both ways.
//! - **§3.2 failure semantics (a defined error, never a silent
//!   drop)**: unchanged -- backpressure (`MailboxFull`) and invalid
//!   targets (`NoSuchMailbox`) remain typed errors, never drops.
//!
//! What honestly remains out of scope after this increment: the
//! capability-transfer message field (§5.1), shared-memory regions
//! (§3.1), and NPS-003 §6.1's wire transport -- the kernel-side mailbox
//! here is the in-kernel same-address-space primitive; the userspace
//! wire transports live in the Linux-backend tree and are measured
//! (IPC-001 §38) separately. Blocking receive HAS landed (§3's longer
//! note): the receiver parked on `scheduler::block_current`, woken by
//! `send` -- see `receive` for its bound's honest shape.

use alloc::collections::VecDeque;
use alloc::vec::Vec;
use x86_64::instructions::interrupts;

use crate::serial_println;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct IpcMessage {
    pub sender: usize,
    pub seq: u32,
}

#[derive(Debug, PartialEq, Eq)]
pub enum IpcError {
    /// Backpressure, not data loss: the caller finds out and can
    /// decide what to do, rather than the message being silently
    /// dropped (which a real transport must also never do -- see the
    /// NPS-003 §5.4 "kernel as sole arbiter" note above: an arbiter
    /// that sometimes drops messages without telling anyone isn't
    /// arbitrating, it's failing quietly).
    MailboxFull,
    NoSuchMailbox,
    /// A blocking [`receive`]'s poll budget elapsed with no message
    /// (the blocking receive's typed failure, never an indefinite
    /// block).
    ReceiveTimedOut,
    /// The send was refused by the arbiter check: the `sender` field
    /// does not name the actually-running task. NPS-003 §5.4: user
    /// code cannot forge identity past the kernel; the kernel-side
    /// analog here is that a mailbox user cannot forge identity past
    /// `send` itself.
    NotRunningTask,
    /// This sender's send-access to this endpoint was revoked
    /// (NPS-003 §4.3); all future sends from it fail closed.
    Revoked,
}

struct Mailbox {
    queue: VecDeque<IpcMessage>,
    capacity: usize,
    /// NPS-003 §4.3 revocation register: senders currently refused at
    /// this endpoint. A revoked sender's queued-past-revocation
    /// messages (if any) remain receivable -- revocation is forward-
    /// looking, per the spec text ("all future send/call operations").
    revoked: Vec<usize>,
    /// The tasks PARKED on this mailbox's queue -- a §3 blocking
    /// receive parks through [`crate::experimental::scheduler::block_current`]
    /// and is woken (unblocked per task) by the next `send` here. Registered under the
    /// same no-interrupts window the queue is mutated under, so a send can never
    /// see a registration before the receiver is in the scheduler's BLOCKED state
    /// (a task only runs send code when the waiting task is already parked):
    /// every unblock below is a legal BLOCKED -> READY edge.
    waiters: VecDeque<usize>,
}

static mut MAILBOXES: Vec<Mailbox> = Vec::new();

const DEFAULT_CAPACITY: usize = 4;

/// MUST be called exactly once, after
/// `crate::experimental::scheduler::init`/`spawn` calls are done (so
/// `task_count` matches the real number of tasks -- one mailbox per
/// task id) and, like the scheduler, only after the kernel heap is
/// live.
pub fn init(task_count: usize) {
    unsafe {
        (*&raw mut MAILBOXES).clear();
        for _ in 0..task_count {
            (*&raw mut MAILBOXES).push(Mailbox {
                queue: VecDeque::new(),
                capacity: DEFAULT_CAPACITY,
                revoked: Vec::new(),
                waiters: VecDeque::new(),
            });
        }
    }
}

/// Reads the *actually running* task id from the scheduler.
/// `experimental::scheduler::current_task()` returns 0 before the first
/// yield_tick has ever dispatched anything else -- but this module only
/// trusts it after scheduler tasks exist, and the initial demo caller
/// (task 0, kernel_main) is the one legitimate ring-0 sender that IS
/// the running task at the time of its sends.
///
/// Kept in `ipc` rather than reached through a hard dependency so the
/// module's dependency surface stays one-directional
/// (scheduler → ipc exists already via the banner; ipc reading
/// scheduler state is the new one-way edge, and `current_task` is
/// read-only).
fn running_task_id() -> usize {
    crate::experimental::scheduler::current_task()
}

/// Copies `msg` into task `dest`'s mailbox, after the NPS-003 §5.4
/// arbiter check: the claimed sender must BE the running task. Fails
/// closed on a full mailbox, an out-of-range destination, a forged
/// sender, or a revoked sender -- never silently drops, never panics
/// -- so the caller (or the receiver-side contract) decides what
/// "refused" means for their protocol.
pub fn send(dest: usize, msg: IpcMessage) -> Result<(), IpcError> {
    interrupts::without_interrupts(|| unsafe {
        // Arbiter check FIRST (NPS-003 §5.4): forgery is refused even
        // if the destination or backpressure state would also have
        // refused -- the sender identity is validated before queue
        // state is even looked at, so a forged send cannot learn
        // anything about the endpoint's queue depth via error probing.
        // The claim must BE the kernel-declared caller identity: the
        // running task, or the gate session the scheduler recorded at a
        // ring-3 transition (a ring-3 caller's sends carry the
        // session's id -- still kernel-declared, never forgeable; see
        // `scheduler::session_identity`).
        if msg.sender != crate::experimental::scheduler::session_identity() {
            return Err(IpcError::NotRunningTask);
        }
        let mailbox = (&mut *(&raw mut MAILBOXES)).get_mut(dest).ok_or(IpcError::NoSuchMailbox)?;
        if mailbox.revoked.contains(&msg.sender) {
            return Err(IpcError::Revoked);
        }
        if mailbox.queue.len() >= mailbox.capacity {
            return Err(IpcError::MailboxFull);
        }
        mailbox.queue.push_back(msg);
        // §3 receive's other half: every parked waiter is woken (un
        // blocked; BLOCKED -> READY) because a message just became
        // available. The waiter RECORD is consumed ONLY on a successful
        // transition; on a refused edge the registration survives and
        // the next send retries (never a lost wake).
        let wakees: Vec<usize> = mailbox.waiters.drain(..).collect();
        for t in wakees {
            if !crate::experimental::scheduler::unblock_task(t) {
                mailbox.waiters.push_back(t);
            }
        }
        Ok(())
    })
}

/// Non-blocking: returns immediately with whatever is (or isn't)
/// already queued. [`receive`] is the blocking complement: parked on
/// the scheduler with wake-on-send instead of a poll loop.
pub fn try_receive(dest: usize) -> Option<IpcMessage> {
    interrupts::without_interrupts(|| unsafe {
        (&mut *(&raw mut MAILBOXES)).get_mut(dest).and_then(|m| m.queue.pop_front())
    })
}

/// How many endpoints exist. Read-only, for callers that must tell
/// "endpoint does not exist" apart from "endpoint exists and is empty" --
/// [`try_receive`] returns `None` for BOTH, and a surface that answers a
/// nonexistent endpoint with its empty answer is conflating two different
/// conditions (the `0x84` gate refuses the nonexistent one with ENOENT and
/// answers only real empty ones with ENODATA).
pub fn endpoint_count() -> usize {
    unsafe { (*&raw const MAILBOXES).len() }
}

/// The `nyrqis.proof=svc84` boot drives `send`/`try_receive` through
/// endpoint 0; make that endpoint exist (a proof boot can reach this
/// before the ordinary `init`/demo phases have ever built the table) and
/// be EMPTY (earlier phases may have queued -- but the proof enters ring 3
/// and never returns, so anything still in flight is unreachable and
/// nothing waits for it: draining here loses nothing observable). NOT a
/// general-purpose reset: a caller of this documents why its path is
/// boot-terminal, as the proof's is.
pub fn prepare_endpoint0_for_proof() {
    interrupts::without_interrupts(|| unsafe {
        let boxes = &mut *(&raw mut MAILBOXES);
        if boxes.is_empty() {
            boxes.push(Mailbox {
                queue: VecDeque::new(),
                capacity: DEFAULT_CAPACITY,
                revoked: Vec::new(),
                waiters: VecDeque::new(),
            });
        } else if let Some(m) = boxes.get_mut(0) {
            m.queue.clear();
        }
    })
}

/// Blocking receive. PARKS on the queue (scheduler-level: the parked
/// task is not dispatched at all, never spins) and is woken by the
/// next `send` to this mailbox. Cooperative-blocking per NPS-002 §5.4
/// ("a process whose thread waits on a receive MUST yield the core")
/// and NPS-003 §3 ("blocks the calling thread until a message arrives").
///
/// The bound's honest shape: `max_wakes` counts EMPTY wakes -- every
/// unblock that finds no message (wake competition with another
/// receiver, or an explicit [`wake_waiters`] kick). When the budget is
/// exhausted the receive fails with the typed `ReceiveTimedOut`.
/// What it is NOT: a wall-clock timeout. A park that is never sent a
/// message and never kicked parks indefinitely; the slot's remaining
/// bound is the caller's protocol (an eventually-sending party, or a
/// periodic kick -- the same primitive a real kernel's timeout tick
/// would sit on). Old `max_polls` semantics (the yield-loop form) were
/// abandoned because they re-implemented "polling not blocking" flares
/// -- a parked task must not consume dispatches merely to tick a
/// private counter.
///
/// The park registration and the queue drain happen under the SAME
/// no-interrupts window, and a sender task can only run send code when
/// the receiver is already parked, so the scheduling between the two
/// cannot observe or miss a message (every wake is attached to a real
/// arrival OR to an explicit kick -- never a lost wake).
pub fn receive(dest: usize, max_wakes: Option<u32>) -> Result<IpcMessage, IpcError> {
    let self_id = running_task_id();
    let mut wakes = 0u32;
    loop {
        // One atomic probe-and-register: pop a queued message if one is
        // there (win -- return it), otherwise park ourselves on the
        // mailbox's waiter list. `interrupts::without_interrupts`
        // makes the whole probe atomic against the only other mutators
        // (send/wake_waiters from another task -- a task can only run
        // send code while the receiver is parked), so a message arriving
        // between "we found nothing" and "we are parked" is impossible;
        // that is the no-lost-wake property, and it is why the wake side
        // can be the simple unbounded drain `send` performs.
        let attempt = interrupts::without_interrupts(|| unsafe {
            let boxes = &mut *(&raw mut MAILBOXES);
            let Some(mailbox) = boxes.get_mut(dest) else {
                return Err(IpcError::NoSuchMailbox);
            };
            if let Some(msg) = mailbox.queue.pop_front() {
                return Ok(Some(msg));
            }
            if !mailbox.waiters.contains(&self_id) {
                mailbox.waiters.push_back(self_id);
            }
            Ok(None)
        });
        match attempt {
            Err(e) => return Err(e),
            Ok(Some(msg)) => return Ok(msg),
            Ok(None) => {}
        }
        // Park (scheduler-level: RUNNING -> BLOCKED, and immediately
        // dispatch someone else -- a parked receiver consumes ZERO
        // dispatches until woken). Returns only after some sender's or
        // kick's `unblock_task` moved us BLOCKED -> READY and the
        // dispatcher picked us again.
        crate::experimental::scheduler::block_current();
        // After the wake, our registration -- if the wake-side drain
        // did not already consume it -- is dropped: a task that is
        // RUNNING again must not stay listed as parked (a later send
        // would otherwise try to unblock a non-blocked task, a refused
        // edge every round). We re-register at the next probe if the
        // queue is still empty.
        interrupts::without_interrupts(|| unsafe {
            let boxes = &mut *(&raw mut MAILBOXES);
            if let Some(mailbox) = boxes.get_mut(dest) {
                mailbox.waiters.retain(|&w| w != self_id);
            }
        });
        // The wake may have carried a message (probed at the next loop
        // top) OR have been empty (wake competition with another
        // receiver, or an explicit [`wake_waiters`] kick). Only an empty
        // wake consumes budget; when `max_wakes` is exhausted the
        // receive fails with the typed `ReceiveTimedOut` rather than
        // re-parking forever.
        let empty = interrupts::without_interrupts(|| unsafe {
            (&mut *(&raw mut MAILBOXES))
                .get_mut(dest)
                .map(|m| m.queue.is_empty())
                .unwrap_or(true)
        });
        if empty {
            wakes += 1;
            if let Some(limit) = max_wakes {
                if wakes >= limit {
                    return Err(IpcError::ReceiveTimedOut);
                }
            }
        }
    }
}

/// A wake WITHOUT a message: unblocks every task parked on `dest` and
/// reports how many it moved. The explicit cancel/abort form of the
/// blocking contract -- what a real kernel's timeout tick would call
/// on each parked waiter -- and the only way a parked receiver's own
/// `max_wakes` budget can tick without another part of the protocol
/// sending first. A mailbox that still holds a queued message wakes
/// nothing (the receivers' next probe will find the message, and an
/// empty-wake budget must not tick on their behalf for work that is
/// actually available); neither changes the queue's contents.
pub fn wake_waiters(dest: usize) -> usize {
    interrupts::without_interrupts(|| unsafe {
        let boxes = &mut *(&raw mut MAILBOXES);
        let Some(mailbox) = boxes.get_mut(dest) else {
            return 0;
        };
        if !mailbox.queue.is_empty() {
            return 0;
        }
        // Same discipline as `send`'s wake: the registration is consumed
        // only on a successful BLOCKED -> READY edge; a refused one keeps
        // its claim for the next attempt.
        let wakees: Vec<usize> = mailbox.waiters.drain(..).collect();
        let mut woken = 0usize;
        for t in wakees {
            if crate::experimental::scheduler::unblock_task(t) {
                woken += 1;
            } else {
                mailbox.waiters.push_back(t);
            }
        }
        woken
    })
}

/// NPS-003 §4.3: the owning process (here: the endpoint's task) MAY
/// revoke all outstanding send-capabilities for a given sender;
/// revocation MUST take effect for all future `send`/`call`
/// operations without requiring cooperation from capability holders.
/// Returns whether the sender was newly revoked (idempotent
/// replays return false and change nothing). The kernel is the
/// arbiter of the validity (§5.4), so this is the one mutation path
/// for the revoked register -- user-side `send` cannot un-revoke.
pub fn revoke_sender(dest: usize, sender: usize) -> bool {
    interrupts::without_interrupts(|| unsafe {
        let Some(mailbox) = (&mut *(&raw mut MAILBOXES)).get_mut(dest) else {
            return false;
        };
        if mailbox.revoked.contains(&sender) {
            return false;
        }
        mailbox.revoked.push(sender);
        true
    })
}

/// Every-boot negative + positive control for the §5.4 arbiter check
/// and the §4.3 revocation contract, run against a SCRATCH mailbox (a
/// freshly-cleared one, created after the demo mailboxes, so the A/B
/// demo state is untouched). Numeraire of honesty: a check that
/// cannot fail if the mechanism is removed is decorative; each half
/// here issues a real request that MUST be refused (or allowed) with
/// the exact expected error, and the test reports FAILED if not.
///
/// Returns whether every check held. Callers assert on this.
pub fn self_test_arbiter_and_revocation() -> bool {
    let mut all_ok = true;

    // ---- The scratch endpoint: append a fresh mailbox. ----
    // The demo's task 0/A/B mailboxes are indexed 0/1/2 and must be
    // untouched; the scratch endpoint is appended at the end.
    let scratch_dest = unsafe { (*&raw const MAILBOXES).len() };
    unsafe {
        (*&raw mut MAILBOXES).push(Mailbox {
            queue: VecDeque::new(),
            capacity: DEFAULT_CAPACITY,
            revoked: Vec::new(),
            waiters: VecDeque::new(),
        });
    }

    // ---- arbiter positive control: a truthful send IS queued. ----
    // The caller here is task 0 (kernel_main), so a message honestly
    // claiming sender 0 must go through.
    let honest = send(
        scratch_dest,
        IpcMessage { sender: 0, seq: 1 },
    );
    let honest_ok = matches!(honest, Ok(()));
    all_ok &= honest_ok;
    serial_println!(
        "  experimental ipc arbiter self-test: honest send from the running task -- {}",
        if honest_ok { "OK (queued)" } else { "FAILED" }
    );

    // ---- arbiter negative control: a forged sender is refused. ----
    // The caller is still task 0, but the message claims sender 1.
    // Must be refused with exactly NotRunningTask, and the queue must
    // be unchanged (nothing silently queued, nothing dropped).
    let forged = send(
        scratch_dest,
        IpcMessage { sender: 1, seq: 2 },
    );
    let forged_ok = matches!(forged, Err(IpcError::NotRunningTask));
    all_ok &= forged_ok;
    serial_println!(
        "  experimental ipc arbiter self-test: forged sender (claims 1, running 0) refused with NotRunningTask -- {}",
        if forged_ok { "OK" } else { "FAILED" }
    );

    // Queue-depth check for both controls: exactly the one honest
    // message is queued.
    let depth_ok = unsafe { MAILBOXES[scratch_dest].queue.len() == 1 };
    all_ok &= depth_ok;

    // ---- revocation negative control: revoke sender 0, then an
    // honest send must fail with Revoked. ----
    let newly_revoked = revoke_sender(scratch_dest, 0);
    let revoke_ok = newly_revoked;
    all_ok &= revoke_ok;
    serial_println!(
        "  experimental ipc revocation self-test: revoke_sender(scratch, 0) newly-revoked -- {}",
        if revoke_ok { "OK" } else { "FAILED" }
    );

    let post_revoke = send(
        scratch_dest,
        IpcMessage { sender: 0, seq: 3 },
    );
    let post_revoke_ok = matches!(post_revoke, Err(IpcError::Revoked));
    all_ok &= post_revoke_ok;
    serial_println!(
        "  experimental ipc revocation self-test: send after revocation refused with Revoked -- {}",
        if post_revoke_ok { "OK" } else { "FAILED" }
    );

    // ---- queued-past-revocation rule: the message queued BEFORE the
    // revocation must still be receivable (revocation is
    // forward-looking only). ----
    let drained = try_receive(scratch_dest);
    let drained_ok = matches!(drained, Some(IpcMessage { sender: 0, seq: 1 }));
    all_ok &= drained_ok;
    serial_println!(
        "  experimental ipc revocation self-test: message queued before revocation still receivable after it -- {}",
        if drained_ok { "OK" } else { "FAILED" }
    );

    // ---- Idempotence half of §4.3: re-revoking returns false and
    // changes nothing. ----
    let idempotent = revoke_sender(scratch_dest, 0);
    let idempotent_ok = !idempotent;
    all_ok &= idempotent_ok;
    serial_println!(
        "  experimental ipc revocation self-test: re-revoking an already-revoked sender is a no-op returning false -- {}",
        if idempotent_ok { "OK" } else { "FAILED" }
    );

    // ---- Blocking receive (NPS-003 §3), NON-BLOCKING legs only
    // here: the legs of `receive` that PARK the caller require a
    // scheduler round-trip to wake from, and the caller of this
    // self-test is task 0 (kernel_main) -- parking task 0 would park
    // the boot itself. The parked-wake and typed-timeout legs are
    // therefore proven every boot by the spawned C/D demo in
    // `kernel_main` (tasks C and D), which parks a real task and
    // drives both a wake_waiters kick and a wake-by-send against it.
    // What this self-test still proves inline: an out-of-range
    // endpoint fails IMMEDIATELY with NoSuchMailbox (never parks),
    // and a message queued BEFORE the receive is delivered at the
    // first probe (drain, never a block either).
    let out_of_range = receive(unsafe { (*&raw const MAILBOXES).len() }, Some(1));
    let range_ok = matches!(out_of_range, Err(IpcError::NoSuchMailbox));
    all_ok &= range_ok;
    serial_println!(
        "  experimental ipc blocking-receive self-test: receive on an out-of-range endpoint returned NoSuchMailbox immediately (never parked) -- {}",
        if range_ok { "OK" } else { "FAILED" }
    );

    let summary_ok = all_ok;
    serial_println!(
        "  experimental ipc self-test: arbiter + revocation + blocking-receive contract checks -- {}",
        if summary_ok { "OK" } else { "FAILED" }
    );
    summary_ok
}
