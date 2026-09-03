"""Backend Abstraction Layer — allows switching between Linux and Nyrqis backends.

This module provides a unified interface for the Nyrqis shell to interact
with the underlying display/compositor backend. The shell and all UI apps
import from this module and call Backend.get() to get the active backend.

When the Nyrqis Rust kernel is ready, it plugs in via NyrqisBackend.
The Linux backend uses PIL/Python as a fallback.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class BackendType(Enum):
    LINUX = "linux"
    NYRQIS = "nyrqis"
    AUTO = "auto"


class PixelFormat(Enum):
    RGBA = "rgba"
    RGB = "rgb"
    BGRA = "bgra"


@dataclass
class SurfaceBuffer:
    """A pixel buffer that can be passed to/from the backend."""
    width: int = 0
    height: int = 0
    data: bytes = b""
    pixel_format: PixelFormat = PixelFormat.RGBA
    stride: int = 0
    timestamp: float = 0.0

    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    @property
    def byte_count(self) -> int:
        return len(self.data)

    @property
    def is_empty(self) -> bool:
        return len(self.data) == 0


@dataclass
class DisplayOutput:
    """Represents a display output/monitor."""
    output_id: int = 0
    name: str = "eDP-1"
    width: int = 1920
    height: int = 1080
    refresh_rate: float = 60.0
    scale: float = 1.0
    x: int = 0
    y: int = 0
    primary: bool = True
    connected: bool = True

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}@{self.refresh_rate:.0f}Hz"

    @property
    def scale_str(self) -> str:
        return f"{self.scale:.1f}x"


@dataclass
class InputEvent:
    """Unified input event."""
    event_type: str = ""  # "key", "mouse_move", "mouse_button", "touch", "scroll"
    timestamp: float = 0.0
    x: float = 0.0
    y: float = 0.0
    button: int = 0
    key_code: int = 0
    key_name: str = ""
    modifiers: List[str] = field(default_factory=list)
    delta_x: float = 0.0
    delta_y: float = 0.0


@dataclass
class BackendCapabilities:
    """What the current backend supports."""
    hardware_acceleration: bool = False
    vulkan: bool = False
    egl: bool = False
    gbm: bool = False
    drm: bool = False
    wayland: bool = False
    opengl: bool = False
    max_texture_size: int = 4096
    multi_monitor: bool = False
    hdr: bool = False
    vsync: bool = True
    opacity: bool = True
    shadows: bool = True
    blur: bool = False
    animations: bool = True


class Backend(ABC):
    """Abstract backend interface.

    All shell components interact with the backend through this interface.
    The active backend is obtained via Backend.get().
    """

    _instance: Optional["Backend"] = None
    _backend_type: BackendType = BackendType.AUTO

    @classmethod
    def get(cls) -> "Backend":
        """Get the active backend instance."""
        if cls._instance is None:
            cls._instance = cls._create_default()
        return cls._instance

    @classmethod
    def set(cls, backend: "Backend"):
        """Set the active backend instance."""
        cls._instance = backend

    @classmethod
    def _create_default(cls) -> "Backend":
        """Create the default backend based on configuration or auto-detection."""
        # Try environment variable first
        env = os.environ.get("NYRQIS_BACKEND", "").lower()
        if env == "nyrqis":
            from .nyrqis_backend import NyrqisBackend
            return NyrqisBackend()
        elif env == "linux":
            from .linux_backend import LinuxBackend
            return LinuxBackend()

        # Auto-detect: try Nyrqis first, fall back to Linux
        if cls._backend_type in (BackendType.NYRQIS, BackendType.AUTO):
            try:
                from .nyrqis_backend import NyrqisBackend
                b = NyrqisBackend()
                if b.is_available():
                    return b
            except Exception:
                pass

        # Fall back to Linux backend
        from .linux_backend import LinuxBackend
        return LinuxBackend()

    @classmethod
    def switch_to(cls, backend_type: BackendType):
        """Switch to a different backend at runtime."""
        if backend_type == BackendType.LINUX:
            from .linux_backend import LinuxBackend
            cls._instance = LinuxBackend()
        elif backend_type == BackendType.NYRQIS:
            from .nyrqis_backend import NyrqisBackend
            cls._instance = NyrqisBackend()
        else:
            cls._instance = cls._create_default()
        cls._backend_type = backend_type

    @classmethod
    def backend_type(cls) -> str:
        """Return the name of the current backend."""
        if cls._instance is None:
            return "none"
        return type(cls._instance).__name__

    # --- Lifecycle ---

    @abstractmethod
    def initialize(self, width: int = 1920, height: int = 1080) -> bool:
        """Initialize the backend. Returns True on success."""
        ...

    @abstractmethod
    def shutdown(self):
        """Shut down the backend and release resources."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available on this system."""
        ...

    # --- Rendering ---

    @abstractmethod
    def begin_frame(self) -> bool:
        """Begin rendering a new frame. Returns True if frame should proceed."""
        ...

    @abstractmethod
    def end_frame(self):
        """End the current frame and present to display."""
        ...

    @abstractmethod
    def clear(self, r: int = 0, g: int = 0, b: int = 0, a: int = 255):
        """Clear the framebuffer to a solid color."""
        ...

    @abstractmethod
    def draw_rect(self, x: int, y: int, w: int, h: int,
                   r: int, g: int, b: int, a: int = 255):
        """Draw a filled rectangle."""
        ...

    @abstractmethod
    def draw_rect_outline(self, x: int, y: int, w: int, h: int,
                           r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        """Draw a rectangle outline."""
        ...

    @abstractmethod
    def draw_text(self, x: int, y: int, text: str,
                   r: int = 255, g: int = 255, b: int = 255, size: int = 14,
                   font: str = ""):
        """Draw text at position."""
        ...

    @abstractmethod
    def draw_line(self, x1: int, y1: int, x2: int, y2: int,
                   r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        """Draw a line between two points."""
        ...

    @abstractmethod
    def draw_circle(self, cx: int, cy: int, radius: int,
                     r: int, g: int, b: int, a: int = 255):
        """Draw a filled circle."""
        ...

    @abstractmethod
    def get_surface(self) -> SurfaceBuffer:
        """Get the current framebuffer as a SurfaceBuffer."""
        ...

    @abstractmethod
    def present(self, buffer: SurfaceBuffer):
        """Present a SurfaceBuffer to the display."""
        ...

    # --- Input ---

    @abstractmethod
    def poll_input(self) -> List[InputEvent]:
        """Poll for input events. Returns list of events since last poll."""
        ...

    # --- Display ---

    @abstractmethod
    def get_outputs(self) -> List[DisplayOutput]:
        """Get list of connected display outputs."""
        ...

    @abstractmethod
    def set_mode(self, output_id: int, width: int, height: int, refresh: float = 60.0):
        """Set display mode for an output."""
        ...

    # --- Capabilities ---

    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Return the backend's capabilities."""
        ...

    @abstractmethod
    def info(self) -> Dict[str, Any]:
        """Return backend information (name, version, renderer, etc.)."""
        ...

    # --- Clipboard ---

    @abstractmethod
    def clipboard_get(self) -> str:
        """Get text from clipboard."""
        ...

    @abstractmethod
    def clipboard_set(self, text: str):
        """Set text to clipboard."""
        ...

    # --- Cursor ---

    @abstractmethod
    def set_cursor(self, cursor_type: str = "default"):
        """Set the mouse cursor type (default, pointer, text, crosshair, etc.)."""
        ...

    @abstractmethod
    def get_cursor_position(self) -> Tuple[float, float]:
        """Get current cursor position."""
        ...

    # --- Window Management (for non-composited mode) ---

    @abstractmethod
    def create_window(self, title: str, width: int, height: int) -> Any:
        """Create a window. Returns window handle."""
        ...

    @abstractmethod
    def destroy_window(self, handle: Any):
        """Destroy a window by handle."""
        ...

    @abstractmethod
    def set_window_title(self, handle: Any, title: str):
        """Set window title."""
        ...

    @abstractmethod
    def set_window_size(self, handle: Any, width: int, height: int):
        """Set window size."""
        ...

    # --- VSync ---

    @abstractmethod
    def set_vsync(self, enabled: bool):
        """Enable or disable vsync."""
        ...

    @abstractmethod
    def get_frame_time_ms(self) -> float:
        """Get time in ms for the last frame."""
        ...

    # --- Screenshot ---

    @abstractmethod
    def screenshot(self) -> Optional[SurfaceBuffer]:
        """Capture the current screen content."""
        ...

    # --- Texture (for hardware backends) ---

    def upload_texture(self, data: bytes, width: int, height: int) -> Any:
        """Upload texture data. Returns texture handle. Default: None."""
        return None

    def draw_texture(self, handle: Any, x: int, y: int, w: int, h: int):
        """Draw a texture. Default: no-op."""
        pass

    def delete_texture(self, handle: Any):
        """Delete a texture. Default: no-op."""
        pass
