//! Which address space does a session live in?
//!
//! The display work keeps needing the same fact: "the server of this
//! session's display lives in THAT address space, so the region window has
//! to exist there too". Until now the kernel answered it with a proof-only
//! pair of statics (`PROOF_PML4`/`PEER_PML4`, and `other_proof_pml4()`
//! meaning "the one that is not active") -- true for the 0x84 proof's two
//! processes and false for every other session. This module is the general
//! answer: a small fixed table from SESSION id to the PML4 physical address
//! of the space that session runs in.
//!
//! What it is not: an address-space allocator, or a task table. The
//! scheduler owns tasks and their CR3s; an `AddressSpace` owns the page
//! tables. A session is registered here when it is ASSOCIATED with a space
//! (a proof process when its space is built, a display server when it
//! starts serving), and unregistered when that session dies. Lookups are
//! read-only facts and are answered with `None`, never with a guess.
//!
//! Two honest limits, stated rather than implied:
//!
//!   * several sessions may share one space (the ordinary boot's tasks
//!     do); the table stores one row per session and does not deduplicate
//!     spaces, because the question it answers is per-session;
//!   * a session that was never registered has no space as far as this
//!     module is concerned, and callers must treat that as "cannot map
//!     there", not as "same space as mine".

use core::sync::atomic::{AtomicU64, Ordering};

/// How many sessions the kernel can name a space for at once.
pub const SLOTS: usize = 8;

/// `EBUSY`: that session is already registered for a different space.
pub const EBUSY: i64 = -16;
/// `EINVAL`: a zero session id or a zero PML4 address.
pub const EINVAL: i64 = -22;

static SESSIONS: [AtomicU64; SLOTS] = [const { AtomicU64::new(0) }; SLOTS];
static SPACES: [AtomicU64; SLOTS] = [const { AtomicU64::new(0) }; SLOTS];

/// Registers `session` in the space whose PML4 is `pml4`. Idempotent when
/// the pair is already recorded; `EBUSY` when the session is recorded
/// against a DIFFERENT space (re-registering a live session under a new
/// PML4 would silently move every window it owns), `EINVAL` for zeros.
pub fn register(session: u64, pml4: u64) -> Result<u64, i64> {
    if session == 0 || pml4 == 0 {
        return Err(EINVAL);
    }
    for index in 0..SLOTS {
        let stored = SESSIONS[index].load(Ordering::SeqCst);
        if stored == session {
            if SPACES[index].load(Ordering::SeqCst) == pml4 {
                return Ok(pml4);
            }
            return Err(EBUSY);
        }
    }
    for index in 0..SLOTS {
        if SESSIONS[index]
            .compare_exchange(0, session, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            SPACES[index].store(pml4, Ordering::SeqCst);
            return Ok(pml4);
        }
    }
    Err(EBUSY) // the table is full
}

/// Registers `session` in whatever address space is active right now. The
/// caller is the session itself (a server starting up in its own space), so
/// the CR3 the CPU is running on IS that session's space.
pub fn register_active(session: u64) -> Result<u64, i64> {
    let (frame, _flags) = x86_64::registers::control::Cr3::read();
    register(session, frame.start_address().as_u64())
}

/// The PML4 physical address of a session's space, if it was registered.
pub fn pml4_of(session: u64) -> Option<u64> {
    if session == 0 {
        return None;
    }
    for index in 0..SLOTS {
        if SESSIONS[index].load(Ordering::SeqCst) == session {
            let pml4 = SPACES[index].load(Ordering::SeqCst);
            return if pml4 == 0 { None } else { Some(pml4) };
        }
    }
    None
}

/// The first session registered in a space, by its PML4 address. Used to
/// name who a window belongs to; `None` when the space is unknown.
pub fn session_of(pml4: u64) -> Option<u64> {
    if pml4 == 0 {
        return None;
    }
    for index in 0..SLOTS {
        if SPACES[index].load(Ordering::SeqCst) == pml4 {
            let session = SESSIONS[index].load(Ordering::SeqCst);
            return if session == 0 { None } else { Some(session) };
        }
    }
    None
}

/// Forgets a session's space. Called where a session dies: a stale row
/// would keep pointing at page tables that belong to a destroyed space.
pub fn unregister(session: u64) -> Option<u64> {
    for index in 0..SLOTS {
        if SESSIONS[index]
            .compare_exchange(session, 0, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            let pml4 = SPACES[index].swap(0, Ordering::SeqCst);
            return if pml4 == 0 { None } else { Some(pml4) };
        }
    }
    None
}

/// How many sessions are registered right now.
pub fn count() -> usize {
    SESSIONS
        .iter()
        .filter(|session| session.load(Ordering::SeqCst) != 0)
        .count()
}

/// The kernel's own self-test of the table's rules. No mapping is involved:
/// this is a table of facts, and the facts are checked the way the served
/// path would ask for them.
pub fn self_test() -> bool {
    let registered = register(11, 0x1234_0000).is_ok();
    let idempotent = register(11, 0x1234_0000).is_ok();
    let conflict = register(11, 0x2222_0000) == Err(EBUSY);
    let zero_session = register(0, 0x1) == Err(EINVAL);
    let zero_space = register(12, 0) == Err(EINVAL);
    let lookups = pml4_of(11) == Some(0x1234_0000)
        && session_of(0x1234_0000) == Some(11)
        && pml4_of(110) == None
        && session_of(0x1) == None
        && count() == 1;
    let second = register(12, 0x1234_0000).is_ok() && count() == 2;
    let gone = unregister(11) == Some(0x1234_0000)
        && pml4_of(11) == None
        && unregister(11) == None
        && count() == 1;
    let cleaned = unregister(12) == Some(0x1234_0000) && count() == 0;
    registered
        && idempotent
        && conflict
        && zero_session
        && zero_space
        && lookups
        && second
        && gone
        && cleaned
}
