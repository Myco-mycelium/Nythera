"""WaylandDisplay — bridges the Wayland crate to the DesktopSession.

Wraps the low-level ``wayland_codec`` FFI functions into a high-level
display object that the ``DesktopSession`` can use for rendering.
When a Wayland compositor is available, the session renders to real
hardware surfaces; when it's not, the session falls back to the PIL
compositor (software rendering to files).

Architecture:
  DesktopSession
      │
      ├── render() / live_render()
      │         │
      │         ▼
      │   WaylandDisplay (if available)
      │     ├── connect() → wl_display
      │     ├── create_surface() → wl_surface
      │     ├── submit_buffer() → SHM buffer attach + commit
      │     └── dispatch_events() → poll + dispatch
      │
      └── Fallback: PIL Compositor (software rendering)

The display is lazy-initialized: the first call to ``open()`` attempts
the Wayland connection; if it fails (no compositor, headless CI), the
display enters stub mode and all rendering falls back to PIL.

References:
  - ADR-0026: Wayland display-server integration
  - ADR-0020: platform-boundary rule (display path below boundary)
"""

from __future__ import annotations

import ctypes
import logging
import select
from dataclasses import dataclass
from typing import Callable, Optional

from . import wayland_codec

logger = logging.getLogger(__name__)


@dataclass
class WaylandBuffer:
    """Metadata for a submitted Wayland buffer."""
    surface_id: int
    width: int
    height: int
    stride: int


@dataclass
class WaylandConfigureEvent:
    """A surface configure event from the compositor."""
    surface_id: int
    width: int
    height: int


@dataclass
class WaylandCloseEvent:
    """A surface close event from the compositor."""
    surface_id: int


@dataclass
class WaylandKeyEvent:
    """A keyboard event from the compositor."""
    key: int
    state: int  # 0=pressed, 1=released
    surface_id: int = -1


@dataclass
class WaylandPointerEvent:
    """A pointer event from the compositor."""
    x: float
    y: float
    button: int = 0
    state: int = 0  # 0=pressed, 1=released
    surface_id: int = -1


class WaylandDisplay:
    """High-level Wayland display connection.

    Wraps the low-level ``wayland_codec`` FFI functions into a
    manageable interface for the DesktopSession.  Handles connection
    lifecycle, surface creation, and buffer submission.

    Parameters
    ----------
    display_name : str, optional
        Wayland display name (e.g. ``"wayland-0"``).  If None, uses
        the ``WAYLAND_DISPLAY`` environment variable.
    """

    def __init__(self, display_name: Optional[str] = None) -> None:
        self._display_name = display_name
        self._conn_id: int = -1
        self._connected: bool = False
        self._surfaces: dict[int, WaylandBuffer] = {}
        # Event callbacks — invoked when the compositor sends events
        self._on_configure: Optional[Callable[[WaylandConfigureEvent], None]] = None
        self._on_close: Optional[Callable[[WaylandCloseEvent], None]] = None
        self._on_key: Optional[Callable[[WaylandKeyEvent], None]] = None
        self._on_pointer: Optional[Callable[[WaylandPointerEvent], None]] = None

    @property
    def connected(self) -> bool:
        """Whether the display is connected to a Wayland compositor."""
        return self._connected

    @property
    def available(self) -> bool:
        """Whether the Wayland crate is available (not in stub mode)."""
        return not wayland_codec.WAYLAND_STUB

    def open(self) -> bool:
        """Connect to the Wayland display server.

        Returns True on success, False on failure (falls back to PIL).
        """
        if not self.available:
            logger.info("Wayland crate not available — using PIL fallback")
            return False

        if self._connected:
            return True

        self._conn_id = wayland_codec.connect(self._display_name)
        if self._conn_id < 0:
            err = wayland_codec.last_error()
            logger.info("Wayland connection failed (%s) — using PIL fallback", err)
            return False

        self._connected = True
        logger.info("Connected to Wayland display (conn_id=%d)", self._conn_id)
        return True

    def close(self) -> None:
        """Disconnect from the Wayland display."""
        if self._connected and self._conn_id >= 0:
            # Destroy all surfaces first
            for surf_id in list(self._surfaces.keys()):
                self.destroy_surface(surf_id)
            wayland_codec.disconnect(self._conn_id)
            logger.info("Disconnected from Wayland display")
        self._connected = False
        self._conn_id = -1

    def create_surface(self, title: Optional[str] = None) -> int:
        """Create a Wayland surface with xdg-shell decoration.

        Parameters
        ----------
        title : str, optional
            Window title for the xdg_toplevel.

        Returns the surface ID on success, -1 on failure.
        """
        if not self._connected:
            return -1

        surf_id = wayland_codec.create_surface(
            self._conn_id, use_xdg=True, title=title
        )
        if surf_id < 0:
            err = wayland_codec.last_error()
            logger.warning("Failed to create surface: %s", err)
            return -1

        self._surfaces[surf_id] = WaylandBuffer(
            surface_id=surf_id,
            width=0,
            height=0,
            stride=0,
        )
        logger.debug("Created surface %d (title=%s)", surf_id, title)
        return surf_id

    def submit_buffer(
        self,
        surface_id: int,
        pixel_data: bytes,
        width: int,
        height: int,
        stride: int,
    ) -> bool:
        """Submit a pixel buffer to a surface.

        Parameters
        ----------
        surface_id : int
            The surface to submit to.
        pixel_data : bytes
            Raw ARGB8888 pixel data.
        width, height, stride : int
            Buffer dimensions.

        Returns True on success.
        """
        if surface_id not in self._surfaces:
            return False

        result = wayland_codec.submit_buffer(
            surface_id, pixel_data, width, height, stride
        )
        if result < 0:
            err = wayland_codec.last_error()
            logger.warning("Failed to submit buffer: %s", err)
            return False

        # Update stored metadata
        buf = self._surfaces[surface_id]
        self._surfaces[surface_id] = WaylandBuffer(
            surface_id=surface_id,
            width=width,
            height=height,
            stride=stride,
        )
        return True

    def destroy_surface(self, surface_id: int) -> bool:
        """Destroy a surface."""
        if surface_id not in self._surfaces:
            return False

        result = wayland_codec.destroy_surface(surface_id)
        del self._surfaces[surface_id]
        return result == 0

    def set_title(self, surface_id: int, title: str) -> bool:
        """Set the title of an xdg_toplevel surface."""
        if surface_id not in self._surfaces:
            return False
        result = wayland_codec.set_title(surface_id, title)
        return result == 0

    @property
    def fd(self) -> int:
        """Get the display connection file descriptor."""
        if not self._connected:
            return -1
        return wayland_codec.get_fd(self._conn_id)

    @property
    def outputs(self) -> list:
        """Get the list of active outputs (monitors).

        Returns a list of dicts with id, x, y, width, height, scale,
        primary.
        """
        if not self._connected:
            return []
        return wayland_codec.get_outputs()

    # -- Event callbacks ------------------------------------------------

    def on_configure(self, callback: Callable[[WaylandConfigureEvent], None]) -> None:
        """Register a callback for surface configure events."""
        self._on_configure = callback

    def on_close(self, callback: Callable[[WaylandCloseEvent], None]) -> None:
        """Register a callback for surface close events."""
        self._on_close = callback

    def on_key(self, callback: Callable[[WaylandKeyEvent], None]) -> None:
        """Register a callback for keyboard events."""
        self._on_key = callback

    def on_pointer(self, callback: Callable[[WaylandPointerEvent], None]) -> None:
        """Register a callback for pointer events."""
        self._on_pointer = callback

    def dispatch_events(self, timeout_ms: int = 100) -> int:
        """Poll and dispatch pending Wayland events.

        Returns the number of events dispatched, or -1 on error.
        """
        if not self._connected:
            return -1
        return wayland_codec.dispatch_events(self._conn_id, timeout_ms)

    def poll_and_dispatch(self, timeout_s: float = 0.016) -> bool:
        """Poll the Wayland fd and dispatch events.

        Uses select() on the display fd to check for pending events,
        then dispatches them.  Returns True if events were processed.

        Parameters
        ----------
        timeout_s : float
            Maximum time to wait for events (seconds).  Default 16ms
            (one frame at 60fps).
        """
        if not self._connected:
            return False

        fd = self.fd
        if fd < 0:
            return False

        try:
            readable, _, _ = select.select([fd], [], [], timeout_s)
            if readable:
                result = self.dispatch_events(timeout_ms=0)
                return result > 0
        except (OSError, ValueError):
            pass

        return False

    def render_and_submit(self, pil_image) -> bool:
        """Render a PIL Image and submit it to the Wayland surface.

        Combines render_frame() with dispatch_events() for a
        single-frame render cycle.

        Returns True on success.
        """
        if not self._connected or not self._surfaces:
            return False

        # Submit the frame
        success = self.render_frame(pil_image)

        # Process any pending events (configure, close, etc.)
        self.poll_and_dispatch(timeout_s=0)

        return success

    def render_frame(self, pil_image) -> bool:
        """Render a PIL Image to the primary Wayland surface.

        Converts the PIL Image to raw ARGB8888 pixel data and submits
        it to the first active surface.

        Parameters
        ----------
        pil_image : PIL.Image.Image
            The rendered frame.

        Returns True on success, False if Wayland is unavailable.
        """
        if not self._connected or not self._surfaces:
            return False

        # Get the first active surface
        surf_id = next(iter(self._surfaces))

        # Ensure the surface matches the image dimensions
        img_width, img_height = pil_image.size
        buf = self._surfaces[surf_id]
        if buf.width != img_width or buf.height != img_height:
            # Recreate the surface with the correct dimensions
            # (In a real implementation, we'd use wl_surface.damage_buffer
            # and resubmit; for now, just store the new dimensions)
            self._surfaces[surf_id] = WaylandBuffer(
                surface_id=surf_id,
                width=img_width,
                height=img_height,
                stride=img_width * 4,  # ARGB8888 = 4 bytes per pixel
            )

        # Convert PIL Image to raw ARGB8888 bytes
        try:
            import ctypes
            # Convert to RGBA mode if needed
            if pil_image.mode != "RGBA":
                img = pil_image.convert("RGBA")
            else:
                img = pil_image

            raw_data = img.tobytes()
            stride = img_width * 4

            return self.submit_buffer(
                surf_id, raw_data, img_width, img_height, stride
            )
        except Exception as e:
            logger.warning("Failed to render frame: %s", e)
            return False

    def summary(self) -> dict:
        """Display summary for diagnostics."""
        return {
            "available": self.available,
            "connected": self._connected,
            "conn_id": self._conn_id,
            "surfaces": len(self._surfaces),
            "last_error": wayland_codec.last_error() if self.available else "",
        }


__all__ = ["WaylandDisplay", "WaylandBuffer"]
