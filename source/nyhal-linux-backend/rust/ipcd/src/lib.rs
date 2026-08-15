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
//! - Any other datagram (non-CALL, non-ping CALL, malformed wire,
//!   unknown sender) is dropped at the trust boundary, mirroring the
//!   floor's serve-loop behavior (drop, never raise, never crash).
//!   The non-ping dispatch handoff (Python service handlers across the
//!   boundary) is the NEXT increment, per ADR-0021 decision point 1.
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
/// creation — the next increment refreshes it per batch).
struct Policy {
    pids: Vec<(i32, Vec<u8>)>,
    trusted_uids: Vec<i32>,
    operator_id: Vec<u8>,
}

/// The serving-loop handle (opaque to the caller).
struct IpcLoop {
    fd: c_int,
    batch_max: u32,
    policy: Policy,
    recv_buf: Vec<u8>,
    ctrl: AlignedCmsg,
    seq: u64,
}

/// CMSG buffer with the alignment cmsghdr requires.
#[repr(C, align(16))]
struct AlignedCmsg([u8; CMSG_BUF]);

impl IpcLoop {
    fn resolve_sender(&self, pid: i32, uid: i32) -> Option<&[u8]> {
        for (p, container) in &self.policy.pids {
            if *p == pid {
                return Some(container);
            }
        }
        if self.policy.trusted_uids.contains(&uid) {
            return Some(&self.policy.operator_id);
        }
        None
    }

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
        policy,
        recv_buf: vec![0u8; RECV_BUF],
        ctrl: AlignedCmsg([0u8; CMSG_BUF]),
        seq: 0,
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
                Some(s) => s.to_vec(),
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
            continue; // non-ping ops are the next increment's handoff
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
        let _ = nyrqis_ipcd_loop_step;
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
    fn loop_drops_non_ping_op() {
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
        assert_eq!(recv_frame(client_fd, &mut buf), 0, "no reply to a non-ping op");

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
