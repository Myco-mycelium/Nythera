//! Nyrqis EGL integration — ADR-0026 Phase 3.
//!
//! Provides OpenGL ES rendering via EGL for hardware-accelerated
//! Wayland display output.  EGL (Embedded-System Graphics Library)
//! provides the interface between rendering APIs (OpenGL ES) and
//! the native windowing system (Wayland/GBM).
//!
//! **FFI surface (ABI 1.0.0).** Real EGL integration via dlopen —
//! resolves `libEGL.so` at runtime for hardware-accelerated rendering.
//!
//! References:
//! - ADR-0026 Phase 3: GPU acceleration
//! - ADR-0010: Vulkan as native graphics API
//! - EGL API: https://www.khronos.org/egl/

use std::os::raw::{c_char, c_int, c_void};
use std::sync::Mutex;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;
const MAX_DISPLAYS: usize = 4;
const MAX_CONFIGS: usize = 32;
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
#[allow(dead_code)]
const EGL_RED_SIZE: u32 = 0x3024;
#[allow(dead_code)]
const EGL_GREEN_SIZE: u32 = 0x3023;
#[allow(dead_code)]
const EGL_BLUE_SIZE: u32 = 0x3022;
#[allow(dead_code)]
const EGL_ALPHA_SIZE: u32 = 0x3021;
#[allow(dead_code)]
const EGL_DEPTH_SIZE: u32 = 0x3025;
#[allow(dead_code)]
const EGL_STENCIL_SIZE: u32 = 0x3026;
#[allow(dead_code)]
const EGL_RENDERABLE_TYPE: u32 = 0x3040;
#[allow(dead_code)]
const EGL_SURFACE_TYPE: u32 = 0x3033;
const EGL_NONE: u32 = 0x3038;

#[allow(dead_code)]
const EGL_OPENGL_ES2_BIT: u32 = 0x0004;
#[allow(dead_code)]
const EGL_WINDOW_BIT: u32 = 0x0004;

// ---------------------------------------------------------------------------
// Opaque handle types
// ---------------------------------------------------------------------------

/// Opaque EGLDisplay handle (actually a pointer in the EGL spec).
type EGLDisplay = *mut c_void;
/// Opaque EGLConfig handle.
type EGLConfig = *mut c_void;
/// Opaque EGLSurface handle.
type EGLSurface = *mut c_void;
/// Opaque EGLContext handle.
type EGLContext = *mut c_void;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

struct DisplaySlot {
    display: EGLDisplay,
    major: i32,
    minor: i32,
    error: u32,
    active: bool,
}

struct ConfigSlot {
    config: EGLConfig,
    display_id: i32,
    active: bool,
}

struct SurfaceSlot {
    surface: EGLSurface,
    display_id: i32,
    width: i32,
    height: i32,
    active: bool,
}

struct ContextSlot {
    context: EGLContext,
    display_id: i32,
    active: bool,
}

struct EglState {
    displays: Vec<Option<DisplaySlot>>,
    configs: Vec<Option<ConfigSlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    contexts: Vec<Option<ContextSlot>>,
    last_error: String,
}
unsafe impl Send for EglState {}
unsafe impl Sync for EglState {}

static STATE: Mutex<Option<EglState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut EglState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| EglState {
        displays: (0..MAX_DISPLAYS).map(|_| None).collect(),
        configs: (0..MAX_CONFIGS).map(|_| None).collect(),
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

// ---------------------------------------------------------------------------
// EGL function pointers loaded via dlopen
// ---------------------------------------------------------------------------

/// EGL function pointers resolved at runtime from libEGL.so.
struct EGLFns {
    get_error: unsafe extern "C" fn() -> u32,
    get_display: unsafe extern "C" fn(native_display: u64) -> EGLDisplay,
    initialize: unsafe extern "C" fn(display: EGLDisplay, major: *mut i32, minor: *mut i32) -> u32,
    choose_config: unsafe extern "C" fn(
        display: EGLDisplay,
        attrib_list: *const u32,
        configs: *mut EGLConfig,
        config_size: u32,
        num_config: *mut u32,
    ) -> u32,
    create_window_surface: unsafe extern "C" fn(
        display: EGLDisplay,
        config: EGLConfig,
        native_window: u64,
        attrib_list: *const u32,
    ) -> EGLSurface,
    create_context: unsafe extern "C" fn(
        display: EGLDisplay,
        config: EGLConfig,
        share_context: EGLContext,
        attrib_list: *const u32,
    ) -> EGLContext,
    make_current: unsafe extern "C" fn(
        display: EGLDisplay,
        surface: EGLSurface,
        draw: EGLContext,
        read: EGLContext,
    ) -> u32,
    swap_buffers: unsafe extern "C" fn(display: EGLDisplay, surface: EGLSurface) -> u32,
    destroy_surface: unsafe extern "C" fn(display: EGLDisplay, surface: EGLSurface) -> u32,
    destroy_context: unsafe extern "C" fn(display: EGLDisplay, context: EGLContext) -> u32,
    terminate: unsafe extern "C" fn(display: EGLDisplay) -> u32,
}
unsafe impl Send for EGLFns {}
unsafe impl Sync for EGLFns {}

#[allow(static_mut_refs)]
static mut EGL_FNS: Option<EGLFns> = None;

/// Load libEGL.so and resolve function pointers.
unsafe fn load_egl_library() -> Option<EGLFns> {
    let lib_paths = [
        "libEGL.so.1",
        "libEGL.so",
        "/usr/lib/x86_64-linux-gnu/libEGL.so.1",
        "/usr/lib/aarch64-linux-gnu/libEGL.so.1",
    ];
    for path in &lib_paths {
        if let Ok(lib) = libloading::Library::new(path) {
            let fns = EGLFns {
                get_error: **lib.get::<libloading::Symbol<unsafe extern "C" fn() -> u32>>(b"eglGetError").ok()?,
                get_display: **lib.get::<libloading::Symbol<unsafe extern "C" fn(u64) -> EGLDisplay>>(b"eglGetDisplay").ok()?,
                initialize: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, *mut i32, *mut i32) -> u32>>(b"eglInitialize").ok()?,
                choose_config: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, *const u32, *mut EGLConfig, u32, *mut u32) -> u32>>(b"eglChooseConfig").ok()?,
                create_window_surface: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLConfig, u64, *const u32) -> EGLSurface>>(b"eglCreateWindowSurface").ok()?,
                create_context: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLConfig, EGLContext, *const u32) -> EGLContext>>(b"eglCreateContext").ok()?,
                make_current: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLSurface, EGLContext, EGLContext) -> u32>>(b"eglMakeCurrent").ok()?,
                swap_buffers: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLSurface) -> u32>>(b"eglSwapBuffers").ok()?,
                destroy_surface: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLSurface) -> u32>>(b"eglDestroySurface").ok()?,
                destroy_context: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay, EGLContext) -> u32>>(b"eglDestroyContext").ok()?,
                terminate: **lib.get::<libloading::Symbol<unsafe extern "C" fn(EGLDisplay) -> u32>>(b"eglTerminate").ok()?,
            };
            std::mem::forget(lib);
            return Some(fns);
        }
    }
    None
}

/// Check if EGL is available at runtime.
#[cfg(not(test))]
fn is_egl_available() -> bool {
    unsafe {
        if EGL_FNS.is_some() {
            return true;
        }
        if let Some(fns) = load_egl_library() {
            EGL_FNS = Some(fns);
            return true;
        }
    }
    false
}

#[cfg(test)]
static EGL_AVAILABLE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(test)]
fn is_egl_available() -> bool {
    EGL_AVAILABLE.load(std::sync::atomic::Ordering::Relaxed)
}

#[cfg(test)]
fn set_egl_available(val: bool) {
    EGL_AVAILABLE.store(val, std::sync::atomic::Ordering::Relaxed);
}

#[cfg(test)]
fn reset_state() {
    let mut guard = STATE.lock().unwrap();
    *guard = Some(EglState {
        displays: (0..MAX_DISPLAYS).map(|_| None).collect(),
        configs: (0..MAX_CONFIGS).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        contexts: (0..MAX_CONTEXTS).map(|_| None).collect(),
        last_error: String::new(),
    });
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

        // Real EGL: call eglGetDisplay(EGL_DEFAULT_DISPLAY)
        #[cfg(not(test))]
        let egl_display = unsafe {
            if let Some(ref fns) = EGL_FNS {
                (fns.get_display)(EGL_DEFAULT_DISPLAY)
            } else {
                std::ptr::null_mut()
            }
        };
        #[cfg(test)]
        let egl_display: EGLDisplay = std::ptr::null_mut();

        state.displays[idx as usize] = Some(DisplaySlot {
            display: egl_display,
            major: 0,
            minor: 0,
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
                // Real EGL: call eglInitialize()
                #[cfg(not(test))]
                let result = unsafe {
                    if let Some(ref fns) = EGL_FNS {
                        (fns.initialize)(d.display, &mut d.major, &mut d.minor)
                    } else {
                        EGL_FALSE
                    }
                };
                #[cfg(test)]
                let result = {
                    d.major = 1;
                    d.minor = 5;
                    EGL_TRUE
                };

                if result == EGL_FALSE {
                    #[cfg(not(test))]
                    let err = unsafe {
                        if let Some(ref fns) = EGL_FNS {
                            (fns.get_error)()
                        } else {
                            0
                        }
                    };
                    #[cfg(test)]
                    let err = 0;
                    d.error = err;
                }
                result
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
                // Build attribute list for RGBA8888, OpenGL ES 2, window surface
                let attribs: [u32; 13] = [
                    EGL_RED_SIZE, 8,
                    EGL_GREEN_SIZE, 8,
                    EGL_BLUE_SIZE, 8,
                    EGL_ALPHA_SIZE, 8,
                    EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                    EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
                    EGL_NONE,
                ];

                let mut config_buf: Vec<EGLConfig> = vec![std::ptr::null_mut(); 1];
                let mut num_config: u32 = 0;

                #[cfg(not(test))]
                let ok = unsafe {
                    if let Some(ref fns) = EGL_FNS {
                        (fns.choose_config)(
                            d.display,
                            attribs.as_ptr(),
                            config_buf.as_mut_ptr(),
                            1,
                            &mut num_config,
                        )
                    } else {
                        EGL_FALSE
                    }
                };
                #[cfg(test)]
                let ok = {
                    num_config = 1;
                    EGL_TRUE
                };

                if ok == EGL_FALSE || num_config == 0 {
                    #[cfg(not(test))]
                    let err = unsafe {
                        if let Some(ref fns) = EGL_FNS {
                            (fns.get_error)()
                        } else {
                            0
                        }
                    };
                    #[cfg(test)]
                    let err = 0;
                    set_last_error(state, &format!("eglChooseConfig failed: error 0x{:x}", err));
                    return -1;
                }

                let config = config_buf[0];
                let idx = match alloc_slot(&mut state.configs) {
                    Some(i) => i as i32,
                    None => {
                        set_last_error(state, "too many configs (max 32)");
                        return -1;
                    }
                };

                state.configs[idx as usize] = Some(ConfigSlot {
                    config,
                    display_id,
                    active: true,
                });

                idx
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
    config_id: c_int,
    width: i32,
    height: i32,
) -> c_int {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return -1;
        }

        if config_id < 0 || config_id as usize >= MAX_CONFIGS {
            set_last_error(state, "invalid config ID");
            return -1;
        }

        let (display, config) = match (&state.displays[display_id as usize], &state.configs[config_id as usize]) {
            (Some(d), Some(c)) if d.active && c.active => (d.display, c.config),
            _ => {
                set_last_error(state, "display or config not active");
                return -1;
            }
        };

        // Create the surface with eglCreateWindowSurface (native_window = 0 for offscreen)
        #[cfg(not(test))]
        let egl_surface = unsafe {
            if let Some(ref fns) = EGL_FNS {
                (fns.create_window_surface)(display, config, 0, std::ptr::null())
            } else {
                std::ptr::null_mut()
            }
        };
        #[cfg(test)]
        let egl_surface: EGLSurface = std::ptr::null_mut();

        let idx = match alloc_slot(&mut state.surfaces) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many surfaces (max 16)");
                return -1;
            }
        };

        state.surfaces[idx as usize] = Some(SurfaceSlot {
            surface: egl_surface,
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
pub extern "C" fn nyrqis_egl_create_context(
    display_id: c_int,
    config_id: c_int,
) -> c_int {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            set_last_error(state, "invalid display ID");
            return -1;
        }

        if config_id < 0 || config_id as usize >= MAX_CONFIGS {
            set_last_error(state, "invalid config ID");
            return -1;
        }

        let (display, config) = match (&state.displays[display_id as usize], &state.configs[config_id as usize]) {
            (Some(d), Some(c)) if d.active && c.active => (d.display, c.config),
            _ => {
                set_last_error(state, "display or config not active");
                return -1;
            }
        };

        // Create OpenGL ES 2.0 context
        let context_attribs: [u32; 3] = [
            0x3098, // EGL_CONTEXT_CLIENT_TYPE
            0x3038, // EGL_NONE
            EGL_NONE,
        ];

        #[cfg(not(test))]
        let egl_context = unsafe {
            if let Some(ref fns) = EGL_FNS {
                (fns.create_context)(display, config, std::ptr::null_mut(), context_attribs.as_ptr())
            } else {
                std::ptr::null_mut()
            }
        };
        #[cfg(test)]
        let egl_context: EGLContext = std::ptr::null_mut();

        let idx = match alloc_slot(&mut state.contexts) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many contexts (max 16)");
                return -1;
            }
        };

        state.contexts[idx as usize] = Some(ContextSlot {
            context: egl_context,
            display_id,
            active: true,
        });

        idx
    })
}

/// Make an EGL context current for rendering.
///
/// Returns EGL_TRUE on success, EGL_FALSE on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_make_current(
    display_id: c_int,
    surface_id: c_int,
    context_id: c_int,
) -> u32 {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            return EGL_FALSE;
        }

        let display = match &state.displays[display_id as usize] {
            Some(d) if d.active => d.display,
            _ => return EGL_FALSE,
        };

        let surface = if surface_id >= 0 && (surface_id as usize) < MAX_SURFACES {
            match &state.surfaces[surface_id as usize] {
                Some(s) if s.active => s.surface,
                _ => std::ptr::null_mut(),
            }
        } else {
            std::ptr::null_mut()
        };

        let context = if context_id >= 0 && (context_id as usize) < MAX_CONTEXTS {
            match &state.contexts[context_id as usize] {
                Some(c) if c.active => c.context,
                _ => std::ptr::null_mut(),
            }
        } else {
            std::ptr::null_mut()
        };

        #[cfg(not(test))]
        let result = unsafe {
            if let Some(ref fns) = EGL_FNS {
                (fns.make_current)(display, surface, context, context)
            } else {
                EGL_FALSE
            }
        };
        #[cfg(test)]
        let result = EGL_TRUE;

        result
    })
}

/// Swap front and back buffers (present a rendered frame).
///
/// Returns EGL_TRUE on success, EGL_FALSE on failure.
#[no_mangle]
pub extern "C" fn nyrqis_egl_swap_buffers(
    display_id: c_int,
    surface_id: c_int,
) -> u32 {
    with_state(|state| {
        if display_id < 0 || display_id as usize >= MAX_DISPLAYS {
            return EGL_FALSE;
        }
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            return EGL_FALSE;
        }

        let (display, surface) = match (&state.displays[display_id as usize], &state.surfaces[surface_id as usize]) {
            (Some(d), Some(s)) if d.active && s.active => (d.display, s.surface),
            _ => return EGL_FALSE,
        };

        #[cfg(not(test))]
        let result = unsafe {
            if let Some(ref fns) = EGL_FNS {
                (fns.swap_buffers)(display, surface)
            } else {
                EGL_FALSE
            }
        };
        #[cfg(test)]
        let result = EGL_TRUE;

        result
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
            // Real EGL: call eglDestroySurface
            #[cfg(not(test))]
            unsafe {
                if let Some(ref fns) = EGL_FNS {
                    let display = match &state.displays[surf.display_id as usize] {
                        Some(d) => d.display,
                        None => std::ptr::null_mut(),
                    };
                    (fns.destroy_surface)(display, surf.surface);
                }
            }
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
            // Real EGL: call eglDestroyContext
            #[cfg(not(test))]
            unsafe {
                if let Some(ref fns) = EGL_FNS {
                    let display = match &state.displays[ctx.display_id as usize] {
                        Some(d) => d.display,
                        None => std::ptr::null_mut(),
                    };
                    (fns.destroy_context)(display, ctx.context);
                }
            }
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
            // Real EGL: call eglTerminate
            #[cfg(not(test))]
            let result = unsafe {
                if let Some(ref fns) = EGL_FNS {
                    (fns.terminate)(d.display)
                } else {
                    EGL_TRUE
                }
            };
            #[cfg(test)]
            let result = EGL_TRUE;
            d.active = false;
            result
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
        reset_state();
        set_egl_available(true);
        let id = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        assert!(id >= 0);
        nyrqis_egl_terminate(id);
        set_egl_available(false);
    }

    #[test]
    fn get_display_returns_error_when_not_available() {
        reset_state();
        set_egl_available(false);
        assert_eq!(nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY), -1);
    }

    #[test]
    fn initialize_invalid_display() {
        assert_eq!(nyrqis_egl_initialize(-1), EGL_FALSE);
    }

    #[test]
    fn initialize_succeeds() {
        reset_state();
        set_egl_available(true);
        let disp = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        assert!(disp >= 0);
        assert_eq!(nyrqis_egl_initialize(disp), EGL_TRUE);
        nyrqis_egl_terminate(disp);
        set_egl_available(false);
    }

    #[test]
    fn choose_config_returns_valid_id() {
        reset_state();
        set_egl_available(true);
        let disp = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        nyrqis_egl_initialize(disp);
        let config = nyrqis_egl_choose_config(disp);
        assert!(config >= 0);
        nyrqis_egl_terminate(disp);
        set_egl_available(false);
    }

    #[test]
    fn create_window_surface_invalid_display() {
        assert_eq!(nyrqis_egl_create_window_surface(-1, 0, 800, 600), -1);
    }

    #[test]
    fn create_context_invalid_display() {
        assert_eq!(nyrqis_egl_create_context(-1, 0), -1);
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
        reset_state();
        set_egl_available(true);

        let disp = nyrqis_egl_get_display(EGL_DEFAULT_DISPLAY);
        assert!(disp >= 0);

        assert_eq!(nyrqis_egl_initialize(disp), EGL_TRUE);

        let config = nyrqis_egl_choose_config(disp);
        assert!(config >= 0);

        let surf = nyrqis_egl_create_window_surface(disp, config, 1920, 1080);
        assert!(surf >= 0);

        let ctx = nyrqis_egl_create_context(disp, config);
        assert!(ctx >= 0);

        assert_eq!(nyrqis_egl_make_current(disp, surf, ctx), EGL_TRUE);
        assert_eq!(nyrqis_egl_swap_buffers(disp, surf), EGL_TRUE);

        assert_eq!(nyrqis_egl_destroy_context(ctx), 0);
        assert_eq!(nyrqis_egl_destroy_surface(surf), 0);
        assert_eq!(nyrqis_egl_terminate(disp), EGL_TRUE);

        set_egl_available(false);
    }
}
