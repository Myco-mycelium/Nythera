//! Nyrqis EGL integration — ADR-0026 Phase 3.
//!
//! Provides OpenGL ES rendering via EGL for hardware-accelerated
//! Wayland display output.  EGL (Embedded-System Graphics Library)
//! provides the interface between rendering APIs (OpenGL ES) and
//! the native windowing system (Wayland/GBM).
//!
//! **FFI surface (ABI 1.0.0).** Stub implementation — real EGL
//! integration requires Mesa/EGL drivers.
//!
//! References:
//! - ADR-0026 Phase 3: GPU acceleration
//! - ADR-0010: Vulkan as native graphics API
//! - EGL API: https://www.khronos.org/egl/

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;
const MAX_DISPLAYS: usize = 4;
const MAX_SURFACES: usize = 16;
const MAX_CONTEXTS: usize = 16;

// ---------------------------------------------------------------------------
// EGL constants
// ---------------------------------------------------------------------------

const EGL_SUCCESS: u32 = 0x3000;
const EGL_FALSE: u32 = 0;
const EGL_TRUE: u32 = 1;
const EGL_DEFAULT_DISPLAY: u64 = 0;
const EGL_NO_DISPLAY: u64 = 0;
const EGL_NO_SURFACE: u64 = 0;
const EGL_NO_CONTEXT: u64 = 0;

// EGL attributes
const EGL_RED_SIZE: u32 = 0x3024;
const EGL_GREEN_SIZE: u32 = 0x3023;
const EGL_BLUE_SIZE: u32 = 0x3022;
const EGL_ALPHA_SIZE: u32 = 0x3021;
const EGL_DEPTH_SIZE: u32 = 0x3025;
const EGL_STENCIL_SIZE: u32 = 0x3026;
const EGL_RENDERABLE_TYPE: u32 = 0x3040;
const EGL_SURFACE_TYPE: u32 = 0x3033;
const EGL_NONE: u32 = 0x3038;

const EGL_OPENGL_ES2_BIT: u32 = 0x0004;
const EGL_WINDOW_BIT: u32 = 0x0004;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

#[allow(dead_code)]
struct DisplaySlot {
    display: u64,  // EGLDisplay handle
    error: u32,    // last error code
    active: bool,
}

#[allow(dead_code)]
struct SurfaceSlot {
    surface: u64,  // EGLSurface handle
    display_id: i32,
    width: i32,
    height: i32,
    active: bool,
}

#[allow(dead_code)]
struct ContextSlot {
    context: u64,  // EGLContext handle
    display_id: i32,
    active: bool,
}

struct EglState {
    displays: Vec<Option<DisplaySlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    contexts: Vec<Option<ContextSlot>>,
    last_error: String,
}

static STATE: Mutex<Option<EglState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut EglState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| EglState {
        displays: (0..MAX_DISPLAYS).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        contexts: (0..MAX_CONTEXTS).map(|_| None).collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_last_error(state: &mut EglState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &EglState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

/// Check if EGL is available at runtime.
fn is_egl_available() -> bool {
    // Check if Mesa EGL libraries exist
    std::path::Path::new("/usr/lib/x86_64-linux-gnu/libEGL.so.1").exists()
        || std::path::Path::new("/usr/lib/aarch64-linux-gnu/libEGL.so.1").exists()
        || std::path::Path::new("/usr/lib/libEGL.so.1").exists()
}

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_egl_version() -> u32 {
    ABI_VERSION
}

/// Get the current EGL error code.
#[no_mangle]
pub extern "C" fn nyrqis_egl_get_error(display_id: c_int) -> u32 {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            return 0;
        }
        match &state.displays[display_id as usize] {
            Some(d) => d.error,
            None => 0,
        }
    })
}

/// Initialize EGL and get a display.
///
/// Returns a display ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_get_display(_display_id: u64) -> c_int {
    with_state(|state| {
        if !is_egl_available() {
            set_last_error(state, "EGL not available — install libegl1-mesa-dev");
            return -1;
        }

        let idx = match alloc_slot(&mut state.displays) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many displays (max 4)");
                return -1;
            }
        };

        state.displays[idx as usize] = Some(DisplaySlot {
            display: EGL_DEFAULT_DISPLAY,
            error: EGL_SUCCESS,
            active: true,
        });

        idx
    })
}

/// Initialize EGL for a display.
///
/// Returns EGL_TRUE on success, EGL_FALSE on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_initialize(display_id: c_int) -> u32 {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return EGL_FALSE;
        }

        match &mut state.displays[display_id as usize] {
            Some(d) if d.active => {
                // Phase 1: stub — real implementation will call eglInitialize()
                d.error = EGL_SUCCESS;
                EGL_TRUE
            }
            _ => {
                set_last_error(state, "display not active");
                EGL_FALSE
            }
        }
    })
}

/// Choose an EGL configuration.
///
/// Returns a config ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_choose_config(display_id: c_int) -> c_int {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return -1;
        }

        match &state.displays[display_id as usize] {
            Some(d) if d.active => {
                // Phase 1: stub — real implementation will call eglChooseConfig()
                0
            }
            _ => {
                set_last_error(state, "display not active");
                -1
            }
        }
    })
}

/// Create an EGL window surface.
///
/// Returns a surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_create_window_surface(
    display_id: c_int,
    width: i32,
    height: i32,
) -> c_int {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return -1;
        }

        match &state.displays[display_id as usize] {
            Some(d) if d.active => {}
            _ => {
                set_last_error(state, "display not active");
                return -1;
            }
        }

        let idx = match alloc_slot(&mut state.surfaces) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many surfaces (max 16)");
                return -1;
            }
        };

        state.surfaces[idx as usize] = Some(SurfaceSlot {
            surface: 0, // stub
            display_id,
            width,
            height,
            active: true,
        });

        idx
    })
}

/// Create an EGL context.
///
/// Returns a context ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_create_context(display_id: c_int) -> c_int {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return -1;
        }

        match &state.displays[display_id as usize] {
            Some(d) if d.active => {}
            _ => {
                set_last_error(state, "display not active");
                return -1;
            }
        }

        let idx = match alloc_slot(&mut state.contexts) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many contexts (max 16)");
                return -1;
            }
        };

        state.contexts[idx as usize] = Some(ContextSlot {
            context: 0, // stub
            display_id,
            active: true,
        });

        idx
    })
}

/// Destroy a surface.
#[no_mangle]
pub extern "C" fn nyrqis_egl_destroy_surface(surface_id: c_int) -> c_int {
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

/// Destroy a context.
#[no_mangle]
pub extern "C" fn nyrqis_egl_destroy_context(context_id: c_int) -> c_int {
    with_state(|state| {
        if context_id < 0 || context_id as usize >= MAX_CONTEXTS {
            return -1;
        }
        if let Some(ctx) = &mut state.contexts[context_id as usize] {
            ctx.active = false;
            0
        } else {
            -1
        }
    })
}

/// Terminate a display.
#[no_mangle]
pub extern "C" fn nyrqis_egl_terminate(display_id: c_int) -> u32 {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            return EGL_FALSE;
        }
        if let Some(d) = &mut state.displays[display_id as usize] {
            d.active = false;
            EGL_TRUE
        } else {
            EGL_FALSE
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_egl_last_error(buf: *mut c_char, cap: c_int) -> c_int {
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
        assert_eq!(nyrqis_egl_version(), 0x0001_0000);
    }

    #[test]
    fn get_display_returns_valid_id() {
        let id = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        assert!(id >= 0);
        // cleanup
        nyrqis_egl_terminate(id);
    }

    #[test]
    fn initialize_invalid_display() {
        assert_eq!(nyrqis_egl_initialize(-1), EGL_FALSE);
    }

    #[test]
    fn create_window_surface_invalid_display() {
        assert_eq!(nyrqis_egl_create_window_surface(-1, 800, 600), -1);
    }

    #[test]
    fn create_context_invalid_display() {
        assert_eq!(nyrqis_egl_create_context(-1), -1);
    }

    #[test]
    fn destroy_surface_invalid_id() {
        assert_eq!(nyrqis_egl_destroy_surface(-1), -1);
    }

    #[test]
    fn destroy_context_invalid_id() {
        assert_eq!(nyrqis_egl_destroy_context(-1), -1);
    }

    #[test]
    fn terminate_invalid_display() {
        assert_eq!(nyrqis_egl_terminate(-1), EGL_FALSE);
    }

    #[test]
    fn last_error_returns_message() {
        let mut buf = [0u8; 64];
        let n = nyrqis_egl_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n >= 0);
    }

    #[test]
    fn full_display_lifecycle() {
        let disp = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        assert!(disp >= 0);

        assert_eq!(nyrqis_egl_initialize(disp), EGL_TRUE);

        let config = nyrqis_egl_choose_config(disp);
        assert!(config >= 0);

        let surf = nyrqis_egl_create_window_surface(disp, 1920, 1080);
        assert!(surf >= 0);

        let ctx = nyrqis_egl_create_context(disp);
        assert!(ctx >= 0);

        assert_eq!(nyrqis_egl_destroy_context(ctx), 0);
        assert_eq!(nyrqis_egl_destroy_surface(surf), 0);
        assert_eq!(nyrqis_egl_terminate(disp), EGL_TRUE);
    }
}
