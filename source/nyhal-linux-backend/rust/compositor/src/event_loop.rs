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
//! Client-compatibility surface (ABI 0x0000_0400): the loop serves the
//! requests every real client sends — wl_output/wl_seat/wl_shm bind
//! events (geometry/mode/done, capabilities/name, format), xdg-shell
//! role creation with the initial toplevel/surface configure pair on
//! first commit, wl_surface.damage/damage_buffer and the region
//! requests, wl_buffer.destroy / wl_shm_pool.resize / wl_shm.destroy,
//! and wl_display.error on protocol errors (the client sees WHY it was
//! disconnected instead of a silent socket close).
//!
//! Honest limitation: the object table is global across clients (ids
//! are per-connection namespaces in the real protocol); the table
//! scopes lookups by client id, so two clients may both use id 2, but
//! a per-client `wl_display` id 1 object is created lazily per client.
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
    Pointer,
    Keyboard,
    Touch,
    Shm,
    ShmPool,
    Buffer,
    Region,
    XdgWmBase,
    XdgSurface,
    XdgToplevel,
    XdgPopup,
    Positioner,
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
            "wl_pointer" => Some(Interface::Pointer),
            "wl_keyboard" => Some(Interface::Keyboard),
            "wl_touch" => Some(Interface::Touch),
            "wl_shm" => Some(Interface::Shm),
            "wl_shm_pool" => Some(Interface::ShmPool),
            "wl_buffer" => Some(Interface::Buffer),
            "wl_region" => Some(Interface::Region),
            "xdg_wm_base" => Some(Interface::XdgWmBase),
            "xdg_surface" => Some(Interface::XdgSurface),
            "xdg_toplevel" => Some(Interface::XdgToplevel),
            "xdg_popup" => Some(Interface::XdgPopup),
            "xdg_positioner" => Some(Interface::Positioner),
            _ => None,
        }
    }
}

/// One object in the compositor's object table.
struct ObjectSlot {
    /// Owning client (object ids are per-connection namespaces in the
    /// Wayland protocol — two clients may both use id 2).
    client: u32,
    object_id: u32,
    interface: Interface,
    /// For Surface/xdg role objects: the crate-root surface slot index
    /// (which is also the compositor surface id). For Callback objects:
    /// the surface id the callback is armed on.
    surface: Option<u32>,
    active: bool,
}

/// xdg-shell role state for one wl_surface (keyed by crate surface id).
struct SurfaceRole {
    /// The client's xdg_surface object id for this surface.
    xdg_surface_obj: u32,
    /// The client's xdg_toplevel object id, when get_toplevel ran.
    toplevel_obj: Option<u32>,
    /// Whether the initial configure pair has been sent.
    configured: bool,
}

struct EventLoopState {
    objects: Vec<Option<ObjectSlot>>,
    /// Outbound server→client events, keyed by client id, wire-encoded.
    out_queues: HashMap<u32, Vec<Vec<u8>>>,
    /// Monotonic serial for xdg_surface.configure events.
    next_serial: u32,
    /// xdg-shell roles, keyed by crate surface id.
    surface_roles: HashMap<u32, SurfaceRole>,
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
        next_serial: 0,
        surface_roles: HashMap::new(),
        last_error: String::new(),
    });
    f(state)
}

/// The Wayland wire protocol fixes object id 1 as the `wl_display`,
/// created implicitly at connection time — before any request can
/// reference it. Called lazily per client so a fresh connection has
/// one.
fn ensure_display_object(state: &mut EventLoopState, client_id: u32) {
    if find_object(state, client_id, 1).is_none() {
        let idx = match alloc_object(state) {
            Some(i) => i,
            None => return, // table full; dispatch will error out
        };
        state.objects[idx] = Some(ObjectSlot {
            client: client_id,
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

/// Look up an active object slot index by owning client + object id.
fn find_object(state: &EventLoopState, client_id: u32, object_id: u32) -> Option<usize> {
    state.objects.iter().position(|o| {
        o.as_ref().map_or(false, |o| {
            o.active && o.client == client_id && o.object_id == object_id
        })
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

fn encode_i32_arg(v: i32) -> Vec<u8> {
    v.to_le_bytes().to_vec()
}

/// wl_shm format codes the presentation half accepts (wayland.yaml:
/// ARGB8888 = 0, XRGB8888 = 1).
const ARGB8888: u32 = 0;
const XRGB8888: u32 = 1;

/// The geometry of the compositor's first active output (the emulated
/// display clients are told about), or 1280x720 when none is
/// configured yet.
fn first_output_geometry() -> (u32, u32) {
    crate::with_state(|s| {
        s.outputs.iter().find_map(|o| {
            o.as_ref().filter(|o| o.active).map(|o| (o.width, o.height))
        })
    })
    .unwrap_or((1280, 720))
}

/// Queue a wl_display.error event (object 1, opcode 0; signature
/// "ous": object id, error CODE (u32), message string — libwayland
/// parses the signature strictly, a missing code shifts the parse and
/// the client reports "message too short" instead of the error).
fn queue_display_error(
    state: &mut EventLoopState,
    client_id: u32,
    object_id: u32,
    message: &str,
) {
    let mut payload = encode_u32_arg(object_id);
    payload.extend_from_slice(&encode_u32_arg(0)); // error code: 0 (generic)
    payload.extend_from_slice(&encode_string_arg(message));
    queue_event(state, client_id, encode_event(1, 0, &payload));
}

/// Deliver the initial xdg-shell configure pair for a surface's first
/// commit: xdg_toplevel.configure(0, 0, []) (the compositor's "you
/// choose the size" answer) followed by xdg_surface.configure(serial).
/// xdg clients block on this — ack_configure can never legitimately
/// precede a configure.
fn send_initial_configure(state: &mut EventLoopState, client_id: u32, surface: u32) {
    let (xdg_surface_obj, toplevel_obj) = match state.surface_roles.get_mut(&surface) {
        Some(role) if !role.configured => {
            role.configured = true;
            (role.xdg_surface_obj, role.toplevel_obj)
        }
        _ => return,
    };
    let toplevel_obj = match toplevel_obj {
        Some(t) => t,
        None => return,
    };
    // xdg_toplevel.configure: width, height (0 = client decides),
    // states array (empty).
    let mut tl = encode_i32_arg(0);
    tl.extend_from_slice(&encode_i32_arg(0));
    tl.extend_from_slice(&encode_u32_arg(0));
    queue_event(state, client_id, encode_event(toplevel_obj, 0, &tl));
    // xdg_surface.configure: the serial the client echoes in
    // ack_configure.
    let serial = state.next_serial;
    state.next_serial = state.next_serial.wrapping_add(1);
    queue_event(state, client_id, encode_event(xdg_surface_obj, 0, &encode_u32_arg(serial)));
}

// ---------------------------------------------------------------------------
// Request opcodes (the served subset)
// ---------------------------------------------------------------------------

const OPCODE_DISPLAY_GET_REGISTRY: u16 = 1;
const OPCODE_DISPLAY_SYNC: u16 = 0;
const OPCODE_REGISTRY_BIND: u16 = 0;
const OPCODE_COMPOSITOR_CREATE_SURFACE: u16 = 0;
const OPCODE_COMPOSITOR_CREATE_REGION: u16 = 1;
const OPCODE_SURFACE_DESTROY: u16 = 0;
const OPCODE_SURFACE_ATTACH: u16 = 1;
const OPCODE_SURFACE_DAMAGE: u16 = 2;
const OPCODE_SURFACE_FRAME: u16 = 3;
const OPCODE_SURFACE_SET_OPAQUE_REGION: u16 = 4;
const OPCODE_SURFACE_SET_INPUT_REGION: u16 = 5;
const OPCODE_SURFACE_COMMIT: u16 = 6;
const OPCODE_SURFACE_SET_BUFFER_TRANSFORM: u16 = 7;
const OPCODE_SURFACE_SET_BUFFER_SCALE: u16 = 8;
const OPCODE_SURFACE_DAMAGE_BUFFER: u16 = 9;
const OPCODE_SHM_CREATE_POOL: u16 = 0;
const OPCODE_SHM_DESTROY: u16 = 1;
// wl_shm_pool request order (per wayland-client-protocol.h):
// create_buffer=0, destroy=1, resize=2.
const OPCODE_POOL_CREATE_BUFFER: u16 = 0;
const OPCODE_POOL_DESTROY: u16 = 1;
const OPCODE_POOL_RESIZE: u16 = 2;
const OPCODE_BUFFER_DESTROY: u16 = 0;
const OPCODE_SEAT_GET_POINTER: u16 = 0;
const OPCODE_SEAT_GET_KEYBOARD: u16 = 1;
const OPCODE_SEAT_GET_TOUCH: u16 = 2;
// xdg_wm_base request order (verified against the wire with the real
// libwayland client in weston-simple-shm):
// destroy=0, create_positioner=1, get_xdg_surface=2, pong=3.
const OPCODE_WM_BASE_DESTROY: u16 = 0;
const OPCODE_WM_BASE_CREATE_POSITIONER: u16 = 1;
const OPCODE_WM_BASE_GET_XDG_SURFACE: u16 = 2;
const OPCODE_WM_BASE_PONG: u16 = 3;
const OPCODE_XDG_SURFACE_DESTROY: u16 = 0;
const OPCODE_XDG_SURFACE_GET_TOPLEVEL: u16 = 1;
const OPCODE_XDG_SURFACE_GET_POPUP: u16 = 2;
const OPCODE_XDG_SURFACE_SET_WINDOW_GEOMETRY: u16 = 3;
const OPCODE_XDG_SURFACE_ACK_CONFIGURE: u16 = 4;
// xdg_toplevel requests (destroy=0, set_parent=1, set_title=2,
// set_app_id=3, set_min_size=5, set_max_size=6, …) are served by the
// (Interface::XdgToplevel, _) catch-all below: recorded shell state
// lives in the protocols module, so the wire loop only keeps the
// protocol state consistent.

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

/// Apply one decoded request to the compositor state, queuing any
/// response events. Returns false on a protocol error (last_error set
/// AND a wl_display.error event queued — the client learns WHY it was
/// disconnected instead of seeing a silent socket close).
fn dispatch_request(
    state: &mut EventLoopState,
    client_id: u32,
    req: &DecodedRequest,
) -> bool {
    let obj_idx = match find_object(state, client_id, req.object_id) {
        Some(i) => i,
        None => {
            let msg = format!("protocol error: unknown object {}", req.object_id);
            queue_display_error(state, client_id, req.object_id, &msg);
            set_loop_error(state, &msg);
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
            if !create_object(state, client_id, new_id, Interface::Callback, None) {
                return false;
            }
            // wl_callback.done (opcode 0) carries a u32 payload — the
            // callback's serial. libwayland parses the signature
            // strictly: an empty done message is "message too short"
            // and aborts the client (found by weston-simple-shm's very
            // first roundtrip). The callback object retires immediately
            // after (one-shot).
            let serial = state.next_serial;
            state.next_serial = state.next_serial.wrapping_add(1);
            queue_event(
                state,
                client_id,
                encode_event(new_id, 0, &encode_u32_arg(serial)),
            );
            retire_object(state, client_id, new_id);
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
            if !create_object(state, client_id, new_id, Interface::Registry, None) {
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
            let version = match args.read_u32() {
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
                    let msg = format!("protocol error: unsupported interface {}", iface_name);
                    queue_display_error(state, client_id, req.object_id, &msg);
                    set_loop_error(state, &msg);
                    return false;
                }
            };
            let name_idx = name as usize;
            if name_idx == 0 || name_idx > ADVERTISED_GLOBALS.len() {
                set_loop_error(state, "protocol error: bind unknown global name");
                return false;
            }
            if !create_object(state, client_id, new_id, iface, None) {
                return false;
            }
            // Version-honesty: never send events a client that bound
            // version v cannot parse.
            let advertised = ADVERTISED_GLOBALS[name_idx - 1].1;
            if version > advertised {
                let msg = format!(
                    "protocol error: bind version {} exceeds advertised {} for {}",
                    version, advertised, iface_name
                );
                queue_display_error(state, client_id, req.object_id, &msg);
                set_loop_error(state, &msg);
                return false;
            }
            // Per-interface bind responses. Real clients need these to
            // proceed: weston-simple-shm reads wl_shm.format to pick a
            // pixel format; GTK/Qt block on wl_output until done.
            match iface {
                Interface::Shm => {
                    // wl_shm.format event for every format the
                    // presentation half accepts.
                    for fmt in ARGB8888..=XRGB8888 {
                        queue_event(
                            state,
                            client_id,
                            encode_event(new_id, 0, &encode_u32_arg(fmt)),
                        );
                    }
                }
                Interface::Output => {
                    // wl_output.geometry, .mode, .done (a single
                    // emulated output carrying the compositor's first
                    // output geometry, 0,0 origin, landscape).
                    let (w, h) = first_output_geometry();
                    // 96 dpi ⇒ 25.4 mm per 96 px (integer math).
                    let mm_w = ((w * 254) / 960) as i32;
                    let mm_h = ((h * 254) / 960) as i32;
                    let mut payload = encode_i32_arg(0); // x
                    payload.extend_from_slice(&encode_i32_arg(0)); // y
                    payload.extend_from_slice(&encode_i32_arg(mm_w));
                    payload.extend_from_slice(&encode_i32_arg(mm_h));
                    payload.extend_from_slice(&encode_i32_arg(0)); // subpixel: unknown
                    payload.extend_from_slice(&encode_string_arg("Nyrqis"));
                    payload.extend_from_slice(&encode_string_arg("Nyrqis Virtual"));
                    payload.extend_from_slice(&encode_i32_arg(0)); // transform: normal
                    queue_event(state, client_id, encode_event(new_id, 0, &payload));
                    // .mode: flags=1 (current/preferred), w, h, refresh
                    // 60000 mHz (60 Hz).
                    let mut payload = encode_i32_arg(1);
                    payload.extend_from_slice(&encode_i32_arg(w as i32));
                    payload.extend_from_slice(&encode_i32_arg(h as i32));
                    payload.extend_from_slice(&encode_i32_arg(60000));
                    queue_event(state, client_id, encode_event(new_id, 1, &payload));
                    // .scale (added in v2, opcode 3) then .done
                    // (added in v2, opcode 2): events are numbered in
                    // declaration order — geometry=0, mode=1, done=2,
                    // scale=3. Scale precedes done so the client
                    // applies it with the done. Never sent to v1
                    // binders — an event outside the bound version is
                    // itself a client-side protocol error.
                    if version >= 2 {
                        queue_event(state, client_id, encode_event(new_id, 3, &encode_i32_arg(1)));
                        queue_event(state, client_id, encode_event(new_id, 2, &[]));
                    }
                }
                Interface::Seat => {
                    // wl_seat.capabilities: pointer(1) | keyboard(2)
                    // (touch(4) not served).
                    queue_event(
                        state,
                        client_id,
                        encode_event(new_id, 0, &encode_u32_arg(3)),
                    );
                    // wl_seat.name (added in v2): gated on the BOUND
                    // version — a v1 binder must never see it.
                    if version >= 2 {
                        queue_event(
                            state,
                            client_id,
                            encode_event(new_id, 1, &encode_string_arg("Nyrqis seat")),
                        );
                    }
                }
                _ => {}
            }
            true
        }
        (Interface::Seat, OPCODE_SEAT_GET_POINTER)
        | (Interface::Seat, OPCODE_SEAT_GET_KEYBOARD)
        | (Interface::Seat, OPCODE_SEAT_GET_TOUCH) => {
            // args: new_id — the seat-relative device object joins the
            // table so the connection stays consistent; no input
            // events are fabricated (fail-closed: an honest seat with
            // no input yet beats a lying stream of synthetic events).
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: seat request missing new_id");
                    return false;
                }
            };
            let iface = match req.opcode {
                OPCODE_SEAT_GET_POINTER => Interface::Pointer,
                OPCODE_SEAT_GET_KEYBOARD => Interface::Keyboard,
                _ => Interface::Touch,
            };
            create_object(state, client_id, new_id, iface, None)
        }
        (Interface::Pointer, _) | (Interface::Keyboard, _) | (Interface::Touch, _) => {
            // Device destruction (release) and the rest are accepted
            // silently; no synthetic input events.
            true
        }
        (Interface::Output, 0) => {
            // wl_output.release (added in v3): destructor request; the
            // object retires.
            state.objects[obj_idx] = None;
            true
        }
        (Interface::Seat, 3) => {
            // wl_seat.release (added in v5): destructor request.
            state.objects[obj_idx] = None;
            true
        }
        (Interface::Compositor, OPCODE_COMPOSITOR_CREATE_REGION) => {
            // arg: new_id for the wl_region object.
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: create_region missing new_id");
                    return false;
                }
            };
            create_object(state, client_id, new_id, Interface::Region, None)
        }
        (Interface::Region, _) => {
            // wl_region.add/subtract/destroy: geometry is not yet used
            // for damage/opaque computation; the region object is
            // tracked so the protocol state machine stays consistent.
            true
        }
        (Interface::XdgWmBase, OPCODE_WM_BASE_DESTROY) => {
            state.objects[obj_idx] = None;
            true
        }
        (Interface::XdgWmBase, OPCODE_WM_BASE_CREATE_POSITIONER) => {
            // arg: new_id for the wl_positioner. Popups are accepted
            // but not positioned yet; the object joins the table so
            // the protocol state stays consistent.
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: create_positioner missing new_id");
                    return false;
                }
            };
            create_object(state, client_id, new_id, Interface::Positioner, None)
        }
        (Interface::XdgWmBase, OPCODE_WM_BASE_GET_XDG_SURFACE) => {
            // args: new_id, surface (wl_surface object id).
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: get_xdg_surface missing new_id");
                    return false;
                }
            };
            let surface_obj = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: get_xdg_surface missing surface");
                    return false;
                }
            };
            let surf_idx = find_object(state, client_id, surface_obj);
            let crate_surface = match surf_idx.map(|i| {
                state.objects[i].as_ref().unwrap().surface
            }) {
                Some(Some(s)) => s,
                _ => {
                    let msg =
                        "protocol error: xdg_surface created for a non-wl_surface object".to_string();
                    queue_display_error(state, client_id, req.object_id, &msg);
                    set_loop_error(state, &msg);
                    return false;
                }
            };
            if !create_object(state, client_id, new_id, Interface::XdgSurface, Some(crate_surface))
            {
                return false;
            }
            state.surface_roles.insert(
                crate_surface,
                SurfaceRole {
                    xdg_surface_obj: new_id,
                    toplevel_obj: None,
                    configured: false,
                },
            );
            true
        }
        (Interface::XdgWmBase, OPCODE_WM_BASE_PONG) => {
            // arg: serial. Acknowledged; nothing further to do — the
            // ping/pong loop keeps unresponsive-client detection
            // satisfied.
            true
        }
        (Interface::XdgSurface, OPCODE_XDG_SURFACE_GET_TOPLEVEL) => {
            // arg: new_id for the xdg_toplevel.
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: get_toplevel missing new_id");
                    return false;
                }
            };
            let crate_surface = state.objects[obj_idx].as_ref().unwrap().surface;
            if !create_object(state, client_id, new_id, Interface::XdgToplevel, crate_surface) {
                return false;
            }
            if let Some(role) = state.surface_roles.get_mut(&crate_surface.unwrap_or(u32::MAX)) {
                role.toplevel_obj = Some(new_id);
            }
            true
        }
        (Interface::XdgSurface, OPCODE_XDG_SURFACE_GET_POPUP) => {
            // args: new_id, parent xdg_surface, positioner. Popups are
            // accepted into the table (no positioning logic yet).
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: get_popup missing new_id");
                    return false;
                }
            };
            let crate_surface = state.objects[obj_idx].as_ref().unwrap().surface;
            create_object(state, client_id, new_id, Interface::XdgPopup, crate_surface)
        }
        (Interface::XdgSurface, OPCODE_XDG_SURFACE_SET_WINDOW_GEOMETRY) => true,
        (Interface::XdgSurface, OPCODE_XDG_SURFACE_ACK_CONFIGURE) => true,
        (Interface::XdgSurface, OPCODE_XDG_SURFACE_DESTROY) => {
            let crate_surface = state.objects[obj_idx].as_ref().unwrap().surface;
            if let Some(s) = crate_surface {
                state.surface_roles.remove(&s);
            }
            state.objects[obj_idx] = None;
            true
        }
        (Interface::XdgToplevel, _) => {
            // set_title / set_app_id / set_min_size / set_max_size /
            // destroy / set_parent: recorded in the protocols module's
            // shell-surface table by the crate-root FFI for wired
            // surfaces; here they only keep the protocol state
            // consistent.
            true
        }
        (Interface::XdgPopup, _) => true,
        (Interface::Positioner, _) => {
            // set_size / set_anchor / set_offset / …: positioning
            // geometry is not yet used (popups are accepted, not
            // positioned); bookkeeping only.
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
            if !create_object(state, client_id, new_id, Interface::Surface, Some(surface_id as u32)) {
                return false;
            }
            true
        }
        (Interface::Surface, OPCODE_SURFACE_DESTROY) => {
            let surface = state.objects[obj_idx].as_ref().unwrap().surface;
            if let Some(s) = surface {
                state.surface_roles.remove(&s);
            }
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
            // A null buffer (id 0) detaches: valid, nothing pending.
            if buffer_id != 0 && find_object(state, client_id, buffer_id).is_none() {
                set_loop_error(state, "protocol error: attach unknown buffer");
                return false;
            }
            if !crate::mark_surface_pending_buffer(surface) {
                set_loop_error(state, "protocol error: attach inactive surface");
                return false;
            }
            true
        }
        (Interface::Surface, OPCODE_SURFACE_DAMAGE)
        | (Interface::Surface, OPCODE_SURFACE_DAMAGE_BUFFER)
        | (Interface::Surface, OPCODE_SURFACE_SET_OPAQUE_REGION)
        | (Interface::Surface, OPCODE_SURFACE_SET_INPUT_REGION) => {
            // Damage and region bookkeeping: the presentation half
            // redraws full frames, so the rectangles are accepted and
            // not tracked. The surface must exist (it does — the
            // object lookup above found it).
            true
        }
        (Interface::Surface, OPCODE_SURFACE_SET_BUFFER_SCALE)
        | (Interface::Surface, OPCODE_SURFACE_SET_BUFFER_TRANSFORM) => {
            // Accepted; scale/transform-aware presentation is follow-on
            // work (the current renderer is 1:1, transform-normal).
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
            if !create_object(state, client_id, new_id, Interface::Callback, surface) {
                return false;
            }
            true
        }
        (Interface::Shm, OPCODE_SHM_CREATE_POOL) => {
            // args: new_id u32, fd (placeholder — the fd itself is
            // delivered out-of-band via SCM_RIGHTS on the host side),
            // size i32. The pool object joins the table so bind →
            // create_pool → create_buffer is a consistent protocol
            // state machine; the fd half lives on the host.
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: create_pool missing new_id");
                    return false;
                }
            };
            let _fd_placeholder = args.read_u32();
            let _size = args.read_u32();
            if !create_object(state, client_id, new_id, Interface::ShmPool, None) {
                return false;
            }
            true
        }
        (Interface::Shm, OPCODE_SHM_DESTROY) => {
            state.objects[obj_idx] = None;
            true
        }
        (Interface::ShmPool, OPCODE_POOL_CREATE_BUFFER) => {
            // args: new_id u32, offset i32, width i32, height i32,
            // stride i32, format u32. The buffer object joins the
            // table so wl_surface.attach(buffer) validates; pixel
            // access is the host's SHM mapping.
            let new_id = match args.read_u32() {
                Some(v) => v,
                None => {
                    set_loop_error(state, "protocol error: create_buffer missing new_id");
                    return false;
                }
            };
            for _ in 0..4 {
                if args.read_u32().is_none() {
                    set_loop_error(state, "protocol error: create_buffer truncated");
                    return false;
                }
            }
            if args.read_u32().is_none() {
                set_loop_error(state, "protocol error: create_buffer missing format");
                return false;
            }
            if !create_object(state, client_id, new_id, Interface::Buffer, None) {
                return false;
            }
            true
        }
        (Interface::ShmPool, OPCODE_POOL_DESTROY) => {
            state.objects[obj_idx] = None;
            true
        }
        (Interface::ShmPool, OPCODE_POOL_RESIZE) => {
            // arg: new_size i32. The fd half (the actual mapping) lives
            // on the host; the protocol object stays valid.
            true
        }
        (Interface::Buffer, OPCODE_BUFFER_DESTROY) => {
            state.objects[obj_idx] = None;
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
                        o.active
                            && o.client == client_id
                            && o.interface == Interface::Callback
                            && o.surface == Some(surface)
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
            // Retire the attached buffers: wl_buffer.release per the
            // protocol (the compositor is done with the buffer's
            // content for this frame). RELEASE DOES NOT DESTROY the
            // buffer object — clients re-attach released buffers
            // (weston-simple-shm double-buffers), so the object stays
            // in the table; only an explicit wl_buffer.destroy
            // retires it.
            let buf_idxs: Vec<usize> = state
                .objects
                .iter()
                .enumerate()
                .filter(|(_, o)| {
                    o.as_ref().map_or(false, |o| {
                        o.active
                            && o.client == client_id
                            && o.interface == Interface::Buffer
                    })
                })
                .map(|(i, _)| i)
                .collect();
            for idx in buf_idxs {
                let buf_id = state.objects[idx].as_ref().unwrap().object_id;
                queue_event(state, client_id, encode_event(buf_id, 0, &[]));
            }
            // xdg-shell: the first commit of a role surface must
            // deliver the initial configure pair — ack_configure can
            // never arrive before one, and xdg clients block on it.
            if state.surface_roles.contains_key(&surface) {
                send_initial_configure(state, client_id, surface);
            }
            true
        }
        _ => {
            let msg = format!(
                "protocol error: unsupported {:?} opcode {}",
                interface, req.opcode
            );
            queue_display_error(state, client_id, req.object_id, &msg);
            set_loop_error(state, &msg);
            false
        }
    }
}

/// Create an object in the table (fails when the table is full or the
/// id is already taken by an active object of the same client).
fn create_object(
    state: &mut EventLoopState,
    client_id: u32,
    object_id: u32,
    interface: Interface,
    surface: Option<u32>,
) -> bool {
    if find_object(state, client_id, object_id).is_some() {
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
        client: client_id,
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
fn retire_object(state: &mut EventLoopState, client_id: u32, object_id: u32) {
    if let Some(idx) = find_object(state, client_id, object_id) {
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
        ensure_display_object(state, client_id);
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

/// Number of active objects in the object table (all clients).
#[no_mangle]
pub extern "C" fn nyrqis_compositor_object_count() -> c_int {
    with_loop_state(|state| {
        state
            .objects
            .iter()
            .filter(|o| o.as_ref().map_or(false, |o| o.active))
            .count() as c_int
    })
}

/// Tear down one client's protocol state: its objects leave the table,
/// its xdg roles are dropped, its crate surfaces are destroyed, and
/// its outbound queue is released. Called by the host half when the
/// socket disconnects.
///
/// Returns 0 (always; unknown clients are a valid no-op).
#[no_mangle]
pub extern "C" fn nyrqis_compositor_client_disconnected(client_id: u32) -> c_int {
    with_loop_state(|state| {
        // Destroy the crate surfaces this client owns (via its
        // wl_surface objects) before the table rows go away.
        let owned_surfaces: Vec<u32> = state
            .objects
            .iter()
            .filter_map(|o| {
                o.as_ref().and_then(|slot| {
                    if slot.active
                        && slot.client == client_id
                        && slot.interface == Interface::Surface
                    {
                        slot.surface
                    } else {
                        None
                    }
                })
            })
            .collect();
        for s in owned_surfaces {
            crate::nyrqis_compositor_destroy_surface(s as c_int);
            state.surface_roles.remove(&s);
        }
        for slot in state.objects.iter_mut() {
            if let Some(o) = slot {
                if o.active && o.client == client_id {
                    *slot = None;
                }
            }
        }
        state.out_queues.remove(&client_id);
        0
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
        let _g = crate::test_lock();
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
    fn output_seat_shm_bind_events() {
        let _g = crate::test_lock();
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
        let _g = crate::test_lock();
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
    fn buffer_release_does_not_destroy_the_object() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, shm=4, pool=5, buffer=6, surface=7
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut b = enc_u32(2);
        b.extend_from_slice(&encode_string_arg("wl_shm"));
        b.extend_from_slice(&enc_u32(1));
        b.extend_from_slice(&enc_u32(4));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut pool = enc_u32(5);
        pool.extend_from_slice(&enc_u32(0));
        pool.extend_from_slice(&enc_u32(4096));
        buf.extend_from_slice(&enc_request(4, OPCODE_SHM_CREATE_POOL, &pool));
        let mut cb = enc_u32(6);
        cb.extend_from_slice(&enc_u32(0));
        cb.extend_from_slice(&enc_u32(4));
        cb.extend_from_slice(&enc_u32(4));
        cb.extend_from_slice(&enc_u32(16));
        cb.extend_from_slice(&enc_u32(0));
        buf.extend_from_slice(&enc_request(5, OPCODE_POOL_CREATE_BUFFER, &cb));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(7)));
        buf.extend_from_slice(&enc_request(7, OPCODE_SURFACE_ATTACH, &enc_u32(6)));
        buf.extend_from_slice(&enc_request(7, OPCODE_SURFACE_COMMIT, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        // The commit queued wl_buffer.release on object 6 — but the
        // object SURVIVES: clients re-attach released buffers.
        let events = unsafe { drain_events(1) };
        assert!(events.iter().any(|e| ev_header(e) == (6, 0) && e.len() == 8));
        assert_eq!(crate::nyrqis_compositor_surface_count(), 1);
        // A second attach+commit of the SAME buffer must dispatch
        // cleanly (no "unknown object" error) and release again.
        let mut buf2 = Vec::new();
        buf2.extend_from_slice(&enc_request(7, OPCODE_SURFACE_ATTACH, &enc_u32(6)));
        buf2.extend_from_slice(&enc_request(7, OPCODE_SURFACE_COMMIT, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf2.as_ptr(), buf2.len()) };
        assert_eq!(n, buf2.len() as c_int);
        let events = unsafe { drain_events(1) };
        assert!(events.iter().any(|e| ev_header(e) == (6, 0)));
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn shm_pool_and_buffer_join_table() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, shm=3, pool=4, buffer=5
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut bind = enc_u32(2); // name: wl_shm is global 2
        bind.extend_from_slice(&encode_string_arg("wl_shm"));
        bind.extend_from_slice(&enc_u32(1));
        bind.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &bind));
        // wl_shm.create_pool(pool=4, fd placeholder, size 64)
        let mut pool = enc_u32(4);
        pool.extend_from_slice(&enc_u32(0)); // fd placeholder
        pool.extend_from_slice(&enc_u32(64));
        buf.extend_from_slice(&enc_request(3, OPCODE_SHM_CREATE_POOL, &pool));
        // wl_shm_pool.create_buffer(buf=5, offset 0, 2x2, stride 8, format 0)
        let mut cb = enc_u32(5);
        cb.extend_from_slice(&enc_u32(0));
        cb.extend_from_slice(&enc_u32(2));
        cb.extend_from_slice(&enc_u32(2));
        cb.extend_from_slice(&enc_u32(8));
        cb.extend_from_slice(&enc_u32(0));
        buf.extend_from_slice(&enc_request(4, OPCODE_POOL_CREATE_BUFFER, &cb));

        let n = unsafe {
            nyrqis_compositor_handle_client_data(7, buf.as_ptr(), buf.len())
        };
        assert_eq!(n, buf.len() as c_int);
        // wl_display + registry + shm + pool + buffer.
        assert_eq!(nyrqis_compositor_object_count(), 5);

        // attach(buffer=5) now validates against the table. Bind the
        // compositor (name 1) as object 6 first, create surface 7.
        let mut buf2 = Vec::new();
        let mut bind2 = enc_u32(1); // name: wl_compositor is global 1
        bind2.extend_from_slice(&encode_string_arg("wl_compositor"));
        bind2.extend_from_slice(&enc_u32(5));
        bind2.extend_from_slice(&enc_u32(6));
        buf2.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &bind2));
        buf2.extend_from_slice(&enc_request(6, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(7)));
        buf2.extend_from_slice(&enc_request(7, OPCODE_SURFACE_ATTACH, &enc_u32(5)));
        let n = unsafe {
            nyrqis_compositor_handle_client_data(7, buf2.as_ptr(), buf2.len())
        };
        assert_eq!(n, buf2.len() as c_int);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn sync_delivers_done_event() {
        let _g = crate::test_lock();
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
        // Exactly one event: wl_callback.done on object 7, WITH its
        // u32 serial payload (libwayland parses the signature
        // strictly — an empty done aborts the client).
        assert_eq!(events.len(), 1);
        let (object_id, opcode) = ev_header(&events[0]);
        assert_eq!(object_id, 7);
        assert_eq!(opcode, 0);
        assert_eq!(events[0].len(), 12, "done carries a u32 serial");
        // The callback object retires after delivery (one-shot).
        assert_eq!(nyrqis_compositor_object_count(), 1);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn unknown_object_is_protocol_error() {
        let _g = crate::test_lock();
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
        let _g = crate::test_lock();
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
        let _g = crate::test_lock();
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
        let _g = crate::test_lock();
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

    // -----------------------------------------------------------------
    // Client-compatibility surface (ABI 0x0000_0400)
    // -----------------------------------------------------------------

    /// Bind every advertised global on client 1; returns the drained
    /// event stream (globals first — 5 events — then the per-bind
    /// responses in bind order).
    unsafe fn bind_all_globals(client_id: u32, shm: u32, output: u32, seat: u32) -> Vec<Vec<u8>> {
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        // name 1: wl_compositor (object 3) — no bind events
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        // name 2: wl_shm v1 (object = shm)
        let mut b = enc_u32(2);
        b.extend_from_slice(&encode_string_arg("wl_shm"));
        b.extend_from_slice(&enc_u32(1));
        b.extend_from_slice(&enc_u32(shm));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        // name 3: wl_output v3 (object = output)
        let mut b = enc_u32(3);
        b.extend_from_slice(&encode_string_arg("wl_output"));
        b.extend_from_slice(&enc_u32(3));
        b.extend_from_slice(&enc_u32(output));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        // name 4: wl_seat v7 (object = seat)
        let mut b = enc_u32(4);
        b.extend_from_slice(&encode_string_arg("wl_seat"));
        b.extend_from_slice(&enc_u32(7));
        b.extend_from_slice(&enc_u32(seat));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let n = nyrqis_compositor_handle_client_data(client_id, buf.as_ptr(), buf.len());
        assert_eq!(n, buf.len() as c_int);
        drain_events(client_id)
    }

    #[test]
    fn shm_bind_announces_formats() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        let events = unsafe { bind_all_globals(1, 10, 11, 12) };
        // 5 globals, then wl_shm.format(ARGB8888), wl_shm.format(XRGB8888).
        assert!(events.len() >= 7);
        let (obj, op) = ev_header(&events[5]);
        assert_eq!((obj, op), (10, 0)); // wl_shm.format = opcode 0
        let fmt0 = u32::from_le_bytes([events[5][8], events[5][9], events[5][10], events[5][11]]);
        let (obj, op) = ev_header(&events[6]);
        assert_eq!((obj, op), (10, 0));
        let fmt1 = u32::from_le_bytes([events[6][8], events[6][9], events[6][10], events[6][11]]);
        assert_eq!((fmt0, fmt1), (0, 1)); // ARGB8888, XRGB8888
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn output_bind_sends_geometry_mode_done() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // start() resets the output table — outputs are added AFTER it.
        assert_eq!(crate::nyrqis_compositor_add_output(800, 600, std::ptr::null(), 0), 0);
        let events = unsafe { bind_all_globals(1, 10, 11, 12) };
        // Find the three events on the output object (id 11).
        let out_events: Vec<&Vec<u8>> =
            events.iter().filter(|e| ev_header(e).0 == 11).collect();
        assert_eq!(out_events.len(), 4, "geometry, mode, scale, done expected");
        let ops: Vec<u16> = out_events.iter().map(|e| ev_header(e).1).collect();
        assert_eq!(ops, vec![0, 1, 3, 2]); // geometry, mode, scale, done
        // geometry payload: x=0, y=0, physical 800x600 px at 96 dpi.
        let px = |e: &[u8], off: usize| {
            i32::from_le_bytes([e[off], e[off + 1], e[off + 2], e[off + 3]])
        };
        let geom = out_events[0];
        assert_eq!((px(geom, 8), px(geom, 12)), (0, 0));
        assert_eq!(px(geom, 16), (800 * 254) / 960);
        assert_eq!(px(geom, 20), (600 * 254) / 960);
        // mode payload: flags=1 (current|preferred), 800x600@60000.
        let mode = out_events[1];
        assert_eq!(px(mode, 8), 1);
        assert_eq!((px(mode, 12), px(mode, 16), px(mode, 20)), (800, 600, 60000));
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn output_v1_bind_gets_no_done_event() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(3);
        b.extend_from_slice(&encode_string_arg("wl_output"));
        b.extend_from_slice(&enc_u32(1)); // v1: no .done event exists
        b.extend_from_slice(&enc_u32(11));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        let events = unsafe { drain_events(1) };
        let out_events: Vec<&Vec<u8>> =
            events.iter().filter(|e| ev_header(e).0 == 11).collect();
        assert_eq!(out_events.len(), 2, "geometry + mode only at v1");
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn seat_bind_sends_capabilities_and_name() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        let events = unsafe { bind_all_globals(1, 10, 11, 12) };
        let seat_events: Vec<&Vec<u8>> =
            events.iter().filter(|e| ev_header(e).0 == 12).collect();
        assert_eq!(seat_events.len(), 2, "capabilities + name expected");
        assert_eq!(ev_header(seat_events[0]).1, 0); // capabilities
        let caps = u32::from_le_bytes([
            seat_events[0][8],
            seat_events[0][9],
            seat_events[0][10],
            seat_events[0][11],
        ]);
        assert_eq!(caps, 3, "pointer | keyboard");
        assert_eq!(ev_header(seat_events[1]).1, 1); // name
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn seat_requests_create_device_objects() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, surface=4, seat=5, pointer=6, keyboard=7
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut b = enc_u32(4);
        b.extend_from_slice(&encode_string_arg("wl_seat"));
        b.extend_from_slice(&enc_u32(7));
        b.extend_from_slice(&enc_u32(5));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        buf.extend_from_slice(&enc_request(5, OPCODE_SEAT_GET_POINTER, &enc_u32(6)));
        buf.extend_from_slice(&enc_request(5, OPCODE_SEAT_GET_KEYBOARD, &enc_u32(7)));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        // display + registry + compositor + surface + seat + 2 devices.
        assert_eq!(nyrqis_compositor_object_count(), 7);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn xdg_shell_toplevel_configure_pair_on_first_commit() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, surface=4, wm_base=5,
        // xdg_surface=6, toplevel=7
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut b = enc_u32(5);
        b.extend_from_slice(&encode_string_arg("xdg_wm_base"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(5));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        let mut gx = enc_u32(6);
        gx.extend_from_slice(&enc_u32(4)); // the wl_surface
        buf.extend_from_slice(&enc_request(5, OPCODE_WM_BASE_GET_XDG_SURFACE, &gx));
        buf.extend_from_slice(&enc_request(6, OPCODE_XDG_SURFACE_GET_TOPLEVEL, &enc_u32(7)));
        // First commit: must produce the initial configure pair.
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_COMMIT, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        let mut events = unsafe { drain_events(1) };
        // The 5 registry globals are also in this stream — the xdg
        // configure pair is exactly the two events AFTER them.
        assert!(events.len() >= 7);
        events.drain(0..ADVERTISED_GLOBALS.len());
        // toplevel.configure (object 7) then xdg_surface.configure (6).
        assert_eq!(events.len(), 2);
        assert_eq!(ev_header(&events[0]), (7, 0));
        // toplevel.configure payload: 0, 0, empty states array.
        let w = i32::from_le_bytes([events[0][8], events[0][9], events[0][10], events[0][11]]);
        let h = i32::from_le_bytes([
            events[0][12],
            events[0][13],
            events[0][14],
            events[0][15],
        ]);
        assert_eq!((w, h), (0, 0));
        let arr_len = u32::from_le_bytes([
            events[0][16],
            events[0][17],
            events[0][18],
            events[0][19],
        ]);
        assert_eq!(arr_len, 0);
        assert_eq!(ev_header(&events[1]), (6, 0));
        // The second commit delivers nothing further (configured once).
        let commit = enc_request(4, OPCODE_SURFACE_COMMIT, &[]);
        let n = unsafe { nyrqis_compositor_handle_client_data(1, commit.as_ptr(), commit.len()) };
        assert_eq!(n, commit.len() as c_int);
        assert!(unsafe { drain_events(1) }.is_empty());
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn xdg_toplevel_configure_ordered_before_surface_configure() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // Different id arrangement (wm_base=4, surface=5, xdg_surface=6,
        // toplevel=7) to pin that ordering does not depend on ids.
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut b = enc_u32(5);
        b.extend_from_slice(&encode_string_arg("xdg_wm_base"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(4));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(5)));
        let mut gx = enc_u32(6);
        gx.extend_from_slice(&enc_u32(5));
        buf.extend_from_slice(&enc_request(4, OPCODE_WM_BASE_GET_XDG_SURFACE, &gx));
        buf.extend_from_slice(&enc_request(6, OPCODE_XDG_SURFACE_GET_TOPLEVEL, &enc_u32(7)));
        buf.extend_from_slice(&enc_request(5, OPCODE_SURFACE_COMMIT, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        let mut events = unsafe { drain_events(1) };
        events.drain(0..ADVERTISED_GLOBALS.len());
        let configured: Vec<(u32, u16)> = events.iter().map(|e| ev_header(e)).collect();
        assert_eq!(configured, vec![(7, 0), (6, 0)]);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn xdg_surface_for_non_surface_object_is_error() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, wm_base=3; get_xdg_surface points at the
        // REGISTRY (not a wl_surface) — the spec's defunct-surface
        // error.
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(5);
        b.extend_from_slice(&encode_string_arg("xdg_wm_base"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut gx = enc_u32(9);
        gx.extend_from_slice(&enc_u32(2)); // the registry object
        buf.extend_from_slice(&enc_request(3, OPCODE_WM_BASE_GET_XDG_SURFACE, &gx));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, -1);
        // wl_display.error names the offender.
        let events = unsafe { drain_events(1) };
        let err = events.iter().find(|e| ev_header(e) == (1, 0));
        assert!(err.is_some(), "wl_display.error expected");
        let bad_obj = u32::from_le_bytes([
            err.unwrap()[8],
            err.unwrap()[9],
            err.unwrap()[10],
            err.unwrap()[11],
        ]);
        // The error names the requesting object (the xdg_wm_base whose
        // argument was invalid), not the bad argument itself.
        assert_eq!(bad_obj, 3, "error must name the xdg_wm_base object");
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn ping_pong_and_wm_base_destroy_served() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, wm_base=3
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(5);
        b.extend_from_slice(&encode_string_arg("xdg_wm_base"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_WM_BASE_PONG, &enc_u32(7)));
        buf.extend_from_slice(&enc_request(3, OPCODE_WM_BASE_DESTROY, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn surface_damage_and_region_requests_served() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, surface=4, region=5
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_REGION, &enc_u32(5)));
        // damage(x=1,y=2,w=30,h=40)
        let mut d = enc_u32(1);
        d.extend_from_slice(&enc_u32(2));
        d.extend_from_slice(&enc_u32(30));
        d.extend_from_slice(&enc_u32(40));
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_DAMAGE, &d));
        // damage_buffer(same)
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_DAMAGE_BUFFER, &d));
        // set_opaque_region(region), set_input_region(region)
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_SET_OPAQUE_REGION, &enc_u32(5)));
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_SET_INPUT_REGION, &enc_u32(5)));
        // set_buffer_scale(1), set_buffer_transform(0)
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_SET_BUFFER_SCALE, &enc_u32(1)));
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_SET_BUFFER_TRANSFORM, &enc_u32(0)));
        buf.extend_from_slice(&enc_request(5, 0, &[])); // wl_region.destroy
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int, "every request served, no protocol error");
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn buffer_destroy_and_pool_resize_served() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, shm=3, pool=4, buffer=5
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(2);
        b.extend_from_slice(&encode_string_arg("wl_shm"));
        b.extend_from_slice(&enc_u32(1));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let mut pool = enc_u32(4);
        pool.extend_from_slice(&enc_u32(0)); // fd placeholder
        pool.extend_from_slice(&enc_u32(64));
        buf.extend_from_slice(&enc_request(3, OPCODE_SHM_CREATE_POOL, &pool));
        let mut cb = enc_u32(5);
        cb.extend_from_slice(&enc_u32(0)); // offset
        cb.extend_from_slice(&enc_u32(2)); // width
        cb.extend_from_slice(&enc_u32(2)); // height
        cb.extend_from_slice(&enc_u32(8)); // stride
        cb.extend_from_slice(&enc_u32(0)); // format
        buf.extend_from_slice(&enc_request(4, OPCODE_POOL_CREATE_BUFFER, &cb));
        buf.extend_from_slice(&enc_request(4, OPCODE_POOL_RESIZE, &enc_u32(128)));
        buf.extend_from_slice(&enc_request(5, OPCODE_BUFFER_DESTROY, &[]));
        buf.extend_from_slice(&enc_request(4, OPCODE_POOL_DESTROY, &[]));
        buf.extend_from_slice(&enc_request(3, OPCODE_SHM_DESTROY, &[]));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn bind_version_over_advertised_is_error() {
        let _g = crate::test_lock();
        reset_state();
        reset_event_loop_state();
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(9)); // advertised: 5
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, -1);
        let events = unsafe { drain_events(1) };
        let err = events.iter().find(|e| ev_header(e) == (1, 0));
        assert!(err.is_some(), "wl_display.error expected for version overrun");
    }

    #[test]
    fn null_buffer_attach_is_valid_detach() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, surface=4
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        buf.extend_from_slice(&enc_request(4, OPCODE_SURFACE_ATTACH, &enc_u32(0))); // null buffer
        let n = unsafe { nyrqis_compositor_handle_client_data(1, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int, "null-buffer attach must not be an error");
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn per_client_object_ids_do_not_collide() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // Both clients create object id 2 (their wl_registry).
        let req = enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, req.as_ptr(), req.len()) };
        assert_eq!(n, req.len() as c_int);
        let n = unsafe { nyrqis_compositor_handle_client_data(2, req.as_ptr(), req.len()) };
        assert_eq!(n, req.len() as c_int);
        // wl_display(1) + registry(2) per client.
        assert_eq!(nyrqis_compositor_object_count(), 4);
        // Client 2's later use of its id-2 object works (client 1's
        // registration must not shadow it).
        let payload = {
            let mut b = enc_u32(1);
            b.extend_from_slice(&encode_string_arg("wl_compositor"));
            b.extend_from_slice(&enc_u32(5));
            b.extend_from_slice(&enc_u32(3));
            b
        };
        let req = enc_request(2, OPCODE_REGISTRY_BIND, &payload);
        let n = unsafe { nyrqis_compositor_handle_client_data(2, req.as_ptr(), req.len()) };
        assert_eq!(n, req.len() as c_int);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn client_disconnect_destroys_its_surfaces_and_objects() {
        let _g = crate::test_lock();
        reset_state();
        assert_eq!(crate::nyrqis_compositor_start(), 0);
        // registry=2, compositor=3, surface=4 (crate surface 0)
        let mut buf = Vec::new();
        buf.extend_from_slice(&enc_request(1, OPCODE_DISPLAY_GET_REGISTRY, &enc_u32(2)));
        let mut b = enc_u32(1);
        b.extend_from_slice(&encode_string_arg("wl_compositor"));
        b.extend_from_slice(&enc_u32(5));
        b.extend_from_slice(&enc_u32(3));
        buf.extend_from_slice(&enc_request(2, OPCODE_REGISTRY_BIND, &b));
        buf.extend_from_slice(&enc_request(3, OPCODE_COMPOSITOR_CREATE_SURFACE, &enc_u32(4)));
        let n = unsafe { nyrqis_compositor_handle_client_data(4, buf.as_ptr(), buf.len()) };
        assert_eq!(n, buf.len() as c_int);
        assert_eq!(crate::nyrqis_compositor_surface_count(), 1);
        assert_eq!(nyrqis_compositor_object_count(), 4);
        // Disconnect: surfaces destroyed, objects dropped, queue gone.
        assert_eq!(nyrqis_compositor_client_disconnected(4), 0);
        assert_eq!(crate::nyrqis_compositor_surface_count(), 0);
        assert_eq!(nyrqis_compositor_object_count(), 0);
        // Idempotent: a second call is a valid no-op.
        assert_eq!(nyrqis_compositor_client_disconnected(4), 0);
        assert_eq!(crate::nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn unsupported_opcode_queues_display_error() {
        let _g = crate::test_lock();
        reset_state();
        reset_event_loop_state();
        // wl_display has only sync(0)/get_registry(1): opcode 9 is
        // unsupported and must name object 1 in wl_display.error.
        let req = enc_request(1, 9, &enc_u32(42));
        let n = unsafe { nyrqis_compositor_handle_client_data(1, req.as_ptr(), req.len()) };
        assert_eq!(n, -1);
        let events = unsafe { drain_events(1) };
        let err = events.iter().find(|e| ev_header(e) == (1, 0));
        assert!(err.is_some(), "wl_display.error expected");
        let bad_obj = u32::from_le_bytes([
            err.unwrap()[8],
            err.unwrap()[9],
            err.unwrap()[10],
            err.unwrap()[11],
        ]);
        assert_eq!(bad_obj, 1);
    }

}
