"""Python FFI bindings for the Nyrqis Vulkan rendering crate.

Provides the native graphics API foundation for Nyrqis per ADR-0010.
Vulkan is the native graphics API, with DirectX-to-Vulkan translation
for Windows compatibility.

References:
    - ADR-0010: Vulkan as native graphics API
    - ADR-0026: Wayland display-server integration
    - Vulkan API: https://www.vulkan.org/
"""

import ctypes
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_VULKAN_STUB = True  # will be False if the real crate is loaded
_lib = None

# Find the Vulkan crate .so
_CANDIDATE_PATHS = [
    os.environ.get("NYRQIS_VULKAN_LIB", ""),
    os.path.join(os.path.dirname(__file__), "..", "rust", "vulkan", "target", "release", "libnyrqis_vulkan.so"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "vulkan", "target", "debug", "libnyrqis_vulkan.so"),
]

def _load_lib():
    global _lib, _VULKAN_STUB
    if _lib is not None:
        return _lib
    for p in _CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            try:
                _lib = ctypes.CDLL(p)
                _VULKAN_STUB = False
                logger.info("Loaded Vulkan crate from %s", p)
                return _lib
            except OSError as e:
                logger.warning("Failed to load %s: %s", p, e)
    logger.info("Vulkan crate not available — using stub mode")
    return None


def is_available() -> bool:
    """Check if the real Vulkan crate is loaded."""
    _load_lib()
    return not _VULKAN_STUB


# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

def vulkan_version() -> int:
    """Return the ABI version of the Vulkan crate."""
    lib = _load_lib()
    if lib is None:
        return 0
    return lib.nyrqis_vulkan_version()


def create_instance() -> int:
    """Create a Vulkan instance.

    Returns an instance ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_vulkan_create_instance()


def destroy_instance(instance_id: int) -> bool:
    """Destroy a Vulkan instance.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_vulkan_destroy_instance(instance_id) == 0


def create_device(instance_id: int) -> int:
    """Create a logical device.

    Returns a device ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_vulkan_create_device(instance_id)


def destroy_device(device_id: int) -> bool:
    """Destroy a logical device.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_vulkan_destroy_device(device_id) == 0


def create_swapchain(device_id: int, width: int, height: int, image_count: int = 3) -> int:
    """Create a swapchain for a Wayland surface.

    Returns a swapchain ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_vulkan_create_swapchain(device_id, width, height, image_count)


def destroy_swapchain(swapchain_id: int) -> bool:
    """Destroy a swapchain.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_vulkan_destroy_swapchain(swapchain_id) == 0


def acquire_next_image(swapchain_id: int) -> int:
    """Acquire the next image from a swapchain.

    Returns the image index on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_vulkan_acquire_next_image(swapchain_id)


def last_error() -> str:
    """Return the last error message from the Vulkan crate."""
    lib = _load_lib()
    if lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    lib.nyrqis_vulkan_last_error(buf, 256)
    return buf.value.decode("utf-8", errors="replace")
