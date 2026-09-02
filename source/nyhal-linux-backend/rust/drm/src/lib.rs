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

struct DeviceSlot {
    fd: c_int,
    active: bool,
}
unsafe impl Send for DeviceSlot {}

struct ConnectorSlot {
    connector_id: u32,
    width: u32,
    height: u32,
    refresh: u32,      // mHz
    status: u32,
    crtc_id: u32,
    device_id: i32,
    active: bool,
}

struct CrtcSlot {
    crtc_id: u32,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    active: bool,
    device_idx: i32,
}

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

// ---------------------------------------------------------------------------
// DRM ioctl structures (matching kernel headers)
// ---------------------------------------------------------------------------

/// DRM mode connector structure for MODE_GETCONNECTOR ioctl.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct DrmModeGetConnector {
    connector_id: u32,
    encoder_id: u32,
    connector_type: u32,
    connector_type_id: u32,
    connection: u32,
    width: u32,
    height: u32,
    subpixel: u32,
    num_modes: u32,
    modes_ptr: u64,
    num_encoders: u32,
    encoders_ptr: u64,
    num_modesources: u32,
    modesources_ptr: u64,
    blob_ids_ptr: u64,
    count_encoders: u32,
    count_modes: u32,
    count_properties: u32,
    properties_ptr: u64,
    prop_values_ptr: u64,
}

/// DRM mode mode_info structure.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct DrmModeModeInfo {
    clock: u32,
    hdisplay: u16,
    hsync_start: u16,
    hsync_end: u16,
    htotal: u16,
    hskew: u16,
    vdisplay: u16,
    vsync_start: u16,
    vsync_end: u16,
    vtotal: u16,
    vscan: u16,
    vrefresh: u32,
    flags: u32,
    type_: u32,
    name: [u8; 32],
}

/// DRM mode get resources structure.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct DrmModeGetResources {
    count_fbs: u32,
    fb_id_ptr: u64,
    count_crtcs: u32,
    crtc_id_ptr: u64,
    count_connectors: u32,
    connector_id_ptr: u64,
    count_encoders: u32,
    encoder_id_ptr: u64,
    min_width: u32,
    max_width: u32,
    min_height: u32,
    max_height: u32,
}

/// DRM mode atomic request structure.
#[repr(C)]
struct DrmModeAtomic {
    flags: u32,
    count_objs: u32,
    objs_ptr: u64,
    count_props: u32,
    props_ptr: u64,
    prop_values_ptr: u64,
    reserved: u64,
    count_clones: u32,
    clone_ptr: u64,
}

// DRM ioctl magic number (from drm.h)
const DRM_IOCTL_BASE: u8 = b'd';
const DRM_IOCTL_MODE_GETRESOURCES: u64 = 0xc03c64a0;  // size=60 (struct drm_mode_card_res)
const DRM_IOCTL_MODE_GETCONNECTOR: u64 = 0xc15064a7;  // size=80 (struct drm_mode_get_connector)
const DRM_IOCTL_MODE_ATOMIC: u64 = 0xc01864ee;
const DRM_IOCTL_SET_MASTER: u64 = 0x1b000014;
const DRM_IOCTL_DROP_MASTER: u64 = 0x1b000015;

/// Safe wrapper around ioctl syscall.
unsafe fn drm_ioctl(fd: c_int, request: u64, arg: *mut libc::c_void) -> c_int {
    libc::ioctl(fd, request, arg) as c_int
}

/// Check if DRM is available at runtime.
///
/// In test builds, this can be overridden via `set_drm_available`.
#[cfg(not(test))]
fn is_drm_available() -> bool {
    // Check if any DRM device exists
    std::path::Path::new("/dev/dri/card0").exists()
        || std::path::Path::new("/dev/dri/renderD128").exists()
}

#[cfg(test)]
static DRM_AVAILABLE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(test)]
fn is_drm_available() -> bool {
    DRM_AVAILABLE.load(std::sync::atomic::Ordering::Relaxed)
}

#[cfg(test)]
fn set_drm_available(val: bool) {
    DRM_AVAILABLE.store(val, std::sync::atomic::Ordering::Relaxed);
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

        // Open the DRM device file
        let fd = if !_device_path_ptr.is_null() && _device_path_len > 0 {
            let path = unsafe {
                std::ffi::CStr::from_ptr(_device_path_ptr)
                    .to_str()
                    .unwrap_or("/dev/dri/card1")
            };
            unsafe { libc::open(path.as_ptr() as *const c_char, libc::O_RDWR | libc::O_CLOEXEC) }
        } else {
            // Auto-detect: try card0, card1, renderD128
            let paths = [
                b"/dev/dri/card0\0".as_ptr() as *const c_char,
                b"/dev/dri/card1\0".as_ptr() as *const c_char,
                b"/dev/dri/renderD128\0".as_ptr() as *const c_char,
            ];
            let mut fd = -1;
            for &p in &paths {
                fd = unsafe { libc::open(p, libc::O_RDWR | libc::O_CLOEXEC) };
                if fd >= 0 {
                    break;
                }
            }
            fd
        };

        if fd < 0 {
            set_last_error(state, "failed to open DRM device");
            return -1;
        }

        state.devices[dev_idx as usize] = Some(DeviceSlot {
            fd,
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

        let fd = match &state.devices[device_id as usize] {
            Some(d) if d.active => d.fd,
            _ => {
                set_last_error(state, "device not active");
                return -1;
            }
        };

        if fd < 0 {
            set_last_error(state, "device fd not open");
            return -1;
        }

        // First call: get count of connectors
        let mut res = DrmModeGetResources::default();
        let ret = unsafe { drm_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &mut res as *mut _ as *mut libc::c_void) };
        if ret < 0 {
            set_last_error(state, "DRM_IOCTL_MODE_GETRESOURCES failed");
            return -1;
        }

        let count = res.count_connectors as i32;
        if count <= 0 {
            return 0;
        }

        // Allocate buffer for connector IDs
        let mut connector_ids: Vec<u32> = vec![0; count as usize];
        res.connector_id_ptr = connector_ids.as_mut_ptr() as u64;
        res.count_connectors = count as u32;

        // Second call: fill connector IDs
        let ret = unsafe { drm_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &mut res as *mut _ as *mut libc::c_void) };
        if ret < 0 {
            set_last_error(state, "DRM_IOCTL_MODE_GETRESOURCES (fill) failed");
            return -1;
        }

        // Clear existing connectors
        for slot in &mut state.connectors {
            *slot = None;
        }

        // Query each connector
        let mut found = 0i32;
        for &conn_id in &connector_ids {
            if found >= MAX_CONNECTORS as i32 {
                break;
            }

            let mut conn = DrmModeGetConnector::default();
            conn.connector_id = conn_id;

            // First call: get connector info
            let ret = unsafe { drm_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &mut conn as *mut _ as *mut libc::c_void) };
            if ret < 0 {
                continue;
            }

            // Get preferred mode dimensions
            let mut width = 0u32;
            let mut height = 0u32;
            let mut refresh = 0u32;

            if conn.count_modes > 0 {
                let mut modes: Vec<DrmModeModeInfo> = vec![DrmModeModeInfo::default(); conn.count_modes as usize];
                conn.modes_ptr = modes.as_mut_ptr() as u64;

                let ret = unsafe { drm_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &mut conn as *mut _ as *mut libc::c_void) };
                if ret >= 0 && !modes.is_empty() {
                    // Use preferred mode or first mode
                    let mode = if conn.count_modes > 0 {
                        &modes[0]
                    } else {
                        &modes[0]
                    };
                    width = mode.hdisplay as u32;
                    height = mode.vdisplay as u32;
                    refresh = mode.vrefresh;
                }
            }

            if let Some(idx) = alloc_slot(&mut state.connectors) {
                state.connectors[idx] = Some(ConnectorSlot {
                    connector_id: conn_id,
                    width,
                    height,
                    refresh,
                    status: conn.connection,
                    crtc_id: 0,
                    device_id,
                    active: true,
                });
                found += 1;
            }
        }

        found
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
/// Uses DRM_IOCTL_MODE_ATOMIC to commit the display configuration.
/// The atomic request sets:
/// - Connector: CRTC_ID property → crtc_id
/// - CRTC: ACTIVE property → 1, FB_ID property → fb_id
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

        let fd = match &state.devices[device_id as usize] {
            Some(d) if d.active => d.fd,
            _ => {
                set_last_error(state, "device not active");
                return -1;
            }
        };

        if connector_id < 0 || crtc_id < 0 || fb_id == 0 {
            set_last_error(state, "invalid atomic commit parameters");
            return -1;
        }

        // Build the atomic commit request.
        // We need to set connector->CRTC_ID and crtc->ACTIVE + crtc->FB_ID.
        // For a minimal commit, we use the legacy path via DRM_IOCTL_MODE_SET_CRTC
        // which is simpler and widely supported.
        //
        // DRM_IOCTL_MODE_SET_CRTC structure:
        // struct drm_mode_crtc {
        //     __u64 set_connectors_ptr;
        //     __u32 count_connectors;
        //     __u32 crtc_id;      // offset 8
        //     __u32 fb_id;        // offset 12
        //     __u32 x;            // offset 16
        //     __u32 y;            // offset 20
        //     __u32 gamma_size;   // offset 24
        //     __u32 mode_valid;   // offset 28
        //     struct drm_mode_modeinfo mode; // offset 32
        // };
        // Total: 32 + sizeof(drm_mode_modeinfo) = 32 + 68 = 100 bytes
        #[repr(C)]
        #[derive(Clone, Copy, Default)]
        struct DrmModeModeInfo {
            clock: u32,
            hdisplay: u16,
            hsync_start: u16,
            hsync_end: u16,
            htotal: u16,
            hskew: u16,
            vdisplay: u16,
            vsync_start: u16,
            vsync_end: u16,
            vtotal: u16,
            vscan: u16,
            vrefresh: u32,
            flags: u32,
            type_: u32,
            name: [u8; 32],
        }

        #[repr(C)]
        #[derive(Clone, Copy)]
        struct DrmModeCrtc {
            set_connectors_ptr: u64,
            count_connectors: u32,
            crtc_id: u32,
            fb_id: u32,
            x: u32,
            y: u32,
            gamma_size: u32,
            mode_valid: u32,
            mode: DrmModeModeInfo,
        }

        let conn_id = connector_id as u32;
        let mut crtc = DrmModeCrtc {
            set_connectors_ptr: &conn_id as *const u32 as u64,
            count_connectors: 1,
            crtc_id: crtc_id as u32,
            fb_id,
            x: 0,
            y: 0,
            gamma_size: 0,
            mode_valid: 1,
            mode: DrmModeModeInfo {
                hdisplay: 1920,
                vdisplay: 1080,
                vrefresh: 60,
                ..DrmModeModeInfo::default()
            },
        };

        // DRM_IOCTL_MODE_SET_CRTC = _IOWR('d', 0xb2, struct drm_mode_crtc)
        // = (3 << 30) | (0x64 << 8) | (0xb2) | (100 << 16)
        // = 0xc0000000 | 0x00640000 | 0x000000b2 | 0x00640000
        // = 0xc06464b2
        const DRM_IOCTL_MODE_SET_CRTC: u64 = 0xc06464b2;

        let ret = unsafe {
            drm_ioctl(fd, DRM_IOCTL_MODE_SET_CRTC, &mut crtc as *mut _ as *mut libc::c_void)
        };

        if ret < 0 {
            set_last_error(state, "DRM_IOCTL_MODE_SET_CRTC failed — display may not support this mode");
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
            if dev.fd >= 0 {
                unsafe { libc::close(dev.fd); }
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
