//! Nyrqis DRM atomic modesetting — ADR-0026 Phase 3.
//!
//! Provides direct scanout of GPU buffers to display via DRM/KMS.
//! DRM (Direct Rendering Manager) is the Linux kernel interface for
//! GPU and display management.
//!
//! This crate is the shipped form of the display hot path;
//! the pure-Python path uses software rendering via `wl_shm`.
//!
//! **FFI surface (ABI 1.0.0).** Stub implementation — real DRM
//! integration requires `/dev/dri/card0` or `/dev/dri/renderD128`.
//!
//! References:
//! - ADR-0026 Phase 3: GPU acceleration
//! - ADR-0010: Vulkan as native graphics API
//! - DRM API: https://docs.kernel.org/gpu/drm.html

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;
const MAX_DEVICES: usize = 4;
const MAX_CONNECTORS: usize = 16;
const MAX_CRTCS: usize = 8;
const MAX_PLANES: usize = 16;

// ---------------------------------------------------------------------------
// DRM mode types (matching kernel headers)
// ---------------------------------------------------------------------------

/// DRM mode type: preferred mode.
const DRM_MODE_TYPE_PREFERRED: u32 = 1 << 3;
/// DRM mode type: current mode.
const DRM_MODE_TYPE_CURRENT: u32 = 1 << 0;

/// DRM connector status: connected.
const DRM_MODE_CONNECTED: u32 = 1;
/// DRM connector status: disconnected.
const DRM_MODE_DISCONNECTED: u32 = 2;

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

#[allow(dead_code)]
struct DeviceSlot {
    fd: c_int,
    active: bool,
}

#[allow(dead_code)]
struct ConnectorSlot {
    connector_id: u32,
    width: u32,
    height: u32,
    refresh: u32,      // mHz
    status: u32,
    crtc_id: u32,
    device_idx: i32,
    active: bool,
}

#[allow(dead_code)]
struct CrtcSlot {
    crtc_id: u32,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    active: bool,
    device_idx: i32,
}

#[allow(dead_code)]
struct PlaneSlot {
    plane_id: u32,
    crtc_id: u32,
    format: u32,
    active: bool,
    device_idx: i32,
}

struct DrmState {
    devices: Vec<Option<DeviceSlot>>,
    connectors: Vec<Option<ConnectorSlot>>,
    crtcs: Vec<Option<CrtcSlot>>,
    planes: Vec<Option<PlaneSlot>>,
    last_error: String,
}

static STATE: Mutex<Option<DrmState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut DrmState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| DrmState {
        devices: (0..MAX_DEVICES).map(|_| None).collect(),
        connectors: (0..MAX_CONNECTORS).map(|_| None).collect(),
        crtcs: (0..MAX_CRTCS).map(|_| None).collect(),
        planes: (0..MAX_PLANES).map(|_| None).collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_last_error(state: &mut DrmState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &DrmState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

/// Check if DRM is available at runtime.
fn is_drm_available() -> bool {
    // Phase 1: stub — real implementation will open /dev/dri/card0
    false
}

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_drm_version() -> u32 {
    ABI_VERSION
}

/// Open a DRM device.
///
/// `device_path` is the path to the DRM device
/// (e.g. `/dev/dri/card0`).  Pass NULL for the default.
///
/// Returns a device ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_drm_open_device(
    _device_path_ptr: *const c_char,
    _device_path_len: c_int,
) -> c_int {
    with_state(|state| {
        if !is_drm_available() {
            set_last_error(state, "DRM not available — need /dev/dri/card0");
            return -1;
        }

        let dev_idx = match alloc_slot(&mut state.devices) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many devices (max 4)");
                return -1;
            }
        };

        state.devices[dev_idx as usize] = Some(DeviceSlot {
            fd: -1,
            active: true,
        });

        dev_idx
    })
}

/// Enumerate connectors on a device.
///
/// Returns the number of connectors found, or -1 on error.
#[no_mangle]
pub extern "C" fn nyrqis_drm_enumerate_connectors(device_id: c_int) -> c_int {
    with_state(|state| {
        if device_id < 0 || device_id as usize >= MAX_DEVICES {
            set_last_error(state, "invalid device ID");
            return -1;
        }

        match &state.devices[device_id as usize] {
            Some(d) if d.active => {}
            _ => {
                set_last_error(state, "device not active");
                return -1;
            }
        }

        // Phase 1: stub — real implementation will use DRM_IOCTL_MODE_GETCONNECTOR
        0
    })
}

/// Get connector info.
///
/// Returns 0 on success, -1 on error.
#[no_mangle]
pub extern "C" fn nyrqis_drm_get_connector_info(
    connector_id: c_int,
    width: *mut u32,
    height: *mut u32,
    refresh: *mut u32,
    status: *mut u32,
) -> c_int {
    with_state(|state| {
        if connector_id < 0 || connector_id as usize >= MAX_CONNECTORS {
            set_last_error(state, "invalid connector ID");
            return -1;
        }

        match &state.connectors[connector_id as usize] {
            Some(c) if c.active => {
                if !width.is_null() { unsafe { *width = c.width; } }
                if !height.is_null() { unsafe { *height = c.height; } }
                if !refresh.is_null() { unsafe { *refresh = c.refresh; } }
                if !status.is_null() { unsafe { *status = c.status; } }
                0
            }
            _ => -1,
        }
    })
}

/// Perform an atomic modesetting commit.
///
/// Returns 0 on success, -1 on error.
/// This is a stub — real implementation will use DRM_IOCTL_MODE_ATOMIC.
#[no_mangle]
pub extern "C" fn nyrqis_drm_atomic_commit(
    device_id: c_int,
    connector_id: c_int,
    crtc_id: c_int,
    fb_id: u32,
) -> c_int {
    with_state(|state| {
        if device_id < 0 || device_id as usize >= MAX_DEVICES {
            set_last_error(state, "invalid device ID");
            return -1;
        }

        match &state.devices[device_id as usize] {
            Some(d) if d.active => {}
            _ => {
                set_last_error(state, "device not active");
                return -1;
            }
        }

        // Phase 1: stub — real implementation will call DRM_IOCTL_MODE_ATOMIC
        if connector_id < 0 || crtc_id < 0 || fb_id == 0 {
            set_last_error(state, "invalid atomic commit parameters");
            return -1;
        }

        0
    })
}

/// Close a device.
#[no_mangle]
pub extern "C" fn nyrqis_drm_close_device(device_id: c_int) -> c_int {
    with_state(|state| {
        if device_id < 0 || device_id as usize >= MAX_DEVICES {
            return -1;
        }
        if let Some(dev) = &mut state.devices[device_id as usize] {
            dev.active = false;
            0
        } else {
            -1
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_drm_last_error(buf: *mut c_char, cap: c_int) -> c_int {
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
        assert_eq!(nyrqis_drm_version(), 0x0001_0000);
    }

    #[test]
    fn open_device_returns_stub_error() {
        assert_eq!(nyrqis_drm_open_device(std::ptr::null(), 0), -1);
    }

    #[test]
    fn enumerate_connectors_invalid_device() {
        assert_eq!(nyrqis_drm_enumerate_connectors(-1), -1);
    }

    #[test]
    fn get_connector_info_invalid_id() {
        assert_eq!(nyrqis_drm_get_connector_info(-1, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut()), -1);
    }

    #[test]
    fn atomic_commit_invalid_device() {
        assert_eq!(nyrqis_drm_atomic_commit(-1, 0, 0, 1), -1);
    }

    #[test]
    fn close_device_invalid_id() {
        assert_eq!(nyrqis_drm_close_device(-1), -1);
    }

    #[test]
    fn last_error_returns_message() {
        with_state(|state| set_last_error(state, "test error"));
        let mut buf = [0u8; 64];
        let n = nyrqis_drm_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n > 0);
    }
}
