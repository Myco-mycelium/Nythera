//! Display ownership -- who may change what a session is looking at.
//!
//! The first version of this module (2026-11-15, same day) gave the
//! display ONE owner: the session that claimed it could repaint, everyone
//! else was refused, and the claim died with its session. That is enough
//! for one client and no server, which is not the shape a display has. A
//! display has a SERVER that outlives the clients it serves, and each
//! client gets its own slice of authority: one may switch the identity,
//! another may request a form factor, a third may only look.
//!
//! So this module is now two things, in this order of precedence:
//!
//! ```text
//!   server exists   the server may mutate everything; an ATTACHED CLIENT
//!                   may mutate exactly the bits its own grant carries
//!                   (clamped to the server's policy at attach time);
//!                   everyone else -- including the exclusive owner below
//!                   and every unattached session -- is -EACCES.
//!   no server       the exclusive owner (0x61) rules, as before: unowned
//!                   means anyone may mutate, owned means only the owner.
//! ```
//!
//! The two forms cannot overlap: `DISPLAY_SERVE` answers `-EBUSY` while an
//! exclusive owner exists, and `DISPLAY_ACQUIRE` answers `-EBUSY` while a
//! server exists. A machine is either running a display server or letting
//! one session hold the display directly, never both.
//!
//! The server outlives its clients by construction: a client's withdrawal
//! frees its slot and nothing else (`release_on_withdrawal`), while the
//! server keeps its policy, its remaining clients and the display's state.
//! A SERVER's withdrawal dissolves the server and detaches every client --
//! an orphaned client table would refuse everyone forever, which is worse
//! than the ownership leak this module exists to prevent. The display's
//! identity and form factor are kernel state (`platform_mode`,
//! `device_mode`), so they survive both: the policy dies, the picture
//! stays.
//!
//! Authority bits are per-call, not per-session-blob, and each one names
//! the row it unlocks:
//!
//!   [`AUTH_IDENTITY`]  `PLATFORM_SET`        (0x5e)
//!   [`AUTH_MODE`]      `PLATFORM_MODE_SET`   (0x60)
//!
//! Session ids are never caller-supplied: the gate hands over the id the
//! scheduler recorded at the ring transition. Ring 0 (the boot session) is
//! privileged throughout -- the kernel is not a tenant of the display it
//! is running on.

use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

/// No session has claimed the display (exclusive-owner form).
pub const NO_OWNER: u64 = 0;
/// No display server is running.
pub const NO_SERVER: u64 = 0;

/// `PLATFORM_SET` may be called by this client.
pub const AUTH_IDENTITY: u64 = 1 << 0;
/// `PLATFORM_MODE_SET` may be called by this client.
pub const AUTH_MODE: u64 = 1 << 1;
/// Both mutating rows. Also the grant a SERVING session reports for itself.
pub const AUTH_ALL: u64 = AUTH_IDENTITY | AUTH_MODE;

/// How many clients a display server can supervise at once.
pub const CLIENT_SLOTS: usize = 4;

/// `EBUSY`: another session already holds the display / already attached.
pub const EBUSY: i64 = -16;
/// `EACCES`: this session has no authority here.
pub const EACCES: i64 = -13;
/// `EINVAL`: an unknown authority bit, or no bits at all.
pub const EINVAL: i64 = -22;
/// `ENODEV`: there is no display server to attach to.
pub const ENODEV: i64 = -19;
/// Linux `ENOENT`: the caller has no surface to give back.
pub const ENOENT: i64 = -2;

static OWNER: AtomicU64 = AtomicU64::new(NO_OWNER);
static SERVER: AtomicU64 = AtomicU64::new(NO_SERVER);
/// The grant mask the server publishes for its clients.
static POLICY: AtomicU64 = AtomicU64::new(0);
/// One word per client slot: `session << 32 | grant`. Zero means free.
static CLIENTS: [AtomicU64; CLIENT_SLOTS] = [const { AtomicU64::new(0) }; CLIENT_SLOTS];

/// One client surface is a MAPPED REGION, not a copy of one.
///
/// The client asks for it with `DISPLAY_SURFACE` (0x69), the kernel maps a
/// fresh page into a window it chooses, and the client DRAWS INTO THAT
/// WINDOW directly -- ordinary stores, no per-pixel call. The server reads
/// the very same pixels through the window the region machine mirrored
/// into the other proof address space: `allocate_region_mapping` maps a
/// requested region into BOTH spaces at the SAME virtual address and over
/// the SAME frames (that mirroring is what made the earlier capability
/// handover work), so composition needs no CR3 switch and no copy. This is
/// the cross-address-space window: one frame, two address spaces, one
/// window each.
///
/// What this module keeps per client slot is the handle and the region id,
/// and nothing of the pixels. On a boot with ONE address space the mirror
/// is a no-op and only the owning process can see the window -- the
/// surface still works for its client, and composition answers with the
/// tiles it could actually read.
const NO_SURFACE: u32 = 0;

/// The region id a slot's surface lives in, stored as `region + 1`; zero
/// means the slot has no surface.
static SURFACE_REGION: [AtomicU32; CLIENT_SLOTS] = [const { AtomicU32::new(0) }; CLIENT_SLOTS];
/// The capability handle the client was granted over that region.
static SURFACE_HANDLE: [AtomicU32; CLIENT_SLOTS] = [const { AtomicU32::new(0) }; CLIENT_SLOTS];
/// The WINDOW's own geometry, kept here rather than looked up through the
/// capability machine at release time. The display owns these frames, so the
/// display must be able to free them even when the capability that named them
/// is already gone, refused, or half torn down: a release path that had to
/// ask another table where its window was could drop the tile and leave the
/// window mapped.
static SURFACE_BASE: [AtomicU64; CLIENT_SLOTS] = [const { AtomicU64::new(0) }; CLIENT_SLOTS];
static SURFACE_LEN: [AtomicU64; CLIENT_SLOTS] = [const { AtomicU64::new(0) }; CLIENT_SLOTS];
/// WHOSE tile a slot's record is, by session. Normally that is the session
/// whose client slot it sits in, but `stop` (a server's death) dissolves the
/// client slots while the tiles are still mapped in their owners' address
/// spaces -- this kernel frees a window only from the space that has it, so
/// those records are PARKED under their owner's name instead of dropped, and
/// the owner gives the tile back with 0x6c or when it dies. Zero means the
/// record is a live client's own.
static SURFACE_OWNER: [AtomicU64; CLIENT_SLOTS] = [const { AtomicU64::new(0) }; CLIENT_SLOTS];

/// What a slot's surface record holds: the capability's names, and the window
/// the display must give back.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SurfaceRecord {
    pub region: u32,
    pub handle: u32,
    pub base: u64,
    pub len: u64,
}

impl SurfaceRecord {
    /// A record naming no window -- what a caller holds when a session died
    /// with no tile at all.
    pub const EMPTY: Self = Self {
        region: u32::MAX,
        handle: u32::MAX,
        base: 0,
        len: 0,
    };
}

fn slot_word(session: u64, grant: u64) -> u64 {
    (session << 32) | (grant & 0xffff_ffff)
}

/// A client may ask for its surface only when it is attached and has not
/// already got one: `-EACCES` for a non-client, `-EBUSY` for a second ask.
pub fn surface_free(session: u64) -> Result<usize, i64> {
    let slot = client_slot(session)?;
    if SURFACE_REGION[slot].load(Ordering::SeqCst) != NO_SURFACE {
        return Err(EBUSY);
    }
    Ok(slot)
}

/// Records the region, the handle minted over it, and the WINDOW's geometry,
/// as the caller's surface. The region id is stored offset by one so zero
/// keeps meaning "no surface"; the geometry is stored alongside it so the
/// release never has to find the window through another table.
pub fn surface_record(
    session: u64,
    region: u32,
    handle: u32,
    base: u64,
    len: u64,
) -> Result<usize, i64> {
    let slot = surface_free(session)?;
    match SURFACE_REGION[slot].compare_exchange(
        NO_SURFACE,
        region.wrapping_add(1),
        Ordering::SeqCst,
        Ordering::SeqCst,
    ) {
        Ok(_) => {
            SURFACE_HANDLE[slot].store(handle, Ordering::SeqCst);
            SURFACE_BASE[slot].store(base, Ordering::SeqCst);
            SURFACE_LEN[slot].store(len, Ordering::SeqCst);
            SURFACE_OWNER[slot].store(0, Ordering::SeqCst);
            Ok(slot)
        }
        Err(_) => Err(EBUSY),
    }
}

/// Takes the caller's surface record AWAY, answering `(region, handle)` so
/// the caller can give the window back. The record is cleared first, so a
/// second release is refused `-ENOENT` by name rather than freeing a window
/// twice. Used by the release row and by a session's withdrawal -- a
/// surface window outlives the capability machine's record of it, so
/// whoever drops the record must also drop the window.
pub fn surface_take(session: u64) -> Result<SurfaceRecord, i64> {
    // A client's own slot first; failing that, a record PARKED under this
    // session by a server's dissolution. Both are "this session's tile", and
    // the caller is the only context that can free its window.
    let slot = match client_slot(session) {
        Ok(slot) if SURFACE_REGION[slot].load(Ordering::SeqCst) != NO_SURFACE => slot,
        _ => match (0..CLIENT_SLOTS).find(|&slot| {
            SURFACE_OWNER[slot].load(Ordering::SeqCst) == session
                && SURFACE_REGION[slot].load(Ordering::SeqCst) != NO_SURFACE
        }) {
            Some(slot) => slot,
            None => return Err(ENOENT),
        },
    };
    let stored = SURFACE_REGION[slot].swap(NO_SURFACE, Ordering::SeqCst);
    if stored == NO_SURFACE {
        return Err(ENOENT);
    }
    Ok(SurfaceRecord {
        region: stored - 1,
        handle: SURFACE_HANDLE[slot].swap(0, Ordering::SeqCst),
        base: SURFACE_BASE[slot].swap(0, Ordering::SeqCst),
        len: SURFACE_LEN[slot].swap(0, Ordering::SeqCst),
    })
}

/// A slot's window geometry, when it has a surface. Composition reads the
/// client's tile through THIS, not through the capability table: the display
/// knows where its own windows are.
pub fn surface_geometry(slot: usize) -> Option<(u64, u64)> {
    if slot >= CLIENT_SLOTS || SURFACE_REGION[slot].load(Ordering::SeqCst) == NO_SURFACE {
        return None;
    }
    Some((
        SURFACE_BASE[slot].load(Ordering::SeqCst),
        SURFACE_LEN[slot].load(Ordering::SeqCst),
    ))
}

/// A slot's `(region, handle)`, when it has a surface.
pub fn surface_region(slot: usize) -> Option<(u32, u32)> {
    if slot >= CLIENT_SLOTS {
        return None;
    }
    let stored = SURFACE_REGION[slot].load(Ordering::SeqCst);
    if stored == NO_SURFACE {
        return None;
    }
    Some((
        stored - 1,
        SURFACE_HANDLE[slot].load(Ordering::SeqCst),
    ))
}

/// How many client surfaces exist right now.
pub fn surface_count() -> usize {
    SURFACE_REGION
        .iter()
        .filter(|stored| stored.load(Ordering::SeqCst) != NO_SURFACE)
        .count()
}

/// Where a slot's surface lands on a `width` x `height` display whose
/// chrome reserves `status` rows. The surface is one page of 32x32 ARGB
/// words, placed in a row under the status bar, one tile apart, clamped so
/// it never runs off the panel.
pub fn surface_origin(slot: usize, width: u32, height: u32, status: u32) -> (u32, u32) {
    let x = 8 + slot as u32 * (SURFACE_TILE + 8);
    let y = status + 8;
    (
        core::cmp::min(x, width.saturating_sub(SURFACE_TILE)),
        core::cmp::min(y, height.saturating_sub(SURFACE_TILE)),
    )
}

/// The surface tile's side, in pixels: one page of 32-bit words.
pub const SURFACE_TILE: u32 = 32;

/// The exclusive owner (0x61 form), or [`NO_OWNER`].
pub fn owner() -> u64 {
    OWNER.load(Ordering::SeqCst)
}

/// The serving session, or [`NO_SERVER`].
pub fn server() -> u64 {
    SERVER.load(Ordering::SeqCst)
}

/// The grant mask the server publishes to clients.
pub fn policy() -> u64 {
    POLICY.load(Ordering::SeqCst)
}

/// How many clients are attached right now.
pub fn client_count() -> usize {
    CLIENTS
        .iter()
        .filter(|slot| slot.load(Ordering::SeqCst) != 0)
        .count()
}

/// The `(slot, grant)` a session holds, if it is attached.
pub fn client_grant(session: u64) -> Option<(usize, u64)> {
    if session == NO_OWNER {
        return None;
    }
    let want = session << 32;
    for (index, slot) in CLIENTS.iter().enumerate() {
        let word = slot.load(Ordering::SeqCst);
        if word & !0xffff_ffff == want {
            return Some((index, word & 0xffff_ffff));
        }
    }
    None
}

/// The slot a session is attached to, if it is a client.
fn client_slot(session: u64) -> Result<usize, i64> {
    match client_grant(session) {
        Some((slot, _)) => Ok(slot),
        None => Err(EACCES),
    }
}

/// The authority rule every mutating row consults, as a pure function of
/// kernel state and the asking session. A caller cannot make this true by
/// claiming a number: the session id is the gate's own record.
pub fn may_mutate_bit(session: u64, bit: u64) -> bool {
    if session == NO_OWNER {
        return false;
    }
    let serving = server();
    if serving != NO_SERVER {
        return serving == session
            || matches!(client_grant(session), Some((_, grant)) if grant & bit != 0);
    }
    let exclusive = owner();
    exclusive == NO_OWNER || exclusive == session
}

/// `may_mutate_bit` for any bit: "may this session change the display at
/// all?" Kept as the rule's readable form for the kernel's own test and
/// for anything that only needs the yes/no.
pub fn may_mutate(session: u64) -> bool {
    may_mutate_bit(session, AUTH_ALL)
}

/// Starts (or re-publishes policy for) a display server owned by
/// `session`. One server at a time, and never beside an exclusive owner.
pub fn serve(session: u64, policy_mask: u64) -> Result<u64, i64> {
    if session == NO_SERVER {
        return Err(EACCES);
    }
    if policy_mask == 0 || policy_mask & !AUTH_ALL != 0 {
        return Err(EINVAL);
    }
    let mut current = SERVER.load(Ordering::SeqCst);
    loop {
        if current == NO_SERVER {
            // The exclusive form and the server form are mutually
            // exclusive; an owner appearing between this check and the
            // claim would be a real conflict, so it is checked inside the
            // claim window.
            if owner() != NO_OWNER {
                return Err(EBUSY);
            }
            match SERVER.compare_exchange(current, session, Ordering::SeqCst, Ordering::SeqCst) {
                Ok(_) => {
                    POLICY.store(policy_mask, Ordering::SeqCst);
                    return Ok(policy_mask);
                }
                Err(actual) => {
                    current = actual;
                    continue;
                }
            }
        }
        if current == session {
            // Re-publishing: the same server may lower or raise its own
            // policy for clients that attach from now on.
            POLICY.store(policy_mask, Ordering::SeqCst);
            return Ok(policy_mask);
        }
        return Err(EBUSY);
    }
}

/// Attaches `session` as a client of the running display server, granting
/// `wanted & policy`. A mask with no bit the server allows is `-EACCES`
/// (there would be nothing to do), an attached session is `-EBUSY`, and a
/// full table is `-EBUSY` too -- the server supervises a fixed set, and
/// answering "full" is honest about it.
pub fn attach(session: u64, wanted: u64) -> Result<(usize, u64), i64> {
    if session == NO_OWNER {
        return Err(EACCES);
    }
    if wanted == 0 || wanted & !AUTH_ALL != 0 {
        return Err(EINVAL);
    }
    if server() == NO_SERVER {
        return Err(ENODEV);
    }
    if client_grant(session).is_some() {
        return Err(EBUSY);
    }
    let grant = wanted & policy();
    if grant == 0 {
        return Err(EACCES);
    }
    let word = slot_word(session, grant);
    for (index, slot) in CLIENTS.iter().enumerate() {
        if slot
            .compare_exchange(0, word, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            return Ok((index, grant));
        }
    }
    Err(EBUSY) // every slot taken
}

/// Gives the caller's slot back. `-EACCES` when it does not hold one.
///
/// THE TEARDOWN CONTRACT for a surface tile: a detach REFUSES `-EBUSY` while
/// the caller still holds one. Dropping the record here would leave a window
/// mapped in the caller's space with nothing in the kernel naming it -- and
/// freeing it here would free pages this context cannot reach, or leave the
/// client's own capability naming a window whose slot is about to be handed
/// to somebody else. So the client gives the tile back with 0x6c first, which
/// frees its window in its own space, and THEN detaches.
pub fn detach(session: u64) -> Result<(), i64> {
    if let Ok(slot) = client_slot(session) {
        if SURFACE_REGION[slot].load(Ordering::SeqCst) != NO_SURFACE {
            return Err(EBUSY); // the tile goes first: 0x6c, then detach
        }
    }
    for (index, slot) in CLIENTS.iter().enumerate() {
        let word = slot.load(Ordering::SeqCst);
        if word != 0 && word & !0xffff_ffff == session << 32 {
            return match slot.compare_exchange(word, 0, Ordering::SeqCst, Ordering::SeqCst) {
                Ok(_) => {
                    // No tile here (refused above if there was one), so
                    // clearing the fields loses nothing.
                    SURFACE_REGION[index].store(NO_SURFACE, Ordering::SeqCst);
                    SURFACE_HANDLE[index].store(0, Ordering::SeqCst);
                    SURFACE_BASE[index].store(0, Ordering::SeqCst);
                    SURFACE_LEN[index].store(0, Ordering::SeqCst);
                    SURFACE_OWNER[index].store(0, Ordering::SeqCst);
                    Ok(())
                }
                Err(_) => Err(EACCES),
            };
        }
    }
    Err(EACCES)
}

/// Claims the display exclusively. Refused while a server supervises it --
/// a server already answers for every client, so a second, blunter claim
/// would be a race with the thing it is meant to protect.
pub fn acquire(session: u64) -> Result<(), i64> {
    if session == NO_OWNER {
        return Err(EACCES);
    }
    let mut current = OWNER.load(Ordering::SeqCst);
    loop {
        if current == NO_OWNER {
            if server() != NO_SERVER {
                return Err(EBUSY);
            }
            match OWNER.compare_exchange(current, session, Ordering::SeqCst, Ordering::SeqCst) {
                Ok(_) => return Ok(()),
                Err(actual) => {
                    current = actual;
                    continue;
                }
            }
        }
        if current == session {
            return Ok(());
        }
        return Err(EBUSY);
    }
}

/// Gives exclusive ownership back. Only the owner may.
pub fn release(session: u64) -> Result<(), i64> {
    match OWNER.compare_exchange(session, NO_OWNER, Ordering::SeqCst, Ordering::SeqCst) {
        Ok(_) => Ok(()),
        Err(_) => Err(EACCES),
    }
}

/// Stops the server `session`. Any client slots it supervised go with it:
/// the policy that gave them meaning is gone. Only the server may.
pub fn stop(session: u64) -> Result<(), i64> {
    match SERVER.compare_exchange(session, NO_SERVER, Ordering::SeqCst, Ordering::SeqCst) {
        Ok(_) => {
            POLICY.store(0, Ordering::SeqCst);
            for (index, slot) in CLIENTS.iter().enumerate() {
                let word = slot.load(Ordering::SeqCst);
                if word != 0 {
                    let _ = slot.compare_exchange(word, 0, Ordering::SeqCst, Ordering::SeqCst);
                }
                // A client's tile is NOT dropped here. This runs in the
                // SERVER's context, and a window can only be freed from the
                // space that maps it, so the record is PARKED under its
                // owner's session: that session gives it back (0x6c) or its
                // own death does, in its own address space.
                if SURFACE_REGION[index].load(Ordering::SeqCst) != NO_SURFACE {
                    SURFACE_OWNER[index].store((word >> 32) & 0xffff_ffff, Ordering::SeqCst);
                }
            }
            Ok(())
        }
        Err(_) => Err(EACCES),
    }
}

/// The withdrawal half: called from the one place a gate session dies.
///
/// A client's death frees ITS slot and leaves the server supervising
/// whoever is left -- that is what "the server outlives its clients"
/// means in this kernel. A server's death dissolves the server and
/// detaches every client, because an orphaned client table would refuse
/// every future session for the rest of the boot.
/// Returns the tile the DYING session still held, if any, so the caller can
/// free its window in this context -- the only context that can, since the
/// pages are mapped in this task's space. A server's clients' tiles are NOT
/// returned here: they belong to their own sessions, stay parked, and are
/// given back by those sessions. A tile that is dropped instead of returned
/// would leak its window; a tile freed from the wrong space would not free
/// anything at all.
pub fn release_on_withdrawal(session: u64) -> Option<SurfaceRecord> {
    // The session's own tile first, while its slot (or its parked record) is
    // still there to name it.
    let own = surface_take(session).ok();
    if server() == session {
        let _ = stop(session);
    } else {
        let _ = detach(session);
        let _ = release(session);
    }
    own
}
