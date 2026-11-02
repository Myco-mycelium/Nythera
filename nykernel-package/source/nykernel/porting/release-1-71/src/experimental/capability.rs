//! Pre-normative capability-checked, endpoint-based IPC core -- the
//! kernel-space primitive set of NPS-003 (Draft) §3-§5 and §7, in the
//! one place NPS-001 §3 says it belongs ("Capability/IPC Primitives").
//!
//! **Not NPS-003 conformance** (still Draft; see `crate::experimental`
//! banner). What it models, and which clause each behaviour comes from:
//!
//! * messages target an **endpoint through a capability**, never a raw
//!   process id (§3.2); sending needs a SEND-right capability (§4.1);
//! * capabilities live only in **kernel-owned per-process tables**; user
//!   code names them by table index, so they cannot be forged (§5.4) and
//!   one process's index means nothing in another's table;
//! * possessing a capability gives no way to enumerate other holders (§4.2)
//!   -- there is simply no API that returns holders;
//! * the owner can **revoke** all outstanding capabilities with no
//!   cooperation from holders (§4.3) -- implemented with a generation
//!   counter, so revocation is O(1) and every stale capability fails;
//! * capabilities travel only in a **dedicated transfer field**, never in
//!   payload bytes (§5.1); the sender must hold it, must hold the GRANT
//!   right, and may only **attenuate**, never widen (§5.2, §5.3);
//! * payloads are **bounded** (§3.1: `MAX_PAYLOAD`); queues are bounded
//!   and a full queue is a reported error, never a silent drop;
//! * messages to a **terminated owner** return a defined error instead of
//!   blocking (§7.1, §7.2).
//!
//! Not modelled: blocking `receive`/`call` (needs the scheduler wired to
//! syscalls), `notify`, shared-memory regions and their zeroing (§3.1
//! second half / FIND-CONTAINER-003), per-container rate limiting (§8.2),
//! the wire/ABI format (§9), and the §6.1 performance gate. No syscall
//! entry yet: this is the in-kernel core those syscalls would call.

use alloc::collections::VecDeque;
use alloc::vec::Vec;

pub const MAX_PAYLOAD: usize = 64;
pub const QUEUE_CAPACITY: usize = 4;
pub const CAP_TABLE_SIZE: usize = 128;

pub type ProcId = u32;
pub type EndpointId = u32;
pub type Handle = u32; // index into the caller's own capability table

/// Rights bit set.
pub mod rights {
    pub const SEND: u8 = 1;
    pub const GRANT: u8 = 2; // may transfer this capability to others
    pub const READ: u8 = 4;  // block device: read sectors
    pub const WRITE: u8 = 8; // block device: write sectors
}

/// What a capability designates. Kernel objects other than endpoints (here:
/// the block device, NPS-001 section 3's "Storage I/O Fast Path") are reached
/// through the same unforgeable, attenuable, transferable handle table.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Endpoint,
    BlockDevice,
    /// x86 I/O ports [lo, hi): the capability a user-space driver needs to touch hardware registers.
    IoPort,
    /// The display scan-out memory: the capability covers framebuffer ROWS [lo, hi) (narrowable like a sector window).
    FrameBuffer,
    /// The right to READ keyboard events from the kernel's input queue (Phase 9): only a holder can poll; user space routes them onward.
    Input,
    SharedMem, // `endpoint` field holds the region id (experimental::shm)
    Reply,     // one-shot right to answer ONE call; `endpoint` holds the caller's ProcId
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capability {
    kind: Kind,
    endpoint: EndpointId,
    generation: u32,
    rights: u8,
    /// BlockDevice only: the capability covers sectors [lo, hi). Full device = [0, u32::MAX].
    /// It can only be NARROWED (`narrow_device`) and travels with the capability on transfer.
    lo: u32,
    hi: u32,
    /// Endpoint only: an immutable tag the RECEIVER sees with every message sent through this capability (0 = unbadged).
    /// Set once by `badge` and carried unchanged through attenuating transfers, so a server can tell its clients apart.
    badge: u32,
}

#[derive(Debug, PartialEq, Eq)]
pub enum IpcError {
    BadProcess,
    BadHandle,       // empty slot or out of range: not forgeable, just invalid
    NoRight,         // capability lacks SEND (or GRANT for a transfer)
    Revoked,         // stale generation
    TargetDead,      // endpoint owner terminated (§7.1)
    PayloadTooLarge, // §3.1
    QueueFull,       // backpressure, not silent loss
    Widening,        // §5.3: tried to grant more rights than held
    NotOwner,
    TableFull,
    WrongType, // capability designates a different kind of object
    Contained, // the receiving process's container does not admit this kind of capability (NPS-007 §3)
}

#[derive(Debug, PartialEq, Eq)]
pub struct Message {
    pub sender: ProcId,
    pub payload: Vec<u8>,
    /// Handle (in the *receiver's* table) of a capability delivered with
    /// this message, if one was transferred.
    pub transferred: Option<Handle>,
    /// Badge of the capability the SENDER used (0 if unbadged).
    pub badge: u32,
}

struct Queued {
    sender: ProcId,
    payload: Vec<u8>,
    cap: Option<Capability>,
    badge: u32,
}

/// A container's policy: which kinds of capability its processes may hold, and how many live processes it may have.
struct ContainerCfg { mask: u8, quota: u32 }

fn kind_bit(k: Kind) -> u8 {
    match k { Kind::Endpoint => 1, Kind::BlockDevice => 2, Kind::IoPort => 4, Kind::SharedMem => 8, Kind::Reply => 16, Kind::FrameBuffer => 32, Kind::Input => 64 }
}
pub const MAX_CONTAINERS: usize = 8;

struct Endpoint {
    owner: ProcId,
    generation: u32,
    alive: bool,
    queue: VecDeque<Queued>,
    /// NPS-003 §3 `notify`: payload-free signal bits, OR-ed in by senders, cleared by the owner's wait. Never queues, so it can never fill up.
    notify: u64,
}

struct Process {
    alive: bool,
    container: u32,
    caps: [Option<Capability>; CAP_TABLE_SIZE],
}

pub struct CapSystem {
    procs: Vec<Process>,
    endpoints: Vec<Endpoint>,
    /// Per shared-memory region: how many capabilities currently refer to it, counting
    /// those sitting in per-process tables AND those in flight inside endpoint queues.
    region_refs: Vec<u32>,
    /// Containers (NPS-007 §3): index 0 is the root container (admits everything, no quota).
    containers: Vec<ContainerCfg>,
    /// Regions whose last capability just disappeared (the kernel drains this and lets the
    /// region die: region lifetime = capability holders, not the creating process).
    released: Vec<u32>,
}

impl CapSystem {
    pub fn new() -> Self {
        CapSystem { procs: Vec::new(), endpoints: Vec::new(), region_refs: Vec::new(), containers: alloc::vec![ContainerCfg { mask: 0xff, quota: u32::MAX }], released: Vec::new() }
    }

    /// New container admitting only the capability kinds in `mask` (bit 1 Endpoint, 2 BlockDevice, 4 IoPort, 32 FrameBuffer, 64 Input,
    /// 8 SharedMem, 16 Reply) with at most `quota` live processes. Returns its id (>= 1).
    pub fn create_container(&mut self, mask: u8, quota: u32) -> Result<u32, IpcError> {
        if self.containers.len() >= MAX_CONTAINERS { return Err(IpcError::TableFull); }
        self.containers.push(ContainerCfg { mask, quota });
        Ok((self.containers.len() - 1) as u32)
    }
    /// 1-70: which container a process is in (0 for an unknown process).
    pub fn container_of(&self, p: u32) -> u32 { self.procs.get(p as usize).map(|x| x.container).unwrap_or(0) }
    pub fn container_exists(&self, c: u32) -> bool { (c as usize) < self.containers.len() }
    pub fn container_members(&self, c: u32) -> Vec<ProcId> {
        self.procs.iter().enumerate().filter(|(_, p)| p.alive && p.container == c).map(|(i, _)| i as ProcId).collect()
    }
    pub fn container_has_room(&self, c: u32) -> bool {
        self.containers.get(c as usize).map_or(false, |cfg| (self.container_members(c).len() as u64) < cfg.quota as u64)
    }
    pub fn set_container(&mut self, p: ProcId, c: u32) { if let Some(pr) = self.procs.get_mut(p as usize) { pr.container = c; } }
    fn admits(&self, p: ProcId, k: Kind) -> bool {
        self.procs.get(p as usize).and_then(|pr| self.containers.get(pr.container as usize)).map_or(false, |c| c.mask & kind_bit(k) != 0)
    }

    pub fn create_process(&mut self) -> ProcId {
        self.procs.push(Process { alive: true, container: 0, caps: [None; CAP_TABLE_SIZE] });
        (self.procs.len() - 1) as ProcId
    }

    /// Terminates a process: its endpoints die (queued messages and
    /// outstanding capabilities become unusable) and its table is wiped.
    pub fn terminate_process(&mut self, p: ProcId) -> Result<(), IpcError> {
        let proc = self.procs.get_mut(p as usize).ok_or(IpcError::BadProcess)?;
        proc.alive = false;
        let held = core::mem::replace(&mut proc.caps, [None; CAP_TABLE_SIZE]);
        let mut dropped: Vec<Capability> = held.iter().flatten().copied().collect();
        for ep in self.endpoints.iter_mut().filter(|e| e.owner == p) {
            ep.alive = false;
            dropped.extend(ep.queue.drain(..).filter_map(|q| q.cap)); // in-flight caps die with the queue
            ep.generation += 1;
        }
        for c in &dropped { self.ref_dec(c); }
        Ok(())
    }

    fn ref_inc(&mut self, cap: &Capability) {
        if cap.kind == Kind::SharedMem {
            let i = cap.endpoint as usize;
            if self.region_refs.len() <= i { self.region_refs.resize(i + 1, 0); }
            self.region_refs[i] += 1;
        }
    }

    fn ref_dec(&mut self, cap: &Capability) {
        if cap.kind == Kind::SharedMem {
            if let Some(n) = self.region_refs.get_mut(cap.endpoint as usize) {
                *n = n.saturating_sub(1);
                if *n == 0 { self.released.push(cap.endpoint); }
            }
        }
    }

    /// Regions whose last holder vanished since the previous call.
    pub fn drain_released(&mut self) -> Vec<u32> { core::mem::take(&mut self.released) }

    fn live(&mut self, p: ProcId) -> Result<&mut Process, IpcError> {
        match self.procs.get_mut(p as usize) {
            Some(x) if x.alive => Ok(x),
            _ => Err(IpcError::BadProcess),
        }
    }

    fn install(proc: &mut Process, cap: Capability) -> Result<Handle, IpcError> {
        let slot = proc.caps.iter().position(|c| c.is_none()).ok_or(IpcError::TableFull)?;
        proc.caps[slot] = Some(cap);
        Ok(slot as Handle)
    }

    /// Creates an endpoint owned by `owner`; the owner receives a
    /// SEND|GRANT capability to it.
    pub fn create_endpoint(&mut self, owner: ProcId) -> Result<(EndpointId, Handle), IpcError> {
        self.live(owner)?;
        let id = self.endpoints.len() as EndpointId;
        self.endpoints.push(Endpoint { owner, generation: 0, alive: true, queue: VecDeque::new(), notify: 0 });
        let cap = Capability { kind: Kind::Endpoint, endpoint: id, generation: 0, rights: rights::SEND | rights::GRANT, lo: 0, hi: 0, badge: 0 };
        let h = Self::install(self.live(owner)?, cap)?;
        Ok((id, h))
    }

    /// Owner-only: invalidates EVERY outstanding capability to the
    /// endpoint (including the owner's own) without any holder's
    /// cooperation (§4.3). The owner can `mint` a fresh one afterwards.
    pub fn revoke(&mut self, owner: ProcId, ep: EndpointId) -> Result<(), IpcError> {
        self.live(owner)?;
        let e = self.endpoints.get_mut(ep as usize).ok_or(IpcError::BadHandle)?;
        if e.owner != owner { return Err(IpcError::NotOwner); }
        e.generation += 1;
        e.notify = 0;
        let dropped: Vec<Capability> = e.queue.drain(..).filter_map(|q| q.cap).collect(); // not delivered
        for c in &dropped { self.ref_dec(c); }
        Ok(())
    }

    /// Owner-only: issue a fresh capability for the current generation.
    pub fn mint(&mut self, owner: ProcId, ep: EndpointId, r: u8) -> Result<Handle, IpcError> {
        let generation = {
            let e = self.endpoints.get(ep as usize).ok_or(IpcError::BadHandle)?;
            if e.owner != owner { return Err(IpcError::NotOwner); }
            if !e.alive { return Err(IpcError::TargetDead); }
            e.generation
        };
        let cap = Capability { kind: Kind::Endpoint, endpoint: ep, generation, rights: r & (rights::SEND | rights::GRANT), lo: 0, hi: 0, badge: 0 };
        Self::install(self.live(owner)?, cap)
    }

    fn lookup(&mut self, p: ProcId, h: Handle) -> Result<Capability, IpcError> {
        let proc = self.live(p)?;
        proc.caps.get(h as usize).copied().flatten().ok_or(IpcError::BadHandle)
    }

    /// Messages currently queued at `ep` (scheduler uses this to decide
    /// whether a task blocked in RECV is runnable again).
    pub fn pending(&self, ep: EndpointId) -> usize {
        self.endpoints.get(ep as usize).map(|e| e.queue.len()).unwrap_or(0)
    }

    /// The `idx`-th endpoint (creation order) owned by `p`: how user code
    /// names its OWN endpoints for `receive` without ever seeing a global id.
    pub fn owned_endpoint(&self, p: ProcId, idx: usize) -> Option<EndpointId> {
        self.endpoints.iter().enumerate().filter(|(_, e)| e.owner == p).nth(idx).map(|(i, _)| i as EndpointId)
    }

    /// KERNEL-ONLY bootstrap: installs an initial capability into a
    /// process's table at launch -- the role NPS-001 §5 stage 4 gives the
    /// kernel when it starts `init` with "elevated initial capabilities".
    /// This is the only way a capability appears other than by `mint`
    /// (owner) or message transfer, and it is not reachable from user
    /// code (no syscall wraps it).
    pub fn bootstrap_grant(&mut self, to: ProcId, ep: EndpointId, r: u8) -> Result<Handle, IpcError> {
        let (generation, alive) = {
            let e = self.endpoints.get(ep as usize).ok_or(IpcError::BadHandle)?;
            (e.generation, e.alive)
        };
        if !alive { return Err(IpcError::TargetDead); }
        let cap = Capability { kind: Kind::Endpoint, endpoint: ep, generation, rights: r & (rights::SEND | rights::GRANT), lo: 0, hi: 0, badge: 0 };
        Self::install(self.live(to)?, cap)
    }

    /// KERNEL-ONLY bootstrap: gives `to` a capability to the block device.
    pub fn bootstrap_ioport(&mut self, to: ProcId, r: u8) -> Result<Handle, IpcError> {
        let cap = Capability { kind: Kind::IoPort, endpoint: 0, generation: 0, rights: r & (rights::READ | rights::WRITE | rights::GRANT), lo: 0, hi: 0x10000, badge: 0 };
        Self::install(self.live(to)?, cap)
    }

    /// Resolves `p`'s handle `h` to its port window; WrongType unless it is an I/O-port capability with READ|WRITE.
    pub fn ioport_window(&mut self, p: ProcId, h: Handle) -> Result<(u32, u32), IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::IoPort { return Err(IpcError::WrongType); }
        if cap.rights & (rights::READ | rights::WRITE) != (rights::READ | rights::WRITE) { return Err(IpcError::NoRight); }
        Ok((cap.lo, cap.hi))
    }

    /// KERNEL-ONLY bootstrap: gives `to` the Input capability (READ = may poll events, GRANT = may pass it on, attenuated).
    pub fn bootstrap_input(&mut self, to: ProcId, r: u8) -> Result<Handle, IpcError> {
        let cap = Capability { kind: Kind::Input, endpoint: 0, generation: 0, rights: r & (rights::READ | rights::GRANT), lo: 0, hi: 0, badge: 0 };
        Self::install(self.live(to)?, cap)
    }

    /// WrongType unless `h` is an Input capability; NoRight unless it carries READ.
    pub fn input_check(&mut self, p: ProcId, h: Handle) -> Result<(), IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::Input { return Err(IpcError::WrongType); }
        if cap.rights & rights::READ == 0 { return Err(IpcError::NoRight); }
        Ok(())
    }

    /// KERNEL-ONLY bootstrap: gives `to` the capability for ALL `rows` rows of the framebuffer.
    pub fn bootstrap_fb(&mut self, to: ProcId, r: u8, rows: u32) -> Result<Handle, IpcError> {
        let cap = Capability { kind: Kind::FrameBuffer, endpoint: 0, generation: 0, rights: r & (rights::READ | rights::WRITE | rights::GRANT), lo: 0, hi: rows, badge: 0 };
        Self::install(self.live(to)?, cap)
    }

    /// Resolves `p`'s handle `h` to (row window [lo, hi), rights); WrongType unless it is a framebuffer capability.
    pub fn fb_window(&mut self, p: ProcId, h: Handle) -> Result<(u32, u32, u8), IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::FrameBuffer { return Err(IpcError::WrongType); }
        Ok((cap.lo, cap.hi, cap.rights))
    }

    pub fn bootstrap_device(&mut self, to: ProcId, r: u8) -> Result<Handle, IpcError> {
        let cap = Capability { kind: Kind::BlockDevice, endpoint: 0, generation: 0, rights: r & (rights::READ | rights::WRITE | rights::GRANT), lo: 0, hi: u32::MAX, badge: 0 };
        Self::install(self.live(to)?, cap)
    }

    /// KERNEL-ONLY: gives `to` a capability to shared-memory region `region`.
    pub fn bootstrap_region(&mut self, to: ProcId, region: u32, r: u8) -> Result<Handle, IpcError> {
        let cap = Capability { kind: Kind::SharedMem, endpoint: region, generation: 0, rights: r & (rights::READ | rights::WRITE | rights::GRANT), lo: 0, hi: 0, badge: 0 };
        let h = Self::install(self.live(to)?, cap)?;
        self.ref_inc(&cap);
        Ok(h)
    }

    /// Resolves `p`'s handle `h` to (region id, rights); WrongType if it is not a region capability.
    pub fn region_cap(&mut self, p: ProcId, h: Handle) -> Result<(u32, u8), IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::SharedMem { return Err(IpcError::WrongType); }
        Ok((cap.endpoint, cap.rights))
    }

    /// Checks that `p`'s handle `h` is a block-device capability carrying `need`.
    /// On success returns the capability's sector window [lo, hi).
    pub fn check_device(&mut self, p: ProcId, h: Handle, need: u8) -> Result<(u32, u32), IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::BlockDevice { return Err(IpcError::WrongType); }
        if cap.rights & need != need { return Err(IpcError::NoRight); }
        Ok((cap.lo, cap.hi))
    }

    /// Derives, in `p`'s own table, a block-device capability covering [lo, lo+len) which must
    /// lie INSIDE the window of `h` (attenuation only: anything else, including an empty or
    /// overflowing window, is refused with Widening).
    /// Derive a BADGED copy of an endpoint capability (needs GRANT; the source must be unbadged: a badge can never be
    /// changed or removed, only the holder of the unbadged capability can mint one). `badge` must be non-zero.
    pub fn badge(&mut self, p: ProcId, h: Handle, badge: u32) -> Result<Handle, IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::Endpoint { return Err(IpcError::WrongType); }
        if cap.rights & rights::GRANT == 0 { return Err(IpcError::NoRight); }
        if badge == 0 || cap.badge != 0 { return Err(IpcError::NotOwner); }
        let tep = self.endpoints.get(cap.endpoint as usize).ok_or(IpcError::BadHandle)?;
        if tep.generation != cap.generation { return Err(IpcError::Revoked); }
        Self::install(self.live(p)?, Capability { badge, ..cap })
    }

    pub fn narrow_device(&mut self, p: ProcId, h: Handle, lo: u64, len: u64) -> Result<Handle, IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::BlockDevice && cap.kind != Kind::IoPort && cap.kind != Kind::FrameBuffer { return Err(IpcError::WrongType); }
        let hi = lo.checked_add(len).ok_or(IpcError::Widening)?;
        if len == 0 || lo < cap.lo as u64 || hi > cap.hi as u64 { return Err(IpcError::Widening); }
        let narrowed = Capability { lo: lo as u32, hi: hi as u32, ..cap };
        Self::install(self.live(p)?, narrowed)
    }

    /// Non-blocking send of a bounded payload through capability `h`,
    /// optionally transferring (an attenuated copy of) capability
    /// `transfer.0` with at most rights `transfer.1`.
    pub fn send(
        &mut self,
        from: ProcId,
        h: Handle,
        payload: &[u8],
        transfer: Option<(Handle, u8)>,
    ) -> Result<(), IpcError> {
        let cap = self.lookup(from, h)?;
        if cap.kind != Kind::Endpoint { return Err(IpcError::WrongType); }
        if cap.rights & rights::SEND == 0 { return Err(IpcError::NoRight); }
        if payload.len() > MAX_PAYLOAD { return Err(IpcError::PayloadTooLarge); }

        // validate the transfer BEFORE touching any queue
        let moved = match transfer {
            None => None,
            Some((th, want)) => {
                let held = self.lookup(from, th)?;
                if held.rights & rights::GRANT == 0 { return Err(IpcError::NoRight); }
                if want & !held.rights != 0 { return Err(IpcError::Widening); }
                // an endpoint capability being transferred must itself still be valid
                if held.kind == Kind::Endpoint {
                    let tep = self.endpoints.get(held.endpoint as usize).ok_or(IpcError::BadHandle)?;
                    if tep.generation != held.generation { return Err(IpcError::Revoked); }
                }
                Some(Capability { rights: want, ..held })
            }
        };

        if let (Some(c), Some(ep)) = (moved, self.endpoints.get(cap.endpoint as usize)) {
            if !self.admits(ep.owner, c.kind) { return Err(IpcError::Contained); } // the receiver's container forbids this kind
        }
        let e = self.endpoints.get_mut(cap.endpoint as usize).ok_or(IpcError::BadHandle)?;
        if !e.alive { return Err(IpcError::TargetDead); }
        if e.generation != cap.generation { return Err(IpcError::Revoked); }
        if e.queue.len() >= QUEUE_CAPACITY { return Err(IpcError::QueueFull); }
        e.queue.push_back(Queued { sender: from, payload: payload.to_vec(), cap: moved, badge: cap.badge });
        if let Some(c) = moved { self.ref_inc(&c); } // the in-flight copy is a holder too
        Ok(())
    }

    /// NPS-003 §3 `notify`: asynchronous, PAYLOAD-FREE signal. Needs a valid Endpoint capability with SEND (same checks as `send`: type, right, liveness, generation) and
    /// ORs `bits` into the endpoint's notification word. No queue slot is used, so it cannot return QueueFull and cannot be used to flood the message queue.
    pub fn notify(&mut self, from: ProcId, h: Handle, bits: u64) -> Result<(), IpcError> {
        let cap = self.lookup(from, h)?;
        if cap.kind != Kind::Endpoint { return Err(IpcError::WrongType); }
        if cap.rights & rights::SEND == 0 { return Err(IpcError::NoRight); }
        let e = self.endpoints.get_mut(cap.endpoint as usize).ok_or(IpcError::BadHandle)?;
        if !e.alive { return Err(IpcError::TargetDead); }
        if e.generation != cap.generation { return Err(IpcError::Revoked); }
        e.notify |= bits;
        Ok(())
    }
    /// The owner collects (and clears) its endpoint's notification word.
    pub fn take_notify(&mut self, ep: EndpointId) -> u64 {
        self.endpoints.get_mut(ep as usize).map(|e| core::mem::replace(&mut e.notify, 0)).unwrap_or(0)
    }
    pub fn notify_pending(&self, ep: EndpointId) -> bool {
        self.endpoints.get(ep as usize).map_or(false, |e| e.notify != 0)
    }

    /// CALL half of call/reply: like `send` (no capability transfer), but the queued
    /// message carries a one-shot REPLY capability naming the caller; the receiver gets
    /// it in the usual dedicated transfer field. Returns the endpoint owner (the only
    /// process that can answer) so the kernel knows whom the caller now waits on.
    pub fn call_send(&mut self, from: ProcId, h: Handle, payload: &[u8]) -> Result<ProcId, IpcError> {
        let cap = self.lookup(from, h)?;
        if cap.kind != Kind::Endpoint { return Err(IpcError::WrongType); }
        if cap.rights & rights::SEND == 0 { return Err(IpcError::NoRight); }
        if payload.len() > MAX_PAYLOAD { return Err(IpcError::PayloadTooLarge); }
        if let Some(ep) = self.endpoints.get(cap.endpoint as usize) {
            if !self.admits(ep.owner, Kind::Reply) { return Err(IpcError::Contained); } // a server in a container without Reply cannot be called
        }
        let e = self.endpoints.get_mut(cap.endpoint as usize).ok_or(IpcError::BadHandle)?;
        if !e.alive { return Err(IpcError::TargetDead); }
        if e.generation != cap.generation { return Err(IpcError::Revoked); }
        if e.queue.len() >= QUEUE_CAPACITY { return Err(IpcError::QueueFull); }
        let owner = e.owner;
        // no GRANT right: a reply capability can never be passed on
        let reply = Capability { kind: Kind::Reply, endpoint: from, generation: 0, rights: rights::SEND, lo: 0, hi: 0, badge: 0 };
        e.queue.push_back(Queued { sender: from, payload: payload.to_vec(), cap: Some(reply), badge: cap.badge });
        Ok(owner)
    }

    /// Who `p`'s handle `h` would answer; WrongType unless it is a reply capability.
    pub fn reply_target(&mut self, p: ProcId, h: Handle) -> Result<ProcId, IpcError> {
        let cap = self.lookup(p, h)?;
        if cap.kind != Kind::Reply { return Err(IpcError::WrongType); }
        Ok(cap.endpoint)
    }

    /// Consumes (empties) `p`'s slot `h`: a reply capability is single-use.
    pub fn drop_handle(&mut self, p: ProcId, h: Handle) {
        let gone = match self.live(p) {
            Ok(proc) => proc.caps.get_mut(h as usize).and_then(|slot| slot.take()),
            Err(_) => None,
        };
        if let Some(c) = gone { self.ref_dec(&c); }
    }

    /// Non-blocking receive by the endpoint's owner. A transferred
    /// capability is installed in the owner's table (the message is only
    /// consumed if that succeeds).
    pub fn receive(&mut self, owner: ProcId, ep: EndpointId) -> Result<Option<Message>, IpcError> {
        self.live(owner)?;
        let e = self.endpoints.get(ep as usize).ok_or(IpcError::BadHandle)?;
        if e.owner != owner { return Err(IpcError::NotOwner); }
        if !e.alive { return Err(IpcError::TargetDead); } // §7.2: deterministic failure
        let needs_slot = e.queue.front().map(|q| q.cap.is_some()).unwrap_or(false);
        if needs_slot && self.procs[owner as usize].caps.iter().all(|c| c.is_some()) {
            return Err(IpcError::TableFull);
        }
        let q = match self.endpoints[ep as usize].queue.pop_front() {
            Some(q) => q,
            None => return Ok(None),
        };
        let transferred = match q.cap {
            Some(c) => Some(Self::install(&mut self.procs[owner as usize], c)?),
            None => None,
        };
        Ok(Some(Message { sender: q.sender, payload: q.payload, transferred, badge: q.badge }))
    }
}

/// Boot-time self-test: each clause is exercised, positive and negative.
pub fn run() -> bool {
    use crate::serial_println;
    let mut failed: Vec<&'static str> = Vec::new();
    let mut check = |name: &'static str, cond: bool| {
        if !cond { failed.push(name); }
    };

    let mut k = CapSystem::new();
    let server = k.create_process();
    let client = k.create_process();
    let intruder = k.create_process();
    let (ep, srv_h) = k.create_endpoint(server).unwrap();

    // -- basics: owner send/receive, bounds, backpressure --
    check("owner send", k.send(server, srv_h, b"hello", None).is_ok());
    let m = k.receive(server, ep).unwrap().unwrap();
    check("owner receive", m.payload == b"hello" && m.sender == server && m.transferred.is_none());
    check("empty receive", k.receive(server, ep).unwrap().is_none());
    let big = [0u8; MAX_PAYLOAD + 1];
    check("payload bound", k.send(server, srv_h, &big, None) == Err(IpcError::PayloadTooLarge));
    check("max payload ok", k.send(server, srv_h, &big[..MAX_PAYLOAD], None).is_ok());
    let _ = k.receive(server, ep);
    for _ in 0..QUEUE_CAPACITY { check("fill", k.send(server, srv_h, b"q", None).is_ok()); }
    check("queue full reported", k.send(server, srv_h, b"q", None) == Err(IpcError::QueueFull));
    for _ in 0..QUEUE_CAPACITY { let _ = k.receive(server, ep); }

    // -- unforgeability / ownership --
    check("no cap -> bad handle", k.send(client, 0, b"x", None) == Err(IpcError::BadHandle));
    check("forged index rejected", k.send(intruder, srv_h, b"x", None) == Err(IpcError::BadHandle));
    check("out-of-range handle", k.send(server, 999, b"x", None) == Err(IpcError::BadHandle));
    check("non-owner receive", k.receive(intruder, ep) == Err(IpcError::NotOwner));
    check("non-owner revoke", k.revoke(intruder, ep) == Err(IpcError::NotOwner));
    check("non-owner mint", k.mint(intruder, ep, rights::SEND) == Err(IpcError::NotOwner));

    // -- rights: a capability without SEND cannot send --
    let nosend = k.bootstrap_grant(client, ep, 0).unwrap();
    check("no SEND right", k.send(client, nosend, b"x", None) == Err(IpcError::NoRight));

    // -- bootstrap + attenuated transfer --
    // init-like: client gets SEND|GRANT to the server's endpoint
    let cli_to_srv = k.bootstrap_grant(client, ep, rights::SEND | rights::GRANT).unwrap();
    check("client send", k.send(client, cli_to_srv, b"req", None).is_ok());
    let m = k.receive(server, ep).unwrap().unwrap();
    check("server got client msg", m.sender == client && m.payload == b"req");

    // server needs a way to reply: client owns a reply endpoint and sends the
    // server an attenuated SEND-only cap to it.
    let (reply_ep, cli_reply_h) = k.create_endpoint(client).unwrap();
    check("widening refused", k.send(client, cli_to_srv, b"x", Some((cli_reply_h, rights::SEND | rights::GRANT | 4))) == Err(IpcError::Widening));
    check("transfer needs GRANT", {
        let weak = k.mint(client, reply_ep, rights::SEND).unwrap(); // SEND only
        k.send(client, cli_to_srv, b"x", Some((weak, rights::SEND))) == Err(IpcError::NoRight)
    });
    check("transfer of non-held handle", k.send(client, cli_to_srv, b"x", Some((7, rights::SEND))) == Err(IpcError::BadHandle));
    check("failed transfers queued nothing", k.receive(server, ep).unwrap().is_none());
    check("attenuated transfer", k.send(client, cli_to_srv, b"reply-cap", Some((cli_reply_h, rights::SEND))).is_ok());
    let m = k.receive(server, ep).unwrap().unwrap();
    let got = m.transferred;
    check("cap delivered in dedicated field", got.is_some() && m.payload == b"reply-cap");
    let reply_h = got.unwrap_or(0);
    check("server can reply", k.send(server, reply_h, b"pong", None).is_ok());
    let r = k.receive(client, reply_ep).unwrap().unwrap();
    check("client got reply", r.payload == b"pong" && r.sender == server);
    // attenuation held: server's copy lacks GRANT, so it cannot pass it on
    check("attenuated cap cannot be re-granted",
        k.send(server, srv_h, b"x", Some((reply_h, rights::SEND))) == Err(IpcError::NoRight));

    // -- revocation without holder cooperation (§4.3) --
    check("revoke", k.revoke(client, reply_ep).is_ok());
    check("holder's cap now stale", k.send(server, reply_h, b"late", None) == Err(IpcError::Revoked));
    let fresh = k.mint(client, reply_ep, rights::SEND).unwrap();
    check("owner can mint after revoke", k.send(client, fresh, b"self", None).is_ok());
    check("old cap still stale", k.send(server, reply_h, b"late", None) == Err(IpcError::Revoked));

    // -- terminated target (§7.1/§7.2) --
    check("terminate", k.terminate_process(client).is_ok());
    check("send to dead owner", k.send(server, reply_h, b"x", None) == Err(IpcError::TargetDead));
    check("receive on dead endpoint", k.receive(client, reply_ep) == Err(IpcError::BadProcess));
    check("dead process cannot send", k.send(client, cli_to_srv, b"x", None) == Err(IpcError::BadProcess));

    // -- region lifetime = capability holders (in tables AND in flight in queues) --
    {
        let mut k = CapSystem::new();
        let a = k.create_process();
        let b = k.create_process();
        let c = k.create_process();
        let (ep_b, _) = k.create_endpoint(b).unwrap();
        let (_ep_c, c_self) = k.create_endpoint(c).unwrap();
        let ab = k.bootstrap_grant(a, ep_b, rights::SEND | rights::GRANT).unwrap();
        let ac = k.bootstrap_grant(a, _ep_c, rights::SEND | rights::GRANT).unwrap();
        let _ = c_self;
        let rh = k.bootstrap_region(a, 7, rights::READ | rights::WRITE | rights::GRANT).unwrap();
        check("region: one holder, nothing released", k.drain_released().is_empty());
        check("region: send to b (in flight)", k.send(a, ab, b"r", Some((rh, rights::READ))).is_ok());
        check("region: a exits, in-flight copy keeps it alive", { let _ = k.terminate_process(a); k.drain_released().is_empty() });
        let m = k.receive(b, ep_b).unwrap().unwrap();
        check("region: b now holds it", m.transferred.is_some() && k.drain_released().is_empty());
        let bh = m.transferred.unwrap_or(0);
        check("region: b exits -> last holder gone", { let _ = k.terminate_process(b); k.drain_released() == alloc::vec![7u32] });
        let _ = (bh, ac);
        // in-flight copy dies with a dead receiver's queue
        let mut k = CapSystem::new();
        let a = k.create_process();
        let b = k.create_process();
        let (ep_b, _) = k.create_endpoint(b).unwrap();
        let ab = k.bootstrap_grant(a, ep_b, rights::SEND | rights::GRANT).unwrap();
        let rh = k.bootstrap_region(a, 3, rights::READ | rights::GRANT).unwrap();
        let _ = k.send(a, ab, b"r", Some((rh, rights::READ)));
        let _ = k.terminate_process(a);
        check("region: queued copy alone keeps it", k.drain_released().is_empty());
        let _ = k.terminate_process(b);
        check("region: queue dies with receiver -> released", k.drain_released() == alloc::vec![3u32]);
    }

    let ok = failed.is_empty();
    for f in &failed { serial_println!("  experimental capability-demo: CHECK FAILED: {}", f); }
    serial_println!(
        "  experimental capability-demo self-test: unforgeable handles, rights, bounded payloads, backpressure, attenuation-only transfer, revocation, dead-target errors -- {}",
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
