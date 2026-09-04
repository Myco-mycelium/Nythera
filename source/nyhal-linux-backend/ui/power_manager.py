"""
Nyrqis OS - Power Manager
Battery stats, power profiles, and sleep/hibernate controls.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class PowerProfile(Enum):
    PERFORMANCE = "performance"
    BALANCED = "balanced"
    POWER_SAVER = "power_saver"
    ULTRA_SAVER = "ultra_saver"
    CUSTOM = "custom"


class BatteryState(Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    FULL = "full"
    NOT_PRESENT = "not_present"
    UNKNOWN = "unknown"


class SleepAction(Enum):
    SUSPEND = "suspend"
    HIBERNATE = "hibernate"
    HYBRID = "hybrid"
    SHUTDOWN = "shutdown"
    NOTHING = "nothing"


class ThermalState(Enum):
    COOL = "cool"
    WARM = "warm"
    HOT = "hot"
    CRITICAL = "critical"


@dataclass
class BatteryInfo:
    state: BatteryState = BatteryState.CHARGING
    charge_percent: float = 85.0
    capacity_mah: float = 8000.0
    voltage: float = 12.0
    temperature_c: float = 32.0
    cycle_count: int = 245
    manufacturer: str = "Samsung SDI"
    model: str = "Nyrqis Battery"
    serial: str = "BAT-2026-NR1"
    first_row_date: str = "2025-06-15"
    design_capacity_mah: float = 8000.0
    energy_wh: float = 96.0
    time_to_empty_s: float = 0.0
    time_to_full_s: float = 0.0

    @property
    def health_percent(self) -> float:
        if self.design_capacity_mah == 0:
            return 0.0
        return (self.capacity_mah / self.design_capacity_mah) * 100

    @property
    def health_status(self) -> str:
        h = self.health_percent
        if h > 90:
            return "🟢 Excellent"
        elif h > 70:
            return "🟡 Good"
        elif h > 50:
            return "🟠 Fair"
        return "🔴 Poor"

    @property
    def charge_bar(self) -> str:
        filled = int(self.charge_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def state_icon(self) -> str:
        icons = {
            BatteryState.CHARGING: "⚡",
            BatteryState.DISCHARGING: "🔋",
            BatteryState.FULL: "✅",
            BatteryState.NOT_PRESENT: "⬜",
            BatteryState.UNKNOWN: "❓",
        }
        return icons.get(self.state, "?")

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 35:
            return "🟢 Cool"
        elif self.temperature_c < 45:
            return "🟡 Warm"
        return "🔴 Hot"


@dataclass
class PowerEvent:
    timestamp: float
    event_type: str  # sleep, wake, charge_start, charge_stop, profile_change
    details: str = ""
    duration_s: float = 0.0

    @property
    def event_icon(self) -> str:
        icons = {
            "sleep": "😴", "wake": "⏰", "charge_start": "🔌",
            "charge_stop": "🔋", "profile_change": "⚙️",
            "low_battery": "🪫", "thermal_throttle": "🌡️",
        }
        return icons.get(self.event_type, "?")


@dataclass
class PowerProfileConfig:
    name: str
    profile: PowerProfile
    cpu_max_freq_mhz: int = 0
    cpu_governor: str = "schedutil"
    gpu_max_freq_mhz: int = 0
    screen_brightness: int = 80
    keyboard_backlight: int = 50
    wifi_power_save: bool = False
    bluetooth_enabled: bool = True
    auto_sleep_minutes: int = 15
    auto_dim_minutes: int = 5
    spin_down_disk: bool = False
    turbo_boost: bool = True

    @property
    def description(self) -> str:
        descs = {
            PowerProfile.PERFORMANCE: "Maximum performance, no power saving",
            PowerProfile.BALANCED: "Balance performance and power consumption",
            PowerProfile.POWER_SAVER: "Extended battery life with reduced performance",
            PowerProfile.ULTRA_SAVER: "Maximum battery life, minimal performance",
            PowerProfile.CUSTOM: "User-defined power settings",
        }
        return descs.get(self.profile, "")


@dataclass
class PowerStats:
    total_sleep_time_s: float = 0.0
    total_awake_time_s: float = 0.0
    sleep_count: int = 0
    charge_cycles: int = 0
    energy_consumed_wh: float = 0.0
    avg_power_watts: float = 0.0
    peak_power_watts: float = 0.0
    screen_on_hours: float = 0.0


class PowerManager:
    def __init__(self):
        self.battery = BatteryInfo()
        self.profiles: List[PowerProfileConfig] = []
        self.current_profile: Optional[PowerProfileConfig] = None
        self.events: List[PowerEvent] = []
        self.stats = PowerStats()
        self.auto_sleep_enabled: bool = True
        self.low_battery_threshold: int = 15
        self.critical_battery_threshold: int = 5
        self.thermal_state: ThermalState = ThermalState.COOL
        self._create_sample_data()

    def _create_sample_data(self):
        self.profiles = [
            PowerProfileConfig(
                name="Performance", profile=PowerProfile.PERFORMANCE,
                cpu_max_freq_mhz=5700, cpu_governor="performance",
                gpu_max_freq_mhz=2520, screen_brightness=100,
                keyboard_backlight=100, wifi_power_save=False,
                auto_sleep_minutes=0, turbo_boost=True),
            PowerProfileConfig(
                name="Balanced", profile=PowerProfile.BALANCED,
                cpu_max_freq_mhz=4500, cpu_governor="schedutil",
                gpu_max_freq_mhz=2000, screen_brightness=80,
                keyboard_backlight=50, wifi_power_save=False,
                auto_sleep_minutes=15, turbo_boost=True),
            PowerProfileConfig(
                name="Power Saver", profile=PowerProfile.POWER_SAVER,
                cpu_max_freq_mhz=3000, cpu_governor="powersave",
                gpu_max_freq_mhz=1200, screen_brightness=50,
                keyboard_backlight=20, wifi_power_save=True,
                auto_sleep_minutes=10, turbo_boost=False),
            PowerProfileConfig(
                name="Ultra Saver", profile=PowerProfile.ULTRA_SAVER,
                cpu_max_freq_mhz=2000, cpu_governor="powersave",
                gpu_max_freq_mhz=800, screen_brightness=30,
                keyboard_backlight=0, wifi_power_save=True,
                bluetooth_enabled=False, auto_sleep_minutes=5,
                spin_down_disk=True, turbo_boost=False),
        ]
        self.current_profile = self.profiles[1]

        now = time.time()
        self.events = [
            PowerEvent(timestamp=now - 7200, event_type="charge_start",
                       details="Connected to AC charger"),
            PowerEvent(timestamp=now - 3600, event_type="charge_stop",
                       details="Battery reached 85%"),
            PowerEvent(timestamp=now - 2400, event_type="profile_change",
                       details="Switched to Balanced profile"),
            PowerEvent(timestamp=now - 1800, event_type="sleep",
                       details="System sleep (lid close)", duration_s=600),
            PowerEvent(timestamp=now - 1200, event_type="wake",
                       details="System wake (lid open)"),
            PowerEvent(timestamp=now - 600, event_type="profile_change",
                       details="Switched to Performance profile"),
            PowerEvent(timestamp=now - 300, event_type="profile_change",
                       details="Switched back to Balanced profile"),
        ]

        self.stats = PowerStats(
            total_sleep_time_s=3600, total_awake_time_s=28800,
            sleep_count=8, charge_cycles=245,
            energy_consumed_wh=72.0, avg_power_watts=28.5,
            peak_power_watts=185.0, screen_on_hours=6.5)

    def set_profile(self, name: str) -> bool:
        profile = next((p for p in self.profiles if p.name == name), None)
        if profile:
            self.current_profile = profile
            self.events.append(PowerEvent(
                timestamp=time.time(), event_type="profile_change",
                details=f"Switched to {name} profile"))
            return True
        return False

    def sleep_system(self, action: SleepAction) -> bool:
        icons = {SleepAction.SUSPEND: "sleep", SleepAction.HIBERNATE: "sleep",
                 SleepAction.SHUTDOWN: "sleep"}
        self.events.append(PowerEvent(
            timestamp=time.time(),
            event_type=icons.get(action, "sleep"),
            details=f"System {action.value}"))
        return True

    def wake_system(self) -> bool:
        self.events.append(PowerEvent(
            timestamp=time.time(), event_type="wake",
            details="System wake"))
        return True

    def set_brightness(self, level: int) -> bool:
        level = max(0, min(100, level))
        if self.current_profile:
            self.current_profile.screen_brightness = level
        return True

    def set_keyboard_backlight(self, level: int) -> bool:
        level = max(0, min(100, level))
        if self.current_profile:
            self.current_profile.keyboard_backlight = level
        return True

    def get_battery_estimate(self) -> Dict:
        if self.battery.state == BatteryState.CHARGING:
            return {"time_to_full_s": self.battery.time_to_full_s,
                    "time_to_empty_s": 0}
        return {"time_to_full_s": 0,
                "time_to_empty_s": self.battery.time_to_empty_s}

    def get_power_summary(self) -> Dict:
        return {
            "battery_percent": self.battery.charge_percent,
            "battery_state": self.battery.state.value,
            "profile": self.current_profile.name if self.current_profile else "None",
            "thermal_state": self.thermal_state.value,
            "sleep_enabled": self.auto_sleep_enabled,
            "avg_power_w": self.stats.avg_power_watts,
        }

    def get_recent_events(self, limit: int = 10) -> List[PowerEvent]:
        return sorted(self.events, key=lambda e: e.timestamp, reverse=True)[:limit]
