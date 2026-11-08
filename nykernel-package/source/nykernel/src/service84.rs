//! The `0x84` service surface: the gfx and input calls the release's shell
//! stack uses, as much of them as this kernel can honestly serve today.
//!
//! What this is: a ring-3-callable gate (`int $0x84`, DPL 3, call number in
//! `rax`) in the release's five-argument ABI -- `rdi/rsi/rdx/r10/r8` all
//! reach `dispatch` (the arguments each row consumes are named in the
//! table), the result goes back in `rax` -- that serves fifteen calls:
//!
//! | nr | call | argument | returns |
//! |---|---|---|---|
//! | 0x40 | `INPUT_STATE` | — | `(x & 0xffff) | (y & 0xffff) << 16 | buttons << 32` |
//! | 0x41 | `GFX_MODE` | — | `(width << 32) | height` of the bound framebuffer |
//! | 0x42 | `GFX_MODE_INTO` | user pointer | writes `(u32 width, u32 height)` there; `0`, or `-EFAULT` |
//! | 0x43 | `PROOF_DONE` | user pointer | the ring-3 proof's read-back check (see below) |
//! | 0x44 | `PROCESS_SELF` | — | the running scheduler task's id (`experimental::scheduler`); `0` = the kernel itself |
//! | 0x45 | `IPC_SEND` | a0=endpoint, a1=seq, OR a1=buffer + a2=len | queues `(sender = caller identity, seq)` or the release's payload message (bytes copied into the kernel); `0`, or the arbiter's errno |
//! | 0x46 | `IPC_RECV` | a0=endpoint (+ a1=buffer, a2=buflen>=64 for the payload dial), a_xfer = 0 plain / 1 blocking | drains one queued message -> `(sender << 32) | seq`, or the payload byte count with the bytes in the caller's buffer; `-ENODATA` when real-and-empty, `-ENOENT` when it does not exist, `-EAGAIN` when a wait woke with nothing |
//! | 0x47 | `CAP_SEND` | a0=handle, a1/a2=payload, a_xfer/a_rights = a handle to transfer, attenuated to at most a_rights (u64::MAX = no transfer) | capability-checked send through `experimental::caps`; the transfer is how a region capability reaches a SECOND holder. `0`, or its errno |
//! | 0x48 | `CAP_RECV` | a0=endpoint, a1/a2=recv buffer, a_rights = out-pointer for a transferred handle (0 = not wanted) | drains one message into user memory -> `(sender << 32) | len`; `-ENODATA` when real-and-empty |
//! | 0x49 | `INPUT_POLL` | a0=handle, a1=record ptr | authority-first Input-capability check, then the oldest RAW input byte -> 16-byte record; `1`, `0` when authorized-and-bare, its errno otherwise |
//! | 0x4a | `CAP_CALL` | a0=endpoint handle, a1/a2=request; a_xfer/a_rights = answer buffer + length for the BLOCKING dial | queues the request carrying a ONE-SHOT `Reply` capability naming the caller -> the endpoint's owner id; with an answer buffer the caller PARKS until the server answers and gets the answer's byte count; its errno otherwise |
//! | 0x4d | `CAP_SHM_CLOSE` | a0=region handle | gives ONE region capability back; the record dies with its LAST holder -> `0`, `-EBADF` (a double close), `-EACCES` for a handle of another kind |
//! | 0x4e | `CAP_EXIT` | nothing (every claim refused `-EINVAL`) | releases the caller's whole table (region counts included), withdraws its session, terminates its task; never returns. `-EPERM` for task 0 |
//! | 0x4c | `CAP_SHM` | a0=region handle, a1=24-byte record ptr | the kernel's own geometry of the mapped region (`{base, len, rights}`) -> `0`, `-EBADF` for a handle that names nothing, `-EACCES` for a handle of another kind |
//! | 0x4f | `CAP_SHM_MAP` | a0=length in bytes (4096-aligned, <= 16 pages), a1=32-byte record ptr | the release's region REQUEST: the kernel maps fresh pages at its own window, records the region, mints the caller a capability and answers `{base, len, rights, handle}` -> `0`; `-EINVAL` for a shape it does not own, `-EFAULT` for a bad record pointer, `-ENOMEM` when the kernel refused the mapping, `-ENOSYS` when no mapping source is installed |
//! | 0x4b | `CAP_REPLY` | a0=reply handle, a1/a2=answer | answers one call at the caller's own endpoint and CONSUMES the handle; `0`, `-EBADF` on a second use, `-EACCES` on a wrong-kind handle |
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
//! rather than served truncated. Since 2026-11-06 the payload half is
//! SERVED as its own dial: `a2 != 0` on SEND means the release's
//! `SEND(handle, buf, len)` -- `a1` is a user buffer and `a2` the byte
//! count, bounded at `ipc::MAX_PAYLOAD` (64, the release's own message
//! size), validated and COPIED into the kernel before the queue is
//! touched; on RECV it means `RECV(idx, buf, buflen)`, whose `buflen`
//! must be at least one whole message and whose answer is the BYTE
//! COUNT with the bytes written to the caller's buffer. A transfer
//! token (`a_xfer != u64::MAX` on SEND) and a rights claim (`a_rights`)
//! are still refused `EINVAL`: a message cannot carry a capability yet,
//! and answering a transfer the other end never receives would be the
//! exact lie the refusal convention exists to prevent.
//!
//! The blocking form (`a_xfer == 1` on RECV)
//! is SERVED for its INSTANT half as of 2026-11-06: a message already on
//! the endpoint is drained and answered exactly as the plain dial would,
//! with the budget (`a1`) checked first (shape, then budget, then
//! destination -- the release's ordering). Its BLOCKED half is refused
//! `-EAGAIN` BY NAME on an empty endpoint, NOT `-ENODATA`: parking from a
//! session would hang it forever (a session id is never schedulable, and
//! this tree has no waker to park on), so "waited and got nothing" must
//! not masquerade as "checked and it was empty" -- the distinction the
//! manifest's saved-trap session machine exists to draw. The IPC rows are
//! sessionized (commit `a661b0a`): a message's sender is the SENDING
//! session's id, so a ring-3 program's own SEND is recognisably its
//! own -- and that is what makes the served instant half provable (the
//! proof pins `0x70000002a`: session 7, seq 42).
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
//! transfer/capability shape of SEND, the PARKED half of the blocking RECV
//! (needs a waker, hence the saved-trap session machine),
//! capability rights and transfers, spawn, shared memory
//! (`porting/release-1-71/src/experimental/syscall_demo.rs` and its
//! `capability.rs`/`shm.rs`) -- is one connected surface, not five
//! independent calls: full SEND requires capability tables, the parked
//! blocking RECV requires block-and-reschedule with a saved trap and a
//! waker to be rescheduled BY, either requires a process
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

/// The release's CALL/REPLY pair (its ledger's calls 9 and 10), served
/// as the capability machine's third kind, `caps::Kind::Reply`: a
/// request that carries a ONE-SHOT right to answer it.
///
/// CALL (0x4a): a0 = an ENDPOINT handle in the caller's own table, a1/a2
/// = the request payload. The kernel mints a SEND-only Reply capability
/// naming the CALLER, rides it in the message's dedicated transfer field
/// (§5.1) and answers the endpoint's OWNER identity -- the only holder
/// that can answer. There is no transfer argument on this row: the
/// capability travelling with a call is the kernel's own mint, so a
/// caller cannot choose what rides along.
///
/// REPLY (0x4b): a0 = the handle RECV installed for that capability (see
/// CALL_CAP_RECV's out-pointer), a1/a2 = the answer. It queues the bytes
/// at the caller's ANSWER SLOT (the first live endpoint the caller owns)
/// and CONSUMES the handle -- one-shot by construction, so the second
/// use is EBADF, not a second answer.
pub const CALL_CAP_CALL: u64 = 0x4a;
pub const CALL_CAP_REPLY: u64 = 0x4b;

/// The release's shared-memory row, served as the capability machine's
/// fourth kind, `caps::Kind::SharedMem`. a0 = a REGION handle in the
/// caller's own table, a1 = a 24-byte record the kernel writes:
/// `{u64 base, u64 len, u64 rights}`. The geometry is the KERNEL's own
/// record of what it mapped -- the holder is told where the region is,
/// never asked where it thinks it is -- and the rights are the ones the
/// capability carries (READ|WRITE|GRANT). Returns 0, or the errno of the
/// refusal (EBADF for a handle that names nothing, EACCES for a handle of
/// another kind).
///
/// What this row does NOT do is map anything: the region is mapped by the
/// kernel before any holder can name it (that is what makes the capability
/// a MAPPING authority rather than a request for one), so the row answers
/// where the mapping is and how much of it the holder may use.
pub const CALL_CAP_SHM: u64 = 0x4c;

/// The release's region lifetime, served as two rows because the two
/// halves are different events:
///
/// 0x4d `CAP_SHM_CLOSE`: a0 = a REGION handle the caller gives back. The
/// handle is consumed, the region's holder count drops by one, and the
/// record dies exactly when its LAST holder walks away -- so a close from
/// one of two holders is not a teardown, and asking for the geometry
/// afterwards (through the other holder's handle) still answers. Returns
/// 0, `EBADF` for a handle that names nothing (a double close lands here,
/// which is why the count cannot be driven negative), `EACCES` for a
/// handle of another kind.
///
/// 0x4e `CAP_EXIT`: end this process. Releases every capability the
/// caller's session holds (region counts included -- this is the OTHER
/// way a region loses a holder, and the one that makes an abrupt death
/// safe), withdraws the session's identity record, and terminates the
/// calling TASK through the scheduler's own lifecycle (`terminate_current`
/// walks RUNNING -> TERMINATING -> TERMINATED). No row returns from it:
/// the caller's task is gone, and every argument is refused if it claims
/// one (`EINVAL`), because an exit that carried a payload would be a
/// shape this row does not own. Task 0 is `EPERM`: the boot's own task is
/// not a process and terminating it would leave the machine with nothing
/// to run the proof's read-back.
///
/// Named sharp edge: the ring-3 proof calls this, so this row's LAST
/// effect on that boot is a context switch into whatever task remains --
/// which is exactly why the proof's read-back runs on task 0 and not on
/// the exiting program.
pub const CALL_CAP_SHM_CLOSE: u64 = 0x4d;
pub const CALL_CAP_EXIT: u64 = 0x4e;

/// The release's region REQUEST -- the half that was missing until
/// 2026-11-07: until then the kernel mapped a region FIRST and a holder
/// could only be told where it was (0x4c). This row is the other
/// direction, the release's `shm` create/map: a0 = the LENGTH IN BYTES
/// (a multiple of the page size, at most [`MAX_REGION_REQUEST_PAGES`]),
/// a1 = a 32-byte record the kernel writes `{u64 base, u64 len, u64
/// rights, u64 handle}`. The kernel half chooses the window and backs it
/// with fresh, zeroed frames, records the region, and mints the caller a
/// capability over it -- so the handle in the record is usable for 0x4c
/// and 0x4d immediately, without the caller knowing any address. Every
/// other argument must claim nothing (`a2 == 0`, `a_xfer == u64::MAX`,
/// `a_rights == 0`): the rights are the kernel half's to grant, and a
/// caller that could ask for GRANT while the machine's owner is the
/// kernel would be widening by request.
///
/// The mapping is OWNED by the region machine: when the last capability
/// over it goes away -- the holder's close (0x4d) or its exit (0x4e) --
/// the pages are unmapped and returned to the frame allocator. A refusal
/// at request time gives back whatever it had already mapped, so an
/// answered error never leaves frames that nothing names.
pub const CALL_CAP_SHM_MAP: u64 = 0x4f;
/// The largest region a request may ask for, in pages. The kernel half's
/// window stride is one 64 KiB slot per request, so a 16-page region is
/// exactly the largest that cannot run into the next request's window --
/// the bound is a property of the mapping layout, not a policy guess.
pub const MAX_REGION_REQUEST_PAGES: u64 = 16;

/// Linux's `ENOSYS`, and the release's convention: negative errno in `rax`.
pub const ENOSYS: i64 = -38;
/// Linux `ENOMEM`: the kernel's mapping source could not back a
/// requested region. The caller holds nothing, and nothing was leaked.
pub const ENOMEM: i64 = -12;
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
        // A second outstanding BLOCKING call from one holder: EAGAIN is
        // the release's "would block", and this caller is already
        // blocked on another call, so it must finish that one first.
        CallPending => EAGAIN,
        // A region id the kernel's own table does not know. ENOENT by
        // name: the handle resolved, but what it named does not exist.
        BadRegion => ENOENT,
        TableFull => ENOSPC,
        // A region REQUEST on a boot whose kernel half installed no
        // mapping source: "this machine cannot map" is exactly ENOSYS,
        // not a zero-length region and not a made-up address.
        NoRegionMapper => ENOSYS,
        // The kernel's mapping source refused (out of frames): a real
        // resource refusal, answered by name.
        OutOfMemory => ENOMEM,
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
/// The TASK the ring-3 caller ran on, recorded by the identity row. The
/// proof's read-back runs on the COORDINATOR (task 0) now, so asking the
/// scheduler "who am I?" at read-back time would answer 0 for a proof
/// about two other tasks: this static is what the differential SELF
/// compare uses instead. (The IDENTITY the capability legs are judged
/// against is the proof's own first session -- a constant, because a
/// static written by whichever process spoke last would make the compare
/// depend on scheduling order.)
static SELF_TASK: AtomicU64 = AtomicU64::new(u64::MAX);
/// CAP_EXIT's record of the LAST exit: the task that asked (it is gone by
/// the time anyone reads this), the session identity it withdrew, and how
/// many region capabilities it gave back. Read by the proof's read-back,
/// so "the teardown happened" is a value, not an assumption.
static EXIT_TASK: AtomicU64 = AtomicU64::new(u64::MAX);
static EXIT_SESSION: AtomicU64 = AtomicU64::new(u64::MAX);
static EXIT_RELEASED: AtomicU64 = AtomicU64::new(0);
static EXIT_COUNT: AtomicU32 = AtomicU32::new(0);
/// Read-back of the exit record: (task, session, region caps released,
/// exits served).
pub fn exit_record() -> (u64, u64, u64, u32) {
    (
        EXIT_TASK.load(Ordering::Relaxed),
        EXIT_SESSION.load(Ordering::Relaxed),
        EXIT_RELEASED.load(Ordering::Relaxed),
        EXIT_COUNT.load(Ordering::Relaxed),
    )
}
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
/// The blocking form's INSTANT half answer, recorded when the ring-3
/// blocking recv consumed a queued message (the same shape as
/// RECVD_VALUE). PROOF_DONE compares the program's stored answer
/// against THIS. The blocked half answers only for a caller with a
/// schedulable home (`scheduler::session_is_schedulable`); a caller
/// without one is refused EAGAIN when its budget survives the drain,
/// never parked (see the arm's sharp-edge note).
static BLOCKED_RECVD_VALUE: AtomicU64 = AtomicU64::new(0);
/// The parked wake's own record, distinct from the instant drain's
/// `BLOCKED_RECVD_VALUE`: the two-process proof's first process parks on
/// an empty box and the PEER's send is what answers it, so "the park
/// happened" is this static being non-zero, and a heap where a drain
/// answered instead would leave it zero.
static WOKEN_RECVD_VALUE: AtomicU64 = AtomicU64::new(0);
/// How many ring-3 receives a PARKED wake answered.
static BLOCKED_RECVS: AtomicU32 = AtomicU32::new(0);
/// The payload form's answer, recorded when a ring-3 payload recv
/// landed bytes in the caller's buffer: the BYTE COUNT (the release's
/// own `RECV` answer), so the proof can judge the length the kernel
/// handed back against what the program's buffer now holds.
static PAYLOAD_RECVD_VALUE: AtomicU64 = AtomicU64::new(0);
/// The capability leg's differential records: what the drain actually
/// handed back BEYOND what rax shows -- the received payload's first 8
/// bytes (zero-padded) and the packed (sender<<32|len) return. The
/// program's stored copies and its own memory are compared against THESE.
// (The CAP leg used to judge itself against two "last drain" statics.
// They cannot survive a second ring-3 drain -- the CALL/REPLY legs drain
// twice more, and the cap leg's expected value silently became the
// answer's -- so its expectation is derived from the live session
// identity and the sentinel instead: stored[14] == (session << 32 | 6)
// and the program's own buffer holding the "nyrqis" sentinel.)
/// The handle the LAST capability receive installed for a transferred
/// capability (u64::MAX = nothing transferred). Recorded so the proof
/// can compare the program's stored copy against the kernel's own, which
/// is how the CALL/REPLY leg proves the handle the program USED is the
/// one the machine minted -- a constant there would prove nothing.
static CAP_XFER_HANDLE: AtomicU64 = AtomicU64::new(u64::MAX);
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

/// The `PROOF_DONE` (0x43) row's body, OUT OF LINE: it is the biggest
/// arm in the dispatcher by far (nine result blocks read back out of user
/// memory, compared field by field, and printed), and a `match` arm's
/// locals live in the DISPATCHER's frame -- measured at ~11.6 KiB, which a
/// five-page task stack that calls another row from inside the gate cannot
/// afford. Out of line, that cost is paid only by the one proof boot that
/// asks for the verdict, and every other row's caller keeps the headroom.
#[inline(never)]
fn serve_proof_done(a0: u64, a1: u64, from_ring3: bool) -> i64 {
    // Twenty-six qwords: the five older results (gfx, input, enosys,
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
    // payload bytes as "the forged answer" exactly that way);
    // and the BLOCKING-RECV legs (2026-11-06): the send ack
    // (BUF+176 -> index 22), the instant answer (BUF+184 ->
    // index 23), the empty retry's EAGAIN (BUF+192 -> index 24)
    // and the dead destination's ENOENT (BUF+200 -> index 25).
    // The CAP PAYLOAD is compared at BUF+352..+360 (it moved out
    // of 192..208 to make room for those legs) and the INPUT
    // POLLED BYTES at BUF+208/+224 (below).
    if !crate::memory::paging::user_range_ok(a0, 208, false) {
        return EFAULT;
    }
    // Read the results back OUT OF USER MEMORY: this is the part
    // the registers alone cannot prove, because it shows the
    // ring-3 program actually received and stored them.
    // The PAYLOAD dials' results live in their own block
    // (BUF+448..+496), because the first array's 8-byte stride
    // collides with the INPUT_POLL records at BUF+208/+224/+240
    // from index 26 on: [0] send ack, [1] recv byte count,
    // [2] the bytes read back OUT of the program's buffer,
    // [3] the oversize refusal, [4] the null-pointer refusal,
    // [5] the too-small receive buffer's refusal.
    if !crate::memory::paging::user_range_ok(a0 + 448, 48, false) {
        return EFAULT;
    }
    let mut stored = [0i64; 26];
    for (i, slot) in stored.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 as *const i64).add(i)) };
    }
    let want_gfx = RET_GFX.load(Ordering::Relaxed);
    let want_input = RET_INPUT.load(Ordering::Relaxed);
    let want_enosys = RET_ENOSYS.load(Ordering::Relaxed);
    // The identity answer is compared against the scheduler's OWN
    // live value at read-back time, so a future session that makes
    // the program a real task changes both sides together.
    let want_self = SELF_TASK.load(Ordering::Relaxed) as i64;
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
    // The BLOCKING legs are judged the same way: the kernel's own
    // record of what the instant half handed back (sender = the
    // gate session, so 0x70000002a for seq 42) compared against
    // the program's stored copy. The empty retry's EAGAIN and the
    // dead destination's ENOENT are pinned as constants -- they
    // are REFUSALS, and a by-name refusal is its own pin.
    let want_blocked = BLOCKED_RECVD_VALUE.load(Ordering::Relaxed) as i64;
    // The PARKED wake's value: the peer's send is what answered the
    // first process's blocking receive, so this record is what says
    // the park happened at all (an instant drain would have left it
    // zero and filled `want_blocked` a second time instead).
    let want_woken = WOKEN_RECVD_VALUE.load(Ordering::Relaxed) as i64;
    // The payload dial's kernel-side record: the byte count the
    // payload recv answered. Compared against the program's
    // stored copy below, like every other answer.
    let want_payload_len = PAYLOAD_RECVD_VALUE.load(Ordering::Relaxed) as i64;
    // The CALL/REPLY block lives after the payload block
    // (BUF+496..576, ten qwords): the call's ack, the call
    // receive's byte count, the handle the kernel wrote at the
    // out-slot, the handle as the program reads it back, the
    // request's bytes, the reply's ack, the one-shot refusal,
    // the wrong-kind refusal, the answer's byte count and the
    // answer's bytes.
    if !crate::memory::paging::user_range_ok(a0 + 496, 80, false) {
        return EFAULT;
    }
    let mut reply_block = [0i64; 10];
    for (i, slot) in reply_block.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 496 + 8 * i as u64) as *const i64) };
    }
    // The SHARED-REGION block (BUF+760..848, eleven qwords): the
    // geometry row's ack, the record's base/length/rights as the
    // program read them back, the sentinel it read THROUGH the
    // mapping, the value it wrote through the same mapping, and
    // the two refusals (a handle that names nothing, a handle of
    // another kind).
    if !crate::memory::paging::user_range_ok(a0 + 760, 88, false) {
        return EFAULT;
    }
    let mut shm = [0i64; 11];
    for (i, slot) in shm.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 760 + 8 * i as u64) as *const i64) };
    }
    // The TWO-PROCESS block (BUF+848..+1000, nineteen qwords): the
    // first process's close of ONE of its two region capabilities,
    // the geometry through the OTHER one (the record survives a
    // holder), the blocking call's answer, the region's two
    // offsets as each process sees them, the last close and the
    // then-consumed handle.
    if !crate::memory::paging::user_range_ok(a0 + 848, 160, false) {
        return EFAULT;
    }
    let mut two = [0i64; 20];
    for (i, slot) in two.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 848 + 8 * i as u64) as *const i64) };
    }
    // The REQUESTED-REGION block (BUF+2136..+2264, sixteen
    // qwords): the 0x4f request's ack, the geometry the kernel's
    // record answered, the handle it minted, the value the holder
    // wrote THROUGH the mapping, and the second request's own
    // facts (a different window), then the two closes and the
    // consumed handle's refusal. The kernel-side half (the window
    // unmapped, the releaser's reading, the frame count) is
    // compared from `svc84_proof`'s own record below.
    if !crate::memory::paging::user_range_ok(a0 + 2136, 128, false) {
        return EFAULT;
    }
    let mut map = [0i64; 16];
    for (i, slot) in map.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2136 + 8 * i as u64) as *const i64) };
    }
    // The HANDED-ON region's block (BUF+2344..+2408, eight qwords): the
    // request's ack, the base the kernel chose for it, the minted handle,
    // the length, the transfer's ack, the close's ack, the process's own
    // marker read back AFTER that close (the page survived it), and the
    // consumed handle's refusal.
    if !crate::memory::paging::user_range_ok(a0 + 2344, 64, false) {
        return EFAULT;
    }
    let mut mapx = [0i64; 8];
    for (i, slot) in mapx.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2344 + 8 * i as u64) as *const i64) };
    }
    // The PEER process's page (a1): its own results, read from ITS
    // memory after its task was TERMINATED -- the strongest form of
    // "this is what the other process observed".
    if !crate::memory::paging::user_range_ok(a1, 216, false) {
        return EFAULT;
    }
    let mut peer = [0i64; 27];
    for (i, slot) in peer.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a1 + 8 * i as u64) as *const i64) };
    }
    let mut payload = [0i64; 6];
    for (i, slot) in payload.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 448 + 8 * i as u64) as *const i64) };
    }
    // The capability leg is judged the same differential way: the
    // drain's actual answer (packed sender<<32|len, recorded when
    // the ring-3 recv ran), the drained payload's first 8 bytes,
    // and the payload bytes still in the program's buffer.
    // "nyrqis" is six bytes: the packed answer is the live
    // session identity shifted above it, exactly as the drain
    // packs (sender << 32 | len). Both sides are derived at
    // read-back time, so a session that changes changes both.
    let want_cap_rax =
        ((crate::svc84_proof::FIRST_SESSION as u64) << 32 | 6) as i64;
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
        && stored[12] == ENODATA // exists-but-empty, a REAL answer
        // The BLOCKING legs: the queued message answered
        // instantly on BOTH sides with the same kernel-recorded
        // value (nonzero by construction), the empty retry was
        // refused EAGAIN BY NAME (never parked, never answered
        // as empty), and the dead destination was refused
        // ENOENT.
        && stored[22] == 0
        && stored[23] == want_blocked
        && want_blocked != 0
        && stored[24] == want_woken
        && want_woken != 0
        && want_woken != want_blocked
        // TWO parked wakes, one per direction: the peer's send
        // woke the first process out of its blocking receive, and
        // the first process's "go" woke the peer out of its own.
        && BLOCKED_RECVS.load(Ordering::Relaxed) == 2
        && stored[25] == ENOENT
        // The PAYLOAD dials (2026-11-06): the send was accepted,
        // the receive answered the kernel-recorded byte count,
        // the program's OWN buffer holds the sent bytes (a
        // kernel that answered a length and copied nothing
        // would fail here), and the three shape claims were
        // refused by name -- too wide, null pointer, too-small
        // buffer.
        && payload[0] == 0
        && payload[1] == want_payload_len
        && want_payload_len == 8
        && payload[2] == 0x2164616f6c796170
        && payload[3] == EMSGSIZE
        && payload[4] == EFAULT
        && payload[5] == EINVAL;
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
    let held_first8 = if crate::memory::paging::user_range_ok(a0 + 352, 8, false) {
        unsafe { core::ptr::read_unaligned((a0 + 352) as *const u64) }
    } else {
        u64::MAX
    };
    let cap_ok = stored[13] == 0
        && stored[14] == want_cap_rax
        && want_cap_rax != 0
        && stored[15] == EBADF
        && stored[16] == EMSGSIZE
        && held_first8 == CAP_SENTINEL;
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
    // The CALL/REPLY leg, judged the way the other legs are: the
    // call's answer is the endpoint's OWNER, which is this very
    // session (endpoint 0 is the proof session's own), read from
    // the live identity at read-back time rather than pinned as a
    // number; the handle is compared against the kernel's own
    // record of what it installed; the one-shot refusal and the
    // wrong-kind refusal are by-name refusals, so constants; and
    // the answer's packed (sender << 32 | len) is derived from
    // the same live identity, since the session answered itself.
    let sid = crate::svc84_proof::FIRST_SESSION;
    let want_reply_ack = sid as i64;
    let want_xfer_handle = CAP_XFER_HANDLE.load(Ordering::Relaxed) as i64;
    let want_answer_rax = (((sid as u64) << 32) | 8) as i64;
    const CALL_BYTES: i64 = 0x2121656d6c6c6163; // callme!!
    const ANSWER_BYTES: i64 = 0x2121726577736e61; // answer!!
    let reply_ok = reply_block[0] == want_reply_ack
        && want_reply_ack >= 0
        && reply_block[1] == want_answer_rax
        && want_xfer_handle >= 0
        && reply_block[2] == want_xfer_handle
        && reply_block[3] == want_xfer_handle
        && reply_block[4] == CALL_BYTES
        && reply_block[5] == 0
        && reply_block[6] == EBADF
        && reply_block[7] == EACCES
        && reply_block[8] == want_answer_rax
        && reply_block[9] == ANSWER_BYTES;
    // The SHARED-REGION leg. Three things make this a proof and
    // not a self-check: the geometry is compared against the
    // KERNEL's own mapping (crate::svc84_proof::PROOF_SHM), the
    // sentinel the program read is the value the kernel wrote
    // BEFORE ring 3, and the kernel then reads the region ITSELF
    // to find the value ring 3 wrote. The last one is the two
    // parties meeting in one page -- if the program were writing
    // some other page of its own, the kernel would still read
    // zero here.
    let want_shm_base = crate::svc84_proof::PROOF_SHM as i64;
    let want_shm_sentinel = crate::svc84_proof::SHM_KERNEL_SENTINEL as i64;
    let want_shm_written = crate::svc84_proof::SHM_RING3_VALUE as i64;
    let want_shm_rights =
        (crate::experimental::caps::rights::READ | crate::experimental::caps::rights::WRITE | crate::experimental::caps::rights::GRANT) as i64;
    let kernel_sees = unsafe {
        core::ptr::read_volatile((crate::svc84_proof::PROOF_SHM + 8) as *const u64)
    } as i64;
    let shm_ok = shm[0] == 0
        && shm[4] == want_shm_base
        && shm[5] == 4096
        && shm[6] == want_shm_rights
        && shm[7] == want_shm_sentinel
        && shm[8] == want_shm_written
        && shm[9] == EBADF
        && shm[10] == EACCES
        && kernel_sees == want_shm_written;
    // The two-process leg. Every value below is one only the
    // OTHER process could have produced: the answer bytes the
    // caller read out of a buffer the kernel wrote when the PEER
    // answered, the region's offset 16 written through the peer's
    // own capability, and the region's record dying with its last
    // holder (the kernel's table says so, not the programs).
    const CALL_REQ: i64 = 0x0000_2141_6d6f_7266; // "fromA!"
    const CALL_ANSWER: i64 = 0x0000_2142_6d6f_7266; // "fromB!"
    let want_shm_offset8 = crate::svc84_proof::SHM_RING3_VALUE as i64;
    let want_peer_write = crate::svc84_proof::PEER_WRITE as i64;
    let proof_region = crate::svc84_proof::PROOF_REGION.load(Ordering::Relaxed);
    let region_live = crate::experimental::caps::region_live(proof_region);
    let region_holders = crate::experimental::caps::region_holders(proof_region);
    let (exit_task, exit_session, exit_released, exit_count) = exit_record();
    let two_ok = two[0] == 0                 // the close was accepted
        && two[4] == want_shm_base           // the OTHER handle still names it
        && two[5] == 4096
        && two[6] == want_shm_rights
        && two[11] == 8                      // the call answered 8 bytes
        && two[12] == CALL_REQ
        && two[13] == CALL_ANSWER            // the peer's bytes, in OUR buffer
        && two[14] == want_shm_offset8       // our own write, still there
        && two[15] == want_peer_write        // the PEER's write, through OUR handle
        && two[16] == 0                      // the last close was accepted
        && two[17] == EBADF                  // ...and the handle is consumed
        && two[18] == 0                      // geometry answered after the first close
        && two[19] == 0                      // the go send was accepted
        // The region's LIFETIME, from the kernel's table: the record
        // is GONE (its last holder exited) and names no holder. A
        // close that merely returned 0 without moving the count
        // would fail here.
        && !region_live
        && region_holders == Some(0)
        // Both processes exited through CAP_EXIT: two exits, the
        // LAST of them the first process (the peer exits first,
        // having answered), and exactly ONE region capability was
        // given back by an exit -- the peer's. The first process
        // had already closed both of its own, so its exit released
        // none; a double release would show up here as 2.
        && exit_count == 2
        && exit_session == crate::svc84_proof::FIRST_SESSION as u64
        && exit_released == 1
        && exit_task != 0;
    // The peer's own observations: the geometry it was told, the
    // FIRST process's write read through its capability, the
    // request it received, its reply's ack, and its write.
    // The REQUESTED region: the caller ASKED (0x4f) and the kernel
    // chose the window, minted a usable handle, and backed it with
    // real frames -- the holder's write is read back by the
    // RELEASER out of the mapping before unmapping, which is the
    // same two-party evidence the SHM leg carries. A second
    // request lands in the NEXT window, so the base is the
    // kernel's choice and not a constant on the program's side.
    // After the last close the window is unmapped in the page
    // tables, and the frame count is back to the pre-pump
    // baseline: the mapping's pages came back, not just its
    // record.
    // Slot order in the pump: the HANDED-ON request is step 16, which runs
    // before the call that parks, so it takes the FIRST window slot; the
    // two requests of step 15 (after the answer) take the next two. The
    // release order inverts that for the first leg only: the receiver
    // closes before it answers, so ITS close is the first release.
    let want_map_base3 = crate::svc84_proof::SHM_REQUEST_WINDOW as i64;
    let want_map_base =
        (crate::svc84_proof::SHM_REQUEST_WINDOW + crate::svc84_proof::SHM_REQUEST_STRIDE) as i64;
    let want_map_base2 =
        (crate::svc84_proof::SHM_REQUEST_WINDOW + 2 * crate::svc84_proof::SHM_REQUEST_STRIDE) as i64;
    let want_map_value = crate::svc84_proof::SHM_REQUEST_VALUE as i64;
    let want_transfer_value = crate::svc84_proof::SHM_TRANSFER_VALUE as i64;
    let want_transfer_write = crate::svc84_proof::SHM_TRANSFER_WRITE as i64;
    let map_report = crate::svc84_proof::requested_region_report();
    let map_ok = match map_report {
        Some(r) => {
            map[0] == 0
                && map[1] == want_map_base
                && map[2] == 4096
                && map[3] == want_shm_rights
                && map[4] >= 0
                && map[5] == want_map_value
                && map[6] == 0
                && map[7] == want_map_base2
                && map[8] == 8192
                && map[9] == want_shm_rights
                && map[10] == map[4] + 1
                && map[11] == 0
                && map[12] == 0
                && map[13] == EBADF
                && map[14] == 0
                && map[15] == want_map_base
                && r.first_unmapped
                && r.second_unmapped
                && r.third_unmapped
                && r.released == 3
                // The FIRST release is the region that was HANDED ON, and
                // its two words are the two processes' writes: the
                // sender's marker at offset 0 (written BEFORE it closed
                // its own capability) and the receiver's at offset 8.
                // Read out of the mapping by the releaser, at the moment
                // the pages went back -- reported by neither program.
                && r.first_word == want_transfer_value as u64
                && r.first_word8 == want_transfer_write as u64
                // The last two releases are step 15's own requests, never
                // written by anyone: their words being zero is what says
                // the release order above is the one the pins describe.
                && r.last_word == 0
                && r.last_word8 == 0
                && r.first_base == crate::svc84_proof::SHM_REQUEST_WINDOW
                && r.last_base == want_map_base2 as u64
                && r.frames_in_use == r.frames_baseline
                // The SENDER's side of the handover: its own request, the
                // transfer's ack, its own close (accepted, and the region
                // did NOT die -- the read below proves the mapping lived),
                // and the consumed handle's refusal. The prototype the
                // receiver got is compared through ITS page, below.
                && mapx[0] == 0
                && mapx[1] == want_map_base3
                && mapx[2] >= 0
                && mapx[3] == 4096
                && mapx[4] == 0
                && mapx[5] == 0
                && mapx[6] == want_transfer_value
                && mapx[7] == EBADF
                // ...and the RECEIVER's: the message it drained (the first
                // process's session, four payload bytes), the handle the
                // machine installed in ITS table, the geometry row through
                // that handle (the same window, attenuated to READ|WRITE),
                // the sender's marker read across the handover, its own
                // write at offset 8, and the close that finally returned
                // the pages. Zero retries: the message was queued before
                // the "go" that resumed it.
                && peer[17] == ((crate::svc84_proof::FIRST_SESSION as i64) << 32 | 4)
                && peer[18] >= 0
                && peer[20] == want_map_base3
                && peer[22]
                    == (crate::experimental::caps::rights::READ
                        | crate::experimental::caps::rights::WRITE) as i64
                && peer[23] == want_transfer_value
                && peer[24] == want_transfer_write
                && peer[25] == 0
                && peer[26] == 0
        }
        None => false,
    };
    let (map_released, map_first_word, map_last_word, map_first_base, map_last_base, map_in_use, map_baseline, map_unmapped1, map_unmapped2, map_first_word8, map_unmapped3) =
        match map_report {
            Some(r) => (
                r.released,
                r.first_word,
                r.last_word,
                r.first_base,
                r.last_base,
                r.frames_in_use,
                r.frames_baseline,
                r.first_unmapped,
                r.second_unmapped,
                r.first_word8,
                r.third_unmapped,
            ),
            None => (0, 0, 0, 0, 0, 0, 0, false, false, 0, false),
        };
    let peer_ok = peer[0] == 0
        && peer[4] == want_shm_base
        && peer[5] == 4096
        && peer[6] == want_shm_offset8
        && peer[7] == ((crate::svc84_proof::FIRST_SESSION as i64) << 32 | 8)
        && peer[8] >= 0
        && peer[9] == CALL_REQ
        && peer[11] == 0
        && peer[12] == want_peer_write;
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
        && input_poll_ok
        && reply_ok
        && shm_ok
        && two_ok
        && peer_ok
        && map_ok;
    let ok = stored_ok && wrote_ok;
    PROOF_STATE.store(if ok { 1 } else { 2 }, Ordering::Relaxed);
    crate::serial_println!(                "  service-0x84 proof: a ring-3 program called int 0x84 from CPL {} -- GFX_MODE {:#x} , INPUT_STATE {:#x} , unported call {:#x} (refused, not answered) , validated write ({},{}) , process SELF {} , wide-args {:#x} {:#x} {:#x} {:#x} , ipc round trip (send-ack {:#x} , recv {:#x} , want {:#x} , shape {} , empty {} , blocked {:#x} , want-blocked {:#x} , again {} , baddest {} , payload (ack {} , len {} , bytes {:#x} , wide {} , null {} , short {})) , caps (ack {:#x} , recv {:#x} , forged {:#x} , oversized {:#x} , payload {:#x}) , read back through the validator: GFX {} , INPUT {} , ENOSYS {} , geometry {} , SELF {} , ARGS {} , IPC {} , CAP {} , INPUT_POLL (1 {:#x} , 2 {:#x} , 3 {:#x} , forged {:#x} , bytes {:#x}/{:#x}) , INPUT {} , REPLY (call {:#x} , recv {:#x} , handle {:#x} , reply {:#x} , twice {:#x} , kind {:#x} , answer {:#x} , bytes {:#x}) , REPLY {} , SHM (ack {:#x} , base {:#x} , len {:#x} , rights {:#x} , sentinel {:#x} , wrote {:#x} , forged {:#x} , kind {:#x}) , SHM {} , TWO-PROCESS (close {:#x} , geometry {:#x} , call {:#x} , request {:#x} , answer {:#x} , offset8 {:#x} , offset16 {:#x} , last {:#x} , twice {:#x} , region live {} holders {:?} , exits {} session {} released {}) , PEER (ack {:#x} , base {:#x} , read {:#x} , recv {:#x} , handle {:#x} , request {:#x} , reply {:#x} , wrote {:#x}) , TWO-PROCESS {} , PEER {} , PEER-XFER (recv {:#x} , handle {:#x} , base {:#x} , read {:#x} , wrote {:#x} , close {:#x} , tries {}) , MAP (ack {:#x} , base {:#x} , len {:#x} , rights {:#x} , handle {:#x} , wrote {:#x} , again {:#x} , base2 {:#x} , len2 {:#x} , handle2 {:#x} , close {:#x} , close2 {:#x} , stale {:#x} , geometry {:#x} , geom base {:#x} , released {} word {:#x}/{:#x} , bases {:#x}/{:#x} , frames {} == {} , unmapped {} / {} / {} , xfer (ack {:#x} , base {:#x} , send {:#x} , close {:#x} , survived {:#x} , last8 {:#x})) , MAP {} -- {}
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
        stored[23] as u64,
        want_blocked as u64,
        stored[24] as u64,
        stored[25] as u64,
        payload[0] as u64,
        payload[1] as u64,
        payload[2] as u64,
        payload[3] as u64,
        payload[4] as u64,
        payload[5] as u64,
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
        reply_block[0] as u64,
        reply_block[1] as u64,
        reply_block[3] as u64,
        reply_block[5] as u64,
        reply_block[6] as u64,
        reply_block[7] as u64,
        reply_block[8] as u64,
        reply_block[9] as u64,
        if reply_ok { "matches" } else { "MISMATCH" },
        shm[0] as u64,
        shm[4] as u64,
        shm[5] as u64,
        shm[6] as u64,
        shm[7] as u64,
        shm[8] as u64,
        shm[9] as u64,
        shm[10] as u64,
        if shm_ok { "matches" } else { "MISMATCH" },
        two[0] as u64,
        two[18] as u64,
        two[11] as u64,
        two[12] as u64,
        two[13] as u64,
        two[14] as u64,
        two[15] as u64,
        two[16] as u64,
        two[17] as u64,
        if region_live { "yes" } else { "no" },
        region_holders,
        exit_count,
        exit_session,
        exit_released,
        peer[0] as u64,
        peer[4] as u64,
        peer[6] as u64,
        peer[7] as u64,
        peer[8] as u64,
        peer[9] as u64,
        peer[11] as u64,
        peer[12] as u64,
        if two_ok { "matches" } else { "MISMATCH" },
        if peer_ok { "matches" } else { "MISMATCH" },
        peer[17] as u64,
        peer[18] as u64,
        peer[20] as u64,
        peer[23] as u64,
        peer[24] as u64,
        peer[25] as u64,
        peer[26] as u64,
        map[0] as u64,
        map[1] as u64,
        map[2] as u64,
        map[3] as u64,
        map[4] as u64,
        map[5] as u64,
        map[6] as u64,
        map[7] as u64,
        map[8] as u64,
        map[10] as u64,
        map[11] as u64,
        map[12] as u64,
        map[13] as u64,
        map[14] as u64,
        map[15] as u64,
        map_released,
        map_first_word,
        map_last_word,
        map_first_base,
        map_last_base,
        map_in_use,
        map_baseline,
        map_unmapped1,
        map_unmapped2,
        map_unmapped3,
        mapx[0] as u64,
        mapx[1] as u64,
        mapx[4] as u64,
        mapx[5] as u64,
        mapx[6] as u64,
        map_first_word8,
        if map_ok { "matches" } else { "MISMATCH" },
        if ok { "OK" } else { "FAILED" }
    );
    0

}

/// The `CAP_SHM_MAP` (0x4f) row's body, OUT OF LINE on purpose: a
/// `dispatch` frame is shared by every arm, and this row's record buffer
/// plus its refusal paths grew it enough to matter. The first version kept
/// the body inline and the ordinary boot's PARKED-CALL proof -- a
/// five-page task stack that is already deep inside `dispatch` -- crossed
/// its guard page (a #PF in the kernel's own number formatter, caught by
/// the boot test at once). In its own frame the row costs stack only when
/// a caller actually asks for a region.
#[inline(never)]
fn serve_region_request(
    a0: u64,
    a1: u64,
    a2: u64,
    a_xfer: u64,
    a_rights: u64,
    from_ring3: bool,
) -> i64 {
    // The shape first: a length in bytes, a record pointer, and no claim in
    // any other field. Checked before the mapping source is asked, so a
    // malformed request can never allocate.
    if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
        return EINVAL;
    }
    if a0 == 0 || a0 % 4096 != 0 || a0 / 4096 > MAX_REGION_REQUEST_PAGES {
        return EINVAL;
    }
    if from_ring3 && !crate::memory::paging::user_range_ok(a1, 32, true) {
        return EFAULT;
    }
    let identity = crate::experimental::scheduler::session_identity();
    let (_region, handle, geometry) =
        match crate::experimental::caps::request_region_for(identity, a0 / 4096) {
            Ok(v) => v,
            Err(e) => return map_cap_error(e),
        };
    // The record carries the HANDLE as well as the geometry: the row that
    // answers "where is it" (0x4c) takes a handle, so handing the caller
    // the one the machine just minted is what makes a request usable
    // without a constant on the program's side. The rights are read back
    // from the capability itself, not assumed from the request.
    let (_, held) = match crate::experimental::caps::region_cap_for(identity, handle) {
        Ok(v) => v,
        Err(e) => return map_cap_error(e),
    };
    let mut rec = [0u8; 32];
    rec[0..8].copy_from_slice(&geometry.base.to_le_bytes());
    rec[8..16].copy_from_slice(&geometry.len.to_le_bytes());
    rec[16..24].copy_from_slice(&(held as u64).to_le_bytes());
    rec[24..32].copy_from_slice(&(handle as u64).to_le_bytes());
    unsafe {
        core::ptr::copy_nonoverlapping(rec.as_ptr(), a1 as *mut u8, 32);
    }
    0
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
        CALL_PROOF_DONE => serve_proof_done(a0, a1, from_ring3),
        CALL_PROCESS_SELF => {
            // The scheduler's own answer, not a canned one: when a real
            // scheduler task ever owns a ring-3 caller, this call starts
            // naming it without this file changing. No bookkeeping beyond
            // what the proof's read-back uses -- identity is not a resource.
            let me = crate::experimental::scheduler::current_task() as i64;
            if from_ring3 {
                SELF_TASK.store(me as u64, Ordering::Relaxed);
            }
            me
        }
        CALL_IPC_SEND => {
            // Two dials, and the difference is a2:
            //
            //   a2 == 0 -- the surface's OWN (sender, seq) form: a1 is
            //     the seq, which must fit the primitive's u32.
            //   a2 != 0 -- the RELEASE'S payload SEND(handle, buf, len):
            //     a1 is a USER buffer pointer and a2 the byte count,
            //     bounded at `ipc::MAX_PAYLOAD` (the release's own
            //     message size) and COPIED OUT here, so the queue holds
            //     kernel bytes and a stale pointer can never be
            //     dereferenced at receive time.
            //
            // a_xfer (the transfer token) and a_rights (the grant mask)
            // are still refused EINVAL: a message cannot yet carry a
            // capability, and accepting the claim while sending only a
            // number would answer a transfer the other end never
            // receives -- the same honesty gate as before.
            if a_rights != 0 {
                return EINVAL; // rights without a transfer are a lie
            }
            if a_xfer != u64::MAX {
                return EINVAL; // the release's own "no transfer" claim
            }
            let payload_form = a2 != 0;
            let mut payload = [0u8; crate::experimental::ipc::MAX_PAYLOAD];
            if payload_form {
                if a2 > crate::experimental::ipc::MAX_PAYLOAD as u64 {
                    return EMSGSIZE;
                }
                if !crate::memory::paging::user_range_ok(a1, a2 as usize, false) {
                    return EFAULT;
                }
                unsafe {
                    core::ptr::copy_nonoverlapping(a1 as *const u8, payload.as_mut_ptr(), a2 as usize);
                }
            } else if a1 > u32::MAX as u64 {
                return ERANGE;
            }
            let dest = a0 as usize;
            // The arbiter (ipc::send) demands the sender claim name the
            // kernel-declared caller identity; this gate supplies the
            // scheduler's OWN answer -- the recorded gate session at a
            // ring-3 transition, the running task otherwise -- so a
            // ring-3 caller cannot forge it. A payload message carries
            // seq 0: the release's SEND has no seq field, and inventing
            // one would be a number the sender never claimed.
            let identity = crate::experimental::scheduler::session_identity();
            let msg = if payload_form {
                crate::experimental::ipc::IpcMessage::with_payload(identity, 0, &payload[..a2 as usize])
                    .expect("payload bounded above")
            } else {
                crate::experimental::ipc::IpcMessage::new(identity, a1 as u32)
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
            // Shape first: no rights claim (there is no transfer to
            // grant on this row).
            if a_rights != 0 {
                return EINVAL;
            }
            // 0x46's dials. a_xfer: 0 is the plain drain, 1 is the
            // RELEASE'S BLOCKING form (a1 = the empty-wake budget in the
            // seq form; see below), anything else is a shape lie. a2:
            // 0 keeps the surface's own `(sender << 32) | seq` answer,
            // non-zero is the RELEASE'S payload RECV(owned_idx, buf,
            // buflen) -- a2 is the receiver's buffer size, which the
            // release requires to be at least one whole message.
            match a_xfer {
                0 | 1 => {}
                _ => return EINVAL,
            }
            let blocking = a_xfer == 1;
            let payload_form = a2 != 0;
            if payload_form {
                if a2 < crate::experimental::ipc::MAX_PAYLOAD as u64 {
                    return EINVAL; // the release's buflen >= 64 floor
                }
                if !crate::memory::paging::user_range_ok(a1, a2 as usize, true) {
                    return EFAULT;
                }
            } else if blocking {
                // The blocking form's second arg is the DRAIN BUDGET
                // ("try to hand me up to this many"): it must be a sane
                // positive count -- needs at least one of them (calls
                // come one message at a time through the gate), so a
                // zero budget has no answer, and a budget wider than
                // the primitive's u32 is a caller bug.
                if a1 > u32::MAX as u64 {
                    return ERANGE;
                }
                if a1 == 0 {
                    return EINVAL;
                }
            } else if a1 != 0 {
                // The plain drain RECV takes no message: a nonzero a1
                // is a caller bug, refused by name (the release's own
                // int parser already refuses the negative field).
                return EINVAL;
            }
            // A nonexistent endpoint is refused BEFORE any receive
            // (try_receive answers None for both cases, and the two
            // differ: the caller must see where the fault was, not
            // just "nothing came"). Same order for every dial: shape,
            // budget/buffer sanity, destination.
            let dest = a0 as usize;
            if dest >= crate::experimental::ipc::endpoint_count() {
                return ENOENT;
            }
            // One answer-shaping place for both the instant drain and
            // the parked wake, so the two paths cannot drift.
            let answer = |m: crate::experimental::ipc::IpcMessage| -> i64 {
                if payload_form {
                    // The release's answer is the byte count, and the
                    // bytes go to the caller's buffer. buflen >= 64 >=
                    // any message, so the copy can never overflow what
                    // was validated above.
                    unsafe {
                        core::ptr::copy_nonoverlapping(m.payload().as_ptr(), a1 as *mut u8, m.len as usize);
                    }
                    m.len as i64
                } else {
                    (((m.sender as u64) << 32) | m.seq as u64) as i64
                }
            };
            match crate::experimental::ipc::try_receive(dest) {
                Some(m) => {
                    let v = answer(m);
                    if from_ring3 {
                        if payload_form {
                            PAYLOAD_RECVD_VALUE.store(v as u64, Ordering::Relaxed);
                        } else if blocking {
                            BLOCKED_RECVD_VALUE.store(v as u64, Ordering::Relaxed);
                        } else {
                            RECVD_VALUE.store(v as u64, Ordering::Relaxed);
                        }
                    }
                    v
                }
                None if blocking => {
                    // The instant half is drained and the wait remains:
                    // the release's BLOCKING dial waits.
                    //
                    // PARK when the caller HAS a schedulable home
                    // (2026-11-06): `scheduler::session_is_schedulable`
                    // says the attached session names a real scheduler
                    // task and that task is the one running -- the
                    // manifest's "sessions as schedulables" edge. The
                    // park is the ordinary `ipc::receive`: this task's
                    // TCB goes RUNNING -> BLOCKED inside the handler
                    // (the trap frame stays on its kernel stack, so the
                    // resume returns into this arm), and the next
                    // `ipc::send` moves it BLOCKED -> READY with the
                    // message waiting. In the seq form `a1` is the
                    // EMPTY-WAKE budget (the primitive's own bound);
                    // the payload form has no spare register for one
                    // (a1 is the buffer, a2 the size), so it waits for
                    // exactly ONE wake -- an empty wake there is
                    // ReceiveTimedOut, answered as EAGAIN.
                    //
                    // REFUSE EAGAIN BY NAME otherwise, and the reason is
                    // the honest one: the ring-3 proof's caller and the
                    // kernel's own self-test are task 0 (the boot
                    // context -- parking it would park the core), and the
                    // ring3-scheduler POC's sessions live in their own
                    // machine, not the house scheduler's TCB table.
                    // Parking an id nothing can wake would hang the
                    // caller FOREVER, so those callers get a released
                    // answer instead: come back and drain again.
                    if !crate::experimental::scheduler::session_is_schedulable() {
                        return EAGAIN;
                    }
                    let budget = if payload_form { 1 } else { a1 as u32 };
                    match crate::experimental::ipc::receive(dest, Some(budget)) {
                        Ok(m) => {
                            let v = answer(m);
                            if from_ring3 {
                                if payload_form {
                                    PAYLOAD_RECVD_VALUE.store(v as u64, Ordering::Relaxed);
                                } else {
                                    // A PARKED wake and an INSTANT drain are
                                    // different events and now have
                                    // different records: a proof that
                                    // wakes a parked receiver must be able
                                    // to tell the two apart, or it cannot
                                    // say whether the park ever happened.
                                    // The FIRST parked wake is the one the
                                    // proof judges (the first process's,
                                    // woken by the peer): later wakes are
                                    // counted, not recorded, so a second
                                    // one cannot quietly replace the value
                                    // under comparison.
                                    if BLOCKED_RECVS.fetch_add(1, Ordering::Relaxed) == 0 {
                                        WOKEN_RECVD_VALUE.store(v as u64, Ordering::Relaxed);
                                    }
                                }
                            }
                            v
                        }
                        Err(crate::experimental::ipc::IpcError::ReceiveTimedOut) => EAGAIN,
                        Err(crate::experimental::ipc::IpcError::NoSuchMailbox) => ENOENT,
                        // The arbiter's refusals cannot fire here (this
                        // arm never SENDS), but a fail-closed answer
                        // beats an unreachable!() on a released path.
                        Err(_) => EPERM,
                    }
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
            // is not a valid handle claim. The rights bound is the union of
            // every right ANY kind carries -- a region transfer asking
            // READ|WRITE|GRANT is a legitimate claim (and the machine's
            // attenuation check is what decides whether the holder may make
            // it), so bounding this by the ENDPOINT mask would refuse every
            // region handover as a malformed handle.
            if a0 > u32::MAX as u64
                || a_rights > crate::experimental::caps::rights::ALL as u64
            {
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
            // a_rights is NOT a rights mask on RECV: it is the
            // OUT-POINTER for the handle a transfer installs (the
            // release's second return register, expressed as a pointer
            // the way INPUT_POLL's record is). 0 means "I do not want
            // it". Without it the CALL/REPLY pair is unusable from ring
            // 3: the reply capability would arrive in the caller's table
            // and the caller could never learn its index.
            if a_xfer != u64::MAX {
                return EBADF;
            }
            // The user-range validator IS the ring-3 boundary check; a
            // cpl-0 caller is the kernel, whose buffers are its own
            // (a kernel stack address is not USER_ACCESSIBLE and would
            // otherwise be refused its own memory).
            if from_ring3 && a_rights != 0 && !crate::memory::paging::user_range_ok(a_rights, 8, true) {
                return EFAULT;
            }
            if a2 > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            if from_ring3 && a2 != 0 && !crate::memory::paging::user_range_ok(a1, a2 as usize, true) {
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
                    if a_rights != 0 {
                        // u64::MAX = "nothing was transferred" -- the
                        // same "none" sentinel the SEND side takes, so a
                        // zeroed out-slot can never be read as handle 0.
                        let h = m.transferred.map(|h| h as u64).unwrap_or(u64::MAX);
                        // FIRST REPLY wins (sentinel u64::MAX = unset):
                        // with two ring-3 processes draining calls, the
                        // LAST writer would make the read-back's compare
                        // depend on which process spoke most recently --
                        // and the leg it judges is the FIRST process's
                        // loopback, whose handle is what the pins name.
                        // The KIND filter is what keeps that true now that
                        // the same transfer field also carries REGION
                        // capabilities between processes: a region handover
                        // is a transfer, but it is not the call's reply, and
                        // recording its handle here would make the CALL leg
                        // compare against the wrong number.
                        if m.transferred_kind
                            == Some(crate::experimental::caps::Kind::Reply)
                            && CAP_XFER_HANDLE.load(Ordering::Relaxed) == u64::MAX
                        {
                            CAP_XFER_HANDLE.store(h, Ordering::Relaxed);
                        }
                        unsafe {
                            core::ptr::write_unaligned(a_rights as *mut u64, h);
                        }
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
        CALL_CAP_CALL => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            // THREE dials on one row, selected by a_xfer/a_rights:
            //
            //   a_xfer == u64::MAX, a_rights == 0 -- the plain call: the
            //     answer comes back later at the caller's own endpoint,
            //     and the row answers the endpoint's owner.
            //   a_xfer != u64::MAX and a_xfer != 0, a_rights >= 1 -- the
            //     BLOCKING call (the release's `call` + wait): a_xfer is
            //     the answer buffer, a_rights its length. The caller's
            //     tasks PARKS until the server answers, and the row
            //     returns the answer's byte count with the bytes in that
            //     buffer. The kernel main task (0) is refused EPERM:
            //     parking it would leave no one to run the scheduler's
            //     own boot chain.
            //   anything else -- a half claim, a zero-length answer
            //     buffer -- is a shape this row does not own (EBADF for
            //     the transfer field's sentinel, EINVAL for the rest).
            let blocking = a_xfer != u64::MAX;
            if blocking && (a_xfer == 0 || a_rights == 0) {
                return EINVAL;
            }
            if !blocking && a_rights != 0 {
                return EBADF;
            }
            if a2 > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            if from_ring3 && a2 != 0 && !crate::memory::paging::user_range_ok(a1, a2 as usize, false) {
                return EFAULT;
            }
            let mut bytes = [0u8; crate::experimental::caps::MAX_PAYLOAD];
            if a2 != 0 {
                unsafe {
                    core::ptr::copy_nonoverlapping(a1 as *const u8, bytes.as_mut_ptr(), a2 as usize);
                }
            }
            if !blocking {
                return match crate::experimental::caps::call_for(
                    crate::experimental::scheduler::session_identity(),
                    a0 as u32,
                    &bytes[..a2 as usize],
                ) {
                    Ok(owner) => owner as i64,
                    Err(e) => map_cap_error(e),
                };
            }
            // ---- the blocking dial ----
            if a_rights > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            // A ring-3 caller's answer buffer goes through the validator;
            // a ring-0 caller's pointer is the kernel's own memory, which
            // the validator is not written to accept (it answers only for
            // USER_ACCESSIBLE ranges). The boundary check exists for ring
            // 3, and that is exactly where it is applied.
            if from_ring3 && !crate::memory::paging::user_range_ok(a_xfer, a_rights as usize, true) {
                return EFAULT;
            }
            let me = crate::experimental::scheduler::current_task();
            if me == 0 {
                return EPERM; // the boot's own task cannot park on a call
            }
            // A ring-3 caller parks INSIDE its trap: the frame sits on the
            // task's own kernel stack (per-task TSS.RSP0, see the
            // scheduler's TCB), the switch saves that stack pointer, and
            // the resume returns through this same trap and `iretq`.
            // Nothing in between touches the caller's kernel stack, which
            // is why a parked ring-3 call is a park of a TASK and not a
            // re-entry.
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::call_wait_for(identity, me, a0 as u32, &bytes[..a2 as usize]) {
                Ok(_owner) => {}
                Err(e) => return map_cap_error(e),
            }
            // Register-then-park is the order that cannot lose a wake: a
            // server that answers between the two finds the wait slot
            // already there.
            crate::experimental::scheduler::block_current();
            match crate::experimental::caps::take_answer_for(identity) {
                Some(answer) => {
                    if answer.len() as u64 > a_rights {
                        // The answer was delivered and cannot fit: the
                        // caller is told by name rather than handed a
                        // truncated reply it would read as complete.
                        return EMSGSIZE;
                    }
                    if !answer.is_empty() {
                        unsafe {
                            core::ptr::copy_nonoverlapping(answer.as_ptr(), a_xfer as *mut u8, answer.len());
                        }
                    }
                    answer.len() as i64
                }
                // Woken without an answer: a real answer to a real state,
                // not a refusal to hide it (the 0x46 blocking dial's own
                // convention).
                None => EAGAIN,
            }
        }
        CALL_CAP_REPLY => {
            // The answer: authority-first (the Reply capability's kind and
            // SEND right are checked BEFORE anything is queued), then the
            // bytes, then the handle is consumed.
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            if a2 > crate::experimental::caps::MAX_PAYLOAD as u64 {
                return EMSGSIZE;
            }
            if from_ring3 && a2 != 0 && !crate::memory::paging::user_range_ok(a1, a2 as usize, false) {
                return EFAULT;
            }
            let mut bytes = [0u8; crate::experimental::caps::MAX_PAYLOAD];
            if a2 != 0 {
                unsafe {
                    core::ptr::copy_nonoverlapping(a1 as *const u8, bytes.as_mut_ptr(), a2 as usize);
                }
            }
            match crate::experimental::caps::reply_for(
                crate::experimental::scheduler::session_identity(),
                a0 as u32,
                &bytes[..a2 as usize],
            ) {
                Ok(_slot) => 0,
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_SHM_CLOSE => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a1 != 0 || a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            match crate::experimental::caps::close_region_for(
                crate::experimental::scheduler::session_identity(),
                a0 as u32,
            ) {
                Ok(()) => 0,
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_EXIT => {
            if a0 != 0 || a1 != 0 || a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let me = crate::experimental::scheduler::current_task();
            if me == 0 {
                // The boot's own task is the proof's read-back and the
                // scheduler's last runnable context: terminating it would
                // park the machine with a verdict nobody would print.
                return EPERM;
            }
            let identity = crate::experimental::scheduler::session_identity();
            // Capabilities first: the region counts must be consistent
            // BEFORE the identity record goes away, or a region would be
            // pinned alive by a holder that no longer exists.
            let released = crate::experimental::caps::release_holder_caps(identity);
            // ACCUMULATED, not overwritten: the interesting number is the
            // total the exits gave back, and the region's teardown depends
            // on all of them (the last holder frees the record).
            EXIT_RELEASED.fetch_add(released as u64, Ordering::Relaxed);
            let withdrew = crate::experimental::scheduler::withdraw_session();
            EXIT_SESSION.store(withdrew.map(|s| s as u64).unwrap_or(u64::MAX), Ordering::Relaxed);
            EXIT_TASK.store(me as u64, Ordering::Relaxed);
            EXIT_COUNT.fetch_add(1, Ordering::Relaxed);
            crate::experimental::scheduler::terminate_current();
        }
        CALL_CAP_SHM => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            // A geometry report carries no payload, no transfer and no
            // rights claim: the record pointer is a1 and everything else
            // must claim "none".
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && !crate::memory::paging::user_range_ok(a1, 24, true) {
                return EFAULT;
            }
            let identity = crate::experimental::scheduler::session_identity();
            let (region, held) = match crate::experimental::caps::region_cap_for(identity, a0 as u32) {
                Ok(v) => v,
                Err(e) => return map_cap_error(e),
            };
            let geometry = match crate::experimental::caps::region_geometry_of(region) {
                Ok(g) => g,
                Err(e) => return map_cap_error(e),
            };
            let mut rec = [0u8; 24];
            rec[0..8].copy_from_slice(&geometry.base.to_le_bytes());
            rec[8..16].copy_from_slice(&geometry.len.to_le_bytes());
            rec[16..24].copy_from_slice(&(held as u64).to_le_bytes());
            unsafe {
                core::ptr::copy_nonoverlapping(rec.as_ptr(), a1 as *mut u8, 24);
            }
            0
        }
        CALL_CAP_SHM_MAP => serve_region_request(a0, a1, a2, a_xfer, a_rights, from_ring3),
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
    let ipc_shape_ok = dispatch(CALL_IPC_SEND, 0, 0, 65, u64::MAX, 0, 0) == EMSGSIZE // payload wider than a message
        && dispatch(CALL_IPC_SEND, 0, 0, 8, u64::MAX, 0, 0) == EFAULT // payload at a null pointer
        && dispatch(CALL_IPC_SEND, 0, 7, 0, 9, 0, 0) == EINVAL // transfer-token claim
        && dispatch(CALL_IPC_SEND, 0, 7, 0, u64::MAX, 5, 0) == EINVAL // rights claim
        && dispatch(CALL_IPC_SEND, 0, u32::MAX as u64 + 1, 0, u64::MAX, 0, 0) == ERANGE
        && dispatch(CALL_IPC_SEND, 0x4000, 7, 0, u64::MAX, 0, 0) == ENOENT
        // The payload RECV dial: the release's buflen floor is refused
        // by name, and a null receive buffer never reaches the queue.
        && dispatch(CALL_IPC_RECV, 0, 0, 32, 0, 0, 0) == EINVAL // buflen < 64
        && dispatch(CALL_IPC_RECV, 0, 0, 64, 0, 0, 0) == EFAULT // null buffer
        && dispatch(CALL_IPC_RECV, 0x4000, 0, 0, 0, 0, 0) == ENOENT
        // The BLOCKING form (a_xfer == 1) is SERVED now; its refusals
        // are exercised by name at the kernel's own dispatch: a dead
        // destination, then the zero and over-wide budget claims on an
        // EXISTING endpoint. (The EAGAIN answer itself is queue-state-
        // dependent, so the ring-3 legs in prog_svc84.s own it -- there
        // the queue is provably empty after the programmable drain.)
        // The arm never parks on ANY caller: the session-waker sharp
        // edge in the arm itself explains why.
        && dispatch(CALL_IPC_RECV, 0x4000, 1, 0, 1, 0, 0) == ENOENT
        && dispatch(CALL_IPC_RECV, 0, 0, 0, 1, 0, 0) == EINVAL // budget 0 is no budget
        && dispatch(CALL_IPC_RECV, 0, u32::MAX as u64 + 1, 0, 1, 0, 0) == ERANGE
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
