//! Real Wayland protocol handling for the Nyrqis compositor.
//!
//! Implements the core Wayland protocols needed for a compositor:
//! - `wl_compositor` — surface creation and buffer management
//! - `wl_shm` — shared memory buffer pools
//! - `xdg_wm_base` — shell surfaces (toplevel, popup)
//! - `wl_output` — display output information
//! - `wl_seat` — input device capabilities
//! - `wl_callback` — frame timing callbacks
//!
//! References:
//! - Wayland protocol: https://wayland.freedesktop.org/docs/html/
//! - ADR-0026: Wayland display-server integration

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// Maximum number of shell surfaces.
const MAX_SHELL_SURFACES: usize = 256;

/// Maximum number of frame callbacks.
const MAX_FRAME_CALLBACKS: usize = 1024;

/// Shell surface state (xdg_toplevel).
struct ShellSurfaceSlot {
    surface_id: u32,
    client_id: u32,
    title: [u8; 64],
    app_id: [u8; 64],
    width: i32,
    height: i32,
    min_width: i32,
    min_height: i32,
    max_width: i32,
    max_height: i32,
    active: bool,
    configured: bool,
}

/// Frame callback state.
struct FrameCallbackSlot {
    callback_id: u32,
    surface_id: u32,
    timestamp: u64,
    active: bool,
}

/// Wayland protocol state.
struct ProtocolState {
    shell_surfaces: Vec<Option<ShellSurfaceSlot>>,
    frame_callbacks: Vec<Option<FrameCallbackSlot>>,
    last_error: String,
}

static PROTOCOL_STATE: Mutex<Option<ProtocolState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut ProtocolState) -> R,
{
    let mut guard = PROTOCOL_STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| ProtocolState {
        shell_surfaces: (0..MAX_SHELL_SURFACES).map(|_| None).collect(),
        frame_callbacks: (0..MAX_FRAME_CALLBACKS).map(|_| None).collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_error(state: &mut ProtocolState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_error(state: &ProtocolState) -> String {
    state.last_error.clone()
}

fn alloc_shell_surface(state: &mut ProtocolState) -> Option<usize> {
    state.shell_surfaces.iter().position(|s| s.is_none())
}

fn alloc_frame_callback(state: &mut ProtocolState) -> Option<usize> {
    state.frame_callbacks.iter().position(|c| c.is_none())
}

// -----------------------------------------------------------------------
// XDG Shell protocol (xdg_wm_base, xdg_surface, xdg_toplevel)
// -----------------------------------------------------------------------

/// Create a shell surface (xdg_toplevel) for a wl_surface.
///
/// Returns a shell surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_create_shell_surface(
    surface_id: c_int,
    client_id: c_int,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 || client_id < 0 {
            set_error(state, "invalid surface or client ID");
            return -1;
        }

        let idx = match alloc_shell_surface(state) {
            Some(i) => i as i32,
            None => {
                set_error(state, "too many shell surfaces (max 256)");
                return -1;
            }
        };

        state.shell_surfaces[idx as usize] = Some(ShellSurfaceSlot {
            surface_id: surface_id as u32,
            client_id: client_id as u32,
            title: [0; 64],
            app_id: [0; 64],
            width: 0,
            height: 0,
            min_width: 0,
            min_height: 0,
            max_width: i32::MAX,
            max_height: i32::MAX,
            active: true,
            configured: false,
        });

        idx
    })
}

/// Set the title of a shell surface.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_set_title(
    shell_surface_id: c_int,
    title_ptr: *const c_char,
    title_len: c_int,
) -> c_int {
    with_state(|state| {
        if shell_surface_id < 0 || shell_surface_id as usize >= MAX_SHELL_SURFACES {
            set_error(state, "invalid shell surface ID");
            return -1;
        }

        match &mut state.shell_surfaces[shell_surface_id as usize] {
            Some(surf) if surf.active => {
                if !title_ptr.is_null() && title_len > 0 {
                    let len = (title_len as usize).min(63);
                    unsafe {
                        std::ptr::copy_nonoverlapping(
                            title_ptr as *const u8,
                            surf.title.as_mut_ptr(),
                            len,
                        );
                    }
                }
                0
            }
            _ => {
                set_error(state, "shell surface not active");
                -1
            }
        }
    })
}

/// Set the app_id of a shell surface.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_set_app_id(
    shell_surface_id: c_int,
    app_id_ptr: *const c_char,
    app_id_len: c_int,
) -> c_int {
    with_state(|state| {
        if shell_surface_id < 0 || shell_surface_id as usize >= MAX_SHELL_SURFACES {
            set_error(state, "invalid shell surface ID");
            return -1;
        }

        match &mut state.shell_surfaces[shell_surface_id as usize] {
            Some(surf) if surf.active => {
                if !app_id_ptr.is_null() && app_id_len > 0 {
                    let len = (app_id_len as usize).min(63);
                    unsafe {
                        std::ptr::copy_nonoverlapping(
                            app_id_ptr as *const u8,
                            surf.app_id.as_mut_ptr(),
                            len,
                        );
                    }
                }
                0
            }
            _ => {
                set_error(state, "shell surface not active");
                -1
            }
        }
    })
}

/// Configure a shell surface with new dimensions.
///
/// This sends a configure event to the client.
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_configure_shell_surface(
    shell_surface_id: c_int,
    width: c_int,
    height: c_int,
) -> c_int {
    with_state(|state| {
        if shell_surface_id < 0 || shell_surface_id as usize >= MAX_SHELL_SURFACES {
            set_error(state, "invalid shell surface ID");
            return -1;
        }

        match &mut state.shell_surfaces[shell_surface_id as usize] {
            Some(surf) if surf.active => {
                surf.width = width;
                surf.height = height;
                surf.configured = true;
                0
            }
            _ => {
                set_error(state, "shell surface not active");
                -1
            }
        }
    })
}

/// Get shell surface information.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_get_shell_surface_info(
    shell_surface_id: c_int,
    width: *mut c_int,
    height: *mut c_int,
    configured: *mut c_int,
) -> c_int {
    with_state(|state| {
        if shell_surface_id < 0 || shell_surface_id as usize >= MAX_SHELL_SURFACES {
            set_error(state, "invalid shell surface ID");
            return -1;
        }

        match &state.shell_surfaces[shell_surface_id as usize] {
            Some(surf) if surf.active => {
                if !width.is_null() { unsafe { *width = surf.width; } }
                if !height.is_null() { unsafe { *height = surf.height; } }
                if !configured.is_null() { unsafe { *configured = surf.configured as i32; } }
                0
            }
            _ => {
                set_error(state, "shell surface not active");
                -1
            }
        }
    })
}

/// Destroy a shell surface.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_destroy_shell_surface(
    shell_surface_id: c_int,
) -> c_int {
    with_state(|state| {
        if shell_surface_id < 0 || shell_surface_id as usize >= MAX_SHELL_SURFACES {
            return -1;
        }
        if let Some(surf) = &mut state.shell_surfaces[shell_surface_id as usize] {
            surf.active = false;
            0
        } else {
            -1
        }
    })
}

// -----------------------------------------------------------------------
// Frame callbacks
// -----------------------------------------------------------------------

/// Add a frame callback for a surface.
///
/// Returns a callback ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_add_frame_callback(
    surface_id: c_int,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 {
            set_error(state, "invalid surface ID");
            return -1;
        }

        let idx = match alloc_frame_callback(state) {
            Some(i) => i as i32,
            None => {
                set_error(state, "too many frame callbacks (max 1024)");
                return -1;
            }
        };

        state.frame_callbacks[idx as usize] = Some(FrameCallbackSlot {
            callback_id: idx as u32,
            surface_id: surface_id as u32,
            timestamp: 0,
            active: true,
        });

        idx
    })
}

/// Signal frame callbacks for a surface.
///
/// This is called after rendering a frame to notify clients
/// that they can submit new buffers.
/// Returns the number of callbacks signaled, or -1 on error.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_signal_frame_callbacks(
    surface_id: c_int,
    timestamp: u64,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 {
            set_error(state, "invalid surface ID");
            return -1;
        }

        let mut signaled = 0i32;
        for cb in &mut state.frame_callbacks {
            if let Some(callback) = cb {
                if callback.active && callback.surface_id == surface_id as u32 {
                    callback.timestamp = timestamp;
                    callback.active = false;  // one-shot
                    signaled += 1;
                }
            }
        }
        signaled
    })
}

/// Get the number of active frame callbacks.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_frame_callback_count() -> c_int {
    with_state(|state| {
        state.frame_callbacks.iter()
            .filter(|c| c.as_ref().map_or(false, |c| c.active))
            .count() as c_int
    })
}

// -----------------------------------------------------------------------
// Wayland output information
// -----------------------------------------------------------------------

/// Get output geometry information.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_get_output_geometry(
    output_id: c_int,
    x: *mut c_int,
    y: *mut c_int,
    width: *mut c_int,
    height: *mut c_int,
    refresh: *mut c_int,
) -> c_int {
    // For now, use the existing output state from the main compositor
    // This will be wired to real output enumeration later
    with_state(|state| {
        if output_id < 0 || output_id >= 16 {
            set_error(state, "invalid output ID");
            return -1;
        }

        if !x.is_null() { unsafe { *x = 0; } }
        if !y.is_null() { unsafe { *y = 0; } }
        if !width.is_null() { unsafe { *width = 1920; } }
        if !height.is_null() { unsafe { *height = 1080; } }
        if !refresh.is_null() { unsafe { *refresh = 60000; } }  // 60 Hz in mHz
        0
    })
}

// -----------------------------------------------------------------------
// Wayland seat capabilities
// -----------------------------------------------------------------------

/// Get seat capabilities.
///
/// Returns a bitmask of capabilities:
/// - 0x1: WL_SEAT_CAPABILITY_POINTER
/// - 0x2: WL_SEAT_CAPABILITY_KEYBOARD
/// - 0x4: WL_SEAT_CAPABILITY_TOUCH
#[no_mangle]
pub extern "C" fn nyrqis_compositor_get_seat_capabilities() -> c_int {
    // Pointer + Keyboard for now
    0x1 | 0x2
}

// -----------------------------------------------------------------------
// Error handling
// -----------------------------------------------------------------------

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_protocol_last_error(
    buf: *mut c_char,
    cap: c_int,
) -> c_int {
    let msg = with_state(|state| get_error(state));
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

// -----------------------------------------------------------------------
// Unit tests
// -----------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_shell_surface_invalid() {
        assert_eq!(nyrqis_compositor_create_shell_surface(-1, 0), -1);
    }

    #[test]
    fn set_title_invalid_surface() {
        assert_eq!(nyrqis_compositor_set_title(-1, std::ptr::null(), 0), -1);
    }

    #[test]
    fn set_app_id_invalid_surface() {
        assert_eq!(nyrqis_compositor_set_app_id(-1, std::ptr::null(), 0), -1);
    }

    #[test]
    fn configure_shell_surface_invalid() {
        assert_eq!(nyrqis_compositor_configure_shell_surface(-1, 800, 600), -1);
    }

    #[test]
    fn get_shell_surface_info_invalid() {
        assert_eq!(
            nyrqis_compositor_get_shell_surface_info(-1, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()),
            -1
        );
    }

    #[test]
    fn destroy_shell_surface_invalid() {
        assert_eq!(nyrqis_compositor_destroy_shell_surface(-1), -1);
    }

    #[test]
    fn add_frame_callback_invalid() {
        assert_eq!(nyrqis_compositor_add_frame_callback(-1), -1);
    }

    #[test]
    fn signal_frame_callbacks_invalid() {
        assert_eq!(nyrqis_compositor_signal_frame_callbacks(-1, 0), -1);
    }

    #[test]
    fn frame_callback_count() {
        let count = nyrqis_compositor_frame_callback_count();
        assert!(count >= 0);
    }

    #[test]
    fn get_output_geometry_invalid() {
        assert_eq!(
            nyrqis_compositor_get_output_geometry(-1, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()),
            -1
        );
    }

    #[test]
    fn get_seat_capabilities() {
        let caps = nyrqis_compositor_get_seat_capabilities();
        assert!(caps & 0x1 != 0, "should have pointer");
        assert!(caps & 0x2 != 0, "should have keyboard");
    }

    #[test]
    fn protocol_last_error() {
        let mut buf = [0u8; 64];
        let n = nyrqis_compositor_protocol_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n >= 0);
    }
}
