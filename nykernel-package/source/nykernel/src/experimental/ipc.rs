//! Inter-task mailbox IPC -- a real message-passing primitive between
//! independently scheduled tasks (`crate::experimental::scheduler`).
//!
//! See `crate::experimental`'s module-level banner: this is **not**
//! NPS-003 (IPC and Capability Passing) conformance. NPS-003 is
//! `Draft`, transport-pending at §6.1 per `NPC-004`, and even once
//! Accepted will require things this module doesn't attempt --
//! capability-scoped transfer validation (§5.2: a sender must not be
//! able to transfer a broader capability than it holds), the kernel
//! as sole arbiter of that validity (§5.4), and a real transport
//! benchmark gate (§6.1). What this module honestly demonstrates: a
//! real bounded mailbox, real cross-task data transfer, and real
//! backpressure (a full mailbox is a reportable error, not silently
//! dropped or silently blocking forever) -- the mechanical primitive
//! any eventual NPS-003-conformant kernel IPC will also need
//! underneath its actual policy.

use alloc::collections::VecDeque;
use alloc::vec::Vec;

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
}

struct Mailbox {
    queue: VecDeque<IpcMessage>,
    capacity: usize,
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
        MAILBOXES.clear();
        for _ in 0..task_count {
            MAILBOXES.push(Mailbox {
                queue: VecDeque::new(),
                capacity: DEFAULT_CAPACITY,
            });
        }
    }
}

/// Copies `msg` into task `dest`'s mailbox. Fails closed on a full
/// mailbox or an out-of-range destination rather than ever silently
/// dropping or panicking -- the caller is the one who gets to decide
/// what "the recipient isn't keeping up" means for their protocol.
pub fn send(dest: usize, msg: IpcMessage) -> Result<(), IpcError> {
    x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        let mailbox = MAILBOXES.get_mut(dest).ok_or(IpcError::NoSuchMailbox)?;
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
/// add but isn't needed to demonstrate the primitive itself, and
/// keeping the demo's control flow simple keeps the correctness
/// assertions (see `kernel_main`) easy to reason about.
pub fn try_receive(dest: usize) -> Option<IpcMessage> {
    x86_64::instructions::interrupts::without_interrupts(|| unsafe {
        MAILBOXES.get_mut(dest).and_then(|m| m.queue.pop_front())
    })
}
