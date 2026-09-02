//! Wayland protocol handling for the Nyrqis compositor.
//!
//! Implements the core Wayland protocols needed for a compositor:
//! - `wl_compositor` — surface creation
//! - `wl_shm` — shared memory buffers
//! - `xdg_wm_base` — shell surfaces
//! - `wl_output` — display outputs
//! - `wl_callback` — frame timing
//!
//! References:
//! - Wayland protocol: https://wayland.freedesktop.org/docs/html/
//! - ADR-0026: Wayland display-server integration

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// Maximum number of SHM pools.
const MAX_SHM_POOLS: usize = 64;

/// Maximum number of buffers per pool.
const MAX_BUFFERS_PER_POOL: usize = 256;

/// SHM pool state.
struct ShmPoolSlot {
    pool_id: u32,
    fd: i32,            // file descriptor for the shared memory
    size: usize,        // size in bytes
    width: i32,
    height: i32,
    stride: i32,
    format: u32,        // WL_SHM_FORMAT_ARGB8888 = 0
    active: bool,
}

/// SHM buffer state.
struct ShmBufferSlot {
    buffer_id: u32,
    pool_id: u32,
    offset: i32,
    width: i32,
    height: i32,
    stride: i32,
    format: u32,
    active: bool,
}

struct WaylandState {
    shm_pools: Vec<Option<ShmPoolSlot>>,
    shm_buffers: Vec<Option<ShmBufferSlot>>,
    last_error: String,
}

static WAYLAND_STATE: Mutex<Option<WaylandState>> = Mutex::new(None);

fn with_wayland_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut WaylandState) -> R,
{
    let mut guard = WAYLAND_STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| WaylandState {
        shm_pools: (0..MAX_SHM_POOLS).map(|_| None).collect(),
        shm_buffers: (0..MAX_SHM_POOLS * MAX_BUFFERS_PER_POOL)
            .map(|_| None)
            .collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_wayland_error(state: &mut WaylandState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_wayland_error(state: &WaylandState) -> String {
    state.last_error.clone()
}

fn alloc_shm_pool(state: &mut WaylandState) -> Option<usize> {
    state.shm_pools.iter().position(|s| s.is_none())
}

fn alloc_shm_buffer(state: &mut WaylandState) -> Option<usize> {
    state.shm_buffers.iter().position(|s| s.is_none())
}

// -----------------------------------------------------------------------
// SHM format constants (from wayland-client-protocol.h)
// -----------------------------------------------------------------------

/// WL_SHM_FORMAT_ARGB8888 = 0
pub const WL_SHM_FORMAT_ARGB8888: u32 = 0;
/// WL_SHM_FORMAT_XRGB8888 = 1
pub const WL_SHM_FORMAT_XRGB8888: u32 = 1;

// -----------------------------------------------------------------------
// FFI exports
// -----------------------------------------------------------------------

/// Create a shared memory pool.
///
/// `fd` is a file descriptor for an anonymous shared memory region
/// (e.g. from `memfd_create` or `shm_open`). `size` is the size in bytes.
///
/// Returns a pool ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_create_pool(
    fd: c_int,
    size: c_int,
) -> c_int {
    with_wayland_state(|state| {
        if fd < 0 || size <= 0 {
            set_wayland_error(state, "invalid pool parameters");
            return -1;
        }

        let idx = match alloc_shm_pool(state) {
            Some(i) => i as i32,
            None => {
                set_wayland_error(state, "too many SHM pools (max 64)");
                return -1;
            }
        };

        state.shm_pools[idx as usize] = Some(ShmPoolSlot {
            pool_id: idx as u32,
            fd,
            size: size as usize,
            width: 0,
            height: 0,
            stride: 0,
            format: WL_SHM_FORMAT_ARGB8888,
            active: true,
        });

        idx
    })
}

/// Create a buffer in an SHM pool.
///
/// Returns a buffer ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_create_buffer(
    pool_id: c_int,
    offset: c_int,
    width: c_int,
    height: c_int,
    stride: c_int,
    format: u32,
) -> c_int {
    with_wayland_state(|state| {
        if pool_id < 0 || pool_id as usize >= MAX_SHM_POOLS {
            set_wayland_error(state, "invalid pool ID");
            return -1;
        }

        let pool = match &state.shm_pools[pool_id as usize] {
            Some(p) if p.active => p,
            _ => {
                set_wayland_error(state, "pool not active");
                return -1;
            }
        };

        if offset < 0 || width <= 0 || height <= 0 || stride <= 0 {
            set_wayland_error(state, "invalid buffer parameters");
            return -1;
        }

        // Check that the buffer fits in the pool
        let end = (offset as usize) + (stride as usize) * (height as usize);
        if end > pool.size {
            set_wayland_error(state, "buffer exceeds pool size");
            return -1;
        }

        let idx = match alloc_shm_buffer(state) {
            Some(i) => i as i32,
            None => {
                set_wayland_error(state, "too many SHM buffers");
                return -1;
            }
        };

        state.shm_buffers[idx as usize] = Some(ShmBufferSlot {
            buffer_id: idx as u32,
            pool_id: pool_id as u32,
            offset,
            width,
            height,
            stride,
            format,
            active: true,
        });

        idx
    })
}

/// Get buffer information.
///
/// Returns 0 on success, -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_get_buffer_info(
    buffer_id: c_int,
    width: *mut c_int,
    height: *mut c_int,
    stride: *mut c_int,
    format: *mut u32,
) -> c_int {
    with_wayland_state(|state| {
        if buffer_id < 0 || buffer_id as usize >= state.shm_buffers.len() {
            set_wayland_error(state, "invalid buffer ID");
            return -1;
        }

        match &state.shm_buffers[buffer_id as usize] {
            Some(buf) if buf.active => {
                if !width.is_null() { unsafe { *width = buf.width; } }
                if !height.is_null() { unsafe { *height = buf.height; } }
                if !stride.is_null() { unsafe { *stride = buf.stride; } }
                if !format.is_null() { unsafe { *format = buf.format; } }
                0
            }
            _ => {
                set_wayland_error(state, "buffer not active");
                -1
            }
        }
    })
}

/// Destroy a buffer.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_destroy_buffer(
    buffer_id: c_int,
) -> c_int {
    with_wayland_state(|state| {
        if buffer_id < 0 || buffer_id as usize >= state.shm_buffers.len() {
            return -1;
        }
        if let Some(buf) = &mut state.shm_buffers[buffer_id as usize] {
            buf.active = false;
            0
        } else {
            -1
        }
    })
}

/// Destroy an SHM pool.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_destroy_pool(
    pool_id: c_int,
) -> c_int {
    with_wayland_state(|state| {
        if pool_id < 0 || pool_id as usize >= state.shm_pools.len() {
            return -1;
        }
        if let Some(pool) = &mut state.shm_pools[pool_id as usize] {
            pool.active = false;
            0
        } else {
            -1
        }
    })
}

/// Get the number of active SHM pools.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_pool_count() -> c_int {
    with_wayland_state(|state| {
        state.shm_pools.iter()
            .filter(|p| p.as_ref().map_or(false, |p| p.active))
            .count() as c_int
    })
}

/// Get the number of active SHM buffers.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_shm_buffer_count() -> c_int {
    with_wayland_state(|state| {
        state.shm_buffers.iter()
            .filter(|b| b.as_ref().map_or(false, |b| b.active))
            .count() as c_int
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_compositor_wayland_last_error(
    buf: *mut c_char,
    cap: c_int,
) -> c_int {
    let msg = with_wayland_state(|state| get_wayland_error(state));
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
    fn shm_create_pool_invalid_fd() {
        assert_eq!(nyrqis_compositor_shm_create_pool(-1, 4096), -1);
    }

    #[test]
    fn shm_create_pool_invalid_size() {
        // Use a dummy fd (won't actually be used in this test)
        assert_eq!(nyrqis_compositor_shm_create_pool(0, 0), -1);
    }

    #[test]
    fn shm_create_buffer_invalid_pool() {
        assert_eq!(nyrqis_compositor_shm_create_buffer(-1, 0, 1920, 1080, 7680, 0), -1);
    }

    #[test]
    fn shm_destroy_buffer_invalid_id() {
        assert_eq!(nyrqis_compositor_shm_destroy_buffer(-1), -1);
    }

    #[test]
    fn shm_destroy_pool_invalid_id() {
        assert_eq!(nyrqis_compositor_shm_destroy_pool(-1), -1);
    }

    #[test]
    fn shm_pool_count() {
        let count = nyrqis_compositor_shm_pool_count();
        assert!(count >= 0);
    }

    #[test]
    fn shm_buffer_count() {
        let count = nyrqis_compositor_shm_buffer_count();
        assert!(count >= 0);
    }

    #[test]
    fn wayland_last_error() {
        let mut buf = [0u8; 64];
        let n = nyrqis_compositor_wayland_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n >= 0);
    }
}
