//! Nyrqis Vulkan rendering — ADR-0010 native graphics API foundation.
//!
//! This crate provides the Vulkan rendering pipeline for Nyrqis.
//! Per ADR-0010, Vulkan is the native graphics API for Nyrqis,
//! with DirectX-to-Vulkan translation for Windows compatibility.
//!
//! **FFI surface (ABI 0.1.0).** Scaffold implementation — real
//! Vulkan integration requires Mesa/Vulkan drivers.
//!
//! References:
//! - ADR-0010: Vulkan as native graphics API
//! - ADR-0026: Wayland display-server integration
//! - Vulkan API: https://www.vulkan.org/

use std::os::raw::{c_char, c_int};
use std::sync::Mutex;

/// ABI version: 0x0000_0100 (0.1.0).
const ABI_VERSION: u32 = 0x0000_0100;
const MAX_INSTANCES: usize = 4;
const MAX_DEVICES: usize = 8;
const MAX_SWAPCHAINS: usize = 16;

// ---------------------------------------------------------------------------
// Vulkan constants (matching Vulkan spec)
// ---------------------------------------------------------------------------

const VK_SUCCESS: i32 = 0;
const VK_NOT_READY: i32 = 1;
const VK_TIMEOUT: i32 = 2;
const VK_ERROR_OUT_OF_HOST_MEMORY: i32 = -1;
const VK_ERROR_OUT_OF_DEVICE_MEMORY: i32 = -2;
const VK_ERROR_INITIALIZATION_FAILED: i32 = -3;

const VK_MAKE_VERSION: fn(u32, u32, u32) -> u32 = |major, minor, patch| {
    (major << 22) | (minor << 12) | patch
};

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

#[allow(dead_code)]
struct InstanceSlot {
    instance: u64,  // VkInstance handle
    api_version: u32,
    active: bool,
}

#[allow(dead_code)]
struct DeviceSlot {
    device: u64,    // VkDevice handle
    physical_device: u64,  // VkPhysicalDevice handle
    instance_id: i32,
    active: bool,
}

#[allow(dead_code)]
struct SwapchainSlot {
    swapchain: u64,  // VkSwapchainKHR handle
    device_id: i32,
    width: u32,
    height: u32,
    image_count: u32,
    active: bool,
}

struct VulkanState {
    instances: Vec<Option<InstanceSlot>>,
    devices: Vec<Option<DeviceSlot>>,
    swapchains: Vec<Option<SwapchainSlot>>,
    last_error: String,
}

static STATE: Mutex<Option<VulkanState>> = Mutex::new(None);

fn with_state<F, R>(f: F) -> R
where
    F: FnOnce(&mut VulkanState) -> R,
{
    let mut guard = STATE.lock().unwrap();
    let state = guard.get_or_insert_with(|| VulkanState {
        instances: (0..MAX_INSTANCES).map(|_| None).collect(),
        devices: (0..MAX_DEVICES).map(|_| None).collect(),
        swapchains: (0..MAX_SWAPCHAINS).map(|_| None).collect(),
        last_error: String::new(),
    });
    f(state)
}

fn set_last_error(state: &mut VulkanState, msg: &str) {
    state.last_error = msg.to_string();
}

fn get_last_error(state: &VulkanState) -> String {
    state.last_error.clone()
}

fn alloc_slot<T>(slots: &mut Vec<Option<T>>) -> Option<usize> {
    slots.iter().position(|s| s.is_none())
}

/// Check if Vulkan is available at runtime.
fn is_vulkan_available() -> bool {
    // Check if Mesa Vulkan drivers exist
    std::path::Path::new("/usr/lib/x86_64-linux-gnu/libvulkan.so.1").exists()
        || std::path::Path::new("/usr/lib/aarch64-linux-gnu/libvulkan.so.1").exists()
}

// ---------------------------------------------------------------------------
// FFI exports
// ---------------------------------------------------------------------------

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_version() -> u32 {
    ABI_VERSION
}

/// Create a Vulkan instance.
///
/// Returns an instance ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_create_instance() -> c_int {
    with_state(|state| {
        if !is_vulkan_available() {
            set_last_error(state, "Vulkan not available — install libvulkan-dev");
            return -1;
        }

        let idx = match alloc_slot(&mut state.instances) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many instances (max 4)");
                return -1;
            }
        };

        // Phase 1: stub — real implementation will call vkCreateInstance()
        state.instances[idx as usize] = Some(InstanceSlot {
            instance: 0,
            api_version: VK_MAKE_VERSION(1, 3, 0),  // Vulkan 1.3
            active: true,
        });

        idx
    })
}

/// Destroy a Vulkan instance.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_destroy_instance(instance_id: c_int) -> c_int {
    with_state(|state| {
        if instance_id < 0 || instance_id as usize >= MAX_INSTANCES {
            return -1;
        }
        if let Some(inst) = &mut state.instances[instance_id as usize] {
            inst.active = false;
            0
        } else {
            -1
        }
    })
}

/// Create a logical device from a physical device.
///
/// Returns a device ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_create_device(instance_id: c_int) -> c_int {
    with_state(|state| {
        if instance_id < 0 || instance_id as usize >= MAX_INSTANCES {
            set_last_error(state, "invalid instance ID");
            return -1;
        }

        match &state.instances[instance_id as usize] {
            Some(i) if i.active => {}
            _ => {
                set_last_error(state, "instance not active");
                return -1;
            }
        }

        let idx = match alloc_slot(&mut state.devices) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many devices (max 8)");
                return -1;
            }
        };

        state.devices[idx as usize] = Some(DeviceSlot {
            device: 0,
            physical_device: 0,
            instance_id,
            active: true,
        });

        idx
    })
}

/// Destroy a logical device.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_destroy_device(device_id: c_int) -> c_int {
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

/// Create a swapchain for a Wayland surface.
///
/// Returns a swapchain ID (0-based) on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_create_swapchain(
    device_id: c_int,
    width: u32,
    height: u32,
    image_count: u32,
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

        if width == 0 || height == 0 || image_count == 0 {
            set_last_error(state, "invalid swapchain parameters");
            return -1;
        }

        let idx = match alloc_slot(&mut state.swapchains) {
            Some(i) => i as i32,
            None => {
                set_last_error(state, "too many swapchains (max 16)");
                return -1;
            }
        };

        state.swapchains[idx as usize] = Some(SwapchainSlot {
            swapchain: 0,
            device_id,
            width,
            height,
            image_count,
            active: true,
        });

        idx
    })
}

/// Destroy a swapchain.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_destroy_swapchain(swapchain_id: c_int) -> c_int {
    with_state(|state| {
        if swapchain_id < 0 || swapchain_id as usize >= MAX_SWAPCHAINS {
            return -1;
        }
        if let Some(sc) = &mut state.swapchains[swapchain_id as usize] {
            sc.active = false;
            0
        } else {
            -1
        }
    })
}

/// Acquire the next image from a swapchain.
///
/// Returns the image index on success, or -1 on failure.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_acquire_next_image(swapchain_id: c_int) -> c_int {
    with_state(|state| {
        if swapchain_id < 0 || swapchain_id as usize >= MAX_SWAPCHAINS {
            return -1;
        }

        match &state.swapchains[swapchain_id as usize] {
            Some(sc) if sc.active => {
                // Phase 1: stub — real implementation will call vkAcquireNextImageKHR()
                0
            }
            _ => -1,
        }
    })
}

/// Copy the last error message into `buf`.
#[no_mangle]
pub extern "C" fn nyrqis_vulkan_last_error(buf: *mut c_char, cap: c_int) -> c_int {
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
        assert_eq!(nyrqis_vulkan_version(), 0x0000_0100);
    }

    #[test]
    fn create_destroy_instance() {
        let id = nyrqis_vulkan_create_instance();
        assert!(id >= 0);
        assert_eq!(nyrqis_vulkan_destroy_instance(id), 0);
    }

    #[test]
    fn create_device_invalid_instance() {
        assert_eq!(nyrqis_vulkan_create_device(-1), -1);
    }

    #[test]
    fn destroy_device_invalid_id() {
        assert_eq!(nyrqis_vulkan_destroy_device(-1), -1);
    }

    #[test]
    fn create_swapchain_invalid_device() {
        assert_eq!(nyrqis_vulkan_create_swapchain(-1, 1920, 1080, 3), -1);
    }

    #[test]
    fn create_swapchain_invalid_params() {
        let inst = nyrqis_vulkan_create_instance();
        assert!(inst >= 0);
        let dev = nyrqis_vulkan_create_device(inst);
        assert!(dev >= 0);
        assert_eq!(nyrqis_vulkan_create_swapchain(dev, 0, 0, 0), -1);
        assert_eq!(nyrqis_vulkan_destroy_device(dev), 0);
        assert_eq!(nyrqis_vulkan_destroy_instance(inst), 0);
    }

    #[test]
    fn destroy_swapchain_invalid_id() {
        assert_eq!(nyrqis_vulkan_destroy_swapchain(-1), -1);
    }

    #[test]
    fn acquire_next_image_invalid_swapchain() {
        assert_eq!(nyrqis_vulkan_acquire_next_image(-1), -1);
    }

    #[test]
    fn full_vulkan_lifecycle() {
        let inst = nyrqis_vulkan_create_instance();
        assert!(inst >= 0);

        let dev = nyrqis_vulkan_create_device(inst);
        assert!(dev >= 0);

        let sc = nyrqis_vulkan_create_swapchain(dev, 1920, 1080, 3);
        assert!(sc >= 0);

        let img = nyrqis_vulkan_acquire_next_image(sc);
        assert!(img >= 0);

        assert_eq!(nyrqis_vulkan_destroy_swapchain(sc), 0);
        assert_eq!(nyrqis_vulkan_destroy_device(dev), 0);
        assert_eq!(nyrqis_vulkan_destroy_instance(inst), 0);
    }

    #[test]
    fn last_error_returns_message() {
        let mut buf = [0u8; 64];
        let n = nyrqis_vulkan_last_error(buf.as_mut_ptr() as *mut c_char, 64);
        assert!(n >= 0);
    }
}
