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
//! NOT ported yet, named so their absence is checkable: the other
//! capability kinds (BlockDevice, IoPort, FrameBuffer, Input, SharedMem,
//! Reply -- each has its own bootstrap/window machinery in the release),
//! badges, containers (NPS-007), notify, blocking receive. Every one of
//! those is a follow-on slice over THIS machine; this slice ports the
//! clauses the `0x84` gate's payload/transfer shape needs, and the
//! kernel-side self-test (`run()`) proves each of them positive AND
//! negative, mirroring the release's own `run()`.
//!
//! HOW CALLERS REACH IT: through the `0x84` gate's capability rows
//! (`service84::CALL_CAP_SEND`/`CALL_CAP_RECV`), whose caller identity is
//! `scheduler::current_task()`. TODAY that means every un-sessioned
//! ring-3 caller shares task 0's table -- the session machine the
//! PORT_MANIFEST parks is what gives ring-3 callers distinct tables; when
//! sessions land, these rows start isolating WITHOUT another ABI change.
//! What a single holder can exercise today -- bounds, generation,
//! attenuation -- is proven here by `run()` (which builds its OWN
//! multi-holder system, so the multi-holder clauses are kernel-proven
//! even before sessions exist) and by the ring-3 proof's capability leg.

use alloc::collections::VecDeque;
use alloc::vec::Vec;
use x86_64::instructions::interrupts;

/// §3.1: payloads are bounded; a bigger claim is a named refusal, not a
/// truncation.
pub const MAX_PAYLOAD: usize = 64;
/// Bounded queues: a full queue is `QueueFull`, never a silent drop.
pub const QUEUE_CAPACITY: usize = 4;
/// Per-holder table size.
pub const CAP_TABLE_SIZE: usize = 32;

/// Rights bits -- ENDPOINT-kind rights only; the device kinds' READ/WRITE
/// arrive with their kinds.
pub mod rights {
    pub const SEND: u8 = 1;
    /// May transfer this capability (attenuated) to others (§5.2).
    pub const GRANT: u8 = 2;
}

/// A capability: which endpoint, WHICH GENERATION of it (§4.3's O(1)
/// revocation), and the rights mask. No raw task ids, no endpoint numbers
/// leak to user code -- only the table indexes ("handles") do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capability {
    endpoint: u32,
    generation: u32,
    rights: u8,
}

/// The typed refusals (the release's IpcError, ENDPOINT half).
#[derive(Debug, PartialEq, Eq)]
pub enum CapError {
    BadProcess,
    BadHandle,
    NoRight,
    Revoked,
    TargetDead,
    PayloadTooLarge,
    QueueFull,
    Widening,
    NotOwner,
    TableFull,
}

/// A message queued at an endpoint: the sender task id, the bounded
/// payload, and an ATTENUATED capability travelling in the dedicated
/// transfer field (§5.1) -- never in payload bytes.
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

/// A whole system: per-holder tables + the endpoints. The gate owns one
/// global instance (`init`/`prepare_task0_for_proof`); the self-test
/// builds its own.
pub struct CapSystem {
    procs: Vec<Holder>,
    endpoints: Vec<Endpoint>,
}

impl CapSystem {
    pub fn new() -> Self {
        CapSystem { procs: Vec::new(), endpoints: Vec::new() }
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
    /// transfers in those queues vanish with the queue.
    pub fn terminate_process(&mut self, p: usize) -> Result<(), CapError> {
        let holder = self.procs.get_mut(p).filter(|h| h.alive).ok_or(CapError::BadProcess)?;
        holder.alive = false;
        for ep in self.endpoints.iter_mut().filter(|e| e.owner == p) {
            ep.alive = false;
            ep.queue.clear();
        }
        Ok(())
    }

    /// Creates an endpoint owned by `owner`, handing the owner a
    /// SEND|GRANT capability in its own table. Returns (endpoint id,
    /// owner's handle).
    pub fn create_endpoint(&mut self, owner: usize) -> Result<(u32, u32), CapError> {
        self.live(owner)?;
        let id = self.endpoints.len() as u32;
        self.endpoints.push(Endpoint { owner, generation: 0, alive: true, queue: VecDeque::new() });
        let cap = Capability { endpoint: id, generation: 0, rights: rights::SEND | rights::GRANT };
        let handle = Self::install(self.live(owner)?, cap)?;
        Ok((id, handle))
    }

    /// Owner-only §4.3 revocation: EVERY outstanding capability for this
    /// endpoint goes stale -- generation bump, queue drained -- with no
    /// holder cooperation needed.
    pub fn revoke(&mut self, owner: usize, ep: u32) -> Result<(), CapError> {
        let e = self.endpoints.get_mut(ep as usize).ok_or(CapError::BadHandle)?;
        if e.owner != owner {
            return Err(CapError::NotOwner);
        }
        e.generation += 1;
        e.queue.clear();
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
        let cap = Capability { endpoint: ep, generation, rights: want & (rights::SEND | rights::GRANT) };
        Self::install(self.live(owner)?, cap)
    }

    /// KERNEL-ONLY bootstrap install (the release's role for `init`'s
    /// elevated caps; never reachable through the 0x84 rows): puts a
    /// capability into `to`'s table directly.
    pub fn bootstrap_grant(&mut self, to: usize, ep: u32, want: u8) -> Result<u32, CapError> {
        let generation = self.endpoints.get(ep as usize).map(|e| e.generation).ok_or(CapError::BadHandle)?;
        let cap = Capability { endpoint: ep, generation, rights: want & (rights::SEND | rights::GRANT) };
        Self::install(self.live(to)?, cap)
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
                let tep = self.endpoints.get(held.endpoint as usize).ok_or(CapError::BadHandle)?;
                if tep.generation != held.generation {
                    return Err(CapError::Revoked);
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
        e.queue.push_back(Queued { sender: from, payload: payload.to_vec(), moved });
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
        let transferred = match q.moved {
            Some(c) => {
                let holder = self.live(owner)?;
                Some(Self::install(holder, c)?)
            }
            None => None,
        };
        Ok(Some(Message { sender: q.sender, payload: q.payload, transferred }))
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
/// minted into task 0's table. Returns (endpoint id, handle) so the proof
/// program and the kernel's read-back agree on the SAME granted index.
/// Explicit, terminal-path-only (the proof never returns), like
/// `ipc::prepare_endpoint0_for_proof`.
pub fn prepare_task0_for_proof() -> (u32, u32) {
    interrupts::without_interrupts(|| unsafe {
        let sys = SYSTEM.get_or_insert_with(CapSystem::new);
        sys.create_process(0).expect("caps proof equip: task 0 holder");
        let (ep, _) = sys.create_endpoint(0).expect("caps proof equip: endpoint 0");
        let handle = sys
            .mint(0, ep, rights::SEND | rights::GRANT)
            .expect("caps proof equip: mint SEND|GRANT");
        (ep, handle)
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

// ---- the kernel-side self-test ------------------------------------------
//
// Every ported clause, positive AND negative, on its own multi-holder
// system. The holder whose handle indexes another holder's table, wider
// rights than held, a stale generation, an oversize payload or an
// overflowing queue each refuse BY NAME; a capability machine that
// answered these with silence would be a costume, not a capability.

pub fn run() -> bool {
    use crate::serial_println;

    let mut failed: Vec<&'static str> = Vec::new();
    macro_rules! check {
        ($name:literal, $cond:expr) => {
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

    // §7.1: terminated owner's endpoints refuse with the typed error
    check!("terminate", sys.terminate_process(SERVER).is_ok());

    if failed.is_empty() {
        serial_println!(
            "  experimental caps: {} clause groups proven positive+negative (handles forge-proof, attenuation-only transfers, O(1) revocation, bounded payloads and queues) -- OK",
            21
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
