"""
Nyrqis OS - Power Profile Manager
CPU governor control, battery optimization, and thermal policies.

Features:
- Power profiles (Performance, Balanced, Powersaver, Ultra, Custom)
- CPU governor control (performance, schedutil, powersave, ondemand)
- Battery health and charging management
- Thermal policies with fan curves
- Per-device power limits (CPU, GPU, DRAM, USB)
- Wake/sleep scheduling
- Power consumption monitoring
- Power event history
"""

import time
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class PowerProfile(Enum):
    PERFORMANCE = "performance"
    BALANCED = "balanced"
    POWERSAVER = "powersaver"
    ULTRA_SAVER = "ultra_saver"
    CUSTOM = "custom"


class CPUGovernor(Enum):
    PERFORMANCE = "performance"
    SCHEDUTIL = "schedutil"
    POWERSAVE = "powersave"
    ONDEMAND = "ondemand"
    CONSERVATIVE = "conservative"
    USERSPACE = "userspace"


class ThermalPolicy(Enum):
    PASSIVE = "passive"
    ACTIVE = "active"
    CRITICAL = "critical"
    DISABLED = "disabled"


class ChargingState(Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    FULL = "full"
    NOT_PRESENT = "not_present"
    FAST_CHARGING = "fast_charging"
    TRICKLE = "trickle"


class WakeReason(Enum):
    MANUAL = "manual"
    TIMER = "timer"
    NETWORK = "network"
    USB = "usb"
    BLUETOOTH = "bluetooth"
    RTC = "rtc"
    UNKNOWN = "unknown"


PROFILE_ICONS = {
    PowerProfile.PERFORMANCE: "⚡",
    PowerProfile.BALANCED: "⚖️",
    PowerProfile.POWERSAVER: "🔋",
    PowerProfile.ULTRA_SAVER: "🐢",
    PowerProfile.CUSTOM: "🔧",
}

GOVERNOR_ICONS = {
    CPUGovernor.PERFORMANCE: "🚀",
    CPUGovernor.SCHEDUTIL: "⚖️",
    CPUGovernor.POWERSAVE: "🔋",
    CPUGovernor.ONDEMAND: "📊",
    CPUGovernor.CONSERVATIVE: "🐢",
    CPUGovernor.USERSPACE: "👤",
}

CHARGING_ICONS = {
    ChargingState.CHARGING: "🔌",
    ChargingState.DISCHARGING: "🔋",
    ChargingState.FULL: "✅",
    ChargingState.NOT_PRESENT: "❓",
    ChargingState.FAST_CHARGING: "⚡",
    ChargingState.TRICKLE: "💧",
}


@dataclass
class BatteryInfo:
    charge_percent: float = 0.0
    design_capacity_mah: float = 0.0
    current_capacity_mah: float = 0.0
    voltage_mv: float = 0.0
    charge_cycles: int = 0
    health_percent: float = 100.0
    temperature_c: float = 25.0
    time_to_empty_s: float = 0.0
    time_to_full_s: float = 0.0
    charging_state: ChargingState = ChargingState.CHARGING
    manufacturer: str = ""
    model: str = ""
    chemistry: str = "Li-ion"

    @property
    def charge_bar(self) -> str:
        filled = int(self.charge_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def health_bar(self) -> str:
        filled = int(self.health_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 35:
            return "🟢 Cool"
        elif self.temperature_c < 45:
            return "🟡 Warm"
        return "🔴 Hot"

    @property
    def health_status(self) -> str:
        if self.health_percent > 90:
            return "🟢 Excellent"
        elif self.health_percent > 70:
            return "🟡 Good"
        elif self.health_percent > 50:
            return "🟠 Fair"
        return "🔴 Poor"

    @property
    def time_to_empty_str(self) -> str:
        if self.time_to_empty_s <= 0:
            return "N/A"
        h = int(self.time_to_empty_s // 3600)
        m = int((self.time_to_empty_s % 3600) // 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

    @property
    def time_to_full_str(self) -> str:
        if self.time_to_full_s <= 0:
            return "N/A"
        h = int(self.time_to_full_s // 3600)
        m = int((self.time_to_full_s % 3600) // 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

    @property
    def capacity_mah(self) -> str:
        return f"{self.current_capacity_mah:.0f}/{self.design_capacity_mah:.0f} mAh"

    @property
    def voltage_str(self) -> str:
        return f"{self.voltage_mv / 1000:.2f}V"

    @property
    def charging_icon(self) -> str:
        return CHARGING_ICONS.get(self.charging_state, "❓")

    @property
    def charge_display(self) -> str:
        return f"{self.charging_icon} {self.charge_percent:.0f}%"


@dataclass
class PowerLimit:
    component: str = ""  # CPU, GPU, DRAM, USB
    current_watts: float = 0.0
    min_watts: float = 0.0
    max_watts: float = 0.0
    default_watts: float = 0.0

    @property
    def percent(self) -> float:
        if self.max_watts == 0:
            return 0
        return (self.current_watts / self.max_watts) * 100

    @property
    def bar(self) -> str:
        filled = int(self.percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def watts_str(self) -> str:
        return f"{self.current_watts:.0f}W / {self.max_watts:.0f}W"


@dataclass
class ThermalSensor:
    name: str = ""
    current_c: float = 0.0
    min_c: float = 0.0
    max_c: float = 0.0
    critical_c: float = 100.0
    warning_c: float = 80.0
    fan_speed_rpm: int = 0
    fan_duty_percent: int = 0

    @property
    def temp_bar(self) -> str:
        # Scale 0-100C
        pct = min(100, max(0, self.current_c))
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status(self) -> str:
        if self.current_c >= self.critical_c:
            return "🔴 CRITICAL"
        elif self.current_c >= self.warning_c:
            return "🟡 WARNING"
        return "🟢 Normal"

    @property
    def fan_bar(self) -> str:
        filled = int(self.fan_duty_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def trend(self) -> str:
        diff = self.current_c - self.min_c
        if diff > 5:
            return "📈 Rising"
        elif diff < -5:
            return "📉 Falling"
        return "➡️ Stable"


@dataclass
class FanProfile:
    name: str = ""
    # Temperature -> fan duty mapping (temp_c, duty_percent)
    curve: List[Tuple[int, int]] = field(default_factory=list)
    hysteresis_c: float = 3.0
    min_rpm: int = 300
    max_rpm: int = 2500

    @property
    def curve_str(self) -> str:
        return " → ".join(f"{t}°C={d}%" for t, d in self.curve[:4])

    @property
    def points_display(self) -> str:
        return ", ".join(f"{t}→{d}%" for t, d in self.curve)


@dataclass
class PowerEvent:
    timestamp: float = 0.0
    event_type: str = ""  # sleep, wake, charge, profile, thermal, throttle
    details: str = ""
    success: bool = True

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {
            "sleep": "😴", "wake": "⏰", "charge": "🔌",
            "profile": "⚙️", "thermal": "🌡️", "throttle": "⚡",
            "suspend": "💤", "resume": "🔄",
        }
        return icons.get(self.event_type, "❓")


@dataclass
class WakeSchedule:
    name: str = ""
    enabled: bool = True
    wake_hour: int = 7
    wake_minute: int = 0
    days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon-Fri
    action: str = "resume"  # resume, power_on

    @property
    def time_str(self) -> str:
        return f"{self.wake_hour:02d}:{self.wake_minute:02d}"

    @property
    def days_str(self) -> str:
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return " ".join(day_names[d] for d in self.days if d < 7)


@dataclass
class PowerProfileConfig:
    name: str = ""
    governor: CPUGovernor = CPUGovernor.SCHEDUTIL
    cpu_max_percent: int = 100
    gpu_max_percent: int = 100
    dram_max_mhz: int = 6000
    usb_suspend: bool = False
    pcie_aspm: bool = True
    turbe_boost: bool = True
    turbo_force_on: bool = False
    kernel_nohz: bool = True
    kernel_idle: str = "menu"
    screen_dim_s: int = 300
    screen_off_s: int = 600
    sleep_s: int = 1800

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_max_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def gpu_bar(self) -> str:
        filled = int(self.gpu_max_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def features(self) -> str:
        parts = []
        if self.turbe_boost:
            parts.append("Turbo")
        if self.pcie_aspm:
            parts.append("ASPM")
        if self.kernel_nohz:
            parts.append("NoHz")
        return " ".join(parts) if parts else "None"


class PowerProfileManager:
    def __init__(self):
        self.battery: BatteryInfo = BatteryInfo()
        self.active_profile: PowerProfile = PowerProfile.BALANCED
        self.profiles: List[PowerProfileConfig] = []
        self.power_limits: List[PowerLimit] = []
        self.thermal_sensors: List[ThermalSensor] = []
        self.fan_profiles: List[FanProfile] = []
        self.active_fan_profile: str = ""
        self.thermal_policy: ThermalPolicy = ThermalPolicy.ACTIVE
        self.wake_schedules: List[WakeSchedule] = []
        self.events: List[PowerEvent] = []
        self._selected_profile: int = 1
        self._view_mode: str = "profiles"
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.battery = BatteryInfo(
            charge_percent=73.0,
            design_capacity_mah=8000.0,
            current_capacity_mah=7440.0,
            voltage_mv=11550.0,
            charge_cycles=245,
            health_percent=93.0,
            temperature_c=32.5,
            time_to_empty_s=14400,
            time_to_full_s=3600,
            charging_state=ChargingState.CHARGING,
            manufacturer="Samsung SDI",
            model="Nyrqis Battery Pack",
            chemistry="Li-ion",
        )

        self.profiles = [
            PowerProfileConfig(
                name="Performance", governor=CPUGovernor.PERFORMANCE,
                cpu_max_percent=100, gpu_max_percent=100,
                dram_max_mhz=6000, usb_suspend=False,
                pcie_aspm=False, turbe_boost=True, turbo_force_on=True,
                screen_dim_s=600, screen_off_s=1200, sleep_s=3600,
            ),
            PowerProfileConfig(
                name="Balanced", governor=CPUGovernor.SCHEDUTIL,
                cpu_max_percent=100, gpu_max_percent=100,
                dram_max_mhz=6000, usb_suspend=True,
                pcie_aspm=True, turbe_boost=True,
                screen_dim_s=300, screen_off_s=600, sleep_s=1800,
            ),
            PowerProfileConfig(
                name="Powersaver", governor=CPUGovernor.POWERSAVE,
                cpu_max_percent=70, gpu_max_percent=80,
                dram_max_mhz=4800, usb_suspend=True,
                pcie_aspm=True, turbe_boost=False,
                screen_dim_s=120, screen_off_s=300, sleep_s=900,
            ),
            PowerProfileConfig(
                name="Ultra Saver", governor=CPUGovernor.POWERSAVE,
                cpu_max_percent=40, gpu_max_percent=50,
                dram_max_mhz=3200, usb_suspend=True,
                pcie_aspm=True, turbe_boost=False, kernel_nohz=False,
                screen_dim_s=60, screen_off_s=120, sleep_s=300,
            ),
            PowerProfileConfig(
                name="Custom Dev", governor=CPUGovernor.SCHEDUTIL,
                cpu_max_percent=85, gpu_max_percent=90,
                dram_max_mhz=6000, usb_suspend=False,
                pcie_aspm=True, turbe_boost=True,
                screen_dim_s=300, screen_off_s=0, sleep_s=0,
            ),
        ]

        self.power_limits = [
            PowerLimit("CPU Package", 125.0, 10.0, 170.0, 170.0),
            PowerLimit("CPU PPT", 142.0, 10.0, 200.0, 200.0),
            PowerLimit("GPU TDP", 285.0, 30.0, 450.0, 450.0),
            PowerLimit("GPU Boost", 350.0, 30.0, 450.0, 450.0),
            PowerLimit("DRAM", 10.0, 3.0, 15.0, 15.0),
            PowerLimit("USB Controller", 4.5, 0.5, 7.5, 7.5),
        ]

        self.thermal_sensors = [
            ThermalSensor("CPU Package", 62.0, 42.0, 85.0, 100.0, 80.0, 1200, 35),
            ThermalSensor("CPU Core 0", 58.0, 40.0, 82.0, 100.0, 80.0, 0, 0),
            ThermalSensor("CPU Core 7", 65.0, 43.0, 88.0, 100.0, 80.0, 0, 0),
            ThermalSensor("GPU Die", 55.0, 38.0, 78.0, 95.0, 83.0, 1100, 30),
            ThermalSensor("GPU Memory", 60.0, 40.0, 80.0, 100.0, 90.0, 0, 0),
            ThermalSensor("NVMe SSD", 42.0, 30.0, 55.0, 70.0, 60.0, 0, 0),
            ThermalSensor("Motherboard", 38.0, 28.0, 50.0, 80.0, 65.0, 0, 0),
            ThermalSensor("VRM", 52.0, 35.0, 72.0, 110.0, 95.0, 0, 0),
        ]

        self.fan_profiles = [
            FanProfile("Silent", [(40, 25), (55, 35), (70, 55), (80, 75), (90, 100)],
                       min_rpm=300, max_rpm=1500),
            FanProfile("Balanced", [(40, 30), (55, 45), (70, 65), (80, 85), (90, 100)],
                       min_rpm=400, max_rpm=2000),
            FanProfile("Performance", [(35, 35), (50, 55), (65, 75), (80, 90), (90, 100)],
                       min_rpm=600, max_rpm=2500),
            FanProfile("Aggressive", [(30, 45), (45, 65), (60, 85), (75, 95), (85, 100)],
                       min_rpm=800, max_rpm=3000),
        ]
        self.active_fan_profile = "Balanced"

        self.wake_schedules = [
            WakeSchedule("Workday", True, 7, 0, [0, 1, 2, 3, 4]),
            WakeSchedule("Weekend", True, 9, 30, [5, 6]),
            WakeSchedule("Backup", False, 3, 0, [0]),
        ]

        self.events = [
            PowerEvent(now - 300, "profile", "Switched to Balanced"),
            PowerEvent(now - 600, "thermal", "CPU temp reached 75°C, fan ramped up"),
            PowerEvent(now - 1200, "sleep", "System suspended (lid close)"),
            PowerEvent(now - 1199, "wake", "Resume from lid open", True),
            PowerEvent(now - 3600, "charge", "Battery reached 100%"),
            PowerEvent(now - 7200, "profile", "Switched to Performance for build"),
            PowerEvent(now - 7800, "throttle", "GPU thermal throttle at 83°C"),
            PowerEvent(now - 14400, "sleep", "Auto-sleep after 30m idle"),
            PowerEvent(now - 14401, "wake", "Network wake packet received", True),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    def set_view(self, view: str):
        self._view_mode = view

    def select_profile(self, idx: int):
        if 0 <= idx < len(self.profiles):
            self._selected_profile = idx

    def select_down(self):
        self._selected_profile = min(self._selected_profile + 1, len(self.profiles) - 1)

    def select_up(self):
        self._selected_profile = max(self._selected_profile - 1, 0)

    # ─── Profile Actions ───────────────────────────────────────────────

    def activate_profile(self, idx: int) -> bool:
        if 0 <= idx < len(self.profiles):
            self._selected_profile = idx
            self.active_profile = [
                PowerProfile.PERFORMANCE, PowerProfile.BALANCED,
                PowerProfile.POWERSAVER, PowerProfile.ULTRA_SAVER,
                PowerProfile.CUSTOM,
            ][idx] if idx < 5 else PowerProfile.CUSTOM
            self.events.insert(0, PowerEvent(
                time.time(), "profile", f"Switched to {self.profiles[idx].name}"
            ))
            return True
        return False

    def set_cpu_max(self, idx: int, percent: int) -> bool:
        if 0 <= idx < len(self.profiles):
            self.profiles[idx].cpu_max_percent = max(10, min(100, percent))
            return True
        return False

    def set_gpu_max(self, idx: int, percent: int) -> bool:
        if 0 <= idx < len(self.profiles):
            self.profiles[idx].gpu_max_percent = max(10, min(100, percent))
            return True
        return False

    def toggle_turbo(self, idx: int) -> bool:
        if 0 <= idx < len(self.profiles):
            self.profiles[idx].turbe_boost = not self.profiles[idx].turbe_boost
            return True
        return False

    def toggle_pcie_aspm(self, idx: int) -> bool:
        if 0 <= idx < len(self.profiles):
            self.profiles[idx].pcie_aspm = not self.profiles[idx].pcie_aspm
            return True
        return False

    def set_governor(self, idx: int, governor: CPUGovernor) -> bool:
        if 0 <= idx < len(self.profiles):
            self.profiles[idx].governor = governor
            return True
        return False

    # ─── Thermal ───────────────────────────────────────────────────────

    def set_fan_profile(self, name: str) -> bool:
        if any(fp.name == name for fp in self.fan_profiles):
            self.active_fan_profile = name
            return True
        return False

    def set_thermal_policy(self, policy: ThermalPolicy) -> bool:
        self.thermal_policy = policy
        return True

    # ─── Power Limits ──────────────────────────────────────────────────

    def set_power_limit(self, idx: int, watts: float) -> bool:
        if 0 <= idx < len(self.power_limits):
            limit = self.power_limits[idx]
            limit.current_watts = max(limit.min_watts, min(limit.max_watts, watts))
            return True
        return False

    def reset_power_limit(self, idx: int) -> bool:
        if 0 <= idx < len(self.power_limits):
            self.power_limits[idx].current_watts = self.power_limits[idx].default_watts
            return True
        return False

    # ─── Battery ───────────────────────────────────────────────────────

    def set_charge_limit(self, limit: float) -> None:
        self.battery.charge_percent = max(0, min(100, limit))

    # ─── Schedules ─────────────────────────────────────────────────────

    def toggle_wake_schedule(self, idx: int) -> bool:
        if 0 <= idx < len(self.wake_schedules):
            self.wake_schedules[idx].enabled = not self.wake_schedules[idx].enabled
            return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_total_power(self) -> float:
        return sum(l.current_watts for l in self.power_limits)

    def get_max_power(self) -> float:
        return sum(l.default_watts for l in self.power_limits)

    def get_power_usage_bar(self) -> str:
        total = self.get_total_power()
        max_power = self.get_max_power()
        if max_power == 0:
            return ""
        pct = (total / max_power) * 100
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    def get_thermal_warnings(self) -> List[ThermalSensor]:
        return [s for s in self.thermal_sensors if s.current_c >= s.warning_c]

    def search_profiles(self, query: str) -> List[PowerProfileConfig]:
        q = query.lower()
        return [p for p in self.profiles if q in p.name.lower()]

    def get_stats(self) -> Dict:
        return {
            "active_profile": self.active_profile.value,
            "profiles": len(self.profiles),
            "power_limits": len(self.power_limits),
            "thermal_sensors": len(self.thermal_sensors),
            "thermal_warnings": len(self.get_thermal_warnings()),
            "fan_profiles": len(self.fan_profiles),
            "active_fan": self.active_fan_profile,
            "total_power_w": round(self.get_total_power(), 1),
            "battery_percent": self.battery.charge_percent,
            "battery_health": self.battery.health_percent,
            "events": len(self.events),
            "wake_schedules": len(self.wake_schedules),
        }
