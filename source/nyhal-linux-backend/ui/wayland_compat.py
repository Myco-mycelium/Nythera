"""wayland_compat — Wayland client compatibility layer for common apps.

Provides a high-level interface that mimics common Wayland client libraries:

1. Surface creation and management
2. Buffer submission via SHM
3. Frame callbacks
4. Keyboard and pointer input
5. Window state management (title, app_id, size)

This allows testing the compositor with simulated client behavior
without requiring real Wayland client libraries.

References:
    - ADR-0026: Wayland display-server integration
    - Wayland protocol: https://wayland.freedesktop.org/docs/html/
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SurfaceState:
    """State of a Wayland surface."""
    id: int
    width: int = 0
    height: int = 0
    buffer_id: int = -1
    title: str = ""
    app_id: str = ""
    configured: bool = False
    active: bool = True


@dataclass
class KeyEvent:
    """A keyboard event."""
    time: int
    key: int
    state: int  # 0=pressed, 1=released


@dataclass
class PointerEvent:
    """A pointer event."""
    time: int
    x: float = 0.0
    y: float = 0.0
    button: int = 0
    state: int = 0


class WaylandCompatClient:
    """High-level Wayland client compatibility layer.
    
    Provides a simplified interface for Wayland operations that
    mimics common client library behavior.
    
    Usage:
        client = WaylandCompatClient("/tmp/wayland-0")
        client.connect()
        
        # Create window
        window = client.create_window("My App", 800, 600)
        
        # Render frame
        window.attach_buffer(pixel_data)
        window.commit()
        
        # Handle events
        events = client.poll_events()
        
        client.disconnect()
    """
    
    def __init__(self, socket_path: str = "/tmp/wayland-0"):
        self.socket_path = socket_path
        self._client = None
        self._windows: Dict[int, SurfaceState] = {}
        self._events: List[Dict] = []
        self._key_handlers: List[Callable] = []
        self._pointer_handlers: List[Callable] = []
        self._frame_handlers: List[Callable] = []
        
        # Input state
        self._pointer_x = 0.0
        self._pointer_y = 0.0
        self._keyboard_focus: Optional[int] = None
    
    def connect(self) -> bool:
        """Connect to the compositor."""
        from ui.wayland_client import WaylandClientTest
        
        self._client = WaylandClientTest(self.socket_path)
        if not self._client.connect():
            return False
        
        # Perform Wayland handshake
        self._client.sync()
        self._client.get_registry()
        
        # Wait for registry globals
        events = self._client.receive_events(timeout=0.5)
        
        # Bind required globals
        # In a real client, we'd parse registry.global events
        # For testing, we'll just create the objects
        self._client._wl_compositor = 100
        self._client._wl_shm = 101
        
        logger.info("Connected to compositor with handshake")
        return True
    
    def disconnect(self):
        """Disconnect from compositor."""
        if self._client:
            self._client.disconnect()
            self._client = None
    
    def create_window(self, title: str, width: int, height: int) -> Window:
        """Create a new window.
        
        Parameters
        ----------
        title : str
            Window title.
        width : int
            Window width in pixels.
        height : int
            Window height in pixels.
            
        Returns
        -------
        Window
            The created window.
        """
        if not self._client:
            raise RuntimeError("Not connected")
        
        # Create surface
        surface_id = self._client.create_surface(width, height)
        
        # Create SHM pool
        stride = width * 4  # ARGB8888
        pool_size = stride * height
        pool_id = self._client.create_shm_pool(pool_size)
        
        # Create buffer
        buffer_id = self._client.create_buffer(pool_id, 0, width, height, stride)
        
        # Track surface state
        state = SurfaceState(
            id=surface_id,
            width=width,
            height=height,
            buffer_id=buffer_id,
            title=title,
        )
        self._windows[surface_id] = state
        
        # Create window wrapper
        window = Window(self, state)
        
        logger.info("Created window: %s (%dx%d)", title, width, height)
        return window
    
    def poll_events(self) -> List[Dict]:
        """Poll for events.
        
        Returns
        -------
        list of dict
            List of events.
        """
        if not self._client:
            return []
        
        # Receive events from compositor
        events = self._client.receive_events(timeout=0.01)
        
        # Convert to high-level events
        high_level_events = []
        for event in events:
            hl_event = self._convert_event(event)
            if hl_event:
                high_level_events.append(hl_event)
                self._events.append(hl_event)
        
        return high_level_events
    
    def _convert_event(self, event) -> Optional[Dict]:
        """Convert a low-level Wayland event to high-level."""
        # This is a simplified conversion
        # Real implementation would parse the event data properly
        
        return {
            "type": "wayland",
            "object_id": event.object_id,
            "opcode": event.opcode,
            "timestamp": event.timestamp,
        }
    
    def on_key(self, handler: Callable):
        """Register a key event handler."""
        self._key_handlers.append(handler)
    
    def on_pointer(self, handler: Callable):
        """Register a pointer event handler."""
        self._pointer_handlers.append(handler)
    
    def on_frame(self, handler: Callable):
        """Register a frame callback handler."""
        self._frame_handlers.append(handler)
    
    def get_windows(self) -> List[SurfaceState]:
        """Get all windows."""
        return list(self._windows.values())
    
    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._client is not None and self._client.is_connected


class Window:
    """High-level Wayland window.
    
    Provides a simplified interface for window operations.
    """
    
    def __init__(self, client: WaylandCompatClient, state: SurfaceState):
        self._client = client
        self._state = state
        self._pixel_data: Optional[bytes] = None
    
    @property
    def id(self) -> int:
        """Window ID."""
        return self._state.id
    
    @property
    def title(self) -> str:
        """Window title."""
        return self._state.title
    
    @property
    def width(self) -> int:
        """Window width."""
        return self._state.width
    
    @property
    def height(self) -> int:
        """Window height."""
        return self._state.height
    
    def set_title(self, title: str):
        """Set window title."""
        self._state.title = title
        # Send xdg_toplevel.set_title request
        if self._client._client:
            # XDG toplevel object ID would be tracked
            pass
    
    def set_app_id(self, app_id: str):
        """Set application ID."""
        self._state.app_id = app_id
    
    def set_size(self, width: int, height: int):
        """Set window size."""
        self._state.width = width
        self._state.height = height
    
    def attach_buffer(self, pixel_data: bytes) -> bool:
        """Attach pixel data to the window.
        
        Parameters
        ----------
        pixel_data : bytes
            Pixel data in ARGB8888 format.
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self._client._client:
            return False
        
        self._pixel_data = pixel_data
        
        # Attach buffer to surface
        return self._client._client.attach_buffer(
            self._state.id,
            self._state.buffer_id,
        )
    
    def commit(self) -> bool:
        """Commit the surface.
        
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self._client._client:
            return False
        
        return self._client._client.commit_surface(self._state.id)
    
    def damage(self, x: int = 0, y: int = 0, w: int = 0, h: int = 0) -> bool:
        """Damage a region of the surface.
        
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self._client._client:
            return False
        
        return self._client._client.damage_surface(
            self._state.id,
            x, y, w, h,
        )
    
    def render_solid(self, r: int, g: int, b: int, a: int = 255):
        """Render a solid color to the window.
        
        Parameters
        ----------
        r, g, b, a : int
            Color components (0-255).
        """
        pixel = struct.pack("BBBB", b, g, r, a)
        row = pixel * self._state.width
        data = row * self._state.height
        self.attach_buffer(data)
    
    def close(self):
        """Close the window."""
        if self._client._client:
            self._client._client.destroy_surface(self._state.id)
        self._state.active = False
        
        if self._state.id in self._client._windows:
            del self._client._windows[self._state.id]
        
        logger.info("Closed window: %s", self._state.title)
