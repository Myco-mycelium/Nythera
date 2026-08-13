//! Nyrqis IPC message wire codec — ADR-0020 migration priority #4.
//!
//! **Implemented 2026-08-13.** The binary framing for `IPCMessage`
//! (NPS-003 §3 / NPS-017 §4.3): the message serialization a real
//! cross-process transport will sit on. The Python IPC semantics are
//! stable and benchmarked (`tests/BENCHMARK_RESULTS.md` §1: 88/124/215
//! µs p50/p95/p99 round-trip in-process); this module extracts the
//! transport's serialization boundary into a memory-safe Rust parser —
//! the parsing trust boundary of future cross-container IPC.
//!
//! The wire format is a hand-rolled binary framing (fixed-length header
//! + length-prefixed fields), with `libc` as the only dependency:
//!
//! ```text
//! 0   4  magic "NYRQ"
//! 4   1  wire version (1)
//! 5   1  message_type (0 send, 1 receive, 2 call, 3 reply, 4 notify)
//! 6   8  timestamp (f64, little-endian)
//! 14  4  message_id_len (u32 LE) + bytes
//!     4  sender_id_len + bytes
//!     4  receiver_id_len + bytes
//!     4  reply_to_len + bytes       (0 = absent)
//!     4  payload_len + bytes
//!     4  caps_flat_len + bytes      ([u32 cap_len + cap bytes]*)
//!     4  metadata_len + bytes       (opaque — JSON blob, caller-owned)
//! ```
//!
//! The format is canonical: the pure-Python floor
//! (`ipc/ipc_codec.py`) must produce **byte-identical** output, which
//! the differential conformance gate verifies. `metadata` is opaque on
//! the wire (the Python side serializes it with `json.dumps`), so no
//! JSON dependency or dict-ordering contract crosses the boundary.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state, no pointers into
//! Python objects. Output buffers are `libc::malloc`'d by the crate and
//! freed by the caller through `nyrqis_ipc_free` (the same ownership
//! contract as `nyrqis_seccomp`/`nyrqis_nyfs`).
//!
//! Return convention: **0 on success or a negative value** — `-errno`
//! for real failures, `ERR_INVALID_WIRE` (-4097) for a malformed or
//! oversized message (outside the errno range by contract; the loader
//! maps it to the same `ValueError` the pure-Python parser raises).

use std::os::raw::{c_uchar, c_void};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns). ERR_INTERNAL and
// ERR_INVALID_WIRE are OUTSIDE the errno range (1..=4095) by contract:
// -errno maps to OSError on the Python side, so in-range codes would be
// misreported.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL — null/absent pointers
pub const ERR_TOO_LARGE: i32 = -27; // EFBIG — beyond the sanity bound
pub const ERR_INTERNAL: i32 = -4096;
pub const ERR_INVALID_WIRE: i32 = -4097; // malformed/oversized message (→ ValueError)

/// Total wire-message sanity bound (16 MiB). Payloads are small in the
/// reference transport; the bound is defense-in-depth against a
/// corrupt/hostile buffer claiming huge lengths.
const MAX_WIRE_BYTES: usize = 16 * 1024 * 1024;
/// Per-field length bound (1 MiB) for the string fields.
const MAX_FIELD_BYTES: usize = 1 * 1024 * 1024;
/// Maximum number of capability entries in the flat buffer.
const MAX_CAPS: u32 = 4096;

const WIRE_VERSION: u8 = 1;
const MAGIC: &[u8; 4] = b"NYRQ";

// Early-return helpers for the two error styles the FFI entry points
// use. The `?` operator cannot be used here: these functions return
// i32 (the error code itself), not Result/Option.
macro_rules! unwrap_err {
    ($e:expr) => {
        match $e {
            Ok(v) => v,
            Err(e) => return e,
        }
    };
}
macro_rules! unwrap_wire {
    ($e:expr) => {
        match $e {
            Some(v) => v,
            None => return ERR_INVALID_WIRE,
        }
    };
}

/// The decoded message view (repr(C) — mirrors the Python ctypes
/// Structure). All pointers reference one malloc'd block: the struct
/// followed by the field bytes; `nyrqis_ipc_free` frees the whole block.
#[repr(C)]
pub struct IpcMessageView {
    message_type: u8,
    timestamp: f64,
    message_id: *const u8,
    message_id_len: u32,
    sender_id: *const u8,
    sender_id_len: u32,
    receiver_id: *const u8,
    receiver_id_len: u32,
    reply_to: *const u8,
    reply_to_len: u32,
    payload: *const u8,
    payload_len: u32,
    caps_flat: *const u8,
    caps_flat_len: u32,
    metadata: *const u8,
    metadata_len: u32,
}

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_ipc_version() -> u32 {
    ABI_VERSION
}

/// Encode a message to the canonical wire format. All pointer args are
/// caller-owned byte buffers of the given length (0-length fields may
/// pass a non-null buffer of any content; null is accepted only with
/// length 0). `caps_flat` is the pre-framed capabilities blob
/// (`[u32 cap_len + cap bytes]*`). The output buffer is `libc::malloc`'d
/// here and freed by the caller via `nyrqis_ipc_free`. Returns 0,
/// -errno, or `ERR_INVALID_WIRE`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipc_encode(
    message_type: u8,
    timestamp: f64,
    message_id: *const c_uchar,
    message_id_len: u32,
    sender_id: *const c_uchar,
    sender_id_len: u32,
    receiver_id: *const c_uchar,
    receiver_id_len: u32,
    reply_to: *const c_uchar,
    reply_to_len: u32,
    payload: *const c_uchar,
    payload_len: u32,
    caps_flat: *const c_uchar,
    caps_flat_len: u32,
    metadata: *const c_uchar,
    metadata_len: u32,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if out_ptr.is_null() || out_len.is_null() {
        return ERR_INVALID_ARGS;
    }
    if message_type > 4 {
        return ERR_INVALID_ARGS;
    }
    let message_id_slice = unwrap_err!(slice_field(message_id, message_id_len));
    let sender_id_slice = unwrap_err!(slice_field(sender_id, sender_id_len));
    let receiver_id_slice = unwrap_err!(slice_field(receiver_id, receiver_id_len));
    let reply_to_slice = unwrap_err!(slice_field(reply_to, reply_to_len));
    let payload_slice = unwrap_err!(slice_data(payload, payload_len));
    let caps_slice = unwrap_err!(slice_data(caps_flat, caps_flat_len));
    let metadata_slice = unwrap_err!(slice_data(metadata, metadata_len));
    let fields: [(&[u8], u32); 4] = [
        (message_id_slice, message_id_len),
        (sender_id_slice, sender_id_len),
        (receiver_id_slice, receiver_id_len),
        (reply_to_slice, reply_to_len),
    ];

    // Capability flat-buffer validation: each entry is [u32 len + bytes],
    // bounded in count and size. (Validation only — the blob is framed
    // on the Python side and embedded verbatim.)
    if !valid_caps_flat(caps_slice) {
        return ERR_INVALID_WIRE;
    }

    let total = 14usize
        .checked_add(4 * 4) // four u32 string-length prefixes
        .and_then(|n| n.checked_add(
            fields.iter().map(|(b, _)| b.len()).sum::<usize>()))
        .and_then(|n| n.checked_add(4)) // payload length prefix
        .and_then(|n| n.checked_add(payload_slice.len()))
        .and_then(|n| n.checked_add(4)) // caps length prefix
        .and_then(|n| n.checked_add(caps_slice.len()))
        .and_then(|n| n.checked_add(4)) // metadata length prefix
        .and_then(|n| n.checked_add(metadata_slice.len()));
    let total = match total {
        Some(n) if n <= MAX_WIRE_BYTES => n,
        _ => return ERR_TOO_LARGE,
    };

    let mut wire = Vec::with_capacity(total);
    wire.extend_from_slice(MAGIC);
    wire.push(WIRE_VERSION);
    wire.push(message_type);
    wire.extend_from_slice(&timestamp.to_le_bytes());
    for (field, len) in fields.iter() {
        wire.extend_from_slice(&(*len).to_le_bytes());
        wire.extend_from_slice(field);
    }
    wire.extend_from_slice(&payload_len.to_le_bytes());
    wire.extend_from_slice(payload_slice);
    wire.extend_from_slice(&caps_flat_len.to_le_bytes());
    wire.extend_from_slice(caps_slice);
    wire.extend_from_slice(&metadata_len.to_le_bytes());
    wire.extend_from_slice(metadata_slice);

    publish(&wire, out_ptr, out_len)
}

/// Decode a wire buffer into an `IpcMessageView`. The view and all its
/// field bytes live in one malloc'd block freed via `nyrqis_ipc_free`.
/// The buffer must be exactly one message (no trailing bytes). Returns
/// 0, -errno, or `ERR_INVALID_WIRE`.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipc_decode(
    buf: *const c_uchar,
    buf_len: u32,
    view_out: *mut *mut IpcMessageView,
) -> i32 {
    if buf.is_null() || view_out.is_null() {
        return ERR_INVALID_ARGS;
    }
    if buf_len as usize > MAX_WIRE_BYTES {
        return ERR_TOO_LARGE;
    }
    let data = std::slice::from_raw_parts(buf, buf_len as usize);
    let mut pos = 0usize;

    // NOTE: the closures whose output borrows from `data` carry NO
    // return-type annotation — an annotated `Option<&[u8]>` gets a
    // fresh elided lifetime unrelated to `data` (E0621: "lifetime may
    // not live long enough"; closures do not share fn-item output
    // elision), while inference binds the output to `data`'s lifetime
    // exactly.
    let take = |data: &[u8], pos: &mut usize, n: usize| {
        if *pos + n > data.len() {
            return None;
        }
        let s = &data[*pos..*pos + n];
        *pos += n;
        Some(s)
    };
    let take_u32 = |pos: &mut usize, data: &[u8]| -> Option<u32> {
        take(data, pos, 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    };
    let take_str = |data: &[u8], pos: &mut usize| {
        let len = take_u32(pos, data)?;
        if len as usize > MAX_FIELD_BYTES {
            return None;
        }
        let s = take(data, pos, len as usize)?;
        Some((s, len))
    };

    if take(data, &mut pos, 4) != Some(MAGIC) {
        return ERR_INVALID_WIRE;
    }
    if take(data, &mut pos, 1) != Some(&[WIRE_VERSION]) {
        return ERR_INVALID_WIRE;
    }
    let message_type = unwrap_wire!(take(data, &mut pos, 1))[0];
    if message_type > 4 {
        return ERR_INVALID_WIRE;
    }
    let ts_bytes = unwrap_wire!(take(data, &mut pos, 8));
    let timestamp = f64::from_le_bytes([
        ts_bytes[0], ts_bytes[1], ts_bytes[2], ts_bytes[3],
        ts_bytes[4], ts_bytes[5], ts_bytes[6], ts_bytes[7],
    ]);
    let (message_id, message_id_len) = unwrap_wire!(take_str(data, &mut pos));
    let (sender_id, sender_id_len) = unwrap_wire!(take_str(data, &mut pos));
    let (receiver_id, receiver_id_len) = unwrap_wire!(take_str(data, &mut pos));
    let (reply_to, reply_to_len) = unwrap_wire!(take_str(data, &mut pos));
    let (payload, payload_len) = {
        let len = unwrap_wire!(take_u32(&mut pos, data));
        if len as usize > MAX_WIRE_BYTES {
            return ERR_INVALID_WIRE;
        }
        (unwrap_wire!(take(data, &mut pos, len as usize)), len)
    };
    let (caps_flat, caps_flat_len) = {
        let len = unwrap_wire!(take_u32(&mut pos, data));
        (unwrap_wire!(take(data, &mut pos, len as usize)), len)
    };
    let (metadata, metadata_len) = {
        let len = unwrap_wire!(take_u32(&mut pos, data));
        (unwrap_wire!(take(data, &mut pos, len as usize)), len)
    };

    // Exact consumption: a trailing byte is a malformed message.
    if pos != data.len() {
        return ERR_INVALID_WIRE;
    }

    let total = std::mem::size_of::<IpcMessageView>()
        + message_id_len as usize + sender_id_len as usize
        + receiver_id_len as usize + reply_to_len as usize
        + payload_len as usize + caps_flat_len as usize + metadata_len as usize
        + 7 * 8; // 8-byte alignment padding between the fields
    let block = unsafe { libc_malloc(total.max(1)) };
    if block.is_null() {
        return ERR_INTERNAL;
    }

    let view_ptr = block as *mut IpcMessageView;
    unsafe { std::ptr::write_bytes(view_ptr, 0, 1) };
    let view = unsafe { &mut *view_ptr };
    view.message_type = message_type;
    view.timestamp = timestamp;

    let mut cursor = (block as usize) + std::mem::size_of::<IpcMessageView>();
    let mut place = |bytes: &[u8], len: u32| -> (*const u8, usize) {
        cursor = align_up(cursor, 8);
        if !bytes.is_empty() {
            unsafe {
                std::ptr::copy_nonoverlapping(
                    bytes.as_ptr(), cursor as *mut c_uchar, bytes.len());
            }
        }
        let ptr = cursor as *const u8;
        cursor += bytes.len();
        (ptr, len as usize)
    };

    let (p, l) = place(message_id, message_id_len);
    view.message_id = p;
    view.message_id_len = l as u32;
    let (p, l) = place(sender_id, sender_id_len);
    view.sender_id = p;
    view.sender_id_len = l as u32;
    let (p, l) = place(receiver_id, receiver_id_len);
    view.receiver_id = p;
    view.receiver_id_len = l as u32;
    let (p, l) = place(reply_to, reply_to_len);
    view.reply_to = p;
    view.reply_to_len = l as u32;
    let (p, l) = place(payload, payload_len);
    view.payload = p;
    view.payload_len = l as u32;
    let (p, l) = place(caps_flat, caps_flat_len);
    view.caps_flat = p;
    view.caps_flat_len = l as u32;
    let (p, l) = place(metadata, metadata_len);
    view.metadata = p;
    view.metadata_len = l as u32;

    *view_out = view_ptr;
    0
}

/// Free an output buffer previously returned by this module (the
/// seccomp/nyfs ownership contract). No-op on a null pointer.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_ipc_free(ptr: *mut c_uchar) {
    if !ptr.is_null() {
        unsafe { libc::free(ptr as *mut c_void) };
    }
}

/// Convert a caller pointer+length into a slice, enforcing the 1 MiB
/// per-field bound (the string fields: id/sender/receiver/reply_to).
unsafe fn slice_field<'a>(ptr: *const c_uchar, len: u32) -> Result<&'a [u8], i32> {
    slice_checked(ptr, len, MAX_FIELD_BYTES)
}

/// Convert a caller pointer+length into a slice, enforcing the 16 MiB
/// total-wire bound (payload/caps/metadata) — the SAME bound the
/// decoder applies, so every wire the encoder can produce the decoder
/// accepts (and vice versa).
unsafe fn slice_data<'a>(ptr: *const c_uchar, len: u32) -> Result<&'a [u8], i32> {
    slice_checked(ptr, len, MAX_WIRE_BYTES)
}

unsafe fn slice_checked<'a>(
    ptr: *const c_uchar,
    len: u32,
    max: usize,
) -> Result<&'a [u8], i32> {
    if len as usize > max {
        return Err(ERR_TOO_LARGE);
    }
    if len == 0 {
        return Ok(&[]);
    }
    if ptr.is_null() {
        return Err(ERR_INVALID_ARGS);
    }
    Ok(std::slice::from_raw_parts(ptr, len as usize))
}

/// Validate the capabilities flat buffer: `[u32 len + bytes]*`, bounded
/// entry count and per-entry size, exact consumption.
fn valid_caps_flat(flat: &[u8]) -> bool {
    let mut pos = 0usize;
    let mut count = 0u32;
    while pos < flat.len() {
        if flat.len() - pos < 4 {
            return false; // truncated length prefix
        }
        let len = u32::from_le_bytes([
            flat[pos], flat[pos + 1], flat[pos + 2], flat[pos + 3],
        ]);
        pos += 4;
        if len as usize > MAX_FIELD_BYTES {
            return false;
        }
        if flat.len() - pos < len as usize {
            return false; // truncated cap bytes
        }
        pos += len as usize;
        count += 1;
        if count > MAX_CAPS {
            return false;
        }
    }
    true
}

/// Copy `bytes` into a freshly malloc'd buffer (the module's ownership
/// contract).
unsafe fn publish(bytes: &[u8], out_ptr: *mut *mut c_uchar, out_len: *mut usize) -> i32 {
    let ptr = unsafe { libc_malloc(bytes.len().max(1)) };
    if ptr.is_null() {
        return ERR_INTERNAL;
    }
    if !bytes.is_empty() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr as *mut c_uchar, bytes.len());
    }
    *out_ptr = ptr as *mut c_uchar;
    *out_len = bytes.len();
    0
}

unsafe fn libc_malloc(size: usize) -> *mut c_void {
    unsafe { libc::malloc(size) }
}

fn align_up(n: usize, align: usize) -> usize {
    (n + align - 1) & !(align - 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Encode with the same signature the FFI exposes (test helper).
    unsafe fn enc(
        mtype: u8,
        id: &[u8], sender: &[u8], receiver: &[u8], reply: &[u8],
        payload: &[u8], caps: &[u8], meta: &[u8],
    ) -> (i32, Vec<u8>) {
        let mut out: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = nyrqis_ipc_encode(
            mtype,
            1234.5,
            id.as_ptr(), id.len() as u32,
            sender.as_ptr(), sender.len() as u32,
            receiver.as_ptr(), receiver.len() as u32,
            reply.as_ptr(), reply.len() as u32,
            payload.as_ptr(), payload.len() as u32,
            caps.as_ptr(), caps.len() as u32,
            meta.as_ptr(), meta.len() as u32,
            &mut out, &mut out_len,
        );
        let bytes = if out.is_null() {
            Vec::new()
        } else {
            let v = std::slice::from_raw_parts(out, out_len).to_vec();
            nyrqis_ipc_free(out);
            v
        };
        (rc, bytes)
    }

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn err_codes_are_outside_errno_range() {
        for code in [ERR_INTERNAL, ERR_INVALID_WIRE] {
            assert!(!(1..=4095).contains(&(-code)));
        }
        assert_eq!(ERR_INVALID_ARGS, -libc::EINVAL);
        assert_eq!(ERR_TOO_LARGE, -libc::EFBIG);
    }

    #[test]
    fn ffi_symbols_exist() {
        let _ = nyrqis_ipc_version;
        let _ = nyrqis_ipc_encode;
        let _ = nyrqis_ipc_decode;
        let _ = nyrqis_ipc_free;
    }

    #[test]
    fn encode_layout_is_canonical() {
        // A message with known fields pins the wire layout byte-for-byte:
        // header, u32 length prefixes, and payloads in order.
        let (rc, w) = unsafe {
            enc(2, b"id1", b"s1", b"r1", b"", b"hello", b"", b"{}")
        };
        assert_eq!(rc, 0);
        assert_eq!(&w[0..4], b"NYRQ");
        assert_eq!(w[4], 1); // wire version
        assert_eq!(w[5], 2); // message_type = call
        assert_eq!(&w[6..14], &1234.5f64.to_le_bytes()); // timestamp
        assert_eq!(&w[14..18], &3u32.to_le_bytes()); // message_id_len
        assert_eq!(&w[18..21], b"id1");
        assert_eq!(&w[21..25], &2u32.to_le_bytes()); // sender_id_len
        assert_eq!(&w[25..27], b"s1");
        assert_eq!(&w[27..31], &2u32.to_le_bytes()); // receiver_id_len
        assert_eq!(&w[31..33], b"r1");
        assert_eq!(&w[33..37], &0u32.to_le_bytes()); // reply_to_len (absent)
        assert_eq!(&w[37..41], &5u32.to_le_bytes()); // payload_len
        assert_eq!(&w[41..46], b"hello");
        assert_eq!(&w[46..50], &0u32.to_le_bytes()); // caps_flat_len
        assert_eq!(&w[50..54], &2u32.to_le_bytes()); // metadata_len
        assert_eq!(&w[54..56], b"{}");
        assert_eq!(w.len(), 56);
    }

    #[test]
    fn encode_decode_roundtrip_preserves_fields() {
        let caps = {
            let mut f = Vec::new();
            for cap in [b"CAP_IPC_SEND".as_slice(), b"CAP_IPC_RECEIVE".as_slice()] {
                f.extend_from_slice(&(cap.len() as u32).to_le_bytes());
                f.extend_from_slice(cap);
            }
            f
        };
        let (rc, w) = unsafe {
            enc(
                0, b"msg-1234", b"container-a", b"container-b", b"",
                b"payload-bytes", &caps, b"{\"k\": 1}",
            )
        };
        assert_eq!(rc, 0);

        let mut view_ptr: *mut IpcMessageView = std::ptr::null_mut();
        let rc = unsafe { nyrqis_ipc_decode(w.as_ptr(), w.len() as u32, &mut view_ptr) };
        assert_eq!(rc, 0);
        let view = unsafe { &*view_ptr };
        assert_eq!(view.message_type, 0);
        assert_eq!(view.timestamp, 1234.5);
        assert_eq!(view.message_id_len as usize, 8);
        assert_eq!(
            unsafe { std::slice::from_raw_parts(view.message_id, view.message_id_len as usize) },
            b"msg-1234"
        );
        assert_eq!(
            unsafe { std::slice::from_raw_parts(view.sender_id, view.sender_id_len as usize) },
            b"container-a"
        );
        assert_eq!(view.reply_to_len, 0);
        assert_eq!(
            unsafe { std::slice::from_raw_parts(view.payload, view.payload_len as usize) },
            b"payload-bytes"
        );
        assert_eq!(view.caps_flat_len as usize, caps.len());
        assert_eq!(
            unsafe { std::slice::from_raw_parts(view.metadata, view.metadata_len as usize) },
            b"{\"k\": 1}"
        );
        unsafe { nyrqis_ipc_free(view_ptr as *mut c_uchar) };
    }

    #[test]
    fn large_payload_roundtrips_within_wire_bound() {
        // Payloads beyond the per-field string cap (1 MiB) are legal:
        // payload/caps/metadata are bounded by the total wire bound
        // (16 MiB) on BOTH encode and decode — the encoder must accept
        // exactly what the decoder accepts.
        let payload = vec![0xABu8; 2 * 1024 * 1024]; // 2 MiB
        let (rc, w) = unsafe {
            enc(0, b"id", b"s", b"r", b"", &payload, b"", b"{}")
        };
        assert_eq!(rc, 0);
        assert!(w.len() > 2 * 1024 * 1024);

        let mut view_ptr: *mut IpcMessageView = std::ptr::null_mut();
        let rc = unsafe { nyrqis_ipc_decode(w.as_ptr(), w.len() as u32, &mut view_ptr) };
        assert_eq!(rc, 0);
        let view = unsafe { &*view_ptr };
        assert_eq!(view.payload_len as usize, payload.len());
        unsafe { nyrqis_ipc_free(view_ptr as *mut c_uchar) };
    }

    #[test]
    fn encode_rejects_payload_beyond_wire_bound() {
        // 17 MiB exceeds the 16 MiB total-wire bound → EFBIG.
        let payload = vec![0u8; 17 * 1024 * 1024];
        let (rc, _) = unsafe {
            enc(0, b"id", b"s", b"r", b"", &payload, b"", b"{}")
        };
        assert_eq!(rc, ERR_TOO_LARGE);
    }

    #[test]
    fn decode_rejects_malformed_wire() {
        let (rc, w) = unsafe { enc(1, b"id", b"s", b"r", b"", b"p", b"", b"") };
        assert_eq!(rc, 0);

        let mut view: *mut IpcMessageView = std::ptr::null_mut();
        // Bad magic.
        let mut bad = w.clone();
        bad[0] ^= 0xff;
        assert_eq!(unsafe { nyrqis_ipc_decode(bad.as_ptr(), bad.len() as u32, &mut view) }, ERR_INVALID_WIRE);
        // Wrong wire version.
        let mut bad = w.clone();
        bad[4] = 2;
        assert_eq!(unsafe { nyrqis_ipc_decode(bad.as_ptr(), bad.len() as u32, &mut view) }, ERR_INVALID_WIRE);
        // Unknown message type.
        let mut bad = w.clone();
        bad[5] = 9;
        assert_eq!(unsafe { nyrqis_ipc_decode(bad.as_ptr(), bad.len() as u32, &mut view) }, ERR_INVALID_WIRE);
        // Truncated.
        assert_eq!(unsafe { nyrqis_ipc_decode(w.as_ptr(), (w.len() - 1) as u32, &mut view) }, ERR_INVALID_WIRE);
        // Trailing garbage.
        let mut bad = w.clone();
        bad.push(0);
        assert_eq!(unsafe { nyrqis_ipc_decode(bad.as_ptr(), bad.len() as u32, &mut view) }, ERR_INVALID_WIRE);
    }

    #[test]
    fn encode_rejects_bad_args_and_caps() {
        let (rc, _) = unsafe { enc(7, b"id", b"s", b"r", b"", b"", b"", b"") };
        assert_eq!(rc, ERR_INVALID_ARGS); // message_type out of range

        // Caps flat buffer with a truncated entry.
        let bad_caps = [0u8, 0, 0, 5, 1, 2]; // claims 5 bytes, has 2
        let (rc, _) = unsafe {
            enc(0, b"id", b"s", b"r", b"", b"", &bad_caps, b"")
        };
        assert_eq!(rc, ERR_INVALID_WIRE);

        // Null pointer with non-zero length.
        let mut out: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_ipc_encode(
                0,
                0.0, // timestamp (irrelevant on the error path)
                std::ptr::null(), 3, // message_id: non-zero len with null ptr
                b"s".as_ptr(), 1,
                b"r".as_ptr(), 1,
                b"".as_ptr(), 0,
                b"".as_ptr(), 0,
                b"".as_ptr(), 0,
                b"".as_ptr(), 0,
                &mut out, &mut out_len,
            )
        };
        assert_eq!(rc, ERR_INVALID_ARGS);
    }

    #[test]
    fn free_null_is_safe() {
        unsafe { nyrqis_ipc_free(std::ptr::null_mut()) };
    }
}
