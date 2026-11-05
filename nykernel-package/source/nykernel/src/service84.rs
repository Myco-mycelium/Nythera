//! The `0x84` service surface: the gfx and input calls the release's shell
//! stack uses, as much of them as this kernel can honestly serve today.
//!
//! What this is: a ring-3-callable gate (`int $0x84`, DPL 3, call number in
//! `rax`) in the release's five-argument ABI -- `rdi/rsi/rdx/r10/r8` all
//! reach `dispatch` (the arguments each row consumes are named in the
//! table), the result goes back in `rax` -- that serves ten calls:
//!
//! | nr | call | argument | returns |
//! |---|---|---|---|
//! | 0x40 | `INPUT_STATE` | — | `(x & 0xffff) | (y & 0xffff) << 16 | buttons << 32` |
//! | 0x41 | `GFX_MODE` | — | `(width << 32) | height` of the bound framebuffer |
//! | 0x42 | `GFX_MODE_INTO` | user pointer | writes `(u32 width, u32 height)` there; `0`, or `-EFAULT` |
//! | 0x43 | `PROOF_DONE` | user pointer | the ring-3 proof's read-back check (see below) |
//! | 0x44 | `PROCESS_SELF` | — | the running scheduler task's id (`experimental::scheduler`); `0` = the kernel itself |
//! | 0x45 | `IPC_SEND` | a0=endpoint, a1=seq | queues `(sender = current task, seq)` through `experimental::ipc`; `0`, or the arbiter's errno |
//! | 0x46 | `IPC_RECV` | a0=endpoint | drains one queued message -> `(sender << 32) | seq`; `-ENODATA` when real-and-empty, `-ENOENT` when it does not exist |
//! | 0x47 | `CAP_SEND` | a0=handle, a1/a2=payload, a_xfer/a_rights | capability-checked send through `experimental::caps`; `0`, or its errno |
//! | 0x48 | `CAP_RECV` | a0=endpoint, a1/a2=recv buffer | drains one message into user memory -> `(sender << 32) | len`; `-ENODATA` when real-and-empty |
//! | 0x49 | `INPUT_POLL` | a0=handle, a1=record ptr | authority-first Input-capability check, then the oldest RAW input byte -> 16-byte record; `1`, `0` when authorized-and-bare, its errno otherwise |
//!
//! The capability rows (0x47/0x48) are the release's ENDPOINT-capability
//! shape (NPS-003 §4/§5) served through THIS tree's own handle-table
//! machine (`experimental::caps`): a send goes through a handle in the
//! caller's OWN table -- never a raw endpoint number (§4.1), the payload
//! is a kernel-validated, kernel-COPIED user read bounded at
//! `caps::MAX_PAYLOAD`, a transfer travels only in the dedicated field
//! and only ATTENUATES (§5.2/§5.3), generations make revocation O(1)
//! and stale (§4.3), and every check happens before any queue is touched.
//! The caller's identity is its scheduler task; while the session machine
//! the manifest parks is not here, every un-sessioned ring-3 caller
//! shares task 0's table -- documented, not hidden (caps's module docs).
//!
//! The IPC rows are the release-typed SEND/RECV door, as far as THIS tree's
//! own primitive can honestly serve it: `ipc::send`/`try_receive` model the
//! message body as `(sender, seq)`, so a caller claiming a payload pointer
//! (`a2`), a transfer token other than the release's "no transfer"
//! (`a_xfer`) or rights (`a_rights`), or a `seq` wider than u32, claims a
//! shape this primitive does not own and is refused (`EINVAL`/`ERANGE`)
//! rather than served truncated. The blocking form (`a_xfer == 1` on RECV)
//! is refused `-ENOSYS` BY NAME: block-and-reschedule from a trap handler
//! needs the saved-trap session machine the manifest parks, and "not here
//! yet" must not be indistinguishable from "empty right now" (which is
//! `-ENODATA`, a real answer).
//!
//! The numbers are ALL OUR OWN, in `0x40..`, and they must be: the
//! release's ledger spans `0..28` contiguously (yield=0, SEND=1, RECV=2,
//! exit=3, spawn=4 ... SYSINFO=26, PROCINFO=27, PROC_KILL=28), and the
//! surface's first version numbered itself `0..4` -- colliding at EVERY
//! served number. A release-typed program's `yield` (0) would have been
//! ANSWERED with `INPUT_STATE` instead of refused with `-ENOSYS` -- the
//! exact lie the refusal convention exists to prevent, invisible until a
//! release-typed program actually ran through the gate. The step-3 shell
//! survey found it; `tools/check_svc84_ledger.py` pins both shapes --
//! disjoint from the release's ledger, and the proof exercises every
//! served number -- so neither can drift back into the other; it runs in
//! build.sh.
//!
//! and answers `-ENOSYS` for everything else. The port ledger for what that
//! "everything else" is: the release's full process machine -- the payload/
//! transfer/capability shape of SEND and the BLOCKING form of RECV,
//! capability rights and transfers, spawn, shared memory
//! (`porting/release-1-71/src/experimental/syscall_demo.rs` and its
//! `capability.rs`/`shm.rs`) -- is one connected surface, not five
//! independent calls: full SEND requires capability tables, blocking RECV
//! requires
//! block-and-reschedule with a saved trap, either requires a process
//! identity (which `PROCESS_SELF` now answers from this tree's own task
//! model, as the surface's first scheduler-backed process call) and
//! sessions the PORT_MANIFEST parks for its last steps. What those calls
//! need from THIS file is already here: the entry stub passes the full
//! five-argument shape to `dispatch` (`rsi/rdx/r10/r8` = payload, payload
//! length, transfer, rights, with the caller's CPL behind them), and the
//! ring-3 proof proves that pass through the trap boundary by before/after
//! values -- the missing pieces are the capability tables and the
//! sessions, not the registers. The IPC rows land the one slice of that
//! machine this tree already owns -- the `(sender, seq)` mailbox primitive
//! -- and refuse the shape claims it cannot own. A call that is not served
//! must say so: a
//! shell that silently receives zeroes from an unported service has no
//! way to tell "nothing happened" from "this kernel cannot do that".
//!
//! What this is NOT, stated as plainly as the manifest does: this is not
//! the release's `0x84` surface (118 KiB of capability/IPC/process/gfx/
//! input), and it does not make the shell a session. There is no
//! user-space process owning the display, no input delivery to a
//! foreground container, no capability-managed framebuffer window and no
//! capability-mediated IPC: the endpoint rows serve `experimental::ipc`'s own
//! `(sender, seq)` mailbox primitive, which is NOT the release's
//! payload/transfer/capability shape and refuses that shape rather than
//! answering it falsely. The calls here are the ones whose answers exist in this kernel: the
//! pointer is where the PS/2 driver says it is, the display is the mode
//! the framebuffer driver bound, and the INPUT_POLL row answers the raw
//! byte stream to the holder of the kernel-MINTED Input capability (task
//! 0's shared table, while the session machine waits) -- not a foreground
//! container, no routing, no decoded events.
//!
//! The pointer-taking call exists only because the user-range validator now
//! does: `GFX_MODE_INTO` refuses any address the active page tables do not
//! mark PRESENT|USER_ACCESSIBLE|WRITABLE (`memory::paging::user_range_ok`),
//! so a ring-3 caller cannot turn "give me the mode" into a kernel write
//! into arbitrary memory. The release's framebuffer *mapping* call -- which
//! hands a process a window onto the device -- is still unported: that is a
//! capability decision (which process may own the display), not an address
//! check.

use core::arch::naked_asm;
use core::sync::atomic::{AtomicI64, AtomicU32, AtomicU64, AtomicU8, AtomicUsize, Ordering};

use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::{PrivilegeLevel, VirtAddr};

/// The vector the release's shell stack calls.
pub const VECTOR: u8 = 0x84;

// All five numbers are the surface's OWN, in `0x40..`: the release's
// ledger spans `0..28` contiguously, so the first version's `0..4`
// collided at every served number (a release-typed program's `yield` = 0
// would have been answered with INPUT_STATE instead of refused -- see the
// module docs above). The ledger gate (tools/check_svc84_ledger.py) runs
// in build.sh and refuses a number that wanders back into the release's
// range.
pub const CALL_INPUT_STATE: u64 = 0x40;
pub const CALL_GFX_MODE: u64 = 0x41;
pub const CALL_GFX_MODE_INTO: u64 = 0x42;
pub const CALL_PROOF_DONE: u64 = 0x43;

/// The first process-side answer: the id of the `experimental::scheduler`
/// task running the caller, read-only (task 0 = the kernel itself). Today
/// every ring-3 caller is scheduler-unowned, so the honest answer is 0 --
/// served rather than refused because the release's shell protocol asks
/// "who am I" first, and answering a REAL id from our OWN task model is
/// truthful regardless of what kind of caller asks. The richer process
/// calls (spawn, capabilities, IPC) stay refused: see the ledger above.
pub const CALL_PROCESS_SELF: u64 = 0x44;

/// The release-typed IPC door, served through THIS tree's own mailbox
/// primitive (`experimental::ipc`), not the release's capability tables:
/// the message body is the primitive's `(sender, seq)`, and the
/// payload-pointer / transfer-token / rights parts of the release's shape
/// are refused (`EINVAL`) rather than silently dropped -- serving a shape
/// you do not own by eating half of it is a lie, not a shortcut.
pub const CALL_IPC_SEND: u64 = 0x45;
pub const CALL_IPC_RECV: u64 = 0x46;

/// The capability-shaped door (NPS-003 §4/§5), served through
/// `experimental::caps` -- per-holder kernel-owned handle tables over
/// endpoint capabilities. The release's shape: a0 = HANDLE (an index in
/// the caller's OWN table; raw endpoint numbers are never taken on the
/// send path, §3.2/§4.1), a1/a2 = payload pointer/length (a kernel-validated
/// read from user memory, bounded at caps::MAX_PAYLOAD), a_xfer = the
/// DEDICATED transfer field (u64::MAX = no transfer; else a handle being
/// transferred attenuated to at most a_rights), only ever narrowing.
/// Identity note: the caller's table is its GATE SESSION's table
/// (`attach_gate_session` -- the session machine's first slice), or, with
/// no session attached, task 0's (the kernel itself: ring-0 callers only).
/// See caps's module docs.
pub const CALL_CAP_SEND: u64 = 0x47;
pub const CALL_CAP_RECV: u64 = 0x48;

/// The release's INPUT_POLL (its ledger's call 22), served through the
/// INPUT capability kind (`experimental::caps::Kind::Input`) and the
/// kernel's raw input byte ring (`experimental::input`): a0 = HANDLE (an
/// index in the caller's OWN table -- same rules as the other capability
/// rows), a1 = pointer to the 16-byte result record the kernel writes
/// under its user-range validator: {byte u8, 2 x zeros, device u8
/// (0 keyboard, 1 mouse), 4 x zeros, ticks u64 LE}. The kernel does NOT
/// decode -- the byte is exactly as the controller delivered it. Returns
/// 1 when a byte was popped, 0 when the caller is AUTHORIZED but the
/// queue is bare (a real answer, not a refusal -- the release's shape).
/// AUTHORITY-FIRST, the release's ordering: the capability is checked
/// BEFORE any event is consumed, so a refused poll can never eat a byte.
/// Identity: the caller's GATE SESSION (first-slice session machine), or
/// task 0 with no session attached.
pub const CALL_INPUT_POLL: u64 = 0x49;

/// Linux's `ENOSYS`, and the release's convention: negative errno in `rax`.
pub const ENOSYS: i64 = -38;
/// Bad user pointer: the validator refused the range.
pub const EFAULT: i64 = -14;
/// Linux `EINVAL`: the call's SHAPE claims something this primitive does
/// not own (a payload pointer, rights, a transfer token on SEND). Refused,
/// never half-served.
pub const EINVAL: i64 = -22;
/// Linux `ERANGE`: `seq` is a u32 in the primitive's message body; a wider
/// value is refused rather than truncated.
pub const ERANGE: i64 = -34;
/// Linux `ENOENT`: the endpoint does not exist (distinct from ENODATA).
pub const ENOENT: i64 = -2;
/// Linux `ENODATA`: the endpoint exists and is empty. Distinguishable from
/// ENOENT precisely because the surface refuses the nonexistent one and
/// answers the empty one.
pub const ENODATA: i64 = -61;
/// Linux `EPERM`: the arbiter refused the send's sender claim.
pub const EPERM: i64 = -1;
/// Linux `EAGAIN`: endpoint back pressure (the primitive's "backpressure,
/// not data loss" MailboxFull -- the caller retries, nothing is dropped).
pub const EAGAIN: i64 = -11;
/// Linux `EACCES`: this sender's access to the endpoint is revoked (the
/// primitive's §4.3 revocation cut).
pub const EACCES: i64 = -13;
/// Linux `EBADF`: the handle (table index) is an empty slot -- not
/// forgeable, just invalid.
pub const EBADF: i64 = -9;
/// Linux `ESRCH`: no such process / a dead endpoint owner.
pub const ESRCH: i64 = -3;
/// Linux `ESTALE`: the capability's generation is stale (§4.3 revocation
/// went through under it).
pub const ESTALE: i64 = -116;
/// Linux `EMSGSIZE`: the payload exceeds the machine's bound, or the
/// receive buffer cannot hold the queued message.
pub const EMSGSIZE: i64 = -90;
/// Linux `ENOSPC`: the holder's capability table is full.
pub const ENOSPC: i64 = -28;

fn map_cap_error(e: crate::experimental::caps::CapError) -> i64 {
    use crate::experimental::caps::CapError::*;
    match e {
        BadProcess | TargetDead => ESRCH,
        BadHandle => EBADF,
        // A handle resolved but named a DIFFERENT kind of object: that is
        // an authorization failure at the type level (the release maps
        // its WrongType to EACCES-family refusals; EACCES by name),
        // distinct from EBADF's "no such handle".
        WrongType => EACCES,
        NoRight | Revoked | NotOwner => EACCES,
        PayloadTooLarge | Widening => EMSGSIZE,
        QueueFull => EAGAIN,
        TableFull => ENOSPC,
    }
}


// The gate's session machine, first slice, store moved HOME: the
// release's sessions are scheduler state, so the store lives in
// `experimental::scheduler` and is recorded at the scheduler's ring-3
// task switches (`ring3_scheduler` attaches E/F/G and the preemption
// hogs' sessions at every switch, withdraws at the ring-0 resumes).
// This is the gate-side NAME of the transition; identity on the rows
// reads `scheduler::session_identity()`.

/// Kernel-only, called ONCE at the ring-3 transition by the proof's
/// equip path: from here on, the capability rows' and the INPUT_POLL
/// row's caller identity is session `sid`'s OWN capability table.
pub fn attach_gate_session(sid: usize) {
    crate::experimental::scheduler::attach_session(sid);
}


// ---- proof bookkeeping -------------------------------------------------
//
// Set only when the caller was ring 3: the boot self-test calls `dispatch`
// directly from ring 0, and those calls must not be mistaken for the
// privilege-transition proof.

static CPL3_CALLS: AtomicU32 = AtomicU32::new(0);
static LAST_CALLER_CPL: AtomicU64 = AtomicU64::new(0xFF);
static RET_GFX: AtomicI64 = AtomicI64::new(0);
static RET_INPUT: AtomicI64 = AtomicI64::new(0);
static RET_ENOSYS: AtomicI64 = AtomicI64::new(0);
/// The widened ABI's differential check: the caller's `rsi/rdx/r10/r8` as
/// the dispatcher actually received them, recorded ONLY on a ring-3 caller
/// of an UNPORTED number (the proof's one junk-arg call). PROOF_DONE
/// compares these against copies the program stored in its own buffer,
/// so the stub's widened pass is verified across the trap boundary and
/// not by reading the assembly and nodding.
static JUNK_ARG_RSI: AtomicU64 = AtomicU64::new(0);
static JUNK_ARG_RDX: AtomicU64 = AtomicU64::new(0);
static JUNK_ARG_R10: AtomicU64 = AtomicU64::new(0);
static JUNK_ARG_R8: AtomicU64 = AtomicU64::new(0);
static WROTE_W: AtomicU32 = AtomicU32::new(0);
static WROTE_H: AtomicU32 = AtomicU32::new(0);
/// What the IPC arms ACTUALLY sent (queue side) and what the drain
/// RETURNED (unpack side), recorded only for ring-3 calls of the good
/// shapes. PROOF_DONE compares the program's stored RECV result against
/// the record, so the round trip through kernel memory and back across
/// the trap boundary is verified with values, not constants.
static IPC_SENT_DEST: AtomicU64 = AtomicU64::new(0);
static IPC_SENT_SEQ: AtomicU64 = AtomicU64::new(0);
static RECVD_VALUE: AtomicU64 = AtomicU64::new(0);
/// The capability leg's differential records: what the drain actually
/// handed back BEYOND what rax shows -- the received payload's first 8
/// bytes (zero-padded) and the packed (sender<<32|len) return. The
/// program's stored copies and its own memory are compared against THESE.
static CAP_RECV_FIRST8: AtomicU64 = AtomicU64::new(0);
static CAP_RECV_RAX: AtomicU64 = AtomicU64::new(0);
/// 0 = not finished, 1 = passed, 2 = failed.
static PROOF_STATE: AtomicU8 = AtomicU8::new(0);

pub fn proof_state() -> u8 {
    PROOF_STATE.load(Ordering::Relaxed)
}

/// Packs the pointer state into the return register. Clamped rather than
/// wrapped: a coordinate past 65535 is a driver bug, and silently
/// truncating it would put the pointer somewhere it never was.
fn pack_pointer() -> i64 {
    let f = crate::ps2_aux::facts();
    let (px, py) = crate::cursor::last_position();
    let px = px.clamp(0, 0xffff) as u64;
    let py = py.clamp(0, 0xffff) as u64;
    let buttons = (f.last.buttons & 0xff) as u64;
    (px | (py << 16) | (buttons << 32)) as i64
}

fn pack_gfx() -> i64 {
    match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => ((w as i64) << 32) | (h as i64),
        None => ENOSYS,
    }
}

/// The dispatcher. `cpl` is the caller's privilege level, read from the
/// saved CS by the entry stub -- without it, a kernel-side call and a
/// ring-3 call look identical, and the proof would be worthless. The
/// release's five-argument shape arrives whole: `a0..=a2` (`rdi/rsi/rdx`)
/// and `a_xfer`/`a_rights` (`r10/r8`) -- only `a0` is consumed by a served
/// call today, the rest by the ledger (nothing reads what was never
/// passed).
pub fn dispatch(nr: u64, a0: u64, a1: u64, a2: u64, a_xfer: u64, a_rights: u64, cpl: u64) -> i64 {
    let from_ring3 = cpl == 3;
    if from_ring3 {
        LAST_CALLER_CPL.store(cpl, Ordering::Relaxed);
        CPL3_CALLS.fetch_add(1, Ordering::Relaxed);
    }
    match nr {
        CALL_INPUT_STATE => {
            let v = pack_pointer();
            if from_ring3 {
                RET_INPUT.store(v, Ordering::Relaxed);
            }
            v
        }
        CALL_GFX_MODE => {
            let v = pack_gfx();
            if from_ring3 {
                RET_GFX.store(v, Ordering::Relaxed);
            }
            v
        }
        CALL_GFX_MODE_INTO => {
            // The validator decides. Nothing is written before it agrees.
            if !crate::memory::paging::user_range_ok(a0, 8, true) {
                return EFAULT;
            }
            let (w, h) = match crate::devices::framebuffer::bound_mode() {
                Some(m) => m,
                None => return ENOSYS,
            };
            unsafe {
                core::ptr::write_unaligned(a0 as *mut u32, w);
                core::ptr::write_unaligned((a0 + 4) as *mut u32, h);
            }
            if from_ring3 {
                WROTE_W.store(w, Ordering::Relaxed);
                WROTE_H.store(h, Ordering::Relaxed);
            }
            0
        }
        CALL_PROOF_DONE => {
            // Twenty-two qwords: the five older results (gfx, input, enosys,
            // (unused), self), the four widened-arg markers at BUF+40..+64,
            // the IPC records (ack BUF+72, recv BUF+80, shape refusal
            // BUF+88, empty-real BUF+96), and the capability records: the
            // CAP_SEND ack (BUF+104), the CAP_RECV rax (BUF+112), the
            // forged-handle refusal (BUF+120) and the oversize refusal
            // (BUF+128); and, of the INPUT_POLL row, the two positive
            // polls' acks (BUF+136, BUF+144), the third poll's real-and-
            // empty answer (BUF+152) and the forged-handle refusal
            // (BUF+168 -- STORED INDEX 21: index 20 is offset 160, the
            // payload staging area, and the proof's first run read the
            // payload bytes as "the forged answer" exactly that way). The CAP PAYLOAD is compared at BUF+192..+198 and
            // the INPUT POLLED BYTES at BUF+208/+224 (below).
            if !crate::memory::paging::user_range_ok(a0, 176, false) {
                return EFAULT;
            }
            // Read the results back OUT OF USER MEMORY: this is the part
            // the registers alone cannot prove, because it shows the
            // ring-3 program actually received and stored them.
            let mut stored = [0i64; 22];
            for (i, slot) in stored.iter_mut().enumerate() {
                *slot = unsafe { core::ptr::read_unaligned((a0 as *const i64).add(i)) };
            }
            let want_gfx = RET_GFX.load(Ordering::Relaxed);
            let want_input = RET_INPUT.load(Ordering::Relaxed);
            let want_enosys = RET_ENOSYS.load(Ordering::Relaxed);
            // The identity answer is compared against the scheduler's OWN
            // live value at read-back time, so a future session that makes
            // the program a real task changes both sides together.
            let want_self = crate::experimental::scheduler::current_task() as i64;
            // The widened pass is judged the same way as the answers:
            // against what the dispatcher ACTUALLY received from the ring-3
            // caller, not against constants compiled into this file.
            let want_rsi = JUNK_ARG_RSI.load(Ordering::Relaxed);
            let want_rdx = JUNK_ARG_RDX.load(Ordering::Relaxed);
            let want_r10 = JUNK_ARG_R10.load(Ordering::Relaxed);
            let want_r8 = JUNK_ARG_R8.load(Ordering::Relaxed);
            // The IPC round trip is judged against what the drain actually
            // unpacked (the queue-side and return-side record the same
            // message's two halves): the program's stored SEND ack must be
            // 0, its stored RECV result must equal THIS value (nonzero by
            // construction -- sender 0, seq 7), and the two shape refusals
            // must have arrived by name.
            let want_ipc = RECVD_VALUE.load(Ordering::Relaxed) as i64;
            // The capability leg is judged the same differential way: the
            // drain's actual answer (packed sender<<32|len, recorded when
            // the ring-3 recv ran), the drained payload's first 8 bytes,
            // and the payload bytes still in the program's buffer.
            let want_cap_rax = CAP_RECV_RAX.load(Ordering::Relaxed) as i64;
            let want_cap_first8 = CAP_RECV_FIRST8.load(Ordering::Relaxed);
            let wrote_ok = crate::devices::framebuffer::bound_mode()
                .map(|(w, h)| {
                    // The geometry was written at BUF+24 (the program's own
                    // offset, matching its assembly); the first version of
                    // this check read BUF+0 -- where GFX_MODE's low dword
                    // lives -- and reported a MISMATCH that was the check's
                    // own bug, not the call's.
                    let at = unsafe { (a0 + 24) as *const u32 };
                    unsafe {
                        core::ptr::read_unaligned(at) == w
                            && core::ptr::read_unaligned(at.add(1)) == h
                    }
                })
                .unwrap_or(true);
            // stored[3] is NOT the validated write's ack: BUF+24 is where
            // the write PUT the geometry (the first version of this check
            // read it as "== 0" and failed every boot with a verdict whose
            // per-field pieces all said matches). The write's side is
            // already covered by `wrote_ok`, which re-reads BUF+24+8.
            let ipc_ok = stored[9] == 0
                && stored[10] == want_ipc
                && want_ipc != 0
                && stored[11] == EINVAL
                && stored[12] == ENODATA; // exists-but-empty, a REAL answer
            // The capability leg: the ack, the round-trip rax, two
            // refusals by name (a forged handle and an oversize claim),
            // and the payload the program ACTUALLY holds matching both
            // the drained message's record and the "nyrqis" sentinel.
            // The sentinel compare is against the KERNEL's own image of
            // the payload (CAP_RECV_FIRST8), so queue -> user-memory is
            // covered with values, not constants.
            // 6 bytes, zero tail: "nyrqis" as the payload copy leaves it
            // in user memory -- an LE u64 read of [ny, rq, is, \0, \0] is
            // 0x0000_7369_7172_796E, so both sides of the compare are the
            // SAME representation.
            const CAP_SENTINEL: u64 = 0x0000_7369_7172_796E;
            let held_first8 = if crate::memory::paging::user_range_ok(a0 + 192, 8, false) {
                unsafe { core::ptr::read_unaligned((a0 + 192) as *const u64) }
            } else {
                u64::MAX
            };
            let cap_ok = stored[13] == 0
                && stored[14] == want_cap_rax
                && want_cap_rax != 0
                && stored[15] == EBADF
                && stored[16] == EMSGSIZE
                && held_first8 == want_cap_first8
                && want_cap_first8 == CAP_SENTINEL;
            // The INPUT_POLL leg, judged against the kernel's OWN bytes
            // (svc84_proof::INJECT_BYTES -- what the kernel injected
            // through the controller before ring 3), so the polled
            // records' compare shares one
            // representation on both sides: the record's low byte is the
            // raw byte, and byte 3 is the device tag (0 = keyboard).
            let expect_bytes = crate::svc84_proof::INJECT_BYTES;
            let polled_first8 = if crate::memory::paging::user_range_ok(a0 + 208, 8, false) {
                unsafe { core::ptr::read_unaligned((a0 + 208) as *const u64) }
            } else {
                u64::MAX
            };
            let polled_second8 = if crate::memory::paging::user_range_ok(a0 + 224, 8, false) {
                unsafe { core::ptr::read_unaligned((a0 + 224) as *const u64) }
            } else {
                u64::MAX
            };
            let polled_empty8 = if crate::memory::paging::user_range_ok(a0 + 240, 8, false) {
                unsafe { core::ptr::read_unaligned((a0 + 240) as *const u64) }
            } else {
                u64::MAX
            };
            let input_poll_ok = stored[17] == 1
                && stored[18] == 1
                && stored[19] == 0 // authorized AND bare: the real answer
                && stored[21] == EBADF
                && polled_first8 & 0xFF == expect_bytes[0] as u64
                && polled_first8 >> 24 & 0xFF == 0 // device = keyboard
                && polled_second8 & 0xFF == expect_bytes[1] as u64
                && polled_second8 >> 24 & 0xFF == 0
                // ...and the empty path wrote NOTHING: the empty record
                // buffer the program zeroed at BUF+240 must still be
                // all-zero (a poll that "answered empty" but scribbled
                // would be a lie).
                && polled_empty8 == 0;
            let stored_ok = stored[0] == want_gfx
                && stored[1] == want_input
                && stored[2] == want_enosys
                && stored[2] == ENOSYS
                && stored[4] == want_self
                && stored[5] == want_rsi as i64
                && stored[6] == want_rdx as i64
                && stored[7] == want_r10 as i64
                && stored[8] == want_r8 as i64
                && ipc_ok
                && cap_ok
                && input_poll_ok;
            let ok = stored_ok && wrote_ok;
            PROOF_STATE.store(if ok { 1 } else { 2 }, Ordering::Relaxed);
            crate::serial_println!(                "  service-0x84 proof: a ring-3 program called int 0x84 from CPL {} -- GFX_MODE {:#x} , INPUT_STATE {:#x} , unported call {:#x} (refused, not answered) , validated write ({},{}) , process SELF {} , wide-args {:#x} {:#x} {:#x} {:#x} ,ipc round trip (send-ack {:#x} , recv {:#x} , want {:#x} , shape {} , empty {}) , caps (ack {:#x} , recv {:#x} , forged {:#x} , oversized {:#x} , payload {:#x}) , read back through the validator: GFX {} , INPUT {} , ENOSYS {} , geometry {} , SELF {} , ARGS {} , IPC {} , CAP {} , INPUT_POLL (1 {:#x} , 2 {:#x} , 3 {:#x} , forged {:#x} , bytes {:#x}/{:#x}) , INPUT {} -- {}
",
                LAST_CALLER_CPL.load(Ordering::Relaxed),
                want_gfx as u64,
                want_input as u64,
                want_enosys as u64,
                WROTE_W.load(Ordering::Relaxed),
                WROTE_H.load(Ordering::Relaxed),
                want_self as u64,
                want_rsi,
                want_rdx,
                want_r10,
                want_r8,
                stored[9] as u64,
                stored[10] as u64,
                want_ipc as u64,
                stored[11] as u64,
                stored[12] as u64,
                stored[13] as u64,
                stored[14] as u64,
                stored[15] as u64,
                stored[16] as u64,
                held_first8,
                if stored[0] == want_gfx { "matches" } else { "MISMATCH" },
                if stored[1] == want_input { "matches" } else { "MISMATCH" },
                if stored[2] == want_enosys { "matches" } else { "MISMATCH" },
                if wrote_ok { "matches" } else { "MISMATCH" },
                if stored[4] == want_self { "matches" } else { "MISMATCH" },
                if stored[5] == want_rsi as i64
                    && stored[6] == want_rdx as i64
                    && stored[7] == want_r10 as i64
                    && stored[8] == want_r8 as i64
                {
                    "matches"
                } else {
                    "MISMATCH"
                },
                if ipc_ok { "matches" } else { "MISMATCH" },
                if cap_ok { "matches" } else { "MISMATCH" },
                stored[17] as u64,
                stored[18] as u64,
                stored[19] as u64,
                stored[21] as u64,
                polled_first8,
                polled_second8,
                if input_poll_ok { "matches" } else { "MISMATCH" },
                if ok { "OK" } else { "FAILED" }
            );
            0
        }
        CALL_PROCESS_SELF => {
            // The scheduler's own answer, not a canned one: when a real
            // scheduler task ever owns a ring-3 caller, this call starts
            // naming it without this file changing. No bookkeeping beyond
            // what the proof's read-back uses -- identity is not a resource.
            crate::experimental::scheduler::current_task() as i64
        }
        CALL_IPC_SEND => {
            // The release's SEND shape arrives with a payload pointer
            // (a2), a transfer token (a_xfer) and rights (a_rights); the
            // primitive this serves models messages as (sender, seq)
            // ONLY, so all of those must claim "none" here and the seq
            // must fit the primitive's u32. Refusing the shape claims is
            // what makes "served" an HONEST word on this row: a gate that
            // accepted them and sent nothing but the seq would answer a
            // message the other end never receives.
            if a2 != 0 || a_rights != 0 {
                return EINVAL;
            }
            if a_xfer != u64::MAX {
                return EINVAL;
            } // the release's own "no transfer" claim
            if a1 > u32::MAX as u64 {
                return ERANGE;
            }
            let dest = a0 as usize;
            // The arbiter (ipc::send) demands the sender claim name the
            // kernel-declared caller identity; this gate supplies the
            // scheduler's OWN answer -- the recorded gate session at a
            // ring-3 transition, the running task otherwise -- so a
            // ring-3 caller cannot forge it.
            let msg = crate::experimental::ipc::IpcMessage {
                sender: crate::experimental::scheduler::session_identity(),
                seq: a1 as u32,
            };
            let r = crate::experimental::ipc::send(dest, msg);
            if from_ring3 {
                IPC_SENT_DEST.store(dest as u64, Ordering::Relaxed);
                IPC_SENT_SEQ.store(msg.seq as u64, Ordering::Relaxed);
            }
            match r {
                Ok(()) => 0,
                Err(crate::experimental::ipc::IpcError::NotRunningTask) => EPERM,
                Err(crate::experimental::ipc::IpcError::Revoked) => EACCES,
                Err(crate::experimental::ipc::IpcError::MailboxFull) => EAGAIN,
                Err(crate::experimental::ipc::IpcError::NoSuchMailbox) => ENOENT,
                Err(crate::experimental::ipc::IpcError::ReceiveTimedOut) => EINVAL,
            }
        }
        CALL_IPC_RECV => {
            if a2 != 0 || a_rights != 0 {
                return EINVAL;
            }
            if a1 != 0 {
                return EINVAL;
            }
            match a_xfer {
                0 => {}
                // The release-typed BLOCKING form. Refused BY NAME (see the
                // module docs): serving it needs the session machine, and
                // answering it with ENODATA would let a caller spin believing
                // "not here yet" meant "empty".
                1 => return ENOSYS,
                _ => return EINVAL,
            }
            // A nonexistent endpoint is refused, not answered as empty:
            // try_receive returns None for both, and the two differ.
            let dest = a0 as usize;
            if dest >= crate::experimental::ipc::endpoint_count() {
                return ENOENT;
            }
            match crate::experimental::ipc::try_receive(dest) {
                Some(m) => {
                    let v = ((m.sender as u64) << 32) | m.seq as u64;
                    if from_ring3 {
                        RECVD_VALUE.store(v, Ordering::Relaxed);
                    }
                    v as i64
                }
                None => ENODATA,
            }
        }
        CALL_CAP_SEND => {
            // The capability rows' identity is the caller's GATE SESSION
            // (kernel-recorded at the ring transition -- the session
            // machine's first slice); with no session, task 0 (the
            // kernel itself). The payload is COPIED out
            // of user memory here -- the queues hold kernel bytes only, so
            // a stale payload pointer can never be dereferenced at receive
            // time -- and every check the machine makes happens BEFORE its
            // queue is touched (caps::send preserves the release's
            // oracle-proof ordering).
            if a2 > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            if a2 != 0 && !crate::memory::paging::user_range_ok(a1, a2 as usize, false) {
                return EFAULT;
            }
            // Handles are u32 table indexes; anything that would TRUNCATE
            // is not a valid handle claim. Endpoints' u8 right masks carry
            // exactly SEND|GRANT today, so a wider rights claim is not a
            // handle either.
            if a0 > u32::MAX as u64 || a_rights > (crate::experimental::caps::rights::SEND | crate::experimental::caps::rights::GRANT) as u64 {
                return EBADF;
            }
            if a_xfer != u64::MAX && a_xfer > u32::MAX as u64 {
                return EBADF;
            }
            let mut bytes = [0u8; crate::experimental::caps::MAX_PAYLOAD];
            if a2 != 0 {
                unsafe {
                    core::ptr::copy_nonoverlapping(a1 as *const u8, bytes.as_mut_ptr(), a2 as usize);
                }
            }
            let transfer = if a_xfer == u64::MAX {
                if a_rights != 0 {
                    return EBADF; // rights only make sense WITH a transfer
                }
                None
            } else {
                Some((a_xfer as u32, a_rights as u8))
            };
            match crate::experimental::caps::send_for(
                crate::experimental::scheduler::session_identity(),
                a0 as u32,
                &bytes[..a2 as usize],
                transfer,
            ) {
                Ok(()) => 0,
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_RECV => {
            if a_rights != 0 || a_xfer != u64::MAX {
                return EBADF;
            }
            if a2 > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            if a2 != 0 && !crate::memory::paging::user_range_ok(a1, a2 as usize, true) {
                return EFAULT;
            }
            match crate::experimental::caps::receive_for(
                crate::experimental::scheduler::session_identity(),
                a0 as u32,
            ) {
                Ok(None) => ENODATA,
                Err(e) => map_cap_error(e),
                Ok(Some(m)) => {
                    if a2 < m.payload.len() as u64 {
                        // The message STAYS queued: a buffer too small is
                        // the caller's shape problem, not a consumed
                        // message's -- backpressure, not data loss.
                        return EMSGSIZE;
                    }
                    if !m.payload.is_empty() {
                        unsafe {
                            core::ptr::copy_nonoverlapping(
                                m.payload.as_ptr(),
                                a1 as *mut u8,
                                m.payload.len(),
                            );
                        }
                    }
                    if from_ring3 {
                        // The PAYLOAD RECORD little-endian, because the
                        // read-back compares it against a raw LE read of
                        // the program's buffer: comparing a big-endian
                        // packing against an LE memory read "works" only
                        // when every byte coincidentally aligns -- the
                        // first run of this compare MISMATCHED exactly
                        // that way (the bytes' order differed), which is
                        // the check's own bug, not the call's.
                        let mut first8 = [0u8; 8];
                        for (i, b) in m.payload.iter().take(8).enumerate() {
                            first8[i] = *b;
                        }
                        CAP_RECV_FIRST8.store(u64::from_le_bytes(first8), Ordering::Relaxed);
                        CAP_RECV_RAX
                            .store(((m.sender as u64) << 32) | m.payload.len() as u64, Ordering::Relaxed);
                    }
                    (((m.sender as u64) << 32) | m.payload.len() as u64) as i64
                }
            }
        }
        CALL_INPUT_POLL => {
            // The release's authority-first poll through the INPUT
            // capability kind. Identity is the capability rows': the
            // caller's GATE SESSION (kernel-recorded at the ring
            // transition), or task 0 with no session attached.
            // Handles are u32 table indexes; anything that would TRUNCATE
            // is not a handle claim.
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            // A poll carries no payload claim, no transfer token and no
            // rights mask: the record pointer is a1 and everything else
            // must claim "none". A shape the row does not own is
            // refused, never half-served.
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            // AUTHORITY FIRST, the release's ordering: the capability is
            // checked before ANY event is consumed, so a refused poll
            // cannot eat a byte it was never allowed to see.
            match crate::experimental::caps::input_check_for(
                crate::experimental::scheduler::session_identity(),
                a0 as u32,
            ) {
                Err(e) => return map_cap_error(e),
                Ok(()) => {}
            }
            if !crate::memory::paging::user_range_ok(a1, 16, true) {
                return EFAULT;
            }
            match crate::experimental::input::pop() {
                // Authorized but bare: 0, a REAL answer (the release's
                // shape), distinguishable from a refusal by name.
                None => 0,
                Some(ev) => {
                    let mut b = [0u8; 16];
                    b[0] = ev.byte;
                    b[3] = if ev.device == crate::experimental::input::DeviceKind::Ps2Mouse { 1 } else { 0 };
                    b[8..16].copy_from_slice(&ev.timestamp_ticks.to_le_bytes());
                    unsafe {
                        core::ptr::copy_nonoverlapping(b.as_ptr(), a1 as *mut u8, 16);
                    }
                    1
                }
            }
        }
        _ => {
            if from_ring3 {
                RET_ENOSYS.store(ENOSYS, Ordering::Relaxed);
                // The proof's junk-arg call is the ONLY ring-3 caller of an
                // unported number in a proof boot, so these hold exactly the
                // wide-arg values its sent -- until PROOF_DONE reads them.
                // If a later legitimate unported ring-3 call preceded it,
                // the compare would fail the proof instead of lying.
                JUNK_ARG_RSI.store(a1, Ordering::Relaxed);
                JUNK_ARG_RDX.store(a2, Ordering::Relaxed);
                JUNK_ARG_R10.store(a_xfer, Ordering::Relaxed);
                JUNK_ARG_R8.store(a_rights, Ordering::Relaxed);
            }
            ENOSYS
        }
    }
}

/// Installs the gate with `DPL=Ring3` (a ring-3 `int` to a DPL-0 gate would
/// fault on the gate's privilege, not on the handler).
pub fn install(idt: &mut InterruptDescriptorTable) {
    unsafe {
        idt[VECTOR as u8]
            .set_handler_addr(VirtAddr::new(service_entry as *const () as u64))
            .set_privilege_level(PrivilegeLevel::Ring3);
    }
}

/// The trap entry. `rax` carries the call number in and the result out;
/// the caller's `rdi/rsi/rdx/r10/r8` arrive whole as `dispatch`'s first
/// five widened arguments (only `rdi` is consumed by a served call today);
/// the caller's CS is read out of the hardware frame.
///
/// The hardware frame (pushed by the CPU only when the privilege level
/// CHANGES) sits at `rbx + 72` after the nine pushes: RIP, CS, RFLAGS,
/// RSP, SS. CS's low two bits are the caller's CPL, which is why this stub
/// reads it from the frame instead of asking the current ring -- inside the
/// handler we are always ring 0, and that would make every call look
/// privileged. Saved-register slots under it: rbx+0 r11, +8 r10, +16 r9,
/// +24 r8, +32 saved rbx, +40 rdi, +48 rsi, +56 rdx, +64 rcx.
///
/// RSP is kept in **rbx**, a callee-saved register; the seven `call`
/// arguments are built in the SysV register order with the seventh (the
/// CPL) on the stack at [rsp] when `call` runs -- an even 16-byte gap, so
/// the callee's alignment is preserved. RSP is rounded down once, still
/// rather than padded by a counted amount (the frame differs between a
/// ring-3 and a ring-0 caller, and a pad that is right for one is wrong
/// for the other). The first version of this stub kept RSP in r11 --
/// caller-saved -- so the Rust dispatcher was free to clobber it, and the
/// epilogue's `mov rsp, r11` restored a garbage stack pointer. That showed
/// up as a double fault *after* two successful calls, which is exactly what
/// a corrupted stack looks like when the first pushes and pops happen to
/// land on valid pages. The widened pass itself is proven, not read and
/// admired: see `dispatch`'s JUNK_ARG statics.
#[unsafe(naked)]
pub unsafe extern "C" fn service_entry() {
    naked_asm!(
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push rbx",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "mov rbx, rsp",        // callee-saved: the dispatch cannot clobber it
        "mov rsi, rdi",       // a0 = the caller's rdi
        "mov rdx, [rbx + 48]", // a1 = the caller's rsi
        "mov rcx, [rbx + 56]", // a2 = the caller's rdx
        "mov r8, [rbx + 8]",   // a_xfer = the caller's r10
        "mov r9, [rbx + 24]",  // a_rights = the caller's r8
        "mov rdi, rax",       // nr = the caller's rax
        "mov r10, [rbx + 80]", // caller CS, from the hardware frame
        "and rsp, -16",
        "sub rsp, 16",
        "mov [rsp], r10",      // 7th SysV arg: cpl, keeping the 16-byte alignment
        "call {dispatch}",
        "add rsp, 16",
        "mov rsp, rbx",
        "pop r11",
        "pop r10",
        "pop r9",
        "pop r8",
        "pop rbx",
        "pop rdi",
        "pop rsi",
        "pop rdx",
        "pop rcx",
        "iretq",
        dispatch = sym service_dispatch_c,
    );
}

/// C half of the entry stub: SysV arguments `(nr, a0..=a2, transfer,
/// rights, cs)`.
extern "C" fn service_dispatch_c(
    nr: u64,
    a0: u64,
    a1: u64,
    a2: u64,
    a_xfer: u64,
    a_rights: u64,
    cs: u64,
) -> i64 {
    dispatch(nr, a0, a1, a2, a_xfer, a_rights, cs & 3)
}

/// The boot proof for this surface's ABI, called from ring 0. It checks the
/// answers, the refusal, and the validator's two sides -- that it accepts a
/// user page and refuses a kernel one. The privilege transition itself is
/// proven separately by `svc84_proof`, which runs a real ring-3 program
/// (`userprog/prog_svc84.s`) and closes via `PROOF_DONE`.
pub fn self_test() -> bool {
    let gfx = dispatch(CALL_GFX_MODE, 0, 0, 0, 0, 0, 0);
    let gfx_ok = match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => gfx == (((w as i64) << 32) | h as i64),
        None => gfx == ENOSYS,
    };

    let input = dispatch(CALL_INPUT_STATE, 0, 0, 0, 0, 0, 0);
    let (px, py) = crate::cursor::last_position();
    let input_ok = input
        == ((px.clamp(0, 0xffff) as i64)
            | (py.clamp(0, 0xffff) as i64) << 16
            | ((crate::ps2_aux::facts().last.buttons as i64 & 0xff) << 32));

    // Every call this kernel does not serve must be refused with the named
    // error, not answered with zeroes.
    let refused = dispatch(10, 0, 0, 0, 0, 0, 0) == ENOSYS
        && dispatch(20, 0, 0, 0, 0, 0, 0) == ENOSYS
        && dispatch(u64::MAX, 0, 0, 0, 0, 0, 0) == ENOSYS;

    // The validator: a kernel stack address must be refused (it is not
    // USER_ACCESSIBLE), and a null page certainly is. Writing through the
    // call must therefore be refused before anything is touched.
    let mut scratch = [0u32; 2];
    let kernel_refused =
        dispatch(CALL_GFX_MODE_INTO, scratch.as_mut_ptr() as u64, 0, 0, 0, 0, 0) == EFAULT;
    let null_refused = dispatch(CALL_GFX_MODE_INTO, 0, 0, 0, 0, 0, 0) == EFAULT;
    let validator_ok = kernel_refused && null_refused;

    // The identity call's answer must BE the scheduler's own running-task
    // id -- task 0 (the kernel) while no ring-3 session owns the gate.
    let self_ok = dispatch(CALL_PROCESS_SELF, 0, 0, 0, 0, 0, 0)
        == crate::experimental::scheduler::current_task() as i64;

    // The IPC rows' ring-0 side: SHAPE refusals only. The good path is
    // proven in ring 3 by svc84_proof (and a self-test send here would
    // queue a self-addressed message, which exactly what the primitive's
    // arbiter forbids); what ring 0 must prove is that a shape claim the
    // (sender, seq) primitive does not own is refused without touching
    // any queue, and that a nonexistent endpoint is ENOENT, not ENODATA.
    // (Endpoint counts differ per boot phase -- 0 before ipc::init -- so
    // anything above the largest real table is nonexistent on every boot.)
    let ipc_shape_ok = dispatch(CALL_IPC_SEND, 0, 7, 8, u64::MAX, 0, 0) == EINVAL // payload claim
        && dispatch(CALL_IPC_SEND, 0, 7, 0, 9, 0, 0) == EINVAL // transfer-token claim
        && dispatch(CALL_IPC_SEND, 0, 7, 0, u64::MAX, 5, 0) == EINVAL // rights claim
        && dispatch(CALL_IPC_SEND, 0, u32::MAX as u64 + 1, 0, u64::MAX, 0, 0) == ERANGE
        && dispatch(CALL_IPC_SEND, 0x4000, 7, 0, u64::MAX, 0, 0) == ENOENT
        && dispatch(CALL_IPC_RECV, 0x4000, 0, 0, 0, 0, 0) == ENOENT
        && dispatch(CALL_IPC_RECV, 0, 0, 0, 1, 0, 0) == ENOSYS // the BLOCKING form
        && dispatch(CALL_IPC_RECV, 0, 7, 0, 0, 0, 0) == EINVAL; // RECV takes no message

    // The capability rows on a NORMAL boot: nothing was ever granted, and
    // boots never silently create endpoint capabilities (the proof boot
    // equips task 0 EXPLICITLY, terminal-path-only). So a handle claim
    // from an un-sessioned caller, against an empty table, is ESRCH (no
    // such holder table yet); a nonexistent cap endpoint is EBADF; an
    // oversize payload claim is EMSGSIZE before anything is touched; and
    // a rights claim wider than the endpoint kinds carry is not a handle
    // claim at all (EBADF). Asserting this exact order here WAITS for a
    // boot where granted caps exist and name-checks the empty-state
    // answers -- a silent zero would be the "answered with nothing
    // happened" lie again.
    let caps_rows_ok = dispatch(CALL_CAP_SEND, 0, 0, 0, u64::MAX, 0, 0) == ESRCH
        && dispatch(CALL_CAP_RECV, 0, 0, 0, u64::MAX, 0, 0) == EBADF
        && dispatch(CALL_CAP_SEND, 0, 0, 65, u64::MAX, 0, 0) == EMSGSIZE
        && dispatch(CALL_CAP_SEND, 0, 0, 0, u64::MAX, u32::MAX as u64, 0) == EBADF;
    // The INPUT_POLL row, on the same empty-table state: the authority
    // check's dead-process refusal (ESRCH, no holder exists yet), a
    // handle claim that would TRUNCATE (EBADF, a shape refusal), and an
    // unused-arg shape claim (EINVAL) -- each by name, and NO byte ever
    // consumed, because the authority-first clause runs before the ring.
    let input_row_ok = dispatch(CALL_INPUT_POLL, 0, 0, 0, u64::MAX, 0, 0) == ESRCH
        && dispatch(CALL_INPUT_POLL, u64::MAX, 0, 0, u64::MAX, 0, 0) == EBADF
        && dispatch(CALL_INPUT_POLL, 0, 0, 5, u64::MAX, 0, 0) == EINVAL;

    let ok = gfx_ok && input_ok && refused && validator_ok && self_ok && ipc_shape_ok && caps_rows_ok && input_row_ok;
    crate::serial_println!(
        "  service-0x84: gfx/input calls -- GFX_MODE {}, INPUT_STATE {}, unported calls refused with -ENOSYS {}, pointer validator refuses a kernel address and null {}, process SELF names the running task {} , IPC shape claims refused by name {} , capability rows refused by name on an empty table {} , INPUT_POLL refused by name on an empty table {} ; gate installed at DPL3 -- {}",
        if gfx_ok { "OK" } else { "FAILED" },
        if input_ok { "OK" } else { "FAILED" },
        if refused { "OK" } else { "FAILED" },
        if validator_ok { "OK" } else { "FAILED" },
        if self_ok { "OK" } else { "FAILED" },
        if ipc_shape_ok { "OK" } else { "FAILED" },
        if caps_rows_ok { "OK" } else { "FAILED" },
        if input_row_ok { "OK" } else { "FAILED" },
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
