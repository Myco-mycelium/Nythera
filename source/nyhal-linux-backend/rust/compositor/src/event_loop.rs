//! Wayland wire-format event loop for the Nyrqis compositor.
//!
//! The request-processing half of a real compositor event loop: parses
//! client→compositor requests from the standard Wayland wire format
//! (8-byte header: `object_id: u32`, `size << 16 | opcode: u32`, then
//! arguments in protocol order), maintains the object table, and
//! dispatches requests into the compositor's surface state machine
//! (the crate-root FFI: surface create/commit/frame). Server→client
//! events (`wl_registry.global`, `wl_callback.done`,
//! `wl_buffer.release`) are encoded back into the same wire format and
//! queued per client — byte-compatible with the `WaylandEncoder` /
//! `WaylandDecoder` pair in the Python-side `wayland_protocol.py`
//! codec.
//!
//! Scope: this module is the protocol engine. The socket/epoll half
//! stays on the host side (`ui/nyrqis_compositor.py`), which feeds
//! client bytes in through `nyrqis_compositor_handle_client_data` and
//! drains responses with `nyrqis_compositor_next_event`. DRM-backed
//! surface presentation remains follow-on work (see the crate header).
//!
//! References:
//! - Wayland wire format: https://wayland.freedesktop.org/docs/html/ch04.html
//! - ADR-0026: Wayland display-server integration

use std::collections::HashMap;
use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// Maximum objects in the object table.
const MAX_OBJECTS: usize = 1024;
/// Maximum clients tracked for outbound event queues (mirrors
/// MAX_CLIENTS in the crate root).
const MAX_EVENT_CLIENTS: usize = 32;
/// Maximum queued outbound events per client (oldest dropped).
const MAX_EVENTS_PER_CLIENT: usize = 64;
/// Wire-format header size (object id + size|opcode).
const HEADER_SIZE: usize = 8;
/// Protocol versions advertised in wl_registry.global events.
const ADVERTISED_GLOBALS: &[(&str, u32)] = &[
    ("wl_compositor", 5),
    ("wl_shm", 1),
    ("wl_output", 3),
    ("wl_seat", 7),
    ("xdg_wm_base", 5),
];

// ---------------------------------------------------------------------------
// Object table
// ---------------------------------------------------------------------------

/// Interfaces the compositor serves (the subset of the protocol this
/// dev loop implements).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Interface {
    Display,
    Registry,
    Compositor,
    Surface,
    Callback,
    Output,
    Seat,
    Shm,
    ShmPool,
    Buffer,
    XdgWmBase,
}

impl Interface {
    fn from_name(name: &str) -> Option<Interface> {
        match name {
            "wl_display" => Some(Interface::Display),
            "wl_registry" => Some(Interface::Registry),
            "wl_compositor" => Some(Interface::Compositor),
            "wl_surface" => Some(Interface::Surface),
            "wl_callback" => Some(Interface::Callback),
            "wl_output" => Some(Interface::Output),
            "wl_seat" => Some(Interface::Seat),
            "wl_shm" => Some(Interface::Shm),
            "wl_shm_pool" => Some(Interface::ShmPool),
            "wl_buffer" => Some(Interface::Buffer),
            "xdg_wm_base" => Some(Interface::XdgWmBase),
            _ => None,
        }
    }
}

/// One object in the compositor's object table.
struct ObjectSlot {
    object_id: u32,
    interface: Interface,
    /// For Surface objects: the crate-root surface slot index (which is
    /// also the compositor surface id). For Callback objects: the
    /// surface id the callback is armed on.
    surface: Option<u32>,
    active: bool,
}

struct EventLoopState {
    objects: Vec<Option<ObjectSlot>>,
    /// Outbound server→client events, keyed by client id, wire-encoded.
    out_queues: HashMap<u32, Vec<Vec<u8>>>,
    last_error: String,
}

static EVENT_LOOP_STATE: Mutex<Option<EventLoopState>> = Mutex::new(None);

fn with_loop_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut EventLoopState) -> R,
{
    let mut guard = EVENT_LOOP_STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| EventLoopState {
        objects: (0..MAX_OBJECTS).map(|_| None).collect(),
        out_queues: HashMap::new(),
        last_error: String::new(),
    });
    f(state)
}

/// The Wayland wire protocol fixes object id 1 as the `wl_display`,
/// created implicitly at connection time — before any request can
/// reference it. Called lazily so a fresh state has it.
fn ensure_display_object(state: &mut EventLoopState) {
    if find_object(state, 1).is_none() {
        // Slot 0 is object id 1's home; take it if free, else any slot.
        let idx = if state.objects[0].is_none() {
            0
        } else {
            match alloc_object(state) {
                Some(i) => i,
                None => return, // table full; dispatch will error out
            }
        };
        state.objects[idx] = Some(ObjectSlot {
            object_id: 1,
            interface: Interface::Display,
            surface: None,
            active: true,
        });
    }
}

fn set_loop_error(state: &mut EventLoopState, msg: &str) {
    state.last_error = msg.to_string();
}

fn alloc_object(state: &mut EventLoopState) -> Option<usize> {
    state.objects.iter().position(|o| o.is_none())
}

/// Look up an active object slot index by object id.
fn find_object(state: &EventLoopState, object_id: u32) -> Option<usize> {
    state.objects.iter().position(|o| {
        o.as_ref().map_or(false, |o| o.active && o.object_id == object_id)
    })
}

// ---------------------------------------------------------------------------
// Wire-format encoding/decoding
// ---------------------------------------------------------------------------

/// One decoded client request. Arguments stay unparsed in the
/// payload; each request reads them positionally per its protocol
/// layout (the wire format carries no type tags).
struct DecodedRequest {
    object_id: u32,
    opcode: u16,
    /// The argument payload just past the header.
    payload: Vec<u8>,
}

/// A positional argument reader over a request payload.
struct ArgReader<'a> {
    buf: &'a [u8],
    off: usize,
}

impl<'a> ArgReader<'a> {
    fn new(buf: &'a [u8]) -> Self {
        ArgReader { buf, off: 0 }
    }

    fn read_u32(&mut self) -> Option<u32> {
        let v = read_u32(self.buf, self.off)?;
        self.off += 4;
        Some(v)
    }

    fn read_string(&mut self) -> Option<String> {
        let (s, next) = read_string(self.buf, self.off)?;
        self.off = next;
        Some(s)
    }
}

/// Decode a u32 at `offset` (None when out of bounds).
fn read_u32(buf: &[u8], offset: usize) -> Option<u32> {
    if offset + 4 > buf.len() {
        return None;
    }
    Some(u32::from_le_bytes([
        buf[offset],
        buf[offset + 1],
        buf[offset + 2],
        buf[offset + 3],
    ]))
}

/// Read a string argument at `offset`: u32 length (including the
/// terminating NUL) then the bytes, padded to 4-byte alignment.
/// Returns the string (NUL stripped) and the offset past the padding.
fn read_string(buf: &[u8], offset: usize) -> Option<(String, usize)> {
    let len = read_u32(buf, offset)? as usize;
    let data_at = offset + 4;
    if len == 0 || data_at + len > buf.len() {
        return None;
    }
    let mut end = data_at + len;
    // Strip the terminating NUL (protocol: length includes it; tolerate
    // its absence from lenient encoders).
    if buf[end - 1] == 0 {
        end -= 1;
    }
    let s = String::from_utf8_lossy(&buf[data_at..end]).into_owned();
    // Advance past the padding to the next 4-byte boundary.
    let padded = (len + 3) & !3;
    Some((s, data_at + padded))
}

/// Parse one complete wire-format message from the front of `buf`.
/// Returns the decoded request and the total consumed size (header +
/// padded payload), or None when the buffer does not hold a complete,
/// well-formed message.
fn parse_message(buf: &[u8]) -> Option<(DecodedRequest, usize)> {
    if buf.len() < HEADER_SIZE {
        return None;
    }
    let object_id = read_u32(buf, 0)?;
    let size_opcode = read_u32(buf, 4)?;
    let size = (size_opcode >> 16) as usize;
    let opcode = (size_opcode & 0xffff) as u16;
    // Size covers the whole message including the header.
    if size < HEADER_SIZE || size > buf.len() {
        return None;
    }
    let payload_buf = &buf[HEADER_SIZE..size];
    Some((
        DecodedRequest {
            object_id,
            opcode,
            payload: payload_buf.to_vec(),
        },
        size,
    ))
}

/// Encode a server→client event: header + already-encoded payload.
fn encode_event(object_id: u32, opcode: u16, payload: &[u8]) -> Vec<u8> {
    let size = (HEADER_SIZE + payload.len()) as u32;
    let mut out = Vec::with_capacity(size as usize);
    out.extend_from_slice(&object_id.to_le_bytes());
    out.extend_from_slice(&((size << 16) | (opcode as u32)).to_le_bytes());
    out.extend_from_slice(payload);
    out
}

/// Encode a string argument (u32 len incl. NUL, bytes, NUL, pad-to-4).
fn encode_string_arg(s: &str) -> Vec<u8> {
    let mut out = Vec::new();
    let len = s.len() + 1; // include NUL, matching wayland_protocol.py
    out.extend_from_slice(&(len as u32).to_le_bytes());
    out.extend_from_slice(s.as_bytes());
    out.push(0);
    while out.len() % 4 != 0 {
        out.push(0);
    }
    out
}

fn encode_u32_arg(v: u32) -> Vec<u8> {
    v.to_le_bytes().to_vec()
}

// ---------------------------------------------------------------------------
// Request opcodes (the served subset)
// ---------------------------------------------------------------------------

const OPCODE_DISPLAY_GET_REGISTRY: u16 = 1;
const OPCODE_DISPLAY_SYNC: u16 = 0;
const OPCODE_REGISTRY_BIND: u16 = 0;
const OPCODE_COMPOSITOR_CREATE_SURFACE: u16 = 0;
const OPCODE_SURFACE_DESTROY: u16 = 0;
const OPCODE_SURFACE_ATTACH: u16 = 1;
const OPCODE_SURFACE_FRAME: u16 = 3;
const OPCODE_SURFACE_COMMIT: u16 = 6;

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

/// Apply one decoded request to the compositor state, queuing any
/// response events. Returns false on a protocol error (last_error set).
fn dispatch_request(
    state: &mut EventLoopState,
    client_id: u32,
    req: &DecodedRequest,
) -> bool {
    let obj_idx = match find_object(state, req.object_id) {
        Some(i) => i,
        None => {
            set_loop_error(
                state,
                &format!("protocol error: unknown object {}", req.object_id),
            );
            return false;
        }
    };
    let interface = state.objects[obj_idx].as_ref().unwrap().interface;
    let mut args = ArgReader::new(&req.payload);
    match (interface, req.opcode) {
        (Interface::Display, OPCODE_DISPLAY_SYNC) => {
            // arg: new_id for the wl_callback object. One-shot done
            // event on the callback (payload: callback data u32 —
            // the event carries no payload; the object id is the
            // callback).
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: sync missing callback id");
                    return false;
                }
            };
            if !create_object(state, new_id, Interface::Callback, None) {
                return false;
            }
            // wl_callback.done (opcode 0) with no payload; the callback
            // object retires immediately (one-shot, event already
            // queued) — mirrors the commit-path delivery.
            queue_event(state, client_id, encode_event(new_id, 0, &[]));
            retire_object(state, new_id);
            true
        }
        (Interface::Display, OPCODE_DISPLAY_GET_REGISTRY) => {
            // arg: new_id for the wl_registry object
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: get_registry missing new_id");
                    return false;
                }
            };
            if !create_object(state, new_id, Interface::Registry, None) {
                return false;
            }
            // Advertise the globals: one wl_registry.global per entry
            // (name, interface, version). Names are 1-based, stable.
            for (i, (name, version)) in ADVERTISED_GLOBALS.iter().enumerate() {
                let mut payload = encode_u32_arg((i + 1) as u32);
                payload.extend_from_slice(&encode_string_arg(name));
                payload.extend_from_slice(&encode_u32_arg(*version));
                queue_event(state, client_id, encode_event(new_id, 0, &payload));
            }
            true
        }
        (Interface::Registry, OPCODE_REGISTRY_BIND) => {
            // args: name u32, interface string, version u32, new_id u32
            let name = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: bind missing name");
                    return false;
                }
            };
            let iface_name = match args.read_string() {
                Some(s) => s,
                None => {
                    set_loop_error(state, "protocol error: bind missing interface");
                    return false;
                }
            };
            let _version = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: bind missing version");
                    return false;
                }
            };
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: bind missing new_id");
                    return false;
                }
            };
            let iface = match Interface::from_name(&iface_name) {
                Some(i) => i,
                None => {
                    set_loop_error(
                        state,
                        &format!("protocol error: unsupported interface {}", iface_name),
                    );
                    return false;
                }
            };
            let name_idx = name as usize;
            if name_idx == 0 || name_idx > ADVERTISED_GLOBALS.len() {
                set_loop_error(state, "protocol error: bind unknown global name");
                return false;
            }
            if !create_object(state, new_id, iface, None) {
                return false;
            }
            true
        }
        (Interface::Compositor, OPCODE_COMPOSITOR_CREATE_SURFACE) => {
            // arg: new_id for the wl_surface object
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: create_surface missing new_id");
                    return false;
                }
            };
            // Create the real surface through the crate-root state
            // machine (slot index == surface id).
            let surface_id = crate::nyrqis_compositor_create_surface(client_id, 0, 0);
            if surface_id < 0 {
                set_loop_error(state, "protocol error: surface table full");
                return false;
            }
            if !create_object(state, new_id, Interface::Surface, Some(surface_id as u32)) {
                return false;
            }
            true
        }
        (Interface::Surface, OPCODE_SURFACE_DESTROY) => {
            let surface = state.objects[obj_idx].as_ref().unwrap().surface;
            crate::nyrqis_compositor_destroy_surface(
                surface.map_or(-1, |s| s as c_int),
            );
            state.objects[obj_idx] = None;
            true
        }
        (Interface::Surface, OPCODE_SURFACE_ATTACH) => {
            // args: buffer object id, x i32, y i32
            let buffer_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: attach missing buffer");
                    return false;
                }
            };
            let surface = state.objects[obj_idx].as_ref().unwrap().surface;
            let surface = match surface {
                Some(s) => s,
                None => {
                    set_loop_error(state, "protocol error: surface object without surface");
                    return false;
                }
            };
            if find_object(state, buffer_id).is_none() {
                set_loop_error(state, "protocol error: attach unknown buffer");
                return false;
            }
            if !crate::mark_surface_pending_buffer(surface) {
                set_loop_error(state, "protocol error: attach inactive surface");
                return false;
            }
            true
        }
        (Interface::Surface, OPCODE_SURFACE_FRAME) => {
            // arg: new_id for the wl_callback object
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: frame missing callback id");
                    return false;
                }
            };
            let surface = state.objects[obj_idx].as_ref().unwrap().surface;
            if !create_object(state, new_id, Interface::Callback, surface) {
                return false;
            }
            true
        }
        (Interface::Surface, OPCODE_SURFACE_COMMIT) => {
            let surface = state.objects[obj_idx].as_ref().unwrap().surface;
            let surface = match surface {
                Some(s) => s,
                None => {
                    set_loop_error(state, "protocol error: surface object without surface");
                    return false;
                }
            };
            if crate::nyrqis_compositor_commit_surface(surface as c_int) != 0 {
                set_loop_error(state, "protocol error: commit inactive surface");
                return false;
            }
            // Deliver the frame callback armed on this surface: one
            // wl_callback.done per armed callback (one-shot), stamped
            // with the surface's commit count (the compositor's
            // monotonic frame stamp).
            let stamp = crate::nyrqis_compositor_last_frame_time(surface as c_int);
            let cb_idxs: Vec<usize> = state
                .objects
                .iter()
                .enumerate()
                .filter(|(_, o)| {
                    o.as_ref().map_or(false, |o| {
                        o.active && o.interface == Interface::Callback && o.surface == Some(surface)
                    })
                })
                .map(|(i, _)| i)
                .collect();
            for idx in cb_idxs {
                let cb_id = state.objects[idx].as_ref().unwrap().object_id;
                let payload = encode_u32_arg(stamp as u32);
                queue_event(state, client_id, encode_event(cb_id, 0, &payload));
                // One-shot: the callback object retires after delivery.
                state.objects[idx] = None;
            }
            // Retire the attached buffer: wl_buffer.release per the
            // protocol (the compositor is done with the buffer's
            // content for this frame).
            let buf_idxs: Vec<usize> = state
                .objects
                .iter()
                .enumerate()
                .filter(|(_, o)| {
                    o.as_ref().map_or(false, |o| {
                        o.active && o.interface == Interface::Buffer
                    })
                })
                .map(|(i, _)| i)
                .collect();
            for idx in buf_idxs {
                let buf_id = state.objects[idx].as_ref().unwrap().object_id;
                queue_event(state, client_id, encode_event(buf_id, 0, &[]));
                state.objects[idx] = None;
            }
            true
        }
        _ => {
            set_loop_error(
                state,
                &format!(
                    "protocol error: unsupported {:?} opcode {}",
                    interface, req.opcode
                ),
            );
            false
        }
    }
}

/// Create an object in the table (fails when the table is full or the
/// id is already taken by an active object).
fn create_object(
    state: &mut EventLoopState,
    object_id: u32,
    interface: Interface,
    surface: Option<u32>,
) -> bool {
    if find_object(state, object_id).is_some() {
        set_loop_error(state, "protocol error: object id already in use");
        return false;
    }
    let idx = match alloc_object(state) {
        Some(i) => i,
        None => {
            set_loop_error(state, "protocol error: object table full");
            return false;
        }
    };
    state.objects[idx] = Some(ObjectSlot {
        object_id,
        interface,
        surface,
        active: true,
    });
    true
}

/// Queue a wire-encoded event for a client (bounded; oldest dropped).
fn queue_event(state: &mut EventLoopState, client_id: u32, event: Vec<u8>) {
    let queue = state.out_queues.entry(client_id).or_default();
    if queue.len() >= MAX_EVENTS_PER_CLIENT {
        queue.remove(0);
    }
    queue.push(event);
}

/// Retire an object by id (its slot becomes reusable).
fn retire_object(state: &mut EventLoopState, object_id: u32) {
    if let Some(idx) = find_object(state, object_id) {
        state.objects[idx] = None;
    }
}

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Feed client bytes into the event loop: parses every complete
/// wire-format message in the buffer and dispatches it. Response
/// events are queued for the client (drain with
/// `nyrqis_compositor_next_event`).
///
/// The buffer must end on a message boundary: a trailing partial
/// message is a protocol error (real clients write whole messages per
/// flush; the host-side socket loop reassembles before calling).
///
/// Returns the number of bytes consumed, or -1 on a protocol error
/// (messages before the malformed one have already been applied).
///
/// # Safety
/// `data_ptr` must point to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_compositor_handle_client_data(
    client_id: u32,
    data_ptr: *const u8,
    len: usize,
) -> c_int {
    if data_ptr.is_null() && len > 0 {
        return -1;
    }
    let buf = if len > 0 {
        std::slice::from_raw_parts(data_ptr, len)
    } else {
        &[][..]
    };
    with_loop_state(|state| {
        ensure_display_object(state);
        let mut consumed = 0usize;
        while consumed < buf.len() {
            match parse_message(&buf[consumed..]) {
                Some((req, size)) => {
                    if !dispatch_request(state, client_id, &req) {
                        return -1;
                    }
                    consumed += size;
                }
                None => {
                    set_loop_error(state, "protocol error: truncated or malformed message");
                    return -1;
                }
            }
        }
        consumed as c_int
    })
}

/// Drain the next queued server→client event for `client_id` into
/// `out_buf` (capacity `cap`).
///
/// Returns the number of bytes written (> 0), 0 when no events are
/// queued, or -1 when the buffer is too small (last_error names the
/// required size) or the arguments are invalid.
///
/// # Safety
/// `out_buf` must be writable for `cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_compositor_next_event(
    client_id: u32,
    out_buf: *mut u8,
    cap: usize,
) -> c_int {
    if out_buf.is_null() && cap > 0 {
        return -1;
    }
    with_loop_state(|state| {
        let empty = state.out_queues.get(&client_id).map_or(true, |q| q.is_empty());
        if empty {
            return 0;
        }
        let event = {
            let queue = state.out_queues.get_mut(&client_id).unwrap();
            queue.remove(0)
        }; // mutable borrow ends here
        if event.len() > cap {
            set_loop_error(
                state,
            &format!("event too large for buffer: need {} bytes", event.len()),
            );
            // Restore the event so it is not lost.
            state
                .out_queues
                .get_mut(&client_id)
                .unwrap()
                .insert(0, event);
            return -1;
        }
        std::ptr::copy_nonoverlapping(event.as_ptr(), out_buf, event.len());
        event.len() as c_int
    })
}

/// Number of active objects in the object table.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_object_count() -> c_int {
    with_loop_state(|state| {
        ensure_display_object(state);
        state
            .objects
            .iter()
            .filter(|o| o.as_ref().map_or(false, |o| o.active))
            .count() as c_int
    })
}

/// Copy the last event-loop error into `buf` (NUL-terminated).
/// Returns the string length (excluding NUL), or -1 when `cap` is too
/// small (the required size is not reported; call again with a larger
/// buffer — errors are short).
///
/// # Safety
/// `buf` must be writable for `cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_compositor_event_loop_last_error(
    buf: *mut c_char,
    cap: c_int,
) -> c_int {
    if buf.is_null() || cap <= 0 {
        return -1;
    }
    let msg = with_loop_state(|state| state.last_error.clone());
    let bytes = msg.as_bytes();
    let n = bytes.len().min((cap - 1) as usize);
    std::ptr::copy_nonoverlapping(bytes.as_ptr() as *const c_char, buf, n);
    *buf.add(n) = 0;
    n as c_int
}

/// Drop all event-loop state (objects + queues).
///
/// Called by the crate-root `nyrqis_compositor_start` so every
/// compositor session begins with a clean object table — otherwise a
/// restart would collide with stale object ids (e.g. a re-connected
/// client's get_registry(new_id=2) failing with "already in use").
pub(crate) fn reset_event_loop_state() {
    let mut guard = EVENT_LOOP_STATE.lock().unwrap();
    *guard = None;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::TEST_LOCK;
    use crate::reset_state;

    /// Minimal request encoder matching the wire format (and
    /// wayland_protocol.py's encoder on the Python side).
    fn enc_request(object_id: u32, opcode: u16, payload: &[u8]) -> Vec<u8> {
        let size = (HEADER_SIZE + payload.len()) as u32;
        let mut out = Vec::new();
        out.extend_from_slice(&object_id.to_le_bytes());
        out.extend_from_slice(&((size << 16) | (opcode as u32)).to_le_bytes());
        out.extend_from_slice(payload);
        out
    }

    fn enc_u32(v: u32) -> Vec<u8> {
        v.to_le_bytes().to_vec()
    }

    /// Drain all queued events for a client as raw byte vectors.
    unsafe fn drain_events(client_id: u32) -> Vec<Vec<u8>> {
        let mut out = Vec::new();
        let mut buf = [0u8; 512];
        loop {
            let n = nyrqis_compositor_next_event(client_id, buf.as_mut_ptr(), buf.len());
            assert!(n >= 0);
            if n == 0 {
                break;
            }
            out.push(buf[..n as usize].to_vec());
        }
        out
    }

    /// Decode an event's (object_id, opcode) header.
    fn ev_header(event: &[u8]) -> (u32, u16) {
        let object_id = u32::from_le_bytes([event[0], event[1], event[2], event[3]]);
        let size_opcode = u32::from_le_bytes([event[4], event[5], event[6], event[7]]);
        (object_id, (size_opcode & 0xffff) as u16)
    }

    #[test]
    fn parse_message_header_fields() {
        let msg = enc_request(7, 3, &enc_u32(42));
        let (req, size) = parse_message(&msg).expect("parses");
        assert_eq!(req.object_id, 7);
        assert_eq!(req.opcode, 3);
        assert_eq!(size, msg.len());
    }

    #[test]
    fn parse_message_rejects_truncated() {
        let msg = enc_request(7, 3, &enc_u32(42));
        assert!(parse_message(&msg[..msg.len() - 1]).is_none());
        assert!(parse_message(&[]).is_none());
    }

    #[test]
    fn parse_message_rejects_undersized_header_claim() {
        // Size field claims 4 bytes (< header size).
        let mut msg = enc_request(7, 3, &[]);
        msg[4] = 4; // size = 4
        msg[6] = 0;
        assert!(parse_message(&msg).is_none());
    }

    #[test]
    fn registry_globals_advertised() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // wl_display.get_registry(new_id=2)
        let req = enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(1, req.as_ptr(), req.len())
        };
        assert_eq!(n, req.len() as c_int);
        let events = unsafe { drain_events(1) };
        assert_eq!(events.len(), ADVERTISED_GLOBALS.len());
        for (i, event) in events.iter().enumerate() {
            let (object_id, opcode) = ev_header(event);
            assert_eq!(object_id, 2);
            assert_eq!(opcode, 0); // wl_registry.global
            // name = i + 1
            let name =
                u32::from_le_bytes([event[8], event[9], event[10], event[11]]);
            assert_eq!(name, (i + 1) as u32);
        }
        // wl_display (implicit) + the registry.
        assert_eq!(nyrqis_compositor_object_count(), 2);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn create_surface_and_commit_through_wire() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        assert_eq!(crate::nyrqis_compositor_add_output(800, 600, std::ptr::null(), 0), 0);

        // registry=2, compositor=3, surface=4
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut payload = enc_u32(1);
        payload.extend_from_slice(&encode_string_arg("wl_compositor"));
        payload.extend_from_slice(&enc_u32(5));
        payload.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &payload));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        // wl_surface(4).commit
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_COMMIT, &[]));

        let n = unsafe {
            nyrqis_compositor_handle_client_data(9, buf.as_ptr(), buf.len())
        };
        assert_eq!(n, buf.len() as c_int);

        // The surface now exists in the crate-root state machine and
        // has one commit. The wl_surface wire object (4) maps to the
        // first free crate surface slot (0).
        assert_eq!(crate::nyrqis_compositor_surface_count(), 1);
        assert_eq!(
            crate::nyrqis_compositor_commit_count(0),
            1,
            "wire commit landed in the surface state machine"
        );
        // wl_display (implicit) + registry + compositor + surface.
        assert_eq!(nyrqis_compositor_object_count(), 4);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn frame_callback_delivered_on_commit() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);

        // registry=2, compositor=3, surface=4, callback=5
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut payload = enc_u32(1);
        payload.extend_from_slice(&encode_string_arg("wl_compositor"));
        payload.extend_from_slice(&enc_u32(5));
        payload.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &payload));

        let n = unsafe {
            nyrqis_compositor_handle_client_data(3, buf.as_ptr(), buf.len())
        };
        assert_eq!(n, buf.len() as c_int);
        // Drain the registry globals so the frame assertion below sees
        // only the callback delivery.
        let globals = unsafe { drain_events(3) };
        assert_eq!(globals.len(), ADVERTISED_GLOBALS.len());

        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        // wl_surface(4).frame(new_id=5)
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_FRAME, &enc_u32(5)));
        // wl_surface(4).commit
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_COMMIT, &[]));

        let n = unsafe {
            nyrqis_compositor_handle_client_data(3, buf.as_ptr(), buf.len())
        };
        assert_eq!(n, buf.len() as c_int);

        let events = unsafe { drain_events(3) };
        // One event: wl_callback.done on object 5.
        assert_eq!(events.len(), 1, "expected exactly the frame-done event");
        let (object_id, opcode) = ev_header(&events[0]);
        assert_eq!(object_id, 5);
        assert_eq!(opcode, 0);
        // Timestamp = the surface's commit stamp (> 0 after commit).
        let stamp = u32::from_le_bytes([
            events[0][8],
            events[0][9],
            events[0][10],
            events[0][11],
        ]);
        assert!(stamp > 0);
        // One-shot: a second commit delivers nothing.
        let commit = enc_request(4, OPCODE_SURFACE_COMMIT, &[]);
        let n = unsafe {
            nyrqis_compositor_handle_client_data(3, commit.as_ptr(), commit.len())
        };
        assert_eq!(n, commit.len() as c_int);
        let events = unsafe { drain_events(3) };
        // The release event for the (unattached) buffer never fires;
        // no callbacks remain armed.
        assert!(events.iter().all(|e| ev_header(e).0 != 5));
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn sync_delivers_done_event() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // wl_display.sync(new_id=7)
        let req = enc_request(1, OPCODE_DISPLAY_SYNC, &enc_u32(7));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(5, req.as_ptr(), req.len())
        };
        assert_eq!(n, req.len() as c_int);
        let events = unsafe { drain_events(5) };
        // Exactly one event: wl_callback.done on object 7.
        assert_eq!(events.len(), 1);
        let (object_id, opcode) = ev_header(&events[0]);
        assert_eq!(object_id, 7);
        assert_eq!(opcode, 0);
        // The callback object retires after delivery (one-shot).
        assert_eq!(nyrqis_compositor_object_count(), 1);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn unknown_object_is_protocol_error() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        // Object 99 does not exist.
        let req = enc_request(99, OPCODE_SURFACE_COMMIT, &[]);
        let n = unsafe {
            nyrqis_compositor_handle_client_data(1, req.as_ptr(), req.len())
        };
        assert_eq!(n, -1);
        let mut errbuf = [0u8; 128];
        let m = unsafe {
            nyrqis_compositor_event_loop_last_error(errbuf.as_mut_ptr() as *mut c_char, 128)
        };
        assert!(m > 0);
        let msg = std::str::from_utf8(&errbuf[..m as usize]).unwrap();
        assert!(msg.contains("unknown object"), "got: {}", msg);
    }

    #[test]
    fn truncated_tail_is_protocol_error() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        let full = enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(1, full.as_ptr(), full.len() - 2)
        };
        assert_eq!(n, -1);
    }

    #[test]
    fn bind_unknown_interface_rejected() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut payload = enc_u32(1);
        payload.extend_from_slice(&encode_string_arg("wl_bogus"));
        payload.extend_from_slice(&enc_u32(1));
        payload.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &payload));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len())
        };
        assert_eq!(n, -1);
        // Only wl_display (implicit) + the registry survive.
        assert_eq!(nyrqis_compositor_object_count(), 2);
    }

    #[test]
    fn event_too_small_buffer_restores_event() {
        let _g = TEST_LOCK.lock().unwrap();
        reset_state();
        reset_event_loop_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        let req = enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(1, req.as_ptr(), req.len())
        };
        assert_eq!(n, req.len() as c_int);
        // Capacity 4 < any global event (>= 12 bytes): rejected, event
        // kept.
        let mut tiny = [0u8; 4];
        let n = unsafe { nyrqis_compositor_next_event(1, tiny.as_mut_ptr(), 4) };
        assert_eq!(n, -1);
        // A properly sized drain still gets all events.
        let events = unsafe { drain_events(1) };
        assert_eq!(events.len(), ADVERTISED_GLOBALS.len());
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

}
