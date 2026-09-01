//! Nyrqis Wayland display-server client — ADR-0026.
//!
//! Provides Wayland protocol bindings for the Nyrqis shell's display
//! integration: surface management, input handling, and buffer
//! allocation via `wl_shm` (software rendering) with a documented
//! path to GBM/DRM (GPU acceleration).
//!
//! This crate is the shipped form of the display-server hot path;
//! the pure-Python `DesktopSession` (`ui/desktop_session.py`) is the
//! reference floor.  The ADR-0020 platform-boundary rule applies:
//! the display path below the boundary must not depend on the Python
//! interpreter in its shipped form.
//!
//! **FFI surface (ABI 1.0.0).** Caller-supplied input only — no
//! heap allocations leak across the boundary, and there is no `free`
//! contract:
//!
//! - `nyrqis_wayland_version() -> u32` — ABI version (`0x0001_0000`).
//! - `nyrqis_wayland_connect(display_name_ptr, display_name_len) -> i32`
//!   — connect to a Wayland display; returns 0 on success, negative
//!   on failure (e.g. `WAYLAND_DISPLAY` not set, compositor refused).
//! - `nyrqis_wayland_create_surface(conn_id) -> i32` — create an
//!   `xdg_toplevel` surface; returns the surface ID (positive) or
//!   negative on failure.
//! - `nyrqis_wayland_submit_buffer(surface_id, pixel_ptr, pixel_len,
//!   width, height, stride) -> i32` — attach an `SHM` buffer to a
//!   surface and commit; returns 0 on success.
//! - `nyrqis_wayland_dispatch_events(conn_id, timeout_ms) -> i32` —
//!   poll the display connection for pending events; returns the
//!   number of events dispatched, or negative on error.
//! - `nyrqis_wayland_last_error(buf, cap) -> i32` — copies the last
//!   error message into a caller buffer (best-effort, for diagnostics).

use std::os::raw::{c_char, c_int};
use std::sync::OnceLock;
use std::ffi::CStr;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;

/// Thread-local error buffer for FFI error reporting — mirrors the
/// pattern used by `nyui`, `ipc`, and other crates.
thread_local! {
    static LAST_ERROR: std::cell::RefCell<String> = std::cell::RefCell::new(String::new());
}

fn set_last_error(msg: &str) {
    LAST_ERROR.with(|e| *e.borrow_mut() = msg.to_string());
}

fn get_last_error() -> String {
    LAST_ERROR.with(|e| e.borrow().clone())
}

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
/// `display_name_ptr` / `display_name_len` describe an optional UTF-8
/// display name (e.g. `"wayland-0"`).  Pass `NULL` / `0` to use the
/// `WAYLAND_DISPLAY` environment variable.
///
/// Returns a connection ID (positive) on success, or a negative error
/// code on failure.
///
/// NOTE: This is a skeleton.  The real implementation will use
/// `wayland_client::Display::connect_to_env()` or
/// `Display::connect_to_name()`.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_connect(
    _display_name_ptr: *const c_char,
    _display_name_len: c_int,
) -> c_int {
    // TODO: Implement real Wayland connection (ADR-0026 Phase 1)
    set_last_error("not yet implemented — stub only");
    -1
}

/// Create an `xdg_toplevel` surface on the given connection.
///
/// Returns the surface ID (positive) on success, or a negative error
/// code on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_create_surface(_conn_id: c_int) -> c_int {
    // TODO: Implement surface creation (ADR-0026 Phase 1)
    set_last_error("not yet implemented — stub only");
    -1
}

/// Submit a pixel buffer to a surface via `wl_shm`.
///
/// `pixel_ptr` / `pixel_len` point to raw ARGB8888 pixel data.
/// `width`, `height`, and `stride` describe the buffer layout.
///
/// Returns 0 on success, negative on failure.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_submit_buffer(
    _surface_id: c_int,
    _pixel_ptr: *const u8,
    _pixel_len: c_int,
    _width: c_int,
    _height: c_int,
    _stride: c_int,
) -> c_int {
    // TODO: Implement SHM buffer submission (ADR-0026 Phase 1)
    set_last_error("not yet implemented — stub only");
    -1
}

/// Poll the display connection for pending events.
///
/// `timeout_ms` is the poll timeout (-1 for infinite).  Returns the
/// number of events dispatched, or a negative error code.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_dispatch_events(
    _conn_id: c_int,
    _timeout_ms: c_int,
) -> c_int {
    // TODO: Implement event dispatch (ADR-0026 Phase 2)
    set_last_error("not yet implemented — stub only");
    -1
}

/// Copy the last error message into `buf` (capacity `cap` bytes).
/// Returns the number of bytes written (excluding NUL), or -1 if the
/// buffer is too small.
#[no_mangle]
pub extern "C" fn nyrqis_wayland_last_error(buf: *mut c_char, cap: c_int) -> c_int {
    let msg = get_last_error();
    if buf.is_null() || cap <= 0 {
        return -1;
    }
    let bytes = msg.as_bytes();
    let write_len = (cap as usize).min(bytes.len());
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf as *mut u8, write_len);
        if (cap as usize) > write_len {
            *buf.add(write_len) = 0; // NUL terminate
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
    fn connect_returns_stub_error() {
        assert_eq!(nyrqis_wayland_connect(std::ptr::null(), 0), -1);
        let err = get_last_error();
        assert!(err.contains("not yet implemented"));
    }

    #[test]
    fn create_surface_returns_stub_error() {
        assert_eq!(nyrqis_wayland_create_surface(1), -1);
    }

    #[test]
    fn submit_buffer_returns_stub_error() {
        assert_eq!(
            nyrqis_wayland_submit_buffer(1, std::ptr::null(), 0, 100, 100, 400),
            -1
        );
    }

    #[test]
    fn dispatch_events_returns_stub_error() {
        assert_eq!(nyrqis_wayland_dispatch_events(1, 100), -1);
    }

    #[test]
    fn last_error_returns_message() {
        set_last_error("test error message");
        let mut buf = [0u8; 64];
        let n = nyrqis_wayland_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n > 0);
        let msg = std::str::from_utf8(&buf[..n as usize]).unwrap();
        assert_eq!(msg, "test error message");
    }

    #[test]
    fn last_error_truncates_when_buffer_too_small() {
        set_last_error("a very long error message that exceeds the buffer");
        let mut buf = [0u8; 10];
        let n = nyrqis_wayland_last_error(buf.as_mut_ptr() as *mut c_char, 10);
        assert_eq!(n, 10);
    }

    #[test]
    fn last_error_null_buffer_returns_neg1() {
        assert_eq!(
            nyrqis_wayland_last_error(std::ptr::null_mut(), 64),
            -1
        );
    }
}
