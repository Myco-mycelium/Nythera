"""
Nyrqis OS — Backend Abstraction Layer

Provides a unified interface so the shell, compositor, and apps can work
with either the Linux backend (DRM, Wayland, GBM, EGL, Vulkan) or the
native Nyrqis kernel backend without any import-time or runtime breakage.

Usage:
    from ui.backend_abstraction import get_backend, BackendType

    backend = get_backend()  # auto-detects which backend is available
    display = backend.create_display()
    compositor = backend.create_compositor()
"""

from __future__ import annotations

import os
import sys
import importlib
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Protocol


# ---------------------------------------------------------------------------
# Backend Type
# ---------------------------------------------------------------------------

class BackendType(Enum):
    LINUX = "linux"          # DRM + Wayland + GBM + EGL + Vulkan
    NYRQIS = "nyrqis"        # Native Nyrqis kernel
    HEADLESS = "headless"    # No hardware — for CI, testing, rendering


# ---------------------------------------------------------------------------
# Abstract Interfaces (what the shell/apps consume)
# ---------------------------------------------------------------------------

class DisplayBackend(ABC):
    """Abstract display output — modesetting, resolution, gamma."""

    @abstractmethod
    def enumerate_monitors(self) -> List[Dict]:
        """Return list of connected monitors with their modes."""
        ...

    @abstractmethod
    def set_mode(self, monitor_id: str, width: int, height: int, refresh: int) -> bool:
        """Set display mode for a monitor."""
        ...

    @abstractmethod
    def get_framebuffer(self) -> Any:
        """Get a rendering target (PIL Image, framebuffer fd, etc.)."""
        ...


class GPUBackend(ABC):
    """Abstract GPU access — rendering context, buffer allocation."""

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the GPU rendering context."""
        ...

    @abstractmethod
    def allocate_buffer(self, width: int, height: int) -> Any:
        """Allocate a pixel buffer."""
        ...

    @abstractmethod
    def render_frame(self, width: int, height: int) -> Any:
        """Render a complete frame and return it."""
        ...


class InputBackend(ABC):
    """Abstract input handling — keyboard, mouse, touch, gestures."""

    @abstractmethod
    def poll_events(self) -> List[Dict]:
        """Poll for input events."""
        ...

    @abstractmethod
    def grab_keyboard(self) -> bool:
        """Take exclusive keyboard access."""
        ...

    @abstractmethod
    def release_keyboard(self) -> bool:
        """Release keyboard access."""
        ...


class CompositorBackend(ABC):
    """Abstract compositor — window management, surfaces, compositing."""

    @abstractmethod
    def create_surface(self, width: int, height: int) -> Any:
        """Create a new compositor surface."""
        ...

    @abstractmethod
    def destroy_surface(self, surface_id: Any) -> bool:
        """Destroy a surface."""
        ...

    @abstractmethod
    def commit_surface(self, surface_id: Any, damage: Optional[Dict] = None):
        """Commit changes to a surface."""
        ...


class FilesystemBackend(ABC):
    """Abstract filesystem — NyFS or host FS."""

    @abstractmethod
    def read_file(self, path: str) -> Optional[bytes]:
        ...

    @abstractmethod
    def write_file(self, path: str, data: bytes) -> bool:
        ...

    @abstractmethod
    def list_dir(self, path: str) -> List[str]:
        ...

    @abstractmethod
    def mkdir(self, path: str) -> bool:
        ...


# ---------------------------------------------------------------------------
# Linux Backend Implementation
# ---------------------------------------------------------------------------

class LinuxDisplayBackend(DisplayBackend):
    """Linux DRM + Wayland display backend."""

    def __init__(self):
        self._drm_available = False
        self._wayland_available = False
        self._check_available()

    def _check_available(self):
        try:
            self._drm_available = os.path.exists("/dev/dri")
        except Exception:
            pass
        try:
            self._wayland_available = os.environ.get("WAYLAND_DISPLAY") is not None
        except Exception:
            pass

    def enumerate_monitors(self) -> List[Dict]:
        # Try DRM first, fall back to simulated
        monitors = []
        if self._drm_available:
            try:
                from ui.wayland_display import WaylandDisplay
                wd = WaylandDisplay()
                if hasattr(wd, 'monitors'):
                    return wd.monitors
            except Exception:
                pass
        # Simulated monitor for headless/test
        return [{
            "id": "default",
            "name": "Built-in Display",
            "width": 1920,
            "height": 1080,
            "refresh": 60,
            "connected": True,
        }]

    def set_mode(self, monitor_id: str, width: int, height: int, refresh: int) -> bool:
        return True  # Mode set simulated

    def get_framebuffer(self):
        try:
            from PIL import Image
            return Image.new("RGB", (1920, 1080), (15, 15, 30))
        except ImportError:
            return None


class LinuxGPUBackend(GPUBackend):
    """Linux GPU backend — EGL/Vulkan/GBM or headless fallback."""

    def __init__(self):
        self._initialized = False

    def initialize(self) -> bool:
        self._initialized = True
        return True

    def allocate_buffer(self, width: int, height: int):
        try:
            from PIL import Image
            return Image.new("RGBA", (width, height), (0, 0, 0, 0))
        except ImportError:
            return None

    def render_frame(self, width: int, height: int):
        return self.allocate_buffer(width, height)


class LinuxInputBackend(InputBackend):
    """Linux input — evdev or libinput."""

    def __init__(self):
        self._grabbed = False

    def poll_events(self) -> List[Dict]:
        return []

    def grab_keyboard(self) -> bool:
        self._grabbed = True
        return True

    def release_keyboard(self) -> bool:
        self._grabbed = False
        return True


class LinuxCompositorBackend(CompositorBackend):
    """Linux Wayland compositor backend."""

    def __init__(self):
        self._surfaces: Dict = {}

    def create_surface(self, width: int, height: int):
        surface_id = len(self._surfaces)
        self._surfaces[surface_id] = {"width": width, "height": height}
        return surface_id

    def destroy_surface(self, surface_id: Any) -> bool:
        if surface_id in self._surfaces:
            del self._surfaces[surface_id]
            return True
        return False

    def commit_surface(self, surface_id: Any, damage: Optional[Dict] = None):
        pass


class LinuxFilesystemBackend(FilesystemBackend):
    """Linux host filesystem (POSIX)."""

    def read_file(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def write_file(self, path: str, data: bytes) -> bool:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            return False

    def list_dir(self, path: str) -> List[str]:
        try:
            return os.listdir(path)
        except Exception:
            return []

    def mkdir(self, path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Nyrqis Kernel Backend (placeholder — ready for your kernel)
# ---------------------------------------------------------------------------

class NyrqisDisplayBackend(DisplayBackend):
    """Native Nyrqis kernel display backend.

    Uses the Nyrqis kernel's display server API (NyrDisplay).
    Supports multi-monitor, HDR, adaptive refresh, and color management.
    """

    def __init__(self):
        self._monitors: List[Dict] = []
        self._gamma_ramp: Optional[List[int]] = None
        self._hdr_enabled: bool = False
        self._color_profile: str = "srgb"
        self._refresh_override: Optional[int] = None
        self._initialize_monitors()

    def _initialize_monitors(self):
        """Initialize monitor list from Nyrqis display server."""
        # When the Nyrqis kernel is available, this will call:
        #   nyrqis_display_enumerate() -> Vec<MonitorInfo>
        self._monitors = [{
            "id": "nyrqis-default",
            "name": "Nyrqis Display",
            "width": 1920,
            "height": 1080,
            "refresh": 60,
            "connected": True,
            "primary": True,
            "x": 0,
            "y": 0,
            "physical_width_mm": 344,
            "physical_height_mm": 194,
            "edid": None,
            "supported_modes": [
                {"width": 1920, "height": 1080, "refresh": 60},
                {"width": 1920, "height": 1080, "refresh": 120},
                {"width": 1280, "height": 720, "refresh": 60},
                {"width": 3840, "height": 2160, "refresh": 60},
            ],
            "color_depth": 24,
            "hdr_capable": False,
            "adaptive_sync": False,
        }]

    def enumerate_monitors(self) -> List[Dict]:
        return list(self._monitors)

    def set_mode(self, monitor_id: str, width: int, height: int, refresh: int) -> bool:
        for m in self._monitors:
            if m["id"] == monitor_id:
                # Validate against supported modes
                supported = m.get("supported_modes", [])
                if supported:
                    valid = any(
                        mode["width"] == width and mode["height"] == height and mode["refresh"] == refresh
                        for mode in supported
                    )
                    if not valid:
                        return False
                m["width"] = width
                m["height"] = height
                m["refresh"] = refresh
                return True
        return False

    def get_framebuffer(self):
        try:
            from PIL import Image
            primary = next((m for m in self._monitors if m.get("primary")), self._monitors[0])
            return Image.new("RGB", (primary["width"], primary["height"]), (15, 15, 30))
        except ImportError:
            return None

    def set_gamma(self, ramp: List[int]) -> bool:
        """Set gamma ramp (256 entries for R, G, B each)."""
        if len(ramp) != 768:
            return False
        self._gamma_ramp = ramp
        return True

    def get_gamma(self) -> Optional[List[int]]:
        """Get current gamma ramp."""
        return self._gamma_ramp

    def enable_hdr(self, enabled: bool) -> bool:
        """Enable/disable HDR output."""
        self._hdr_enabled = enabled
        return True

    def set_color_profile(self, profile: str) -> bool:
        """Set ICC color profile (srgb, display-p3, adobe-rgb)."""
        valid_profiles = {"srgb", "display-p3", "adobe-rgb", "bt2020", "linear"}
        if profile not in valid_profiles:
            return False
        self._color_profile = profile
        return True

    def set_refresh_override(self, refresh: Optional[int]) -> bool:
        """Force a specific refresh rate, or None for auto."""
        self._refresh_override = refresh
        return True


class NyrqisGPUBackend(GPUBackend):
    """Native Nyrqis kernel GPU backend.

    Uses the Nyrqis kernel's GPU service (NyrGPU) for hardware-accelerated
    rendering via the kernel's built-in OpenGL/Vulkan drivers.
    """

    def __init__(self):
        self._initialized = False
        self._renderer: str = "software"
        self._vendor: str = "Nyrqis"
        self._vram_total: int = 256 * 1024 * 1024  # 256MB default
        self._vram_used: int = 0
        self._buffers: Dict[int, Any] = {}
        self._buffer_counter: int = 0
        self._frame_count: int = 0
        self._vsync: bool = True
        self._shader_cache: Dict[str, Any] = {}

    def initialize(self) -> bool:
        # When the Nyrqis kernel is available:
        #   nyrqis_gpu_init() -> GpuInfo
        self._initialized = True
        self._renderer = "nyrqis-vulkan"
        return True

    def allocate_buffer(self, width: int, height: int):
        try:
            from PIL import Image
            buf_id = self._buffer_counter
            self._buffer_counter += 1
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            self._buffers[buf_id] = {"image": img, "width": width, "height": height}
            self._vram_used += width * height * 4
            return img
        except ImportError:
            return None

    def render_frame(self, width: int, height: int):
        self._frame_count += 1
        return self.allocate_buffer(width, height)

    def get_renderer_info(self) -> Dict:
        """Get GPU/renderer information."""
        return {
            "renderer": self._renderer,
            "vendor": self._vendor,
            "vram_total": self._vram_total,
            "vram_used": self._vram_used,
            "frame_count": self._frame_count,
            "vsync": self._vsync,
            "buffer_count": len(self._buffers),
        }

    def set_vsync(self, enabled: bool) -> bool:
        """Enable/disable vertical sync."""
        self._vsync = enabled
        return True

    def compile_shader(self, shader_id: str, source: str) -> bool:
        """Compile a GPU shader and cache it."""
        self._shader_cache[shader_id] = {"source": source, "compiled": True}
        return True

    def get_shader(self, shader_id: str) -> Optional[Dict]:
        """Get a compiled shader from cache."""
        return self._shader_cache.get(shader_id)

    def free_buffer(self, buf_id: int) -> bool:
        """Free a GPU buffer."""
        if buf_id in self._buffers:
            buf = self._buffers.pop(buf_id)
            self._vram_used -= buf["width"] * buf["height"] * 4
            return True
        return False


class NyrqisInputBackend(InputBackend):
    """Native Nyrqis kernel input backend.

    Uses the Nyrqis kernel's input subsystem (NyrInput) which provides
    keyboard, mouse, touchpad, touchscreen, gamepad, and stylus input.
    """

    def __init__(self):
        self._grabbed = False
        self._grab_exclusive = False
        self._key_repeat_delay: int = 500
        self._key_repeat_rate: int = 30
        self._mouse_sensitivity: float = 1.0
        self._touchpad_enabled: bool = True
        self._tap_to_click: bool = True
        self._natural_scroll: bool = False
        self._events: List[Dict] = []

    def poll_events(self) -> List[Dict]:
        events = list(self._events)
        self._events.clear()
        return events

    def push_event(self, event: Dict):
        """Push an event to the input queue (for testing/simulation)."""
        self._events.append(event)

    def grab_keyboard(self) -> bool:
        self._grabbed = True
        self._grab_exclusive = True
        return True

    def release_keyboard(self) -> bool:
        self._grabbed = False
        self._grab_exclusive = False
        return True

    def set_key_repeat(self, delay_ms: int, rate: int) -> bool:
        """Configure key repeat delay and rate."""
        if delay_ms < 0 or rate < 0:
            return False
        self._key_repeat_delay = delay_ms
        self._key_repeat_rate = rate
        return True

    def set_mouse_sensitivity(self, sensitivity: float) -> bool:
        """Set mouse sensitivity (0.1 to 5.0)."""
        if sensitivity < 0.1 or sensitivity > 5.0:
            return False
        self._mouse_sensitivity = sensitivity
        return True

    def configure_touchpad(self, tap_to_click: bool = True, natural_scroll: bool = False) -> bool:
        """Configure touchpad behavior."""
        self._tap_to_click = tap_to_click
        self._natural_scroll = natural_scroll
        return True

    def get_input_devices(self) -> List[Dict]:
        """List connected input devices."""
        return [
            {"type": "keyboard", "name": "Nyrqis Keyboard", "enabled": True},
            {"type": "mouse", "name": "Nyrqis Mouse", "enabled": True},
            {"type": "touchpad", "name": "Nyrqis Touchpad", "enabled": self._touchpad_enabled},
        ]


class NyrqisCompositorBackend(CompositorBackend):
    """Native Nyrqis kernel compositor backend.

    Uses the Nyrqis kernel's compositor (NyrCompositor) for surface
    management, damage tracking, layer composition, and presentation.
    """

    def __init__(self):
        self._surfaces: Dict = {}
        self._surface_counter: int = 0
        self._layers: List[str] = ["background", "bottom", "normal", "top", "overlay", "notification"]
        self._damage_regions: List[Dict] = []
        self._vsync_pending: bool = False
        self._frame_pending: bool = False

    def create_surface(self, width: int, height: int):
        surface_id = self._surface_counter
        self._surface_counter += 1
        self._surfaces[surface_id] = {
            "width": width,
            "height": height,
            "x": 0,
            "y": 0,
            "visible": True,
            "opacity": 1.0,
            "z_order": surface_id,
            "layer": "normal",
            "input_region": None,
            "damaged": False,
        }
        return surface_id

    def destroy_surface(self, surface_id: Any) -> bool:
        if surface_id in self._surfaces:
            del self._surfaces[surface_id]
            return True
        return False

    def commit_surface(self, surface_id: Any, damage: Optional[Dict] = None):
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["damaged"] = True
            if damage:
                self._damage_regions.append({"surface": surface_id, **damage})

    def move_surface(self, surface_id: Any, x: int, y: int) -> bool:
        """Move a surface to a new position."""
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["x"] = x
            self._surfaces[surface_id]["y"] = y
            return True
        return False

    def resize_surface(self, surface_id: Any, width: int, height: int) -> bool:
        """Resize a surface."""
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["width"] = width
            self._surfaces[surface_id]["height"] = height
            return True
        return False

    def set_surface_opacity(self, surface_id: Any, opacity: float) -> bool:
        """Set surface opacity (0.0 to 1.0)."""
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["opacity"] = max(0.0, min(1.0, opacity))
            return True
        return False

    def set_surface_layer(self, surface_id: Any, layer: str) -> bool:
        """Set surface layer (background, bottom, normal, top, overlay, notification)."""
        if surface_id in self._surfaces and layer in self._layers:
            self._surfaces[surface_id]["layer"] = layer
            return True
        return False

    def set_surface_visible(self, surface_id: Any, visible: bool) -> bool:
        """Show or hide a surface."""
        if surface_id in self._surfaces:
            self._surfaces[surface_id]["visible"] = visible
            return True
        return False

    def get_surface_count(self) -> int:
        return len(self._surfaces)

    def clear_damage(self):
        """Clear all damage regions after composition."""
        self._damage_regions.clear()
        for s in self._surfaces.values():
            s["damaged"] = False


class NyrqisFilesystemBackend(FilesystemBackend):
    """Native Nyrqis kernel filesystem (NyFS).

    Uses the Nyrqis kernel's built-in filesystem (NyFS) which provides
    copy-on-write snapshots, deduplication, integrity checking,
    and per-process sandboxing.
    """

    def __init__(self):
        self._files: Dict[str, bytes] = {}
        self._dirs: Dict[str, bool] = {"/": True}
        self._snapshots: List[str] = []
        self._sandbox_enabled: bool = True
        self._mount_points: Dict[str, str] = {"/": "nyfs"}

    def read_file(self, path: str) -> Optional[bytes]:
        return self._files.get(path)

    def write_file(self, path: str, data: bytes) -> bool:
        self._files[path] = data
        return True

    def list_dir(self, path: str) -> List[str]:
        prefix = path.rstrip("/") + "/"
        entries = set()
        for key in self._files:
            if key.startswith(prefix):
                rest = key[len(prefix):].split("/")[0]
                entries.add(rest)
        for key in self._dirs:
            if key.startswith(prefix) and key != path:
                rest = key[len(prefix):].split("/")[0]
                if rest:
                    entries.add(rest)
        return sorted(entries)

    def mkdir(self, path: str) -> bool:
        self._dirs[path] = True
        return True

    def create_snapshot(self, name: str) -> bool:
        """Create a filesystem snapshot."""
        self._snapshots.append(name)
        return True

    def list_snapshots(self) -> List[str]:
        """List all filesystem snapshots."""
        return list(self._snapshots)

    def set_sandbox(self, enabled: bool):
        """Enable or disable per-process sandboxing."""
        self._sandbox_enabled = enabled

    def stat_file(self, path: str) -> Optional[Dict]:
        """Get file metadata."""
        if path in self._files:
            return {
                "path": path,
                "size": len(self._files[path]),
                "type": "file",
                "readable": True,
                "writable": True,
            }
        return None

    def delete_file(self, path: str) -> bool:
        if path in self._files:
            del self._files[path]
            return True
        return False

    def get_mount_points(self) -> Dict[str, str]:
        """Get all mount points and their filesystem types."""
        return dict(self._mount_points)


# ---------------------------------------------------------------------------
# Headless Backend (for CI, testing, rendering)
# ---------------------------------------------------------------------------

class HeadlessDisplayBackend(DisplayBackend):
    def enumerate_monitors(self) -> List[Dict]:
        return [{"id": "headless", "name": "Headless", "width": 1280, "height": 720, "refresh": 60, "connected": True}]
    def set_mode(self, monitor_id, width, height, refresh): return True
    def get_framebuffer(self):
        try:
            from PIL import Image
            return Image.new("RGB", (1280, 720), (15, 15, 30))
        except ImportError:
            return None

class HeadlessGPUBackend(GPUBackend):
    def initialize(self): return True
    def allocate_buffer(self, w, h):
        try:
            from PIL import Image
            return Image.new("RGBA", (w, h), (0, 0, 0, 0))
        except ImportError:
            return None
    def render_frame(self, w, h): return self.allocate_buffer(w, h)

class HeadlessInputBackend(InputBackend):
    def poll_events(self): return []
    def grab_keyboard(self): return True
    def release_keyboard(self): return True

class HeadlessCompositorBackend(CompositorBackend):
    def __init__(self):
        self._surfaces: Dict = {}
    def create_surface(self, w, h):
        sid = len(self._surfaces)
        self._surfaces[sid] = {"width": w, "height": h}
        return sid
    def destroy_surface(self, sid):
        self._surfaces.pop(sid, None)
        return True
    def commit_surface(self, sid, damage=None): pass

class HeadlessFilesystemBackend(FilesystemBackend):
    def __init__(self):
        self._files: Dict[str, bytes] = {}
    def read_file(self, path): return self._files.get(path)
    def write_file(self, path, data):
        self._files[path] = data
        return True
    def list_dir(self, path): return []
    def mkdir(self, path): return True


# ---------------------------------------------------------------------------
# Backend Registry
# ---------------------------------------------------------------------------

@dataclass
class BackendSet:
    display: DisplayBackend
    gpu: GPUBackend
    input_backend: InputBackend
    compositor: CompositorBackend
    filesystem: FilesystemBackend
    backend_type: BackendType = BackendType.HEADLESS


# ---------------------------------------------------------------------------
# Auto-detect and provide backend
# ---------------------------------------------------------------------------

_current_backend: Optional[BackendSet] = None
_backend_type: Optional[BackendType] = None


def detect_backend() -> BackendType:
    """Auto-detect which backend to use."""
    # Check environment variable override
    env_backend = os.environ.get("NYRQIS_BACKEND", "").lower()
    if env_backend == "nyrqis":
        return BackendType.NYRQIS
    elif env_backend == "linux":
        return BackendType.LINUX
    elif env_backend == "headless":
        return BackendType.HEADLESS

    # Check for Nyrqis kernel
    if os.path.exists("/dev/nyrqis"):
        return BackendType.NYRQIS

    # Check for Linux DRM
    if os.path.exists("/dev/dri"):
        return BackendType.LINUX

    return BackendType.HEADLESS


def get_backend(backend_type: Optional[BackendType] = None) -> BackendSet:
    """Get or create the backend set."""
    global _current_backend, _backend_type

    if _current_backend is not None and backend_type is None:
        return _current_backend

    if backend_type is None:
        backend_type = detect_backend()

    _backend_type = backend_type

    if backend_type == BackendType.LINUX:
        _current_backend = BackendSet(
            display=LinuxDisplayBackend(),
            gpu=LinuxGPUBackend(),
            input_backend=LinuxInputBackend(),
            compositor=LinuxCompositorBackend(),
            filesystem=LinuxFilesystemBackend(),
            backend_type=BackendType.LINUX,
        )
    elif backend_type == BackendType.NYRQIS:
        _current_backend = BackendSet(
            display=NyrqisDisplayBackend(),
            gpu=NyrqisGPUBackend(),
            input_backend=NyrqisInputBackend(),
            compositor=NyrqisCompositorBackend(),
            filesystem=NyrqisFilesystemBackend(),
            backend_type=BackendType.NYRQIS,
        )
    else:
        _current_backend = BackendSet(
            display=HeadlessDisplayBackend(),
            gpu=HeadlessGPUBackend(),
            input_backend=HeadlessInputBackend(),
            compositor=HeadlessCompositorBackend(),
            filesystem=HeadlessFilesystemBackend(),
            backend_type=BackendType.HEADLESS,
        )

    return _current_backend


def switch_backend(backend_type: BackendType) -> BackendSet:
    """Switch to a different backend. Returns the new backend set."""
    global _current_backend, _backend_type
    _current_backend = None
    return get_backend(backend_type)


def reset_backend():
    """Reset the backend cache so next get_backend() creates a fresh instance."""
    global _current_backend, _backend_type
    _current_backend = None
    _backend_type = None
