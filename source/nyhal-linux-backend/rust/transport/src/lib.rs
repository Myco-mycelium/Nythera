//! Nyrqis IPC Unix-domain datagram transport hot path — ADR-0020
//! migration priority #6.
//!
//! **Implemented 2026-08-14.** The send/receive half of the cross-process
//! IPC transport (`ipc/transport.py`): moving wire frames and
//! kernel-attached identity across a process boundary. The wire bytes
//! themselves are opaque here — the codec crate (migration #4,
//! `rust/ipc`) already owns framing and parsing (the trust boundary);
//! this crate owns the syscall path: one `sendto` per outbound frame,
//! one `poll`+`recvmsg` per inbound frame, and the `SCM_CREDENTIALS`
//! ancillary-data parse that yields the sender's real `(pid, uid, gid)`
//! (the transport's honest trust anchor — the receiver sets
//! `SO_PASSCRED` at bind on the Python side and the kernel attaches the
//! sender's GLOBAL pid and real uid/gid to every inbound datagram,
//! verified on this host 2026-08-14: a sender in a new pid+user
//! namespace presents its host pid, not its namespace-local one).
//!
//! Why the hot path is here: the reference (Python) transport measured
//! p50 188.79 µs per CALL/REPLY over the wire (BENCHMARK_RESULTS.md
//! §20) vs 87.28 µs in-process — the gap is the two process hops of
//! Python per-message overhead around the syscalls. This crate removes
//! the Python recvmsg/CMSG parse and sendto framing from the measured
//! path; the benchmark is re-run with the crate active to quantify the
//! delta on the NPS-003 §6.1 (<100 µs) close path.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state, no pointers into
//! Python objects. Output buffers are `libc::malloc`'d here and freed
//! by the caller through `nyrqis_transport_free` (the same ownership
//! contract as the seccomp/nyfs/ipc crates).
//!
//! Return convention: **0 on success or a negative value** — `-errno`
//! for real failures, `ERR_INTERNAL` (-4096) for module failures. The
//! loader maps the codes exactly as the other migration loaders do.
//!
//! Sockets are NOT bound here: path management (0700 perms, unlink on
//! close, `SO_PASSCRED`) stays on the Python floor, which passes the
//! bound socket's fd in. `nyrqis_transport_recv` never blocks the
//! caller: it `poll`s with the requested timeout and `recvmsg`s with
//! `MSG_DONTWAIT`, so it is safe on both blocking and non-blocking
//! fds and cannot race into an indefinite hang.

use std::ffi::{c_char, c_int, c_void, CStr};
use std::os::raw::c_uchar;
use std::ptr;

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns). ERR_INTERNAL is OUTSIDE the
// errno range (1..=4095) by contract: -errno maps to OSError on the
// Python side, so an in-range code would be misreported.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL — null/absent pointers, bad fd
pub const ERR_TOO_LARGE: i32 = -27; // EFBIG — beyond the sanity bound
pub const ERR_INTERNAL: i32 = -4096;

/// Wire-frame sanity bound (16 MiB, mirroring the codec crate).
const MAX_WIRE_BYTES: usize = 16 * 1024 * 1024;
/// `sun_path` is 108 bytes; we need at least one byte for the NUL.
const MAX_SUN_PATH: usize = 107;
/// Offset of `sun_path` in `sockaddr_un` (2 on Linux: family then path,
/// no padding).
const SUN_PATH_OFFSET: usize = std::mem::size_of::<libc::sa_family_t>();
/// Inbound frame cap (the Python floor's recv buffer size).
const RECV_BUF: usize = 64 * 1024;
/// Ancillary buffer: room for one `ucred` (CMSG_SPACE(12) ≈ 32) with
/// headroom, aligned for `cmsghdr`.
const CMSG_BUF: usize = 64;

fn errno_or(e: i32) -> i32 {
    std::io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(e)
}

/// Fill a zeroed `sockaddr_un` with `path` (must be 1..=MAX_SUN_PATH
/// bytes; the trailing NUL is written at `path.len()`).
fn pack_sun_path(path: &[u8]) -> Result<libc::sockaddr_un, i32> {
    if path.is_empty() || path.len() > MAX_SUN_PATH {
        return Err(ERR_INVALID_ARGS);
    }
    let mut sun: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    sun.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (i, b) in path.iter().enumerate() {
        sun.sun_path[i] = *b as c_char;
    }
    Ok(sun)
}

/// Malloc a copy of `src` into `*out` and set `*out_len`. Zero-length
/// `src` yields a null pointer and length 0.
fn publish_bytes(
    src: &[u8],
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if src.is_empty() {
        unsafe {
            *out_ptr = ptr::null_mut();
            *out_len = 0;
        }
        return 0;
    }
    let buf = unsafe { libc::malloc(src.len()) as *mut c_uchar };
    if buf.is_null() {
        return -libc::ENOMEM;
    }
    unsafe {
        ptr::copy_nonoverlapping(src.as_ptr(), buf, src.len());
        *out_ptr = buf;
        *out_len = src.len();
    }
    0
}

/// Malloc a NUL-terminated C string copy of `src` (always at least the
/// empty string) into `*out`.
fn publish_cstr(src: &[u8], out_ptr: *mut *mut c_char) -> i32 {
    let len = src.len() + 1;
    let buf = unsafe { libc::malloc(len) as *mut c_char };
    if buf.is_null() {
        return -libc::ENOMEM;
    }
    unsafe {
        ptr::copy_nonoverlapping(src.as_ptr() as *const c_char, buf, src.len());
        *buf.add(src.len()) = 0;
        *out_ptr = buf;
    }
    0
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_transport_version() -> u32 {
    ABI_VERSION
}

/// Send one wire frame as a datagram to `peer_path` on the bound socket
/// `fd`. `peer_path` is a NUL-terminated Unix socket pathname. The
/// caller attaches NO credentials — the receiving side's `SO_PASSCRED`
/// makes the kernel attach the sender's real identity. Returns 0,
/// -errno, or `ERR_INVALID_ARGS`/`ERR_TOO_LARGE`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_transport_send(
    fd: c_int,
    wire: *const c_uchar,
    wire_len: usize,
    peer_path: *const c_char,
) -> i32 {
    if fd < 0 || wire.is_null() || peer_path.is_null() {
        return ERR_INVALID_ARGS;
    }
    if wire_len > MAX_WIRE_BYTES {
        return ERR_TOO_LARGE;
    }
    let data = std::slice::from_raw_parts(wire, wire_len);
    let path = match CStr::from_ptr(peer_path).to_bytes() {
        p if p.is_empty() || p.len() > MAX_SUN_PATH => return ERR_INVALID_ARGS,
        p => p,
    };
    let sun = match pack_sun_path(path) {
        Ok(s) => s,
        Err(e) => return e,
    };
    let addr_len = (SUN_PATH_OFFSET + path.len() + 1) as libc::socklen_t;
    let sent = libc::sendto(
        fd,
        data.as_ptr() as *const c_void,
        data.len(),
        0,
        &sun as *const libc::sockaddr_un as *const libc::sockaddr,
        addr_len,
    );
    if sent < 0 {
        return -errno_or(libc::EIO);
    }
    if sent as usize != data.len() {
        return -libc::EPIPE; // partial datagram: impossible for UDS, defensive
    }
    0
}

/// Receive one wire frame from the bound socket `fd` with `timeout_ms`
/// (negative = block until data). Never blocks past the timeout (poll
/// first, `MSG_DONTWAIT` recvmsg). On success writes the malloc'd frame
/// bytes (freed via `nyrqis_transport_free`), the kernel-attached
/// sender `(pid, uid, gid)`, and the sender's bound path (malloc'd C
/// string, always non-null on success). No data within the timeout
/// returns 0 with `*out_wire_len = 0`. Returns 0, -errno, or
/// `ERR_INVALID_ARGS`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_transport_recv(
    fd: c_int,
    timeout_ms: i64,
    out_wire: *mut *mut c_uchar,
    out_wire_len: *mut usize,
    out_pid: *mut i32,
    out_uid: *mut i32,
    out_gid: *mut i32,
    out_sender_path: *mut *mut c_char,
) -> i32 {
    if fd < 0
        || out_wire.is_null()
        || out_wire_len.is_null()
        || out_pid.is_null()
        || out_uid.is_null()
        || out_gid.is_null()
        || out_sender_path.is_null()
    {
        return ERR_INVALID_ARGS;
    }
    *out_wire = ptr::null_mut();
    *out_wire_len = 0;
    *out_pid = 0;
    *out_uid = -1;
    *out_gid = -1;
    *out_sender_path = ptr::null_mut();

    let ms: c_int = if timeout_ms < 0 {
        -1
    } else {
        timeout_ms.min(c_int::MAX as i64) as c_int
    };
    let mut pfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    let prc = libc::poll(&mut pfd, 1, ms);
    if prc < 0 {
        return -errno_or(libc::EIO);
    }
    if prc == 0 {
        return 0; // timeout — no data
    }

    // recvmsg into an aligned control buffer (the kernel writes
    // cmsghdr, which needs 8-byte alignment) and a captured source
    // address.
    #[repr(C, align(16))]
    struct Aligned([u8; CMSG_BUF]);
    let mut ctrl = Aligned([0u8; CMSG_BUF]);
    let mut data_buf = [0u8; RECV_BUF];
    let mut src: libc::sockaddr_un = std::mem::zeroed();
    let mut iov = libc::iovec {
        iov_base: data_buf.as_mut_ptr() as *mut c_void,
        iov_len: data_buf.len(),
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
        if e == libc::EAGAIN || e == libc::EWOULDBLOCK {
            return 0; // another reader stole the datagram — a timeout
        }
        return -e;
    }
    if n == 0 {
        return 0; // no data (defensive — UDS datagrams cannot EOF)
    }
    let n = n as usize;

    // Kernel-attached credentials from the SCM_CREDENTIALS ancillary.
    let mut pid: i32 = 0;
    let mut uid: i32 = -1;
    let mut gid: i32 = -1;
    let mut cmsg = libc::CMSG_FIRSTHDR(&msg);
    while !cmsg.is_null() {
        if (*cmsg).cmsg_level == libc::SOL_SOCKET
            && (*cmsg).cmsg_type == libc::SCM_CREDENTIALS
        {
            let cred = *(libc::CMSG_DATA(cmsg) as *const libc::ucred);
            pid = cred.pid;
            uid = cred.uid as i32;
            gid = cred.gid as i32;
        }
        cmsg = libc::CMSG_NXTHDR(&msg, cmsg);
    }

    // Sender's bound path from the captured source address. Note:
    // `sun_path` is `[c_char]` (i8 on Linux) — cast each byte to u8
    // (the loader returns the path as UTF-8 bytes).
    let mut sender_path: Vec<u8> = Vec::new();
    if msg.msg_namelen > SUN_PATH_OFFSET as libc::socklen_t {
        let avail = (msg.msg_namelen - SUN_PATH_OFFSET as libc::socklen_t) as usize;
        let path_bytes = &src.sun_path[..avail.min(MAX_SUN_PATH + 1)];
        let mut end = path_bytes.len();
        while end > 0 && path_bytes[end - 1] == 0 {
            end -= 1;
        }
        sender_path.extend(path_bytes[..end].iter().map(|&b| b as u8));
    }

    let pub_rc = publish_bytes(&data_buf[..n], out_wire, out_wire_len);
    if pub_rc != 0 {
        return pub_rc; // -ENOMEM from publish_bytes itself
    }
    let pub_rc = publish_cstr(&sender_path, out_sender_path);
    if pub_rc != 0 {
        if !(*out_wire).is_null() {
            libc::free(*out_wire as *mut c_void);
            *out_wire = ptr::null_mut();
            *out_wire_len = 0;
        }
        return pub_rc;
    }
    *out_pid = pid;
    *out_uid = uid;
    *out_gid = gid;
    0
}

/// Free an output buffer previously returned by this module.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_transport_free(ptr: *mut c_void) {
    if !ptr.is_null() {
        libc::free(ptr);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;

    fn bind_socket(path: &CStr) -> c_int {
        let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_DGRAM, 0) };
        assert!(fd >= 0, "socket failed");
        let sun = pack_sun_path(path.to_bytes()).expect("valid path");
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

    fn enable_passcred(fd: c_int) {
        let one: c_int = 1;
        let rc = unsafe {
            libc::setsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_PASSCRED,
                &one as *const c_int as *const c_void,
                std::mem::size_of::<c_int>() as libc::socklen_t,
            )
        };
        assert_eq!(rc, 0, "SO_PASSCRED failed");
    }

    fn temp_sock(name: &str) -> CString {
        CString::new(format!(
            "{}/nyrqis-transport-test-{}-{}.sock",
            std::env::temp_dir().display(),
            std::process::id(),
            name
        ))
        .unwrap()
    }

    #[test]
    fn pack_path_bounds() {
        assert!(pack_sun_path(b"").is_err());
        assert!(pack_sun_path(&[b'a'; 108]).is_err());
        let sun = pack_sun_path(b"/tmp/x.sock").unwrap();
        assert_eq!(sun.sun_family, libc::AF_UNIX as libc::sa_family_t);
        let got = unsafe {
            let mut end = 0usize;
            while end < MAX_SUN_PATH && sun.sun_path[end] != 0 {
                end += 1;
            }
            std::slice::from_raw_parts(sun.sun_path.as_ptr() as *const u8, end)
        };
        assert_eq!(got, b"/tmp/x.sock");
    }

    #[test]
    fn invalid_args() {
        let rc = unsafe { nyrqis_transport_send(-1, ptr::null(), 0, b"x\0".as_ptr() as *const c_char) };
        assert_eq!(rc, ERR_INVALID_ARGS);
        let rc = unsafe {
            nyrqis_transport_recv(
                -1,
                10,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
            )
        };
        assert_eq!(rc, ERR_INVALID_ARGS);
    }

    #[test]
    fn roundtrip_carries_frame_creds_and_sender_path() {
        let recv_path = temp_sock("recv");
        let send_path = temp_sock("send");
        let recv_fd = bind_socket(&recv_path);
        let send_fd = bind_socket(&send_path);
        enable_passcred(recv_fd);

        let wire = b"NYRQ\x01\x02test-frame-bytes";
        let rc = unsafe {
            nyrqis_transport_send(
                send_fd,
                wire.as_ptr(),
                wire.len(),
                recv_path.as_ptr(),
            )
        };
        assert_eq!(rc, 0, "send failed");

        let mut out_wire: *mut c_uchar = ptr::null_mut();
        let mut out_len: usize = 0;
        let mut pid: i32 = 0;
        let mut uid: i32 = 0;
        let mut gid: i32 = 0;
        let mut out_path: *mut c_char = ptr::null_mut();
        let rc = unsafe {
            nyrqis_transport_recv(
                recv_fd,
                2000,
                &mut out_wire,
                &mut out_len,
                &mut pid,
                &mut uid,
                &mut gid,
                &mut out_path,
            )
        };
        assert_eq!(rc, 0, "recv failed");
        assert_eq!(out_len, wire.len());
        assert_eq!(unsafe { std::slice::from_raw_parts(out_wire, out_len) }, wire);
        assert_eq!(pid, unsafe { libc::getpid() });
        assert_eq!(uid, unsafe { libc::getuid() } as i32);
        assert_eq!(gid, unsafe { libc::getgid() } as i32);
        let got_path = unsafe { CStr::from_ptr(out_path) }.to_bytes().to_vec();
        assert_eq!(got_path, send_path.to_bytes());

        unsafe {
            nyrqis_transport_free(out_wire as *mut c_void);
            nyrqis_transport_free(out_path as *mut c_void);
            libc::close(recv_fd);
            libc::close(send_fd);
            libc::unlink(recv_path.as_ptr());
            libc::unlink(send_path.as_ptr());
        }
    }

    #[test]
    fn recv_times_out_with_no_data() {
        let recv_path = temp_sock("timeout");
        let recv_fd = bind_socket(&recv_path);
        enable_passcred(recv_fd);

        let mut out_wire: *mut c_uchar = ptr::null_mut();
        let mut out_len: usize = 1;
        let mut pid: i32 = 0;
        let mut uid: i32 = 0;
        let mut gid: i32 = 0;
        let mut out_path: *mut c_char = ptr::null_mut();
        let rc = unsafe {
            nyrqis_transport_recv(
                recv_fd,
                20,
                &mut out_wire,
                &mut out_len,
                &mut pid,
                &mut uid,
                &mut gid,
                &mut out_path,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(out_len, 0);
        assert!(out_wire.is_null());

        unsafe {
            libc::close(recv_fd);
            libc::unlink(recv_path.as_ptr());
        }
    }
}
