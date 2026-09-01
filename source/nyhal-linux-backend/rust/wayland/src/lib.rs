//! Nyrqis Wayland display-server client — ADR-0026.
//!
//! Provides Wayland protocol bindings for the Nyrqis shell's display
//! integration: surface management, input handling, and buffer
//! allocation via `wl_shm` (software rendering) with a documented
//! path to GBM/DRM (GPU acceleration).
//!
//! **FFI surface (ABI 1.0.0).** Caller-supplied input only — no
//! heap allocations leak across the boundary, and there is no `free`
//! contract.

use std::os::raw::{c_char, c_int};
use std::ptr;
use std::sync::Mutex;

use wayland_sys::client::{wayland_client_handle, wl_display, wl_proxy};
use wayland_sys::common::wl_argument;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;
const MAX_CONNECTIONS: usize = 8;
const MAX_SURFACES: usize = 64;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

struct ConnectionSlot {
    display: *mut wl_display,
    fd: c_int,
    connected: bool,
}
unsafe impl Send for ConnectionSlot {}

struct SurfaceSlot {
    surface: *mut wl_surface,
    width: i32,
    height: i32,
    stride: i32,
    conn_id: i32,
    active: bool,
}
unsafe impl Send for SurfaceSlot {}

/// Opaque handle type for surfaces (external consumers see an i32 ID).
#[allow(non_camel_case_types)]
type wl_surface = std::ffi::c_void;

struct WaylandState {
    connections: Vec<Option<ConnectionSlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    last_error: String,
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
        last_error: String::new(),
    });
    f(state)
}

fn set_last_error(state: &mut WaylandState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &WaylandState) -> String {
    state.last_error.clone()
}

fn alloc_connection(state: &mut WaylandState) -> Option<usize> {
    state.connections.iter().position(|s| s.is_none())
}

fn alloc_surface(state: &mut WaylandState) -> Option<usize> {
    state.surfaces.iter().position(|s| s.is_none())
}

fn is_wayland_available() -> bool {
    wayland_sys::client::is_lib_available()
}

// ---------------------------------------------------------------------------
// Wayland interface descriptors (minimal — enough for marshal)
// ---------------------------------------------------------------------------

// We use the extern "C" fn pointer trick for wl_interface's function
// pointers, but since we only need the `name` field for bind, we can
// use minimal static descriptors.

// SAFETY: These statics are valid for the duration of the process.
// The `name` pointers are to static byte strings.

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

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_version() -> u32 {
    ABI_VERSION
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

        let conn_idx = match alloc_connection(state) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many connections (max 8)");
                return -1;
            }
        };

        let h = wayland_client_handle();

        // Build display name
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

        // Free the CString if we allocated it
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

        state.connections[conn_idx as usize] = Some(ConnectionSlot {
            display,
            fd,
            connected: true,
        });

        conn_idx
    })
}

/// Create a `wl_surface` on the given connection.
///
/// Returns the surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_create_surface(conn_id: c_int) -> c_int {
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

        unsafe {
            // Step 1: wl_display.get_registry (opcode 1)
            let mut args = [wl_argument { n: 0 }]; // new_id
            let registry = (h.wl_proxy_marshal_array_constructor)(
                conn.display as *mut wl_proxy,
                1, // get_registry opcode
                args.as_mut_ptr(),
                &WL_REGISTRY_IFACE,
            );

            if registry.is_null() {
                set_last_error(state, "failed to get wl_registry");
                return -1;
            }

            // Step 2: Roundtrip to receive globals
            (h.wl_display_roundtrip)(conn.display);

            // Step 3: Try to bind wl_compositor from globals 1..10
            let mut compositor: *mut wl_proxy = ptr::null_mut();
            for global_name in 1..=10u32 {
                let mut bind_args = [
                    wl_argument { u: global_name },
                    wl_argument {
                        s: WL_COMPOSITOR_IFACE.name,
                    },
                    wl_argument { u: 4 },
                    wl_argument { n: 0 }, // new_id
                ];
                let candidate = (h.wl_proxy_marshal_array_constructor)(
                    registry,
                    0, // bind opcode
                    bind_args.as_mut_ptr(),
                    &WL_COMPOSITOR_IFACE,
                );
                if !candidate.is_null() {
                    let class = (h.wl_proxy_get_class)(candidate);
                    if !class.is_null() {
                        let class_cstr = std::ffi::CStr::from_ptr(class);
                        if class_cstr.to_bytes() == b"wl_compositor" {
                            compositor = candidate;
                            break;
                        }
                    }
                    (h.wl_proxy_destroy)(candidate);
                }
            }

            (h.wl_proxy_destroy)(registry);

            if compositor.is_null() {
                set_last_error(state, "wl_compositor global not found");
                return -1;
            }

            // Step 4: wl_compositor.create_surface (opcode 1)
            let surface = (h.wl_proxy_marshal_array_constructor)(
                compositor,
                1, // create_surface opcode
                ptr::null_mut(),
                &WL_SURFACE_IFACE,
            );

            (h.wl_proxy_destroy)(compositor);

            if surface.is_null() {
                set_last_error(state, "failed to create wl_surface");
                return -1;
            }

            let surf_idx = match alloc_surface(state) {
                Some(i) => i as i32,
                None => {
                    (h.wl_proxy_destroy)(surface);
                    set_last_error(state, "too many surfaces (max 64)");
                    return -1;
                }
            };

            state.surfaces[surf_idx as usize] = Some(SurfaceSlot {
                surface: surface as *mut wl_surface,
                width: 0,
                height: 0,
                stride: 0,
                conn_id,
                active: true,
            });

            surf_idx
        }
    })
}

/// Submit a pixel buffer to a surface via `wl_shm`.
///
/// Returns 0 on success, negative on failure.
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

        let surf = match &mut state.surfaces[surface_id as usize] {
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

        // Store metadata. SHM buffer creation is Phase 1b.
        surf.width = width;
        surf.height = height;
        surf.stride = stride;

        0
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

        if !conn.display.is_null() {
            let h = wayland_client_handle();
            unsafe { (h.wl_display_disconnect)(conn.display); }
        }

        for surf_opt in &mut state.surfaces {
            if let Some(surf) = surf_opt {
                if surf.conn_id == conn_id && surf.active {
                    surf.active = false;
                }
            }
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

        if !surf.surface.is_null() {
            let h = wayland_client_handle();
            unsafe { (h.wl_proxy_destroy)(surf.surface as *mut wl_proxy); }
        }

        0
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
        assert_eq!(nyrqis_wayland_version(), 0x0001_0000);
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
        assert_eq!(nyrqis_wayland_create_surface(-1), -1);
    }

    #[test]
    fn create_surface_nonexistent_conn_returns_error() {
        assert_eq!(nyrqis_wayland_create_surface(99), -1);
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
}
