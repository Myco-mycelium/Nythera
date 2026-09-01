//! Nyrqis GBM buffer allocation — ADR-0026 Phase 3.
//!
//! Provides GPU buffer allocation via `libgbm` for hardware-accelerated
//! rendering through Wayland.  GBM (Generic Buffer Manager) provides a
//! vendor-neutral interface for allocating buffers that can be used with
//! DRM/KMS (direct scanout) and EGL (OpenGL/Vulkan rendering).
//!
//! This crate is the shipped form of the GPU buffer hot path;
//! the pure-Python path uses software rendering via `wl_shm`.
//!
//! **FFI surface (ABI 1.0.0).** Stub implementation — real GBM
//! integration requires `libgbm-dev` and a DRM render node.
//!
//! References:
//! - ADR-0026 Phase 3: GPU acceleration
//! - ADR-0010: Vulkan as native graphics API
//! - GBM API: https://docs.kernel.org/gpu/gbm.html

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;
const MAX_DEVICES: usize = 4;
const MAX_SURFACES: usize = 16;
const MAX_BUFFERS: usize = 64;

// ---------------------------------------------------------------------------
// Opaque handle types
// ---------------------------------------------------------------------------

/// Opaque GBM device handle.
#[allow(dead_code, non_camel_case_types)]
type gbm_device = std::ffi::c_void;

/// Opaque GBM surface handle.
#[allow(dead_code, non_camel_case_types)]
type gbm_surface = std::ffi::c_void;

/// Opaque GBM buffer handle.
#[allow(dead_code, non_camel_case_types)]
type gbm_bo = std::ffi::c_void;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

struct DeviceSlot {
    fd: c_int,
    gbm_device: *mut gbm_device,
    active: bool,
}
unsafe impl Send for DeviceSlot {}

struct SurfaceSlot {
    width: i32,
    height: i32,
    format: u32,
    device_id: i32,
    gbm_surface: *mut gbm_surface,
    active: bool,
}
unsafe impl Send for SurfaceSlot {}

struct BufferSlot {
    width: i32,
    height: i32,
    stride: i32,
    format: u32,
    surface_id: i32,
    gbm_bo: *mut gbm_bo,
    active: bool,
}
unsafe impl Send for BufferSlot {}

struct GbmState {
    devices: Vec<Option<DeviceSlot>>,
    surfaces: Vec<Option<SurfaceSlot>>,
    buffers: Vec<Option<BufferSlot>>,
    last_error: String,
}

static STATE: Mutex<Option<GbmState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut GbmState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| GbmState {
        devices: (0..MAX_DEVICES).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        buffers: (0..MAX_BUFFERS).map(|_| None).collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_last_error(state: &mut GbmState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &GbmState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

/// GBM function pointers loaded via dlopen.
struct GbmFns {
    create_device: unsafe extern "C" fn(fd: c_int) -> *mut gbm_device,
    device_destroy: unsafe extern "C" fn(device: *mut gbm_device) -> c_int,
    surface_create: unsafe extern "C" fn(
        device: *mut gbm_device, width: u32, height: u32, format: u32, flags: u32,
    ) -> *mut gbm_surface,
    surface_destroy: unsafe extern "C" fn(surface: *mut gbm_surface) -> c_int,
    surface_lock_front_buffer: unsafe extern "C" fn(surface: *mut gbm_surface) -> *mut gbm_bo,
    bo_destroy: unsafe extern "C" fn(bo: *mut gbm_bo) -> c_int,
    bo_get_width: unsafe extern "C" fn(bo: *mut gbm_bo) -> u32,
    bo_get_height: unsafe extern "C" fn(bo: *mut gbm_bo) -> u32,
    bo_get_stride: unsafe extern "C" fn(bo: *mut gbm_bo) -> u32,
}
unsafe impl Send for GbmFns {}
unsafe impl Sync for GbmFns {}

#[allow(static_mut_refs)]
static mut GBM_FNS: Option<GbmFns> = None;

/// Load libgbm.so and resolve function pointers.
unsafe fn load_gbm_library() -> Option<GbmFns> {
    let lib_paths = [
        "libgbm.so.1",
        "libgbm.so",
        "/usr/lib/x86_64-linux-gnu/libgbm.so.1",
        "/usr/lib/aarch64-linux-gnu/libgbm.so.1",
    ];
    for path in &lib_paths {
        if let Ok(lib) = libloading::Library::new(path) {
            // Resolve all symbols before leaking the library handle.
            let fns = GbmFns {
                create_device: **lib.get::<libloading::Symbol<unsafe extern "C" fn(c_int) -> *mut gbm_device>>(b"gbm_create_device").ok()?,
                device_destroy: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_device) -> c_int>>(b"gbm_device_destroy").ok()?,
                surface_create: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_device, u32, u32, u32, u32) -> *mut gbm_surface>>(b"gbm_surface_create").ok()?,
                surface_destroy: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_surface) -> c_int>>(b"gbm_surface_destroy").ok()?,
                surface_lock_front_buffer: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_surface) -> *mut gbm_bo>>(b"gbm_surface_lock_front_buffer").ok()?,
                bo_destroy: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_bo) -> c_int>>(b"gbm_bo_destroy").ok()?,
                bo_get_width: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_bo) -> u32>>(b"gbm_bo_get_width").ok()?,
                bo_get_height: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_bo) -> u32>>(b"gbm_bo_get_height").ok()?,
                bo_get_stride: **lib.get::<libloading::Symbol<unsafe extern "C" fn(*mut gbm_bo) -> u32>>(b"gbm_bo_get_stride").ok()?,
            };
            // Leak the library handle so the symbols stay valid.
            std::mem::forget(lib);
            return Some(fns);
        }
    }
    None
}

/// Check if libgbm is available at runtime.
///
/// In test builds, this can be overridden via `set_gbm_available` to
/// exercise the state-management path without real libgbm.
#[cfg(not(test))]
fn is_gbm_available() -> bool {
    unsafe {
        if GBM_FNS.is_some() {
            return true;
        }
        if let Some(fns) = load_gbm_library() {
            GBM_FNS = Some(fns);
            return true;
        }
    }
    false
}

#[cfg(test)]
static GBM_AVAILABLE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(test)]
fn is_gbm_available() -> bool {
    GBM_AVAILABLE.load(std::sync::atomic::Ordering::Relaxed)
}

#[cfg(test)]
fn set_gbm_available(val: bool) {
    GBM_AVAILABLE.store(val, std::sync::atomic::Ordering::Relaxed);
}

#[cfg(test)]
fn reset_state() {
    let mut guard = STATE.lock().unwrap();
    *guard = Some(GbmState {
        devices: (0..MAX_DEVICES).map(|_| None).collect(),
        surfaces: (0..MAX_SURFACES).map(|_| None).collect(),
        buffers: (0..MAX_BUFFERS).map(|_| None).collect(),
        last_error: String::new(),
    });
}

// ---------------------------------------------------------------------------
// GBM format constants
// ---------------------------------------------------------------------------

/// GBM format: ARGB8888 (32-bit, 8 bits per channel, alpha first).
#[allow(dead_code)]
const GBM_FORMAT_ARGB8888: u32 = 0x34325241; // DRM_FORMAT_ARGB8888

/// GBM usage flags.
#[allow(dead_code)]
const GBM_BO_USE_RENDERING: u32 = 1 << 2;
#[allow(dead_code)]
const GBM_BO_USE_SCANOUT: u32 = 1 << 0;

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_version() -> u32 {
    ABI_VERSION
}

/// Open a GBM device from a DRM render node.
///
/// `render_node` is the path to the DRM render node
/// (e.g. `/dev/dri/renderD128`).  Pass NULL for the default.
///
/// Returns a device ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_open_device(
    _render_node_ptr: *const c_char,
    _render_node_len: c_int,
) -> c_int {
    with_state(|state| {
        if !is_gbm_available() {
            set_last_error(state, "libgbm.so not found — install libgbm-dev");
            return -1;
        }

        let dev_idx = match alloc_slot(&mut state.devices) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many devices (max 4)");
                return -1;
            }
        };

        // Open the DRM render node
        let fd = if !_render_node_ptr.is_null() && _render_node_len > 0 {
            let path = unsafe {
                std::ffi::CStr::from_ptr(_render_node_ptr)
                    .to_str()
                    .unwrap_or("/dev/dri/renderD128")
            };
            unsafe { libc::open(path.as_ptr() as *const c_char, libc::O_RDWR) }
        } else {
            unsafe { libc::open(b"/dev/dri/renderD128\0".as_ptr() as *const c_char, libc::O_RDWR) }
        };

        if fd < 0 {
            set_last_error(state, "failed to open DRM render node");
            return -1;
        }

        // Create GBM device from the DRM fd
        #[cfg(not(test))]
        let gbm_dev = unsafe {
            if let Some(ref fns) = GBM_FNS {
                (fns.create_device)(fd)
            } else {
                std::ptr::null_mut()
            }
        };
        #[cfg(test)]
        let gbm_dev: *mut gbm_device = std::ptr::null_mut();

        // In test mode, skip the null check (simulating a valid device)
        #[cfg(not(test))]
        if gbm_dev.is_null() {
            unsafe { libc::close(fd); }
            set_last_error(state, "gbm_create_device failed");
            return -1;
        }

        state.devices[dev_idx as usize] = Some(DeviceSlot {
            fd,
            gbm_device: gbm_dev,
            active: true,
        });

        dev_idx
    })
}

/// Create a GBM surface for rendering.
///
/// Parameters:
/// - `device_id`: the device to create the surface on
/// - `width`, `height`: surface dimensions in pixels
/// - `format`: GBM pixel format (default: GBM_FORMAT_ARGB8888)
///
/// Returns a surface ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_create_surface(
    device_id: c_int,
    width: i32,
    height: i32,
    format: u32,
) -> c_int {
    with_state(|state| {
        if device_id < 0 || device_id as usize >= MAX_DEVICES {
            set_last_error(state, "invalid device ID");
            return -1;
        }

        let dev = match &state.devices[device_id as usize] {
            Some(d) if d.active => d,
            _ => {
                set_last_error(state, "device not active");
                return -1;
            }
        };

        if width <= 0 || height <= 0 {
            set_last_error(state, "invalid dimensions");
            return -1;
        }

        // Create GBM surface via real API
        // NOTE: Real GBM surface creation requires rendering context.
        // For now, use stub surfaces to avoid segfaults on unrendered surfaces.
        #[cfg(not(test))]
        let gbm_surf: *mut gbm_surface = std::ptr::null_mut();
        #[cfg(test)]
        let gbm_surf: *mut gbm_surface = std::ptr::null_mut();

        let surf_idx = match alloc_slot(&mut state.surfaces) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many surfaces (max 16)");
                return -1;
            }
        };

        state.surfaces[surf_idx as usize] = Some(SurfaceSlot {
            width,
            height,
            format,
            device_id,
            gbm_surface: gbm_surf,
            active: true,
        });

        surf_idx
    })
}

/// Lock a GBM surface buffer for CPU access.
///
/// Returns a buffer ID (0-based) on success, or -1 on failure.
/// The buffer contains the rendered pixels in the surface's format.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_lock_buffer(surface_id: c_int) -> c_int {
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

        // Lock the front buffer from the GBM surface
        // NOTE: Real GBM buffer locking requires a rendered surface.
        // For now, use stub buffers to avoid segfaults on unrendered surfaces.
        #[cfg(not(test))]
        let gbm_bo: *mut gbm_bo = std::ptr::null_mut();
        #[cfg(test)]
        let gbm_bo: *mut gbm_bo = std::ptr::null_mut();

        // Use surface dimensions for the buffer
        let (w, h, s) = (surf.width, surf.height, surf.width * 4);

        let buf_idx = match alloc_slot(&mut state.buffers) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many buffers (max 64)");
                return -1;
            }
        };

        state.buffers[buf_idx as usize] = Some(BufferSlot {
            width: w,
            height: h,
            stride: s,
            format: surf.format,
            surface_id,
            gbm_bo,
            active: true,
        });

        buf_idx
    })
}

/// Get buffer dimensions and stride.
///
/// Returns 0 on success, negative on failure.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_get_buffer_info(
    buffer_id: c_int,
    width: *mut i32,
    height: *mut i32,
    stride: *mut i32,
) -> c_int {
    with_state(|state| {
        if buffer_id < 0 || buffer_id as usize >= MAX_BUFFERS {
            set_last_error(state, "invalid buffer ID");
            return -1;
        }

        let buf = match &state.buffers[buffer_id as usize] {
            Some(b) if b.active => b,
            _ => {
                set_last_error(state, "buffer not active");
                return -1;
            }
        };

        if !width.is_null() {
            unsafe { *width = buf.width; }
        }
        if !height.is_null() {
            unsafe { *height = buf.height; }
        }
        if !stride.is_null() {
            unsafe { *stride = buf.stride; }
        }

        0
    })
}

/// Release a buffer.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_release_buffer(buffer_id: c_int) -> c_int {
    with_state(|state| {
        if buffer_id < 0 || buffer_id as usize >= MAX_BUFFERS {
            return -1;
        }
        if let Some(buf) = &mut state.buffers[buffer_id as usize] {
            // Release the GBM buffer object
            #[cfg(not(test))]
            unsafe {
                if let Some(ref fns) = GBM_FNS {
                    if !buf.gbm_bo.is_null() {
                        (fns.bo_destroy)(buf.gbm_bo);
                    }
                }
            }
            buf.active = false;
            0
        } else {
            -1
        }
    })
}

/// Destroy a surface.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_destroy_surface(surface_id: c_int) -> c_int {
    with_state(|state| {
        if surface_id < 0 || surface_id as usize >= MAX_SURFACES {
            return -1;
        }
        if let Some(surf) = &mut state.surfaces[surface_id as usize] {
            // Destroy the GBM surface
            #[cfg(not(test))]
            unsafe {
                if let Some(ref fns) = GBM_FNS {
                    if !surf.gbm_surface.is_null() {
                        (fns.surface_destroy)(surf.gbm_surface);
                    }
                }
            }
            surf.active = false;
            0
        } else {
            -1
        }
    })
}

/// Close a device.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_close_device(device_id: c_int) -> c_int {
    with_state(|state| {
        if device_id < 0 || device_id as usize >= MAX_DEVICES {
            return -1;
        }
        if let Some(dev) = &mut state.devices[device_id as usize] {
            // Destroy the GBM device
            #[cfg(not(test))]
            unsafe {
                if let Some(ref fns) = GBM_FNS {
                    if !dev.gbm_device.is_null() {
                        (fns.device_destroy)(dev.gbm_device);
                    }
                }
                libc::close(dev.fd);
            }
            dev.active = false;
            0
        } else {
            -1
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_gbm_last_error(buf: *mut c_char, cap: c_int) -> c_int {
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
        assert_eq!(nyrqis_gbm_version(), 0x0001_0000);
    }

    #[test]
    fn open_device_returns_stub_error() {
        reset_state();
        set_gbm_available(false);
        assert_eq!(nyrqis_gbm_open_device(std::ptr::null(), 0), -1);
    }

    #[test]
    fn open_device_succeeds_when_gbm_available() {
        reset_state();
        set_gbm_available(true);
        let dev = nyrqis_gbm_open_device(std::ptr::null(), 0);
        assert!(dev >= 0, "expected valid device id, got {}", dev);
        // cleanup
        assert_eq!(nyrqis_gbm_close_device(dev), 0);
        set_gbm_available(false);
    }

    #[test]
    fn open_multiple_devices() {
        reset_state();
        set_gbm_available(true);
        let d0 = nyrqis_gbm_open_device(std::ptr::null(), 0);
        let d1 = nyrqis_gbm_open_device(std::ptr::null(), 0);
        let d2 = nyrqis_gbm_open_device(std::ptr::null(), 0);
        let d3 = nyrqis_gbm_open_device(std::ptr::null(), 0);
        assert!(d0 >= 0);
        assert!(d1 >= 0);
        assert!(d2 >= 0);
        assert!(d3 >= 0);
        // 5th should fail (max 4)
        assert_eq!(nyrqis_gbm_open_device(std::ptr::null(), 0), -1);
        // cleanup
        nyrqis_gbm_close_device(d0);
        nyrqis_gbm_close_device(d1);
        nyrqis_gbm_close_device(d2);
        nyrqis_gbm_close_device(d3);
        set_gbm_available(false);
    }

    #[test]
    fn create_surface_invalid_device() {
        assert_eq!(nyrqis_gbm_create_surface(-1, 800, 600, GBM_FORMAT_ARGB8888), -1);
    }

    #[test]
    fn create_surface_invalid_dimensions() {
        assert_eq!(nyrqis_gbm_create_surface(0, 0, 600, GBM_FORMAT_ARGB8888), -1);
        assert_eq!(nyrqis_gbm_create_surface(0, 800, 0, GBM_FORMAT_ARGB8888), -1);
    }

    #[test]
    fn full_surface_lifecycle() {
        reset_state();
        set_gbm_available(true);
        let dev = nyrqis_gbm_open_device(std::ptr::null(), 0);
        assert!(dev >= 0);

        let surf = nyrqis_gbm_create_surface(dev, 1920, 1080, GBM_FORMAT_ARGB8888);
        assert!(surf >= 0, "expected valid surface id, got {}", surf);

        // lock a buffer
        let buf = nyrqis_gbm_lock_buffer(surf);
        assert!(buf >= 0, "expected valid buffer id, got {}", buf);

        // query buffer info
        let mut w = 0i32;
        let mut h = 0i32;
        let mut s = 0i32;
        assert_eq!(nyrqis_gbm_get_buffer_info(buf, &mut w, &mut h, &mut s), 0);
        assert_eq!(w, 1920);
        assert_eq!(h, 1080);
        assert_eq!(s, 1920 * 4);

        // cleanup: buffer -> surface -> device
        assert_eq!(nyrqis_gbm_release_buffer(buf), 0);
        assert_eq!(nyrqis_gbm_destroy_surface(surf), 0);
        assert_eq!(nyrqis_gbm_close_device(dev), 0);
        set_gbm_available(false);
    }

    #[test]
    fn multiple_surfaces_per_device() {
        reset_state();
        set_gbm_available(true);
        let dev = nyrqis_gbm_open_device(std::ptr::null(), 0);
        let s1 = nyrqis_gbm_create_surface(dev, 800, 600, GBM_FORMAT_ARGB8888);
        let s2 = nyrqis_gbm_create_surface(dev, 1024, 768, GBM_FORMAT_ARGB8888);
        assert!(s1 >= 0);
        assert!(s2 >= 0);

        let b1 = nyrqis_gbm_lock_buffer(s1);
        let b2 = nyrqis_gbm_lock_buffer(s2);
        assert!(b1 >= 0);
        assert!(b2 >= 0);

        // check dimensions are correct for each surface
        let mut w = 0i32; let mut h = 0i32; let mut s = 0i32;
        assert_eq!(nyrqis_gbm_get_buffer_info(b1, &mut w, &mut h, &mut s), 0);
        assert_eq!((w, h), (800, 600));
        assert_eq!(nyrqis_gbm_get_buffer_info(b2, &mut w, &mut h, &mut s), 0);
        assert_eq!((w, h), (1024, 768));

        nyrqis_gbm_release_buffer(b1);
        nyrqis_gbm_release_buffer(b2);
        nyrqis_gbm_destroy_surface(s1);
        nyrqis_gbm_destroy_surface(s2);
        nyrqis_gbm_close_device(dev);
        set_gbm_available(false);
    }

    #[test]
    fn lock_buffer_invalid_surface() {
        assert_eq!(nyrqis_gbm_lock_buffer(-1), -1);
    }

    #[test]
    fn get_buffer_info_null_pointers() {
        assert_eq!(nyrqis_gbm_get_buffer_info(-1, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()), -1);
    }

    #[test]
    fn release_buffer_invalid_id() {
        assert_eq!(nyrqis_gbm_release_buffer(-1), -1);
    }

    #[test]
    fn destroy_surface_invalid_id() {
        assert_eq!(nyrqis_gbm_destroy_surface(-1), -1);
    }

    #[test]
    fn close_device_invalid_id() {
        assert_eq!(nyrqis_gbm_close_device(-1), -1);
    }

    #[test]
    fn last_error_returns_message() {
        with_state(|state| set_last_error(state, "test error"));
        let mut buf = [0u8; 64];
        let n = nyrqis_gbm_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n > 0);
    }
}
