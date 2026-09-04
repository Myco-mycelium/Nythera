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
    """Native Nyrqis kernel display backend."""

    def enumerate_monitors(self) -> List[Dict]:
        # Will use Nyrqis kernel display API
        return [{"id": "default", "name": "Nyrqis Display", "width": 1920, "height": 1080, "refresh": 60, "connected": True}]

    def set_mode(self, monitor_id: str, width: int, height: int, refresh: int) -> bool:
        return True

    def get_framebuffer(self):
        try:
            from PIL import Image
            return Image.new("RGB", (1920, 1080), (15, 15, 30))
        except ImportError:
            return None


class NyrqisGPUBackend(GPUBackend):
    """Native Nyrqis kernel GPU backend."""

    def initialize(self) -> bool:
        return True

    def allocate_buffer(self, width: int, height: int):
        try:
            from PIL import Image
            return Image.new("RGBA", (width, height), (0, 0, 0, 0))
        except ImportError:
            return None

    def render_frame(self, width: int, height: int):
        return self.allocate_buffer(width, height)


class NyrqisInputBackend(InputBackend):
    """Native Nyrqis kernel input backend."""

    def poll_events(self) -> List[Dict]:
        return []

    def grab_keyboard(self) -> bool:
        return True

    def release_keyboard(self) -> bool:
        return True


class NyrqisCompositorBackend(CompositorBackend):
    """Native Nyrqis kernel compositor backend."""

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


class NyrqisFilesystemBackend(FilesystemBackend):
    """Native Nyrqis kernel filesystem (NyFS)."""

    def read_file(self, path: str) -> Optional[bytes]:
        # Will use NyFS API
        return None

    def write_file(self, path: str, data: bytes) -> bool:
        return False

    def list_dir(self, path: str) -> List[str]:
        return []

    def mkdir(self, path: str) -> bool:
        return False


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
