"""ImageViewer — Image viewing UI for Nyrqis.

Provides image viewing with:
- Open and display images (simulated)
- Zoom in/out with fit-to-window
- Rotate (90° increments) and flip
- Thumbnail gallery sidebar
- Image info (dimensions, size, format)
- Slideshow mode
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ZoomMode(Enum):
    FIT_WINDOW = auto()
    FIT_WIDTH = auto()
    FIT_HEIGHT = auto()
    ACTUAL_SIZE = auto()
    CUSTOM = auto()


class RotateAngle(Enum):
    NONE = auto()
    CW_90 = auto()
    CW_180 = auto()
    CW_270 = auto()


class ImageView(Enum):
    SINGLE = auto()
    GALLERY = auto()
    COMPARE = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ImageInfo:
    """Metadata for an image."""
    path: str
    name: str = ""
    width: int = 0
    height: int = 0
    format: str = "PNG"
    file_size: int = 0
    color_depth: int = 24
    has_alpha: bool = False
    modified: float = 0.0
    color: Tuple[int, int, int] = (80, 80, 100)  # dominant color

    @property
    def display_size(self) -> str:
        return f"{self.width} × {self.height}"

    @property
    def display_file_size(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        if self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def aspect_ratio(self) -> str:
        if self.width == 0 or self.height == 0:
            return "—"
        from math import gcd
        d = gcd(self.width, self.height)
        return f"{self.width // d}:{self.height // d}"


# ---------------------------------------------------------------------------
# Default gallery images (simulated)
# ---------------------------------------------------------------------------

GALLERY_IMAGES = [
    ImageInfo("/tmp/wallpaper-eclipse.png", "Eclipse Dark",
              1920, 1080, "PNG", 2_400_000, color=(20, 22, 36)),
    ImageInfo("/tmp/wallpaper-nord.png", "Nord Frost",
              2560, 1440, "PNG", 3_100_000, color=(46, 52, 64)),
    ImageInfo("/tmp/screenshot-desktop.png", "Desktop Screenshot",
              1920, 1080, "PNG", 1_800_000, color=(30, 30, 40)),
    ImageInfo("/tmp/photo-001.jpg", "Mountain Landscape",
              3840, 2160, "JPEG", 4_500_000, color=(80, 120, 60)),
    ImageInfo("/tmp/photo-002.jpg", "City at Night",
              1920, 1080, "JPEG", 2_200_000, color=(20, 30, 60)),
    ImageInfo("/tmp/icon-terminal.png", "Terminal Icon",
              256, 256, "PNG", 45_000, color=(60, 200, 120)),
    ImageInfo("/tmp/logo-nyrqis.svg", "Nyrqis Logo",
              512, 512, "SVG", 12_000, color=(80, 140, 255)),
    ImageInfo("/tmp/texture-wood.jpg", "Wood Texture",
              1024, 1024, "JPEG", 890_000, color=(140, 100, 60)),
    ImageInfo("/tmp/wallpaper-sunset.png", "Sunset Gradient",
              3840, 2160, "PNG", 1_200_000, color=(80, 40, 30)),
    ImageInfo("/tmp/diagram-arch.png", "Architecture Diagram",
              1200, 800, "PNG", 560_000, color=(240, 240, 240)),
]


# ---------------------------------------------------------------------------
# ImageViewer
# ---------------------------------------------------------------------------

class ImageViewer:
    """Image viewing UI for Nyrqis.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 800, height: int = 600):
        self.width = width
        self.height = height

        # Current image
        self._current: Optional[ImageInfo] = None
        self._gallery: List[ImageInfo] = list(GALLERY_IMAGES)
        self._selected_index: int = 0

        # View state
        self._zoom: float = 1.0
        self._zoom_mode: ZoomMode = ZoomMode.FIT_WINDOW
        self._rotation: RotateAngle = RotateAngle.NONE
        self._flipped_h: bool = False
        self._flipped_v: bool = False

        # Slideshow
        self._slideshow: bool = False
        self._slideshow_interval: float = 5.0  # seconds
        self._slideshow_timer: float = 0.0

        # UI state
        self._sidebar_visible: bool = True
        self._info_visible: bool = False
        self._visible: bool = False

    @property
    def current(self) -> Optional[ImageInfo]:
        return self._current

    @property
    def gallery(self) -> List[ImageInfo]:
        return list(self._gallery)

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def rotation(self) -> RotateAngle:
        return self._rotation

    # -- Image loading ---------------------------------------------------

    def open_image(self, path: str) -> bool:
        """Open an image by path."""
        for img in self._gallery:
            if img.path == path:
                self._current = img
                self._selected_index = self._gallery.index(img)
                self._reset_view()
                return True

        # Create new entry
        name = os.path.basename(path) if path else "Untitled"
        img = ImageInfo(path=path, name=name)
        self._gallery.append(img)
        self._current = img
        self._selected_index = len(self._gallery) - 1
        self._reset_view()
        return True

    def _reset_view(self) -> None:
        self._zoom = 1.0
        self._zoom_mode = ZoomMode.FIT_WINDOW
        self._rotation = RotateAngle.NONE
        self._flipped_h = False
        self._flipped_v = False

    # -- Navigation ------------------------------------------------------

    def next_image(self) -> Optional[ImageInfo]:
        if not self._gallery:
            return None
        self._selected_index = (self._selected_index + 1) % len(self._gallery)
        self._current = self._gallery[self._selected_index]
        self._reset_view()
        return self._current

    def prev_image(self) -> Optional[ImageInfo]:
        if not self._gallery:
            return None
        self._selected_index = (self._selected_index - 1) % len(self._gallery)
        self._current = self._gallery[self._selected_index]
        self._reset_view()
        return self._current

    def select(self, index: int) -> Optional[ImageInfo]:
        if 0 <= index < len(self._gallery):
            self._selected_index = index
            self._current = self._gallery[index]
            self._reset_view()
            return self._current
        return None

    # -- Zoom ------------------------------------------------------------

    def zoom_in(self, factor: float = 1.25) -> None:
        self._zoom = min(10.0, self._zoom * factor)
        self._zoom_mode = ZoomMode.CUSTOM

    def zoom_out(self, factor: float = 1.25) -> None:
        self._zoom = max(0.1, self._zoom / factor)
        self._zoom_mode = ZoomMode.CUSTOM

    def zoom_fit(self) -> None:
        self._zoom = 1.0
        self._zoom_mode = ZoomMode.FIT_WINDOW

    def zoom_actual(self) -> None:
        self._zoom = 1.0
        self._zoom_mode = ZoomMode.ACTUAL_SIZE

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, min(10.0, zoom))
        self._zoom_mode = ZoomMode.CUSTOM

    # -- Rotate / Flip ---------------------------------------------------

    def rotate_cw(self) -> RotateAngle:
        angles = list(RotateAngle)
        idx = angles.index(self._rotation)
        self._rotation = angles[(idx + 1) % len(angles)]
        return self._rotation

    def rotate_ccw(self) -> RotateAngle:
        angles = list(RotateAngle)
        idx = angles.index(self._rotation)
        self._rotation = angles[(idx - 1) % len(angles)]
        return self._rotation

    def flip_horizontal(self) -> bool:
        self._flipped_h = not self._flipped_h
        return self._flipped_h

    def flip_vertical(self) -> bool:
        self._flipped_v = not self._flipped_v
        return self._flipped_v

    # -- Slideshow -------------------------------------------------------

    def toggle_slideshow(self) -> bool:
        self._slideshow = not self._slideshow
        self._slideshow_timer = 0.0
        return self._slideshow

    def tick(self, elapsed: float = 0.1) -> bool:
        """Advance slideshow timer. Returns True if image changed."""
        if not self._slideshow:
            return False
        self._slideshow_timer += elapsed
        if self._slideshow_timer >= self._slideshow_interval:
            self._slideshow_timer = 0.0
            self.next_image()
            return True
        return False

    # -- Gallery management ----------------------------------------------

    def remove_from_gallery(self, index: int) -> bool:
        if 0 <= index < len(self._gallery):
            self._gallery.pop(index)
            if self._selected_index >= len(self._gallery):
                self._selected_index = max(0, len(self._gallery) - 1)
            if self._gallery:
                self._current = self._gallery[self._selected_index]
            else:
                self._current = None
            return True
        return False

    def clear_gallery(self) -> int:
        count = len(self._gallery)
        self._gallery.clear()
        self._current = None
        self._selected_index = 0
        return count

    # -- Keyboard --------------------------------------------------------

    def handle_key(self, key: str) -> str:
        if key == "Right":
            self.next_image()
            return "next"
        elif key == "Left":
            self.prev_image()
            return "prev"
        elif key == "+":
            self.zoom_in()
            return "zoom"
        elif key == "-":
            self.zoom_out()
            return "zoom"
        elif key == "0":
            self.zoom_fit()
            return "zoom"
        elif key == "r":
            self.rotate_cw()
            return "rotate"
        elif key == "f":
            self.flip_horizontal()
            return "flip"
        elif key == "i":
            self._info_visible = not self._info_visible
            return "info"
        elif key == "s":
            self.toggle_slideshow()
            return "slideshow"
        elif key == "Escape":
            return "close"
        return ""

    # -- Toggle ----------------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "gallery_size": len(self._gallery),
            "current": self._current.name if self._current else None,
            "zoom": f"{self._zoom:.1%}",
            "rotation": self._rotation.name,
            "slideshow": self._slideshow,
        }

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the image viewer UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)

        # Checkerboard background (transparency indicator)
        for y in range(h):
            for x in range(w):
                check = ((x // 16) + (y // 16)) % 2
                c = (40, 40, 44) if check else (32, 32, 36)
                idx = (y * w + x) * 3
                buf[idx] = c[0]
                buf[idx + 1] = c[1]
                buf[idx + 2] = c[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (30, 30, 40))

        # Sidebar thumbnails
        if self._sidebar_visible:
            sidebar_w = 80
            self._fill_rect(buf, w, 0, 48, sidebar_w, h - 48, (30, 30, 40))
            for i, img in enumerate(self._gallery[:8]):
                ty = 56 + i * 72
                is_selected = (i == self._selected_index)
                border = (80, 140, 255) if is_selected else (60, 60, 80)
                self._fill_rect(buf, w, 4, ty, sidebar_w - 8, 64, img.color)
                # Border
                if is_selected:
                    for dx in range(sidebar_w - 8):
                        buf[((ty) * w + 4 + dx) * 3] = border[0]
                        buf[((ty) * w + 4 + dx) * 3 + 1] = border[1]
                        buf[((ty) * w + 4 + dx) * 3 + 2] = border[2]

        # Main image area
        if self._current:
            # Center a colored rectangle representing the image
            img_w = min(w - 120, self._current.width // 4)
            img_h = min(h - 120, self._current.height // 4)
            img_x = (w - img_w) // 2
            img_y = (h - img_h) // 2
            self._fill_rect(buf, w, img_x, img_y, img_w, img_h,
                           self._current.color)

        # Info overlay
        if self._info_visible and self._current:
            info_w = 220
            info_h = 140
            info_x = w - info_w - 12
            info_y = 56
            self._fill_rect(buf, w, info_x, info_y, info_w, info_h,
                           (30, 30, 40))
            # Info placeholders
            self._fill_rect(buf, w, info_x + 8, info_y + 8, 140, 12, (200, 200, 210))
            self._fill_rect(buf, w, info_x + 8, info_y + 26, 100, 10, (150, 150, 170))
            self._fill_rect(buf, w, info_x + 8, info_y + 44, 80, 10, (150, 150, 170))
            self._fill_rect(buf, w, info_x + 8, info_y + 62, 60, 10, (150, 150, 170))
            self._fill_rect(buf, w, info_x + 8, info_y + 80, 120, 10, (150, 150, 170))

        # Zoom indicator
        zoom_text_w = 40
        self._fill_rect(buf, w, 12, h - 28, zoom_text_w, 16, (42, 42, 56))

        return bytes(buf), w, h

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current": self._current.name if self._current else None,
            "gallery": len(self._gallery),
            "zoom": self._zoom,
            "rotation": self._rotation.name,
            "slideshow": self._slideshow,
        }


__all__ = [
    "ImageViewer", "ImageInfo", "ZoomMode", "RotateAngle",
    "ImageView", "GALLERY_IMAGES",
]
