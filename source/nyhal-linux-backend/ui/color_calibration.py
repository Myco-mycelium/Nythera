"""
Nyrqis OS - Display Color Calibration
ICC profiles, gamma correction, and per-monitor color settings.

Features:
- Per-monitor color calibration profiles
- Gamma, brightness, contrast, saturation controls
- ICC profile management and import/export
- Color temperature presets (warm, neutral, cool, custom)
- Night light scheduling with automatic color shift
- Per-channel RGB gain adjustment
- Calibration wizard with test patterns
- HDR/Dolby Vision settings
"""

import time
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class MonitorType(Enum):
    IPS = "IPS"
    VA = "VA"
    TN = "TN"
    OLED = "OLED"
    MiniLED = "Mini-LED"
    QD_OLED = "QD-OLED"


class ColorSpace(Enum):
    SRGB = "sRGB"
    ADOBE_RGB = "Adobe RGB"
    DCI_P3 = "DCI-P3"
    REC2020 = "Rec. 2020"
    REC709 = "Rec. 709"


class CalibrationPreset(Enum):
    STANDARD = "Standard"
    sRGB = "sRGB"
    ADOBE_RGB = "Adobe RGB"
    DCI_P3 = "DCI-P3"
    MOVIE = "Movie"
    GAMING = "Gaming"
    READING = "Reading"
    NIGHT_LIGHT = "Night Light"
    CUSTOM = "Custom"


class HDRMode(Enum):
    OFF = "off"
    AUTO = "auto"
    ON = "on"
    HLG = "HLG"
    HDR10 = "HDR10"
    DOLBY_VISION = "Dolby Vision"


class DitherMode(Enum):
    OFF = "off"
    TEMPORAL = "temporal"
    SPATIAL = "spatial"
    TEMPORAL_SPATIAL = "temporal-spatial"


class GammaType(Enum):
    BT1886 = "BT.1886"
    sRGB = "sRGB"
    POWER_22 = "2.2"
    POWER_24 = "2.4"
    LINEAR = "1.0 (Linear)"
    LUMINANCE = "Luminance-based"


PRESET_ICONS = {
    CalibrationPreset.STANDARD: "🖥️",
    CalibrationPreset.sRGB: "🎨",
    CalibrationPreset.ADOBE_RGB: "📐",
    CalibrationPreset.DCI_P3: "🎬",
    CalibrationPreset.MOVIE: "🍿",
    CalibrationPreset.GAMING: "🎮",
    CalibrationPreset.READING: "📖",
    CalibrationPreset.NIGHT_LIGHT: "🌙",
    CalibrationPreset.CUSTOM: "🔧",
}


@dataclass
class RGBColor:
    r: float = 1.0
    g: float = 1.0
    b: float = 1.0

    @property
    def hex(self) -> str:
        r = max(0, min(255, int(self.r * 255)))
        g = max(0, min(255, int(self.g * 255)))
        b = max(0, min(255, int(self.b * 255)))
        return f"#{r:02x}{g:02x}{b:02x}"

    def clamp(self):
        self.r = max(0.0, min(2.0, self.r))
        self.g = max(0.0, min(2.0, self.g))
        self.b = max(0.0, min(2.0, self.b))


@dataclass
class ICCProfile:
    name: str = ""
    description: str = ""
    file_path: str = ""
    color_space: ColorSpace = ColorSpace.SRGB
    created: float = 0.0
    is_builtin: bool = False
    white_point: str = "D65"
    luminance_cd: float = 120.0
    trc: str = "sRGB"

    @property
    def size_str(self) -> str:
        return "Built-in" if self.is_builtin else "2.4 KB"

    @property
    def icon(self) -> str:
        return "🏗️" if self.is_builtin else "📄"


@dataclass
class NightLightSchedule:
    enabled: bool = False
    start_hour: int = 20
    start_minute: int = 0
    end_hour: int = 7
    end_minute: int = 0
    temperature_k: int = 2700
    ramp_duration_min: int = 30
    schedule_by_location: bool = True

    @property
    def start_str(self) -> str:
        return f"{self.start_hour:02d}:{self.start_minute:02d}"

    @property
    def end_str(self) -> str:
        return f"{self.end_hour:02d}:{self.end_minute:02d}"

    @property
    def temp_bar(self) -> str:
        # Range 1000K - 6500K
        pct = min(100, max(0, ((self.temperature_k - 1000) / 5500) * 100))
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_label(self) -> str:
        k = self.temperature_k
        if k <= 2000:
            return "Very Warm"
        elif k <= 3000:
            return "Warm"
        elif k <= 4500:
            return "Neutral"
        elif k <= 5500:
            return "Cool"
        return "Very Cool"


@dataclass
class MonitorCalibration:
    name: str = ""
    model: str = ""
    serial: str = ""
    monitor_type: MonitorType = MonitorType.IPS
    resolution: str = "2560x1440"
    refresh_rate: int = 144
    size_inches: float = 27.0
    hdr_capable: bool = True
    color_bits: int = 10

    # Current calibration settings
    preset: CalibrationPreset = CalibrationPreset.STANDARD
    profile: str = ""
    brightness: int = 80  # 0-100
    contrast: int = 75  # 0-100
    saturation: int = 50  # 0-100 (50 = neutral)
    gamma: GammaType = GammaType.BT1886
    gamma_value: float = 2.2
    color_temperature: int = 6500  # Kelvin
    sharpness: int = 50  # 0-100
    hue: int = 0  # -180 to 180

    # RGB gain
    red_gain: float = 1.0
    green_gain: float = 1.0
    blue_gain: float = 1.0

    # Color channel offsets
    red_offset: float = 0.0
    green_offset: float = 0.0
    blue_offset: float = 0.0

    # Advanced settings
    hdr_mode: HDRMode = HDRMode.AUTO
    dither: DitherMode = DitherMode.TEMPORAL
    color_space: ColorSpace = ColorSpace.SRGB
    black_level: float = 0.0  # 0.0 - 0.5
    dynamic_contrast: bool = False
    local_dimming: bool = True
    motion_blur_reduction: bool = False
    flicker_free: bool = True

    # LUT calibration points (1D, 256 entries simplified to 16)
    lut_gamma: List[float] = field(default_factory=lambda: [
        0.0, 0.067, 0.118, 0.164, 0.207, 0.247, 0.286, 0.324,
        0.360, 0.396, 0.430, 0.464, 0.498, 0.530, 0.563, 1.0
    ])

    # Metadata
    edid_id: str = ""
    connection: str = "DisplayPort 1.4"
    is_primary: bool = False

    @property
    def brightness_bar(self) -> str:
        filled = int(self.brightness / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def contrast_bar(self) -> str:
        filled = int(self.contrast / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def saturation_bar(self) -> str:
        filled = int(self.saturation / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def sharpness_bar(self) -> str:
        filled = int(self.sharpness / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def color_temp_bar(self) -> str:
        pct = min(100, max(0, ((self.color_temperature - 2700) / (6500 - 2700)) * 100))
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_label(self) -> str:
        k = self.color_temperature
        if k <= 3000:
            return "Warm"
        elif k <= 4500:
            return "Neutral"
        elif k <= 5500:
            return "Daylight"
        elif k <= 6500:
            return "D65 (Standard)"
        return "Cool (D75+)"

    @property
    def hdr_badge(self) -> str:
        if not self.hdr_capable:
            return "SDR"
        icons = {
            HDRMode.OFF: "SDR",
            HDRMode.AUTO: "HDR Auto",
            HDRMode.ON: "HDR On",
            HDRMode.HDR10: "HDR10",
            HDRMode.DOLBY_VISION: "DV",
        }
        return icons.get(self.hdr_mode, "HDR")

    @property
    def resolution_display(self) -> str:
        rr = f" @ {self.refresh_rate}Hz"
        hdr = f" {self.hdr_badge}" if self.hdr_capable else ""
        return f"{self.resolution}{rr}{hdr}"

    @property
    def rgb_gain_str(self) -> str:
        return f"R:{self.red_gain:.2f} G:{self.green_gain:.2f} B:{self.blue_gain:.2f}"

    @property
    def display_name(self) -> str:
        primary = " ⭐" if self.is_primary else ""
        return f"{self.name}{primary}"

    @property
    def color_bar(self) -> str:
        r_len = int(max(0, min(1, self.red_gain)) * 6)
        g_len = int(max(0, min(1, self.green_gain)) * 6)
        b_len = int(max(0, min(1, self.blue_gain)) * 6)
        return "🟥" * r_len + "🟩" * g_len + "🟦" * b_len


@dataclass
class CalibrationStep:
    name: str = ""
    description: str = ""
    completed: bool = False
    current: bool = False

    @property
    def icon(self) -> str:
        if self.completed:
            return "✅"
        elif self.current:
            return "▶️"
        return "○"


class ColorCalibrationManager:
    def __init__(self):
        self.monitors: List[MonitorCalibration] = []
        self.profiles: List[ICCProfile] = []
        self.night_light: NightLightSchedule = NightLightSchedule()
        self.calibration_steps: List[CalibrationStep] = []
        self._selected_monitor: int = 0
        self._view_mode: str = "monitors"
        self._create_sample_data()

    def _create_sample_data(self):
        self.monitors = [
            MonitorCalibration(
                name="Primary Display", model="ASUS ProArt PA278QV",
                serial="PQ3X01234", monitor_type=MonitorType.IPS,
                resolution="2560x1440", refresh_rate=170,
                size_inches=27.0, hdr_capable=True, color_bits=10,
                preset=CalibrationPreset.sRGB,
                profile="ProArt sRGB Calibrated",
                brightness=75, contrast=80, saturation=50,
                gamma=GammaType.sRGB, gamma_value=2.2,
                color_temperature=6500, sharpness=50,
                red_gain=1.02, green_gain=0.98, blue_gain=1.01,
                hdr_mode=HDRMode.AUTO,
                dither=DitherMode.TEMPORAL,
                color_space=ColorSpace.SRGB,
                local_dimming=False, is_primary=True,
                edid_id="ACI27A2", connection="DisplayPort 2.1",
            ),
            MonitorCalibration(
                name="OLED TV", model="LG C2 42\"",
                serial="LG42C2ABC", monitor_type=MonitorType.OLED,
                resolution="3840x2160", refresh_rate=120,
                size_inches=42.0, hdr_capable=True, color_bits=10,
                preset=CalibrationPreset.MOVIE,
                profile="DCI-P3 Movie Calibrated",
                brightness=60, contrast=85, saturation=55,
                gamma=GammaType.BT1886, gamma_value=2.4,
                color_temperature=6500, sharpness=40,
                red_gain=1.00, green_gain=1.00, blue_gain=1.00,
                hdr_mode=HDRMode.DOLBY_VISION,
                dither=DitherMode.OFF,
                color_space=ColorSpace.DCI_P3,
                black_level=0.005, local_dimming=False,
                motion_blur_reduction=False,
                connection="HDMI 2.1",
            ),
            MonitorCalibration(
                name="Secondary Display", model="Dell U2723QE",
                serial="DELL27QE001", monitor_type=MonitorType.IPS,
                resolution="3840x2160", refresh_rate=60,
                size_inches=27.0, hdr_capable=True, color_bits=10,
                preset=CalibrationPreset.ADOBE_RGB,
                profile="Adobe RGB Calibrated",
                brightness=70, contrast=75, saturation=50,
                gamma=GammaType.BT1886, gamma_value=2.2,
                color_temperature=5500, sharpness=50,
                red_gain=1.05, green_gain=0.97, blue_gain=0.98,
                hdr_mode=HDRMode.ON,
                color_space=ColorSpace.ADOBE_RGB,
                local_dimming=False,
                connection="USB-C (DP Alt)",
            ),
        ]

        self.profiles = [
            ICCProfile("ProArt sRGB Calibrated", "Factory-calibrated sRGB",
                       "/usr/share/color/icc/proart-srgb.icc",
                       ColorSpace.SRGB, time.time() - 86400 * 30, True),
            ICCProfile("DCI-P3 Movie Calibrated", "Calibrated for DCI-P3 movie content",
                       "/usr/share/color/icc/lg-c2-dci-p3.icc",
                       ColorSpace.DCI_P3, time.time() - 86400 * 15, True),
            ICCProfile("Adobe RGB Calibrated", "Photography and print work",
                       "/usr/share/color/icc/dell-u2723qe-argb.icc",
                       ColorSpace.ADOBE_RGB, time.time() - 86400 * 10, True),
            ICCProfile("Custom sRGB Warm", "User-calibrated warm sRGB",
                       "/home/user/.local/share/icc/custom-warm.icc",
                       ColorSpace.SRGB, time.time() - 86400 * 5, False),
            ICCProfile("Gaming Enhanced", "Vibrant colors for gaming",
                       "/home/user/.local/share/icc/gaming.icc",
                       ColorSpace.SRGB, time.time() - 86400 * 2, False),
            ICCProfile("Night Light 2700K", "Warm preset for nighttime",
                       "/home/user/.local/share/icc/night-2700k.icc",
                       ColorSpace.SRGB, time.time() - 86400, False),
        ]

        self.night_light = NightLightSchedule(
            enabled=True, start_hour=20, start_minute=30,
            end_hour=7, end_minute=0, temperature_k=2700,
            ramp_duration_min=30, schedule_by_location=True,
        )

        self.calibration_steps = [
            CalibrationStep("Brightness", "Set comfortable brightness level", True),
            CalibrationStep("Contrast", "Adjust contrast ratio", True),
            CalibrationStep("White Point", "Set color temperature", True),
            CalibrationStep("Gamma", "Calibrate gamma curve", True),
            CalibrationStep("RGB Balance", "Fine-tune RGB channels", False, True),
            CalibrationStep("Saturation", "Adjust color saturation", False),
            CalibrationStep("Verify", "Verify calibration with test patterns", False),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_monitor(self) -> Optional[MonitorCalibration]:
        if 0 <= self._selected_monitor < len(self.monitors):
            return self.monitors[self._selected_monitor]
        return None

    def select_monitor(self, idx: int):
        if 0 <= idx < len(self.monitors):
            self._selected_monitor = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_monitor = min(self._selected_monitor + 1, len(self.monitors) - 1)

    def select_up(self):
        self._selected_monitor = max(self._selected_monitor - 1, 0)

    # ─── Calibration Controls ──────────────────────────────────────────

    def set_brightness(self, idx: int, value: int) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].brightness = max(0, min(100, value))
            return True
        return False

    def set_contrast(self, idx: int, value: int) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].contrast = max(0, min(100, value))
            return True
        return False

    def set_saturation(self, idx: int, value: int) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].saturation = max(0, min(100, value))
            return True
        return False

    def set_gamma(self, idx: int, gamma: GammaType, value: float = 2.2) -> bool:
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            m.gamma = gamma
            m.gamma_value = value
            return True
        return False

    def set_color_temperature(self, idx: int, kelvin: int) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].color_temperature = max(2700, min(9300, kelvin))
            return True
        return False

    def set_rgb_gain(self, idx: int, r: float, g: float, b: float) -> bool:
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            m.red_gain = max(0.5, min(2.0, r))
            m.green_gain = max(0.5, min(2.0, g))
            m.blue_gain = max(0.5, min(2.0, b))
            return True
        return False

    def set_preset(self, idx: int, preset: CalibrationPreset) -> bool:
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            m.preset = preset
            # Apply preset defaults
            defaults = {
                CalibrationPreset.STANDARD: (80, 75, 50, 6500, GammaType.BT1886),
                CalibrationPreset.sRGB: (75, 80, 50, 6500, GammaType.sRGB),
                CalibrationPreset.ADOBE_RGB: (70, 75, 50, 5500, GammaType.BT1886),
                CalibrationPreset.DCI_P3: (60, 85, 55, 6500, GammaType.BT1886),
                CalibrationPreset.MOVIE: (50, 80, 55, 6500, GammaType.BT1886),
                CalibrationPreset.GAMING: (85, 80, 65, 6500, GammaType.POWER_22),
                CalibrationPreset.READING: (60, 60, 45, 4500, GammaType.sRGB),
                CalibrationPreset.NIGHT_LIGHT: (40, 50, 40, 2700, GammaType.sRGB),
            }
            if preset in defaults:
                b, c, s, t, g = defaults[preset]
                m.brightness, m.contrast, m.saturation = b, c, s
                m.color_temperature, m.gamma = t, g
            return True
        return False

    def set_hdr_mode(self, idx: int, mode: HDRMode) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].hdr_mode = mode
            return True
        return False

    def set_sharpness(self, idx: int, value: int) -> bool:
        if 0 <= idx < len(self.monitors):
            self.monitors[idx].sharpness = max(0, min(100, value))
            return True
        return False

    # ─── Night Light ───────────────────────────────────────────────────

    def toggle_night_light(self):
        self.night_light.enabled = not self.night_light.enabled

    def set_night_light_temp(self, kelvin: int):
        self.night_light.temperature_k = max(1000, min(6500, kelvin))

    # ─── Profile Management ────────────────────────────────────────────

    def apply_profile(self, monitor_idx: int, profile_idx: int) -> bool:
        if 0 <= monitor_idx < len(self.monitors) and 0 <= profile_idx < len(self.profiles):
            self.monitors[monitor_idx].profile = self.profiles[profile_idx].name
            self.monitors[monitor_idx].color_space = self.profiles[profile_idx].color_space
            return True
        return False

    def import_profile(self, name: str, path: str, color_space: ColorSpace = ColorSpace.SRGB) -> ICCProfile:
        profile = ICCProfile(
            name=name, description=f"Imported: {name}",
            file_path=path, color_space=color_space,
            created=time.time(), is_builtin=False,
        )
        self.profiles.append(profile)
        return profile

    def delete_profile(self, idx: int) -> bool:
        if 0 <= idx < len(self.profiles) and not self.profiles[idx].is_builtin:
            self.profiles.pop(idx)
            return True
        return False

    # ─── Calibration Wizard ────────────────────────────────────────────

    def advance_calibration(self) -> bool:
        for i, step in enumerate(self.calibration_steps):
            if step.current:
                step.current = False
                step.completed = True
                if i + 1 < len(self.calibration_steps):
                    self.calibration_steps[i + 1].current = True
                return True
        return False

    def reset_calibration(self):
        for i, step in enumerate(self.calibration_steps):
            step.completed = False
            step.current = (i == 0)

    @property
    def calibration_progress(self) -> float:
        completed = sum(1 for s in self.calibration_steps if s.completed)
        return (completed / len(self.calibration_steps)) * 100 if self.calibration_steps else 0

    @property
    def calibration_progress_bar(self) -> str:
        pct = self.calibration_progress
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    # ─── LUT ───────────────────────────────────────────────────────────

    def reset_lut(self, monitor_idx: int) -> bool:
        if 0 <= monitor_idx < len(self.monitors):
            self.monitors[monitor_idx].lut_gamma = [
                0.0, 0.067, 0.118, 0.164, 0.207, 0.247, 0.286, 0.324,
                0.360, 0.396, 0.430, 0.464, 0.498, 0.530, 0.563, 1.0
            ]
            return True
        return False

    # ─── Stats ─────────────────────────────────────────────────────────

    def get_hdr_monitors(self) -> List[MonitorCalibration]:
        return [m for m in self.monitors if m.hdr_capable]

    def get_stats(self) -> Dict:
        return {
            "monitors": len(self.monitors),
            "hdr_monitors": len(self.get_hdr_monitors()),
            "profiles": len(self.profiles),
            "builtin_profiles": sum(1 for p in self.profiles if p.is_builtin),
            "night_light": self.night_light.enabled,
            "calibration_progress": round(self.calibration_progress, 0),
        }

    def search_profiles(self, query: str) -> List[ICCProfile]:
        q = query.lower()
        return [p for p in self.profiles if q in p.name.lower() or q in p.description.lower()]

    def search_monitors(self, query: str) -> List[MonitorCalibration]:
        q = query.lower()
        return [m for m in self.monitors if q in m.name.lower() or q in m.model.lower()]
