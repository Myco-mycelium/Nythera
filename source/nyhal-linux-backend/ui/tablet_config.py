"""Drawing Tablet Configuration — Pressure curves, button mapping, and display mapping.

Features:
- Tablet device detection and info display
- Pressure curve editor with Bezier control points
- Button mapping (pen buttons, express keys, touch ring)
- Display area mapping (full, aspect ratio, custom)
- Tilt sensitivity configuration
- Multi-monitor display selection
- Profile management with per-app overrides
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class ButtonAction(Enum):
    NONE = "none"
    LEFT_CLICK = "left_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    ERASER = "eraser"
    PAN = "pan"
    ZOOM = "zoom"
    UNDO = "redo"
    REDO = "redo"
    BRUSH_SIZE_UP = "brush_size_up"
    BRUSH_SIZE_DOWN = "brush_size_down"
    SWITCH_TOOL = "switch_tool"
    KEYBOARD_SHORTCUT = "keyboard_shortcut"
    MODIFIER_CTRL = "modifier_ctrl"
    MODIFIER_ALT = "modifier_alt"

    @property
    def icon(self) -> str:
        icons = {
            ButtonAction.NONE: "❌", ButtonAction.LEFT_CLICK: "🖱",
            ButtonAction.RIGHT_CLICK: "🖱", ButtonAction.ERASER: "🧹",
            ButtonAction.PAN: "✋", ButtonAction.ZOOM: "🔍",
            ButtonAction.UNDO: "↩", ButtonAction.REDO: "↪",
            ButtonAction.BRUSH_SIZE_UP: "⬆", ButtonAction.BRUSH_SIZE_DOWN: "⬇",
            ButtonAction.SWITCH_TOOL: "🔄", ButtonAction.KEYBOARD_SHORTCUT: "⌨",
        }
        return icons.get(self, "?")


class CurveType(Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    CUSTOM = "custom"
    S_CURVE = "s_curve"

    @property
    def icon(self) -> str:
        icons = {
            CurveType.LINEAR: "─", CurveType.EASE_IN: "╯",
            CurveType.EASE_OUT: "╮", CurveType.EASE_IN_OUT: "╮╯",
            CurveType.CUSTOM: "✏️", CurveType.S_CURVE: "∿",
        }
        return icons.get(self, "?")


class DisplayMapping(Enum):
    FULL = "full"
    ASPECT = "aspect_ratio"
    CENTER = "center"
    CUSTOM = "custom"

    @property
    def icon(self) -> str:
        icons = {
            DisplayMapping.FULL: "🖥", DisplayMapping.ASPECT: "📐",
            DisplayMapping.CENTER: "⊕", DisplayMapping.CUSTOM: "✏️",
        }
        return icons.get(self, "?")


@dataclass
class PressurePoint:
    input_pct: float = 0.0
    output_pct: float = 0.0


@dataclass
class PressureCurve:
    curve_type: CurveType = CurveType.LINEAR
    control_points: List[PressurePoint] = field(default_factory=list)
    min_pressure: float = 0.0
    max_pressure: float = 100.0
    sensitivity: float = 50.0
    smoothing: float = 30.0

    @property
    def point_count(self) -> int:
        return len(self.control_points)

    @property
    def curve_visual(self) -> str:
        """ASCII representation of the curve."""
        width = 20
        height = 8
        grid = [[" " for _ in range(width)] for _ in range(height)]
        for pt in self.control_points:
            x = int(pt.input_pct / 100 * (width - 1))
            y = height - 1 - int(pt.output_pct / 100 * (height - 1))
            if 0 <= x < width and 0 <= y < height:
                grid[y][x] = "●"
        # Draw axes
        for i in range(width):
            grid[height - 1][i] = "─"
        for i in range(height):
            grid[i][0] = "│"
        grid[height - 1][0] = "└"
        return "\n".join("".join(row) for row in grid)


@dataclass
class TabletButton:
    id: int = 0
    name: str = ""
    action: ButtonAction = ButtonAction.NONE
    shortcut: str = ""
    custom_label: str = ""
    enabled: bool = True

    @property
    def display(self) -> str:
        if self.action == ButtonAction.KEYBOARD_SHORTCUT:
            return f"⌨ {self.shortcut}"
        if self.action == ButtonAction.NONE:
            return "❌ Unassigned"
        return f"{self.action.icon} {self.action.value}"


@dataclass
class TouchRing:
    mode: str = "scroll"  # scroll, zoom, rotate, brush_size
    sensitivity: float = 50.0
    invert: bool = False

    @property
    def mode_icon(self) -> str:
        icons = {"scroll": "📜", "zoom": "🔍", "rotate": "🔄", "brush_size": "🖌"}
        return icons.get(self.mode, "?")


@dataclass
class TabletDevice:
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    connected: bool = True
    wireless: bool = False
    battery_pct: float = 100.0
    pressure_levels: int = 8192
    tilt_range: int = 60  # degrees
    active_area_mm: Tuple[float, float] = (254.0, 158.8)  # WxH
    max_resolution: Tuple[int, int] = (32767, 32767)  # LPI
    report_rate_hz: int = 200
    pen_buttons: int = 2
    express_keys: int = 8
    touch_ring: bool = True
    display_id: int = 0

    @property
    def battery_bar(self) -> str:
        filled = min(20, int(self.battery_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def battery_icon(self) -> str:
        if self.battery_pct < 10:
            return "🪫"
        return "🔋"

    @property
    def status_icon(self) -> str:
        return "🟢" if self.connected else "🔴"

    @property
    def area_str(self) -> str:
        return f"{self.active_area_mm[0]:.0f}×{self.active_area_mm[1]:.0f}mm"

    @property
    def resolution_str(self) -> str:
        return f"{self.max_resolution[0]}×{self.max_resolution[1]} LPI"


@dataclass
class DisplayConfig:
    id: int = 0
    name: str = ""
    width: int = 1920
    height: int = 1080
    primary: bool = False
    tablet_mapping: DisplayMapping = DisplayMapping.FULL

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def primary_icon(self) -> str:
        return "⭐" if self.primary else "  "


@dataclass
class TabletProfile:
    name: str = ""
    app_override: str = ""  # empty = global
    pressure_curve: PressureCurve = field(default_factory=PressureCurve)
    pen_buttons: List[TabletButton] = field(default_factory=list)
    express_keys: List[TabletButton] = field(default_factory=list)
    active: bool = False

    @property
    def display(self) -> str:
        if self.app_override:
            return f"{self.name} ({self.app_override})"
        return f"{self.name} (global)"


class TabletConfig:
    def __init__(self):
        self._devices: List[TabletDevice] = []
        self._profiles: List[TabletProfile] = []
        self._pen_buttons: List[TabletButton] = []
        self._express_keys: List[TabletButton] = []
        self._touch_ring = TouchRing()
        self._displays: List[DisplayConfig] = []
        self._current_device: int = 0
        self._current_profile: int = 0
        self._selected_button: int = 0
        self._view_mode: str = "device"  # device, pressure, buttons, display, profiles
        self._create_samples()

    def _create_samples(self):
        # Devices
        self._devices = [
            TabletDevice("Wacom Intuos Pro", "Wacom", "PTH-660", "WC-87234", "1.8.2",
                         True, False, 100, 8192, 60, (318.0, 198.0), (32767, 32767), 200, 2, 8, True),
            TabletDevice("Huion Kamvas 16", "Huion", "GT-156", "HU-56123", "2.1.0",
                         True, True, 72, 16384, 60, (344.0, 193.0), (32767, 32767), 233, 2, 0, False),
            TabletDevice("XP-Pen Deco 01", "XP-Pen", "Deco 01", "XP-34567", "1.5.0",
                         False, False, 100, 8192, 60, (254.0, 158.8), (32767, 32767), 200, 2, 8, True),
        ]

        # Pen buttons
        self._pen_buttons = [
            TabletButton(0, "Pen Button 1", ButtonAction.LEFT_CLICK, enabled=True),
            TabletButton(1, "Pen Button 2", ButtonAction.ERASER, enabled=True),
        ]

        # Express keys
        self._express_keys = [
            TabletButton(0, "Key 1", ButtonAction.UNDO, enabled=True),
            TabletButton(1, "Key 2", ButtonAction.REDO, enabled=True),
            TabletButton(2, "Key 3", ButtonAction.ZOOM, enabled=True),
            TabletButton(3, "Key 4", ButtonAction.PAN, enabled=True),
            TabletButton(4, "Key 5", ButtonAction.BRUSH_SIZE_UP, enabled=True),
            TabletButton(5, "Key 6", ButtonAction.BRUSH_SIZE_DOWN, enabled=True),
            TabletButton(6, "Key 7", ButtonAction.SWITCH_TOOL, enabled=True),
            TabletButton(7, "Key 8", ButtonAction.NONE, enabled=False),
        ]

        # Touch ring
        self._touch_ring = TouchRing("brush_size", 65, False)

        # Displays
        self._displays = [
            DisplayConfig(0, "Primary Monitor", 2560, 1440, True, DisplayMapping.FULL),
            DisplayConfig(1, "Secondary Monitor", 1920, 1080, False, DisplayMapping.ASPECT),
        ]

        # Profiles
        pressure_linear = PressureCurve(
            CurveType.LINEAR,
            [PressurePoint(0, 0), PressurePoint(50, 50), PressurePoint(100, 100)],
            0, 100, 50, 30,
        )
        pressure_soft = PressureCurve(
            CurveType.EASE_IN,
            [PressurePoint(0, 0), PressurePoint(30, 50), PressurePoint(70, 80), PressurePoint(100, 100)],
            0, 100, 70, 40,
        )
        pressure_firm = PressureCurve(
            CurveType.EASE_OUT,
            [PressurePoint(0, 0), PressurePoint(50, 20), PressurePoint(80, 70), PressurePoint(100, 100)],
            0, 100, 30, 20,
        )

        self._profiles = [
            TabletProfile("Default (Linear)", "", pressure_linear,
                          self._pen_buttons[:], self._express_keys[:], True),
            TabletProfile("Digital Painting", "Krita", pressure_soft,
                          [TabletButton(0, "Pen 1", ButtonAction.LEFT_CLICK),
                           TabletButton(1, "Pen 2", ButtonAction.BRUSH_SIZE_DOWN)],
                          [TabletButton(0, "K1", ButtonAction.UNDO),
                           TabletButton(1, "K2", ButtonAction.BRUSH_SIZE_UP),
                           TabletButton(2, "K3", ButtonAction.SWITCH_TOOL)]),
            TabletProfile("Photo Editing", "GIMP", pressure_firm,
                          [TabletButton(0, "Pen 1", ButtonAction.LEFT_CLICK),
                           TabletButton(1, "Pen 2", ButtonAction.ZOOM)],
                          [TabletButton(0, "K1", ButtonAction.ZOOM),
                           TabletButton(1, "K2", ButtonAction.PAN)]),
            TabletProfile("3D Sculpting", "Blender", pressure_soft,
                          [TabletButton(0, "Pen 1", ButtonAction.LEFT_CLICK),
                           TabletButton(1, "Pen 2", ButtonAction.ERASER)],
                          [TabletButton(0, "K1", ButtonAction.UNDO),
                           ButtonAction.PAN]),
        ]

    @property
    def current_device(self) -> Optional[TabletDevice]:
        if 0 <= self._current_device < len(self._devices):
            return self._devices[self._current_device]
        return None

    @property
    def current_profile(self) -> Optional[TabletProfile]:
        if 0 <= self._current_profile < len(self._profiles):
            return self._profiles[self._current_profile]
        return None

    def select_device(self, idx: int):
        if 0 <= idx < len(self._devices):
            self._current_device = idx

    def select_profile(self, idx: int):
        if 0 <= idx < len(self._profiles):
            self._current_profile = idx

    def set_view(self, mode: str):
        if mode in ("device", "pressure", "buttons", "display", "profiles"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS TABLET CONFIGURATOR                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        dev = self.current_device
        dev_name = f"{dev.manufacturer} {dev.model}" if dev else "None"
        prof = self.current_profile
        prof_name = prof.name if prof else "None"
        lines.append(f"  🖊 {dev_name}  📋 {prof_name}  🔋 {dev.battery_pct:.0f}%  {'🟢' if dev and dev.connected else '🔴'}")
        lines.append("")

        if self._view_mode == "device":
            lines.append("  ── Device Info ──")
            if dev:
                lines.append(f"  Name: {dev.manufacturer} {dev.name} ({dev.model})")
                lines.append(f"  Serial: {dev.serial}  Firmware: {dev.firmware}")
                lines.append(f"  Pressure Levels: {dev.pressure_levels:,}  Tilt: ±{dev.tilt_range}°")
                lines.append(f"  Active Area: {dev.area_str}  Resolution: {dev.resolution_str}")
                lines.append(f"  Report Rate: {dev.report_rate_hz}Hz  Pen Buttons: {dev.pen_buttons}  Express Keys: {dev.express_keys}")
                if dev.wireless:
                    lines.append(f"  {dev.battery_icon} Battery: [{dev.battery_bar}] {dev.battery_pct:.0f}%")
            lines.append("")
            # All devices
            lines.append("  ── Devices ──")
            for i, d in enumerate(self._devices):
                sel = "▶" if i == self._current_device else " "
                wireless = "📡" if d.wireless else "🔌"
                lines.append(f"  {sel} {d.status_icon} {wireless} {d.manufacturer} {d.name} ({d.model})  {d.pressure_levels:,} levels")

        elif self._view_mode == "pressure":
            lines.append("  ── Pressure Curve ──")
            if prof:
                pc = prof.pressure_curve
                lines.append(f"  Curve Type: {pc.curve_type.icon} {pc.curve_type.value}")
                lines.append(f"  Sensitivity: {pc.sensitivity:.0f}%  Smoothing: {pc.smoothing:.0f}%")
                lines.append(f"  Min: {pc.min_pressure:.0f}%  Max: {pc.max_pressure:.0f}%")
                lines.append(f"  Control Points: {pc.point_count}")
                lines.append("")
                # ASCII curve
                for row in pc.curve_visual.split("\n"):
                    lines.append(f"  {row}")
                lines.append(f"  Input →  Output ↑")
            lines.append("")
            lines.append("  ── Preset Curves ──")
            for ct in CurveType:
                lines.append(f"  {ct.icon} {ct.value}")

        elif self._view_mode == "buttons":
            lines.append("  ── Pen Buttons ──")
            for btn in self._pen_buttons:
                enabled = "🟢" if btn.enabled else "🔴"
                lines.append(f"  {enabled} {btn.name}: {btn.display}")
            lines.append("")
            lines.append("  ── Express Keys ──")
            for btn in self._express_keys:
                enabled = "🟢" if btn.enabled else "🔴"
                lines.append(f"  {enabled} {btn.name}: {btn.display}")
            lines.append("")
            lines.append(f"  ── Touch Ring ──")
            lines.append(f"  Mode: {self._touch_ring.mode_icon} {self._touch_ring.mode}  Sensitivity: {self._touch_ring.sensitivity:.0f}%  Invert: {'✓' if self._touch_ring.invert else '✗'}")

        elif self._view_mode == "display":
            lines.append("  ── Display Mapping ──")
            for d in self._displays:
                primary = d.primary_icon
                lines.append(f"  {primary} {d.name} ({d.resolution})  Mapping: {d.tablet_mapping.icon} {d.tablet_mapping.value}")
            lines.append("")
            lines.append("  ── Mapping Preview ──")
            lines.append("  ┌─────────────────────────────────┐")
            lines.append("  │  Display 2560×1440              │")
            lines.append("  │  ┌───────────────────────┐      │")
            lines.append("  │  │   Tablet Area         │      │")
            lines.append("  │  │   (318×198mm)         │      │")
            lines.append("  │  └───────────────────────┘      │")
            lines.append("  └─────────────────────────────────┘")

        elif self._view_mode == "profiles":
            lines.append("  ── Profiles ──")
            for i, p in enumerate(self._profiles):
                sel = "▶" if i == self._current_profile else " "
                active = "🟢" if p.active else "⚪"
                lines.append(f"  {sel} {active} {p.display}")
                lines.append(f"      Curve: {p.pressure_curve.curve_type.icon} {p.pressure_curve.curve_type.value}  Pen: {len(p.pen_buttons)} buttons  Keys: {len(p.express_keys)} keys")

        lines.append("")
        lines.append("  [D]evice [P]ressure [B]uttons [M]apping [F] Profiles [↑↓]Nav")
        return lines
