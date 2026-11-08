//! An ARMED page-fault probe: one expected user fault, recorded instead of
//! fatal.
//!
//! Why this exists: `idt::page_fault_handler` has exactly two outcomes today
//! -- the boot's guard-page demo (armed, expected, fatal by design) and the
//! generic "print and halt". There is no path that lets a RING-3 fault be
//! evidence: any user fault stops the kernel, so "the page is really gone"
//! could only ever be proven by reading page tables from the kernel, never
//! by the holder's own access.
//!
//! The revocation leg needs the access itself. So this module lets the
//! kernel ARM one fault -- the address, and the task expected to touch it --
//! and the handler then:
//!
//!   * checks the fault is the armed one (address matches, and the running
//!     task is the armed task when one was named),
//!   * records what the CPU said (CR2, the error code, whether the access
//!     came from ring 3, and which task made it),
//!   * TERMINATES that task through the scheduler's own lifecycle
//!     (`terminate_current`, the same edge `CAP_EXIT` uses) instead of
//!     halting the machine.
//!
//! Anything that does not match still halts exactly as before -- the probe
//! is a keyhole, not a general fault policy. It is armed only by the 0x84
//! proof, and it disarms itself when it fires.

use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// The fault the kernel is willing to absorb.
static ARMED: AtomicBool = AtomicBool::new(false);
static EXPECTED_ADDR: AtomicU64 = AtomicU64::new(0);
/// The task expected to fault, or [`NO_TASK`] for "any".
static EXPECTED_TASK: AtomicU64 = AtomicU64::new(NO_TASK);
pub const NO_TASK: u64 = u64::MAX;

/// What the CPU reported, recorded once.
static HITS: AtomicU64 = AtomicU64::new(0);
static FAULT_ADDR: AtomicU64 = AtomicU64::new(0);
static FAULT_ERROR: AtomicU64 = AtomicU64::new(0);
static FAULT_TASK: AtomicU64 = AtomicU64::new(0);
static FAULT_FROM_RING3: AtomicBool = AtomicBool::new(false);

/// Arms the probe for `addr`, expected to be touched by `task`
/// ([`NO_TASK`] accepts any task).
pub fn arm(addr: u64, task: u64) {
    HITS.store(0, Ordering::SeqCst);
    FAULT_ADDR.store(0, Ordering::SeqCst);
    FAULT_ERROR.store(0, Ordering::SeqCst);
    FAULT_TASK.store(0, Ordering::SeqCst);
    FAULT_FROM_RING3.store(false, Ordering::SeqCst);
    EXPECTED_ADDR.store(addr, Ordering::SeqCst);
    EXPECTED_TASK.store(task, Ordering::SeqCst);
    ARMED.store(true, Ordering::SeqCst);
}

pub fn is_armed() -> bool {
    ARMED.load(Ordering::SeqCst)
}

/// True when `accessed` is the armed address for the running task. Does NOT
/// disarm: the caller decides, so a mismatched fault keeps the probe armed.
pub fn matches(accessed: u64, task: usize) -> bool {
    if !is_armed() {
        return false;
    }
    let want = EXPECTED_TASK.load(Ordering::SeqCst);
    accessed == EXPECTED_ADDR.load(Ordering::SeqCst)
        && (want == NO_TASK || want == task as u64)
}

/// Records the fault and disarms. Returns the fields, so the caller can
/// decide what to do next without reading them back.
pub fn record(accessed: u64, error_code: u64, task: usize, from_ring3: bool) {
    FAULT_ADDR.store(accessed, Ordering::SeqCst);
    FAULT_ERROR.store(error_code, Ordering::SeqCst);
    FAULT_TASK.store(task as u64, Ordering::SeqCst);
    FAULT_FROM_RING3.store(from_ring3, Ordering::SeqCst);
    HITS.fetch_add(1, Ordering::SeqCst);
    ARMED.store(false, Ordering::SeqCst);
}

/// What the probe saw: how many times it fired (0 = the fault never
/// happened, which is a FAILED claim, not a pass), the address, the CPU's
/// error code, the task, and whether the access came from ring 3.
pub fn report() -> Option<ProbeReport> {
    if HITS.load(Ordering::SeqCst) == 0 {
        return None;
    }
    Some(ProbeReport {
        hits: HITS.load(Ordering::SeqCst),
        addr: FAULT_ADDR.load(Ordering::SeqCst),
        error: FAULT_ERROR.load(Ordering::SeqCst),
        task: FAULT_TASK.load(Ordering::SeqCst),
        from_ring3: FAULT_FROM_RING3.load(Ordering::SeqCst),
    })
}

#[derive(Clone, Copy)]
pub struct ProbeReport {
    pub hits: u64,
    pub addr: u64,
    pub error: u64,
    pub task: u64,
    pub from_ring3: bool,
}
