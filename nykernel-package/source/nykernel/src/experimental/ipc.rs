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
//! What honestly remains out of scope after this increment: blocking
//! receive, the capability-transfer message field (§5.1), shared-memory
//! regions (§3.1), and NPS-003 §6.1's wire transport -- the kernel-side
//! mailbox here is the in-kernel same-address-space primitive; the
//! userspace wire transports live in the Linux-backend tree and are
//! measured (IPC-001 §38) separately.

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
        if msg.sender != running_task_id() {
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
        Ok(())
    })
}

/// Non-blocking: returns immediately with whatever is (or isn't)
/// already queued. There is no blocking `receive()` in this proof of
/// concept -- a blocking version would need to cooperatively
/// `yield_task()` in a loop while empty, which is straightforward to
/// add but isn't needed to demonstrate the primitive + arbiter + the
/// revocation contract, and keeping the demo's control flow simple
/// keeps the correctness assertions (see `kernel_main`) easy to
/// reason about.
pub fn try_receive(dest: usize) -> Option<IpcMessage> {
    interrupts::without_interrupts(|| unsafe {
        (&mut *(&raw mut MAILBOXES)).get_mut(dest).and_then(|m| m.queue.pop_front())
    })
}

/// Blocking receive, added 2026-10-11 as the next NPS-003 conformance
/// increment (NPS-003 §3: `receive` "blocks the calling thread until a
/// message arrives at an endpoint it owns"). Cooperative-blocking:
/// the caller yields the core between polls (NPS-002 §5.4 -- a
/// process whose thread waits on a receive MUST yield the core, per
/// "Performance"), so a waiting receiver never spins hot. Bounded by
/// `max_polls` so a sender that never sends fails with a typed error
/// ("defined error rather than blocking indefinitely" -- the NPS-002
/// §7.1 posture, adapted here to a receive timeout) -- pass
/// `Option::None` to wait without a bound (used by nothing yet; kept
/// for callers whose protocol has its own liveness story). Returns
/// the message on success; `ReceiveTimedOut` if `max_polls` elapsed;
/// `NoSuchMailbox` for an out-of-range endpoint.
pub fn receive(dest: usize, max_polls: Option<u32>) -> Result<IpcMessage, IpcError> {
    let mut polls = 0u32;
    loop {
        if let Some(msg) = try_receive(dest) {
            return Ok(msg);
        }
        if let Some(limit) = max_polls {
            if polls >= limit {
                return Err(IpcError::ReceiveTimedOut);
            }
        }
        polls += 1;
        crate::experimental::scheduler::yield_task();
    }
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

    // ---- Blocking receive (NPS-003 §3, added 2026-10-11): use a
    // FRESH scratch mailbox (the first one is revoked for sender 0 by
    // the revocation test above, by design). An empty bounded
    // `receive` MUST return the typed ReceiveTimedOut rather than
    // hanging or busy-spinning (each poll yields the core, so a hung
    // test here would show as a boot timeout, caught by the harness).
    let scratch_dest2 = unsafe { (*&raw const MAILBOXES).len() };
    unsafe {
        (*&raw mut MAILBOXES).push(Mailbox {
            queue: VecDeque::new(),
            capacity: DEFAULT_CAPACITY,
            revoked: Vec::new(),
        });
    }
    let timed_out = receive(scratch_dest2, Some(2));
    let timeout_ok = matches!(timed_out, Err(IpcError::ReceiveTimedOut));
    all_ok &= timeout_ok;
    serial_println!(
        "  experimental ipc blocking-receive self-test: bounded receive on an empty mailbox returned ReceiveTimedOut (typed, never an indefinite block) -- {}",
        if timeout_ok { "OK" } else { "FAILED" }
    );

    // Then the positive half: send once (as the running task), and the
    // SAME bounded receive must now deliver exactly that message.
    let delivered = match send(scratch_dest2, IpcMessage { sender: 0, seq: 7 }) {
        Ok(()) => receive(scratch_dest2, Some(2)),
        Err(e) => Err(e),
    };
    let deliver_ok = matches!(delivered, Ok(IpcMessage { sender: 0, seq: 7 }));
    all_ok &= deliver_ok;
    serial_println!(
        "  experimental ipc blocking-receive self-test: bounded receive delivered the message sent before it ({:?}) -- {}",
        delivered,
        if deliver_ok { "OK" } else { "FAILED" }
    );

    let summary_ok = all_ok;
    serial_println!(
        "  experimental ipc self-test: arbiter + revocation + blocking-receive contract checks -- {}",
        if summary_ok { "OK" } else { "FAILED" }
    );
    summary_ok
}
