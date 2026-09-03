"""DisplaySettings — Display configuration UI for Nyrqis.

Provides display management with:
- Resolution selection (preset and custom)
- Refresh rate selection
- Display scaling (100%-300%)
- Wallpaper selection and preview
- Night light / blue light filter
- Multi-monitor arrangement
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScalingMode(Enum):
    FACTOR = auto()    # 100%, 125%, 150%, 200%
    FRACTIONAL = auto()  # 125%, 175%, etc.


class NightLightMode(Enum):
    OFF = auto()
    SCHEDULED = auto()  # sunset to sunrise
    ALWAYS = auto()


class WallpaperMode(Enum):
    FILL = auto()       # scale to fill, crop edges
    FIT = auto()        # scale to fit, letterbox
    STRETCH = auto()    # stretch to fill
    TILE = auto()       # tile the image
    CENTER = auto()     # center, no scaling


class DisplayOrientation(Enum):
    LANDSCAPE = auto()
    PORTRAIT = auto()
    LANDSCAPE_FLIPPED = auto()
    PORTRAIT_FLIPPED = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DisplayMode:
    """A display resolution + refresh rate combination."""
    width: int
    height: int
    refresh_rate: int  # Hz
    preferred: bool = False
    available: bool = True

    @property
    def label(self) -> str:
        return f"{self.width}×{self.height} @ {self.refresh_rate}Hz"

    @property
    def aspect_ratio(self) -> str:
        from math import gcd
        d = gcd(self.width, self.height)
        return f"{self.width // d}:{self.height // d}"


@dataclass
class Wallpaper:
    """A wallpaper entry."""
    id: str
    name: str
    path: str = ""
    color: Tuple[int, int, int] = (30, 30, 40)
    gradient: bool = False
    gradient_colors: Tuple[Tuple[int, int, int], Tuple[int, int, int]] = (
        (30, 30, 40), (50, 50, 70))
    builtin: bool = True


@dataclass
class NightLightConfig:
    """Night light / blue light filter settings."""
    mode: NightLightMode = NightLightMode.OFF
    temperature: int = 4000  # Kelvin (2700=very warm, 6500=daylight)
    strength: float = 0.5    # 0.0-1.0
    schedule_start: str = "20:00"
    schedule_end: str = "07:00"


@dataclass
class DisplayConfig:
    """Complete display configuration."""
    resolution: Tuple[int, int] = (1920, 1080)
    refresh_rate: int = 60
    scaling: float = 1.0
    orientation: DisplayOrientation = DisplayOrientation.LANDSCAPE
    wallpaper_id: str = "eclipse-dark"
    night_light: NightLightConfig = field(default_factory=NightLightConfig)
    vsync: bool = True
    hdr: bool = False
    color_depth: int = 24  # bits per pixel


# ---------------------------------------------------------------------------
# DisplaySettings
# ---------------------------------------------------------------------------

class DisplaySettings:
    """Display configuration UI for Nyrqis.

    Provides resolution, refresh rate, scaling, wallpaper, night light,
    and multi-monitor management.

    Parameters
    ----------
    width, height : int
        Screen dimensions for rendering.
    """

    # Common resolution presets
    PRESET_MODES = [
        DisplayMode(1920, 1080, 60, preferred=True),
        DisplayMode(1920, 1080, 144),
        DisplayMode(1920, 1080, 240),
        DisplayMode(2560, 1440, 60, preferred=True),
        DisplayMode(2560, 1440, 144),
        DisplayMode(2560, 1440, 240),
        DisplayMode(3840, 2160, 60, preferred=True),
        DisplayMode(3840, 2160, 120),
        DisplayMode(1280, 720, 60),
        DisplayMode(1280, 720, 144),
        DisplayMode(1600, 900, 60),
        DisplayMode(2560, 1080, 60),
        DisplayMode(2560, 1080, 144),
        DisplayMode(3440, 1440, 60),
        DisplayMode(3440, 1440, 144),
        DisplayMode(5120, 2880, 60),
    ]

    # Built-in wallpapers
    BUILTIN_WALLPAPERS = [
        Wallpaper("eclipse-dark", "Eclipse Dark", color=(20, 22, 36),
                  gradient=True, gradient_colors=((20, 22, 36), (40, 44, 68))),
        Wallpaper("solar-light", "Solar Light", color=(253, 246, 227),
                  gradient=True, gradient_colors=((253, 246, 227), (200, 195, 170))),
        Wallpaper("nord-frost", "Nord Frost", color=(46, 52, 64),
                  gradient=True, gradient_colors=((46, 52, 64), (76, 86, 106))),
        Wallpaper("dracula", "Dracula", color=(40, 42, 54),
                  gradient=True, gradient_colors=((40, 42, 54), (68, 71, 90))),
        Wallpaper("mycelium-green", "Mycelium Green", color=(15, 25, 15),
                  gradient=True, gradient_colors=((15, 25, 15), (30, 60, 30))),
        Wallpaper("deep-ocean", "Deep Ocean", color=(10, 15, 30),
                  gradient=True, gradient_colors=((10, 15, 30), (20, 40, 80))),
        Wallpaper("sunset", "Sunset", color=(40, 20, 50),
                  gradient=True, gradient_colors=((40, 20, 50), (80, 40, 30))),
        Wallpaper("pure-black", "Pure Black", color=(0, 0, 0)),
        Wallpaper("pure-white", "Pure White", color=(255, 255, 255)),
    ]

    SCALING_PRESETS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

    def __init__(self, width: int = 480, height: int = 600):
        self.width = width
        self.height = height

        # Current config
        self._config = DisplayConfig()

        # Available modes (simulated)
        self._available_modes = list(self.PRESET_MODES)

        # Wallpapers
        self._wallpapers = list(self.BUILTIN_WALLPAPERS)
        self._custom_wallpapers: List[Wallpaper] = []

        # Multi-monitor
        self._monitors: List[Dict[str, Any]] = [
            {"id": "eDP-1", "name": "Built-in Display",
             "resolution": (1920, 1080), "primary": True,
             "position": (0, 0), "scale": 1.0, "connected": True},
        ]

        # Night light state
        self._night_light_active = False

        # UI state
        self._selected_tab = "display"  # display, wallpaper, night_light
        self._selected_mode_index = 0

    # -- Config access --------------------------------------------------

    @property
    def config(self) -> DisplayConfig:
        return self._config

    @property
    def available_modes(self) -> List[DisplayMode]:
        return [m for m in self._available_modes if m.available]

    @property
    def wallpapers(self) -> List[Wallpaper]:
        return self._wallpapers + self._custom_wallpapers

    @property
    def monitors(self) -> List[Dict[str, Any]]:
        return list(self._monitors)

    # -- Resolution / refresh rate --------------------------------------

    def set_resolution(self, width: int, height: int) -> bool:
        """Set display resolution."""
        for mode in self._available_modes:
            if mode.width == width and mode.height == height:
                self._config.resolution = (width, height)
                self._config.refresh_rate = mode.refresh_rate
                return True
        # Custom resolution
        self._config.resolution = (width, height)
        return True

    def set_refresh_rate(self, rate: int) -> None:
        """Set refresh rate."""
        self._config.refresh_rate = rate

    def get_compatible_rates(self, width: int, height: int) -> List[int]:
        """Get compatible refresh rates for a resolution."""
        rates = set()
        for mode in self._available_modes:
            if mode.width == width and mode.height == height:
                rates.add(mode.refresh_rate)
        return sorted(rates) or [60]

    # -- Scaling --------------------------------------------------------

    def set_scaling(self, scale: float) -> None:
        """Set display scaling factor."""
        self._config.scaling = max(0.5, min(3.0, scale))

    def get_effective_resolution(self) -> Tuple[int, int]:
        """Get effective resolution after scaling."""
        w, h = self._config.resolution
        return (int(w / self._config.scaling), int(h / self._config.scaling))

    # -- Orientation ----------------------------------------------------

    def set_orientation(self, orientation: DisplayOrientation) -> None:
        self._config.orientation = orientation

    # -- Wallpaper ------------------------------------------------------

    def set_wallpaper(self, wallpaper_id: str) -> bool:
        """Set the active wallpaper."""
        for wp in self.wallpapers:
            if wp.id == wallpaper_id:
                self._config.wallpaper_id = wallpaper_id
                return True
        return False

    def add_custom_wallpaper(self, name: str, path: str) -> Wallpaper:
        """Add a custom wallpaper from file path."""
        wp_id = f"custom-{len(self._custom_wallpapers)}"
        wp = Wallpaper(wp_id, name, path=path, builtin=False)
        self._custom_wallpapers.append(wp)
        return wp

    def remove_custom_wallpaper(self, wallpaper_id: str) -> bool:
        before = len(self._custom_wallpapers)
        self._custom_wallpapers = [
            w for w in self._custom_wallpapers if w.id != wallpaper_id]
        return len(self._custom_wallpapers) < before

    def get_wallpaper(self, wallpaper_id: str) -> Optional[Wallpaper]:
        for wp in self.wallpapers:
            if wp.id == wallpaper_id:
                return wp
        return None

    # -- Night light ----------------------------------------------------

    def set_night_light(self, mode: NightLightMode,
                        temperature: int = 4000,
                        strength: float = 0.5) -> None:
        """Configure night light."""
        self._config.night_light.mode = mode
        self._config.night_light.temperature = max(2700, min(6500, temperature))
        self._config.night_light.strength = max(0.0, min(1.0, strength))
        self._night_light_active = mode != NightLightMode.OFF

    def get_night_light_color(self) -> Tuple[int, int, int]:
        """Get the current night light tint color."""
        if not self._night_light_active:
            return (255, 255, 255)
        temp = self._config.night_light.temperature
        # Approximate color temperature to RGB
        t = (temp - 2700) / (6500 - 2700)  # 0=warm, 1=cool
        r = int(255 * (0.6 + 0.4 * (1 - t)))
        g = int(255 * (0.5 + 0.3 * t + 0.2 * (1 - t) * 0.8))
        b = int(255 * (0.3 + 0.7 * t))
        return (r, g, b)

    # -- Multi-monitor --------------------------------------------------

    def add_monitor(self, name: str, resolution: Tuple[int, int],
                    position: Tuple[int, int] = (0, 0)) -> Dict[str, Any]:
        """Add a monitor."""
        monitor = {
            "id": f"HDMI-{len(self._monitors) + 1}",
            "name": name,
            "resolution": resolution,
            "primary": False,
            "position": position,
            "scale": 1.0,
            "connected": True,
        }
        self._monitors.append(monitor)
        return monitor

    def remove_monitor(self, monitor_id: str) -> bool:
        """Remove a monitor (can't remove primary)."""
        for m in self._monitors:
            if m["id"] == monitor_id and not m["primary"]:
                self._monitors = [
                    x for x in self._monitors if x["id"] != monitor_id]
                return True
        return False

    def set_primary_monitor(self, monitor_id: str) -> bool:
        """Set the primary monitor."""
        for m in self._monitors:
            if m["id"] == monitor_id:
                for x in self._monitors:
                    x["primary"] = False
                m["primary"] = True
                return True
        return False

    def arrange_monitors(self, arrangement: str = "horizontal") -> None:
        """Auto-arrange monitors."""
        if arrangement == "horizontal":
            x_offset = 0
            for m in self._monitors:
                m["position"] = (x_offset, 0)
                x_offset += m["resolution"][0]
        elif arrangement == "vertical":
            y_offset = 0
            for m in self._monitors:
                m["position"] = (0, y_offset)
                y_offset += m["resolution"][1]

    def get_total_resolution(self) -> Tuple[int, int]:
        """Get total combined resolution across all monitors."""
        max_x = max(m["position"][0] + m["resolution"][0]
                    for m in self._monitors)
        max_y = max(m["position"][1] + m["resolution"][1]
                    for m in self._monitors)
        return (max_x, max_y)

    # -- Rendering ------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the display settings UI to an RGB byte buffer."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)

        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # Tab bar
        tabs = ["Display", "Wallpaper", "Night Light"]
        tab_y = 48
        tab_w = w // len(tabs)
        for i, tab in enumerate(tabs):
            color = (80, 140, 255) if i == tabs.index(
                self._selected_tab.capitalize().replace("_", " ")) else (42, 42, 56)
            self._fill_rect(buf, w, i * tab_w, tab_y, tab_w, 40, color)

        # Content area
        content_y = 100
        if self._selected_tab == "display":
            # Resolution preview
            preview_w = min(300, w - 40)
            preview_h = int(preview_w * 9 / 16)
            preview_x = (w - preview_w) // 2
            self._fill_rect(buf, w, preview_x, content_y,
                           preview_w, preview_h, (20, 20, 30))

            # Wallpaper preview in the monitor frame
            wp = self.get_wallpaper(self._config.wallpaper_id)
            if wp and wp.gradient:
                for dy in range(preview_h):
                    t = dy / max(1, preview_h)
                    r = int(wp.gradient_colors[0][0] * (1 - t) +
                           wp.gradient_colors[1][0] * t)
                    g = int(wp.gradient_colors[0][1] * (1 - t) +
                           wp.gradient_colors[1][1] * t)
                    b = int(wp.gradient_colors[0][2] * (1 - t) +
                           wp.gradient_colors[1][2] * t)
                    self._fill_rect(buf, w, preview_x, content_y + dy,
                                   preview_w, 1, (r, g, b))

            # Resolution info
            info_y = content_y + preview_h + 16
            res = self._config.resolution
            self._fill_rect(buf, w, 20, info_y, 120, 14, (200, 200, 210))
            self._fill_rect(buf, w, 20, info_y + 20, 80, 14, (150, 150, 170))

            # Mode list
            y = info_y + 50
            for i, mode in enumerate(self.available_modes[:8]):
                is_selected = (mode.width == res[0] and
                              mode.height == res[1] and
                              mode.refresh_rate == self._config.refresh_rate)
                row_color = (50, 50, 70) if is_selected else (35, 35, 48)
                self._fill_rect(buf, w, 16, y, w - 32, 36, row_color)
                if is_selected:
                    self._fill_rect(buf, w, 16, y, 4, 36, (80, 140, 255))
                y += 40

        elif self._selected_tab == "wallpaper":
            # Wallpaper grid
            cols = 3
            pad = 12
            thumb_w = (w - pad * (cols + 1)) // cols
            thumb_h = int(thumb_w * 9 / 16)
            y = content_y
            for i, wp in enumerate(self.wallpapers[:9]):
                col = i % cols
                row = i // cols
                x = pad + col * (thumb_w + pad)
                ty = y + row * (thumb_h + pad + 20)

                # Thumbnail
                if wp.gradient:
                    for dy in range(thumb_h):
                        t = dy / max(1, thumb_h)
                        r = int(wp.gradient_colors[0][0] * (1 - t) +
                               wp.gradient_colors[1][0] * t)
                        g = int(wp.gradient_colors[0][1] * (1 - t) +
                               wp.gradient_colors[1][1] * t)
                        b = int(wp.gradient_colors[0][2] * (1 - t) +
                               wp.gradient_colors[1][2] * t)
                        self._fill_rect(buf, w, x, ty + dy, thumb_w, 1,
                                       (r, g, b))
                else:
                    self._fill_rect(buf, w, x, ty, thumb_w, thumb_h, wp.color)

                # Selected border
                if wp.id == self._config.wallpaper_id:
                    border_color = (80, 140, 255)
                    for dx in range(thumb_w):
                        buf[((ty) * w + x + dx) * 3] = border_color[0]
                        buf[((ty) * w + x + dx) * 3 + 1] = border_color[1]
                        buf[((ty) * w + x + dx) * 3 + 2] = border_color[2]
                        buf[((ty + thumb_h - 1) * w + x + dx) * 3] = border_color[0]
                        buf[((ty + thumb_h - 1) * w + x + dx) * 3 + 1] = border_color[1]
                        buf[((ty + thumb_h - 1) * w + x + dx) * 3 + 2] = border_color[2]

        elif self._selected_tab == "night_light":
            # Night light preview — warm overlay
            nl = self._config.night_light
            preview_w = w - 40
            preview_h = 200
            self._fill_rect(buf, w, 20, content_y, preview_w, preview_h,
                           (20, 22, 36))

            if nl.mode != NightLightMode.OFF:
                color = self.get_night_light_color()
                overlay = tuple(int(c * nl.strength * 0.3) for c in color)
                self._fill_rect(buf, w, 20, content_y, preview_w, preview_h,
                               overlay)

            # Temperature bar
            bar_y = content_y + preview_h + 20
            bar_w = preview_w
            for x in range(bar_w):
                t = x / max(1, bar_w)
                temp = int(2700 + t * (6500 - 2700))
                r = int(255 * (0.6 + 0.4 * (1 - t)))
                g = int(255 * (0.5 + 0.3 * t + 0.2 * (1 - t) * 0.8))
                b = int(255 * (0.3 + 0.7 * t))
                self._fill_rect(buf, w, 20 + x, bar_y, 1, 16, (r, g, b))

        return bytes(buf), w, h

    # -- Serialization --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolution": list(self._config.resolution),
            "refresh_rate": self._config.refresh_rate,
            "scaling": self._config.scaling,
            "orientation": self._config.orientation.name,
            "wallpaper": self._config.wallpaper_id,
            "night_light": {
                "mode": self._config.night_light.mode.name,
                "temperature": self._config.night_light.temperature,
                "strength": self._config.night_light.strength,
            },
            "monitors": len(self._monitors),
            "vsync": self._config.vsync,
        }

    # -- Helpers --------------------------------------------------------

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


__all__ = [
    "DisplaySettings", "DisplayMode", "Wallpaper", "WallpaperMode",
    "NightLightConfig", "NightLightMode", "DisplayConfig",
    "DisplayOrientation", "ScalingMode",
]
