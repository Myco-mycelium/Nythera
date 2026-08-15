//! Nyrqis IPC serving loop — ADR-0021 (NyRuntime direction).
//!
//! **First increment 2026-08-15.** The first NyRuntime-shaped artifact:
//! the IPC serving loop moves behind the FFI boundary. Per ADR-0021,
//! the loop owns the whole dispatch cycle for the daemon's service
//! socket — `poll` → `recvmsg` (`SCM_CREDENTIALS`) → wire-codec parse →
//! sender authorization → service dispatch → `sendto` reply — inside
//! the Rust process loop, and crosses the boundary once per *batch*
//! (a bounded drain of datagrams) instead of once per message. The
//! per-message ctypes boundary tax of the Python floor
//! (BENCHMARK_RESULTS.md §20) is paid once per batch, not twice per
//! round trip.
//!
//! Scope of this increment (honest, ADR-0021's gate-on-data rule):
//!
//! - The loop serves the built-in `ping` op of the status service
//!   (`{"op": "ping"}` → the exact floor reply payload) — the op that
//!   needs no live policy data beyond sender identity.
//! - Sender authorization policy crosses the boundary as plain data at
//!   loop creation (ABI-001: no pointers into Python objects): a
//!   pid→container table, the trusted-uid set, and the operator id —
//!   the same inputs the floor's `_authorized` uses. The loop does the
//!   execution; Python supplies only the data.
//! - Non-ping CALLs from authorized senders are QUEUED and handed to
//!   Python as plain data (ADR-0021 decision point 1 — the dispatch
//!   handoff): the driver drains the batch
//!   (``nyrqis_ipcd_loop_drain_requests``), dispatches through the
//!   Python service handlers (the reply wires are built by the same
//!   codec the floor uses), and the replies come back through
//!   ``nyrqis_ipcd_loop_enqueue_replies``, which the loop routes to
//!   the RECORDED sender address. Requests the handlers decline are
//!   reaped by ``nyrqis_ipcd_loop_discard_requests``. Any other
//!   datagram (non-CALL, malformed wire, unknown or forged sender) is
//!   dropped at the trust boundary, mirroring the floor's serve-loop
//!   behavior (drop, never raise, never crash).
//! - The floor stays shipped. The loop lands behind the differential
//!   conformance gate (reply semantics byte-equivalent to the floor)
//!   and the §N benchmark A/B; ADR-0021's close gate (beat the floor
//!   AND < 100 µs wire median) decides when the floor is demoted.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state, no pointers into
//! Python objects. Return convention: **0 on success or a negative
//! value** — `-errno` for real failures, `ERR_INTERNAL` (-4096) for
//! module failures (outside the errno range by contract, matching the
//! other migration loaders). `nyrqis_ipcd_loop_step` returns the
//! number of datagrams drained (≥ 0).
//!
//! The socket is NOT bound here and the loop does NOT close the fd:
//! path management (0700 perms, `SO_PASSCRED`, unlink on close) stays
//! on the Python floor (ADR-0021 "what stays"), which passes the
//! bound fd in. The loop never blocks past its timeout (poll first,
//! `MSG_DONTWAIT` recvmsg) and is safe on both blocking and
//! non-blocking fds.

use std::ffi::{c_char, c_int, c_void, CStr};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns). ERR_INTERNAL is OUTSIDE the
// errno range (1..=4095) by contract: -errno maps to OSError on the
// Python side, so an in-range code would be misreported.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL — null/absent pointers, bad fd
pub const ERR_INTERNAL: i32 = -4096;

/// Total wire-message sanity bound (16 MiB, mirroring the codec crate)
/// and per-field bound for the string fields (1 MiB).
const MAX_WIRE_BYTES: usize = 16 * 1024 * 1024;
const MAX_FIELD_BYTES: usize = 1 * 1024 * 1024;

const WIRE_VERSION: u8 = 1;
const MAGIC: &[u8; 4] = b"NYRQ";
const MT_CALL: u8 = 2;
const MT_REPLY: u8 = 3;

/// Receive buffer: the Python floor receives into 64 KiB, so a full
/// frame fits (bounds above still enforced on the parse).
const RECV_BUF: usize = 64 * 1024;
/// Ancillary buffer: room for a `ucred` (CMSG_SPACE(12) ≈ 32) with
/// headroom, aligned for `cmsghdr`.
const CMSG_BUF: usize = 128;

/// The status service's reply payload for `ping`, byte-identical to the
/// floor's `json.dumps(..., sort_keys=True)` (ipc/service.py). The
/// sender identity (container id or operator id) is spliced into the
/// `container` field.
const PING_REPLY_PREFIX: &[u8] = b"{\"container\": \"";
const PING_REPLY_SUFFIX: &[u8] =
    b"\", \"echo\": \"pong\", \"ok\": true, \"service\": \"nyrqis.backend.status\", \"service_version\": \"1.0\"}";
/// One pid→container mapping (the loop's authorization policy data).
/// `container` is a NUL-terminated UTF-8 container id.
#[repr(C)]
pub struct PidEntry {
    pub pid: i32,
    pub container: *const c_char,
}

/// The loop's sender-authorization policy (a snapshot of the backend's
/// registry + trusted-uid set, supplied across the boundary at loop
/// creation and refreshed through ``nyrqis_ipcd_loop_set_policy`` — the
/// per-container pid-table refresh, ADR-0021's
/// policy-data-across-the-boundary contract).
#[derive(Clone)]
struct Policy {
    pids: Vec<(i32, Vec<u8>)>,
    trusted_uids: Vec<i32>,
    operator_id: Vec<u8>,
}

/// A request the loop queued for the Python dispatch handoff
/// (ADR-0021 decision point 1): a non-ping CALL from an authorized
/// sender that the loop cannot answer itself. The driver drains these
/// (``nyrqis_ipcd_loop_drain_requests``), dispatches them through the
/// Python service handlers, and the replies come back through
/// ``nyrqis_ipcd_loop_enqueue_replies``, which sends each to the
/// RECORDED sender address (the caller's bound path, captured at
/// recv) — the reply routing never trusts the wire.
struct PendingRequest {
    /// The request's message_id (the reply's ``reply_to`` correlates).
    message_id: Vec<u8>,
    /// The full request wire (bounded by RECV_BUF — a datagram larger
    /// than the recv buffer is truncated and dropped by the parse).
    wire: Vec<u8>,
    /// Raw ``sockaddr_un`` bytes of the sender (up to ``addr_len``).
    addr: [u8; 128],
    addr_len: usize,
}

/// Defensive bound on the pending queue. The driver drains after every
/// step, so pending stays at one step's worth in practice; the bound is
/// fail-closed insurance against a wedged driver (new requests are
/// dropped, like the floor drops an unanswered request — never
/// answered, never crashed).
const MAX_PENDING: usize = 4096;

/// The serving-loop handle (opaque to the caller). The policy is behind
/// a ``Mutex`` because ``set_policy`` can be called from the host's
/// main thread (container spawn/terminate) while the drive thread is
/// mid-step — the reads in the step loop and the writes in set_policy
/// must not race (the FFI surface itself still exposes no shared state;
/// the mutex is internal). ``pending`` is only ever touched by the
/// step loop thread and the drain/enqueue/discard calls, which the
/// driver serializes on the same thread — no lock needed.
struct IpcLoop {
    fd: c_int,
    batch_max: u32,
    policy: std::sync::Mutex<Policy>,
    recv_buf: Vec<u8>,
    ctrl: AlignedCmsg,
    seq: u64,
    pending: Vec<PendingRequest>,
}

impl IpcLoop {
    /// Resolve the kernel-attached ``(pid, uid)`` to a container id or
    /// the operator id (None = unknown sender — drop). Reads the policy
    /// under the lock and clones the id out (the caller owns it).
    fn resolve_sender(&self, pid: i32, uid: i32) -> Option<Vec<u8>> {
        let policy = self.policy.lock().unwrap();
        for (p, container) in &policy.pids {
            if *p == pid {
                return Some(container.clone());
            }
        }
        if policy.trusted_uids.contains(&uid) {
            return Some(policy.operator_id.clone());
        }
        None
    }
}

/// CMSG buffer with the alignment cmsghdr requires.
#[repr(C, align(16))]
struct AlignedCmsg([u8; CMSG_BUF]);

impl IpcLoop {
    /// Build the reply wire for a ping CALL. Matches the floor's
    /// `IPCDatagramServer.reply` semantics: message_type REPLY (3),
    /// fresh message_id, empty sender/receiver, `reply_to` = the call's
    /// message_id, the ping payload, empty caps, metadata `{}`.
    fn build_ping_reply(&mut self, call_id: &[u8], sender: &[u8]) -> Vec<u8> {
        let timestamp = now_f64();
        let message_id = format!(
            "ipcd-{}-{}-{:x}", std::process::id(), timestamp_micros(), self.seq
        );
        self.seq += 1;
        let payload = build_ping_payload(sender);
        let mut wire = Vec::with_capacity(64 + call_id.len() + payload.len());
        wire.extend_from_slice(MAGIC);
        wire.push(WIRE_VERSION);
        wire.push(MT_REPLY);
        wire.extend_from_slice(&timestamp.to_le_bytes());
        push_field(&mut wire, message_id.as_bytes());
        push_field(&mut wire, b""); // sender_id — the floor leaves it empty
        push_field(&mut wire, b""); // receiver_id — empty
        push_field(&mut wire, call_id); // reply_to
        push_field(&mut wire, &payload);
        push_field(&mut wire, b""); // caps_flat
        push_field(&mut wire, b"{}"); // metadata — the floor's {} json
        wire
    }
}

fn errno_or(e: i32) -> i32 {
    std::io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(e)
}

fn now_f64() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut ts) };
    ts.tv_sec as f64 + (ts.tv_nsec as f64) / 1_000_000_000.0
}

fn timestamp_micros() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut ts) };
    ts.tv_sec as i64 * 1_000_000 + ts.tv_nsec as i64 / 1_000
}

/// Append a length-prefixed field (the wire codec's u32-len framing).
fn push_field(wire: &mut Vec<u8>, field: &[u8]) {
    wire.extend_from_slice(&(field.len() as u32).to_le_bytes());
    wire.extend_from_slice(field);
}

/// The ping reply payload, byte-identical to the floor's
/// `json.dumps(..., sort_keys=True)` with the sender spliced in.
fn build_ping_payload(sender: &[u8]) -> Vec<u8> {
    let mut payload = Vec::with_capacity(
        PING_REPLY_PREFIX.len() + sender.len() + PING_REPLY_SUFFIX.len(),
    );
    payload.extend_from_slice(PING_REPLY_PREFIX);
    payload.extend_from_slice(sender);
    payload.extend_from_slice(PING_REPLY_SUFFIX);
    payload
}

/// The minimal dispatch parse: extract `message_type`, `message_id`,
/// and `payload` from a wire buffer. Returns None on any malformation
/// (the loop drops those at the trust boundary, like the floor). The
/// full codec parse remains rust/ipc's artifact; this is the loop's
/// dispatch subset, bounded identically and pinned by the differential
/// conformance gate against the floor.
struct DispatchMsg<'a> {
    message_type: u8,
    message_id: &'a [u8],
    sender_id: &'a [u8],
    // The request's reply_to is always empty for CALLs; kept parsed so
    // the conformance reader (and tests) can verify REPLY correlation.
    #[allow(dead_code)]
    reply_to: &'a [u8],
    payload: &'a [u8],
}

fn parse_dispatch(data: &[u8]) -> Option<DispatchMsg<'_>> {
    if data.len() < 14 || &data[0..4] != MAGIC || data[4] != WIRE_VERSION {
        return None;
    }
    let message_type = data[5];
    if message_type > 4 {
        return None;
    }
    let mut pos = 14usize;
    let message_id = take_str(data, &mut pos)?;
    let sender_id = take_str(data, &mut pos)?;
    let _receiver = take_str(data, &mut pos)?;
    let reply_to = take_str(data, &mut pos)?;
    let payload = take_data(data, &mut pos)?;
    let _caps = take_data(data, &mut pos)?;
    let _metadata = take_data(data, &mut pos)?;
    if pos != data.len() {
        return None; // trailing bytes — malformed, exactly like the codec
    }
    Some(DispatchMsg {
        message_type,
        message_id,
        sender_id,
        reply_to,
        payload,
    })
}

fn take_str<'a>(data: &'a [u8], pos: &mut usize) -> Option<&'a [u8]> {
    let field = take_field(data, pos)?;
    if field.len() > MAX_FIELD_BYTES {
        return None;
    }
    Some(field)
}

fn take_data<'a>(data: &'a [u8], pos: &mut usize) -> Option<&'a [u8]> {
    take_field(data, pos)
}

fn take_field<'a>(data: &'a [u8], pos: &mut usize) -> Option<&'a [u8]> {
    if *pos + 4 > data.len() {
        return None;
    }
    let len = u32::from_le_bytes([data[*pos], data[*pos + 1], data[*pos + 2], data[*pos + 3]])
        as usize;
    *pos += 4;
    if len > MAX_WIRE_BYTES || *pos + len > data.len() {
        return None;
    }
    let field = &data[*pos..*pos + len];
    *pos += len;
    Some(field)
}

/// True when the payload is a JSON object whose `"op"` key has the
/// string value `"ping"` (the floor's `request.get("op") == "ping"`
/// contract, without a JSON dependency). A minimal key/value scanner:
/// reads every JSON string in order; when a string equals `"op"`, the
/// next non-whitespace token must be `:` followed by the string value
/// `"ping"`. Malformed payloads scan to false (the loop drops them).
fn payload_is_ping(payload: &[u8]) -> bool {
    let mut i = 0usize;
    while i < payload.len() {
        if payload[i] == b'"' {
            match read_json_string(payload, i) {
                Some((key, next)) => {
                    if key == b"op" {
                        let mut j = next;
                        while j < payload.len() && payload[j].is_ascii_whitespace() {
                            j += 1;
                        }
                        if j >= payload.len() || payload[j] != b':' {
                            return false;
                        }
                        j += 1;
                        while j < payload.len() && payload[j].is_ascii_whitespace() {
                            j += 1;
                        }
                        if j < payload.len() && payload[j] == b'"' {
                            if let Some((value, _)) = read_json_string(payload, j) {
                                return value == b"ping";
                            }
                        }
                        return false;
                    }
                    i = next;
                }
                None => return false, // unterminated string — malformed
            }
        } else {
            i += 1;
        }
    }
    false
}

/// Read the JSON string starting at `data[start] == b'"'`. Returns the
/// string's bytes (escape sequences kept verbatim) and the index just
/// past the closing quote, or None when unterminated.
fn read_json_string(data: &[u8], start: usize) -> Option<(&[u8], usize)> {
    let mut i = start + 1;
    let out_start = i;
    while i < data.len() {
        match data[i] {
            b'\\' => i += 2,
            b'"' => return Some((&data[out_start..i], i + 1)),
            _ => i += 1,
        }
    }
    None
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_ipcd_version() -> u32 {
    ABI_VERSION
}

/// Create a serving loop over an already-bound, SO_PASSCRED-enabled
/// socket `fd`. `pids`/`pid_count` is the pid→container authorization
/// table (any order), `trusted_uids`/`trusted_count` the trusted uid
/// set, and `operator` the operator id (NUL-terminated, used for
/// trusted uids whose pid is unknown — exactly the floor's
/// `_authorized` fallback). Returns an opaque handle or NULL.
///
/// Ownership: the loop does NOT close `fd` — the Python floor owns the
/// socket lifecycle (bind, unlink on close).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_new(
    fd: c_int,
    batch_max: u32,
    pids: *const PidEntry,
    pid_count: usize,
    trusted_uids: *const i32,
    trusted_count: usize,
    operator: *const c_char,
) -> *mut c_void {
    if fd < 0 || batch_max == 0 || operator.is_null() {
        return std::ptr::null_mut();
    }
    let mut policy = Policy {
        pids: Vec::with_capacity(pid_count),
        trusted_uids: Vec::with_capacity(trusted_count),
        operator_id: Vec::new(),
    };
    let operator_bytes = match CStr::from_ptr(operator).to_bytes() {
        p if p.is_empty() => return std::ptr::null_mut(),
        p => p,
    };
    policy.operator_id.extend_from_slice(operator_bytes);
    if !pids.is_null() {
        for i in 0..pid_count {
            let entry = &*pids.add(i);
            if entry.container.is_null() {
                return std::ptr::null_mut();
            }
            let container = CStr::from_ptr(entry.container).to_bytes();
            if container.is_empty() {
                return std::ptr::null_mut();
            }
            policy.pids.push((entry.pid, container.to_vec()));
        }
    }
    if !trusted_uids.is_null() {
        for i in 0..trusted_count {
            policy.trusted_uids.push(*trusted_uids.add(i));
        }
    }
    let mut loop_handle = Box::new(IpcLoop {
        fd,
        batch_max,
        policy: std::sync::Mutex::new(policy),
        recv_buf: vec![0u8; RECV_BUF],
        ctrl: AlignedCmsg([0u8; CMSG_BUF]),
        seq: 0,
        pending: Vec::new(),
    });
    // Set SO_PASSCRED defensively: the floor already sets it at bind,
    // but a caller that forgets would silently lose identity.
    let one: c_int = 1;
    unsafe {
        libc::setsockopt(
            fd,
            libc::SOL_SOCKET,
            libc::SO_PASSCRED,
            &one as *const c_int as *const c_void,
            std::mem::size_of::<c_int>() as libc::socklen_t,
        );
    }
    let ptr: *mut IpcLoop = &mut *loop_handle;
    std::mem::forget(loop_handle);
    ptr as *mut c_void
}

/// Replace the loop's sender-authorization policy in place (the
/// per-container pid-table refresh — ADR-0021's
/// policy-data-across-the-boundary contract). Same marshalling as
/// `nyrqis_ipcd_loop_new`; safe to call from another thread while the
/// drive thread is stepping (the policy is behind a mutex). Returns 0
/// or `ERR_INVALID_ARGS`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_set_policy(
    handle: *mut c_void,
    pids: *const PidEntry,
    pid_count: usize,
    trusted_uids: *const i32,
    trusted_count: usize,
    operator: *const c_char,
) -> i32 {
    if handle.is_null() || operator.is_null() {
        return ERR_INVALID_ARGS;
    }
    let operator_bytes = match CStr::from_ptr(operator).to_bytes() {
        p if p.is_empty() => return ERR_INVALID_ARGS,
        p => p,
    };
    let mut policy = Policy {
        pids: Vec::with_capacity(pid_count),
        trusted_uids: Vec::with_capacity(trusted_count),
        operator_id: operator_bytes.to_vec(),
    };
    if !pids.is_null() {
        for i in 0..pid_count {
            let entry = &*pids.add(i);
            if entry.container.is_null() {
                return ERR_INVALID_ARGS;
            }
            let container = CStr::from_ptr(entry.container).to_bytes();
            if container.is_empty() {
                return ERR_INVALID_ARGS;
            }
            policy.pids.push((entry.pid, container.to_vec()));
        }
    }
    if !trusted_uids.is_null() {
        for i in 0..trusted_count {
            policy.trusted_uids.push(*trusted_uids.add(i));
        }
    }
    let loop_handle = &mut *(handle as *mut IpcLoop);
    *loop_handle.policy.lock().unwrap() = policy;
    0
}

/// Drain the loop's queued non-ping requests into `buf` (ADR-0021
/// decision point 1 — the Python dispatch handoff). The records are
/// plain data: ``[u32 len_le][wire bytes]`` per request, in queue
/// order. Returns the number of bytes written (0 = nothing pending) or
/// ``-ENOBUFS`` when the first record does not fit `cap` (the caller
/// grows the buffer). The queue is NOT emptied — the requests still
/// owe replies, which come back through
/// ``nyrqis_ipcd_loop_enqueue_replies``; the driver calls
/// ``nyrqis_ipcd_loop_discard_requests`` after a batch to reap any the
/// handlers chose not to answer.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_drain_requests(
    handle: *mut c_void,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    if handle.is_null() || (buf.is_null() && cap > 0) {
        return ERR_INVALID_ARGS;
    }
    let loop_handle = &mut *(handle as *mut IpcLoop);
    let mut written: usize = 0;
    for req in &loop_handle.pending {
        let rec_len = 4 + req.wire.len();
        if written + rec_len > cap {
            if written == 0 {
                return -libc::ENOBUFS;
            }
            break;
        }
        let dst = (buf as *mut u8).add(written);
        let len_prefix = (req.wire.len() as u32).to_le_bytes();
        len_prefix.as_ptr().copy_to(dst, 4);
        req.wire.as_ptr().copy_to(dst.add(4), req.wire.len());
        written += rec_len;
    }
    written as i32
}

/// A reply wire for the dispatch handoff (plain data — the wire the
/// Python service built with the floor's codec; the loop only routes
/// it).
#[repr(C)]
pub struct ReplyWire {
    pub wire: *const u8,
    pub wire_len: usize,
}

/// Send the drained requests' replies. For each reply wire: parse its
/// ``reply_to`` (the request's message_id), find the matching pending
/// request, send the wire to the RECORDED sender address, and remove
/// it from the queue. Replies with an unknown/empty ``reply_to`` are
/// skipped (the floor has no such reply either — a handler can only
/// reply to the call it was given). Returns 0 or ``-errno``.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_enqueue_replies(
    handle: *mut c_void,
    replies: *const ReplyWire,
    count: usize,
) -> i32 {
    if handle.is_null() || (replies.is_null() && count > 0) {
        return ERR_INVALID_ARGS;
    }
    let loop_handle = &mut *(handle as *mut IpcLoop);
    for i in 0..count {
        let entry = &*replies.add(i);
        if entry.wire.is_null() || entry.wire_len == 0 {
            continue;
        }
        let wire = std::slice::from_raw_parts(entry.wire, entry.wire_len);
        let parsed = match parse_dispatch(wire) {
            Some(p) => p,
            None => continue, // malformed reply — drop, like the floor
        };
        if parsed.reply_to.is_empty() {
            continue;
        }
        let idx = match loop_handle
            .pending
            .iter()
            .position(|r| r.message_id == parsed.reply_to)
        {
            Some(i) => i,
            None => continue, // unknown call — no request to answer
        };
        let req = loop_handle.pending.swap_remove(idx);
        let sent = libc::sendto(
            loop_handle.fd,
            wire.as_ptr() as *const c_void,
            wire.len(),
            0,
            req.addr.as_ptr() as *const libc::sockaddr,
            req.addr_len as libc::socklen_t,
        );
        if sent < 0 {
            return -errno_or(libc::EIO);
        }
    }
    0
}

/// Reap the queue: drop every pending request the handlers did not
/// answer (the driver calls this after processing a drained batch,
/// mirroring the floor where a no-reply op simply produces no reply).
/// Returns 0.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_discard_requests(
    handle: *mut c_void,
) -> i32 {
    if handle.is_null() {
        return ERR_INVALID_ARGS;
    }
    let loop_handle = &mut *(handle as *mut IpcLoop);
    loop_handle.pending.clear();
    0
}

/// Reusable receive buffer for the client half: a per-call
/// ``[0u8; RECV_BUF]`` stack array would zero 64 KiB on EVERY call
/// (the same allocation pathology the drain buffer had — measured
/// ~8 µs/call). A static buffer is safe because the client call is
/// synchronous (one call at a time; concurrent callers serialize on
/// the lock, which is far cheaper than a per-call memset).
static CLIENT_RECV: std::sync::Mutex<[u8; RECV_BUF]> =
    std::sync::Mutex::new([0u8; RECV_BUF]);

/// Monotonic milliseconds (the client call's deadline clock).
fn now_monotonic_ms() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as i64 * 1000 + ts.tv_nsec as i64 / 1_000_000
}

/// One CALL round trip through the client half of the loop
/// (ADR-0021 — the client half behind the boundary): ``sendto`` the
/// request wire to ``peer_path``, then ``poll`` + ``recvmsg``
/// (``MSG_DONTWAIT``) until a REPLY whose ``reply_to`` matches the
/// call's ``message_id`` arrives or ``timeout_ms`` elapses. Datagrams
/// that are not that reply (other replies, malformed wire, noise) are
/// dropped — exactly the floor's ``IPCClient.call`` correlation loop,
/// now inside the Rust process.
///
/// Returns the reply wire length (≥ 0, copied into ``reply_buf``),
/// ``-ETIMEDOUT`` on timeout, ``-ENOBUFS`` when the reply exceeds
/// ``reply_cap``, ``-EINVAL`` for invalid arguments, or ``-errno``.
/// The caller (Python) owns the fd (the floor binds it) and the
/// reply buffer (reused across calls — the transport v2 contract).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_client_call(
    fd: c_int,
    peer_path: *const c_char,
    call_wire: *const u8,
    call_wire_len: usize,
    reply_buf: *mut u8,
    reply_cap: usize,
    timeout_ms: i64,
) -> i32 {
    if fd < 0
        || peer_path.is_null()
        || (call_wire.is_null() && call_wire_len > 0)
        || (reply_buf.is_null() && reply_cap > 0)
        || timeout_ms < 0
    {
        return ERR_INVALID_ARGS;
    }
    let path = match CStr::from_ptr(peer_path).to_bytes() {
        p if p.is_empty() => return ERR_INVALID_ARGS,
        p => p,
    };
    if path.len() > 107 {
        return -libc::EINVAL; // sun_path bound (the transport's limit)
    }
    let wire = std::slice::from_raw_parts(call_wire, call_wire_len);
    // The call's message_id is the correlation anchor — parse it from
    // the request wire (a malformed request is rejected up front).
    let call_id = match parse_dispatch(wire) {
        Some(p) if p.message_type == MT_CALL => p.message_id.to_vec(),
        _ => return ERR_INVALID_ARGS,
    };
    // Peer address with the transport's trimmed sockaddr_un length
    // (the loop's sendto requires the real sun_path, not a cast).
    let mut sun: libc::sockaddr_un = std::mem::zeroed();
    sun.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (i, b) in path.iter().enumerate() {
        sun.sun_path[i] = *b as c_char;
    }
    let addr_len = (2 + path.len() + 1) as libc::socklen_t;
    let sent = libc::sendto(
        fd,
        wire.as_ptr() as *const c_void,
        wire.len(),
        0,
        &sun as *const libc::sockaddr_un as *const libc::sockaddr,
        addr_len,
    );
    if sent < 0 {
        return -errno_or(libc::EIO);
    }
    if sent as usize != wire.len() {
        return -libc::EIO;
    }

    let deadline = now_monotonic_ms() + timeout_ms;
    let mut ctrl = AlignedCmsg([0u8; CMSG_BUF]);
    let mut src: libc::sockaddr_un = std::mem::zeroed();
    // Locked once for the whole call: the guard lives until the reply
    // arrives, so the buffer is not zeroed per poll iteration.
    let mut buf_guard = CLIENT_RECV.lock().unwrap();
    let buf: &mut [u8] = &mut *buf_guard;
    loop {
        let now = now_monotonic_ms();
        if now >= deadline {
            return -libc::ETIMEDOUT;
        }
        let remaining = (deadline - now).min(c_int::MAX as i64) as c_int;
        let mut pfd = libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        };
        let prc = libc::poll(&mut pfd, 1, remaining);
        if prc < 0 {
            let e = errno_or(libc::EIO);
            if e == libc::EINTR {
                continue;
            }
            return -e;
        }
        if prc == 0 {
            return -libc::ETIMEDOUT;
        }
        let mut iov = libc::iovec {
            iov_base: buf.as_mut_ptr() as *mut c_void,
            iov_len: buf.len(),
        };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_name = &mut src as *mut libc::sockaddr_un as *mut c_void;
        msg.msg_namelen = std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t;
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = ctrl.0.as_mut_ptr() as *mut c_void;
        msg.msg_controllen = ctrl.0.len();
        let n = libc::recvmsg(fd, &mut msg, libc::MSG_DONTWAIT);
        if n < 0 {
            let e = errno_or(libc::EIO);
            if e == libc::EINTR || e == libc::EAGAIN || e == libc::EWOULDBLOCK {
                continue; // spurious wake — re-poll until the deadline
            }
            return -e;
        }
        if n == 0 {
            continue; // defensive — UDS datagrams cannot EOF
        }
        let data = &buf[..n as usize];
        let parsed = match parse_dispatch(data) {
            Some(p) => p,
            None => continue, // malformed — drop, like the floor
        };
        if parsed.message_type != MT_REPLY || parsed.reply_to != call_id.as_slice() {
            continue; // not our reply — drop, exactly the floor's correlation
        }
        if data.len() > reply_cap {
            return -libc::ENOBUFS;
        }
        std::ptr::copy_nonoverlapping(data.as_ptr(), reply_buf, data.len());
        return data.len() as i32;
    }
}

/// Run one loop iteration: `poll` up to `timeout_ms` (negative =
/// block), then drain up to the batch bound, answering each valid ping
/// CALL. Returns the number of datagrams drained (answered or dropped)
/// — 0 on a clean timeout. Never blocks past the timeout. Returns
/// `-errno` on a real failure.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_step(
    handle: *mut c_void,
    timeout_ms: i64,
) -> i32 {
    if handle.is_null() {
        return ERR_INVALID_ARGS;
    }
    let loop_handle = &mut *(handle as *mut IpcLoop);
    let ms: c_int = if timeout_ms < 0 {
        -1
    } else {
        timeout_ms.min(c_int::MAX as i64) as c_int
    };
    let mut pfd = libc::pollfd {
        fd: loop_handle.fd,
        events: libc::POLLIN,
        revents: 0,
    };
    let prc = libc::poll(&mut pfd, 1, ms);
    if prc < 0 {
        return -errno_or(libc::EIO);
    }
    if prc == 0 {
        return 0; // timeout — nothing to drain
    }

    let mut processed: i32 = 0;
    let mut src: libc::sockaddr_un = std::mem::zeroed();
    for _ in 0..loop_handle.batch_max {
        let mut iov = libc::iovec {
            iov_base: loop_handle.recv_buf.as_mut_ptr() as *mut c_void,
            iov_len: loop_handle.recv_buf.len(),
        };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_name = &mut src as *mut libc::sockaddr_un as *mut c_void;
        msg.msg_namelen = std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t;
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = loop_handle.ctrl.0.as_mut_ptr() as *mut c_void;
        msg.msg_controllen = loop_handle.ctrl.0.len();

        let n = libc::recvmsg(loop_handle.fd, &mut msg, libc::MSG_DONTWAIT);
        if n < 0 {
            let e = errno_or(libc::EIO);
            if e == libc::EAGAIN || e == libc::EWOULDBLOCK {
                break; // drained
            }
            return -e;
        }
        if n == 0 {
            break; // no data (defensive — UDS datagrams cannot EOF)
        }
        processed += 1;

        // Kernel-attached identity (SO_PASSCRED, set at bind).
        let (pid, uid, _gid) = read_creds(&msg);

        // Immutable reads first (parse + sender resolution), scoped so
        // their borrows of the loop end before the mutable reply build.
        let (call_id, sender, is_ping) = {
            let data = &loop_handle.recv_buf[..n as usize];
            let parsed = match parse_dispatch(data) {
                Some(p) => p,
                None => continue, // malformed — drop, like the floor
            };
            if parsed.message_type != MT_CALL {
                continue; // SEND/RECEIVE/NOTIFY stay on the floor's path
            }
            // Sender authorization: pid → container, else trusted uid
            // → operator (the floor's `_authorized` contract), and the
            // wire `sender_id` must MATCH the kernel-resolved identity
            // (the floor drops forged senders — the kernel creds are
            // the attribution anchor, never the wire).
            let sender = match loop_handle.resolve_sender(pid, uid) {
                Some(s) => s,
                None => continue, // unknown sender — drop
            };
            if parsed.sender_id != sender {
                continue; // forged sender_id — drop
            }
            (
                parsed.message_id.to_vec(),
                sender,
                payload_is_ping(parsed.payload),
            )
        };
        if !is_ping {
            // Non-ping CALL from an authorized sender: queue it for the
            // Python dispatch handoff (ADR-0021 decision point 1) —
            // the loop answers only the built-in ping itself; the
            // driver drains, dispatches through the Python service
            // handlers, and enqueues the reply wires for the loop to
            // send (the reply goes to the RECORDED sender address).
            if loop_handle.pending.len() < MAX_PENDING {
                let wire = loop_handle.recv_buf[..n as usize].to_vec();
                let mut addr = [0u8; 128];
                let addr_len = (msg.msg_namelen as usize)
                    .min(std::mem::size_of::<libc::sockaddr_un>());
                let raw = &src as *const libc::sockaddr_un as *const u8;
                addr[..addr_len]
                    .copy_from_slice(std::slice::from_raw_parts(raw, addr_len));
                loop_handle.pending.push(PendingRequest {
                    message_id: call_id,
                    wire,
                    addr,
                    addr_len,
                });
            }
            continue;
        }
        let reply = loop_handle.build_ping_reply(&call_id, &sender);
        let addr_len = (msg.msg_namelen as usize).min(
            std::mem::size_of::<libc::sockaddr_un>(),
        ) as libc::socklen_t;
        let sent = libc::sendto(
            loop_handle.fd,
            reply.as_ptr() as *const c_void,
            reply.len(),
            0,
            &src as *const libc::sockaddr_un as *const libc::sockaddr,
            addr_len,
        );
        if sent < 0 {
            return -errno_or(libc::EIO);
        }
    }
    processed
}

/// Free a loop handle (does NOT close the fd — the caller owns it).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipcd_loop_free(handle: *mut c_void) {
    if !handle.is_null() {
        drop(Box::from_raw(handle as *mut IpcLoop));
    }
}

/// Extract `(pid, uid, gid)` from the recvmsg ancillary data (the
/// SCM_CREDENTIALS the kernel attached because SO_PASSCRED is set).
unsafe fn read_creds(msg: &libc::msghdr) -> (i32, i32, i32) {
    let mut pid: i32 = 0;
    let mut uid: i32 = -1;
    let mut gid: i32 = -1;
    let mut cmsg = libc::CMSG_FIRSTHDR(msg);
    while !cmsg.is_null() {
        if (*cmsg).cmsg_level == libc::SOL_SOCKET
            && (*cmsg).cmsg_type == libc::SCM_CREDENTIALS
        {
            let cred = *(libc::CMSG_DATA(cmsg) as *const libc::ucred);
            pid = cred.pid;
            uid = cred.uid as i32;
            gid = cred.gid as i32;
        }
        cmsg = libc::CMSG_NXTHDR(msg, cmsg);
    }
    (pid, uid, gid)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;
    use std::ptr;

    const TEST_RECV: usize = 64 * 1024;

    /// Build a canonical wire message (the codec layout).
    fn build_wire(
        message_type: u8,
        message_id: &[u8],
        sender: &[u8],
        receiver: &[u8],
        reply_to: &[u8],
        payload: &[u8],
        metadata: &[u8],
    ) -> Vec<u8> {
        let mut wire = Vec::new();
        wire.extend_from_slice(MAGIC);
        wire.push(WIRE_VERSION);
        wire.push(message_type);
        wire.extend_from_slice(&0.0f64.to_le_bytes());
        push_field(&mut wire, message_id);
        push_field(&mut wire, sender);
        push_field(&mut wire, receiver);
        push_field(&mut wire, reply_to);
        push_field(&mut wire, payload);
        push_field(&mut wire, b"");
        push_field(&mut wire, metadata);
        wire
    }

    fn ping_call(id: &[u8], sender: &[u8]) -> Vec<u8> {
        build_wire(MT_CALL, id, sender, b"backend", b"", b"{\"op\": \"ping\"}", b"{}")
    }

    fn bind_socket(path: &CStr) -> c_int {
        // Defensive: a stale socket from a previous (possibly failed)
        // run must not break the bind.
        unsafe {
            libc::unlink(path.as_ptr());
        }
        let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_DGRAM, 0) };
        assert!(fd >= 0, "socket failed");
        let mut sun: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        sun.sun_family = libc::AF_UNIX as libc::sa_family_t;
        for (i, b) in path.to_bytes().iter().enumerate() {
            sun.sun_path[i] = *b as c_char;
        }
        let rc = unsafe {
            libc::bind(
                fd,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
            )
        };
        assert_eq!(rc, 0, "bind failed");
        fd
    }

    // cargo test runs tests in parallel: every loop test binds its own
    // pair of sockets, so the path must be unique per test (a shared
    // name would race the bind/unlink and fail intermittently).
    static SOCK_SEQ: std::sync::atomic::AtomicUsize =
        std::sync::atomic::AtomicUsize::new(0);

    fn temp_sock(name: &str) -> CString {
        let seq = SOCK_SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        CString::new(format!(
            "{}/nyrqis-ipcd-test-{}-{}-{}.sock",
            std::env::temp_dir().display(),
            std::process::id(),
            seq,
            name
        ))
        .unwrap()
    }

    /// Build a real `sockaddr_un` for `path` with the transport's
    /// trimmed address length (family offset + path + NUL).
    fn sockaddr_of(path: &CStr) -> (libc::sockaddr_un, libc::socklen_t) {
        let mut sun: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        sun.sun_family = libc::AF_UNIX as libc::sa_family_t;
        for (i, b) in path.to_bytes().iter().enumerate() {
            sun.sun_path[i] = *b as c_char;
        }
        let addr_len = (2 + path.to_bytes().len() + 1) as libc::socklen_t;
        (sun, addr_len)
    }

    fn recv_frame(fd: c_int, buf: &mut [u8]) -> usize {
        let n = unsafe {
            libc::recv(
                fd,
                buf.as_mut_ptr() as *mut c_void,
                buf.len(),
                libc::MSG_DONTWAIT,
            )
        };
        if n < 0 {
            let e = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
            if e == libc::EAGAIN || e == libc::EWOULDBLOCK {
                return 0;
            }
            panic!("recv failed: errno {e}");
        }
        n as usize
    }

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn err_codes_are_outside_errno_range() {
        assert!(!(1..=4095).contains(&(-ERR_INTERNAL)));
        assert_eq!(ERR_INVALID_ARGS, -libc::EINVAL);
    }

    #[test]
    fn ffi_symbols_exist() {
        let _ = nyrqis_ipcd_version;
        let _ = nyrqis_ipcd_loop_new;
        let _ = nyrqis_ipcd_loop_set_policy;
        let _ = nyrqis_ipcd_loop_step;
        let _ = nyrqis_ipcd_loop_drain_requests;
        let _ = nyrqis_ipcd_loop_enqueue_replies;
        let _ = nyrqis_ipcd_loop_discard_requests;
        let _ = nyrqis_ipcd_client_call;
        let _ = nyrqis_ipcd_loop_free;
    }

    #[test]
    fn ping_detection_contract() {
        assert!(payload_is_ping(b"{\"op\": \"ping\"}"));
        assert!(payload_is_ping(b"{\"op\":\"ping\"}"));
        assert!(payload_is_ping(b"{\"service\": \"nyrqis.backend.status\", \"op\": \"ping\"}"));
        assert!(payload_is_ping(b"  {\"op\": \"ping\"}  "));
        assert!(!payload_is_ping(b"{\"op\": \"status\"}"));
        assert!(!payload_is_ping(b"{\"op\": \"health\"}"));
        assert!(!payload_is_ping(b"{\"operation\": \"ping\"}"));
        assert!(!payload_is_ping(b"{\"op\": \"Ping\"}"));
        assert!(!payload_is_ping(b"not json"));
        assert!(!payload_is_ping(b""));
        assert!(!payload_is_ping(b"{\"op\" \"ping\"}"));
    }

    #[test]
    fn dispatch_parse_contract() {
        let wire = ping_call(b"call-1", b"ctr-a");
        let parsed = parse_dispatch(&wire).expect("valid call");
        assert_eq!(parsed.message_type, MT_CALL);
        assert_eq!(parsed.message_id, b"call-1");
        assert_eq!(parsed.sender_id, b"ctr-a");
        assert_eq!(parsed.reply_to, b"");
        assert_eq!(parsed.payload, b"{\"op\": \"ping\"}");

        // Malformed inputs → None (drop at the trust boundary).
        assert!(parse_dispatch(b"").is_none());
        assert!(parse_dispatch(&wire[..wire.len() - 1]).is_none()); // truncated
        let mut bad = wire.clone();
        bad[0] ^= 0xff;
        assert!(parse_dispatch(&bad).is_none()); // bad magic
        let mut bad = wire.clone();
        bad[4] = 2;
        assert!(parse_dispatch(&bad).is_none()); // bad version
        let mut bad = wire.clone();
        bad.push(0);
        assert!(parse_dispatch(&bad).is_none()); // trailing bytes
        let mut bad = wire.clone();
        bad[5] = 9;
        assert!(parse_dispatch(&bad).is_none()); // bad type
    }

    #[test]
    fn reply_payload_matches_floor() {
        let payload = build_ping_payload(b"ctr-abc123");
        assert_eq!(
            payload,
            b"{\"container\": \"ctr-abc123\", \"echo\": \"pong\", \"ok\": true, \"service\": \"nyrqis.backend.status\", \"service_version\": \"1.0\"}"
        );
        let mut w = Vec::new();
        w.extend_from_slice(MAGIC);
        w.push(WIRE_VERSION);
        w.push(MT_REPLY);
        w.extend_from_slice(&0.0f64.to_le_bytes());
        push_field(&mut w, b"id");
        push_field(&mut w, b"");
        push_field(&mut w, b"");
        push_field(&mut w, b"call-9");
        push_field(&mut w, &payload);
        push_field(&mut w, b"");
        push_field(&mut w, b"{}");
        let parsed = parse_dispatch(&w).expect("valid reply");
        assert_eq!(parsed.message_type, MT_REPLY);
        assert_eq!(parsed.message_id, b"id");
        assert_eq!(parsed.reply_to, b"call-9");
        assert_eq!(parsed.payload, payload);
    }

    /// End-to-end loop test: a real client socket pings the loop; the
    /// loop answers with a REPLY whose reply_to matches the call.
    fn loop_fixture(policy: Policy) -> (c_int, c_int, *mut c_void, CString, CString) {
        let server_path = temp_sock("srv");
        let client_path = temp_sock("cli");
        let server_fd = bind_socket(&server_path);
        let client_fd = bind_socket(&client_path);
        // SO_PASSCRED on the server so recvmsg carries the creds.
        let one: c_int = 1;
        unsafe {
            libc::setsockopt(
                server_fd,
                libc::SOL_SOCKET,
                libc::SO_PASSCRED,
                &one as *const c_int as *const c_void,
                std::mem::size_of::<c_int>() as libc::socklen_t,
            );
        }
        let operator = CString::new(policy.operator_id.clone()).unwrap();
        let mut entries: Vec<PidEntry> = Vec::new();
        let mut containers: Vec<CString> = Vec::new();
        for (pid, container) in &policy.pids {
            let c = CString::new(container.clone()).unwrap();
            entries.push(PidEntry {
                pid: *pid,
                container: c.as_ptr(),
            });
            containers.push(c);
        }
        let uids: Vec<i32> = policy.trusted_uids.clone();
        let handle = unsafe {
            nyrqis_ipcd_loop_new(
                server_fd,
                16,
                entries.as_ptr(),
                entries.len(),
                uids.as_ptr(),
                uids.len(),
                operator.as_ptr(),
            )
        };
        assert!(!handle.is_null());
        (client_fd, server_fd, handle, server_path, client_path)
    }

    #[test]
    fn loop_answers_ping_to_registered_container() {
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-loop-test".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = ping_call(b"call-1", b"ctr-loop-test");
        let sent = unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            )
        };
        assert_eq!(sent as usize, wire.len());

        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 1, "one datagram drained");

        let mut buf = [0u8; TEST_RECV];
        let n = recv_frame(client_fd, &mut buf);
        assert!(n > 0, "client must receive the reply");
        let parsed = parse_dispatch(&buf[..n]).expect("valid reply wire");
        assert_eq!(parsed.message_type, MT_REPLY);
        assert!(parsed.message_id.starts_with(b"ipcd-"));
        assert_eq!(parsed.reply_to, b"call-1");
        assert_eq!(parsed.payload, build_ping_payload(b"ctr-loop-test"));

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_answers_trusted_uid_operator() {
        let policy = Policy {
            pids: vec![],
            trusted_uids: vec![unsafe { libc::getuid() } as i32],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = ping_call(b"call-op", b"host-operator");
        let sent = unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            )
        };
        assert_eq!(sent as usize, wire.len());

        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 1);

        let mut buf = [0u8; TEST_RECV];
        let n = recv_frame(client_fd, &mut buf);
        assert!(n > 0);
        let parsed = parse_dispatch(&buf[..n]).expect("valid reply");
        assert_eq!(parsed.payload, build_ping_payload(b"host-operator"));

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_drops_unknown_sender() {
        // Our real pid is NOT in the table and our uid is not trusted.
        let policy = Policy {
            pids: vec![(123456789, b"ctr-other".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = ping_call(b"call-x", b"ctr-unknown");
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 1, "the datagram was drained");
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no reply to an unknown sender");

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_drops_forged_sender_id() {
        // The kernel creds authenticate our pid as ctr-ok, but the wire
        // claims a different sender — the loop must drop it (the floor's
        // `_authorized` contract: the kernel is the attribution anchor).
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-ok".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = ping_call(b"call-forged", b"ctr-evil");
        let (sun, addr_len) = sockaddr_of(&server_path);
        unsafe {
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 1, "the datagram was drained");
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no reply to a forged sender");

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_queues_non_ping_op_for_dispatch() {
        // A non-ping CALL from an authorized sender is NOT answered
        // inline — it is queued for the Python dispatch handoff
        // (ADR-0021 decision point 1). Observable behavior matches the
        // floor's "no reply yet": the caller gets nothing until the
        // driver dispatches and enqueues the reply.
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-ok".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = build_wire(
            MT_CALL, b"call-status", b"ctr-ok", b"backend", b"",
            b"{\"op\": \"status\"}", b"{}",
        );
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 1, "the datagram was drained");
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no inline reply to a non-ping op");

        // The request is now pending: drain returns it as a
        // length-prefixed record.
        let mut drain = [0u8; TEST_RECV];
        let n = unsafe { nyrqis_ipcd_loop_drain_requests(handle, drain.as_mut_ptr(), drain.len()) };
        assert!(n > 0, "the non-ping request must be drained");
        let rec_len = u32::from_le_bytes(drain[0..4].try_into().unwrap()) as usize;
        assert_eq!(rec_len, wire.len());
        assert_eq!(&drain[4..4 + rec_len], &wire[..]);

        // Enqueue a reply (built by the test, mirroring what the Python
        // service's codec would produce): the loop routes it to the
        // RECORDED sender address.
        let reply_payload = b"{\"ok\": true, \"container\": \"ctr-ok\"}";
        let reply_wire = build_wire(
            MT_REPLY, b"reply-1", b"", b"", b"call-status",
            reply_payload, b"{}",
        );
        let entry = ReplyWire {
            wire: reply_wire.as_ptr(),
            wire_len: reply_wire.len(),
        };
        let rc = unsafe { nyrqis_ipcd_loop_enqueue_replies(handle, &entry, 1) };
        assert_eq!(rc, 0);
        let n = recv_frame(client_fd, &mut buf);
        assert!(n > 0, "the reply must reach the caller");
        let parsed = parse_dispatch(&buf[..n]).expect("valid reply");
        assert_eq!(parsed.message_type, MT_REPLY);
        assert_eq!(parsed.reply_to, b"call-status");
        assert_eq!(parsed.payload, reply_payload);

        // The queue is now empty (the reply consumed the request).
        assert_eq!(
            unsafe { nyrqis_ipcd_loop_drain_requests(handle, drain.as_mut_ptr(), drain.len()) },
            0,
            "the answered request must leave the queue"
        );

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn enqueue_unknown_reply_to_is_skipped() {
        // A reply whose reply_to matches no queued request is skipped
        // (defensive — the floor can only reply to a call it was given)
        // and the queue is untouched.
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-ok".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let reply_wire = build_wire(
            MT_REPLY, b"reply-x", b"", b"", b"never-requested",
            b"{}", b"{}",
        );
        let entry = ReplyWire {
            wire: reply_wire.as_ptr(),
            wire_len: reply_wire.len(),
        };
        let rc = unsafe { nyrqis_ipcd_loop_enqueue_replies(handle, &entry, 1) };
        assert_eq!(rc, 0);
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "nothing must be sent");

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn discard_reaps_unanswered_pending() {
        // The driver calls discard after a drained batch to reap
        // requests the handlers chose not to answer (the floor's
        // no-reply semantics, bounded on the loop side).
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-ok".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = build_wire(
            MT_CALL, b"call-1", b"ctr-ok", b"backend", b"",
            b"{\"op\": \"health\"}", b"{}",
        );
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        assert_eq!(unsafe { nyrqis_ipcd_loop_step(handle, 2000) }, 1);
        assert_eq!(
            unsafe { nyrqis_ipcd_loop_discard_requests(handle) },
            0,
            "discard succeeds"
        );
        let mut drain = [0u8; TEST_RECV];
        assert_eq!(
            unsafe { nyrqis_ipcd_loop_drain_requests(handle, drain.as_mut_ptr(), drain.len()) },
            0,
            "the queue is empty after discard"
        );
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no reply was ever sent");

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn drain_enobufs_when_buffer_too_small() {
        // -ENOBUFS when even the first record cannot fit — the driver
        // grows the buffer.
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-ok".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        let wire = build_wire(
            MT_CALL, b"call-big", b"ctr-ok", b"backend", b"",
            b"{\"op\": \"status\"}", b"{}",
        );
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        assert_eq!(unsafe { nyrqis_ipcd_loop_step(handle, 2000) }, 1);
        let mut tiny = [0u8; 4]; // only the length prefix fits, not the record
        let rc = unsafe { nyrqis_ipcd_loop_drain_requests(handle, tiny.as_mut_ptr(), tiny.len()) };
        assert_eq!(rc, -libc::ENOBUFS);
        let mut buf = [0u8; TEST_RECV];
        let n = unsafe { nyrqis_ipcd_loop_drain_requests(handle, buf.as_mut_ptr(), buf.len()) };
        assert!(n > 0, "a bigger buffer drains the record");

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_batches_multiple_pings() {
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-batch".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        for i in 0..5 {
            let wire = ping_call(format!("call-{i}").as_bytes(), b"ctr-batch");
            let (sun, addr_len) = sockaddr_of(&server_path);
            unsafe {
                libc::sendto(
                    client_fd,
                    wire.as_ptr() as *const c_void,
                    wire.len(),
                    0,
                    &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                    addr_len,
                );
            }
        }
        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 2000) };
        assert_eq!(rc, 5, "one step drains the whole batch");
        let mut buf = [0u8; TEST_RECV];
        let mut replies = 0;
        while recv_frame(client_fd, &mut buf) > 0 {
            replies += 1;
        }
        assert_eq!(replies, 5);

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn loop_times_out_cleanly() {
        let policy = Policy {
            pids: vec![],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);
        let rc = unsafe { nyrqis_ipcd_loop_step(handle, 20) };
        assert_eq!(rc, 0, "clean timeout, nothing drained");
        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn set_policy_refreshes_pid_table() {
        // A loop created with an empty table must start answering for a
        // container only after ``nyrqis_ipcd_loop_set_policy`` pushes the
        // refreshed table (the per-container pid-table refresh).
        let policy = Policy {
            pids: vec![],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);

        // Before the refresh: our pid is unknown → the ping is dropped.
        let wire = ping_call(b"call-pre", b"ctr-fresh");
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        assert_eq!(unsafe { nyrqis_ipcd_loop_step(handle, 2000) }, 1);
        let mut buf = [0u8; TEST_RECV];
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no reply before the refresh");

        // Push the refreshed policy (our real pid → ctr-fresh).
        let entry = PidEntry {
            pid: unsafe { libc::getpid() },
            container: b"ctr-fresh\0".as_ptr() as *const c_char,
        };
        let rc = unsafe {
            nyrqis_ipcd_loop_set_policy(
                handle,
                &entry,
                1,
                ptr::null(),
                0,
                b"host-operator\0".as_ptr() as *const c_char,
            )
        };
        assert_eq!(rc, 0);

        // After the refresh: the same ping is answered with ctr-fresh.
        let wire = ping_call(b"call-post", b"ctr-fresh");
        unsafe {
            let (sun, addr_len) = sockaddr_of(&server_path);
            libc::sendto(
                client_fd,
                wire.as_ptr() as *const c_void,
                wire.len(),
                0,
                &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                addr_len,
            );
        }
        assert_eq!(unsafe { nyrqis_ipcd_loop_step(handle, 2000) }, 1);
        let n = recv_frame(client_fd, &mut buf);
        assert!(n > 0, "reply after the refresh");
        let parsed = parse_dispatch(&buf[..n]).expect("valid reply");
        assert_eq!(parsed.reply_to, b"call-post");
        assert_eq!(parsed.payload, build_ping_payload(b"ctr-fresh"));

        // Invalid refresh args → ERR_INVALID_ARGS, policy unchanged.
        assert_eq!(
            unsafe {
                nyrqis_ipcd_loop_set_policy(
                    ptr::null_mut(),
                    &entry,
                    1,
                    ptr::null(),
                    0,
                    b"host-operator\0".as_ptr() as *const c_char,
                )
            },
            ERR_INVALID_ARGS
        );
        assert_eq!(
            unsafe {
                nyrqis_ipcd_loop_set_policy(
                    handle,
                    &entry,
                    1,
                    ptr::null(),
                    0,
                    ptr::null(),
                )
            },
            ERR_INVALID_ARGS
        );

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn client_call_roundtrip_against_serving_loop() {
        // Both halves in Rust, end-to-end: the serving loop answers a
        // ping from our pid; the client half does the whole round trip
        // in one FFI call (sendto + poll + recvmsg + correlation).
        let policy = Policy {
            pids: vec![(unsafe { libc::getpid() }, b"ctr-cli".to_vec())],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);
        let wire = ping_call(b"call-cli", b"ctr-cli");
        // A raw pointer is not Send — hand the handle across as usize
        // and cast back inside the stepping thread.
        let srv_handle = handle as usize;
        let t = std::thread::spawn(move || {
            // The ping is answered on the first step; a few more steps
            // cover scheduling jitter, then the thread exits (the loop
            // polls 50 ms with nothing left — keep it short).
            for _ in 0..10 {
                unsafe { nyrqis_ipcd_loop_step(srv_handle as *mut c_void, 50) };
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
        });
        let mut reply = [0u8; TEST_RECV];
        let n = unsafe {
            nyrqis_ipcd_client_call(
                client_fd,
                server_path.as_ptr(),
                wire.as_ptr(),
                wire.len(),
                reply.as_mut_ptr(),
                reply.len(),
                2000,
            )
        };
        t.join().unwrap();
        assert!(n > 0, "the round trip must complete (got {n})");
        let parsed = parse_dispatch(&reply[..n as usize]).expect("valid reply");
        assert_eq!(parsed.message_type, MT_REPLY);
        assert_eq!(parsed.reply_to, b"call-cli");
        assert_eq!(parsed.payload, build_ping_payload(b"ctr-cli"));

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn client_call_times_out_when_no_reply() {
        // A server that never answers (our pid unknown → the ping is
        // dropped) → -ETIMEDOUT, bounded by the timeout.
        let policy = Policy {
            pids: vec![],
            trusted_uids: vec![],
            operator_id: b"host-operator".to_vec(),
        };
        let (client_fd, server_fd, handle, server_path, client_path) = loop_fixture(policy);
        let wire = ping_call(b"call-t", b"ctr-ghost");
        let mut reply = [0u8; TEST_RECV];
        let n = unsafe {
            nyrqis_ipcd_client_call(
                client_fd,
                server_path.as_ptr(),
                wire.as_ptr(),
                wire.len(),
                reply.as_mut_ptr(),
                reply.len(),
                60,
            )
        };
        assert_eq!(n, -libc::ETIMEDOUT);

        unsafe {
            nyrqis_ipcd_loop_free(handle);
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn client_call_correlates_amid_noise() {
        // The client's receive queue holds junk, a reply for a
        // DIFFERENT call, then the matching reply — the client half
        // must drop the noise and return the correlated reply,
        // exactly the floor's loop.
        let server_path = temp_sock("csrv");
        let client_path = temp_sock("ccli");
        let server_fd = bind_socket(&server_path);
        let client_fd = bind_socket(&client_path);
        let call_wire = build_wire(
            MT_CALL, b"call-42", b"ctr-cli", b"backend", b"",
            b"{\"op\": \"ping\"}", b"{}",
        );
        let junk = b"garbage-not-a-wire";
        let other = build_wire(
            MT_REPLY, b"r-1", b"", b"", b"other-call", b"{}", b"{}",
        );
        let matching = build_wire(
            MT_REPLY, b"r-2", b"", b"", b"call-42",
            b"{\"ok\": true}", b"{}",
        );
        for payload in [&junk[..], &other[..], &matching[..]] {
            let (sun, addr_len) = sockaddr_of(&client_path);
            unsafe {
                libc::sendto(
                    client_fd,
                    payload.as_ptr() as *const c_void,
                    payload.len(),
                    0,
                    &sun as *const libc::sockaddr_un as *const libc::sockaddr,
                    addr_len,
                );
            }
        }
        let mut reply = [0u8; TEST_RECV];
        let n = unsafe {
            nyrqis_ipcd_client_call(
                client_fd,
                server_path.as_ptr(),
                call_wire.as_ptr(),
                call_wire.len(),
                reply.as_mut_ptr(),
                reply.len(),
                2000,
            )
        };
        assert!(n > 0, "the correlated reply must be returned");
        let parsed = parse_dispatch(&reply[..n as usize]).expect("valid reply");
        assert_eq!(parsed.reply_to, b"call-42");
        assert_eq!(parsed.payload, b"{\"ok\": true}");

        unsafe {
            libc::close(client_fd);
            libc::close(server_fd);
            libc::unlink(server_path.as_ptr());
            libc::unlink(client_path.as_ptr());
        }
    }

    #[test]
    fn client_call_invalid_args() {
        let p = b"/tmp/nyrqis-client-test.sock\0".as_ptr() as *const c_char;
        let empty = ptr::null();
        let null_mut = ptr::null_mut();
        assert_eq!(
            unsafe { nyrqis_ipcd_client_call(-1, p, empty, 0, null_mut, 0, 10) },
            ERR_INVALID_ARGS
        );
        assert_eq!(
            unsafe { nyrqis_ipcd_client_call(3, ptr::null(), empty, 0, null_mut, 0, 10) },
            ERR_INVALID_ARGS
        );
        assert_eq!(
            unsafe { nyrqis_ipcd_client_call(3, p, empty, 0, null_mut, 0, -1) },
            ERR_INVALID_ARGS
        );
        // An empty wire has no message_id to correlate → invalid.
        assert_eq!(
            unsafe { nyrqis_ipcd_client_call(3, p, empty, 0, null_mut, 0, 10) },
            ERR_INVALID_ARGS
        );
    }

    #[test]
    fn invalid_loop_args() {
        assert!(unsafe { nyrqis_ipcd_loop_new(-1, 4, ptr::null(), 0, ptr::null(), 0, b"op\0".as_ptr() as *const c_char) }
            .is_null());
        assert!(unsafe { nyrqis_ipcd_loop_new(3, 0, ptr::null(), 0, ptr::null(), 0, b"op\0".as_ptr() as *const c_char) }
            .is_null());
        assert!(unsafe { nyrqis_ipcd_loop_new(3, 4, ptr::null(), 0, ptr::null(), 0, ptr::null()) }
            .is_null());
        assert_eq!(unsafe { nyrqis_ipcd_loop_step(ptr::null_mut(), 10) }, ERR_INVALID_ARGS);
        unsafe { nyrqis_ipcd_loop_free(ptr::null_mut()) }; // no-op is safe
    }
}
