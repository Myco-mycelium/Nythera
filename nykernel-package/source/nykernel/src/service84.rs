//! The `0x84` service surface: the gfx and input calls the release's shell
//! stack uses, as much of them as this kernel can honestly serve today.
//!
//! What this is: a ring-3-callable gate (`int $0x84`, DPL 3, call number in
//! `rax`) in the release's five-argument ABI -- `rdi/rsi/rdx/r10/r8` all
//! reach `dispatch` (the arguments each row consumes are named in the
//! table), the result goes back in `rax` -- that serves forty-five calls:
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
//! | 0x48 | `CAP_RECV` | a0=endpoint, a1/a2=recv buffer, a_rights = out-pointer for a transferred handle (0 = not wanted), a_xfer = out-pointer for the message's BADGE (u64::MAX = not wanted) | drains one message into user memory -> `(sender << 32) | len`; writes the sender capability's badge (`0` = unbadged) where asked; `-ENODATA` when real-and-empty, `-EFAULT` for a bad out-pointer |
//! | 0x49 | `INPUT_POLL` | a0=handle, a1=record ptr | authority-first Input-capability check, then the oldest RAW input byte -> 16-byte record; `1`, `0` when authorized-and-bare, its errno otherwise |
//! | 0x4a | `CAP_CALL` | a0=endpoint handle, a1/a2=request; a_xfer/a_rights = answer buffer + length for the BLOCKING dial | queues the request carrying a ONE-SHOT `Reply` capability naming the caller -> the endpoint's owner id; with an answer buffer the caller PARKS until the server answers and gets the answer's byte count; its errno otherwise |
//! | 0x50 | `CAP_SHM_UNMAP` | a0=region handle | drops the CALLER's window on a region it still holds: frames and every other holder's window stay -> `0`, `-ENOENT` when this holder has no window left, `-EACCES` for a handle of another kind |
//! | 0x4d | `CAP_SHM_CLOSE` | a0=region handle | gives ONE region capability back; the record dies with its LAST holder -> `0`, `-EBADF` (a double close), `-EACCES` for a handle of another kind |
//! | 0x4e | `CAP_EXIT` | nothing (every claim refused `-EINVAL`) | releases the caller's whole table (region counts included), withdraws its session, terminates its task; never returns. `-EPERM` for task 0 |
//! | 0x4c | `CAP_SHM` | a0=region handle, a1=24-byte record ptr | the kernel's own geometry of the mapped region (`{base, len, rights}`) -> `0`, `-EBADF` for a handle that names nothing, `-EACCES` for a handle of another kind |
//! | 0x4f | `CAP_SHM_MAP` | a0=length in bytes (4096-aligned, <= 16 pages), a1=32-byte record ptr | the release's region REQUEST: the kernel maps fresh pages at its own window, records the region, mints the caller a capability and answers `{base, len, rights, handle}` -> `0`; `-EINVAL` for a shape it does not own, `-EFAULT` for a bad record pointer, `-ENOMEM` when the kernel refused the mapping, `-ENOSYS` when no mapping source is installed |
//! | 0x4b | `CAP_REPLY` | a0=reply handle, a1/a2=answer | answers one call at the caller's own endpoint and CONSUMES the handle; `0`, `-EBADF` on a second use, `-EACCES` on a wrong-kind handle |
//! | 0x51 | `CAP_BADGE` | a0=endpoint handle, a1=nonzero badge | derives a badged copy in the caller's own table -> its handle; `-EACCES` without GRANT, for a zero badge or an already-badged source, `-EACCES` for another kind, `-EBADF` for an empty slot |
//! | 0x52 | `CAP_NOTIFY` | a0=endpoint handle, a1=bits | ORs payload-free signal bits into the endpoint (never queues, so it cannot fill the queue) -> `0`; its errno otherwise |
//! | 0x53 | `CAP_NOTIFY_TAKE` | a0=endpoint handle | the OWNER collects and clears its endpoint's signal word -> the bits; `-EACCES` for a non-owner or another kind, `-EBADF` for an empty slot |
//! | 0x54 | `CAP_DEVICE_INFO` | a0=resource handle, a1=window out-pointer (`u64::MAX` = none) | the release's resource kinds: `(kind_code << 32) \| rights` (2 block, 4 I/O ports, 32 framebuffer) and `(lo << 32) \| hi` where asked; `-EACCES` for a handle of another kind, `-EINVAL` for a payload-shaped call |
//! | 0x55 | `CAP_IOPORT_IN` | a0=IoPort handle, a1=port | authority-first (kind, READ, window containment) then the port's byte -> `0..255`; `-EACCES` for a missing right or a port outside the window |
//! | 0x56 | `CAP_IOPORT_OUT` | a0=IoPort handle, a1=port, a2=byte | same checks with WRITE before the `out` is issued -> `0` |
//! | 0x57 | `CAP_FB_READ` | a0=FrameBuffer handle, a1=`(x<<32)\|y` | kind + READ, row inside the granted window -> the RAW scan-out word; `-EACCES` outside the window or for another kind |
//! | 0x58 | `CAP_FB_WRITE` | a0=FrameBuffer handle, a1=`(x<<32)\|y`, a2=word | kind + WRITE, row inside the window -> stores the word -> `0` |
//! | 0x59 | `CAP_BLOCK_READ` | a0=BlockDevice handle, a1=LBA, a2=buffer, a_xfer=bytes (1..16384) | kind + READ + EVERY sector of the span inside the window, then the span read sector-by-sector THROUGH THE SECTOR CACHE into the buffer -> the byte count; `-EACCES` outside the window, `-EIO` on a failed read |
//! | 0x5a | `CAP_BLOCK_INFO` | a0=BlockDevice handle, a1=24-byte record | kind + READ, then the drive's own geometry (`{sector size, sector count, 0}`) -> `0`; `-EIO` when it will not answer |
//! | 0x5b | `CAP_BLOCK_WRITE` | a0=BlockDevice handle, a1=LBA, a2=buffer, a_xfer=bytes (1..2048) | kind + WRITE + window containment, then one sector written (LBA28) -> the byte count; `-EROFS` for read-only ATAPI media, `-EACCES` outside the window or for another kind, `-ENODEV`/`-EIO` |
//! | 0x5c | `CAP_BLOCK_CACHE_STATS` | a0=BlockDevice handle, a1=16-byte record | kind + READ, then `{hits, misses}` of the kernel's sector cache -> `0`; a repeat read through 0x59 is answered from memory and shows as a hit |
//! | 0x5d | `PLATFORM_GET` | -- | the session identity the kernel presents, as `Platform::ALL`'s index (0 nyrqis, 1 android, 2 windows, 3 macos, 4 console) |
//! | 0x5e | `PLATFORM_SET` | a0 = identity index | repaints the device chrome, applies that identity through `platform_mode::switch`, and reads its landmark back -> `0`; `-EINVAL` for an index no identity owns, `-ENODEV` when no display is bound, `-EIO` when the skin failed its read-back |
//! | 0x5f | `PLATFORM_INFO` | a0 = 48-byte record ptr | writes `{identity, form factor, width, height, status-bar height, accent RGB}` as six u64s -> `0`; `-EFAULT` for a pointer the user-range walker refuses, `-ENODEV` when no display is bound |
//! | 0x60 | `PLATFORM_MODE_SET` | a0 = form-factor index | re-renders the device chrome at that form factor, re-applies the current identity, and publishes the new mode only after BOTH read-backs verify -> `0`; `-EINVAL` for an index no form factor owns, `-ENODEV` when no display is bound, `-EIO` when either render failed its read-back (nothing is published then) |
//! | 0x61 | `DISPLAY_ACQUIRE` | — | claims the display for the CALLER's gate session -> `0` (idempotent for the owner); `-EBUSY` when another session owns it, `-EPERM` from ring 0 (the kernel is not a tenant of its own display) |
//! | 0x62 | `DISPLAY_RELEASE` | — | gives ownership back -> `0`; `-EACCES` for any session that does not own it. A session that EXITs releases it automatically |
//! | 0x63 | `DISPLAY_OWNER` | — | the owning session id, or `0` when nobody owns the display. While an owner exists, `PLATFORM_SET` / `PLATFORM_MODE_SET` from any OTHER ring-3 session are refused `-EACCES` |
//! | 0x64 | `DISPLAY_SERVE` | a0 = policy mask (`1` identity, `2` form factor, `3` both) | becomes the display SERVER -> `0`, or `-EBUSY` beside a server or an exclusive owner, `-EINVAL` for an empty/unknown mask, `-EPERM` from ring 0. Re-publishing from the same session updates the policy |
//! | 0x65 | `DISPLAY_ATTACH` | a0 = requested mask | attaches the caller's session as a client, granted `a0 & policy` -> `(slot << 32) | grant`; `-ENODEV` when no server runs, `-EBUSY` when already attached or the four slots are full, `-EACCES` when the policy grants none of the bits, `-EINVAL`, `-EPERM` |
//! | 0x66 | `DISPLAY_DETACH` | — | frees the caller's slot -> `0`; `-EACCES` when the caller holds none |
//! | 0x67 | `DISPLAY_CLIENT` | a0 = session to query (`0` = the caller) | that session's `(slot << 32) | grant`, or `0` when it is not a client. A query, never authority |
//! | 0x68 | `DISPLAY_STATUS` | — | packed `server << 32 | client count << 16 | caller grant`, where the server reports `3` for itself |
//! | 0x69 | `DISPLAY_SURFACE` | a1 = 32-byte record | gives the CALLER's client slot a SURFACE: the kernel requests one page, maps it into a window it chooses, mints the caller a handle over it, and writes `{base, len, rights, handle}` to `a1` -> `0`; `-EACCES` when the caller is not a client, `-EBUSY` when it already has one, `-EFAULT` for a bad record pointer, `-EPERM` from ring 0. The client draws into `base` directly; the region machine mirrors the same frames into the OTHER address space, which is how the server reads them |
//! | 0x6c | `DISPLAY_SURFACE_RELEASE` | — | a client gives its surface tile back: the window's frames, its mirror and its slot return to the display's reclaiming window table, and the answer packs `live << 32 | issued` so a caller can watch the issue count climb while the live count stays flat |
//! | 0x6b | `DISPLAY_SPACES` | — | packed `registry count << 32 | server-registered << 1 | caller-registered`: the session-to-address-space registry, as three facts. No address is handed to ring 3 |
//! | 0x6a | `DISPLAY_COMPOSE` | — | SERVER only: blits every client surface (read through the mirrored window at the region's base) onto the bound display and reads its own work back -> the number of tiles composed; `-EACCES` for anyone but the server, `-ENODEV` with no display, `-EIO` when a blit or read-back failed |
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

use x86_64::instructions::port::Port;
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

/// 0x50 `CAP_SHM_UNMAP`: a0 = a REGION handle the caller KEEPS. Drops the
/// caller's own WINDOW on the region -- the mapping in its address space --
/// and nothing else: the frames are not returned (the region still owns
/// them and another holder may be reading them), the other holders' windows
/// are untouched, and the capability still answers geometry afterwards. It
/// is the complement of 0x4d, which gives the capability back and leaves the
/// window until the region itself dies, and the counterpart of a kernel-side
/// revocation, which takes the frames from under every holder.
///
/// Returns 0, `ENOENT` when this holder has no window to drop (never had
/// one, or already dropped it -- refused by name rather than answered as a
/// second success), `EACCES` for a handle of another kind, `EBADF` for a
/// handle that names nothing. Every other argument must claim nothing
/// (`a1 == 0`, `a2 == 0`, `a_xfer == u64::MAX`, `a_rights == 0`).
pub const CALL_CAP_SHM_UNMAP: u64 = 0x50;

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

/// The release's `badge`: a0 = an endpoint capability handle the caller
/// holds, a1 = a NON-ZERO u32 tag. The machine installs a badged copy in
/// the CALLER's own table and returns its handle; every message sent
/// through the copy carries the tag, so a server can tell its clients
/// apart without trusting payload bytes. The source must be unbadged and
/// carry GRANT (a badge is minted once and cannot be rewritten or
/// widened), which is why a zero badge or a second badge is `EACCES` by
/// name. Every other argument must claim nothing (`a2 == 0`,
/// `a_xfer == u64::MAX`, `a_rights == 0`).
pub const CALL_CAP_BADGE: u64 = 0x51;

/// The release's `notify` (NPS-003 §3): a0 = an endpoint capability
/// handle with SEND, a1 = signal bits. The bits are OR-ed into the
/// endpoint's notification word; nothing is queued, so this row can never
/// return `-EAGAIN`/`-ENOMEM` for queue pressure and cannot be used to
/// flood the message queue. The same checks as SEND (kind, right, live
/// generation) apply. Every other argument must claim nothing.
pub const CALL_CAP_NOTIFY: u64 = 0x52;

/// The receiving half of `notify`: a0 = an endpoint capability handle in
/// the caller's own table, and the caller must be that endpoint's OWNER
/// -- a SEND-only grant cannot take another holder's signals. Returns the
/// accumulated bits and CLEARS them, so a second take answers 0. Every
/// other argument must claim nothing.
pub const CALL_CAP_NOTIFY_TAKE: u64 = 0x53;

/// The release's RESOURCE kinds (BlockDevice / IoPort / FrameBuffer):
/// a0 = a capability handle in the caller's own table, a1 = an OPTIONAL
/// out-pointer (u64::MAX = "no window wanted") where the kernel writes
/// `(lo << 32) | hi`, the window the capability actually covers. Returns
/// `(kind_code << 32) | rights`, with the release's kind bits: 2 block
/// device, 4 I/O ports, 32 framebuffer. Any other kind is `EACCES` by
/// name (an endpoint handle is not a device), and a bad out-pointer is
/// `EFAULT` BEFORE anything is written. Every other argument must claim
/// nothing.
pub const CALL_CAP_DEVICE_INFO: u64 = 0x54;

/// The I/O-port DATA PLANE, read half: a0 = an IoPort capability handle,
/// a1 = a port number. The machine checks kind, READ and that the port is
/// inside the capability's window BEFORE the port is touched -> the byte
/// read (0..255). A port one past the window is `EACCES` by name, like a
/// missing right: the window IS the authority.
pub const CALL_CAP_IOPORT_IN: u64 = 0x55;

/// The I/O-port DATA PLANE, write half: a0 = an IoPort capability handle,
/// a1 = a port number, a2 = the byte to write (0..255). Same authority
/// checks with WRITE before the `out` is issued -> `0`.
pub const CALL_CAP_IOPORT_OUT: u64 = 0x56;

/// The framebuffer DATA PLANE, read half: a0 = a FrameBuffer capability
/// handle, a1 = `(x << 32) | y`. The machine checks kind and READ, then
/// the row must lie inside the capability's row window and the pixel
/// inside the bound display -> the RAW scan-out word.
pub const CALL_CAP_FB_READ: u64 = 0x57;

/// The framebuffer DATA PLANE, write half: a0 = a FrameBuffer capability
/// handle, a1 = `(x << 32) | y`, a2 = the raw scan-out word to store.
/// Same checks with WRITE before the store -> `0`.
pub const CALL_CAP_FB_WRITE: u64 = 0x58;

/// The block-device DATA PLANE: a0 = a BlockDevice capability handle,
/// a1 = a logical block address, a2 = a user buffer, a_xfer = how many
/// bytes to copy (1..16384). The machine checks kind, READ and that EVERY
/// sector the span touches lies inside the sector window BEFORE the device
/// is touched, then reads the span sector-by-sector (2048 bytes on ATAPI,
/// 512 on ATA) and copies the requested prefix -> the byte count.
/// `-EACCES` for a missing right or a sector outside the window,
/// `-EFAULT` for a bad buffer, `-EINVAL` for a bad count or a shape
/// claim. The capability names its DEVICE (IDE channel), so a handle
/// whose channel has no device fails at the drive (`-EIO`).
pub const CALL_CAP_BLOCK_READ: u64 = 0x59;

/// The block device's GEOMETRY: a0 = a BlockDevice capability handle,
/// a1 = a 24-byte record `{sector size: u64, sector count: u64,
/// reserved: u64}`. Kind + READ are checked, then the drive is asked
/// (ATAPI READ CAPACITY, or IDENTIFY DEVICE for an ATA disk) -> `0` with
/// the record filled; `-EACCES` for a wrong kind or missing READ,
/// `-EIO` when the drive will not answer (an empty channel included).
pub const CALL_CAP_BLOCK_INFO: u64 = 0x5a;

/// The block device's WRITE half: a0 = a BlockDevice capability handle,
/// a1 = a logical block address, a2 = a user buffer, a_xfer = how many
/// bytes of the sector to write (1..2048). Kind, WRITE and window
/// containment are checked BEFORE the device is touched, then one sector
/// is written (LBA28 WRITE SECTORS). The kernel's packet path is
/// read-only, so an ATAPI device answers `-EROFS` rather than issuing a
/// command the media cannot take; `-EACCES` for a missing right, a wrong
/// kind or an LBA outside the window, `-EIO` on a failed write. The
/// capability names its device; a missing one surfaces as `-EIO`.
pub const CALL_CAP_BLOCK_WRITE: u64 = 0x5b;

/// The block device's CACHE: a0 = a BlockDevice handle, a1 = a 16-byte
/// record `{hits: u64, misses: u64}`. Kind + READ are checked, then the
/// kernel answers the sector cache's own counters -> `0`. A repeated read
/// through 0x59 is answered from memory and shows up here as a hit.
pub const CALL_CAP_BLOCK_CACHE_STATS: u64 = 0x5c;
/// `PLATFORM_GET` (0x5d): the session identity the kernel presents.
pub const CALL_PLATFORM_GET: u64 = 0x5d;
/// `PLATFORM_SET` (0x5e): switch the session identity (and repaint it).
pub const CALL_PLATFORM_SET: u64 = 0x5e;
/// `PLATFORM_INFO` (0x5f): the session shape in one record.
pub const CALL_PLATFORM_INFO: u64 = 0x5f;
/// `PLATFORM_MODE_SET` (0x60): request a form factor at runtime.
pub const CALL_PLATFORM_MODE_SET: u64 = 0x60;
/// `DISPLAY_ACQUIRE` (0x61): claim the display for the caller's session.
pub const CALL_DISPLAY_ACQUIRE: u64 = 0x61;
/// `DISPLAY_RELEASE` (0x62): give ownership back.
pub const CALL_DISPLAY_RELEASE: u64 = 0x62;
/// `DISPLAY_OWNER` (0x63): who owns the display right now.
pub const CALL_DISPLAY_OWNER: u64 = 0x63;
/// `DISPLAY_SERVE` (0x64): become the display server, publishing a policy.
pub const CALL_DISPLAY_SERVE: u64 = 0x64;
/// `DISPLAY_ATTACH` (0x65): attach the caller's session as a client.
pub const CALL_DISPLAY_ATTACH: u64 = 0x65;
/// `DISPLAY_DETACH` (0x66): give the caller's client slot back.
pub const CALL_DISPLAY_DETACH: u64 = 0x66;
/// `DISPLAY_CLIENT` (0x67): a session's client grant, or zero.
pub const CALL_DISPLAY_CLIENT: u64 = 0x67;
/// `DISPLAY_STATUS` (0x68): the supervised display in one word.
pub const CALL_DISPLAY_STATUS: u64 = 0x68;
/// `DISPLAY_SURFACE` (0x69): claim the caller's own surface tile.
pub const CALL_DISPLAY_SURFACE: u64 = 0x69;
/// `DISPLAY_COMPOSE` (0x6a): server-side composition of every surface.
pub const CALL_DISPLAY_COMPOSE: u64 = 0x6a;
/// `DISPLAY_SPACES` (0x6b): the session -> address-space registry, as facts.
pub const CALL_DISPLAY_SPACES: u64 = 0x6b;
/// `DISPLAY_SURFACE_RELEASE` (0x6c): give a surface tile back, answering the
/// display's packed window counts.
pub const CALL_DISPLAY_SURFACE_RELEASE: u64 = 0x6c;

/// The block data plane's largest single call: eight 2048-byte sectors.
/// A span keeps one call from becoming an unbounded device loop while
/// still making a descriptor chain (or a 4 KiB page) one request.
pub const MAX_BLOCK_SPAN: u64 = 8 * 2048;

/// Linux's `ENOSYS`, and the release's convention: negative errno in `rax`.
pub const ENOSYS: i64 = -38;
/// Linux `EIO`: the device is present but the I/O itself failed (a
/// timeout, an error bit, a short data phase).
pub const EIO: i64 = -5;

/// Linux `EROFS`: the device is real and writable by code, but this MEDIA
/// cannot take a write (the ATAPI packet path is read-only here).
pub const EROFS: i64 = -30;

/// Linux `ENODEV`: the call names hardware this machine does not have.
/// The block-data row uses it rather than pretending a sector was read.
pub const ENODEV: i64 = -19;

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
        // NPS-007 §3: the receiver's container does not admit this kind.
        // The capability is valid and the sender may hold it; DELIVERY is
        // what the policy refuses, so EACCES by name.
        Contained => EACCES,
        // A handle resolved but named a DIFFERENT kind of object: that is
        // an authorization failure at the type level (the release maps
        // its WrongType to EACCES-family refusals; EACCES by name),
        // distinct from EBADF's "no such handle".
        WrongType => EACCES,
        NoRight | Revoked | NotOwner | NotOwned => EACCES,
        // "Nothing is mapped there": the row that only drops a window has
        // nothing to drop, which is a different answer from "no right".
        NotMapped => ENOENT,
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

/// The other end of [`attach_gate_session`]: a ring-3 process that dies
/// WITHOUT its own EXIT row. The revocation leg's armed fault terminates a
/// task that still holds a capability over the region the kernel just took
/// back, so its table is released and its session withdrawn here -- the same
/// two steps `CAP_EXIT` performs, in the same order (capabilities first, so
/// no region is pinned by an identity that no longer exists). The region is
/// already dead at this point, so the release spends a count and returns no
/// pages: the `live` guard is what makes that a no-op and not a double free.
///
/// Returns how many capabilities were released, for the caller's record.
pub fn abandon_gate_session() -> usize {
    let identity = crate::experimental::scheduler::session_identity();
    // Display state FIRST, and through the same release path CAP_EXIT uses: a
    // task that dies holding a surface tile must give its window back here,
    // in this space -- the only one that maps it -- or the tile outlives the
    // session that could have freed it. (Order matches CAP_EXIT: the tile's
    // capability is closed by `release_tile`, then the rest of the table.)
    if let Some(tile) = crate::display::release_on_withdrawal(identity as u64) {
        let _ = release_tile(identity as u64, tile);
    }
    let released = crate::experimental::caps::release_holder_caps(identity);
    let _ = crate::experimental::scheduler::withdraw_session();
    released
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
/// The last identity a ring-3 caller read / set, for the proof's read-back.
static RET_PLATFORM: AtomicI64 = AtomicI64::new(-1);
static RET_PLATFORM_SET: AtomicI64 = AtomicI64::new(i64::MIN);
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
// ---- the REFUSAL MATRIX ---------------------------------------------------
//
// The proof's other legs prove rows by making them SUCCEED and reading the
// answer back. That leaves a blind spot the README named: a row can answer
// every well-formed call correctly and still mis-handle the malformed one.
// This table is the other half -- one cell per row, each a call ring 3 makes
// that the row MUST refuse, and the typed errno it must refuse with.
//
// Two rules decide what may go in here, both learned by breaking the proof:
//
// 1. A cell may only name rows that validate BEFORE they act. If the row
//    allocates, maps, writes or queues before it discovers the argument is
//    bad, the probe mutates live state and the other legs go red -- which is
//    the row's real order of operations, not a false alarm. The first
//    attempt at this matrix included CAP_SHM_MAP (0x4f) with a
//    non-page-aligned length and went red in four unrelated legs, and the row
//    was blamed for it -- "it aligns the length instead of refusing it". That
//    was wrong. `serve_region_request` checks `a0 % 4096` and answers EINVAL
//    BEFORE it asks the mapping source for a single frame; the red legs were
//    the overlapping answer slots (rule 2). The blame cost a round trip and
//    is why 0x4f is now the seventh cell rather than an exception: a row is
//    not guilty of what the harness did to it.
// 2. Answers live in their own page. REFUSAL_MATRIX_SLOT is 12288, the first
//    byte of the FOURTH results page, which exists only because this table
//    does (svc84_proof.rs maps four, not three). At 12160 the slots
//    overlapped the ring-3 stack and the same three legs failed twice over.
//
// A cell may also probe the WRITE side of a pointer check. `user_range_ok`
// refuses a page that is PRESENT and USER_ACCESSIBLE but not WRITABLE when
// the row is about to write into it, and until now every pointer cell was
// only non-user -- which the read and the write check both refuse, so it
// proved neither. The proof maps `svc84_proof::PROOF_RO`, a fifth page that
// is user-accessible and deliberately NOT writable, and the block and region
// rows whose destination is a caller pointer are handed it: the cell is
// EFAULT only because the row asked for a WRITABLE range. A row that used
// `write = false` on an out-pointer, or skipped the check, answers 0 here
// (or faults ring 3), and the two cells are what hold them to it.
//
// The errnos are compared as data: serve_proof_done reads each answer out of
// user memory, compares it against the row's `want`, prints any cell that
// disagrees BY NAME, and packs the low byte of every answer into one word so
// a wrong cell is readable out of the serial log without a re-run.

/// One cell of the refusal matrix: the call ring 3 made, the errno the row
/// must answer with, and what was wrong with the arguments, for the line the
/// verdict prints when the cell disagrees.
pub struct RefusalRow {
    /// The `int 0x84` call number (`CALL_*`).
    pub call: u64,
    /// The typed errno that call must be refused with.
    pub want: i64,
    /// The bad argument, printed by name when this cell disagrees.
    pub what: &'static str,
}

/// First byte of the matrix's answers: the fourth results page. The program
/// writes cell `i` here at `8 * i`; the kernel leg reads it back from the
/// same slot, so a shrink of the mapping is a page fault rather than a
/// silent overwrite of the leg above it.
pub const REFUSAL_MATRIX_SLOT: u64 = 12288;

/// The cells, in the order `prog_svc84.s` writes them. Every one of these
/// rows settles its arguments before it touches anything, which is what lets
/// the probes run inside a live proof.
pub const REFUSAL_MATRIX: [RefusalRow; 25] = [
    RefusalRow {
        call: CALL_GFX_MODE_INTO,
        want: EFAULT,
        what: "GFX_MODE_INTO with a misaligned, non-user pointer",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_INFO,
        want: EBADF,
        what: "CAP_BLOCK_INFO with a forged block handle",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_CACHE_STATS,
        want: EBADF,
        what: "CAP_BLOCK_CACHE_STATS with a forged block handle",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_WRITE,
        want: EBADF,
        what: "CAP_BLOCK_WRITE with a forged block handle",
    },
    RefusalRow {
        call: CALL_DISPLAY_DETACH,
        want: EACCES,
        what: "DISPLAY_DETACH with no slot to give back",
    },
    RefusalRow {
        call: CALL_DISPLAY_SURFACE_RELEASE,
        want: ENOENT,
        what: "DISPLAY_SURFACE_RELEASE with no tile to give back",
    },
    RefusalRow {
        call: CALL_CAP_SHM_MAP,
        want: EINVAL,
        what: "CAP_SHM_MAP with a length that is not a multiple of the page size",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_READ,
        want: EINVAL,
        what: "CAP_BLOCK_READ with a zero transfer length",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_WRITE,
        want: EINVAL,
        what: "CAP_BLOCK_WRITE with a zero transfer length",
    },
    RefusalRow {
        call: CALL_CAP_SHM_MAP,
        want: EINVAL,
        what: "CAP_SHM_MAP with a zero length",
    },
    RefusalRow {
        call: CALL_CAP_SHM_MAP,
        want: EFAULT,
        what: "CAP_SHM_MAP with a misaligned, non-user record pointer",
    },
    RefusalRow {
        call: CALL_CAP_SHM,
        want: EFAULT,
        what: "CAP_SHM with a misaligned, non-user record pointer",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_INFO,
        want: EFAULT,
        what: "CAP_BLOCK_INFO with a misaligned, non-user record pointer",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_READ,
        want: EBADF,
        what: "CAP_BLOCK_READ with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_INFO,
        want: EBADF,
        what: "CAP_BLOCK_INFO with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_WRITE,
        want: EBADF,
        what: "CAP_BLOCK_WRITE with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_CACHE_STATS,
        want: EBADF,
        what: "CAP_BLOCK_CACHE_STATS with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_READ,
        want: EINVAL,
        what: "CAP_BLOCK_READ with a length past MAX_BLOCK_SPAN",
    },
    RefusalRow {
        call: CALL_CAP_SHM_MAP,
        want: EINVAL,
        what: "CAP_SHM_MAP with a length past MAX_REGION_REQUEST_PAGES",
    },
    RefusalRow {
        call: CALL_CAP_BLOCK_READ,
        want: EFAULT,
        what: "CAP_BLOCK_READ writing into a read-only user page",
    },
    RefusalRow {
        call: CALL_CAP_SHM_MAP,
        want: EFAULT,
        what: "CAP_SHM_MAP recording into a read-only user page",
    },
    RefusalRow {
        call: CALL_CAP_SHM,
        want: EBADF,
        what: "CAP_SHM with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_SHM_CLOSE,
        want: EBADF,
        what: "CAP_SHM_CLOSE with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_SHM_UNMAP,
        want: EBADF,
        what: "CAP_SHM_UNMAP with a handle above u32::MAX",
    },
    RefusalRow {
        call: CALL_CAP_NOTIFY,
        want: EBADF,
        what: "CAP_NOTIFY with a handle above u32::MAX",
    },
];

// ---- verdict provenance ---------------------------------------------------
//
// `verdict_word!` is the ONLY place the words "matches"/"MISMATCH" are
// written. `claim!` is the only way a leg reaches the printed verdict: it
// folds the leg's own value into the running `ok` AND yields the word printed
// beside that same value.
//
// This is not tidiness. The block-info bug was a leg that PRINTED one
// record and DECIDED on another; the renames fixed that instance, and this
// fixes the shape: a printed leg that is not counted (or a counted leg that
// is not printed) is now impossible to write, because both come from one
// expression. A hand-maintained `a_ok && b_ok && ...` list cannot say that --
// it is exactly how eight printed legs came to be missing from this
// function's own verdict.
macro_rules! verdict_word {
    ($cond:expr) => {
        if $cond {
            "matches"
        } else {
            "MISMATCH"
        }
    };
}

// `claim!(ok, leg_ok)` -> the word to print; `ok` accumulates as a side
// effect, so the leg cannot be printed without being counted.
macro_rules! claim {
    ($acc:ident, $leg:expr) => {{
        let leg_now: bool = $leg;
        $acc &= leg_now;
        verdict_word!(leg_now)
    }};
}

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
    // The HANDED-ON region's block (BUF+2344..+2416, nine qwords): the
    // request's ack, the base the kernel chose for it, the minted handle,
    // the length, the transfer's ack, the close's ack, the process's own
    // marker read back AFTER that close (the page survived it), the
    // consumed handle's refusal, and the WIDER ask's refusal (the
    // attenuation negative control: it must be refused, not clipped).
    if !crate::memory::paging::user_range_ok(a0 + 2344, 200, false) {
        return EFAULT;
    }
    // The WINDOW leg's own words (2026-11-08), read by offset because the
    // program's record is its own layout: the 0x50 row's answer and its
    // refusal when repeated, the geometry the surviving capability still
    // answers, and the request that made the region in the first place.
    let unmap_ack = unsafe { core::ptr::read_unaligned((a0 + 2424) as *const i64) };
    let unmap_again = unsafe { core::ptr::read_unaligned((a0 + 2432) as *const i64) };
    let unmap_geom_ack = unsafe { core::ptr::read_unaligned((a0 + 2440) as *const i64) };
    let unmap_geom_base = unsafe { core::ptr::read_unaligned((a0 + 2448) as *const i64) };
    let unmap_req_ack = unsafe { core::ptr::read_unaligned((a0 + 2504) as *const i64) };
    let unmap_base = unsafe { core::ptr::read_unaligned((a0 + 2512) as *const i64) };
    let unmap_len = unsafe { core::ptr::read_unaligned((a0 + 2520) as *const i64) };
    let unmap_handle = unsafe { core::ptr::read_unaligned((a0 + 2536) as *const i64) };
    let unmap_marker = unsafe { core::ptr::read_unaligned((a0 + 2488) as *const i64) };
    let mut mapx = [0i64; 9];
    for (i, slot) in mapx.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2344 + 8 * i as u64) as *const i64) };
    }
    // The PEER process's page (a1): its own results, read from ITS
    // memory after its task was TERMINATED -- the strongest form of
    // "this is what the other process observed".
    //
    // Since 2026-11-08 a1 IS THE SAME VIRTUAL ADDRESS as a0 (both
    // processes are linked and given their pages at 0x60000010000), so the
    // read has to happen under the peer's CR3: with the first program's
    // tables still active the kernel would read the FIRST program's page
    // and call it the peer's. The word at offset 8 is captured on each
    // side of the switch for the verdict -- same VA, two pages, and the
    // peer's value is the region base its own geometry call answered, so a
    // wrong space or a shared page would show up as equal words.
    let same_va_first = unsafe { core::ptr::read_unaligned((a0 + 8) as *const u64) };
    // The BADGE/NOTIFY block (BUF+2600..+2720, fifteen qwords): the notify
    // row's ack, the owner's take of the OR-ed bits, the second take's 0
    // (the word was cleared), then three by-name refusals -- notify
    // through an Input capability (WrongType -> EACCES), notify through
    // an empty slot (EBADF), and a non-owner's take (NotOwner -> EACCES);
    // then the badge row's minted handle, a zero badge's refusal, a
    // wrong-kind badge's refusal, a forged handle's refusal, and the
    // re-badge refusal through the handle the kernel itself minted. The
    // bits are the release's OR semantics (one notify of 0x5 here; the
    // machine's self-test pins the multi-call OR).
    //
    // The last four words are the RECEIVE-side badge: a send through the
    // badged handle, a RECV with a NULL badge out-pointer (refused EFAULT
    // BEFORE the drain), the same RECV with a valid pointer (the message
    // 18m did not consume, its packed identity, and the tag the kernel
    // wrote), all compared against the tag the program itself asked for.
    if !crate::memory::paging::user_range_ok(a0 + 2600, 120, false) {
        return EFAULT;
    }
    let mut bn = [0i64; 15];
    for (i, slot) in bn.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2600 + 8 * i as u64) as *const i64) };
    }
    let want_badge_recv =
        ((crate::svc84_proof::FIRST_SESSION as u64) << 32 | 6) as i64;
    let notify_ok = bn[0] == 0
        && bn[1] == 5
        && bn[2] == 0
        && bn[3] == EACCES
        && bn[4] == EBADF
        && bn[5] == EACCES
        && bn[6] >= 0
        && bn[7] == EACCES
        && bn[8] == EACCES
        && bn[9] == EBADF
        && bn[10] == EACCES
        && bn[11] == 0
        && bn[12] == want_badge_recv
        && bn[13] == 0xBEEF
        && bn[14] == EFAULT;
    // The RESOURCE-KIND block (BUF+2744..+2800, seven qwords): the
    // DEVICE_INFO answer for the block device (code 2, rights 14), for the
    // I/O-port capability (code 4) and the window it wrote out, for the
    // framebuffer (code 32) and the ROW count it wrote out, then the two
    // by-name refusals -- an endpoint handle asked for a device window,
    // and a call whose shape claims a payload.
    if !crate::memory::paging::user_range_ok(a0 + 2744, 56, false) {
        return EFAULT;
    }
    let mut dev = [0i64; 7];
    for (i, slot) in dev.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2744 + 8 * i as u64) as *const i64) };
    }
    let want_fb_rows = crate::devices::framebuffer::bound_mode()
        .map(|(_, h)| h as i64)
        .unwrap_or(0);
    let device_ok = dev[0] == 0x2_0000_000E
        && dev[1] == 0x4_0000_000E
        && dev[2] == 0x80_0000_0081
        && dev[3] == 0x20_0000_000E
        && dev[4] == want_fb_rows
        && dev[5] == EACCES
        && dev[6] == EINVAL;
    // The DATA-PLANE block (BUF+2800..+2856, seven qwords): the port
    // write's ack, the port read's byte (compared against the kernel's
    // OWN read of 0x80 at verdict time), a port one past the window
    // refused EACCES, the framebuffer write's ack, the raw word read
    // back, a row outside the granted window refused EACCES, and a
    // framebuffer operation through an I/O-port handle refused EACCES.
    if !crate::memory::paging::user_range_ok(a0 + 2800, 56, false) {
        return EFAULT;
    }
    let mut data = [0i64; 7];
    for (i, slot) in data.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2800 + 8 * i as u64) as *const i64) };
    }
    let kernel_port80 = unsafe { Port::<u8>::new(0x80).read() } as i64;
    let data_ok = data[0] == 0
        && data[1] == kernel_port80
        && data[2] == EACCES
        && data[3] == 0
        && data[4] == 0x00ff00ff
        && data[5] == EACCES
        && data[6] == EACCES;
    // The BLOCK-DEVICE block (BUF+2856..+2896, five qwords): the read's
    // byte count, the first 8 bytes of LBA 16 (the ISO volume
    // descriptor, whose bytes 1..6 must be "CD001"), an LBA past the
    // window refused EACCES, a zero-length copy refused EINVAL, and a
    // block read through another kind's handle refused EACCES.
    if !crate::memory::paging::user_range_ok(a0 + 2856, 40, false) {
        return EFAULT;
    }
    let mut block = [0i64; 5];
    for (i, slot) in block.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2856 + 8 * i as u64) as *const i64) };
    }
    let iso_descriptor = u64::from_le_bytes([
        0x01, b'C', b'D', b'0', b'0', b'1', 0x01, 0x00,
    ]) as i64;
    let block_ok = block[0] == 8
        && block[1] == iso_descriptor
        && block[2] == EACCES
        && block[3] == EINVAL
        && block[4] == EACCES;
    // The BLOCK-INFO block (BUF+2896..+2936, five qwords): the geometry
    // row's ack, the sector size and sector count it wrote, the reserved
    // word, and a geometry call through the framebuffer handle refused
    // EACCES before any device register is touched.
    if !crate::memory::paging::user_range_ok(a0 + 2896, 40, false) {
        return EFAULT;
    }
    // `block_info`, not `info`: this function also builds the PLATFORM_INFO
    // record into a local called `info` (BUF+11400) a few hundred lines
    // below, and Rust's shadowing made the print at the end of this file
    // report THAT array under the BLOCK-INFO label -- {identity 2, mode 1,
    // 1024, 768, status 28} came out as "ack 0x2, sector-size 0x1,
    // sectors 0x400, reserved 0x300, wrong-kind 0x1c". The check below was
    // reading the right words the whole time, which is why the leg printed
    // nonsense and still said "matches".
    let mut block_info = [0i64; 5];
    for (i, slot) in block_info.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 2896 + 8 * i as u64) as *const i64) };
    }
    let want_sector_size = crate::ata::boot_channel()
        .map(crate::ata::sector_size)
        .unwrap_or(0) as i64;
    // Named for its own leg on purpose: `info_ok` below is the PLATFORM
    // info leg's result, and an earlier version of this file reused the same
    // name -- which shadowed THIS one, so the block geometry claim never
    // reached `ok` and its print reported the platform leg's verdict. A leg
    // whose result nothing reads cannot fail, which is the worst kind of
    // green.
    let block_info_ok = block_info[0] == 0
        && block_info[1] == want_sector_size
        && want_sector_size != 0
        && block_info[2] > 0
        && block_info[3] == 0
        && block_info[4] == EACCES;
    // The BLOCK-SPAN block (BUF+7040..+7088, six qwords): the 4096-byte
    // two-sector read's byte count, a span whose SECOND sector is outside
    // the window refused EACCES as a whole, a zero-length span refused
    // EINVAL, the ATAPI write's media refusal EROFS, a write past the
    // window refused EACCES, and a write through another kind refused
    // EACCES. The span's own bytes are checked by the VOLUME block below.
    if !crate::memory::paging::user_range_ok(a0 + 7040, 48, false) {
        return EFAULT;
    }
    let mut span = [0i64; 6];
    for (i, slot) in span.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a0 + 7040 + 8 * i as u64) as *const i64) };
    }
    // The ISO9660 VOLUME block: ring 3 hands back the 32-byte volume id it
    // read from the primary descriptor's bytes 40..72 (BUF+7104), and the
    // kernel compares it with its OWN read of the same sector plus the
    // terminator descriptor at the next LBA.
    if !crate::memory::paging::user_range_ok(a0 + 2944, 4096, false)
        || !crate::memory::paging::user_range_ok(a0 + 7104, 32, false)
    {
        return EFAULT;
    }
    let mut ring3_id = [0u8; 32];
    unsafe {
        core::ptr::copy_nonoverlapping((a0 + 7104) as *const u8, ring3_id.as_mut_ptr(), 32);
    }
    let ring3_span_descriptor =
        unsafe { core::ptr::read_unaligned((a0 + 2944) as *const u64) };
    let ring3_terminator =
        unsafe { core::ptr::read_unaligned((a0 + 2944 + 2048) as *const u64) };
    let mut own_id = [0u8; 32];
    let mut own_next = [0u8; 8];
    let mut own_root_lba = 0u32;
    let mut volume_read = false;
    if let Some(channel) = crate::ata::boot_channel() {
        let mut sector = [0u8; 2048];
        if unsafe { crate::ata::read_sector(channel, 16, &mut sector) } {
            own_id.copy_from_slice(&sector[40..72]);
            // The primary descriptor's ROOT directory record: extent LBA
            // at PVD+158 (LE u32), data length at PVD+166.
            own_root_lba = u32::from_le_bytes([
                sector[158], sector[159], sector[160], sector[161],
            ]);
            volume_read = true;
        }
        if volume_read && unsafe { crate::ata::read_sector(channel, 17, &mut sector) } {
            own_next.copy_from_slice(&sector[0..8]);
        } else {
            volume_read = false;
        }
    }
    // Every ISO9660 volume descriptor, whatever its type, carries "CD001"
    // at bytes 1..6 and version 1 at byte 6; the NEXT one after the primary
    // is this disc's El Torito boot record, not necessarily a terminator.
    let next_is_descriptor = own_next[1..6] == *b"CD001" && own_next[6] == 1;
    let span_ok = span[0] == 4096
        && span[1] == EACCES
        && span[2] == EINVAL
        && span[3] == EROFS
        && span[4] == EACCES
        && span[5] == EACCES;
    let volume_ok = volume_read
        && ring3_span_descriptor == iso_descriptor as u64
        && ring3_terminator == u64::from_le_bytes(own_next)
        && next_is_descriptor
        && ring3_id == own_id;
    // The CACHE, SCRATCH and ROOT-DIRECTORY blocks live in BUF+7136..+11264:
    // two cache queries around a repeated read, the scratch write and its
    // read-back, and the ISO root directory ring 3 walked.
    if !crate::memory::paging::user_range_ok(a0 + 7136, 4096, false)
        || !crate::memory::paging::user_range_ok(a0 + 9216, 2048, false)
    {
        return EFAULT;
    }
    let read_q = |off: u64| unsafe { core::ptr::read_unaligned((a0 + off) as *const i64) };
    let cache_ok = read_q(7136) == 0
        && read_q(7160) == 0
        && read_q(7168) >= read_q(7144) + 1
        && read_q(7176) >= read_q(7152);
    // The scratch disk: ring 3 wrote a pattern to LBA 1000 and read it
    // back. The kernel reads the SAME LBA itself and compares all three
    // copies. Without an attached disk the write is -EBADF by name.
    let mut scratch_ok = false;
    if let Some(sectors) = unsafe { crate::ata::probe_ata_disk(1) } {
        scratch_ok = sectors > 1000 && read_q(7184) == 512 && read_q(7192) == 512;
        if scratch_ok {
            let mut disk = [0u8; 512];
            if unsafe { crate::ata::read_sector(1, 1000, &mut disk[..]) } {
                scratch_ok = disk[0..8] == 0xfeed_face_cafe_beefu64.to_le_bytes();
                for i in 0..512 {
                    let wrote = unsafe { core::ptr::read_unaligned((a0 + 8192 + i as u64) as *const u8) };
                    let back = unsafe { core::ptr::read_unaligned((a0 + 8704 + i as u64) as *const u8) };
                    if disk[i] != wrote || back != wrote {
                        scratch_ok = false;
                        break;
                    }
                }
            } else {
                scratch_ok = false;
            }
        }
    } else {
        scratch_ok = read_q(7184) == EBADF && read_q(7192) == EBADF;
    }
    // The ISO ROOT DIRECTORY, walked by ring 3 and re-walked here from the
    // kernel's own read: the same [len][bytes] sequence must come out.
    let mut own_names = [0u8; 768];
    let mut own_names_len = 0usize;
    let mut own_count = 0usize;
    let mut root_read = false;
    if volume_read && own_root_lba != 0 {
        if let Some(channel) = crate::ata::boot_channel() {
            let mut sector = [0u8; 2048];
            if unsafe { crate::ata::read_sector(channel, own_root_lba, &mut sector) } {
                root_read = read_q(7208) == 2048;
                let mut off = 0usize;
                while off + 33 <= 2048 && own_count < 12 {
                    let len = sector[off] as usize;
                    if len == 0 {
                        break;
                    }
                    let nl = sector[off + 32] as usize;
                    if nl > 1 && nl <= 64 && off + 33 + nl <= 2048 {
                        own_names[own_names_len] = nl as u8;
                        own_names_len += 1;
                        own_names[own_names_len..own_names_len + nl]
                            .copy_from_slice(&sector[off + 33..off + 33 + nl]);
                        own_names_len += nl;
                        own_count += 1;
                    }
                    off += len;
                }
            }
        }
    }
    let ring3_count = read_q(7200) as usize;
    let mut names_ok = root_read && ring3_count == own_count;
    if names_ok {
        for i in 0..own_names_len {
            let b = unsafe { core::ptr::read_unaligned((a0 + 7216 + i as u64) as *const u8) };
            if b != own_names[i] {
                names_ok = false;
                break;
            }
        }
    }
    unsafe { crate::svc84_proof::select_peer_space() };
    if !crate::memory::paging::user_range_ok(a1, 384, false) {
        unsafe { crate::svc84_proof::select_proof_space() };
        return EFAULT;
    }
    let mut peer = [0i64; 48];
    for (i, slot) in peer.iter_mut().enumerate() {
        *slot = unsafe { core::ptr::read_unaligned((a1 + 8 * i as u64) as *const i64) };
    }
    unsafe { crate::svc84_proof::select_proof_space() };
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
        // THREE parked receives: the peer's send woke the first
        // process out of its blocking receive, the first process's
        // "go" woke the peer out of its own, and (since the
        // revocation leg) the coordinator's wake resumed the peer
        // a second time -- that park is what makes the revocation
        // happen with nobody touching the page.
        && BLOCKED_RECVS.load(Ordering::Relaxed) == 3
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
        // ONE process exits through CAP_EXIT: the first one. Since the
        // revocation leg the PEER does not exit -- it KEEPS the handed-on
        // region's capability and is TERMINATED by the armed fault, so its
        // table is released by the fault path instead of the EXIT row. The
        // exit therefore releases NO region capability (the first process
        // had closed both of its own) -- ONE since the WINDOW leg landed:
        // step 16g's region is deliberately still held when the process
        // exits, so the exit is what releases it and frees those pages.
        // `fault_released` covers the peer's side, and the REVOKE segment
        // below covers what its own read did.
        && exit_count == 1
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
    // (the handed-on region: the boot's FIRST request, slot 0)
    let want_map_base3 = crate::svc84_proof::SHM_REQUEST_WINDOW as i64;
    // The window slot of the region whose window the first process dropped
    // with 0x50: its SECOND request (the handed-on region was the first),
    // so one stride in -- slots are handed out in program order, not by
    // which leg asked.
    let want_map_base4 =
        (crate::svc84_proof::SHM_REQUEST_WINDOW + crate::svc84_proof::SHM_REQUEST_STRIDE) as i64;
    const WINDOW_MARKER: i64 = 0x0ddba11cafef00d;
    let want_window_marker = WINDOW_MARKER;
    // The windows are handed out in PROGRAM order, one stride per request,
    // so the slots are not in the order the legs are described in. Since the
    // WINDOW leg landed (2026-11-08) the order is: slot 0 = the handed-on
    // region (the first process's first request), slot 1 = the WINDOW leg's
    // region, slots 2 and 3 = step 15's two. Naming them by slot keeps the
    // pins honest about which mapping they are talking about.
    let slot = |i: u64| {
        (crate::svc84_proof::SHM_REQUEST_WINDOW + i * crate::svc84_proof::SHM_REQUEST_STRIDE) as i64
    };
    let want_map_base = slot(2);
    let want_map_base2 = slot(3);
    // (the handed-on region and the window leg's region, for the pins that
    // name them through their own words)
    let want_handover_base = slot(0);
    let want_window_base = slot(1);
    let want_map_value = crate::svc84_proof::SHM_REQUEST_VALUE as i64;
    let want_transfer_value = crate::svc84_proof::SHM_TRANSFER_VALUE as i64;
    let want_transfer_write = crate::svc84_proof::SHM_TRANSFER_WRITE as i64;
    // THE REVOCATION LEG: the kernel took the handed-on region's pages back
    // while the peer still held the capability, then woke the peer, whose
    // own next read faulted. The probe's record is the evidence -- the
    // address is the base the KERNEL handed out, the error code says a
    // READ that found no mapping from RING 3 (bit 2 set, bit 0 clear, bit 1
    // clear = 0x4), the task is the one the probe was armed for, and it
    // fired exactly once. `revoked`/`woke` pin that the machine accepted the
    // revoke and that a message actually reached the parked holder.
    let revoke_report = crate::svc84_proof::revocation_report();
    let (rv_base, rv_addr, rv_err, rv_task, rv_expected, rv_hits, rv_woke) = match revoke_report {
        Some(r) => (
            r.base,
            r.fault_addr,
            r.fault_error,
            r.fault_task,
            r.expected_task,
            r.hits,
            if r.woke { 1 } else { 0 },
        ),
        None => (0, 0, 0, u64::MAX, u64::MAX, 0, 0),
    };
    let revoke_ok = matches!(revoke_report, Some(r)
        if r.revoked
            && r.woke
            && r.hits == 1
            && r.base != 0
            && r.fault_addr == r.base
            && r.fault_error == 4
            && r.fault_task == r.expected_task
            && r.from_ring3
            // The GENERAL policy, not a keyhole: the reason is derived from
            // the CPU's error word (bit 0 clear = nothing is mapped there).
            && r.reason == "not mapped");
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
                // The two handles are minted from the same table but are no
                // longer adjacent: the WINDOW leg's request (16g) is minted
                // between them, so this pins ORDER (the second request gets
                // a later handle) rather than arithmetic that would break
                // every time a leg adds a capability.
                && map[10] > map[4]
                && map[11] == 0
                && map[12] == 0
                && map[13] == EBADF
                && map[14] == 0
                && map[15] == want_map_base
                // THE WINDOW LEG: the first process's fourth region, whose
                // window it DROPPED with 0x50 while keeping the capability.
                // The row answers 0 once, the same call again is -ENOENT by
                // name, the capability still answers geometry afterwards,
                // and the peer -- the other space the kernel mirrored the
                // frames into -- reads the first process's marker at that
                // window AFTER it was dropped here. One holder's window
                // went; the frames and the other window did not.
                && unmap_req_ack == 0
                && unmap_base == want_map_base4
                && unmap_len == 4096
                && unmap_handle >= 0
                && unmap_marker == want_window_marker
                && unmap_ack == 0
                && unmap_again == ENOENT
                && unmap_geom_ack == 0
                && unmap_geom_base == want_map_base4
                && peer[29] == want_window_marker
                && r.first_unmapped
                && r.second_unmapped
                && r.third_unmapped
                && r.fourth_unmapped
                // ...and the release that freed that region found NO window
                // in the space it ran in: the machine's own record that the
                // window was dropped before the region died, not merely
                // that it was unmapped afterwards.
                && r.released_without_window == 1
                && r.released == 4
                // The FIRST release is the region that was HANDED ON, and
                // its two words are the two processes' writes: the
                // sender's marker at offset 0 (written BEFORE it closed
                // its own capability) and the receiver's at offset 8.
                // Read out of the mapping by the releaser, at the moment
                // the pages went back -- reported by neither program.
                // Since the revocation leg the release order is: step 15's
                // two regions (the first process's closes after its parked
                // call returns), then the HANDED-ON region when the kernel
                // REVOKES it -- the peer still held the capability, so that
                // region is the LAST release and its two words are both
                // processes' writes. The earlier two were written by the
                // first process alone, at offset 0.
                && r.first_word == want_map_value as u64
                && r.first_word8 == 0
                && r.last_word == want_transfer_value as u64
                && r.last_word8 == want_transfer_write as u64
                && r.first_base == want_map_base as u64
                && r.last_base == want_map_base3 as u64
                // The region legs' own frames must all come back. The baseline
                // was taken before their pump, and the PEER's surface tile is
                // alive across it by design, so the display's own holdings are
                // named exactly rather than ignored.
                && r.frames_in_use
                    == r.frames_baseline + crate::experimental::region_window::surface_frames_held()
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
                // The WIDER ask (a_rights = ALL, one right the sender never
                // held) was refused by NAME and queued nothing: the
                // receiver below drained the ONE good message, attenuated
                // to READ|WRITE. If attenuation had clipped instead of
                // refusing, that drain would have carried 0xf.
                && mapx[8] == EMSGSIZE
                && revoke_ok
                // ...and the RECEIVER's: the message it drained (the first
                // process's session, four payload bytes), the handle the
                // machine installed in ITS table, the geometry row through
                // that handle (the same window, attenuated to READ|WRITE),
                // the sender's marker read across the handover, its own
                // write at offset 8, and the close that finally returned
                // the pages. Zero retries: the message was queued before
                // the "go" that resumed it.
                // THE TWO SPACES: the same VA read on both sides of a CR3
                // switch, and the peer's page is its own -- the region base
                // its geometry call answered, not anything the first
                // program wrote.
                && same_va_first != peer[1] as u64
                && peer[1] == crate::svc84_proof::PROOF_SHM as i64
                && peer[17] == ((crate::svc84_proof::FIRST_SESSION as i64) << 32 | 4)
                && peer[18] >= 0
                && peer[20] == want_map_base3
                && peer[22]
                    == (crate::experimental::caps::rights::READ
                        | crate::experimental::caps::rights::WRITE) as i64
                && peer[23] == want_transfer_value
                && peer[24] == want_transfer_write
                // PEN+200 is no longer a close's answer: since the
                // revocation leg the peer KEEPS the capability and PARKS
                // for the kernel's wake instead, so this slot holds the
                // parked receive's answer -- the coordinator's seq 99,
                // packed as (sender 0 << 32 | 99). The old `== 0` here was
                // the close that used to make this process the last holder.
                && peer[25] == 99
                && peer[26] == 0
        }
        None => false,
    };
    let (map_released, map_first_word, map_last_word, map_first_base, map_last_base, map_in_use, map_baseline, map_unmapped1, map_unmapped2, map_first_word8, map_last_word8_rep, map_unmapped3, map_unmapped4) =
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
                r.last_word8,
                r.third_unmapped,
                r.fourth_unmapped,
            ),
            None => (0, 0, 0, 0, 0, 0, 0, false, false, 0, 0, false, false),
        };
    // The peer's CLIENT half of the supervised display: its attach was
    // clamped to the server's policy (IDENTITY only, mask 1 -- the high
    // word is the slot the kernel chose, not a constant here), the
    // identity change under that grant was allowed (or `-ENODEV` with no
    // display bound), the form-factor request was refused by AUTHORITY
    // before the device could be consulted, the self-query reported the
    // same grant, detach freed it and the second attach got the same slot
    // back with the same mask.
    let peer_fb = crate::devices::framebuffer::bound_mode().is_some();
    let peer_client_ok = peer[36] & 0xffff == crate::display::AUTH_IDENTITY as i64
        && peer[37] == if peer_fb { 0 } else { ENODEV }
        && peer[38] == EACCES
        && peer[39] == peer[36]
        && peer[40] == 0
        && peer[41] == peer[36]
        && peer[42] >= 0
        // ...and its own SURFACE: the claim succeeded, and its three pixel
        // rows were accepted (a client's pixels, in a client's slot).
        && peer[43] == 0
        && peer[44] != 0
        && peer[45] == 4096
        && peer[46]
            == (crate::experimental::caps::rights::READ
                | crate::experimental::caps::rights::WRITE
                | crate::experimental::caps::rights::GRANT) as i64;
    let peer_ok = peer_client_ok
        && peer[0] == 0
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
    // ---- the platform + display rows ---------------------------------
    // 0x5d GET, 0x5e SET, 0x5f INFO, 0x60 MODE_SET, 0x61/0x62/0x63
    // ownership. The program RECORDS what it set and read; the live kernel
    // state is checked at the END, after the owning session has exited --
    // the identity and form factor are kernel state and outlive the
    // process that chose them, while ownership does not outlive it.
    let plat_range_ok = crate::memory::paging::user_range_ok(a0 + 11296, 56, false);
    let plat = if plat_range_ok {
        [
            read_q(11296),
            read_q(11304),
            read_q(11312),
            read_q(11320),
            read_q(11328),
            read_q(11336),
            read_q(11344),
        ]
    } else {
        [i64::MIN; 7]
    };
    let windows = crate::platform_mode::Platform::Windows.id() as i64;
    let fb_bound = crate::devices::framebuffer::bound_mode().is_some();
    // plat[0] is the identity the machine booted with (read by the
    // program), plat[6] the read-back AFTER its restore: they must be the
    // same value, and the SET/GET words around them must be the statuses
    // the sequence promises. The live tie-in happens in the DISPLAY leg.
    let platform_ok = plat_range_ok
        && plat[3] == EINVAL
        && plat[6] == plat[0]
        && if fb_bound {
            plat[0] == plat[6]
                && plat[1] == 0
                && plat[2] == windows
                && plat[4] == windows
                && plat[5] == 0
        } else {
            plat[0] == plat[6]
                && plat[1] == ENODEV
                && plat[2] == plat[0]
                && plat[4] == plat[0]
                && plat[5] == ENODEV
        };
    // The running verdict. Every leg below folds itself in as it prints
    // (see `claim!`), so "the line said matches" and "the verdict counted
    // it" are the same fact.
    let mut ok = true;

    crate::serial_println!(
        "  service-0x84 proof: PLATFORM (get {:#x} , set windows {:#x} , get after {:#x} , bad set {:#x} , after refusal {:#x} , restore {:#x} , get restored {:#x}) , PLATFORM {}",
        plat[0] as u64, plat[1] as u64, plat[2] as u64, plat[3] as u64,
        plat[4] as u64, plat[5] as u64, plat[6] as u64,
        claim!(ok, platform_ok)
    );

    // PLATFORM_INFO (0x5f): one record, six facts. The record was taken
    // while the machine was in its BOOT posture, so the expected layout
    // and accent are derived from the record's OWN identity/mode words --
    // a record that merely echoed the request cannot satisfy that.
    let info_range_ok = crate::memory::paging::user_range_ok(a0 + 11400, 48, false);
    let info = if info_range_ok {
        let mut words = [0u64; 6];
        for (i, w) in words.iter_mut().enumerate() {
            *w = unsafe {
                core::ptr::read_unaligned((a0 + 11400 + 8 * i as u64) as *const u64)
            };
        }
        words
    } else {
        [u64::MAX; 6]
    };
    let info_ack = read_q(11360);
    let original_mode = info[1];
    let info_ok = if let Some((w, h)) = crate::devices::framebuffer::bound_mode() {
        let recorded_mode = crate::device_mode::DeviceMode::from_id(info[1]);
        let recorded_platform = crate::platform_mode::Platform::from_id(info[0]);
        match (recorded_mode, recorded_platform) {
            (Some(mode), Some(platform)) => {
                let layout = crate::device_mode::layout(mode, w, h);
                info_ack == 0
                    && info_range_ok
                    && info[0] == plat[0] as u64
                    && info[2] == w as u64
                    && info[3] == h as u64
                    && info[4] == layout.status as u64
                    && info[5] == (platform.palette().accent & 0xff_ffff) as u64
            }
            _ => false,
        }
    } else {
        info_ack == ENODEV
    };
    crate::serial_println!(
        "  service-0x84 proof: PLATFORM-INFO (ack {:#x} , identity {:#x} , mode {:#x} , width {:#x} , height {:#x} , bar {:#x} , accent {:#x}) , PLATFORM-INFO {}",
        info_ack as u64, info[0], info[1], info[2], info[3], info[4], info[5],
        claim!(ok, info_ok)
    );

    // PLATFORM_MODE_SET (0x60): phone, read back, then the ORIGINAL form
    // factor (read out of the first record, never assumed) and read back.
    let mode_ack = read_q(11368);
    let info2_ack = read_q(11376);
    let restore_ack = read_q(11384);
    let info3_ack = read_q(11392);
    let mut info2 = [u64::MAX; 6];
    let mut info3 = [u64::MAX; 6];
    let mode_records_ok = crate::memory::paging::user_range_ok(a0 + 11456, 96, false);
    if mode_records_ok {
        for i in 0..6 {
            info2[i] = unsafe { core::ptr::read_unaligned((a0 + 11456 + 8 * i as u64) as *const u64) };
            info3[i] = unsafe { core::ptr::read_unaligned((a0 + 11512 + 8 * i as u64) as *const u64) };
        }
    }
    let phone = crate::device_mode::DeviceMode::Phone;
    let mode_ok = if let Some((w, h)) = crate::devices::framebuffer::bound_mode() {
        let phone_layout = crate::device_mode::layout(phone, w, h);
        let restored_mode = crate::device_mode::DeviceMode::from_id(original_mode);
        mode_ack == 0
            && mode_records_ok
            && info2_ack == 0
            && info2[0] == plat[0] as u64
            && info2[1] == phone.id()
            && info2[2] == w as u64
            && info2[3] == h as u64
            && info2[4] == phone_layout.status as u64
            && restore_ack == 0
            && info3_ack == 0
            && match restored_mode {
                Some(m) => {
                    let back = crate::device_mode::layout(m, w, h);
                    info3[1] == m.id() && info3[4] == back.status as u64
                }
                None => false,
            }
    } else {
        mode_ack == ENODEV && restore_ack == ENODEV
    };
    crate::serial_println!(
        "  service-0x84 proof: PLATFORM-MODE (set phone {:#x} , info2 ack {:#x} , mode {:#x} , bar {:#x} , restore {:#x} , info3 ack {:#x} , mode {:#x} , bar {:#x}) , PLATFORM-MODE {}",
        mode_ack as u64, info2_ack as u64, info2[1], info2[4],
        restore_ack as u64, info3_ack as u64, info3[1], info3[4],
        claim!(ok, mode_ok)
    );

    // ---- the supervised display (0x64..0x68, beside 0x61/0x63) --------
    // The first process SERVED the display before it ever parked, with a
    // policy that grants clients the IDENTITY bit only. The peer attached
    // under it (retrying -ENODEV until the server existed), was clamped to
    // that bit, switched the identity -- and was refused the form factor.
    // Those facts live in the peer's page and are checked in `peer_ok`.
    //
    // What is checked HERE is the server's side, at verdict time, after
    // both processes are gone: the display could not be claimed
    // exclusively while it was served (-EBUSY), the server reported zero
    // clients before the peer attached and one while it was attached, its
    // own authority covered the form factor no client could touch, and its
    // withdrawal dissolved the server and detached the parked client. The
    // identity and form factor it set are kernel state and stand.
    let dl_range_ok = crate::memory::paging::user_range_ok(a0 + 11624, 216, false);
    let (
        serve_ack,
        status_first,
        owner_seen,
        acquire_try,
        client_self,
        status_serving,
        set_served,
        mode_served,
        release_try,
        compose_ack,
        spaces_word,
        dl_info_ack,
    ) = if dl_range_ok {
        (
            read_q(11624),
            read_q(11632),
            read_q(11640),
            read_q(11648),
            read_q(11656),
            read_q(11664),
            read_q(11672),
            read_q(11680),
            read_q(11744),
            read_q(11752),
            read_q(11760),
            read_q(11736),
        )
    } else {
        (
            i64::MIN, i64::MIN, i64::MIN, i64::MIN, i64::MIN, i64::MIN, i64::MIN, i64::MIN,
            i64::MIN, i64::MIN, i64::MIN, i64::MIN,
        )
    };
    let mut dl_info = [u64::MAX; 6];
    if dl_range_ok {
        for i in 0..6 {
            dl_info[i] = unsafe {
                core::ptr::read_unaligned((a0 + 11688 + 8 * i as u64) as *const u64)
            };
        }
    }
    let session = crate::svc84_proof::FIRST_SESSION as u64;
    // The proof registered BOTH sessions (the program's and the peer's), and
    // the server's own registration is idempotent over the first, so at the
    // moment the program asks, the registry holds two entries, the caller is
    // one of them and its server is too. After the server's withdrawal the
    // caller's row must be gone.
    let spaces_expected = ((2u64 << 32) | 0b11) as i64;
    // The SURFACE CHURN record: the server attached itself as a client, then
    // opened and gave back a surface tile twelve times. What must hold is the
    // PAIR of numbers, not just the count of successes: the windows ISSUED
    // climbed by exactly one per opening while the windows LIVE came back to
    // where they started, and the base the kernel handed out was the same one
    // every time -- a slot returned, not a range consumed.
    let churn = if dl_range_ok {
        let mut words = [0u64; 10];
        for (index, word) in words.iter_mut().enumerate() {
            *word = unsafe {
                core::ptr::read_unaligned((a0 + 11768 + 8 * index as u64) as *const u64)
            };
        }
        words
    } else {
        [0u64; 10]
    };
    // Between the FIRST and the LAST release answer, eleven further windows
    // were issued: twelve openings, the first of them already counted in the
    // "before" number because that answer is the first release's. The LIVE
    // number is the one that must not grow, and the base is the one that must
    // come back.
    // The BASE comparison is deliberately NOT part of the verdict: the peer's
    // own surface may be given back somewhere inside the churn, which shifts
    // which free slot the churn takes without saying anything about
    // reclamation. The two numbers that cannot be explained away are the live
    // count (flat) and the completed openings (twelve, against eight slots).
    let churn_flat = churn[0] == 12
        && churn[1] == churn[2]
        && churn[3].wrapping_add(11) == churn[4]
        && churn[7] > 0
        && churn[8] == 0;
    // "The surface table is empty" is only worth asserting together with the
    // table's other number: the issue count the program saw at its LAST
    // release is the kernel's own count, and nothing opens a surface after the
    // churn, so a table that was never armed (or never handed out a window)
    // cannot satisfy this by having nothing to report. Empty, and empty after
    // at least a churn's worth of windows was genuinely issued and returned.
    let surfaces_returned = churn_flat
        && crate::experimental::region_window::surface_in_use() == 0
        && crate::experimental::region_window::surface_issued() == churn[4]
        && churn[4] >= 12;
    let caller_unregistered =
        crate::experimental::space_registry::pml4_of(session).is_none();
    // STATUS packs `server << 32 | clients << 16 | caller grant`; a server
    // reports the full mask for itself.
    let status_word = |clients: u64| -> i64 {
        ((session << 32) | ((clients & 0xffff) << 16) | crate::display::AUTH_ALL) as i64
    };
    let live_identity = crate::platform_mode::current();
    let live_mode = crate::device_mode::current();
    let owner_now = crate::display::owner();
    let server_now = crate::display::server();
    let clients_now = crate::display::client_count();
    let surfaces_now = crate::display::surface_count();
    // The tile the client filled through DISPLAY_SURFACE_PIXEL, as the
    // DISPLAY now holds it. The origin is the kernel's own placement rule,
    // computed at the mode the composition ran in, and the three probes are
    // the three words the client wrote -- one of them not at the origin.
    let (tile_x, tile_y) = match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => crate::display::surface_origin(
            0,
            w,
            h,
            crate::device_mode::layout(phone, w, h).status as u32,
        ),
        None => (0, 0),
    };
    let tile_pixels = (
        crate::devices::framebuffer::kernel_read_pixel(tile_x, tile_y),
        crate::devices::framebuffer::kernel_read_pixel(tile_x + 1, tile_y),
        crate::devices::framebuffer::kernel_read_pixel(tile_x + 3, tile_y + 2),
    );
    let display_ok = match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => {
            let phone_layout = crate::device_mode::layout(phone, w, h);
            dl_range_ok
                && serve_ack == crate::display::AUTH_IDENTITY as i64
                && status_first == status_word(0)
                && owner_seen == 0
                && acquire_try == crate::display::EBUSY
                && client_self == 0
                && status_serving == status_word(1)
                && set_served == 0
                && mode_served == 0
                && release_try == crate::display::EACCES
                && spaces_word == spaces_expected
                && caller_unregistered
                && surfaces_returned
                && compose_ack == 1
                && tile_pixels.0 == Some(0x0022_4466)
                && tile_pixels.1 == Some(0x0033_5577)
                && tile_pixels.2 == Some(0x00ab_cdef)
                && surfaces_now == 0
                && dl_info_ack == 0
                && dl_info[0] == windows as u64
                && dl_info[1] == phone.id()
                && dl_info[2] == w as u64
                && dl_info[3] == h as u64
                && dl_info[4] == phone_layout.status as u64
                && server_now == 0
                && clients_now == 0
                && crate::display::policy() == 0
                && owner_now == 0
                && live_identity.id() as i64 == windows
                && live_mode.id() == phone.id()
        }
        None => {
            dl_range_ok
                && serve_ack == crate::display::AUTH_IDENTITY as i64
                && status_first == status_word(0)
                && owner_seen == 0
                && acquire_try == crate::display::EBUSY
                && client_self == 0
                && status_serving == status_word(1)
                && set_served == ENODEV
                && mode_served == ENODEV
                && release_try == crate::display::EACCES
                && spaces_word == spaces_expected
                && caller_unregistered
                && surfaces_returned
                && compose_ack == ENODEV
                && surfaces_now == 0
                && dl_info_ack == ENODEV
                && server_now == 0
                && clients_now == 0
                && crate::display::policy() == 0
                && owner_now == 0
                && live_identity.id() as i64 == plat[0]
                && live_mode.id() == original_mode
        }
    };
    crate::serial_println!(
        "  service-0x84 proof: DISPLAY (serve {:#x} , status first {:#x} , owner {:#x} , acquire beside server {:#x} , self client {:#x} , status serving {:#x} , set {:#x} , mode {:#x} , release {:#x} , compose {:#x} , spaces {:#x} , churn {}/{}/{}..{} , churn fails {} refuse {:#x} , window live/issued {}/{} , tile {:#x}/{:#x}/{:#x} , info ack {:#x} , identity {:#x} , mode word {:#x} , server at verdict {:#x} , clients {:#x} , surfaces at verdict {:#x} , live identity {:#x} , live mode {:#x}) , DISPLAY {}",
        serve_ack as u64, status_first as u64, owner_seen as u64, acquire_try as u64,
        client_self as u64, status_serving as u64, set_served as u64, mode_served as u64,
        release_try as u64, compose_ack as u64, spaces_word as u64,
        churn[0], churn[1], churn[3], churn[4],
        // A churn that stopped short names what stopped it: the loop's own
        // failure count and the errno the refused row answered with, beside
        // the display's OWN window counters -- so "zero openings" can be told
        // apart from "openings that the program never counted".
        churn[8],
        churn[9],
        crate::experimental::region_window::surface_in_use(),
        crate::experimental::region_window::surface_issued(),
        tile_pixels.0.unwrap_or(0) as u64, tile_pixels.1.unwrap_or(0) as u64,
        tile_pixels.2.unwrap_or(0) as u64,
        dl_info_ack as u64, dl_info[0], dl_info[1], server_now, clients_now, surfaces_now,
        live_identity.id(), live_mode.id(),
        claim!(ok, display_ok)
    );

    // `stored_ok` is the one leg whose verdict is folded in here rather
    // than at its print site (it is asserted by the kernel's own read-back
    // of the program's results page, which prints field by field).
    ok &= stored_ok;

    crate::serial_println!(
        "  service-0x84 proof: BADGE/NOTIFY (notify {:#x} , take {:#x} , again {:#x} , wrong-kind {:#x} , forged {:#x} , non-owner {:#x} , badge {:#x} , zero {:#x} , kind {:#x} , forged-badge {:#x} , twice {:#x} , send {:#x} , recv {:#x} , tag {:#x} , null-tag {:#x}) , BADGE/NOTIFY {}",
        bn[0] as u64,
        bn[1] as u64,
        bn[2] as u64,
        bn[3] as u64,
        bn[4] as u64,
        bn[5] as u64,
        bn[6] as u64,
        bn[7] as u64,
        bn[8] as u64,
        bn[9] as u64,
        bn[10] as u64,
        bn[11] as u64,
        bn[12] as u64,
        bn[13] as u64,
        bn[14] as u64,
        claim!(ok, notify_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: DEVICE (block {:#x} , ioport {:#x} window {:#x} , fb {:#x} rows {:#x} , endpoint {:#x} , shape {:#x}) , DEVICE {}",
        dev[0] as u64,
        dev[1] as u64,
        dev[2] as u64,
        dev[3] as u64,
        dev[4] as u64,
        dev[5] as u64,
        dev[6] as u64,
        claim!(ok, device_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: DATA-PLANE (port-write {:#x} , port-read {:#x} == kernel {:#x} , past-window {:#x} , fb-write {:#x} , fb-read {:#x} , fb-row {:#x} , fb-kind {:#x}) , DATA-PLANE {}",
        data[0] as u64,
        data[1] as u64,
        kernel_port80 as u64,
        data[2] as u64,
        data[3] as u64,
        data[4] as u64,
        data[5] as u64,
        data[6] as u64,
        claim!(ok, data_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: BLOCK (read {:#x} , lba16 {:#x} (want {:#x}) , past-window {:#x} , zero-len {:#x} , wrong-kind {:#x}) , BLOCK {}",
        block[0] as u64,
        block[1] as u64,
        iso_descriptor as u64,
        block[2] as u64,
        block[3] as u64,
        block[4] as u64,
        claim!(ok, block_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-INFO (ack {:#x} , sector-size {:#x} , sectors {:#x} , reserved {:#x} , wrong-kind {:#x}) , BLOCK-INFO {}",
        block_info[0] as u64,
        block_info[1] as u64,
        block_info[2] as u64,
        block_info[3] as u64,
        block_info[4] as u64,
        claim!(ok, block_info_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-SPAN (ack {:#x} , past-window {:#x} , zero-len {:#x} , media {:#x} , write-window {:#x} , write-kind {:#x}) , BLOCK-SPAN {}",
        span[0] as u64,
        span[1] as u64,
        span[2] as u64,
        span[3] as u64,
        span[4] as u64,
        span[5] as u64,
        claim!(ok, span_ok)
    );
    // ---- the REFUSAL MATRIX ---------------------------------------------
    // The answers the probes at the end of prog_svc84.s left in the fourth
    // results page, compared against `REFUSAL_MATRIX` cell by cell. Reading
    // them through the validator first means a page that was never mapped
    // fails this leg by name instead of faulting the kernel mid-verdict.
    let matrix_len = REFUSAL_MATRIX.len();
    let matrix_at = a0 + REFUSAL_MATRIX_SLOT;
    let matrix_range_ok =
        crate::memory::paging::user_range_ok(matrix_at, 8 * matrix_len, false);
    let mut matrix_matched = 0u64;
    // The projection, and why it is a FOLD rather than packed words:
    // a byte-per-cell layout needs as many words as the matrix has cells, and
    // every way of writing that by hand broke. One word panicked the kernel
    // at the ninth cell (a shift by 64); two words silently ORed the
    // seventeenth cell onto the ninth's byte (`0xea | 0xf7` = 0xff) while
    // every cell still "matched". Both failures came from the projection
    // needing to know where a cell lands.
    //
    // FNV-1a knows nothing: no shift, no word index, no eight-cell stride, no
    // limit, and therefore nothing that can wrap or overflow. Each answer's
    // low byte is folded in cell order into ONE word. It is a diagnostic, not
    // the verdict -- a disagreement already sets `matrix_ok` false, is counted
    // in `matched` and is printed by name as a ROW line -- so a hash that
    // pins the whole sequence is exactly the strength this needs, and the gate
    // asserts it as one exact string.
    let mut matrix_fold = 0xcbf2_9ce4_8422_2325u64; // FNV-1a 64 offset basis
    let mut matrix_ok = matrix_range_ok;
    if matrix_range_ok {
        for (index, row) in REFUSAL_MATRIX.iter().enumerate() {
            let got = unsafe {
                core::ptr::read_unaligned((matrix_at + 8 * index as u64) as *const i64)
            };
            // Wrapping arithmetic by construction: `wrapping_mul` cannot
            // trap in a debug build, whatever the cell count.
            matrix_fold = (matrix_fold ^ (got as u64 & 0xff)).wrapping_mul(0x0000_0100_0000_01b3);
            if got == row.want {
                matrix_matched += 1;
            } else {
                // Named, not just counted: the row, the argument that was
                // wrong, what came back and what the row's own rule says.
                crate::serial_println!(
                    "  service-0x84 proof: REFUSAL-MATRIX ROW {:#x} ({}) got {:#x} , want {:#x}",
                    row.call,
                    row.what,
                    got as u64,
                    row.want as u64
                );
            }
        }
        matrix_ok &= matrix_matched == matrix_len as u64;
    } else {
        crate::serial_println!(
            "  service-0x84 proof: REFUSAL-MATRIX answers at {:#x} are not mapped",
            matrix_at
        );
    }
    crate::serial_println!(
        "  service-0x84 proof: REFUSAL-MATRIX (rows {:#x} , matched {:#x} , fold {:#018x}) , REFUSAL-MATRIX {}",
        matrix_len as u64,
        matrix_matched,
        matrix_fold,
        claim!(ok, matrix_ok)
    );

    let mut id_end = own_id.len();
    while id_end > 0 && (own_id[id_end - 1] == b' ' || own_id[id_end - 1] == 0) {
        id_end -= 1;
    }
    let id_text = core::str::from_utf8(&own_id[..id_end]).unwrap_or("?");
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-VOLUME (id \"{}\" , pvd 0x{:016x} , next 0x{:016x} , ring3-id {}) , BLOCK-VOLUME {}",
        id_text,
        ring3_span_descriptor,
        ring3_terminator,
        verdict_word!(ring3_id == own_id),
        claim!(ok, volume_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-CACHE (hits {} -> {} , misses {} -> {} , queries 0x{:x}/0x{:x}) , BLOCK-CACHE {}",
        read_q(7144),
        read_q(7168),
        read_q(7152),
        read_q(7176),
        read_q(7136),
        read_q(7160),
        claim!(ok, cache_ok)
    );
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-SCRATCH (write 0x{:x} , read 0x{:x} , disk and both ring-3 copies agree {}) , BLOCK-SCRATCH {}",
        read_q(7184) as u64,
        read_q(7192) as u64,
        if scratch_ok { "yes" } else { "NO" },
        claim!(ok, scratch_ok)
    );
    let mut list = [0u8; 128];
    let mut list_len = 0usize;
    let mut i = 0usize;
    let mut shown = 0usize;
    while i < own_names_len && shown < own_count && list_len + 66 < list.len() {
        let nl = own_names[i] as usize;
        if nl == 0 || i + 1 + nl > own_names_len {
            break;
        }
        if shown > 0 {
            list[list_len] = b',';
            list_len += 1;
        }
        list[list_len..list_len + nl].copy_from_slice(&own_names[i + 1..i + 1 + nl]);
        list_len += nl;
        shown += 1;
        i += 1 + nl;
    }
    crate::serial_println!(
        "  service-0x84 proof: BLOCK-ROOT (entries {} , names \"{}\") , BLOCK-ROOT {}",
        ring3_count,
        core::str::from_utf8(&list[..list_len]).unwrap_or("?"),
        claim!(ok, names_ok)
    );
    PROOF_STATE.store(if ok { 1 } else { 2 }, Ordering::Relaxed);
    crate::serial_println!(                "  service-0x84 proof: a ring-3 program called int 0x84 from CPL {} -- GFX_MODE {:#x} , INPUT_STATE {:#x} , unported call {:#x} (refused, not answered) , validated write ({},{}) , process SELF {} , wide-args {:#x} {:#x} {:#x} {:#x} , ipc round trip (send-ack {:#x} , recv {:#x} , want {:#x}  , shape {} , empty {} , blocked {:#x} , want-blocked {:#x} , again {} , baddest {} , payload (ack {} , len {} , bytes {:#x} , wide {} , null {} , short {})) , caps (ack {:#x} , recv {:#x} , forged {:#x} , oversized {:#x} , payload {:#x}) , read back through the validator: GFX {} , INPUT {} , ENOSYS {} , geometry {} , SELF {} , ARGS {} , IPC {} , CAP {} , INPUT_POLL (1 {:#x} , 2 {:#x} , 3 {:#x} , forged {:#x} , bytes {:#x}/{:#x}) , INPUT {} , REPLY (call {:#x} , recv {:#x} , handle {:#x} , reply {:#x} , twice {:#x} , kind {:#x} , answer {:#x} , bytes {:#x}) , REPLY {} , SHM (ack {:#x} , base {:#x} , len {:#x} , rights {:#x} , sentinel {:#x} , wrote {:#x} , forged {:#x} , kind {:#x}) , SHM {} , TWO-PROCESS (close {:#x} , geometry {:#x} , call {:#x} , request {:#x} , answer {:#x} , offset8 {:#x} , offset16 {:#x} , last {:#x} , twice {:#x} , region live {} holders {:?} , exits {} session {} released {}) , PEER (ack {:#x} , base {:#x} , read {:#x} , recv {:#x} , handle {:#x} , request {:#x} , reply {:#x} , wrote {:#x}) , TWO-PROCESS {} , PEER {} , PEER-XFER (recv {:#x} , handle {:#x} , base {:#x} , read {:#x} , wrote {:#x} , parked {:#x} , tries {}) , SPACES (same va 0x60000010000 , first {:#x} , peer {:#x}) , REVOKE (base {:#x} , addr {:#x} , err {:#x} , task {} , expected {} , hits {} , woke {} , reason {} , session {}) , WINDOW (req {:#x} , base {:#x} , len {:#x} , handle {:#x} , marker {:#x} , unmap {:#x} , again {:#x} , geom {:#x} , geom base {:#x} , peer read {:#x} , no-window release {}) , MAP (ack {:#x} , base {:#x} , len {:#x} , rights {:#x} , handle {:#x} , wrote {:#x} , again {:#x} , base2 {:#x} , len2 {:#x} , handle2 {:#x} , close {:#x} , close2 {:#x} , stale {:#x} , geometry {:#x} , geom base {:#x} , released {} word {:#x}/{:#x} , bases {:#x}/{:#x} , frames {} == {} , unmapped {} / {} / {} / {} , xfer (ack {:#x} , base {:#x} , send {:#x} , close {:#x} , survived {:#x} , first8 {:#x} , last8 {:#x} , wider {:#x})) , MAP {} -- {}
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
        verdict_word!(stored[0] == want_gfx),
        verdict_word!(stored[1] == want_input),
        verdict_word!(stored[2] == want_enosys),
        claim!(ok, wrote_ok),
        verdict_word!(stored[4] == want_self),
        verdict_word!(
            stored[5] == want_rsi as i64
                && stored[6] == want_rdx as i64
                && stored[7] == want_r10 as i64
                && stored[8] == want_r8 as i64
        ),
        claim!(ok, ipc_ok),
        claim!(ok, cap_ok),
        stored[17] as u64,
        stored[18] as u64,
        stored[19] as u64,
        stored[21] as u64,
        polled_first8,
        polled_second8,
        claim!(ok, input_poll_ok),
        reply_block[0] as u64,
        reply_block[1] as u64,
        reply_block[3] as u64,
        reply_block[5] as u64,
        reply_block[6] as u64,
        reply_block[7] as u64,
        reply_block[8] as u64,
        reply_block[9] as u64,
        claim!(ok, reply_ok),
        shm[0] as u64,
        shm[4] as u64,
        shm[5] as u64,
        shm[6] as u64,
        shm[7] as u64,
        shm[8] as u64,
        shm[9] as u64,
        shm[10] as u64,
        claim!(ok, shm_ok),
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
        claim!(ok, two_ok),
        claim!(ok, peer_ok),
        peer[17] as u64,
        peer[18] as u64,
        peer[20] as u64,
        peer[23] as u64,
        peer[24] as u64,
        peer[25] as u64,
        peer[26] as u64,
        same_va_first,
        peer[1] as u64,
        rv_base,
        rv_addr,
        rv_err,
        rv_task,
        rv_expected,
        rv_hits,
        rv_woke,
        revoke_report.map(|r| r.reason).unwrap_or("none"),
        revoke_report.map(|r| r.session).unwrap_or(u64::MAX),
        unmap_req_ack as u64,
        unmap_base as u64,
        unmap_len as u64,
        unmap_handle as u64,
        unmap_marker as u64,
        unmap_ack as u64,
        unmap_again as u64,
        unmap_geom_ack as u64,
        unmap_geom_base as u64,
        peer[29] as u64,
        map_report.map(|r| r.released_without_window).unwrap_or(u64::MAX),
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
        map_unmapped4,
        mapx[0] as u64,
        mapx[1] as u64,
        mapx[4] as u64,
        mapx[5] as u64,
        mapx[6] as u64,
        map_first_word8,
        map_last_word8_rep,
        mapx[8] as u64,
        claim!(ok, map_ok),
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
        CALL_PLATFORM_GET => {
            // The kernel's LIVE identity, never a canned answer: a session
            // that switched identity moves this value with it.
            let v = crate::platform_mode::current().id() as i64;
            if from_ring3 {
                RET_PLATFORM.store(v, Ordering::Relaxed);
            }
            v
        }
        CALL_PLATFORM_SET => serve_platform_set(a0, from_ring3),
        CALL_PLATFORM_INFO => serve_platform_info(a0),
        CALL_PLATFORM_MODE_SET => serve_platform_mode_set(a0, from_ring3),
        CALL_DISPLAY_ACQUIRE => serve_display_acquire(from_ring3),
        CALL_DISPLAY_RELEASE => serve_display_release(from_ring3),
        CALL_DISPLAY_OWNER => crate::display::owner() as i64,
        CALL_DISPLAY_SERVE => serve_display_serve(a0, from_ring3),
        CALL_DISPLAY_ATTACH => serve_display_attach(a0, from_ring3),
        CALL_DISPLAY_DETACH => serve_display_detach(from_ring3),
        CALL_DISPLAY_CLIENT => serve_display_client(a0),
        CALL_DISPLAY_STATUS => serve_display_status(),
        CALL_DISPLAY_SURFACE => serve_display_surface(a1, from_ring3),
        CALL_DISPLAY_COMPOSE => serve_display_compose(from_ring3),
        CALL_DISPLAY_SPACES => serve_display_spaces(),
        CALL_DISPLAY_SURFACE_RELEASE => serve_display_surface_release(from_ring3),
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
            // a_xfer is the optional BADGE out-pointer: u64::MAX means
            // "I do not want the sender's tag" (the same "none" sentinel
            // SEND uses), and every other value is a u64 the kernel writes
            // the message's badge into. It is validated BEFORE the queue is
            // drained, so a bad pointer cannot consume a message it never
            // delivered.
            let badge_out = a_xfer != u64::MAX;
            // The user-range validator IS the ring-3 boundary check; a
            // cpl-0 caller is the kernel, whose buffers are its own
            // (a kernel stack address is not USER_ACCESSIBLE and would
            // otherwise be refused its own memory).
            if from_ring3 && a_rights != 0 && !crate::memory::paging::user_range_ok(a_rights, 8, true) {
                return EFAULT;
            }
            if from_ring3 && badge_out && !crate::memory::paging::user_range_ok(a_xfer, 8, true) {
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
                    if badge_out {
                        // The sender's tag, written only after the payload
                        // copy succeeded: a receive that answered an error
                        // never half-delivers.
                        unsafe {
                            core::ptr::write_unaligned(a_xfer as *mut u64, m.badge as u64);
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
        CALL_CAP_SHM_UNMAP => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a1 != 0 || a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            match crate::experimental::caps::unmap_region_for(
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
            // A surface window is NOT a capability this machine frees: the
            // display owns those frames, so the display's withdrawal HANDS
            // BACK whatever tile this session still held and the same release
            // path the 0x6c row uses frees it -- in THIS space, the only one
            // that maps it. A server's clients' tiles are not handed back
            // here: they stay parked under their own sessions, which give
            // them back themselves (or on their own death). A session that
            // never asked for a tile hands back nothing.
            if let Some(tile) = crate::display::release_on_withdrawal(identity as u64) {
                let _ = release_tile(identity as u64, tile);
            }
            // ...and its address space leaves with it: a stale row would
            // name page tables that no longer exist.
            let _ = crate::experimental::space_registry::unregister(identity as u64);
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
        CALL_CAP_BADGE => {
            // A ZERO badge is the MACHINE's refusal (NotOwner: zero is
            // "unbadged", not a tag), not a shape error here -- the row
            // must not steal a by-name refusal the clause owns.
            if a0 > u32::MAX as u64 || a1 > u32::MAX as u64 {
                return EINVAL;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::badge_for(identity, a0 as u32, a1 as u32) {
                Ok(h) => h as i64,
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_NOTIFY => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::notify_for(identity, a0 as u32, a1) {
                Ok(()) => 0,
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_DEVICE_INFO => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            // A window report carries no payload, no transfer and no
            // rights claim; a1 is either "none" or a u64 out-pointer.
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && a1 != u64::MAX && !crate::memory::paging::user_range_ok(a1, 8, true) {
                return EFAULT;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::device_info_for(identity, a0 as u32) {
                Ok((code, lo, hi, held)) => {
                    if a1 != u64::MAX {
                        let window = ((lo as u64) << 32) | hi as u64;
                        unsafe {
                            core::ptr::write_unaligned(a1 as *mut u64, window);
                        }
                    }
                    ((code as i64) << 32) | held as i64
                }
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_BLOCK_INFO => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && !crate::memory::paging::user_range_ok(a1, 24, true) {
                return EFAULT;
            }
            let identity = crate::experimental::scheduler::session_identity();
            let channel = match crate::experimental::caps::block_info_for(identity, a0 as u32) {
                Ok(unit) => unit as usize,
                Err(e) => return map_cap_error(e),
            };
            match unsafe { crate::ata::read_capacity(channel) } {
                Some((sector_size, sectors)) => {
                    let mut rec = [0u8; 24];
                    rec[0..8].copy_from_slice(&(sector_size as u64).to_le_bytes());
                    rec[8..16].copy_from_slice(&sectors.to_le_bytes());
                    unsafe {
                        core::ptr::copy_nonoverlapping(rec.as_ptr(), a1 as *mut u8, 24);
                    }
                    0
                }
                None => EIO,
            }
        }
        CALL_CAP_BLOCK_READ => {
            if a0 > u32::MAX as u64 || a1 > u32::MAX as u64 {
                return EBADF;
            }
            if a_xfer == 0 || a_xfer > MAX_BLOCK_SPAN || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && !crate::memory::paging::user_range_ok(a2, a_xfer as usize, true) {
                return EFAULT;
            }
            // The probe's OWN unit for turning a byte count into sectors,
            // named apart from the drive's answer below. Both were called
            // `size` and the second shadowed the first: harmless on an ATAPI
            // CD (both 2048), a stack overread the day a drive answers more
            // than the sector buffer holds.
            let probe_sector = 2048usize;
            let sectors = ((a_xfer as usize) + probe_sector - 1) / probe_sector;
            let identity = crate::experimental::scheduler::session_identity();
            // The SPAN is checked as a whole: every sector the call will
            // touch must be inside the window, not just the first one.
            let channel = match crate::experimental::caps::block_check_span_for(
                identity,
                a0 as u32,
                a1 as u32,
                sectors as u32,
            ) {
                Ok(unit) => unit as usize,
                Err(e) => return map_cap_error(e),
            };
            let sector_bytes = crate::ata::sector_size(channel) as usize;
            let mut sector = [0u8; 2048];
            let mut done = 0usize;
            for i in 0..sectors {
                let lba = match (a1 as u32).checked_add(i as u32) {
                    Some(l) => l,
                    None => return EACCES,
                };
                if !unsafe { crate::ata::read_sector_cached(channel, lba, &mut sector) } {
                    // A device that answered the probe but not the read is
                    // a real I/O failure, distinct from "no device".
                    return EIO;
                }
                let n = (a_xfer as usize - done)
                    .min(sector_bytes)
                    .min(sector.len());
                unsafe {
                    core::ptr::copy_nonoverlapping(sector.as_ptr(), (a2 as *mut u8).add(done), n);
                }
                done += n;
                if done == a_xfer as usize {
                    break;
                }
            }
            done as i64
        }
        CALL_CAP_BLOCK_WRITE => {
            if a0 > u32::MAX as u64 || a1 > u32::MAX as u64 {
                return EBADF;
            }
            if a_xfer == 0 || a_xfer > 2048 || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && !crate::memory::paging::user_range_ok(a2, a_xfer as usize, false) {
                return EFAULT;
            }
            let identity = crate::experimental::scheduler::session_identity();
            let channel = match crate::experimental::caps::block_write_check_for(
                identity,
                a0 as u32,
                a1 as u32,
            ) {
                Ok(unit) => unit as usize,
                Err(e) => return map_cap_error(e),
            };
            // Authority is settled; now the MEDIA decides. The packet path
            // is read-only, so an ATAPI disc is EROFS by name, not EIO and
            // definitely not a silent success.
            if crate::ata::is_atapi(channel) {
                return EROFS;
            }
            // Bounded by the BUFFER as well as by the request: `size` is
            // what the DRIVE says its sector is, and the staging buffer is
            // 2048 bytes fixed, so a device answering anything larger would
            // have overrun it from caller-supplied bytes.
            let sector_bytes = crate::ata::sector_size(channel) as usize;
            let mut sector = [0u8; 2048];
            let n = (a_xfer as usize).min(sector_bytes).min(sector.len());
            unsafe {
                core::ptr::copy_nonoverlapping(a2 as *const u8, sector.as_mut_ptr(), n);
            }
            if unsafe { crate::ata::write_sector(channel, a1 as u32, &sector) } {
                crate::ata::cache_invalidate(channel, a1 as u32);
                n as i64
            } else {
                EIO
            }
        }
        CALL_CAP_BLOCK_CACHE_STATS => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            if from_ring3 && !crate::memory::paging::user_range_ok(a1, 16, true) {
                return EFAULT;
            }
            let identity = crate::experimental::scheduler::session_identity();
            if let Err(e) = crate::experimental::caps::block_info_for(identity, a0 as u32) {
                return map_cap_error(e);
            }
            let (hits, misses) = crate::ata::cache_stats();
            let mut rec = [0u8; 16];
            rec[0..8].copy_from_slice(&hits.to_le_bytes());
            rec[8..16].copy_from_slice(&misses.to_le_bytes());
            unsafe {
                core::ptr::copy_nonoverlapping(rec.as_ptr(), a1 as *mut u8, 16);
            }
            0
        }
        CALL_CAP_IOPORT_IN => {
            if a0 > u32::MAX as u64 || a1 > u16::MAX as u64 {
                return EINVAL;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::ioport_check_for(
                identity,
                a0 as u32,
                a1 as u16,
                crate::experimental::caps::rights::READ,
            ) {
                Ok(()) => unsafe { Port::<u8>::new(a1 as u16).read() as i64 },
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_IOPORT_OUT => {
            if a0 > u32::MAX as u64 || a1 > u16::MAX as u64 || a2 > u8::MAX as u64 {
                return EINVAL;
            }
            if a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::ioport_check_for(
                identity,
                a0 as u32,
                a1 as u16,
                crate::experimental::caps::rights::WRITE,
            ) {
                Ok(()) => {
                    unsafe { Port::<u8>::new(a1 as u16).write(a2 as u8) };
                    0
                }
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_FB_READ => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let x = (a1 >> 32) as u32;
            let y = a1 as u32;
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::fb_pixel_check_for(
                identity,
                a0 as u32,
                crate::experimental::caps::rights::READ,
            ) {
                Ok((lo, hi)) => {
                    if y < lo || y >= hi {
                        return EACCES;
                    }
                    match crate::devices::framebuffer::kernel_read_pixel(x, y) {
                        Some(v) => v as i64,
                        None => EFAULT,
                    }
                }
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_FB_WRITE => {
            if a0 > u32::MAX as u64 || a2 > u32::MAX as u64 {
                return EBADF;
            }
            if a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let x = (a1 >> 32) as u32;
            let y = a1 as u32;
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::fb_pixel_check_for(
                identity,
                a0 as u32,
                crate::experimental::caps::rights::WRITE,
            ) {
                Ok((lo, hi)) => {
                    if y < lo || y >= hi {
                        return EACCES;
                    }
                    if crate::devices::framebuffer::kernel_write_pixel(x, y, a2 as u32) {
                        0
                    } else {
                        EFAULT
                    }
                }
                Err(e) => map_cap_error(e),
            }
        }
        CALL_CAP_NOTIFY_TAKE => {
            if a0 > u32::MAX as u64 {
                return EBADF;
            }
            if a1 != 0 || a2 != 0 || a_xfer != u64::MAX || a_rights != 0 {
                return EINVAL;
            }
            let identity = crate::experimental::scheduler::session_identity();
            match crate::experimental::caps::take_notify_for(identity, a0 as u32) {
                Ok(bits) => bits as i64,
                Err(e) => map_cap_error(e),
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
/// The `PLATFORM_SET` (0x5e) row, out of line for the same reason
/// `serve_region_request` is: `dispatch`'s frame is shared by every arm,
/// and repainting the display is a deep call chain (device chrome, the
/// shell renderer, the skin). In its own frame it costs stack only when
/// a caller actually asks for a switch.
#[inline(never)]
fn serve_platform_set(a0: u64, from_ring3: bool) -> i64 {
    let Some(platform) = crate::platform_mode::Platform::from_id(a0) else {
        return EINVAL; // no identity owns that number
    };
    if !caller_may_mutate_display(from_ring3, crate::display::AUTH_IDENTITY) {
        return EACCES; // no identity authority for this session
    }
    match crate::platform_mode::switch_bound(platform) {
        Ok(skin) => {
            if from_ring3 {
                RET_PLATFORM_SET.store(skin.platform.id() as i64, Ordering::Relaxed);
            }
            0
        }
        Err(e) => e,
    }
}

/// The `PLATFORM_INFO` (0x5f) row: one validated write of a six-word
/// record. Out of line for the same stack reason the other two rows are.
fn serve_platform_info(a0: u64) -> i64 {
    if !crate::memory::paging::user_range_ok(a0, 48, true) {
        return EFAULT;
    }
    let Some((w, h)) = crate::devices::framebuffer::bound_mode() else {
        return ENODEV;
    };
    let mode = crate::device_mode::current();
    let layout = crate::device_mode::layout(mode, w, h);
    let platform = crate::platform_mode::current();
    let words: [u64; 6] = [
        platform.id(),
        mode.id(),
        w as u64,
        h as u64,
        layout.status as u64,
        (platform.palette().accent & 0xff_ffff) as u64,
    ];
    for (i, word) in words.iter().enumerate() {
        unsafe { core::ptr::write_unaligned((a0 + 8 * i as u64) as *mut u64, *word) };
    }
    0
}

/// The `PLATFORM_MODE_SET` (0x60) row: validates the index, then hands
/// the render+verify+publish path to `platform_mode`. Out of line for the
/// same stack reason the other identity rows are.
#[inline(never)]
fn serve_platform_mode_set(a0: u64, from_ring3: bool) -> i64 {
    let Some(mode) = crate::device_mode::DeviceMode::from_id(a0) else {
        return EINVAL; // no form factor owns that number
    };
    if !caller_may_mutate_display(from_ring3, crate::display::AUTH_MODE) {
        return EACCES; // no form-factor authority for this session
    }
    match crate::platform_mode::switch_mode_bound(mode) {
        Ok(()) => 0,
        Err(e) => e,
    }
}

/// The display authority rule, as the mutating rows apply it. Ring 0 is
/// the boot session -- the kernel is not a tenant of its own display --
/// and a ring-3 caller is whatever session the kernel recorded for it,
/// never a number it chose.
fn caller_may_mutate_display(from_ring3: bool, bit: u64) -> bool {
    if !from_ring3 {
        return true;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    crate::display::may_mutate_bit(session, bit)
}

/// `DISPLAY_ACQUIRE` (0x61): the owner is the CALLER's gate session.
#[inline(never)]
fn serve_display_acquire(from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM; // ring 0 owns the display by construction
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match crate::display::acquire(session) {
        Ok(()) => 0,
        Err(e) => e,
    }
}

/// `DISPLAY_RELEASE` (0x62): only the owner may clear its own claim.
fn serve_display_release(from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match crate::display::release(session) {
        Ok(()) => 0,
        Err(e) => e,
    }
}

/// `DISPLAY_SERVE` (0x64): the serving session is the caller.
fn serve_display_serve(policy: u64, from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM; // ring 0 supervises the boot display, not a server
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match crate::display::serve(session, policy) {
        Ok(granted) => {
            // A serving session is a session whose ADDRESS SPACE the kernel
            // must be able to name: client surfaces are mirrored into it so
            // composition can read them. The space is the one it is running
            // in right now, which is a fact the CPU's own CR3 carries.
            match crate::experimental::space_registry::register_active(session) {
                Ok(_) => granted as i64,
                Err(e) => {
                    // A server the kernel cannot locate cannot compose: undo
                    // the claim rather than leave one that cannot work.
                    let _ = crate::display::stop(session);
                    e
                }
            }
        }
        Err(e) => e,
    }
}

/// `DISPLAY_SPACES` (0x6b): three facts about the session -> space registry
/// -- how many sessions are registered, whether the CALLER is one of them,
/// and whether the display server is. The PML4 addresses themselves stay in
/// the kernel: ring 3 gets the answer to "can a window be placed for me, and
/// for my server", not a map of physical memory.
fn serve_display_spaces() -> i64 {
    use crate::experimental::space_registry;
    let session = crate::experimental::scheduler::session_identity() as u64;
    let count = space_registry::count() as u64;
    let caller = space_registry::pml4_of(session).is_some();
    let server = crate::display::server() != crate::display::NO_SERVER
        && space_registry::pml4_of(crate::display::server()).is_some();
    ((count << 32) | ((server as u64) << 1) | caller as u64) as i64
}

/// `DISPLAY_ATTACH` (0x65): the client is the caller. The answer packs the
/// slot (high 32) and the granted mask (low 32), so the caller learns both
/// without a second call and without a user-memory record.
fn serve_display_attach(wanted: u64, from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match crate::display::attach(session, wanted) {
        Ok((slot, grant)) => (((slot as u64) << 32) | (grant & 0xffff_ffff)) as i64,
        Err(e) => e,
    }
}

/// `DISPLAY_DETACH` (0x66): only the holder's own slot, by session.
fn serve_display_detach(from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match crate::display::detach(session) {
        Ok(()) => 0,
        Err(e) => e,
    }
}

/// `DISPLAY_CLIENT` (0x67): a read-only query. a0 = session, `0` = caller.
fn serve_display_client(a0: u64) -> i64 {
    let session = if a0 == 0 {
        crate::experimental::scheduler::session_identity() as u64
    } else {
        a0
    };
    match crate::display::client_grant(session) {
        Some((slot, grant)) => (((slot as u64) << 32) | (grant & 0xffff_ffff)) as i64,
        None => 0,
    }
}

/// `DISPLAY_STATUS` (0x68): `server << 32 | clients << 16 | caller grant`.
/// The grant word is the CALLER's: a server reports `3` for itself (it may
/// mutate everything), a client reports its own mask, anyone else `0`.
fn serve_display_status() -> i64 {
    let server = crate::display::server();
    let clients = crate::display::client_count() as u64;
    let session = crate::experimental::scheduler::session_identity() as u64;
    let caller_grant = match crate::display::client_grant(session) {
        Some((_, grant)) => grant,
        None => {
            if server != crate::display::NO_SERVER && server == session {
                crate::display::AUTH_ALL
            } else {
                0
            }
        }
    };
    ((server << 32) | ((clients & 0xffff) << 16) | (caller_grant & 0xffff)) as i64
}

/// `DISPLAY_SURFACE` (0x69): a client gets a surface it can DRAW INTO.
///
/// The mapping is the release's own region-request path: one page, a
/// window the kernel chooses, and a capability over it minted into the
/// CALLER's table. `allocate_region_mapping` mirrors that mapping into the
/// other proof address space over the same frames, which is the
/// cross-address-space window the server composes through -- no CR3 switch
/// and no copy. The record written to a1 carries the base and length (so
/// the client needs no constant) and the handle (so it can close it).
fn serve_display_surface(record: u64, from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    if !crate::memory::paging::user_range_ok(record, 32, true) {
        return EFAULT;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    // The slot must be the caller's, attached, and without a surface yet:
    // refused BEFORE the request so a refusal never leaves a region behind.
    if let Err(e) = crate::display::surface_free(session) {
        return e;
    }
    // The window comes from the DISPLAY's own window table, not the
    // capability machine's region windows: a surface is opened and given
    // back for as long as a server runs, so it must spend a RECLAIMED
    // budget (and, on the proof boot, must not take one of the monotonic
    // slots the region verdict pins). The capability over it is minted on
    // the mapping the kernel already made, so nothing in the capability
    // machine owns these frames -- the display releases them.
    let Some(geometry) = (unsafe { crate::experimental::region_window::map_surface(1) }) else {
        return ENOMEM; // no window: a real refusal, with nothing mapped
    };
    let (region, handle) = match crate::experimental::caps::adopt_region_for(
        session as usize,
        geometry.base,
        geometry.len,
    ) {
        Ok(v) => v,
        Err(e) => {
            unsafe { crate::experimental::region_window::release_surface(geometry) };
            return map_cap_error(e);
        }
    };
    if let Err(e) = crate::display::surface_record(
        session,
        region,
        handle,
        geometry.base,
        geometry.len,
    ) {
        // The slot changed under us: give the capability and the window back
        // rather than record a surface nobody owns.
        let _ = crate::experimental::caps::close_region_for(session as usize, handle);
        unsafe { crate::experimental::region_window::release_surface(geometry) };
        return e;
    }
    // The rights are read back from the capability itself, not assumed.
    let held = match crate::experimental::caps::region_cap_for(session as usize, handle) {
        Ok((_, held)) => held,
        Err(e) => return map_cap_error(e),
    };
    let mut rec = [0u8; 32];
    rec[0..8].copy_from_slice(&geometry.base.to_le_bytes());
    rec[8..16].copy_from_slice(&geometry.len.to_le_bytes());
    rec[16..24].copy_from_slice(&(held as u64).to_le_bytes());
    rec[24..32].copy_from_slice(&(handle as u64).to_le_bytes());
    unsafe {
        core::ptr::copy_nonoverlapping(rec.as_ptr(), record as *mut u8, 32);
    }
    0
}

/// THE single release path for a surface tile: close the capability (best
/// effort -- its record is the capability machine's business) and give the
/// WINDOW back, which is the display's. Every path that takes a surface record
/// ends here: the 0x6c row, a session's own withdrawal, and a faulted task's
/// abandonment. Returns whether the capability was closed, for the caller's
/// reporting; the window goes back either way, because the frames belong to
/// the display and not to the capability that named them.
fn release_tile(session: u64, tile: crate::display::SurfaceRecord) -> bool {
    let closed = crate::experimental::caps::close_region_for(session as usize, tile.handle).is_ok();
    unsafe {
        crate::experimental::region_window::release_surface(crate::experimental::caps::Region {
            base: tile.base,
            len: tile.len,
        })
    };
    closed
}

/// `DISPLAY_SURFACE_RELEASE` (0x6c): a client gives its surface tile back.
/// The record goes first (so a second release is `-ENOENT` by name), then
/// the capability, then the window itself: frames, mirror and slot return
/// to the display's table, which is what lets a server open and close
/// surfaces all boot without ever running out of windows. The answer packs
/// the display's LIVE and ISSUED window counts, so the caller can see
/// reclamation rather than take it on faith: `issued` climbs with every
/// opening, `live` returns to where it started.
fn serve_display_surface_release(from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    match release_surface_window(session) {
        // The two counts the caller gets back are the display's, read after
        // the window went: `live` where the table stands now, `issued` where
        // it has got to. That pair is the whole evidence for reclamation.
        Ok((live, issued)) => ((live << 32) | (issued & 0xffff_ffff)) as i64,
        Err(e) => e,
    }
}

/// THE release path for a surface tile -- one path, two callers: the 0x6c row
/// and a session's own withdrawal. Splitting these was the bug worth avoiding:
/// a row that frees a window one way and a teardown that frees it another is
/// how a tile ends up unmapped but with its slot still spent, or freed twice.
///
/// The order is the record, the capability, the window. The record goes
/// FIRST so a second release is refused `-ENOENT` by name; the capability
/// next because its record is the capability machine's; then the window --
/// frames, mirror and slot -- because those frames belong to the DISPLAY, not
/// to the capability that named them, and they must go back even when the
/// close found nothing left to do.
///
/// A session that never asked for a tile answers whatever
/// `surface_take` answered (`-EACCES` for a non-client, `-ENOENT` for a
/// client between tiles), which is why a withdrawal can ignore the result
/// without pretending it released anything.
///
/// The window comes out of the display's OWN record, never out of a
/// capability lookup: the tile has already been taken away by the time this
/// runs, so a lookup that failed would leave the window mapped with nothing
/// left in the kernel naming it. The capability is closed best-effort
/// afterwards -- the frames are the display's, and they go back either way.
fn release_surface_window(session: u64) -> Result<(u64, u64), i64> {
    let tile = crate::display::surface_take(session)?;
    if !release_tile(session, tile) {
        return Err(crate::display::ENOENT);
    }
    Ok((
        crate::experimental::region_window::surface_in_use(),
        crate::experimental::region_window::surface_issued(),
    ))
}

/// `DISPLAY_COMPOSE` (0x6b): the server blits every client surface onto the
/// display, in slot order, and reads its own work back before answering
/// with a count. A blit that did not land is `-EIO`, not a smaller number:
/// composition that half-worked is a failure.
fn serve_display_compose(from_ring3: bool) -> i64 {
    if !from_ring3 {
        return EPERM;
    }
    let session = crate::experimental::scheduler::session_identity() as u64;
    if crate::display::server() != session {
        return EACCES; // composition is the server's job, not a client's
    }
    let Some((width, height)) = crate::devices::framebuffer::bound_mode() else {
        return ENODEV;
    };
    let mode = crate::device_mode::current();
    let status = crate::device_mode::layout(mode, width, height).status as u32;
    let tile = crate::display::SURFACE_TILE;
    let mut composed = 0i64;
    for slot in 0..crate::display::CLIENT_SLOTS {
        let Some((base, len)) = crate::display::surface_geometry(slot) else {
            continue; // this client has nothing to show
        };
        if len < (tile * tile * 4) as u64 {
            return EIO; // a surface smaller than a tile is not one
        }
        // The client drew into THIS window; the region machine mirrored it
        // into this address space over the same frames, so the server reads
        // the client's pixels without a copy and without a CR3 switch.
        let mut first = 0u32;
        let mut last = 0u32;
        let (x0, y0) = crate::display::surface_origin(slot, width, height, status);
        for y in 0..tile {
            for x in 0..tile {
                let word = unsafe {
                    core::ptr::read_volatile(
                        (base + ((y * tile + x) * 4) as u64) as *const u32
                    ) & 0x00ff_ffff
                };
                if x == 0 && y == 0 {
                    first = word;
                }
                if x == tile - 1 && y == tile - 1 {
                    last = word;
                }
                if !crate::devices::framebuffer::kernel_write_pixel(x0 + x, y0 + y, word) {
                    return EIO;
                }
            }
        }
        let read_first = crate::devices::framebuffer::kernel_read_pixel(x0, y0);
        let read_last =
            crate::devices::framebuffer::kernel_read_pixel(x0 + tile - 1, y0 + tile - 1);
        if read_first != Some(first) || read_last != Some(last) {
            return EIO; // the pixels are not where the composition put them
        }
        composed += 1;
    }
    composed
}

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

    // The identity rows: GET must answer the kernel's OWN live identity
    // (not a constant), and an out-of-range SET must be refused by name
    // with nothing repainted. A successful SET runs from ring 3 in the
    // proof -- doing it here would repaint the boot screen for no reason.
    let platform_get_ok =
        dispatch(CALL_PLATFORM_GET, 0, 0, 0, 0, 0, 0) == crate::platform_mode::current().id() as i64;
    let platform_set_refused = dispatch(CALL_PLATFORM_SET, 99, 0, 0, 0, 0, 0) == EINVAL
        && dispatch(CALL_PLATFORM_SET, u64::MAX, 0, 0, 0, 0, 0) == EINVAL;
    // PLATFORM_INFO's validator half: a kernel-stack record pointer and
    // a null one must both be refused before anything is written. The
    // served half is proven from ring 3 against the live identity.
    let platform_info_refused =
        dispatch(CALL_PLATFORM_INFO, scratch.as_mut_ptr() as u64, 0, 0, 0, 0, 0) == EFAULT
            && dispatch(CALL_PLATFORM_INFO, 0, 0, 0, 0, 0, 0) == EFAULT;
    // PLATFORM_MODE_SET's refusal half: an index no form factor owns is
    // refused by name, before anything is re-rendered. A valid request
    // repaints the display, so it is proven from ring 3 instead.
    let platform_mode_refused = dispatch(CALL_PLATFORM_MODE_SET, 4, 0, 0, 0, 0, 0) == EINVAL
        && dispatch(CALL_PLATFORM_MODE_SET, u64::MAX, 0, 0, 0, 0, 0) == EINVAL;
    // The display authority rule on its REAL functions: acquisition from
    // The display authority rule on its REAL functions. Ring 0 owns the
    // boot display and can neither claim nor serve nor attach; a served
    // display clamps every client to the server's policy; a client's
    // withdrawal frees its slot and leaves the server standing; a server's
    // withdrawal dissolves everything it supervised. No test-only path:
    // these are the functions the gate calls.
    use crate::display::{AUTH_ALL, AUTH_IDENTITY, AUTH_MODE};
    let display_kernel_refused = dispatch(CALL_DISPLAY_ACQUIRE, 0, 0, 0, 0, 0, 0) == EPERM
        && dispatch(CALL_DISPLAY_SERVE, AUTH_ALL, 0, 0, 0, 0, 0) == EPERM
        && dispatch(CALL_DISPLAY_ATTACH, AUTH_ALL, 0, 0, 0, 0, 0) == EPERM
        && dispatch(CALL_DISPLAY_DETACH, 0, 0, 0, 0, 0, 0) == EPERM;
    let attach_without_server =
        crate::display::attach(8, AUTH_ALL) == Err(crate::display::ENODEV);
    let served = crate::display::serve(7, AUTH_IDENTITY).is_ok();
    let serve_busy = crate::display::serve(8, AUTH_ALL) == Err(crate::display::EBUSY);
    let serve_bad_policy = crate::display::serve(7, 0) == Err(crate::display::EINVAL)
        && crate::display::serve(7, 1 << 7) == Err(crate::display::EINVAL);
    let acquire_beside_server =
        crate::display::acquire(9) == Err(crate::display::EBUSY);
    let clamped = match crate::display::attach(8, AUTH_ALL) {
        Ok((slot, grant)) => {
            grant == AUTH_IDENTITY
                && crate::display::client_count() == 1
                && crate::display::client_grant(8) == Some((slot, AUTH_IDENTITY))
        }
        Err(_) => false,
    };
    let per_client_authority =
        crate::display::may_mutate_bit(8, AUTH_IDENTITY)
            && !crate::display::may_mutate_bit(8, AUTH_MODE)
            && crate::display::may_mutate_bit(7, AUTH_MODE)
            && !crate::display::may_mutate_bit(9, AUTH_IDENTITY);
    let attach_twice = crate::display::attach(8, AUTH_ALL) == Err(crate::display::EBUSY);
    let attach_bad_mask = crate::display::attach(9, 0) == Err(crate::display::EINVAL)
        && crate::display::attach(9, 1 << 7) == Err(crate::display::EINVAL);
    let detach_ok = crate::display::detach(8).is_ok()
        && crate::display::detach(8) == Err(crate::display::EACCES);
    // The server supervises the slot it just got back: detaching is not
    // leaving, and the same session can be re-granted the same slot.
    let no_grant = crate::display::attach(8, AUTH_MODE) == Err(crate::display::EACCES);
    let slot_reused = no_grant
        && matches!(crate::display::attach(8, AUTH_ALL), Ok((_, grant)) if grant == AUTH_IDENTITY);
    // THE PERSISTENCE CLAIM, kernel-side: a CLIENT's withdrawal frees its
    // slot and nothing else -- the server, its policy and its authority to
    // change the display all survive the client that just died.
    let _ = crate::display::release_on_withdrawal(8);
    let server_outlives_client = crate::display::client_count() == 0
        && crate::display::server() == 7
        && crate::display::policy() == AUTH_IDENTITY
        && crate::display::may_mutate_bit(7, AUTH_MODE)
        && crate::display::may_mutate_bit(7, AUTH_IDENTITY);
    // A SERVER's withdrawal dissolves the server and its clients, because
    // an orphaned policy would refuse every session for the rest of boot.
    let _ = crate::display::release_on_withdrawal(7);
    let server_dissolved = crate::display::server() == 0
        && crate::display::policy() == 0
        && crate::display::client_count() == 0
        && crate::display::attach(9, AUTH_ALL) == Err(crate::display::ENODEV)
        && crate::display::may_mutate_bit(9, AUTH_IDENTITY);
    // The exclusive form still works when nothing is served.
    let exclusive =
        crate::display::acquire(10).is_ok() && crate::display::release(10).is_ok();
    // Client surfaces: ring 0 may not claim, write or compose; a session
    // that is not a client has no surface; a client's tile is its own, is
    // bounded, cannot be claimed twice, and goes away with the slot.
    let surface_kernel_refused = dispatch(CALL_DISPLAY_SURFACE, 0, 0, 0, 0, 0, 0) == EPERM
        && dispatch(CALL_DISPLAY_COMPOSE, 0, 0, 0, 0, 0, 0) == EPERM;
    // The region itself belongs to the ring-3 proof (a request needs the
    // gate's mapping source, which the ordinary boot does not install), so
    // what the boot can prove is the SLOT bookkeeping the surface row is
    // built on: a non-client has none, the record lands in the caller's
    // own slot, a second ask is refused, and the slot frees the surface.
    let surface_needs_client = crate::display::surface_free(9) == Err(crate::display::EACCES);
    let surface_ok = crate::experimental::region_window::surface_in_use() == 0
        && surface_kernel_refused
        && surface_needs_client
        && crate::display::serve(7, AUTH_ALL).is_ok()
        && matches!(
            crate::display::attach(8, AUTH_ALL),
            Ok((_, grant)) if grant == AUTH_ALL
        )
        && match crate::display::surface_free(8) {
            Ok(slot) => {
                let recorded = crate::display::surface_record(8, 3, 7, 0x4000_2000_0000, 4096)
                    .is_ok()
                    && crate::display::surface_region(slot) == Some((3, 7))
                    && crate::display::surface_geometry(slot)
                        == Some((0x4000_2000_0000, 4096))
                    // THE TEARDOWN CONTRACT: a client holding a tile cannot
                    // detach -- it gives the tile back first -- because a
                    // detach that dropped the record would leave the window
                    // mapped with nothing naming it.
                    && crate::display::detach(8) == Err(crate::display::EBUSY)
                    // Still held: the slot keeps refusing a second ask, and
                    // the record is counted, BEFORE the take below.
                    && crate::display::surface_free(8) == Err(crate::display::EBUSY)
                    && crate::display::surface_count() == 1
                    && matches!(
                        crate::display::surface_take(8),
                        Ok(tile)
                            if tile.base == 0x4000_2000_0000
                                && tile.len == 4096
                                && tile.region == 3
                                && tile.handle == 7
                    );
                recorded
                    && crate::display::detach(8).is_ok()
                    && crate::display::surface_count() == 0
            }
            Err(_) => false,
        };
    // Dissolve the server again: a self-test must not leave the boot with a
    // claimed display or a live tile.
    let _ = crate::display::release_on_withdrawal(7);
    // STOP PARKS, IT DOES NOT DROP. A server's death dissolves its clients, but
    // their tiles are mapped in THEIR address spaces: a server's context cannot
    // free one, so dropping the record here would leave a window mapped with
    // nothing in the kernel naming it. `stop` therefore PARKS each held tile
    // under its owner's session, and the owner can still take it afterwards --
    // which is the only way it can ever be given back.
    //
    // The parked base is deliberately different from the tile above, so what
    // comes back can only be the parked record's own geometry.
    let stop_parked = crate::display::serve(7, AUTH_ALL).is_ok()
        && matches!(
            crate::display::attach(8, AUTH_ALL),
            Ok((_, grant)) if grant == AUTH_ALL
        )
        && match crate::display::surface_free(8) {
            Ok(slot) => {
                let recorded = crate::display::surface_record(8, 5, 9, 0x4000_2000_1000, 4096)
                    .is_ok()
                    && crate::display::surface_geometry(slot) == Some((0x4000_2000_1000, 4096))
                    // The server dies with the client still holding the tile.
                    && crate::display::stop(7).is_ok()
                    && crate::display::server() == 0
                    && crate::display::client_count() == 0
                    // PARKED, not dropped: the record and its WINDOW are still
                    // the display's to account for.
                    && crate::display::surface_count() == 1
                    && crate::display::surface_geometry(slot) == Some((0x4000_2000_1000, 4096))
                    // The owner takes it BY SESSION, with no client slot left
                    // to resolve it through: this is the parked lookup.
                    && matches!(
                        crate::display::surface_take(8),
                        Ok(tile)
                            if tile.base == 0x4000_2000_1000
                                && tile.len == 4096
                                && tile.region == 5
                                && tile.handle == 9
                    )
                    && crate::display::surface_count() == 0
                    // Consumed, not merely hidden: a second take is refused by
                    // name.
                    && crate::display::surface_take(8) == Err(crate::display::ENOENT)
                    // The client cannot detach any more -- its slot went with
                    // the server it was attached to (`-EACCES`, not a silent
                    // success). The tile-less detach itself is covered above,
                    // where a client gives its tile back and then detaches.
                    && crate::display::detach(8) == Err(crate::display::EACCES);
                recorded
            }
            Err(_) => false,
        };
    let surface_cleared =
        crate::display::server() == 0 && crate::display::surface_count() == 0;
    // The session -> space registry: its own rules first, then the row that
    // reports it. Ring 0 has no session identity, so the row answers "no
    // caller registered, no server, zero entries" -- and the table must be
    // empty again after the self-test, or every later boot would start with
    // a space nobody owns.
    let spaces_ok = crate::experimental::space_registry::self_test()
        && crate::experimental::space_registry::count() == 0
        && dispatch(CALL_DISPLAY_SPACES, 0, 0, 0, 0, 0, 0) == 0;
    let display_ok = display_kernel_refused
        && attach_without_server
        && served
        && serve_busy
        && serve_bad_policy
        && acquire_beside_server
        && clamped
        && per_client_authority
        && attach_twice
        && attach_bad_mask
        && detach_ok
        && slot_reused
        && server_outlives_client
        && server_dissolved
        && exclusive
        && surface_ok
        && surface_cleared
        && stop_parked
        && spaces_ok;
    let platform_ok = platform_get_ok
        && platform_set_refused
        && platform_info_refused
        && platform_mode_refused
        && display_ok;

    let ok = gfx_ok && input_ok && refused && validator_ok && self_ok && ipc_shape_ok && caps_rows_ok && input_row_ok && platform_ok;
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
    crate::serial_println!(
        "  service-0x84: identity rows -- PLATFORM_GET names the live identity {} ({}), an out-of-range PLATFORM_SET is refused -EINVAL {} ; a successful switch is proven from ring 3",
        crate::platform_mode::current().as_str(),
        if platform_get_ok { "OK" } else { "FAILED" },
        if platform_set_refused { "OK" } else { "FAILED" }
    );
    crate::serial_println!(
        "  service-0x84: identity rows -- PLATFORM_INFO refuses a kernel-stack and a null record pointer before writing {} (the served record is proven from ring 3)",
        if platform_info_refused { "OK" } else { "FAILED" }
    );
    crate::serial_println!(
        "  service-0x84: identity rows -- PLATFORM_MODE_SET refuses a form factor no index owns -EINVAL {} before re-rendering (a served request is proven from ring 3)",
        if platform_mode_refused { "OK" } else { "FAILED" }
    );
    ok
}
