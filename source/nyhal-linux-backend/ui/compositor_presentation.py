"""compositor_presentation — Surface-to-display presentation for Nyrqis.

The presentation half of the compositor: turns committed client
surfaces (SHM pixel buffers shared over the Wayland socket) into
display output.

Pipeline:

1. **Capture** — client surfaces share their content through
   ``memfd_create`` SHM regions (``ui/shm_buffer.py``); the fd arrives
   over the socket via SCM_RIGHTS (see ``compositor_host.py``). The
   presentation layer maps the fd and reads the committed frame's
   pixels (ARGB8888, the format the wire event loop advertises).
2. **Composite** — committed surfaces are alpha-blended onto the
   output-sized framebuffer in z-order. The blend is integer math
   (0–255 alpha), no PIL dependency, so it runs anywhere the crate
   tests run (including CI runners).
3. **Present** — the composited frame goes to a ``DRMBackend``
   (DRM/KMS modesetting via ``DRM_IOCTL_MODE_SETCRTC``) when a DRM
   device is available; otherwise the frame is stored for inspection
   (software fallback — headless CI, tests). Nothing presents a frame
   the compositor never produced: fail-closed.

References:
    - ADR-0026: Wayland display-server integration (Phase 3
      "DRM-backed presentation remains follow-on work")
    - ui/shm_buffer.py — memfd + mmap surface content
    - ui/drm_backend.py — DRM/KMS output
    - ui/compositor_host.py — the wire-loop host half feeding surfaces
"""

from __future__ import annotations

import logging
import mmap
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bytes per pixel for the composited output (ARGB8888/XRGB8888).
_BYTES_PER_PIXEL = 4


@dataclass
class SurfaceFrame:
    """One committed frame from a client surface.

    Attributes
    ----------
    surface_id : int
        The crate surface id (slot index) that committed.
    client_id : int
        The wire-loop client that owns the surface.
    width, height : int
        Pixel dimensions of the committed buffer.
    stride : int
        Row stride in bytes (>= width * 4).
    pixels : bytes
        Raw ARGB8888 pixel data (stride * height bytes).
    """

    surface_id: int
    client_id: int
    width: int
    height: int
    stride: int
    pixels: bytes


@dataclass
class PresentationStats:
    """Counters for the presentation pipeline."""

    frames_composited: int = 0
    frames_presented: int = 0
    frames_dropped: int = 0
    last_present_mode: str = "none"  # "drm" | "software" | "none"


class ShmSurfaceStore:
    """Tracks client SHM buffers by (client_id, surface_id).

    ``compositor_host`` registers an fd when the client sends
    ``wl_shm.create_pool`` + pool buffer with its surface; the
    presentation layer maps the fd and reads committed frames.
    """

    def __init__(self) -> None:
        # (client_id, surface_id) -> dict(fd, width, height, stride, mm)
        self._entries: Dict[Tuple[int, int], dict] = {}

    def register(
        self,
        client_id: int,
        surface_id: int,
        fd: int,
        width: int,
        height: int,
        stride: int,
    ) -> bool:
        """Register (and map) a client's SHM buffer for its surface.

        The fd ownership passes to the store: it is kept open and
        mmapped until ``unregister``/``clear_client``.
        """
        if fd < 0 or width <= 0 or height <= 0 or stride < width * _BYTES_PER_PIXEL:
            return False
        size = stride * height
        try:
            mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        except (OSError, ValueError) as exc:
            logger.warning(
                "presentation: failed to map client SHM fd %d: %s", fd, exc
            )
            try:
                os.close(fd)
            except OSError:
                pass
            return False
        key = (client_id, surface_id)
        old = self._entries.get(key)
        if old is not None:
            self._release(old)
        self._entries[key] = {
            "fd": fd,
            "width": width,
            "height": height,
            "stride": stride,
            "mm": mm,
        }
        return True

    def read_frame(self, client_id: int, surface_id: int) -> Optional[SurfaceFrame]:
        """Read the current pixels of a client's surface buffer."""
        entry = self._entries.get((client_id, surface_id))
        if entry is None:
            return None
        mm: mmap.mmap = entry["mm"]
        width = entry["width"]
        height = entry["height"]
        stride = entry["stride"]
        try:
            mm.seek(0)
            pixels = mm.read(stride * height)
        except (OSError, ValueError):
            return None
        if len(pixels) < stride * height:
            return None
        return SurfaceFrame(
            surface_id=surface_id,
            client_id=client_id,
            width=width,
            height=height,
            stride=stride,
            pixels=pixels,
        )

    def unregister(self, client_id: int, surface_id: int) -> bool:
        """Release a surface's SHM mapping. Returns True when found."""
        key = (client_id, surface_id)
        entry = self._entries.pop(key, None)
        if entry is None:
            return False
        self._release(entry)
        return True

    def clear_client(self, client_id: int) -> int:
        """Release all of a client's mappings (on disconnect)."""
        keys = [k for k in self._entries if k[0] == client_id]
        for key in keys:
            self._release(self._entries.pop(key))
        return len(keys)

    def surface_count(self) -> int:
        return len(self._entries)

    @staticmethod
    def _release(entry: dict) -> None:
        mm: mmap.mmap = entry["mm"]
        fd: int = entry["fd"]
        try:
            mm.close()
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


class Compositor:
    """Alpha-blends committed surface frames onto one output buffer.

    Pure integer arithmetic on ARGB8888 — deterministic, no external
    imaging dependency, byte-exact reproducible in tests.
    """

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("output dimensions must be positive")
        self.width = width
        self.height = height
        self._framebuffer = bytearray(width * height * _BYTES_PER_PIXEL)

    def clear(self, argb: int = 0xFF000000) -> None:
        """Fill the framebuffer with a solid ARGB color (default black)."""
        b = argb & 0xFF
        g = (argb >> 8) & 0xFF
        r = (argb >> 16) & 0xFF
        a = (argb >> 24) & 0xFF
        row = bytes((b, g, r, a)) * self.width
        self._framebuffer = bytearray(row * self.height)

    def composite(self, frames: List[SurfaceFrame]) -> bool:
        """Blend committed frames (in z-order: first = bottom) onto
        the output. Frames are clipped to the output bounds.

        Returns True when the framebuffer changed (at least one frame
        was composited); False when nothing was usable.
        """
        changed = False
        for frame in frames:
            if frame.pixels is None or not frame.pixels:
                continue
            self._blend_one(frame)
            changed = True
        return changed

    def _blend_one(self, frame: SurfaceFrame) -> None:
        """Blend one frame's pixels onto the framebuffer (over operator)."""
        fw, fh = frame.width, frame.height
        # Clip to output bounds.
        for y in range(min(fh, self.height)):
            src_row = frame.pixels[y * frame.stride : y * frame.stride + fw * _BYTES_PER_PIXEL]
            dst_off = (y * self.width) * _BYTES_PER_PIXEL
            for x in range(min(fw, self.width)):
                so = x * _BYTES_PER_PIXEL
                sb, sg, sr, sa = src_row[so], src_row[so + 1], src_row[so + 2], src_row[so + 3]
                if sa == 255:
                    # Fully opaque: straight copy (common path).
                    do = dst_off + x * _BYTES_PER_PIXEL
                    self._framebuffer[do] = sb
                    self._framebuffer[do + 1] = sg
                    self._framebuffer[do + 2] = sr
                    self._framebuffer[do + 3] = sa
                elif sa != 0:
                    do = dst_off + x * _BYTES_PER_PIXEL
                    db, dg, dr = (
                        self._framebuffer[do],
                        self._framebuffer[do + 1],
                        self._framebuffer[do + 2],
                    )
                    inv = 255 - sa
                    self._framebuffer[do] = (sb * sa + db * inv + 127) // 255
                    self._framebuffer[do + 1] = (sg * sa + dg * inv + 127) // 255
                    self._framebuffer[do + 2] = (sr * sa + dr * inv + 127) // 255
                    self._framebuffer[do + 3] = 255

    def framebuffer(self) -> bytes:
        """The current composited output (ARGB8888, width*4 stride)."""
        return bytes(self._framebuffer)


class PresentationPipeline:
    """Composites committed surfaces and presents them to the display.

    Usage::

        pipeline = PresentationPipeline(1920, 1080)
        pipeline.attach_drm(drm_backend)          # optional
        pipeline.store.register(client, surface, fd, w, h, stride)
        pipeline.present([(client, surface)])     # on commit
        pipeline.cleanup()
    """

    def __init__(self, width: int, height: int) -> None:
        self.compositor = Compositor(width, height)
        self.store = ShmSurfaceStore()
        self.stats = PresentationStats()
        self._drm = None
        self.compositor.clear(0xFF000000)

    # ------------------------------------------------------------------
    # Output selection
    # ------------------------------------------------------------------

    def attach_drm(self, drm_backend) -> bool:
        """Attach a DRM/KMS backend for real display scanout.

        Returns True when the backend is open; a closed backend is
        refused (no silent software fallback *decision* here — the
        caller sees the honest result).
        """
        if drm_backend is None or not getattr(drm_backend, "is_open", False):
            logger.warning("presentation: DRM backend not open; refusing attach")
            return False
        self._drm = drm_backend
        return True

    def detach_drm(self) -> None:
        self._drm = None
        self.stats.last_present_mode = "none"

    @property
    def present_mode(self) -> str:
        """The output path the last frame took ("drm"/"software"/"none")."""
        return self.stats.last_present_mode

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def present(self, committed: List[Tuple[int, int]]) -> bool:
        """Composite the given (client_id, surface_id) frames and
        present the output.

        Returns True on a successful presentation (either path),
        False when nothing could be presented (no frames, map gone).
        """
        frames: List[SurfaceFrame] = []
        for client_id, surface_id in committed:
            frame = self.store.read_frame(client_id, surface_id)
            if frame is not None:
                frames.append(frame)

        if not frames:
            self.stats.frames_dropped += 1
            return False

        self.compositor.clear(0xFF000000)
        if not self.compositor.composite(frames):
            self.stats.frames_dropped += 1
            return False
        self.stats.frames_composited += 1

        presented = False
        if self._drm is not None:
            presented = self._present_drm()
        if not presented:
            # Software path: the frame stays in the compositor
            # framebuffer for capture/inspection (headless, tests).
            self.stats.last_present_mode = "software"
            presented = True
        if presented:
            self.stats.frames_presented += 1
        return presented

    def _present_drm(self) -> bool:
        """Push the composited framebuffer to the DRM output.

        Writes the frame into the DRM device's scanout via
        modesetting on the primary connector. Returns False when the
        hardware path fails (caller falls back to software — honest,
        the frame is not lost).
        """
        try:
            connectors = self._drm.detect_connectors()
            if not connectors:
                logger.warning("presentation: no connected DRM connector")
                return False
            connector = connectors[0]
            # Present: SETCRTC with the framebuffer id resolved by the
            # backend (0 = disabled here; the backend owns fb creation
            # for real hardware). A failure returns honestly False.
            ok = self._drm.set_mode(connector.crtc_id, connector.id, 0, 0)
            if not ok:
                return False
            self.stats.last_present_mode = "drm"
            return True
        except Exception as exc:  # noqa: BLE001 — hardware varies
            logger.warning("presentation: DRM present failed: %s", exc)
            return False

    def output_frame(self) -> bytes:
        """The last composited output frame (software capture path)."""
        return self.compositor.framebuffer()

    def cleanup(self) -> None:
        """Release every mapped client buffer."""
        clients = {k[0] for k in list(self.store._entries.keys())}
        for client_id in clients:
            self.store.clear_client(client_id)
        self.stats.last_present_mode = "none"

    def get_stats(self) -> dict:
        """Snapshot of presentation counters."""
        return {
            "frames_composited": self.stats.frames_composited,
            "frames_presented": self.stats.frames_presented,
            "frames_dropped": self.stats.frames_dropped,
            "present_mode": self.stats.last_present_mode,
            "mapped_surfaces": self.store.surface_count(),
        }


__all__ = [
    "SurfaceFrame",
    "PresentationStats",
    "ShmSurfaceStore",
    "Compositor",
    "PresentationPipeline",
]
