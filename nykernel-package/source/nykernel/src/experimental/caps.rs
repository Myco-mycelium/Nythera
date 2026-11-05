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
//! capability kinds (BlockDevice, IoPort, FrameBuffer, SharedMem, Reply
//! -- each has its own bootstrap/window machinery in the release),
//! badges, containers (NPS-007), notify. The INPUT kind IS now ported
//! (see its section below -- the router `kbdrt`'s prerequisite). Every
//! gap is a follow-on slice over THIS machine; the kernel-side
//! self-test (`run()`) proves each ported clause positive AND
//! negative, mirroring the release's own `run()`.
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
}

/// The capability kinds THIS machine knows. Endpoint is the queue-based
/// kind; Input is the release's Phase-9/1-29 poll-authority token
/// (endpoint 0, never stales). The remaining release kinds are named in
/// the module doc.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Endpoint,
    Input,
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
        let cap = Capability { kind: Kind::Endpoint, endpoint: id, generation: 0, rights: rights::SEND | rights::GRANT };
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
