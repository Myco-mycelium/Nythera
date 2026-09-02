//! Nyrqis minimal Wayland compositor — for testing and development.
//!
//! This crate provides a minimal Wayland compositor implementation
//! that can be used for automated testing of the GPU rendering pipeline.
//! It implements the core Wayland protocols needed for a compositor:
//!
//! - `wl_compositor` — surface creation
//! - `wl_shm` — shared memory buffers
//! - `xdg_wm_base` — shell surfaces
//! - `wl_seat` — input devices
//! - `wl_output` — display outputs
//! - `wl_callback` — frame timing
//!
//! **FFI surface (ABI 0.1.0).** Scaffold implementation — real
//! compositor requires a DRM device and event loop.
//!
//! References:
//! - ADR-0026: Wayland display-server integration
//! - Wayland protocol: https://wayland.freedesktop.org/docs/html/

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

pub mod wayland;

/// ABI version: 0x0000_0100 (0.1.0).
const ABI_VERSION: u32 = 0x0000_0100;
const MAX_CLIENTS: usize = 32;
const MAX_SURFACES: usize = 256;
const MAX_OUTPUTS: usize = 16;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

#[allow(dead_code)]
struct ClientSlot {
    client_id: u32,
    pid: i32,
    active: bool,
}

#[allow(dead_code)]
struct SurfaceSlot {
    surface_id: u32,
    client_id: u32,
    width: i32,
    height: i32,
    buffer_fd: i32,    // SHM buffer fd
    active: bool,
}

#[allow(dead_code)]
struct OutputSlot {
    output_id: u32,
    width: u32,
    height: u32,
    name: String,
    active: bool,
}

struct CompositorState {
    clients: Vec<Option<ClientSlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    outputs: Vec<Option<OutputSlot>>,
    last_error: String,
    running: bool,
}

static STATE: Mutex<Option<CompositorState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut CompositorState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| CompositorState {
        clients: (0..MAX_CLIENTS).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        outputs: (0..MAX_OUTPUTS).map(|_| None).collect(),
        last_error: String::new(),
        running: false,
    });
    f(state)
}

fn set_last_error(state: &mut CompositorState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &CompositorState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_version() -> u32 {
    ABI_VERSION
}

/// Start the compositor event loop.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_start() -> c_int {
    with_state(|state| {
        if state.running {
            set_last_error(state, "compositor already running");
            return -1;
        }
        state.running = true;
        0
    })
}

/// Stop the compositor event loop.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_stop() -> c_int {
    with_state(|state| {
        if !state.running {
            set_last_error(state, "compositor not running");
            return -1;
        }
        state.running = false;
        0
    })
}

/// Check if the compositor is running.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_is_running() -> c_int {
    with_state(|state| {
        if state.running { 1 } else { 0 }
    })
}

/// Add an output to the compositor.
///
/// Returns an output ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_add_output(
    width: u32,
    height: u32,
    name_ptr: *const c_char,
    name_len: c_int,
) -> c_int {
    with_state(|state| {
        let idx = match alloc_slot(&mut state.outputs) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many outputs (max 16)");
                return -1;
            }
        };

        let name = if !name_ptr.is_null() && name_len > 0 {
            unsafe {
                std::ffi::CStr::from_ptr(name_ptr)
                    .to_str()
                    .unwrap_or("output")
                    .to_string()
            }
        } else {
            format!("output-{}", idx)
        };

        state.outputs[idx as usize] = Some(OutputSlot {
            output_id: idx as u32,
            width,
            height,
            name,
            active: true,
        });

        idx
    })
}

/// Create a surface for a client.
///
/// Returns a surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_create_surface(
    client_id: u32,
    width: i32,
    height: i32,
) -> c_int {
    with_state(|state| {
        let idx = match alloc_slot(&mut state.surfaces) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many surfaces (max 256)");
                return -1;
            }
        };

        state.surfaces[idx as usize] = Some(SurfaceSlot {
            surface_id: idx as u32,
            client_id,
            width,
            height,
            buffer_fd: -1,
            active: true,
        });

        idx
    })
}

/// Destroy a surface.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_destroy_surface(surface_id: c_int) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            return -1;
        }
        if let Some(surf) = &mut state.surfaces[surface_id as usize] {
            surf.active = false;
            0
        } else {
            -1
        }
    })
}

/// Get the number of active surfaces.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_surface_count() -> c_int {
    with_state(|state| {
        state.surfaces.iter()
            .filter(|s| s.as_ref().map_or(false, |s| s.active))
            .count() as c_int
    })
}

/// Get the number of active outputs.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_output_count() -> c_int {
    with_state(|state| {
        state.outputs.iter()
            .filter(|o| o.as_ref().map_or(false, |o| o.active))
            .count() as c_int
    })
}

// ---------------------------------------------------------------------------
// Frame callbacks
// ---------------------------------------------------------------------------

/// Frame callback state.
#[allow(dead_code)]
struct FrameCallbackSlot {
    callback_id: u32,
    surface_id: u32,
    timestamp: u64,
    active: bool,
}

const MAX_CALLBACKS: usize = 256;

// ---------------------------------------------------------------------------
// Input handling
// ---------------------------------------------------------------------------

/// Input event types.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub enum InputEventType {
    KeyPress = 1,
    KeyRelease = 2,
    PointerMotion = 3,
    PointerButton = 4,
}

/// Input event data.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct InputEvent {
    pub event_type: InputEventType,
    pub surface_id: u32,
    pub key_code: u32,
    pub button: u32,
    pub x: f64,
    pub y: f64,
    pub timestamp: u64,
}

/// Process an input event.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_process_input(
    event_type: InputEventType,
    surface_id: u32,
    key_code: u32,
    button: u32,
    x: f64,
    y: f64,
) -> c_int {
    with_state(|state| {
        if !state.running {
            return -1;
        }

        // Find the surface
        let surf = state.surfaces.iter().find(|s| {
            s.as_ref().map_or(false, |s| s.active && s.surface_id == surface_id)
        });

        if surf.is_none() {
            return -1;
        }

        // Phase 1: stub — real implementation will dispatch to client
        0
    })
}

/// Send a frame callback to a surface.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_send_frame_callback(
    surface_id: c_int,
    timestamp: u64,
) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            return -1;
        }

        match &state.surfaces[surface_id as usize] {
            Some(surf) if surf.active => {
                // Phase 1: stub — real implementation will call wl_callback
                0
            }
            _ => -1,
        }
    })
}

/// Commit a surface (process pending buffer).
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_commit_surface(surface_id: c_int) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            return -1;
        }

        match &state.surfaces[surface_id as usize] {
            Some(surf) if surf.active => {
                // Phase 1: stub — real implementation will process buffer
                0
            }
            _ => -1,
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_last_error(buf: *mut c_char, cap: c_int) -> c_int {
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
fn reset_state() {
    let mut guard = STATE.lock().unwrap();
    *guard = Some(CompositorState {
        clients: (0..MAX_CLIENTS).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        outputs: (0..MAX_OUTPUTS).map(|_| None).collect(),
        last_error: String::new(),
        running: false,
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_returns_abi_version() {
        assert_eq!(nyrqis_compositor_version(), 0x0000_0100);
    }

    #[test]
    fn start_stop_lifecycle() {
        assert_eq!(nyrqis_compositor_start(), 0);
        assert_eq!(nyrqis_compositor_is_running(), 1);
        assert_eq!(nyrqis_compositor_stop(), 0);
        assert_eq!(nyrqis_compositor_is_running(), 0);
    }

    #[test]
    fn start_twice_fails() {
        assert_eq!(nyrqis_compositor_start(), 0);
        assert_eq!(nyrqis_compositor_start(), -1);
        assert_eq!(nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn add_output() {
        let id = nyrqis_compositor_add_output(1920, 1080, std::ptr::null(), 0);
        assert!(id >= 0);
        assert_eq!(nyrqis_compositor_output_count(), 1);
    }

    #[test]
    fn create_surface() {
        let id = nyrqis_compositor_create_surface(0, 800, 600);
        assert!(id >= 0);
        assert_eq!(nyrqis_compositor_surface_count(), 1);
    }

    #[test]
    fn destroy_surface() {
        reset_state();
        let id = nyrqis_compositor_create_surface(0, 800, 600);
        assert!(id >= 0);
        assert_eq!(nyrqis_compositor_destroy_surface(id), 0);
        assert_eq!(nyrqis_compositor_surface_count(), 0);
    }

    #[test]
    fn destroy_surface_invalid_id() {
        reset_state();
        assert_eq!(nyrqis_compositor_destroy_surface(-1), -1);
    }

    #[test]
    fn last_error_returns_message() {
        reset_state();
        let mut buf = [0u8; 64];
        let n = nyrqis_compositor_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n >= 0);
    }

    #[test]
    fn process_input_fails_when_not_running() {
        reset_state();
        // Compositor is not running after reset
        assert_eq!(nyrqis_compositor_is_running(), 0);
        assert_eq!(nyrqis_compositor_process_input(
            InputEventType::KeyPress, 0, 0, 0, 0.0, 0.0), -1);
    }

    #[test]
    fn process_input_fails_for_invalid_surface() {
        reset_state();
        assert_eq!(nyrqis_compositor_start(), 0);
        assert_eq!(nyrqis_compositor_process_input(
            InputEventType::KeyPress, 9999, 0, 0, 0.0, 0.0), -1);
        assert_eq!(nyrqis_compositor_stop(), 0);
    }

    #[test]
    fn send_frame_callback_invalid_surface() {
        reset_state();
        assert_eq!(nyrqis_compositor_send_frame_callback(-1, 0), -1);
    }

    #[test]
    fn commit_surface_invalid_surface() {
        reset_state();
        assert_eq!(nyrqis_compositor_commit_surface(-1), -1);
    }

    #[test]
    fn commit_surface_valid() {
        reset_state();
        let id = nyrqis_compositor_create_surface(0, 800, 600);
        assert!(id >= 0);
        assert_eq!(nyrqis_compositor_commit_surface(id), 0);
        assert_eq!(nyrqis_compositor_destroy_surface(id), 0);
    }
}
