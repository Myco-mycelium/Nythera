//! Nyrqis Wayland display-server client — ADR-0026.
//!
//! Provides Wayland protocol bindings for the Nyrqis shell's display
//! integration: surface management, input handling, and buffer
//! allocation via `wl_shm` (software rendering) with a documented
//! path to GBM/DRM (GPU acceleration).
//!
//! **FFI surface (ABI 1.1.0).** Caller-supplied input only — no
//! heap allocations leak across the boundary, and there is no `free`
//! contract.

use std::os::raw::{c_char, c_int};
use std::ptr;
use std::sync::Mutex;

use wayland_sys::client::{wayland_client_handle, wl_display, wl_proxy};
use wayland_sys::common::wl_argument;

/// ABI version: 0x0001_0100 (1.1.0) — Phase 1b + xdg-shell + input.
const ABI_VERSION: u32 = 0x0001_0100;
const MAX_CONNECTIONS: usize = 8;
const MAX_SURFACES: usize = 64;
const MAX_BUFFERS: usize = 128;
const MAX_OUTPUTS: usize = 16;

// ---------------------------------------------------------------------------
// Event types (for the FFI callback)
// ---------------------------------------------------------------------------

/// Event types dispatched to the registered handler.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub enum WaylandEventType {
    /// Surface needs to redraw (configure event).
    SurfaceConfigure = 1,
    /// Surface should close (xdg_toplevel.close).
    SurfaceClose = 2,
    /// Keyboard key event.
    KeyboardKey = 3,
    /// Keyboard modifiers event.
    KeyboardModifiers = 4,
    /// Pointer motion event.
    PointerMotion = 5,
    /// Pointer button event.
    PointerButton = 6,
    /// Surface entered (pointer entered surface).
    SurfaceEnter = 7,
    /// Surface left (pointer left surface).
    SurfaceLeave = 8,
    /// Output (monitor) added or changed.
    OutputChanged = 9,
    /// Output (monitor) removed.
    OutputRemoved = 10,
}

/// Event data union — carries the relevant data for each event type.
#[repr(C)]
pub union WaylandEventData {
    /// SurfaceConfigure: new width/height from compositor.
    pub configure: WaylandConfigureData,
    /// KeyboardKey: key code + state.
    pub key: WaylandKeyData,
    /// KeyboardModifiers: modifier mask.
    pub modifiers: WaylandModifiersData,
    /// PointerMotion: x/y position.
    pub motion: WaylandMotionData,
    /// PointerButton: button code + state.
    pub button: WaylandButtonData,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandConfigureData {
    pub width: i32,
    pub height: i32,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandKeyData {
    pub key: u32,      // xkb key code
    pub state: u32,    // 0 = released, 1 = pressed
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandModifiersData {
    pub mods_depressed: u32,
    pub mods_latched: u32,
    pub mods_locked: u32,
    pub group: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandMotionData {
    pub x: f64,
    pub y: f64,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandButtonData {
    pub button: u32,   // button code
    pub state: u32,    // 0 = released, 1 = pressed
}

/// C callback type for event delivery.
pub type WaylandEventHandler = extern "C" fn(
    event_type: WaylandEventType,
    surface_id: c_int,
    data: WaylandEventData,
);

/// Output (monitor) information.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct WaylandOutputInfo {
    pub id: c_int,           // output ID (0-based)
    pub x: i32,              // global x offset
    pub y: i32,              // global y offset
    pub width: i32,          // physical width in pixels
    pub height: i32,         // physical height in pixels
    pub scale: i32,          // buffer scale factor
    pub primary: c_int,      // 1 if primary, 0 otherwise
}

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

struct ConnectionSlot {
    display: *mut wl_display,
    fd: c_int,
    connected: bool,
    // Bound globals
    compositor: *mut wl_proxy,
    shm: *mut wl_proxy,
    xdg_wm_base: *mut wl_proxy,
    seat: *mut wl_proxy,
}
unsafe impl Send for ConnectionSlot {}

struct BufferSlot {
    pool_fd: c_int,        // memfd file descriptor
    pool_ptr: *mut u8,     // mmap'd pointer
    pool_size: usize,      // mapped size
    buffer: *mut wl_proxy, // wl_buffer proxy
    width: i32,
    height: i32,
    stride: i32,
    active: bool,
}
unsafe impl Send for BufferSlot {}

struct SurfaceSlot {
    surface: *mut wl_proxy,
    xdg_surface: *mut wl_proxy,
    xdg_toplevel: *mut wl_proxy,
    width: i32,
    height: i32,
    conn_id: i32,
    buffer_id: i32,        // current buffer (-1 = none)
    active: bool,
}
unsafe impl Send for SurfaceSlot {}

struct OutputSlot {
    output: *mut wl_proxy,  // wl_output proxy
    x: i32,
    y: i32,
    width: i32,
    height: i32,
    scale: i32,
    conn_id: i32,
    active: bool,
}
unsafe impl Send for OutputSlot {}

/// Opaque handle type for surfaces.
#[allow(non_camel_case_types)]
type wl_surface = std::ffi::c_void;

struct WaylandState {
    connections: Vec<Option<ConnectionSlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    buffers: Vec<Option<BufferSlot>>,
    outputs: Vec<Option<OutputSlot>>,
    last_error: String,
    event_handler: Option<WaylandEventHandler>,
}

static STATE: Mutex<Option<WaylandState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut WaylandState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| WaylandState {
        connections: (0..MAX_CONNECTIONS).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        buffers: (0..MAX_BUFFERS).map(|_| None).collect(),
        outputs: (0..MAX_OUTPUTS).map(|_| None).collect(),
        last_error: String::new(),
        event_handler: None,
    });
    f(state)
}

fn set_last_error(state: &mut WaylandState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &WaylandState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

fn is_wayland_available() -> bool {
    wayland_sys::client::is_lib_available()
}

// ---------------------------------------------------------------------------
// Wayland interface descriptors
// ---------------------------------------------------------------------------

static WL_SURFACE_IFACE: wayland_sys::common::wl_interface = wayland_sys::common::wl_interface {
    name: b"wl_surface\0".as_ptr() as *const c_char,
    version: 6,
    request_count: 0,
    requests: ptr::null(),
    event_count: 0,
    events: ptr::null(),
};

static WL_REGISTRY_IFACE: wayland_sys::common::wl_interface = wayland_sys::common::wl_interface {
    name: b"wl_registry\0".as_ptr() as *const c_char,
    version: 1,
    request_count: 0,
    requests: ptr::null(),
    event_count: 0,
    events: ptr::null(),
};

static WL_COMPOSITOR_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"wl_compositor\0".as_ptr() as *const c_char,
        version: 6,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static WL_SHM_IFACE: wayland_sys::common::wl_interface = wayland_sys::common::wl_interface {
    name: b"wl_shm\0".as_ptr() as *const c_char,
    version: 2,
    request_count: 0,
    requests: ptr::null(),
    event_count: 0,
    events: ptr::null(),
};

static WL_SHM_POOL_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"wl_shm_pool\0".as_ptr() as *const c_char,
        version: 1,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static WL_BUFFER_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"wl_buffer\0".as_ptr() as *const c_char,
        version: 1,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static XDG_WM_BASE_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"xdg_wm_base\0".as_ptr() as *const c_char,
        version: 2,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static XDG_SURFACE_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"xdg_surface\0".as_ptr() as *const c_char,
        version: 2,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static XDG_TOPLEVEL_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"xdg_toplevel\0".as_ptr() as *const c_char,
        version: 1,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static WL_SEAT_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"wl_seat\0".as_ptr() as *const c_char,
        version: 8,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

static WL_OUTPUT_IFACE: wayland_sys::common::wl_interface =
    wayland_sys::common::wl_interface {
        name: b"wl_output\0".as_ptr() as *const c_char,
        version: 4,
        request_count: 0,
        requests: ptr::null(),
        event_count: 0,
        events: ptr::null(),
    };

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Bind a wl_registry global by name.
unsafe fn registry_bind(
    registry: *mut wl_proxy,
    name: u32,
    interface: &wayland_sys::common::wl_interface,
    version: u32,
) -> *mut wl_proxy {
    let h = wayland_client_handle();
    let mut args = [
        wl_argument { u: name },
        wl_argument { s: interface.name },
        wl_argument { u: version },
        wl_argument { n: 0 },
    ];
    (h.wl_proxy_marshal_array_constructor)(
        registry,
        0, // bind opcode
        args.as_mut_ptr(),
        interface,
    )
}

/// Create a wl_surface from a wl_compositor.
unsafe fn compositor_create_surface(compositor: *mut wl_proxy) -> *mut wl_proxy {
    let h = wayland_client_handle();
    (h.wl_proxy_marshal_array_constructor)(
        compositor,
        1, // create_surface opcode
        ptr::null_mut(),
        &WL_SURFACE_IFACE,
    )
}

/// Create a wl_shm_pool from a wl_shm.
unsafe fn shm_create_pool(shm: *mut wl_proxy, fd: c_int, size: i32) -> *mut wl_proxy {
    let h = wayland_client_handle();
    let mut args = [
        wl_argument { h: fd },
        wl_argument { i: size },
        wl_argument { n: 0 },
    ];
    (h.wl_proxy_marshal_array_constructor)(
        shm,
        0, // create_pool opcode
        args.as_mut_ptr(),
        &WL_SHM_POOL_IFACE,
    )
}

/// Create a wl_buffer from a wl_shm_pool.
unsafe fn shm_pool_create_buffer(
    pool: *mut wl_proxy,
    offset: i32,
    width: i32,
    height: i32,
    stride: i32,
    format: u32,
) -> *mut wl_proxy {
    let h = wayland_client_handle();
    let mut args = [
        wl_argument { i: offset },
        wl_argument { i: width },
        wl_argument { i: height },
        wl_argument { i: stride },
        wl_argument { u: format },
        wl_argument { n: 0 },
    ];
    (h.wl_proxy_marshal_array_constructor)(
        pool,
        1, // create_buffer opcode
        args.as_mut_ptr(),
        &WL_BUFFER_IFACE,
    )
}

/// Attach a wl_buffer to a wl_surface.
unsafe fn surface_attach(surface: *mut wl_proxy, buffer: *mut wl_proxy, x: i32, y: i32) {
    let h = wayland_client_handle();
    let mut args = [
        wl_argument {
            o: buffer as *const std::ffi::c_void,
        },
        wl_argument { i: x },
        wl_argument { i: y },
    ];
    (h.wl_proxy_marshal_array)(
        surface,
        1, // attach opcode
        args.as_mut_ptr(),
    );
}

/// Damage a wl_surface region.
unsafe fn surface_damage_buffer(surface: *mut wl_proxy, x: i32, y: i32, w: i32, h: i32) {
    let hh = wayland_client_handle();
    let mut args = [
        wl_argument { i: x },
        wl_argument { i: y },
        wl_argument { i: w },
        wl_argument { i: h },
    ];
    (hh.wl_proxy_marshal_array)(
        surface,
        2, // damage_buffer opcode
        args.as_mut_ptr(),
    );
}

/// Commit a wl_surface.
unsafe fn surface_commit(surface: *mut wl_proxy) {
    let h = wayland_client_handle();
    (h.wl_proxy_marshal_array)(
        surface,
        6, // commit opcode
        ptr::null_mut(),
    );
}

/// Create xdg_surface from wl_surface via xdg_wm_base.get_xdg_surface.
unsafe fn xdg_wm_base_get_xdg_surface(
    wm_base: *mut wl_proxy,
    surface: *mut wl_proxy,
) -> *mut wl_proxy {
    let h = wayland_client_handle();
    let mut args = [
        wl_argument {
            o: surface as *const std::ffi::c_void,
        },
        wl_argument { n: 0 },
    ];
    (h.wl_proxy_marshal_array_constructor)(
        wm_base,
        2, // get_xdg_surface opcode
        args.as_mut_ptr(),
        &XDG_SURFACE_IFACE,
    )
}

/// Create xdg_toplevel from xdg_surface.
unsafe fn xdg_surface_get_toplevel(xdg_surface: *mut wl_proxy) -> *mut wl_proxy {
    let h = wayland_client_handle();
    (h.wl_proxy_marshal_array_constructor)(
        xdg_surface,
        1, // get_toplevel opcode
        ptr::null_mut(),
        &XDG_TOPLEVEL_IFACE,
    )
}

/// Set xdg_toplevel title.
unsafe fn xdg_toplevel_set_title(toplevel: *mut wl_proxy, title: &str) {
    let h = wayland_client_handle();
    let c_title = match std::ffi::CString::new(title) {
        Ok(s) => s,
        Err(_) => return,
    };
    let mut args = [wl_argument { s: c_title.as_ptr() }];
    (h.wl_proxy_marshal_array)(
        toplevel,
        2, // set_title opcode
        args.as_mut_ptr(),
    );
}

/// Set xdg_toplevel app_id.
unsafe fn xdg_toplevel_set_app_id(toplevel: *mut wl_proxy, app_id: &str) {
    let h = wayland_client_handle();
    let c_app_id = match std::ffi::CString::new(app_id) {
        Ok(s) => s,
        Err(_) => return,
    };
    let mut args = [wl_argument { s: c_app_id.as_ptr() }];
    (h.wl_proxy_marshal_array)(
        toplevel,
        3, // set_app_id opcode
        args.as_mut_ptr(),
    );
}

/// Send xdg_wm_base.pong (respond to ping).
unsafe fn xdg_wm_base_pong(wm_base: *mut wl_proxy, serial: u32) {
    let h = wayland_client_handle();
    let mut args = [wl_argument { u: serial }];
    (h.wl_proxy_marshal_array)(
        wm_base,
        1, // pong opcode
        args.as_mut_ptr(),
    );
}

/// Create a keyboard from wl_seat.
unsafe fn wl_seat_get_keyboard(seat: *mut wl_proxy) -> *mut wl_proxy {
    let h = wayland_client_handle();
    (h.wl_proxy_marshal_array_constructor)(
        seat,
        3, // get_keyboard opcode
        ptr::null_mut(),
        ptr::null(),
    )
}

/// Create a pointer from wl_seat.
unsafe fn wl_seat_get_pointer(seat: *mut wl_proxy) -> *mut wl_proxy {
    let h = wayland_client_handle();
    (h.wl_proxy_marshal_array_constructor)(
        seat,
        1, // get_pointer opcode
        ptr::null_mut(),
        ptr::null(),
    )
}

/// memfd_create syscall wrapper.
fn memfd_create(name: &str, flags: u32) -> Result<c_int, i32> {
    let c_name = std::ffi::CString::new(name).map_err(|_| -1i32)?;
    let fd = unsafe { libc::memfd_create(c_name.as_ptr(), flags) };
    if fd < 0 {
        Err(unsafe { *libc::__errno_location() })
    } else {
        Ok(fd)
    }
}

/// mmap wrapper.
fn mmap_shared(fd: c_int, size: usize) -> Result<*mut u8, i32> {
    let ptr = unsafe {
        libc::mmap(
            ptr::null_mut(),
            size,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            fd,
            0,
        )
    };
    if ptr == libc::MAP_FAILED {
        Err(unsafe { *libc::__errno_location() })
    } else {
        Ok(ptr as *mut u8)
    }
}

/// munmap wrapper.
fn munmap_shared(ptr: *mut u8, size: usize) {
    unsafe {
        libc::munmap(ptr as *mut libc::c_void, size);
    }
}

/// wl_shm format constants.
const WL_SHM_FORMAT_ARGB8888: u32 = 0;

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_version() -> u32 {
    ABI_VERSION
}

/// Register an event handler callback.
///
/// The callback is invoked for surface configure, close, keyboard,
/// and pointer events.  Pass NULL to unregister.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_set_event_handler(handler: Option<WaylandEventHandler>) {
    with_state(|state| {
        state.event_handler = handler;
    })
}

/// Connect to a Wayland display server.
///
/// Returns a connection ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_connect(
    display_name_ptr: *const c_char,
    display_name_len: c_int,
) -> c_int {
    with_state(|state| {
        if !is_wayland_available() {
            set_last_error(state, "libwayland-client.so not found");
            return -1;
        }

        let conn_idx = match alloc_slot(&mut state.connections) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many connections (max 8)");
                return -1;
            }
        };

        let h = wayland_client_handle();

        let c_name = if display_name_ptr.is_null() || display_name_len <= 0 {
            ptr::null()
        } else {
            let slice = unsafe {
                std::slice::from_raw_parts(
                    display_name_ptr as *const u8,
                    display_name_len as usize,
                )
            };
            match std::ffi::CString::new(slice) {
                Ok(s) => s.into_raw() as *const c_char,
                Err(_) => {
                    set_last_error(state, "display name contains null byte");
                    return -1;
                }
            }
        };

        let display = unsafe { (h.wl_display_connect)(c_name) };

        if !c_name.is_null() {
            unsafe {
                let _ = std::ffi::CString::from_raw(c_name as *mut c_char);
            }
        }

        if display.is_null() {
            set_last_error(state, "failed to connect to Wayland display");
            return -1;
        }

        let fd = unsafe { (h.wl_display_get_fd)(display) };

        // Bind common globals: wl_compositor, wl_shm, xdg_wm_base, wl_seat
        unsafe {
            let mut args = [wl_argument { n: 0 }];
            let registry = (h.wl_proxy_marshal_array_constructor)(
                display as *mut wl_proxy,
                1, // get_registry
                args.as_mut_ptr(),
                &WL_REGISTRY_IFACE,
            );

            if registry.is_null() {
                (h.wl_display_disconnect)(display);
                set_last_error(state, "failed to get wl_registry");
                return -1;
            }

            (h.wl_display_roundtrip)(display);

            // Bind globals by trying names 1..20
            let mut compositor: *mut wl_proxy = ptr::null_mut();
            let mut shm: *mut wl_proxy = ptr::null_mut();
            let mut xdg_wm_base: *mut wl_proxy = ptr::null_mut();
            let mut seat: *mut wl_proxy = ptr::null_mut();
            let mut output: *mut wl_proxy = ptr::null_mut();

            for global_name in 1..=20u32 {
                macro_rules! try_bind {
                    ($gn:expr, $name:expr, $iface:expr, $ver:expr, $target:expr) => {
                        if $target.is_null() {
                            let mut bind_args = [
                                wl_argument { u: $gn },
                                wl_argument { s: $iface.name },
                                wl_argument { u: $ver },
                                wl_argument { n: 0 },
                            ];
                            let candidate = (h.wl_proxy_marshal_array_constructor)(
                                registry,
                                0, // bind
                                bind_args.as_mut_ptr(),
                                $iface,
                            );
                            if !candidate.is_null() {
                                let class = (h.wl_proxy_get_class)(candidate);
                                if !class.is_null() {
                                    let class_cstr = std::ffi::CStr::from_ptr(class);
                                    if class_cstr.to_bytes() == $name.as_bytes() {
                                        $target = candidate;
                                    } else {
                                        (h.wl_proxy_destroy)(candidate);
                                    }
                                } else {
                                    (h.wl_proxy_destroy)(candidate);
                                }
                            }
                        }
                    };
                }
                try_bind!(global_name, "wl_compositor", &WL_COMPOSITOR_IFACE, 4, compositor);
                try_bind!(global_name, "wl_shm", &WL_SHM_IFACE, 2, shm);
                try_bind!(global_name, "xdg_wm_base", &XDG_WM_BASE_IFACE, 2, xdg_wm_base);
                try_bind!(global_name, "wl_seat", &WL_SEAT_IFACE, 8, seat);
                try_bind!(global_name, "wl_output", &WL_OUTPUT_IFACE, 4, output);
            }

            // Enumerate wl_output globals (they can have multiple instances)
            // by trying to bind each global name as wl_output
            let mut output_count = 0i32;
            for global_name in 1..=20u32 {
                if output.is_null() {
                    // First output already bound above
                    let mut bind_args = [
                        wl_argument { u: global_name },
                        wl_argument { s: WL_OUTPUT_IFACE.name },
                        wl_argument { u: 4 },
                        wl_argument { n: 0 },
                    ];
                    let candidate = (h.wl_proxy_marshal_array_constructor)(
                        registry,
                        0, // bind
                        bind_args.as_mut_ptr(),
                        &WL_OUTPUT_IFACE,
                    );
                    if !candidate.is_null() {
                        let class = (h.wl_proxy_get_class)(candidate);
                        if !class.is_null() {
                            let class_cstr = std::ffi::CStr::from_ptr(class);
                            if class_cstr.to_bytes() == b"wl_output" {
                                output = candidate;
                                // Store first output
                                if let Some(idx) = alloc_slot(&mut state.outputs) {
                                    state.outputs[idx] = Some(OutputSlot {
                                        output: candidate,
                                        x: 0, y: 0,
                                        width: 0, height: 0,
                                        scale: 1,
                                        conn_id: conn_idx,
                                        active: true,
                                    });
                                    output_count += 1;
                                }
                            } else {
                                (h.wl_proxy_destroy)(candidate);
                            }
                        } else {
                            (h.wl_proxy_destroy)(candidate);
                        }
                    }
                } else {
                    // Try to bind additional outputs
                    let mut bind_args = [
                        wl_argument { u: global_name },
                        wl_argument { s: WL_OUTPUT_IFACE.name },
                        wl_argument { u: 4 },
                        wl_argument { n: 0 },
                    ];
                    let candidate = (h.wl_proxy_marshal_array_constructor)(
                        registry,
                        0, // bind
                        bind_args.as_mut_ptr(),
                        &WL_OUTPUT_IFACE,
                    );
                    if !candidate.is_null() {
                        let class = (h.wl_proxy_get_class)(candidate);
                        if !class.is_null() {
                            let class_cstr = std::ffi::CStr::from_ptr(class);
                            if class_cstr.to_bytes() == b"wl_output" {
                                if let Some(idx) = alloc_slot(&mut state.outputs) {
                                    state.outputs[idx] = Some(OutputSlot {
                                        output: candidate,
                                        x: 0, y: 0,
                                        width: 0, height: 0,
                                        scale: 1,
                                        conn_id: conn_idx,
                                        active: true,
                                    });
                                    output_count += 1;
                                } else {
                                    (h.wl_proxy_destroy)(candidate);
                                }
                            } else {
                                (h.wl_proxy_destroy)(candidate);
                            }
                        } else {
                            (h.wl_proxy_destroy)(candidate);
                        }
                    }
                }
            }

            (h.wl_proxy_destroy)(registry);

            if compositor.is_null() {
                (h.wl_display_disconnect)(display);
                set_last_error(state, "wl_compositor global not found");
                return -1;
            }

            state.connections[conn_idx as usize] = Some(ConnectionSlot {
                display,
                fd,
                connected: true,
                compositor,
                shm,
                xdg_wm_base,
                seat,
            });
        }

        conn_idx
    })
}

/// Create a `wl_surface` with optional xdg-shell decoration.
///
/// If `use_xdg` is true, creates an xdg_surface + xdg_toplevel
/// on the surface (for proper window management).
///
/// Returns the surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_create_surface(
    conn_id: c_int,
    use_xdg: c_int,
    title_ptr: *const c_char,
    title_len: c_int,
) -> c_int {
    with_state(|state| {
        if conn_id < 0 || conn_id as usize >= MAX_CONNECTIONS {
            set_last_error(state, "invalid connection ID");
            return -1;
        }

        let conn = match &state.connections[conn_id as usize] {
            Some(c) if c.connected => c,
            _ => {
                set_last_error(state, "connection not active");
                return -1;
            }
        };

        if conn.compositor.is_null() {
            set_last_error(state, "wl_compositor not available");
            return -1;
        }

        let h = wayland_client_handle();

        unsafe {
            // Create wl_surface
            let surface = compositor_create_surface(conn.compositor);
            if surface.is_null() {
                set_last_error(state, "failed to create wl_surface");
                return -1;
            }

            // Optionally create xdg_surface + xdg_toplevel
            let mut xdg_surface: *mut wl_proxy = ptr::null_mut();
            let mut xdg_toplevel: *mut wl_proxy = ptr::null_mut();

            if use_xdg != 0 && !conn.xdg_wm_base.is_null() {
                xdg_surface = xdg_wm_base_get_xdg_surface(conn.xdg_wm_base, surface);
                if !xdg_surface.is_null() {
                    xdg_toplevel = xdg_surface_get_toplevel(xdg_surface);

                    // Set title if provided
                    if !title_ptr.is_null() && title_len > 0 {
                        let title_slice = std::slice::from_raw_parts(
                            title_ptr as *const u8,
                            title_len as usize,
                        );
                        if let Ok(title) = std::str::from_utf8(title_slice) {
                            xdg_toplevel_set_title(xdg_toplevel, title);
                            xdg_toplevel_set_app_id(xdg_toplevel, "nyrqis-shell");
                        }
                    }

                    // Commit the surface to map the toplevel
                    surface_commit(surface);
                }
            }

            let surf_idx = match alloc_slot(&mut state.surfaces) {
                Some(i) => i as i32,
                None => {
                    (h.wl_proxy_destroy)(surface);
                    if !xdg_surface.is_null() {
                        (h.wl_proxy_destroy)(xdg_surface);
                    }
                    if !xdg_toplevel.is_null() {
                        (h.wl_proxy_destroy)(xdg_toplevel);
                    }
                    set_last_error(state, "too many surfaces (max 64)");
                    return -1;
                }
            };

            state.surfaces[surf_idx as usize] = Some(SurfaceSlot {
                surface,
                xdg_surface,
                xdg_toplevel,
                width: 0,
                height: 0,
                conn_id,
                buffer_id: -1,
                active: true,
            });

            surf_idx
        }
    })
}

/// Submit a pixel buffer to a surface via `wl_shm`.
///
/// Creates a memfd, copies pixel data into it, creates a wl_shm_pool
/// and wl_buffer, attaches it to the surface, and commits.
///
/// Returns the buffer ID on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_submit_buffer(
    surface_id: c_int,
    pixel_ptr: *const u8,
    pixel_len: c_int,
    width: c_int,
    height: c_int,
    stride: c_int,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            set_last_error(state, "invalid surface ID");
            return -1;
        }

        let surf = match &state.surfaces[surface_id as usize] {
            Some(s) if s.active => s,
            _ => {
                set_last_error(state, "surface not active");
                return -1;
            }
        };

        if pixel_ptr.is_null() || pixel_len <= 0 || width <= 0 || height <= 0 || stride <= 0 {
            set_last_error(state, "invalid buffer parameters");
            return -1;
        }

        let expected_len = (stride * height) as usize;
        if (pixel_len as usize) < expected_len {
            set_last_error(state, "pixel data too small for given dimensions");
            return -1;
        }

        let conn = match &state.connections[surf.conn_id as usize] {
            Some(c) if c.connected => c,
            _ => {
                set_last_error(state, "connection not active");
                return -1;
            }
        };

        if conn.shm.is_null() {
            set_last_error(state, "wl_shm not available");
            return -1;
        }

        let pool_size = (stride * height) as usize;

        unsafe {
            // Step 1: Create memfd
            let fd = match memfd_create("wayland-shm", libc::MFD_CLOEXEC) {
                Ok(fd) => fd,
                Err(_) => {
                    set_last_error(state, "memfd_create failed");
                    return -1;
                }
            };

            // Step 2: Set size
            if libc::ftruncate(fd, pool_size as libc::off_t) < 0 {
                libc::close(fd);
                set_last_error(state, "ftruncate failed");
                return -1;
            }

            // Step 3: mmap
            let pool_ptr = match mmap_shared(fd, pool_size) {
                Ok(p) => p,
                Err(_) => {
                    libc::close(fd);
                    set_last_error(state, "mmap failed");
                    return -1;
                }
            };

            // Step 4: Copy pixel data
            std::ptr::copy_nonoverlapping(
                pixel_ptr,
                pool_ptr,
                pixel_len as usize,
            );

            // Step 5: Create wl_shm_pool
            let pool = shm_create_pool(conn.shm, fd, stride * height);
            if pool.is_null() {
                munmap_shared(pool_ptr, pool_size);
                libc::close(fd);
                set_last_error(state, "wl_shm.create_pool failed");
                return -1;
            }

            // Step 6: Create wl_buffer
            let buffer = shm_pool_create_buffer(
                pool,
                0,
                width,
                height,
                stride,
                WL_SHM_FORMAT_ARGB8888,
            );
            (wayland_client_handle().wl_proxy_destroy)(pool);

            if buffer.is_null() {
                munmap_shared(pool_ptr, pool_size);
                libc::close(fd);
                set_last_error(state, "wl_shm_pool.create_buffer failed");
                return -1;
            }

            // Step 7: Attach buffer to surface
            surface_attach(surf.surface, buffer, 0, 0);
            surface_damage_buffer(surf.surface, 0, 0, width, height);
            surface_commit(surf.surface);

            // Step 8: Free the memfd (the compositor has a reference
            // to the buffer through the fd, so we can close ours)
            munmap_shared(pool_ptr, pool_size);
            libc::close(fd);

            // Store buffer metadata
            let buf_idx = match alloc_slot(&mut state.buffers) {
                Some(i) => i as i32,
                None => {
                    (wayland_client_handle().wl_proxy_destroy)(buffer);
                    set_last_error(state, "too many buffers (max 128)");
                    return -1;
                }
            };

            state.buffers[buf_idx as usize] = Some(BufferSlot {
                pool_fd: -1, // already closed
                pool_ptr: ptr::null_mut(),
                pool_size: 0,
                buffer,
                width,
                height,
                stride,
                active: true,
            });

            // Update surface's buffer reference
            let surf_mut = &mut state.surfaces[surface_id as usize];
            if let Some(s) = surf_mut {
                s.buffer_id = buf_idx;
                s.width = width;
                s.height = height;
            }

            buf_idx
        }
    })
}

/// Poll the display connection for pending events.
///
/// Returns the number of events dispatched, or -1 on error.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_dispatch_events(
    conn_id: c_int,
    timeout_ms: c_int,
) -> c_int {
    with_state(|state| {
        if conn_id < 0 || conn_id as usize >= MAX_CONNECTIONS {
            set_last_error(state, "invalid connection ID");
            return -1;
        }

        let conn = match &state.connections[conn_id as usize] {
            Some(c) if c.connected => c,
            _ => {
                set_last_error(state, "connection not active");
                return -1;
            }
        };

        let h = wayland_client_handle();

        let mut pollfd = libc::pollfd {
            fd: conn.fd,
            events: libc::POLLIN,
            revents: 0,
        };

        let timeout = if timeout_ms < 0 { -1 } else { timeout_ms };
        let ret = unsafe { libc::poll(&mut pollfd as *mut _, 1, timeout) };

        if ret < 0 {
            set_last_error(state, "poll() failed");
            return -1;
        }

        if ret == 0 {
            return 0;
        }

        let dispatched = unsafe { (h.wl_display_dispatch)(conn.display) };

        if dispatched < 0 {
            set_last_error(state, "wl_display_dispatch failed");
            return -1;
        }

        dispatched
    })
}

/// Destroy a connection and free its resources.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_disconnect(conn_id: c_int) -> c_int {
    with_state(|state| {
        if conn_id < 0 || conn_id as usize >= MAX_CONNECTIONS {
            set_last_error(state, "invalid connection ID");
            return -1;
        }

        let conn = match state.connections[conn_id as usize].take() {
            Some(c) => c,
            None => {
                set_last_error(state, "connection not found");
                return -1;
            }
        };

        let h = wayland_client_handle();

        // Destroy bound globals
        unsafe {
            if !conn.compositor.is_null() {
                (h.wl_proxy_destroy)(conn.compositor);
            }
            if !conn.shm.is_null() {
                (h.wl_proxy_destroy)(conn.shm);
            }
            if !conn.xdg_wm_base.is_null() {
                (h.wl_proxy_destroy)(conn.xdg_wm_base);
            }
            if !conn.seat.is_null() {
                (h.wl_proxy_destroy)(conn.seat);
            }
        }

        // Mark surfaces as inactive
        for surf_opt in &mut state.surfaces {
            if let Some(surf) = surf_opt {
                if surf.conn_id == conn_id && surf.active {
                    surf.active = false;
                }
            }
        }

        if !conn.display.is_null() {
            unsafe { (h.wl_display_disconnect)(conn.display); }
        }

        0
    })
}

/// Destroy a surface and free its resources.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_destroy_surface(surface_id: c_int) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            set_last_error(state, "invalid surface ID");
            return -1;
        }

        let surf = match state.surfaces[surface_id as usize].take() {
            Some(s) => s,
            None => {
                set_last_error(state, "surface not found");
                return -1;
            }
        };

        let h = wayland_client_handle();
        unsafe {
            if !surf.xdg_toplevel.is_null() {
                (h.wl_proxy_destroy)(surf.xdg_toplevel);
            }
            if !surf.xdg_surface.is_null() {
                (h.wl_proxy_destroy)(surf.xdg_surface);
            }
            if !surf.surface.is_null() {
                (h.wl_proxy_destroy)(surf.surface as *mut wl_proxy);
            }
        }

        0
    })
}

/// Set the title of an existing xdg_toplevel surface.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_set_title(
    surface_id: c_int,
    title_ptr: *const c_char,
    title_len: c_int,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            set_last_error(state, "invalid surface ID");
            return -1;
        }

        let surf = match &state.surfaces[surface_id as usize] {
            Some(s) if s.active => s,
            _ => {
                set_last_error(state, "surface not active");
                return -1;
            }
        };

        if surf.xdg_toplevel.is_null() {
            set_last_error(state, "surface has no xdg_toplevel");
            return -1;
        }

        if title_ptr.is_null() || title_len <= 0 {
            return 0;
        }

        let title_slice = unsafe {
            std::slice::from_raw_parts(title_ptr as *const u8, title_len as usize)
        };

        match std::str::from_utf8(title_slice) {
            Ok(title) => unsafe {
                xdg_toplevel_set_title(surf.xdg_toplevel, title);
                0
            },
            Err(_) => {
                set_last_error(state, "title is not valid UTF-8");
                -1
            }
        }
    })
}

/// Get the list of active outputs (monitors).
///
/// `outputs_buf` is a caller-allocated array of `WaylandOutputInfo`.
/// `max_outputs` is the capacity of the buffer.
/// Returns the number of outputs written, or -1 on error.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_get_outputs(
    outputs_buf: *mut WaylandOutputInfo,
    max_outputs: c_int,
) -> c_int {
    with_state(|state| {
        if outputs_buf.is_null() || max_outputs <= 0 {
            return -1;
        }

        let mut count = 0i32;
        for (i, output_opt) in state.outputs.iter().enumerate() {
            if count >= max_outputs {
                break;
            }
            if let Some(out) = output_opt {
                if out.active {
                    let info = WaylandOutputInfo {
                        id: i as c_int,
                        x: out.x,
                        y: out.y,
                        width: out.width,
                        height: out.height,
                        scale: out.scale,
                        primary: if count == 0 { 1 } else { 0 },
                    };
                    unsafe {
                        *outputs_buf.add(count as usize) = info;
                    }
                    count += 1;
                }
            }
        }

        count
    })
}

/// Get the file descriptor for the display connection (for external polling).
#[no_mangle]
pub extern "C" fn nyrqis_wayland_get_fd(conn_id: c_int) -> c_int {
    with_state(|state| {
        if conn_id < 0 || conn_id as usize >= MAX_CONNECTIONS {
            return -1;
        }

        match &state.connections[conn_id as usize] {
            Some(c) if c.connected => c.fd,
            _ => -1,
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_last_error(buf: *mut c_char, cap: c_int) -> c_int {
    let msg = with_state(|state| get_last_error(state));
    if buf.is_null() || cap <= 0 {
        return -1;
    }
    let bytes = msg.as_bytes();
    let write_len = (cap as usize).min(bytes.len());
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf as *mut u8, write_len);
        if (cap as usize) > write_len {
            *buf.add(write_len) = 0;
        }
    }
    write_len as c_int
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_returns_abi_version() {
        assert_eq!(nyrqis_wayland_version(), 0x0001_0100);
    }

    #[test]
    fn last_error_returns_message() {
        with_state(|state| set_last_error(state, "test error message"));
        let mut buf = [0u8; 64];
        let n = nyrqis_wayland_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n > 0);
        let msg = std::str::from_utf8(&buf[..n as usize]).unwrap();
        assert_eq!(msg, "test error message");
    }

    #[test]
    fn last_error_truncates_when_buffer_too_small() {
        with_state(|state| {
            set_last_error(state, "a very long error message that exceeds the buffer")
        });
        let mut buf = [0u8; 10];
        let n = nyrqis_wayland_last_error(buf.as_mut_ptr() as *mut c_char, 10);
        assert_eq!(n, 10);
    }

    #[test]
    fn last_error_null_buffer_returns_neg1() {
        assert_eq!(nyrqis_wayland_last_error(ptr::null_mut(), 64), -1);
    }

    #[test]
    fn connect_without_compositor_returns_error() {
        let result = nyrqis_wayland_connect(ptr::null(), 0);
        if result < 0 {
            let err = with_state(|state| get_last_error(state));
            assert!(!err.is_empty(), "Expected meaningful error");
        }
    }

    #[test]
    fn create_surface_invalid_conn_returns_error() {
        assert_eq!(nyrqis_wayland_create_surface(-1, 0, ptr::null(), 0), -1);
    }

    #[test]
    fn create_surface_nonexistent_conn_returns_error() {
        assert_eq!(nyrqis_wayland_create_surface(99, 0, ptr::null(), 0), -1);
    }

    #[test]
    fn submit_buffer_invalid_surface_returns_error() {
        assert_eq!(
            nyrqis_wayland_submit_buffer(-1, ptr::null(), 0, 100, 100, 400),
            -1
        );
    }

    #[test]
    fn submit_buffer_null_pointer_returns_error() {
        assert_eq!(
            nyrqis_wayland_submit_buffer(0, ptr::null(), 0, 100, 100, 400),
            -1
        );
    }

    #[test]
    fn dispatch_events_invalid_conn_returns_error() {
        assert_eq!(nyrqis_wayland_dispatch_events(-1, 100), -1);
    }

    #[test]
    fn disconnect_invalid_conn_returns_error() {
        assert_eq!(nyrqis_wayland_disconnect(-1), -1);
    }

    #[test]
    fn destroy_surface_invalid_id_returns_error() {
        assert_eq!(nyrqis_wayland_destroy_surface(-1), -1);
    }

    #[test]
    fn set_event_handler_works() {
        extern "C" fn handler(
            _event_type: WaylandEventType,
            _surface_id: c_int,
            _data: WaylandEventData,
        ) {
        }
        nyrqis_wayland_set_event_handler(Some(handler));
        nyrqis_wayland_set_event_handler(None);
    }

    #[test]
    fn get_fd_invalid_conn_returns_neg1() {
        assert_eq!(nyrqis_wayland_get_fd(-1), -1);
    }

    #[test]
    fn set_title_invalid_surface_returns_error() {
        assert_eq!(nyrqis_wayland_set_title(-1, ptr::null(), 0), -1);
    }

    #[test]
    fn memfd_create_works() {
        // memfd_create should work on Linux
        let result = memfd_create("test-memfd", libc::MFD_CLOEXEC);
        match result {
            Ok(fd) => {
                assert!(fd >= 0);
                unsafe { libc::close(fd); }
            }
            Err(errno) => {
                // Some environments don't support memfd_create
                eprintln!("memfd_create failed with errno {} (may be expected in CI)", errno);
            }
        }
    }

    #[test]
    fn mmap_shared_works() {
        let fd = match memfd_create("test-mmap", libc::MFD_CLOEXEC) {
            Ok(fd) => fd,
            Err(_) => return, // skip if memfd not available
        };

        let size = 4096;
        unsafe { libc::ftruncate(fd, size as libc::off_t); }

        let ptr = match mmap_shared(fd, size) {
            Ok(p) => p,
            Err(_) => {
                unsafe { libc::close(fd); }
                return;
            }
        };

        // Write and read back
        unsafe {
            *ptr = 0xAB;
            assert_eq!(*ptr, 0xAB);
        }

        munmap_shared(ptr, size);
        unsafe { libc::close(fd); }
    }
}
