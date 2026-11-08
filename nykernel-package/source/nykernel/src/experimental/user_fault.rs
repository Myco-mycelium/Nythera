//! A ring-3 page fault ends that PROCESS, not the machine.
//!
//! Before this module `idt::page_fault_handler` had exactly two outcomes: the
//! boot's armed guard-page demo (expected, fatal by design) and the generic
//! "print and halt". Both are ring 0. A RING-3 fault -- a process touching a
//! page it does not have -- therefore stopped the kernel dead, which made a
//! whole class of capability facts unprovable from the holder's side ("the
//! page is really gone" could only be read out of page tables, never
//! observed by the process that lost it) and made one buggy program a
//! machine-wide kill.
//!
//! The policy is now the ordinary one for a protected machine: a fault taken
//! from ring 3 is RECORDED (address, the CPU's error word, the task, its
//! session, and a named reason derived from the error bits) and the faulting
//! task is TERMINATED through the scheduler's own lifecycle -- the same edge
//! `CAP_EXIT` uses -- after its capabilities are released and its session
//! withdrawn (`service84::abandon_gate_session`). Everything else keeps
//! running. Faults taken from ring 0 are still fatal: that is the kernel
//! contradicting its own mapping record, which the boot has no business
//! surviving.
//!
//! This is not a trap handler for a faulting instruction: the task does not
//! resume. It is the reason a process dies with a record instead of taking
//! the kernel with it.

use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// Why the CPU refused the access, derived from the error word rather than
/// guessed: bit 0 says the page was not present, bit 1 says the access was a
/// write, bit 2 says it came from user mode.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reason {
    /// Not present: nothing is mapped there at all (the common case for a
    /// revoked or never-granted page).
    NotMapped,
    /// Present but refused: a protection violation (write to a read-only
    /// page, or a user access to a supervisor page).
    Protection,
}

impl Reason {
    fn of(error: u64) -> Self {
        if error & 1 == 0 {
            Reason::NotMapped
        } else {
            Reason::Protection
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Reason::NotMapped => "not mapped",
            Reason::Protection => "protection",
        }
    }
}

/// What the CPU reported, kept for the proof's read-back.
#[derive(Clone, Copy)]
pub struct Record {
    pub addr: u64,
    pub error: u64,
    pub task: u64,
    pub session: u64,
    pub from_ring3: bool,
    pub write: bool,
    pub reason: Reason,
}

static COUNT: AtomicU64 = AtomicU64::new(0);
static FAULT_ADDR: AtomicU64 = AtomicU64::new(0);
static FAULT_ERROR: AtomicU64 = AtomicU64::new(0);
static FAULT_TASK: AtomicU64 = AtomicU64::new(0);
static FAULT_SESSION: AtomicU64 = AtomicU64::new(0);
static FAULT_FROM_RING3: AtomicBool = AtomicBool::new(false);

/// Records one user fault. Called from the handler with the CPU's own words;
/// returns the record so the caller can print it without reading it back.
pub fn record(addr: u64, error: u64, task: usize, session: u64, from_ring3: bool) -> Record {
    let rec = Record {
        addr,
        error,
        task: task as u64,
        session,
        from_ring3,
        write: error & 2 != 0,
        reason: Reason::of(error),
    };
    FAULT_ADDR.store(rec.addr, Ordering::SeqCst);
    FAULT_ERROR.store(rec.error, Ordering::SeqCst);
    FAULT_TASK.store(rec.task, Ordering::SeqCst);
    FAULT_SESSION.store(rec.session, Ordering::SeqCst);
    FAULT_FROM_RING3.store(rec.from_ring3, Ordering::SeqCst);
    COUNT.fetch_add(1, Ordering::SeqCst);
    rec
}

/// Every user fault this boot took, in the order they happened (only the
/// last one's details are kept; the count says whether a task died here).
pub fn count() -> u64 {
    COUNT.load(Ordering::SeqCst)
}

/// The last recorded fault, or `None` if no ring-3 fault has happened.
pub fn report() -> Option<Record> {
    if COUNT.load(Ordering::SeqCst) == 0 {
        return None;
    }
    let error = FAULT_ERROR.load(Ordering::SeqCst);
    Some(Record {
        addr: FAULT_ADDR.load(Ordering::SeqCst),
        error,
        task: FAULT_TASK.load(Ordering::SeqCst),
        session: FAULT_SESSION.load(Ordering::SeqCst),
        from_ring3: FAULT_FROM_RING3.load(Ordering::SeqCst),
        write: error & 2 != 0,
        reason: Reason::of(error),
    })
}
