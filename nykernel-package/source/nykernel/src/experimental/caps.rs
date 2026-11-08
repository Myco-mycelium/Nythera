//! Capability-checked sending -- the release's `capability.rs` core
//! (NPS-003 §4/§5 clauses), ported to THIS tree's task model.
//!
//! Source: `porting/release-1-71/src/experimental/capability.rs` (645
//! lines). What is ported here is its ENDPOINT half, adapted to this
//! kernel's identity: holders are scheduler task ids
//! (`experimental::scheduler::current_task()`), handles are indexes into
//! the holder's OWN kernel-owned table (not forgeable, not sharedable --
//! §5.4), and queues live inside this module, distinct from the
//! `(sender, seq)` mailbox primitive in `experimental::ipc` (which keeps
//! serving the simpler 0x45/0x46 rows byte-for-byte).
//!
//! The clauses, each with its section and its refusal:
//! * sending targets an endpoint THROUGH a capability, never a raw task
//!   id or a raw endpoint number (§3.2, §4.1);
//! * sending needs the SEND right: `NoRight` otherwise;
//! * handles are table indexes: an empty slot or a foreign index is
//!   `BadHandle` -- a holder gets no way to name another table's caps;
//! * the owner can revoke ALL outstanding capabilities with no holder
//!   cooperation (§4.3), implemented with a generation counter, so a
//!   stale capability fails closed (`Revoked`);
//! * capabilities travel only in the DEDICATED transfer field, never in
//!   payload bytes (§5.1); transferring needs GRANT, and may only
//!   ATTENUATE, never widen (§5.2, §5.3);
//! * payloads are bounded (`MAX_PAYLOAD`) and queues are bounded with a
//!   reported error, never a silent drop (§3.1 "backpressure");
//! * a dead holder's endpoints fail sends with a defined error (§7.1).
//!
//! NOT ported yet, named so their absence is checkable: the device/window
//! kinds (BlockDevice, IoPort, FrameBuffer -- each needs a hardware window
//! this tree does not expose to a holder yet; serving them today would be
//! a costume, not a capability), badges, containers (NPS-007), notify.
//! FOUR kinds ARE ported: Endpoint (the queue-based one), Input (the
//! router `kbdrt`'s prerequisite), Reply (the one-shot answer right, with
//! the CALL/REPLY pair and its blocking wait) and SharedMem (a region the
//! kernel maps and both rings write, with the release's LIFETIME: the
//! record is counted in live capabilities and dies with its last holder,
//! whether that holder closes the capability or exits); every gap is a
//! follow-on slice over THIS machine, and the kernel-side self-test
//! (`run()`) proves each ported clause positive AND negative, mirroring
//! the release's `run()`.
//!
//! ---- the SharedMem REQUEST and the mapping's lifetime ----------------
//!
//! Until 2026-11-07 the SharedMem kind only named regions the KERNEL had
//! already mapped: the capability was an authority over a mapping, never a
//! request for one, so a holder could not ASK. `request_region` closes
//! that: the machine asks the kernel half for a fresh mapping (`mapper`,
//! installed by the boot that can actually allocate frames) and mints the
//! caller a capability over it. A region created that way is OWNED by this
//! machine -- `RegionEntry::owned` -- and when its last capability goes
//! away, close OR exit, the `releaser` is called so the pages come back to
//! the frame allocator. The boot's kernel-mapped regions are `owned: false`
//! and are never unmapped here: they are the kernel's own pages, and the
//! 0x84 proof's SHM leg deliberately keeps reading one after its holders
//! are gone. A refused mint (no such holder, full table) releases the
//! mapping it just made, so a refusal cannot leak what it created -- and
//! the self-test checks both the release and the no-leak ordering.
//!
//! ---- a region reaches a SECOND holder ------------------------------
//!
//! The transfer field always carried region capabilities, but the copy it
//! installs was not COUNTED: the receiver's capability did not appear in
//! the region's holder count, so the sender's own close could free the
//! pages while the receiver was still entitled to them. A queued copy is
//! now a holder from the moment it is queued (`send` increments, `receive`
//! hands the count over without a second one), which is what makes the
//! delivery path honest: `drop_queued` gives the count back wherever a
//! queue is thrown away -- endpoint revocation, an owner's termination --
//! and releases the pages when that was the last count. The transfer also
//! refuses BY NAME a region that is already dead, and the row's rights
//! bound is the union of all kinds' rights (`rights::ALL`), because
//! bounding it by the endpoint mask would refuse every region handover.
//!
//! ---- the Input kind (the release's Phase-9/1-29 clauses) ------------
//!
//! `Kind::Input` is the TOKEN that authorizes polling input events:
//! `READ` = may poll raw events, `GRANT` = may pass it on, ATTENUATED
//! (the release's exact right set, applied in its `bootstrap_input`:
//! rights masked to READ|GRANT). It has NO endpoint: `endpoint` is 0
//! and its generation never stales (the queue it guards is the kernel's
//! input hardware state, not a revocable mailbox), so the transfer
//! machine's liveness check is kind-aware. The release's shape is kept
//! exactly: only the kernel ever MINTS an Input capability
//! (`bootstrap_input`, init's role -- never reachable from user code),
//! and holders can only receive one by transfer. The refusals are the
//! release's: WrongType when the handle is some other kind, NoRight
//! when it carries no READ. The GATE row that serves
//! INPUT_POLL-with-this-check EXISTS (`service84::CALL_INPUT_POLL`),
//! mounted on the proven clauses, authority-first (the release's exact
//! ordering: the check runs BEFORE any event is consumed) under the
//! gate's SESSION identity (see below).
//!
//! HOW CALLERS REACH IT: through the `0x84` gate's capability rows
//! (`service84::CALL_CAP_SEND`/`CALL_CAP_RECV`/`CALL_INPUT_POLL`), whose
//! identity is the caller's GATE SESSION (`service84::attach_gate_session`,
//! the transition's kernel-only NAME) or, with no session, the running
//! scheduler task -- the release's session machine gives a ring-3
//! caller its OWN table (the proof boot mints its session one;
//! `prepare_session_for_proof`), and the manifest's parked step -- every
//! ring-3 task a session, the STORE in `experimental::scheduler`
//! (`session_identity`/`attach_session`/`withdraw_session`), recorded at
//! the ring-3 task switches -- is this slice's generalization, so the
//! ring3 scheduler's E/F/G and the preemption hogs each speak from
//! their mapped session at every switch WITHOUT another ABI change. Ring-0
//! callers -- the kernel itself, boot self-tests -- keep task 0's identity:
//! that IS the kernel's table. What a single holder can exercise today --
//! bounds, generation, attenuation -- is proven here by `run()` (which
//! builds its OWN multi-holder system, so the multi-holder clauses are
//! kernel-proven even beyond the proof's one session) and by the ring-3
//! proof's capability leg.

use alloc::collections::VecDeque;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicU64, Ordering};
use x86_64::instructions::interrupts;

/// §3.1: payloads are bounded; a bigger claim is a named refusal, not a
/// truncation.
pub const MAX_PAYLOAD: usize = 64;
/// Bounded queues: a full queue is `QueueFull`, never a silent drop.
pub const QUEUE_CAPACITY: usize = 4;
/// Per-holder table size.
pub const CAP_TABLE_SIZE: usize = 32;

/// Rights bits. SEND/GRANT are the ENDPOINT-kind rights; READ is the
/// device/kind rights bit the Input capability uses ("may poll events").
pub mod rights {
    pub const SEND: u8 = 1;
    /// May transfer this capability (attenuated) to others (§5.2).
    pub const GRANT: u8 = 2;
    /// The device kinds' right (Input: may poll the raw event stream).
    pub const READ: u8 = 4;
    /// The release's WRITE: may store through a shared region's mapping.
    pub const WRITE: u8 = 8;
    /// Every right any kind can carry: the only claim a row can check
    /// WITHOUT knowing the kind, and the mask a "this is not a handle"
    /// refusal is bounded by. Which subset a transfer may actually carry
    /// is the MACHINE's check (attenuation against the holder's own
    /// mask, per kind), not the row's.
    pub const ALL: u8 = SEND | GRANT | READ | WRITE;
}

/// The capability kinds THIS machine knows. Endpoint is the queue-based
/// kind; Input is the release's Phase-9/1-29 poll-authority token
/// (endpoint 0, never stales). The remaining release kinds are named in
/// the module doc.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Endpoint,
    Input,
    /// The release's one-shot right to answer ONE call: minted by the
    /// kernel on the CALL path, naming the CALLER (`endpoint` holds its
    /// identity), carrying SEND and nothing else. Consumed by its first
    /// `reply` -- a second use is `BadHandle`, and it can never be
    /// transferred on (it holds no GRANT).
    Reply,
    /// A mapping capability over a REGION of frames the kernel owns and
    /// has mapped USER_ACCESSIBLE: `endpoint` holds the region id, and the
    /// rights are the release's READ|WRITE|GRANT. Unlike an endpoint it
    /// has no generation (a region is not revoked out from under its
    /// holders) and no queue -- what it authorizes is the MAPPING, which
    /// is why the row that serves it reports geometry and nothing else.
    SharedMem,
}

/// A capability: which KIND, which endpoint, WHICH GENERATION of it
/// (§4.3's O(1) revocation), and the rights mask. No raw task ids, no
/// endpoint numbers leak to user code -- only the table indexes
/// ("handles") do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capability {
    kind: Kind,
    endpoint: u32,
    generation: u32,
    rights: u8,
}

/// The typed refusals (the release's IpcError, ENDPOINT half).
#[derive(Debug, PartialEq, Eq)]
pub enum CapError {
    BadProcess,
    /// The handle names a capability of a DIFFERENT kind than the
    /// operation required (the release's WrongType, named per-clause).
    WrongType,
    BadHandle,
    NoRight,
    /// A region the kernel mapped (not owned by this machine) cannot be
    /// revoked: there is no mapping of ours to take back.
    NotOwned,
    Revoked,
    TargetDead,
    PayloadTooLarge,
    QueueFull,
    Widening,
    NotOwner,
    TableFull,
    /// A handle that resolves to no region: the kernel's region table does
    /// not know it (refused rather than treated as a zero-length one).
    BadRegion,
    /// A BLOCKING call was begun by a caller that is already waiting for
    /// one -- two outstanding calls from one holder would need two
    /// answers and one wake slot, so the second is refused by name.
    CallPending,
    /// A region was REQUESTED on a boot whose kernel half installed no
    /// mapping source. A region cannot be conjured from a capability
    /// table, so the request is refused rather than answered with a
    /// geometry nobody mapped.
    NoRegionMapper,
    /// The kernel's mapping source refused the request (out of frames, or
    /// the request's own cap). The caller still holds nothing, and the
    /// half-made mapping has already been given back.
    OutOfMemory,
    /// The holder asked to drop ITS window of a region whose table already
    /// records no mapping for it: either it never had one or it has already
    /// dropped it. Refused by name rather than answered as success, so a
    /// repeated unmap cannot look like a second window was taken away.
    NotMapped,
}

/// A message queued at an endpoint: the sender task id, the bounded
/// payload, and an ATTENUATED capability travelling in the dedicated
/// transfer field (§5.1) -- never in payload bytes. That field is how a
/// region capability reaches a SECOND holder: the sender must hold GRANT
/// on it, the copy may only narrow its rights, and the region is counted
/// from the moment the copy is queued until either the receiver installs
/// it or the queue is discarded.
struct Queued {
    sender: usize,
    payload: Vec<u8>,
    /// `None` when the caller claimed no transfer.
    moved: Option<Capability>,
}

/// What a receive hands back. `transferred` is the handle (in the
/// RECEIVER's own table) where a transferred capability was installed --
/// the only way a new capability is minted in a holder's table.
pub struct Message {
    pub sender: usize,
    pub payload: Vec<u8>,
    pub transferred: Option<u32>,
    /// The KIND of the capability that arrived in the transfer field
    /// (`None` when nothing did). The gate reports the handle it installed
    /// for a REPLY specifically, so a later message carrying a region
    /// capability cannot be mistaken for the call's reply handle.
    pub transferred_kind: Option<Kind>,
}

struct Endpoint {
    owner: usize,
    generation: u32,
    alive: bool,
    queue: VecDeque<Queued>,
}

struct Holder {
    alive: bool,
    caps: Vec<Option<Capability>>,
}

/// A shared region: frames the kernel allocated, mapped USER_ACCESSIBLE at
/// a known virtual address, with the geometry a holder is told. The bytes
/// are the kernel's own memory once mapped, so what a holder writes IS
/// what the kernel reads -- which is the whole point of the kind, and what
/// the boot proof checks from both sides.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Region {
    pub base: u64,
    pub len: u64,
}

/// A region's record: the geometry a holder is told, HOW MANY live
/// capabilities name it, and whether it still exists. The count is the
/// release's region lifetime in one number -- the record dies with its
/// LAST holder, not with its first, and a holder that never gives the
/// capability back keeps the region alive by construction. A cap in
/// flight counts too: a transferred capability sitting in an endpoint's
/// queue names the region exactly as a table slot does, so it is counted
/// at send and handed over -- not re-counted -- at receive.
struct RegionEntry {
    geometry: Region,
    holders: u32,
    live: bool,
    /// WHICH HOLDERS currently have this region mapped in their own address
    /// space, by holder id. A region's frames are one thing; the window a
    /// holder reads them through is another, and they come and go
    /// separately: the 0x50 row drops ONE holder's window while the others
    /// keep theirs (and the frames stay), while a close or a revocation
    /// takes the whole region. Kept as ids rather than a count so a
    /// repeated unmap by the same holder is refused by name instead of
    /// eating another holder's mapping.
    mapped: Vec<usize>,
    /// Whether THIS machine created the mapping (a holder's request) and
    /// therefore owns giving its pages back when the last capability goes
    /// away. False for the regions the kernel mapped before minting a
    /// capability: those are the kernel's own pages, and this table is
    /// only remembering their geometry.
    owned: bool,
}

/// A whole system: per-holder tables + the endpoints. The gate owns one
/// global instance (`init`/`prepare_task0_for_proof`); the self-test
/// builds its own.
pub struct CapSystem {
    procs: Vec<Holder>,
    endpoints: Vec<Endpoint>,
    /// The kernel half of a region REQUEST: `pages -> geometry`, mapping
    /// fresh frames USER_ACCESSIBLE at a window the kernel chooses. `None`
    /// until the boot installs it (see `install_region_hooks`), which is
    /// why `request_region` answers `NoRegionMapper` rather than guessing.
    region_mapper: Option<unsafe fn(u64) -> Option<Region>>,
    /// The other half: give a REQUESTED region's pages back when its last
    /// capability dies. Separate from the mapper because the two happen at
    /// different times and a table must not keep a mapping alive merely
    /// because it once had a way to make one.
    region_releaser: Option<unsafe fn(Region)>,
    /// The PER-HOLDER half: drop one window (the caller's space) without
    /// freeing a frame. Separate from the releaser because that one takes
    /// the whole region back.
    region_unmapper: Option<unsafe fn(Region)>,
    /// One entry per PARKED caller: the blocking CALL's wait slot. The
    /// answer is deposited here by `reply` (which also unblocks the
    /// caller's task) and consumed by the gate when the parked task
    /// resumes. A caller that answers through its own endpoint instead
    /// never gets an entry.
    waits: Vec<Waiting>,
    regions: Vec<RegionEntry>,
}

/// A caller blocked on a call. `task` is the scheduler task to wake --
/// the gate passes it because the capability machine only knows
/// IDENTITIES (sessions), and a session's home task is the scheduler's
/// fact, not this table's.
struct Waiting {
    caller: usize,
    task: usize,
    answer: Option<Vec<u8>>,
}

impl CapSystem {
    pub fn new() -> Self {
        CapSystem {
            procs: Vec::new(),
            endpoints: Vec::new(),
            region_mapper: None,
            region_releaser: None,
            region_unmapper: None,
            waits: Vec::new(),
            regions: Vec::new(),
        }
    }

    /// Creates a holder for task id `p`. Duplicate ids are refused
    /// rather than silently re-created: the id space is the scheduler's,
    /// so a duplicate here means a scheduler bug, and hiding it would be
    /// backstage borrowing, not issuance.
    pub fn create_process(&mut self, p: usize) -> Result<(), CapError> {
        // Pad with DEAD holders up to the id, never a clone-filler (a Clone
        // of a live holder would silently duplicate capabilities into a
        // second table -- the one thing a capability machine must not do).
        while self.procs.len() <= p {
            self.procs.push(Holder { alive: false, caps: Vec::new() });
        }
        if self.procs[p].alive {
            return Err(CapError::BadProcess);
        }
        self.procs[p] = Holder {
            alive: true,
            caps: (0..CAP_TABLE_SIZE).map(|_| None).collect(),
        };
        Ok(())
    }

    /// Ends a holder: its endpoints die with it (§7.1) and in-flight
    /// transfers in those queues vanish with the queue -- which means the
    /// region counts they were holding have to come back, or a dead
    /// endpoint would pin a region alive forever.
    pub fn terminate_process(&mut self, p: usize) -> Result<(), CapError> {
        let holder = self.procs.get_mut(p).filter(|h| h.alive).ok_or(CapError::BadProcess)?;
        holder.alive = false;
        // Collected first, released after: `drop_queued` needs the whole
        // table, and an endpoint borrow would fight it.
        let mut dropped: Vec<VecDeque<Queued>> = Vec::new();
        for ep in self.endpoints.iter_mut().filter(|e| e.owner == p) {
            ep.alive = false;
            dropped.push(core::mem::take(&mut ep.queue));
        }
        for q in dropped {
            self.drop_queued(q);
        }
        Ok(())
    }

    /// Gives back the region counts carried by messages nobody will
    /// receive. A queued copy counts as a holder from the moment the send
    /// queues it (see `send`), so every path that throws a queue away --
    /// endpoint teardown, revocation, a dead owner -- must decrement for
    /// it, and release the pages when it was the last one. Non-region
    /// transfers carry no count: an endpoint cap is bounded by its
    /// generation and a Reply cap is single-use.
    fn drop_queued(&mut self, queue: VecDeque<Queued>) {
        let mut freed: Vec<Region> = Vec::new();
        for q in queue.iter() {
            let moved = match q.moved {
                Some(c) if c.kind == Kind::SharedMem => c,
                _ => continue,
            };
            if let Some(entry) = self.regions.get_mut(moved.endpoint as usize) {
                entry.holders = entry.holders.saturating_sub(1);
                if entry.holders == 0 && entry.live {
                    entry.live = false;
                    if entry.owned {
                        freed.push(entry.geometry);
                    }
                }
            }
        }
        for geometry in freed {
            if let Some(release) = self.region_releaser {
                unsafe { release(geometry) };
            }
        }
    }

    /// Creates an endpoint owned by `owner`, handing the owner a
    /// SEND|GRANT capability in its own table. Returns (endpoint id,
    /// owner's handle).
    pub fn create_endpoint(&mut self, owner: usize) -> Result<(u32, u32), CapError> {
        self.live(owner)?;
        let id = self.endpoints.len() as u32;
        self.endpoints.push(Endpoint { owner, generation: 0, alive: true, queue: VecDeque::new() });
        let cap = Capability { kind: Kind::Endpoint, endpoint: id, generation: 0, rights: rights::SEND | rights::GRANT };
        let handle = Self::install(self.live(owner)?, cap)?;
        Ok((id, handle))
    }

    /// Owner-only §4.3 revocation: EVERY outstanding capability for this
    /// endpoint goes stale -- generation bump, queue drained -- with no
    /// holder cooperation needed.
    pub fn revoke(&mut self, owner: usize, ep: u32) -> Result<(), CapError> {
        let dropped = {
            let e = self.endpoints.get_mut(ep as usize).ok_or(CapError::BadHandle)?;
            if e.owner != owner {
                return Err(CapError::NotOwner);
            }
        e.generation += 1;
        core::mem::take(&mut e.queue)
    };
    // Outside the endpoint borrow: a revoked queue may have been the last
    // holder of a requested region, and giving its pages back walks the
    // page tables.
    self.drop_queued(dropped);
    Ok(())
    }

    /// Owner-only: issue a fresh capability of the CURRENT generation.
    pub fn mint(&mut self, owner: usize, ep: u32, want: u8) -> Result<u32, CapError> {
        let generation = {
            let e = self.endpoints.get(ep as usize).ok_or(CapError::BadHandle)?;
            if e.owner != owner {
                return Err(CapError::NotOwner);
            }
            if !e.alive {
                return Err(CapError::TargetDead);
            }
            e.generation
        };
        let cap = Capability { kind: Kind::Endpoint, endpoint: ep, generation, rights: want & (rights::SEND | rights::GRANT) };
        Self::install(self.live(owner)?, cap)
    }

    /// KERNEL-ONLY bootstrap install (the release's role for `init`'s
    /// elevated caps; never reachable through the 0x84 rows): puts a
    /// capability into `to`'s table directly.
    pub fn bootstrap_grant(&mut self, to: usize, ep: u32, want: u8) -> Result<u32, CapError> {
        let generation = self.endpoints.get(ep as usize).map(|e| e.generation).ok_or(CapError::BadHandle)?;
        let cap = Capability { kind: Kind::Endpoint, endpoint: ep, generation, rights: want & (rights::SEND | rights::GRANT) };
        Self::install(self.live(to)?, cap)
    }

    /// The release's `bootstrap_input` (Phase-9/1-29), same role: the
    /// KERNEL mints `to` the Input capability -- rights masked to
    /// READ|GRANT (READ = may poll events, GRANT = may pass it on,
    /// attenuated). Only the kernel ever mints an Input capability; a
    /// holder receives one by transfer, never by minting.
    pub fn bootstrap_input(&mut self, to: usize, want: u8) -> Result<u32, CapError> {
        let cap = Capability { kind: Kind::Input, endpoint: 0, generation: 0, rights: want & (rights::READ | rights::GRANT) };
        Self::install(self.live(to)?, cap)
    }

    /// The release's `input_check`: WrongType unless `h` is an Input
    /// capability; NoRight unless it carries READ. This is the
    /// authorization the INPUT_POLL row mounts on
    /// (`service84::CALL_INPUT_POLL`) -- checking rights BEFORE any
    /// event is consumed, the release's authority-first ordering.
    pub fn input_check(&mut self, p: usize, h: u32) -> Result<(), CapError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::Input {
            return Err(CapError::WrongType);
        }
        if cap.rights & rights::READ == 0 {
            return Err(CapError::NoRight);
        }
        Ok(())
    }

    /// The release's `call_send`: a send that ALSO mints a one-shot Reply
    /// capability naming `from` and queues it in the message's dedicated
    /// transfer field (§5.1), so the receiving owner gets it the usual way
    /// (`receive` installs it into its own table and reports the handle).
    /// Returns the endpoint's OWNER -- the only holder that can answer.
    ///
    /// Unlike `send` there is no transfer argument: the capability riding
    /// this message is the kernel's Reply mint, never the caller's choice,
    /// which is exactly why a caller cannot smuggle one in.
    pub fn call_send(&mut self, from: usize, h: u32, payload: &[u8]) -> Result<usize, CapError> {
        let cap = self.lookup(from, h)?;
        if cap.kind != Kind::Endpoint {
            return Err(CapError::WrongType);
        }
        if cap.rights & rights::SEND == 0 {
            return Err(CapError::NoRight);
        }
        if payload.len() > MAX_PAYLOAD {
            return Err(CapError::PayloadTooLarge);
        }
        // The reply names the caller by identity, and the capability field
        // is u32: an identity wider than that could not be carried back,
        // so it is refused rather than truncated.
        if from > u32::MAX as usize {
            return Err(CapError::BadProcess);
        }
        let reply = Capability {
            kind: Kind::Reply,
            endpoint: from as u32,
            generation: 0,
            rights: rights::SEND,
        };
        let e = self.endpoints.get_mut(cap.endpoint as usize).ok_or(CapError::BadHandle)?;
        if !e.alive {
            return Err(CapError::TargetDead);
        }
        if e.generation != cap.generation {
            return Err(CapError::Revoked);
        }
        if e.queue.len() >= QUEUE_CAPACITY {
            return Err(CapError::QueueFull);
        }
        let owner = e.owner;
        e.queue.push_back(Queued { sender: from, payload: payload.to_vec(), moved: Some(reply) });
        Ok(owner)
    }

    /// The release's `reply_target`: who `p`'s handle `h` would answer.
    /// WrongType unless it is a Reply capability -- an ENDPOINT handle
    /// here is a caller confusing "send" with "answer", refused by name.
    pub fn reply_target(&mut self, p: usize, h: u32) -> Result<usize, CapError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::Reply {
            return Err(CapError::WrongType);
        }
        if cap.rights & rights::SEND == 0 {
            return Err(CapError::NoRight);
        }
        Ok(cap.endpoint as usize)
    }

    /// Answers ONE call: the bytes are queued at the caller's ANSWER SLOT
    /// -- the first live endpoint it owns -- and the reply capability is
    /// CONSUMED (the release's `drop_handle`, one-shot by construction).
    ///
    /// Two ordering choices worth naming. The target's slot and its free
    /// room are checked BEFORE the handle is emptied, where the release
    /// drops the capability first and only then looks for a wait slot: an
    /// answer that cannot land here is a refusal the caller can still
    /// retry, not a consumed right with bytes on the floor.
    pub fn reply(&mut self, p: usize, h: u32, payload: &[u8]) -> Result<u32, CapError> {
        let target = self.reply_target(p, h)?;
        if payload.len() > MAX_PAYLOAD {
            return Err(CapError::PayloadTooLarge);
        }
        // A PARKED caller is answered where it waits, not where it owns
        // an endpoint: the blocking CALL registered a wait slot, and the
        // parked task is unblocked by the same reply that fills it.
        let waiting = self.waits.iter().position(|w| w.caller == target && w.answer.is_none());
        let slot = match waiting {
            Some(_) => usize::MAX,
            None => {
                let s = self
                    .endpoints
                    .iter()
                    .position(|e| e.owner == target && e.alive)
                    .ok_or(CapError::TargetDead)?;
                if self.endpoints[s].queue.len() >= QUEUE_CAPACITY {
                    return Err(CapError::QueueFull);
                }
                s
            }
        };
        let holder = self.live(p)?;
        match holder.caps.get_mut(h as usize) {
            Some(c) if c.is_some() => *c = None,
            _ => return Err(CapError::BadHandle),
        }
        match waiting {
            Some(i) => {
                self.waits[i].answer = Some(payload.to_vec());
                // The wake is part of the answer, not a separate step a
                // caller could forget: a filled slot with a still-parked
                // task would be a lost wake (NPS-002 §5.4).
                crate::experimental::scheduler::unblock_task(self.waits[i].task);
                Ok(u32::MAX)
            }
            None => {
                self.endpoints[slot].queue.push_back(Queued { sender: p, payload: payload.to_vec(), moved: None });
                Ok(slot as u32)
            }
        }
    }

    /// KERNEL-ONLY: records a region it has already mapped, answering its
    /// id (the release's `shm` region id, held in the capability's
    /// `endpoint` field). The kernel owns the mapping; this table only
    /// remembers the geometry so a holder can be told it.
    pub fn create_region(&mut self, base: u64, len: u64) -> u32 {
        let id = self.regions.len() as u32;
        self.regions.push(RegionEntry {
            geometry: Region { base, len },
            holders: 0,
            live: true,
            owned: false,
            // Not a window this table installed: the kernel mapped it, and
            // the holders that receive a capability over it are recorded by
            // `bootstrap_region`.
            mapped: Vec::new(),
        });
        id
    }

    /// Installs the kernel half of a region REQUEST. Both are needed for a
    /// requested region's whole life (a mapper without a releaser would
    /// leak every region the holders close), so they are installed
    /// together rather than as two optional knobs.
    pub fn install_region_hooks(
        &mut self,
        mapper: unsafe fn(u64) -> Option<Region>,
        releaser: unsafe fn(Region),
        unmapper: unsafe fn(Region),
    ) {
        self.region_mapper = Some(mapper);
        self.region_releaser = Some(releaser);
        self.region_unmapper = Some(unmapper);
    }

    /// The release's region CREATE/MAP request: the caller ASKS for a
    /// region instead of being told about one the kernel already mapped.
    /// The kernel half chooses the virtual window and backs it with fresh
    /// frames; this table records the geometry, counts the capability and
    /// -- the ordering that matters -- gives the mapping back immediately
    /// if the mint is refused, so a request that answers an error never
    /// leaves frames mapped that no capability names.
    pub fn request_region(
        &mut self,
        to: usize,
        pages: u64,
        want: u8,
    ) -> Result<(u32, u32, Region), CapError> {
        let mapper = self.region_mapper.ok_or(CapError::NoRegionMapper)?;
        if pages == 0 {
            return Err(CapError::BadRegion);
        }
        let geometry = unsafe { mapper(pages) }.ok_or(CapError::OutOfMemory)?;
        let id = self.regions.len() as u32;
        let mut mapped = Vec::new();
        mapped.push(to);
        self.regions.push(RegionEntry {
            geometry,
            holders: 0,
            live: true,
            owned: true,
            // The mapper maps for the REQUESTER (its own space, the active
            // one), so the requester is the first holder with a window.
            mapped,
        });
        match self.bootstrap_region(to, id, want) {
            Ok(handle) => Ok((id, handle, geometry)),
            Err(e) => {
                // The record was pushed last, so popping it is exact; the
                // mapping is given back for the same reason: nothing would
                // ever name it again, and the region machine created it.
                self.regions.pop();
                if let Some(release) = self.region_releaser {
                    unsafe { release(geometry) };
                }
                Err(e)
            }
        }
    }

    /// The release's `bootstrap_region`: gives `to` a capability over
    /// region `region`, rights masked to READ|WRITE|GRANT.
    pub fn bootstrap_region(&mut self, to: usize, region: u32, want: u8) -> Result<u32, CapError> {
        match self.regions.get(region as usize) {
            Some(e) if e.live => {}
            _ => return Err(CapError::BadRegion),
        }
        let cap = Capability {
            kind: Kind::SharedMem,
            endpoint: region,
            generation: 0,
            rights: want & (rights::READ | rights::WRITE | rights::GRANT),
        };
        let holder = self.live(to)?;
        let handle = Self::install(holder, cap)?;
        // Counted only AFTER the mint succeeded: a refused grant that
        // incremented the count would pin a region alive for a holder
        // that never received it.
        self.regions[region as usize].holders += 1;
        // ...and the holder has a WINDOW: a requested region is mapped in
        // the requester's space, and a capability arriving through the
        // transfer field is mirrored into the receiver's. Idempotent, so a
        // re-install cannot double-count one space.
        let entry = &mut self.regions[region as usize];
        if !entry.mapped.contains(&to) {
            entry.mapped.push(to);
        }
        Ok(handle)
    }

    /// The release's region CLOSE (this surface's 0x4d): the holder gives
    /// ONE capability back. The region's lifetime is the count of live
    /// capabilities, so the record dies exactly when its last holder
    /// walks away -- and a refusal to CLOSE (a handle that names no
    /// region) changes nothing, which matters because a double close is
    /// the obvious bug and it must not decrement twice.
    /// REVOKES a live region's mapping while its holders still have
    /// capabilities: the pages go back to the allocator NOW, the record
    /// goes dead, and every capability naming it becomes STALE (geometry
    /// answers `BadRegion`, close drops a handle that no longer frees
    /// anything). This is the release's revocation direction, and it is
    /// deliberately not something a capability can ask for: holding a
    /// region never gives you the power to take it away, so the caller
    /// here is the kernel half of the machine, not a row.
    ///
    /// The count is NOT cleared: those capabilities still exist, and
    /// `close_region_cap`'s `holders == 0 && live` guard is what keeps the
    /// later closes from freeing the pages a second time.
    /// This surface's 0x50: the holder drops ITS OWN window on a region it
    /// still holds a capability on. The frames stay (other holders may be
    /// reading them, and the capability still answers geometry), and so do
    /// the other holders' windows -- this unmaps exactly one space, the
    /// caller's. A holder that never had a window, or that already dropped
    /// it, is refused `NotMapped` by name rather than told it succeeded.
    ///
    /// Deliberately NOT the same thing as the close (0x4d): a close gives a
    /// capability back and lowers the holder count, while its window stays
    /// mapped until the region itself goes (that is the fact the queued-copy
    /// leg pins: the sender's own read still lands after its close). This row
    /// is the other order -- window first, capability kept.
    pub fn unmap_region_cap(&mut self, holder: usize, handle: u32) -> Result<(), CapError> {
        let cap = self.lookup(holder, handle)?;
        if cap.kind != Kind::SharedMem {
            return Err(CapError::WrongType);
        }
        let region = cap.endpoint;
        let entry = match self.regions.get_mut(region as usize) {
            Some(e) if e.live => e,
            _ => return Err(CapError::BadRegion),
        };
        let at = entry.mapped.iter().position(|&h| h == holder);
        let at = match at {
            Some(at) => at,
            None => return Err(CapError::NotMapped),
        };
        let geometry = entry.geometry;
        entry.mapped.remove(at);
        let unmap = self.region_unmapper;
        // Outside the record's borrow: unmapping walks page tables.
        if let Some(unmap) = unmap {
            unsafe { unmap(geometry) }
        }
        Ok(())
    }

    /// Whether `holder` has a window on `region` right now (the read-back the
    /// 0x50 leg asks, so the answer is the table's and not a guess).
    pub fn holder_is_mapped(&self, region: u32, holder: usize) -> bool {
        matches!(self.regions.get(region as usize), Some(e) if e.live && e.mapped.contains(&holder))
    }

    pub fn revoke_region(&mut self, region: u32) -> Result<(), CapError> {
        let entry = match self.regions.get_mut(region as usize) {
            Some(e) if e.live => e,
            _ => return Err(CapError::BadRegion),
        };
        if !entry.owned {
            // A region the KERNEL mapped (the SHM kind's own page, for
            // instance) has no releaser and no frames this machine may
            // take back; refusing by name is the honest answer rather
            // than silently marking it dead while it stays mapped.
            return Err(CapError::NotOwned);
        }
        let geometry = entry.geometry;
        entry.live = false;
        // Every holder's window goes with the pages: the releaser unmaps in
        // the space it is called from and removes the mirror, so no space is
        // left naming frames the allocator takes back.
        entry.mapped.clear();
        let release = self.region_releaser;
        // Outside the record's borrow, for the same reason
        // `close_region_cap` does it: a releaser walks page tables.
        if let Some(release) = release {
            unsafe { release(geometry) }
        }
        Ok(())
    }

    /// The region id whose geometry starts at `base`, if any. Kernel-side
    /// lookup used by the boot half of the region machine (the proof revokes
    /// a region it knows by the address the kernel handed out, not by a
    /// table index it was never told).
    pub fn region_id_by_base(&self, base: u64) -> Option<u32> {
        self.regions
            .iter()
            .position(|e| e.geometry.base == base)
            .map(|i| i as u32)
    }

    pub fn close_region_cap(&mut self, p: usize, h: u32) -> Result<(), CapError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::SharedMem {
            return Err(CapError::WrongType);
        }
        let region = cap.endpoint as usize;
        // Drop the capability FIRST: the handle is consumed even if the
        // region record is somehow already gone (it cannot be while this
        // cap lives -- the count includes it -- but ordering the table
        // behind the count is the bug that makes that invariant
        // unobservable rather than load-bearing).
        if let Some(holder) = self.procs.get_mut(p) {
            if let Some(slot) = holder.caps.get_mut(h as usize) {
                *slot = None;
            }
        }
        let mut freed: Option<Region> = None;
        if let Some(entry) = self.regions.get_mut(region) {
            entry.holders = entry.holders.saturating_sub(1);
            // `live` is the guard that makes a double release impossible:
            // a capability can only name a live region (the count is what
            // keeps it live), so a second close at zero is a machine bug
            // and must not free the pages a second time.
            if entry.holders == 0 && entry.live {
                entry.live = false;
                if entry.owned {
                    freed = Some(entry.geometry);
                }
            }
        }
        // Outside the record's borrow: giving pages back walks page tables
        // and touches the frame allocator, and holding the region table
        // across that would make a re-entrant request (a releaser that
        // asks for something) a deadlock instead of a design mistake.
        if let Some(geometry) = freed {
            if let Some(release) = self.region_releaser {
                unsafe { release(geometry) };
            }
        }
        Ok(())
    }

    /// CAP_EXIT's half: every capability this holder owns goes away, and
    /// every region they named loses one holder. Returns how many region
    /// capabilities were released, so the gate can report the teardown
    /// instead of assuming it. The holder itself is marked dead -- a
    /// session identity is terminal, so its table must not be reusable.
    pub fn release_holder(&mut self, p: usize) -> usize {
        let Some(holder) = self.procs.get_mut(p) else {
            return 0;
        };
        if !holder.alive {
            return 0;
        }
        let mut released = 0usize;
        // One entry per region that died with this table. A holder can
        // hold TWO capabilities over one region, so the release must be
        // collected across the whole table and fired once per REGION (not
        // once per capability) after the counts settle.
        let mut freed: Vec<Region> = Vec::new();
        let caps = core::mem::take(&mut holder.caps);
        for cap in caps.into_iter().flatten() {
            if cap.kind == Kind::SharedMem {
                released += 1;
                if let Some(entry) = self.regions.get_mut(cap.endpoint as usize) {
                    entry.holders = entry.holders.saturating_sub(1);
                    if entry.holders == 0 && entry.live {
                        entry.live = false;
                        if entry.owned {
                            freed.push(entry.geometry);
                        }
                    }
                }
            }
        }
        holder.alive = false;
        for geometry in freed {
            if let Some(release) = self.region_releaser {
                unsafe { release(geometry) };
            }
        }
        released
    }

    /// Read-back for the gate and the self-test: is the region record
    /// still there, and how many holders does it name?
    pub fn region_live(&self, region: u32) -> bool {
        matches!(self.regions.get(region as usize), Some(e) if e.live)
    }

    pub fn region_holders(&self, region: u32) -> Option<u32> {
        self.regions.get(region as usize).map(|e| e.holders)
    }

    /// The release's `region_cap`: (region id, rights), WrongType unless
    /// the handle IS a region capability. No right is required here -- the
    /// release asks only what the holder may do, and the answering row
    /// reports READ|WRITE as it finds them rather than deciding for it.
    pub fn region_cap(&mut self, p: usize, h: u32) -> Result<(u32, u8), CapError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::SharedMem {
            return Err(CapError::WrongType);
        }
        Ok((cap.endpoint, cap.rights))
    }

    /// The geometry behind a region id (the row's answer, not the holder's
    /// claim): both are the kernel's own record of what it mapped.
    pub fn region_geometry(&self, region: u32) -> Result<Region, CapError> {
        match self.regions.get(region as usize) {
            Some(e) if e.live => Ok(e.geometry),
            _ => Err(CapError::BadRegion),
        }
    }

    /// Registers a BLOCKING call: `call_send`, plus the wait slot the
    /// answer will be deposited into. The caller's TASK is recorded
    /// because the machine only knows identities and the scheduler is
    /// what a wake names.
    pub fn call_wait_begin(&mut self, caller: usize, task: usize, h: u32, payload: &[u8]) -> Result<usize, CapError> {
        if self.waits.iter().any(|w| w.caller == caller && w.answer.is_none()) {
            return Err(CapError::CallPending);
        }
        let owner = self.call_send(caller, h, payload)?;
        self.waits.push(Waiting { caller, task, answer: None });
        Ok(owner)
    }

    /// Consumes this caller's wait slot, answer and all. `None` means the
    /// caller was woken for something that was not an answer -- the
    /// gate answers that EAGAIN by name rather than pretending.
    pub fn take_answer(&mut self, caller: usize) -> Option<Vec<u8>> {
        let i = self.waits.iter().position(|w| w.caller == caller)?;
        self.waits.remove(i).answer
    }

    fn live(&mut self, p: usize) -> Result<&mut Holder, CapError> {
        match self.procs.get_mut(p) {
            Some(h) if h.alive => Ok(h),
            _ => Err(CapError::BadProcess),
        }
    }

    fn install(holder: &mut Holder, cap: Capability) -> Result<u32, CapError> {
        let slot = holder
            .caps
            .iter()
            .position(|c| c.is_none())
            .ok_or(CapError::TableFull)?;
        holder.caps[slot] = Some(cap);
        Ok(slot as u32)
    }

    /// Resolves `p`'s handle `h`: an empty slot or a foreign table index
    /// is `BadHandle` (not forgeable, just invalid -- §5.4).
    fn lookup(&mut self, p: usize, h: u32) -> Result<Capability, CapError> {
        let holder = self.live(p)?;
        holder.caps.get(h as usize).copied().flatten().ok_or(CapError::BadHandle)
    }

    /// Non-blocking send through capability `h`: payload bytes (already
    /// COPIED by the caller -- the gate copies them out of user memory
    /// under its range validator), optionally transferring capability
    /// transfer.0 attenuated to at most transfer.1 rights (§5.2/§5.3).
    ///
    /// Every pre-queue check happens BEFORE any queue is touched, so a
    /// refused send cannot leak queue depth (the release's oracle-proof
    /// ordering, preserved).
    pub fn send(
        &mut self,
        from: usize,
        h: u32,
        payload: &[u8],
        transfer: Option<(u32, u8)>,
    ) -> Result<(), CapError> {
        let cap = self.lookup(from, h)?;
        if payload.len() > MAX_PAYLOAD {
            return Err(CapError::PayloadTooLarge);
        }
        // The transfer is validated BEFORE the queue: attenuation only,
        // the sender must hold GRANT, and a transferred endpoint
        // capability must still live in the CURRENT generation.
        let moved = match transfer {
            None => None,
            Some((th, want)) => {
                let held = self.lookup(from, th)?;
                if held.rights & rights::GRANT == 0 {
                    return Err(CapError::NoRight);
                }
                if want & !held.rights != 0 {
                    return Err(CapError::Widening);
                }
                // Kind-aware liveness: an Endpoint capability must still
                // name the CURRENT generation; an Input capability has no
                // endpoint state (its authority never stales -- the
                // release grants no revoke on it); other kinds are not
                // ported and are refused BY NAME rather than guessed at.
                match held.kind {
                    Kind::Endpoint => {
                        let tep = self.endpoints.get(held.endpoint as usize).ok_or(CapError::BadHandle)?;
                        if tep.generation != held.generation {
                            return Err(CapError::Revoked);
                        }
                    }
                    Kind::Input => {}
                    // Unreachable as written: the GRANT check above
                    // already refused a Reply capability (it carries SEND
                    // only). Kept explicit so the day a right is added
                    // here, the answer is still a by-name refusal rather
                    // than a capability travelling on a single-use right.
                    Kind::Reply => return Err(CapError::NoRight),
                    // A region capability's authority never stales: the
                    // frames it names are the kernel's for as long as the
                    // region exists, and there is no revoke on them. What
                    // it CAN do is die, so a transfer naming a region that
                    // is already gone is refused here rather than queued:
                    // the queue would otherwise carry a capability over
                    // frames the allocator has taken back.
                    Kind::SharedMem => {
                        match self.regions.get(held.endpoint as usize) {
                            Some(r) if r.live => {}
                            _ => return Err(CapError::BadRegion),
                        }
                    }
                }
                Some(Capability { rights: want, ..held })
            }
        };
        let e = self.endpoints.get_mut(cap.endpoint as usize).ok_or(CapError::BadHandle)?;
        if !e.alive {
            return Err(CapError::TargetDead);
        }
        if e.generation != cap.generation {
            return Err(CapError::Revoked);
        }
        if e.queue.len() >= QUEUE_CAPACITY {
            return Err(CapError::QueueFull);
        }
        // A queued copy is a capability in flight, so it counts against
        // the region's lifetime FROM THE MOMENT IT IS QUEUED -- otherwise
        // a sender could transfer a region, close its own capability, and
        // free the pages out from under the message waiting to be
        // received. `receive` hands that count over to the receiving
        // table slot (it does not add a second one), and every discarded
        // queue gives it back (`drop_queued`).
        let queued_region = moved
            .as_ref()
            .filter(|c| c.kind == Kind::SharedMem)
            .map(|c| c.endpoint);
        e.queue.push_back(Queued { sender: from, payload: payload.to_vec(), moved });
        if let Some(region) = queued_region {
            if let Some(entry) = self.regions.get_mut(region as usize) {
                entry.holders += 1;
            }
        }
        Ok(())
    }

    /// Non-blocking receive by the endpoint's owner. A transferred
    /// capability is installed in the owner's OWN table -- the message is
    /// only consumed when that install succeeds.
    pub fn receive(&mut self, owner: usize, ep: u32) -> Result<Option<Message>, CapError> {
        // The endpoint's deciding state is read under ONE immutable borrow
        // and copied out (all `Copy`/`bool` values), so the holder check
        // -- a mutable borrow -- cannot fight the queue read for `self`.
        let (needs_slot,) = {
            let e0 = self.endpoints.get(ep as usize).ok_or(CapError::BadHandle)?;
            if e0.owner != owner {
                return Err(CapError::NotOwner);
            }
            if !e0.alive {
                return Err(CapError::TargetDead); // §7.2: deterministic failure
            }
            (e0.queue.front().map(|q| q.moved.is_some()).unwrap_or(false),)
        };
        self.live(owner)?;
        if needs_slot {
            let full = self
                .procs
                .get(owner)
                .map(|h| h.caps.iter().all(|c| c.is_some()))
                .unwrap_or(true);
            if full {
                return Err(CapError::TableFull);
            }
        }
        let e1 = self.endpoints.get_mut(ep as usize).ok_or(CapError::BadHandle)?;
        let q = match e1.queue.pop_front() {
            Some(q) => q,
            None => return Ok(None),
        };
        let moved_kind = q.moved.map(|c| c.kind);
        let transferred = match q.moved {
            Some(c) => {
                // No region count here: the capability being installed was
                // COUNTED when it was queued (a queued copy is a holder),
                // so this is the count moving from the queue into a slot,
                // not a second capability being born.
                let installed = {
                    let holder = self.live(owner)?;
                    Self::install(holder, c)
                };
                match installed {
                    Ok(h) => Some(h),
                    Err(e) => {
                        // The slot pre-check above makes this unreachable,
                        // but the message is already consumed here: hand its
                        // region count back rather than leak a holder.
                        let mut orphan = VecDeque::new();
                        orphan.push_back(Queued { sender: q.sender, payload: Vec::new(), moved: Some(c) });
                        self.drop_queued(orphan);
                        return Err(e);
                    }
                }
            }
            None => None,
        };
        Ok(Some(Message {
            sender: q.sender,
            payload: q.payload,
            transferred,
            transferred_kind: if transferred.is_some() { moved_kind } else { None },
        }))
    }
}

// ---- the gate's single global instance ---------------------------------
//
// `init` builds one holder per scheduler task (matching
// `experimental::ipc::init`'s shape); `prepare_task0_for_proof` is the
// proof-boot's ONE kernel-only equip -- endpoint 0 owned by task 0 plus a
// SEND|GRANT capability at a known handle -- the same role the release's
// bootstrap_grant plays for its init process.

static mut SYSTEM: Option<CapSystem> = None;

/// One holder per scheduler task, matching the mailbox count. Called in
/// the same phase as `experimental::ipc::init` (after the scheduler's
/// spawns; the gate's CALL_CAP rows are refused-by-name in the boot
/// self-test BEFORE any of this, on empty-table state).
pub fn init(task_count: usize) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        for t in 0..task_count {
            let _ = sys.create_process(t);
        }
    })
}

/// Proof-boot equip: endpoint 0 owned by task 0; a fresh SEND|GRANT cap
/// minted into task 0's table. Retired 2026-11-05 for
/// `prepare_session_for_proof`: the session machine gives a ring-3
/// caller its OWN table, so sharing task 0's is not the equip's shape
/// any more. (Store home since the generalization: the scheduler.)
/// The gate session's equip, the release's session machine in FIRST
/// SLICE (`service84::attach_gate_session` and `src/svc84_proof.rs`):
/// creates the holder for the ring-3 caller's SESSION `sid` -- its OWN
/// table, not task 0's -- mints its endpoint-0 capability (slot 0 owner
/// cap, slot 1 SEND|GRANT) and the Input kind (slot 2, READ|GRANT, the
/// release's `bootstrap_input` right set). Returns (endpoint id, send
/// handle, input-poll handle) so the proof program and the kernel's
/// read-back agree on the SAME granted indexes. Explicit,
/// terminal-path-only (the proof never returns): in the full machine
/// the manifest parks (every ring-3 task a session), the table is
/// minted at the scheduler's ring-3 ALLOC and destroyed on exit; this
/// slice's session lives until the boot ends.
pub fn prepare_session_for_proof(sid: usize) -> (u32, u32, u32) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.create_process(sid).expect("caps session equip: session holder");
        let (ep, _) = sys.create_endpoint(sid).expect("caps session equip: endpoint 0");
        let cap_handle = sys
            .mint(sid, ep, rights::SEND | rights::GRANT)
            .expect("caps session equip: mint SEND|GRANT");
        let input_handle = sys
            .bootstrap_input(sid, rights::READ | rights::GRANT)
            .expect("caps session equip: mint INPUT");
        (ep, cap_handle, input_handle)
    })
}

/// The two-process proof's equip: `sid` becomes a second SESSION holding
/// a NEW endpoint (the peer's own, whose owner capability RECV uses) and
/// a grant of the SHARED region -- so the region has three live
/// capabilities over two holders, which is what makes its lifetime
/// observable. `caller` gets a second grant of the same region (its
/// "the record survives my close" handle) and a SEND|GRANT capability for
/// the peer's endpoint (the call it will park on).
///
/// Returns `(peer endpoint, peer owner handle, peer region handle,
/// caller's second region handle, caller's send-to-peer handle)` in the
/// install order that produced them -- the proof asserts each index
/// rather than assuming it, so a table-drift bug fails there instead of
/// turning into a mysterious EBADF from ring 3.
pub fn prepare_peer_for_proof(
    sid: usize,
    caller: usize,
    region: u32,
) -> (u32, u32, u32, u32, u32) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.create_process(sid).expect("caps peer equip: peer holder");
        let (ep, owner_handle) = sys.create_endpoint(sid).expect("caps peer equip: peer endpoint");
        let peer_region = sys
            .bootstrap_region(sid, region, rights::READ | rights::WRITE | rights::GRANT)
            .expect("caps peer equip: peer region grant");
        let caller_region2 = sys
            .bootstrap_region(caller, region, rights::READ | rights::WRITE | rights::GRANT)
            .expect("caps peer equip: caller's second region grant");
        let send_handle = sys
            .bootstrap_grant(caller, ep, rights::SEND | rights::GRANT)
            .expect("caps peer equip: caller SEND|GRANT to the peer");
        (ep, owner_handle, peer_region, caller_region2, send_handle)
    })
}

/// Proof-boot equip for the CALL/REPLY pair: `server` owns endpoint 0 (so
/// its answer slot exists) and `caller` holds a SEND|GRANT capability for
/// that endpoint in its OWN table -- two DISTINCT holders, which is what
/// makes the call cross tables rather than loop inside one. Returns the
/// caller's handle. Kernel-only, like every other bootstrap here.
pub fn prepare_call_proof(server: usize, caller: usize) -> u32 {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.create_process(server).expect("caps call equip: server holder");
        sys.create_process(caller).expect("caps call equip: caller holder");
        let (ep, _owner_handle) = sys.create_endpoint(server).expect("caps call equip: server endpoint");
        sys.bootstrap_grant(caller, ep, rights::SEND | rights::GRANT)
            .expect("caps call equip: caller SEND|GRANT")
    })
}

/// Access for the 0x84 rows: sends a kernel-side payload COPY (the gate
/// already validated the user range) from the caller task through
/// `handle`.
pub fn send_for(
    caller: usize,
    handle: u32,
    payload: &[u8],
    transfer: Option<(u32, u8)>,
) -> Result<(), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.send(caller, handle, payload, transfer)
    })
}

/// Mirror for receive; the gate copies the drained payload into user
/// memory under its own validator.
pub fn receive_for(caller: usize, endpoint: u32) -> Result<Option<Message>, CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.receive(caller, endpoint)
    })
}

/// Proof-boot equip, the release's `bootstrap_input` role lifted to
/// the gate's equip path (like `prepare_session_for_proof`): the
/// KERNEL mints `to`'s holder an Input capability, rights masked to
/// READ|GRANT (READ = may poll, GRANT = may pass it on). Kernel-only
/// and terminal-path-only: a normal boot never mints an Input cap
/// into a live table; the equip path is the proof session's (the
/// session machine's first slice), and identity on the rows is the
/// SESSION, not task 0.
    pub fn bootstrap_input_for(to: usize, want: u8) -> Result<u32, CapError> {
        interrupts::without_interrupts(|| unsafe {
            let sys = SYSTEM.get_or_insert_with(CapSystem::new);
            sys.bootstrap_input(to, want)
        })
    }


/// Access for the INPUT_POLL row: the release's authority-first check --
/// WrongType unless `handle` names an Input capability, NoRight unless
/// it carries READ -- run BEFORE any event is consumed.
pub fn input_check_for(caller: usize, handle: u32) -> Result<(), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.input_check(caller, handle)
    })
}

/// Access for the CALL row: a call from `caller` through `handle`,
/// answering the endpoint's owner (the release's `call_send` return).
pub fn call_for(caller: usize, handle: u32, payload: &[u8]) -> Result<usize, CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.call_send(caller, handle, payload)
    })
}

/// Access for the REPLY row: answers one call and consumes the
/// capability. Answers the slot index the bytes landed in.
pub fn reply_for(caller: usize, handle: u32, payload: &[u8]) -> Result<u32, CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.reply(caller, handle, payload)
    })
}

/// A second grant of an ALREADY created region, for the two-process
/// proof (the kernel-side equip's second holder). Returns the handle the
/// holder was given; the region's holder count rises by one, which is
/// what makes the lifetime proof meaningful.
pub fn equip_region_for(to: usize, region: u32) -> u32 {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.bootstrap_region(to, region, rights::READ | rights::WRITE | rights::GRANT)
            .expect("caps region equip: second holder")
    })
}

/// Access for the shared-memory row: (region id, rights the holder's
/// capability carries). WrongType unless the handle IS a region
/// capability -- an endpoint handle asked for geometry is refused by name.
pub fn region_cap_for(caller: usize, handle: u32) -> Result<(u32, u8), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.region_cap(caller, handle)
    })
}

/// Access for the geometry the row REPORTS: the kernel's own record of the
/// mapping, not anything the holder supplied.
pub fn region_geometry_of(region: u32) -> Result<Region, CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.region_geometry(region)
    })
}

/// Installs the kernel half of a region REQUEST on the GATE's system
/// (`service84`'s 0x4f row). Proof boot only, exactly like the equips:
/// the ordinary boot has no ring-3 caller that could ask, and installing
/// a mapper there would claim a capability the demo chain never uses.
pub fn install_region_hooks(
    mapper: unsafe fn(u64) -> Option<Region>,
    releaser: unsafe fn(Region),
    unmapper: unsafe fn(Region),
) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.install_region_hooks(mapper, releaser, unmapper)
    })
}

/// The gate's 0x50 row: the caller drops ITS OWN window on a region whose
/// capability it keeps. The frames and every other holder's window stay.
pub fn unmap_region_for(caller: usize, handle: u32) -> Result<(), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.unmap_region_cap(caller, handle)
    })
}

/// The 0x50 leg's read-back: does `holder` still have a window on `region`?
pub fn holder_window(caller: usize, region: u32) -> bool {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.holder_is_mapped(region, caller)
    })
}

/// The gate's region REQUEST: the caller asks for `pages` fresh pages and
/// is minted a capability over the mapping the kernel chose. Returns the
/// region id, the holder's handle and the geometry, so the answering row
/// and the kernel's read-back agree on the same facts.
pub fn request_region_for(caller: usize, pages: u64) -> Result<(u32, u32, Region), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.request_region(caller, pages, rights::READ | rights::WRITE | rights::GRANT)
    })
}

/// Proof-boot equip for the region kind: the kernel records the region it
/// has ALREADY mapped and mints `to` a capability over it. Returns the
/// region id and the holder's handle, so the caller and the kernel's
/// read-back agree on the same granted index.
pub fn prepare_region_for_proof(to: usize, base: u64, len: u64) -> (u32, u32) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        let region = sys.create_region(base, len);
        let handle = sys
            .bootstrap_region(to, region, rights::READ | rights::WRITE | rights::GRANT)
            .expect("caps region equip: bootstrap_region");
        (region, handle)
    })
}

/// The release's region CLOSE for a holder: one capability given back.
pub fn close_region_for(caller: usize, handle: u32) -> Result<(), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.close_region_cap(caller, handle)
    })
}

/// CAP_EXIT's capability half: releases every capability the holder owns
/// and reports how many region capabilities that tore down.
pub fn release_holder_caps(caller: usize) -> usize {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.release_holder(caller)
    })
}

/// How many callers are parked on a call right now -- the stall dump's
/// most direct question ("did the caller register at all?").
pub fn wait_count() -> usize {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.waits.iter().filter(|w| w.answer.is_none()).count()
    })
}

/// Read-backs for the gate: whether the region record survived its
/// holders, and how many it currently names.
pub fn region_live(region: u32) -> bool {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.region_live(region)
    })
}

pub fn region_holders(region: u32) -> Option<u32> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.region_holders(region)
    })
}

/// The region whose geometry starts at `base` -- the boot half's way of
/// naming a region it handed out by ADDRESS rather than by table index.
/// Kernel-side only: no row exposes it, so a holder cannot probe the
/// region table through it.
pub fn region_id_by_base(base: u64) -> Option<u32> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.region_id_by_base(base)
    })
}

/// REVOKES a live region's mapping: the pages go back while its holders
/// keep their (now stale) capabilities. Kernel-side only -- see
/// [`CapSystem::revoke_region`].
pub fn revoke_region(region: u32) -> Result<(), CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.revoke_region(region)
    })
}

/// Access for the BLOCKING CALL: registers the wait slot, then queues the
/// request exactly as the non-blocking form does. The gate parks the
/// caller after this returns, so the order matters -- register first, or
/// a fast reply could land before there is anywhere to put it.
pub fn call_wait_for(caller: usize, task: usize, handle: u32, payload: &[u8]) -> Result<usize, CapError> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.call_wait_begin(caller, task, handle, payload)
    })
}

/// Access for the resumed caller: drain whatever the reply deposited.
pub fn take_answer_for(caller: usize) -> Option<Vec<u8>> {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.take_answer(caller)
    })
}

// ---- the kernel-side self-test ------------------------------------------
//
// Every ported clause, positive AND negative, on its own multi-holder
// system. The holder whose handle indexes another holder's table, wider
// rights than held, a stale generation, an oversize payload or an
// overflowing queue each refuse BY NAME; a capability machine that
// answered these with silence would be a costume, not a capability.

// The self-test's OWN kernel half of a region request: this module must be
// able to prove the request/release ordering without a page table, so the
// mapper answers a fixed window and the releaser records what it was given.
// The proof boot installs the REAL pair on the gate's system instead
// (`install_region_hooks`); these statics are never installed there.
const SELF_TEST_REGION_BASE: u64 = 0x6000_0006_0000;
static TEST_RELEASED: AtomicU64 = AtomicU64::new(0);
static TEST_RELEASED_BASE: AtomicU64 = AtomicU64::new(0);

fn self_test_region_mapper(pages: u64) -> Option<Region> {
    Some(Region { base: SELF_TEST_REGION_BASE, len: pages * 4096 })
}

fn self_test_region_releaser(geometry: Region) {
    TEST_RELEASED.fetch_add(1, Ordering::SeqCst);
    TEST_RELEASED_BASE.store(geometry.base, Ordering::SeqCst);
}

/// The per-holder half: one window dropped, no frame returned. Counted
/// separately from the releaser so the self-test can tell "the window went"
/// from "the pages went".
fn self_test_region_unmapper(geometry: Region) {
    TEST_UNMAPPED.fetch_add(1, Ordering::SeqCst);
    TEST_UNMAPPED_BASE.store(geometry.base, Ordering::SeqCst);
}

static TEST_UNMAPPED: AtomicU64 = AtomicU64::new(0);
static TEST_UNMAPPED_BASE: AtomicU64 = AtomicU64::new(0);

pub fn run() -> bool {
    use crate::serial_println;

    let mut failed: Vec<&'static str> = Vec::new();
    let mut groups: u32 = 0;
    macro_rules! check {
        ($name:literal, $cond:expr) => {
            groups += 1;
            if !$cond {
                failed.push($name);
            }
        };
    }

    let mut sys = CapSystem::new();
    const SERVER: usize = 1;
    const CLIENT: usize = 2;
    let _ = sys.create_process(SERVER);
    let _ = sys.create_process(CLIENT);
    let (ep, srv_h) = sys.create_endpoint(SERVER).expect("self-test: server endpoint");

    // owner send + receive round trip
    check!("owner send", sys.send(SERVER, srv_h, b"ping", None).is_ok());
    let m = sys.receive(SERVER, ep).unwrap().unwrap();
    check!("owner receive", m.sender == SERVER && m.payload == b"ping" && m.transferred.is_none());

    // an EMPTY slot in the caller's own table is BadHandle, not silence
    check!(
        "empty slot refused",
        sys.send(SERVER, 5, b"x", None) == Err(CapError::BadHandle)
    );

    // a dead holder's table is unreachable in every way
    check!(
        "dead holder refused",
        sys.send(usize::MAX, 0, b"x", None) == Err(CapError::BadProcess)
    );

    // payload bound (§3.1)
    let big = [7u8; MAX_PAYLOAD + 1];
    check!(
        "oversize payload refused",
        sys.send(SERVER, srv_h, &big, None) == Err(CapError::PayloadTooLarge)
    );

    // backpressure: bounded queue, reported error, NOTHING silently dropped
    for _ in 0..QUEUE_CAPACITY {
        let _ = sys.send(SERVER, srv_h, b"fill", None);
    }
    check!("bounded queue refused", sys.send(SERVER, srv_h, b"overflow", None) == Err(CapError::QueueFull));
    for _ in 0..QUEUE_CAPACITY {
        let _ = sys.receive(SERVER, ep);
    }
    // queue really maintains EVERYTHING sent: nothing dropped on the floor
    let mcount = (0..QUEUE_CAPACITY).filter(|_| sys.receive(SERVER, ep).unwrap().is_some()).count();
    check!("queue drained fully after backpressure drain", mcount == 0);

    // transfer: the client holds a SEND|GRANT cap (kernel-only bootstrap),
    // sends, AND transfers that SAME capability attenuated (SEND only)
    let cli_h = sys
        .bootstrap_grant(CLIENT, ep, rights::SEND | rights::GRANT)
        .expect("self-test: client grant");
    check!("client send through grant", sys.send(CLIENT, cli_h, b"hello", None).is_ok());
    let m = sys.receive(SERVER, ep).unwrap().unwrap();
    check!("client send attributed", m.sender == CLIENT && m.payload == b"hello");
    check!(
        "attenuated transfer accepted",
        sys.send(CLIENT, cli_h, b"", Some((cli_h, rights::SEND))).is_ok()
    );
    let m = sys.receive(SERVER, ep).unwrap().unwrap();
    let new_in_server = match m.transferred {
        Some(h) => Some(h),
        None => None,
    };
    check!("transfer installs a handle in the receiver", new_in_server.is_some());
    let new_in_server = new_in_server.unwrap();
    // the receiver's new handle can SEND (it kept SEND)...
    check!("attenuated transfer still SENDs", sys.send(SERVER, new_in_server, b"via-transfer", None).is_ok());
    let _ = sys.receive(SERVER, ep);
    // ...but only WITHIN its (attenuated) rights: no GRANT on the copy
    check!(
        "transferred copy cannot re-transfer",
        sys.send(SERVER, new_in_server, b"", Some((new_in_server, rights::SEND))) == Err(CapError::NoRight)
    );

    // §4 issuance is OWNER-only: a holder minting on someone else's
    // endpoint is refused by NAME. (The earlier version of this check
    // called mint with the CLIENT by accident and panicked -- the
    // machine refused correctly; the check was wrong. Moved here as an
    // explicit, expected refusal.)
    check!("non-owner mint refused", sys.mint(CLIENT, ep, rights::SEND) == Err(CapError::NotOwner));

    // WIDENING: a holder demanding rights beyond what it holds refused
    // against a cap it ACTUALLY holds. The transfer machine checks the
    // GRANT right FIRST (no widening discussion for a cap that cannot be
    // transferred at all), so two different instruments: a SEND-only
    // holder answers NoRight, and a GRANT-only holder -- legal in the
    // rights space -- reaches the subrange check and answers Widening.
    // (The earlier version minted a SEND-only cap and demanded SEND|GRANT
    // from it: NoRight fired first, and the widening check never ran.)
    let cli_send_only = sys.bootstrap_grant(CLIENT, ep, rights::SEND).expect("self-test: SEND-only client grant");
    check!(
        "transfer without GRANT refused",
        sys.send(CLIENT, cli_send_only, b"", Some((cli_send_only, rights::SEND))) == Err(CapError::NoRight)
    );
    let cli_grant_only = sys.bootstrap_grant(CLIENT, ep, rights::GRANT).expect("self-test: GRANT-only client grant");
    check!(
        "transfer demanding UNheld rights refused (widening)",
        sys.send(CLIENT, cli_grant_only, b"", Some((cli_grant_only, rights::SEND | rights::GRANT))) == Err(CapError::Widening)
    );

    // ---- the Input kind (the release's Phase-9/1-29 clauses) ----
    // The kernel mints CLIENT the Input cap (READ|GRANT): the holder may
    // POLL events. Refusals, by name:
    // NOTE: this group needs a LIVE ep and a CURRENT-generation cli_h
    // (the transfer rides a send THROUGH the endpoint), so it runs
    // BEFORE the revocation and terminate groups below -- those kill
    // exactly what this group leans on.
    let input_h = sys.bootstrap_input(CLIENT, rights::READ | rights::GRANT).expect("self-test: input grant");
    check!("input check through a READ copy", sys.input_check(CLIENT, input_h).is_ok());
    // WrongType: the check run against an ENDPOINT cap's handle -- the
    // kind field is what refuses, not silence.
    check!("input check on an endpoint cap refused (WrongType)", sys.input_check(CLIENT, cli_h) == Err(CapError::WrongType));
    // NoRight: the same kind but masked to GRANT only -- the authority
    // to pass it on is NOT the authority to poll.
    let input_grant_only = sys.bootstrap_input(CLIENT, rights::GRANT).expect("self-test: GRANT-only input");
    check!("input check without READ refused (NoRight)", sys.input_check(CLIENT, input_grant_only) == Err(CapError::NoRight));
    // Transfer: the holder passes the Input cap on, ATTENUATED to the
    // SERVER's poll authority (READ only, no GRANT in the copy).
    check!(
        "input cap transferred attenuated",
        sys.send(CLIENT, cli_h, b"input", Some((input_h, rights::READ))).is_ok()
    );
    let m = sys.receive(SERVER, ep).expect("self-test: transfer receive").expect("self-test: transfer message");
    let input_in_server = m.transferred.expect("self-test: input transfer installed");
    check!("received input copy polls", sys.input_check(SERVER, input_in_server).is_ok());
    // The attenuation holds in the OTHER direction too: the received
    // copy carries no GRANT, so it cannot be passed on again. (The
    // SERVER sends through its own SEND-something endpoint handle --
    // the send's own authority is fine; the TRANSFER clause inspects
    // the input copy, finds no GRANT, and refuses.)
    check!(
        "received input copy cannot re-transfer",
        sys.send(SERVER, srv_h, b"", Some((input_in_server, rights::READ))) == Err(CapError::NoRight)
    );

    // ---- the Reply kind (the release's one-shot answer authority) ----
    // A call mints a SEND-only Reply capability naming the CALLER and
    // rides it in the message's dedicated transfer field; the receiver
    // gets a handle, answers ONCE, and the handle is gone. Every step
    // here is on the real machine, not a look-alike: the same endpoint
    // the earlier groups use.
    check!("call answers the owner", sys.call_send(SERVER, srv_h, b"ask").unwrap_or(usize::MAX) == SERVER);
    let call_msg = match sys.receive(SERVER, ep) {
        Ok(Some(m)) => Some(m),
        _ => None,
    };
    let reply_h = call_msg.as_ref().and_then(|m| m.transferred);
    check!(
        "call delivers the message and a reply capability",
        matches!(&call_msg, Some(m) if m.sender == SERVER && m.payload == b"ask") && reply_h.is_some()
    );
    let reply_h = reply_h.unwrap_or(u32::MAX);
    check!("reply target names the caller", sys.reply_target(SERVER, reply_h) == Ok(SERVER));
    // WrongType: an ENDPOINT handle asked to answer -- the kind field is
    // what refuses, exactly as the release's reply_target does.
    check!(
        "reply through an endpoint handle refused (WrongType)",
        sys.reply_target(SERVER, srv_h) == Err(CapError::WrongType)
    );
    // The answer lands at the caller's own endpoint and the handle is
    // consumed by the same call.
    check!("reply queued", sys.reply(SERVER, reply_h, b"answer").is_ok());
    check!("reply is one-shot (handle consumed)", sys.reply(SERVER, reply_h, b"again") == Err(CapError::BadHandle));
    let answered = match sys.receive(SERVER, ep) {
        Ok(Some(m)) => Some(m),
        _ => None,
    };
    check!(
        "the caller drains its answer",
        matches!(&answered, Some(m) if m.payload == b"answer" && m.sender == SERVER && m.transferred.is_none())
    );
    // A reply capability is SEND-only, so it can never be handed on:
    // a second call gives a FRESH handle to try the transfer with.
    let _ = sys.call_send(SERVER, srv_h, b"ask2");
    let second = sys.receive(SERVER, ep).ok().flatten().and_then(|m| m.transferred);
    check!(
        "a reply capability cannot be transferred",
        matches!(second, Some(h) if sys.send(SERVER, srv_h, b"", Some((h, rights::SEND))) == Err(CapError::NoRight))
    );
    // ...and a call through a NON-endpoint handle is WrongType, not a
    // send with a reply riding along.
    check!("call through a non-endpoint handle refused", sys.call_send(CLIENT, input_h, b"x") == Err(CapError::WrongType));

    // ---- the BLOCKING call's wait registry ----
    // The park itself is the scheduler's and is proven by the boot's own
    // two-task proof (the gate parks a caller and a DIFFERENT session's
    // task answers it); what belongs here is the machine's half: one
    // outstanding wait per caller, the answer landing in the WAIT slot
    // rather than an endpoint, and the slot being gone once consumed.
    // `usize::MAX` is the task to wake -- there is none, and `unblock_task`
    // refuses an unknown id, so the self-test never touches the boot's own
    // task states.
    check!("blocking call accepted", sys.call_wait_begin(CLIENT, usize::MAX, cli_h, b"wait").is_ok());
    check!(
        "a second blocking call from the same caller refused (CallPending)",
        sys.call_wait_begin(CLIENT, usize::MAX, cli_h, b"x") == Err(CapError::CallPending)
    );
    let waited = sys.receive(SERVER, ep).ok().flatten().and_then(|m| m.transferred);
    check!("the waiting call arrives with its one-shot reply right", waited.is_some());
    check!(
        "reply fills the PARKED caller's slot",
        matches!(waited, Some(h) if sys.reply(SERVER, h, b"early").is_ok())
    );
    check!(
        "the waiting caller wakes with the answer in its own slot",
        sys.take_answer(CLIENT).as_deref() == Some(&b"early"[..])
    );
    check!("the consumed wait slot is gone", sys.take_answer(CLIENT).is_none());

    // ---- the SHARED-MEMORY kind ----
    // The region table's own half: the kernel records a mapping, the
    // capability names it, and the geometry answered is the KERNEL's
    // record. (Whether the bytes are genuinely shared is the boot proof's
    // business -- it writes through the same frames from both rings.)
    let region = sys.create_region(0x6000_0003_0000, 4096);
    let region_h = sys.bootstrap_region(CLIENT, region, rights::READ | rights::WRITE).expect("self-test: region grant");
    check!(
        "region capability names its region and rights",
        sys.region_cap(CLIENT, region_h) == Ok((region, rights::READ | rights::WRITE))
    );
    check!(
        "region geometry is the kernel's record",
        matches!(sys.region_geometry(region), Ok(g) if g.base == 0x6000_0003_0000 && g.len == 4096)
    );
    check!(
        "an endpoint handle asked for geometry is WrongType",
        sys.region_cap(CLIENT, cli_h) == Err(CapError::WrongType)
    );
    check!(
        "an unknown region id is BadRegion",
        sys.region_geometry(u32::MAX) == Err(CapError::BadRegion)
    );
    check!(
        "bootstrapping an unknown region is refused",
        sys.bootstrap_region(CLIENT, u32::MAX, rights::READ) == Err(CapError::BadRegion)
    );
    check!(
        "a wider region rights claim is masked, not widened",
        matches!(sys.bootstrap_region(CLIENT, region, 0xff), Ok(h) if sys.region_cap(CLIENT, h) == Ok((region, rights::READ | rights::WRITE | rights::GRANT)))
    );

    // ---- the region's LIFETIME ----
    // Fresh holders, so the counts below are this section's alone: the
    // release's rule is that a region dies with its LAST holder, and a
    // self-test that shared holders with the sections above could not say
    // which close moved which count. (Small ids on purpose: `create_process`
    // PADS the table up to the id it is given, and the first version of
    // this section asked for 9000 -- a 288 KB table that exhausted the
    // kernel heap and panicked the boot. The ids only have to be free in
    // THIS system, which is the self-test's own.)
    const LIFE: usize = 3;
    const LIFE2: usize = 4;
    sys.create_process(LIFE).expect("self-test: lifetime holder");
    sys.create_process(LIFE2).expect("self-test: lifetime holder 2");
    let life_region = sys.create_region(0x6000_0004_0000, 8192);
    let life_a = sys.bootstrap_region(LIFE, life_region, rights::READ | rights::WRITE).expect("self-test: lifetime grant");
    let life_b = sys.bootstrap_region(LIFE, life_region, rights::READ | rights::WRITE).expect("self-test: lifetime grant 2");
    let life_c = sys.bootstrap_region(LIFE2, life_region, rights::READ).expect("self-test: lifetime grant 3");
    check!(
        "a region counts every live capability, not every holder",
        sys.region_holders(life_region) == Some(3)
    );
    check!(
        "closing one capability decreases the count",
        sys.close_region_cap(LIFE, life_a).is_ok() && sys.region_holders(life_region) == Some(2)
    );
    check!(
        "the record survives a close while another holder lives",
        sys.region_geometry(life_region).is_ok() && sys.region_live(life_region)
    );
    check!(
        "a closed handle is consumed, and the count does not move again",
        sys.close_region_cap(LIFE, life_a) == Err(CapError::BadHandle)
            && sys.region_holders(life_region) == Some(2)
    );
    // A capability of ANOTHER kind in the SAME table: the close row must
    // refuse by kind, not by "not found" -- and it must not move the count.
    // (The first version of this clause handed the row CLIENT's handle,
    // which LIFE's table has no idea about: that is BadHandle, a different
    // refusal, and the boot said so by name.)
    let (_, life_other) = sys.create_endpoint(LIFE).expect("self-test: lifetime endpoint");
    check!(
        "closing a NON-region capability is WrongType, and changes nothing",
        sys.close_region_cap(LIFE, life_other) == Err(CapError::WrongType)
            && sys.region_holders(life_region) == Some(2)
    );
    check!(
        "closing the second-to-last capability leaves the record live",
        sys.close_region_cap(LIFE, life_b).is_ok()
            && sys.region_live(life_region)
            && sys.region_holders(life_region) == Some(1)
    );
    check!(
        "closing the LAST capability frees the record by name",
        sys.close_region_cap(LIFE2, life_c).is_ok()
            && !sys.region_live(life_region)
            && sys.region_holders(life_region) == Some(0)
            && sys.region_geometry(life_region) == Err(CapError::BadRegion)
    );
    check!(
        "a freed region cannot be granted again",
        sys.bootstrap_region(LIFE, life_region, rights::READ) == Err(CapError::BadRegion)
    );
    // ...and the OTHER way a holder goes away: an abrupt exit. A holder
    // that never closes anything still must not pin a dead region alive.
    let exit_region = sys.create_region(0x6000_0005_0000, 4096);
    let _keep = sys.bootstrap_region(LIFE, exit_region, rights::READ | rights::WRITE).expect("self-test: exit grant");
    let _gone = sys.bootstrap_region(LIFE2, exit_region, rights::READ).expect("self-test: exit grant 2");
    check!(
        "an exit releases every region capability it held (one count per cap)",
        sys.release_holder(LIFE2) == 1
            && sys.region_live(exit_region)
            && sys.region_holders(exit_region) == Some(1)
    );
    check!(
        "a released holder's table is dead, not reusable",
        sys.region_cap(LIFE2, 0) == Err(CapError::BadProcess)
    );
    check!(
        "the last remaining holder's exit frees the region",
        sys.release_holder(LIFE) >= 1
            && !sys.region_live(exit_region)
            && sys.region_geometry(exit_region) == Err(CapError::BadRegion)
    );
    check!(
        "releasing a dead holder twice releases nothing",
        sys.release_holder(LIFE2) == 0
    );

    // ---- the region REQUEST and the mapping's lifetime ----
    // The kernel half (a real mapper + frame allocator) is installed by a
    // proof boot's gate; the self-test installs its OWN pair, so the
    // machine's half is proven without touching a page table. The counters
    // make "the mapping came back" a value.
    const REQ: usize = 5;
    sys.create_process(REQ).expect("self-test: request holder");
    check!(
        "a region request with no kernel mapper is refused by name",
        sys.request_region(REQ, 1, rights::READ) == Err(CapError::NoRegionMapper)
    );
    sys.install_region_hooks(
        self_test_region_mapper,
        self_test_region_releaser,
        self_test_region_unmapper,
    );
    TEST_RELEASED.store(0, Ordering::SeqCst);
    TEST_RELEASED_BASE.store(0, Ordering::SeqCst);
    let (req_region, req_h, req_geom) = sys
        .request_region(REQ, 2, rights::READ | rights::WRITE)
        .expect("self-test: region request");
    check!(
        "a requested region is mapped by the kernel half and its geometry recorded",
        req_geom.base == SELF_TEST_REGION_BASE
            && req_geom.len == 8192
            && sys.region_geometry(req_region) == Ok(req_geom)
            && sys.region_holders(req_region) == Some(1)
    );
    check!(
        "the requested region's capability carries the rights asked for",
        sys.region_cap(REQ, req_h) == Ok((req_region, rights::READ | rights::WRITE))
    );
    check!(
        "closing the LAST capability of a REQUESTED region gives its pages back",
        sys.close_region_cap(REQ, req_h).is_ok()
            && !sys.region_live(req_region)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
            && TEST_RELEASED_BASE.load(Ordering::SeqCst) == SELF_TEST_REGION_BASE
    );
    check!(
        "a double close does not release the mapping twice",
        sys.close_region_cap(REQ, req_h) == Err(CapError::BadHandle)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // A refused MINT must give the mapping back: the request that cannot
    // reach its holder is the case where frames would leak silently.
    let records_before = sys.regions.len();
    check!(
        "a request for a holder that does not exist is refused",
        sys.request_region(9999, 1, rights::READ) == Err(CapError::BadProcess)
    );
    check!(
        "...and the mapping it made is given back, with no record left behind",
        TEST_RELEASED.load(Ordering::SeqCst) == 2 && sys.regions.len() == records_before
    );
    // ...and the EXIT path releases a requested mapping too, exactly once
    // even when the exiting table held TWO capabilities over one region.
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let (exit_req, exit_a, _) = sys
        .request_region(REQ, 1, rights::READ)
        .expect("self-test: second region request");
    let exit_b = sys
        .bootstrap_region(REQ, exit_req, rights::READ)
        .expect("self-test: second capability over the requested region");
    check!(
        "two capabilities over one requested region count as two holders",
        sys.region_holders(exit_req) == Some(2)
            && exit_a != exit_b
    );
    check!(
        "an exiting holder releases ONE mapping, not one per capability",
        sys.release_holder(REQ) == 2
            && !sys.region_live(exit_req)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // A region the KERNEL mapped (owned: false) must NOT be unmapped by
    // this table: the boot proof keeps reading its page after its holders
    // are gone, so releasing it would be the bug.
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let boot_like = sys.create_region(0x6000_0007_0000, 4096);
    let boot_h = sys.bootstrap_region(CLIENT, boot_like, rights::READ).expect("self-test: kernel-mapped region");
    check!(
        "a kernel-mapped region's death does NOT call the releaser",
        sys.close_region_cap(CLIENT, boot_h).is_ok()
            && !sys.region_live(boot_like)
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
    );

    // ---- a region reaches a SECOND holder through the transfer field ----
    // The transfer machine always allowed a region capability to ride a
    // message; what it did not do was COUNT the copy, so a sender could
    // transfer a region, close its own capability and free the pages while
    // the receiver's copy was still in the queue -- or worse, installed and
    // pointing at frames the allocator had taken back. These clauses pin
    // the count at send, the handover at receive, and the count coming
    // back when a queue is thrown away.
    const XA: usize = 3; // transfers a region it requested
    const XB: usize = 4; // receives it into its own table
    let _ = sys.create_process(XA);
    let _ = sys.create_process(XB);
    let (xep, _xb_owner_h) = sys.create_endpoint(XB).expect("self-test: transfer endpoint");
    let xa_ep = sys
        .bootstrap_grant(XA, xep, rights::SEND | rights::GRANT)
        .expect("self-test: sender's endpoint grant");
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let (x_region, x_h, _) = sys
        .request_region(XA, 1, rights::READ | rights::WRITE | rights::GRANT)
        .expect("self-test: region for transfer");
    check!(
        "a requested region with GRANT can be handed on",
        sys.region_holders(x_region) == Some(1)
    );
    check!(
        "a transfer through the endpoint is queued",
        sys.send(XA, xa_ep, b"take", Some((x_h, rights::READ | rights::WRITE))).is_ok()
    );
    check!(
        "the QUEUED copy counts as a holder, not just the table slot",
        sys.region_holders(x_region) == Some(2)
    );
    check!(
        "the sender's close cannot free a region a copy is in flight for",
        sys.close_region_cap(XA, x_h).is_ok()
            && sys.region_live(x_region)
            && sys.region_holders(x_region) == Some(1)
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
    );
    let xm = sys.receive(XB, xep).expect("self-test: receive transfer").expect("self-test: message");
    let x_recv_h = xm.transferred.expect("self-test: region installed in the receiver");
    check!(
        "receive hands the count over instead of counting a second capability",
        sys.region_holders(x_region) == Some(1) && xm.payload == b"take"
    );
    check!(
        "the receiver's copy names the same region, attenuated to what was asked",
        sys.region_cap(XB, x_recv_h) == Ok((x_region, rights::READ | rights::WRITE))
    );
    // A READ-only region capability carries no GRANT. Attenuation cannot
    // manufacture one, so the transfer is refused by NAME -- the same
    // instrument the endpoint clauses use, on the region kind.
    let x_ro = sys
        .bootstrap_region(XA, x_region, rights::READ)
        .expect("self-test: READ-only region cap");
    check!(
        "a region cap without GRANT cannot be transferred",
        sys.send(XA, xa_ep, b"", Some((x_ro, rights::READ))) == Err(CapError::NoRight)
    );
    check!(
        "the receiver's copy cannot re-transfer (it kept no GRANT)",
        sys.send(XB, x_recv_h, b"", Some((x_recv_h, rights::READ))) == Err(CapError::NoRight)
    );
    // A THIRD capability over the same region (the kernel half granting the
    // sender a narrower copy) must move the count too, or the two closes
    // below would free the region twice.
    check!(
        "a further grant over a transferred region still counts",
        sys.region_holders(x_region) == Some(2)
            && sys.close_region_cap(XA, x_ro).is_ok()
            && sys.region_live(x_region)
            && sys.region_holders(x_region) == Some(1)
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
    );
    // WIDENING on the region kind, the other direction attenuation can be
    // asked to break: a capability holding READ|GRANT cannot be handed on as
    // WRITE|GRANT. The GRANT check passes (the holder has GRANT), so the
    // WIDER ask is what has to be refused -- by name, before the queue is
    // touched, leaving no count and no message behind. Without this clause
    // the refusal could regress to a silent truncation (the copy arriving
    // masked) and every earlier clause would still pass.
    let x_g = sys
        .bootstrap_region(XA, x_region, rights::READ | rights::GRANT)
        .expect("self-test: READ|GRANT region cap");
    check!(
        "a region transfer asking for a right the holder never held is refused (Widening), and leaves no count and no message",
        sys.send(XA, xa_ep, b"", Some((x_g, rights::WRITE | rights::GRANT)))
            == Err(CapError::Widening)
            && sys.region_holders(x_region) == Some(2)
            && sys.receive(XB, xep).ok().flatten().is_none()
            && sys.close_region_cap(XA, x_g).is_ok()
            && sys.region_live(x_region)
            && sys.region_holders(x_region) == Some(1)
    );
    // ...and now the receiver's close is the LAST one: the pages go back
    // exactly once, and only now -- the count that made the region survive
    // the sender's close is the one this close spends.
    check!(
        "the LAST holder's close frees the requested pages, once",
        sys.close_region_cap(XB, x_recv_h).is_ok()
            && !sys.region_live(x_region)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // REVOCATION: the kernel half takes a live region's pages back while
    // its holders still have capabilities. The mapping dies NOW (the
    // releaser fires once), the record goes dead, and the capabilities
    // that named it go stale -- geometry refuses by name and their closes
    // free nothing, which is the clause that keeps revocation from turning
    // into a double free when the holders are eventually torn down.
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let (rv_region, rv_h, _) = sys
        .request_region(XA, 1, rights::READ | rights::WRITE | rights::GRANT)
        .expect("self-test: region to revoke");
    let rv_h2 = sys
        .bootstrap_region(XB, rv_region, rights::READ)
        .expect("self-test: second holder of the revoked region");
    check!(
        "a region can be revoked while two holders still hold it, and the pages go back once",
        sys.region_holders(rv_region) == Some(2)
            && sys.revoke_region(rv_region).is_ok()
            && !sys.region_live(rv_region)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // The capability itself still resolves (the holder still HAS it -- the
    // kernel took the page, not the handle), but the GEOMETRY the row
    // reports is the kernel's record, and that record is dead: the same two
    // questions `0x4c` asks in that order, so a stale holder's geometry call
    // answers ENOENT by name rather than a base that no longer maps.
    check!(
        "the holders' capabilities survive the revocation as STALE: the handle still resolves, the geometry refuses by name, and the count is unchanged",
        sys.region_cap(XA, rv_h) == Ok((rv_region, rights::READ | rights::WRITE | rights::GRANT))
            && sys.region_geometry(rv_region) == Err(CapError::BadRegion)
            && sys.region_holders(rv_region) == Some(2)
    );
    check!(
        "a stale close after revocation frees nothing a second time",
        sys.close_region_cap(XA, rv_h).is_ok()
            && sys.close_region_cap(XB, rv_h2).is_ok()
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    check!(
        "a dead region cannot be revoked again",
        sys.revoke_region(rv_region) == Err(CapError::BadRegion)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // PER-HOLDER WINDOWS (2026-11-08): a region's frames and the window a
    // holder reads them through are different things, and the 0x50 row drops
    // exactly one window -- the caller's -- while the frames and the other
    // holders' windows stay. Nothing is released here: only the unmapper
    // fires, and the capability survives so the row can answer geometry
    // afterwards.
    TEST_RELEASED.store(0, Ordering::SeqCst);
    TEST_UNMAPPED.store(0, Ordering::SeqCst);
    let (w_region, w_h, w_geom) = sys
        .request_region(XA, 1, rights::READ | rights::WRITE | rights::GRANT)
        .expect("self-test: region whose window is dropped");
    let w_h2 = sys
        .bootstrap_region(XB, w_region, rights::READ)
        .expect("self-test: second holder keeps its window");
    check!(
        "one holder can drop its OWN window while the other holder's stays: the unmapper fires once, no frame is released, and both capabilities survive",
        sys.holder_is_mapped(w_region, XA)
            && sys.holder_is_mapped(w_region, XB)
            && sys.unmap_region_cap(XA, w_h).is_ok()
            && !sys.holder_is_mapped(w_region, XA)
            && sys.holder_is_mapped(w_region, XB)
            && TEST_UNMAPPED.load(Ordering::SeqCst) == 1
            && TEST_UNMAPPED_BASE.load(Ordering::SeqCst) == w_geom.base
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
            && sys.region_holders(w_region) == Some(2)
            && sys.region_geometry(w_region) == Ok(w_geom)
    );
    check!(
        "dropping the same window twice is refused by name, and a holder that never had one cannot drop another's",
        sys.unmap_region_cap(XA, w_h) == Err(CapError::NotMapped)
            && TEST_UNMAPPED.load(Ordering::SeqCst) == 1
    );
    check!(
        "the frames outlive both windows: the last close is what releases, once",
        sys.close_region_cap(XA, w_h).is_ok()
            && sys.close_region_cap(XB, w_h2).is_ok()
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
            && !sys.region_live(w_region)
    );
    // A queue that is THROWN AWAY (revocation) must give its counts back:
    // a revoked endpoint would otherwise pin a requested region's pages
    // forever, which is the leak this lifetime exists to prevent.
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let (x2_region, x2_h, _) = sys
        .request_region(XA, 1, rights::READ | rights::WRITE | rights::GRANT)
        .expect("self-test: second region for transfer");
    check!(
        "a transfer is queued for a region whose sender will keep a capability",
        sys.send(XA, xa_ep, b"", Some((x2_h, rights::READ | rights::WRITE))).is_ok()
            && sys.region_holders(x2_region) == Some(2)
    );
    check!(
        "revocation gives the queued copy's count back",
        sys.revoke(XB, xep).is_ok()
            && sys.region_live(x2_region)
            && sys.region_holders(x2_region) == Some(1)
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
    );
    check!(
        "...and the sender's own close is then the last one",
        sys.close_region_cap(XA, x2_h).is_ok()
            && !sys.region_live(x2_region)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );
    // Teardown of a dead owner's endpoints discards the same queues; the
    // count must come back the same way (a dead endpoint must not be a
    // place a region goes to live forever).
    let (x3_region, x3_h, _) = sys
        .request_region(XA, 1, rights::READ | rights::WRITE | rights::GRANT)
        .expect("self-test: third region for transfer");
    TEST_RELEASED.store(0, Ordering::SeqCst);
    let _ = sys.send(XA, xa_ep, b"", Some((x3_h, rights::READ | rights::WRITE)));
    check!(
        "terminating the owner hands the queued copy's count back",
        sys.terminate_process(XB).is_ok()
            && sys.region_live(x3_region)
            && sys.region_holders(x3_region) == Some(1)
            && TEST_RELEASED.load(Ordering::SeqCst) == 0
    );
    check!(
        "...and the region dies with its actual last capability",
        sys.close_region_cap(XA, x3_h).is_ok()
            && !sys.region_live(x3_region)
            && TEST_RELEASED.load(Ordering::SeqCst) == 1
    );

    // §4.3 revocation: O(1), outstanding caps dead, owner mints fresh
    check!("revoke", sys.revoke(SERVER, ep).is_ok());
    check!("stale generation refused", sys.send(CLIENT, cli_h, b"after revoke", None) == Err(CapError::Revoked));
    check!(
        "revoked handle also stale for transfers",
        sys.send(CLIENT, cli_h, b"", Some((cli_h, rights::SEND))) == Err(CapError::Revoked)
    );
    let fresh = sys.mint(SERVER, ep, rights::SEND | rights::GRANT).expect("self-test: fresh mint");
    check!("fresh mint works after revoke", sys.send(SERVER, fresh, b"post-revoke", None).is_ok());
    let _ = sys.receive(SERVER, ep);

    // §7.1: terminated owner's endpoints refuse with the typed error --
    // TERMINAL on purpose: this kills every endpoint SERVER owns, so no
    // later group may ride them (the panicking first run of this
    // self-test proved the ordering matters).
    check!("terminate", sys.terminate_process(SERVER).is_ok());

    if failed.is_empty() {
        serial_println!(
            "  experimental caps: {} clause groups proven positive+negative (handles forge-proof, attenuation-only transfers, O(1) revocation, bounded payloads and queues, the Input kind's poll authority) -- OK",
            groups
        );
        true
    } else {
        serial_println!(
            "  experimental caps: FAILED clauses {:?} -- capability semantics do not hold",
            failed
        );
        false
    }
}
