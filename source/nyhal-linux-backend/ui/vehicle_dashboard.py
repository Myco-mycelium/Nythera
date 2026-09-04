"""
Nyrqis OS - Vehicle Dashboard
Speedometer, fuel gauge, trip computer, and OBD-II diagnostics.

Features:
- Real-time speed/RPM gauges with needle animation
- Fuel/charge level with range estimation
- Trip computer (distance, time, avg speed, fuel economy)
- OBD-II diagnostic trouble codes (DTCs)
- Temperature and pressure gauges
- Tire pressure monitoring (TPMS)
- Service interval tracking
- Multiple trip memories
- Driving score and efficiency metrics
"""

import time
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class FuelType(Enum):
    GASOLINE = "gasoline"
    DIESEL = "diesel"
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    PLUG_IN_HYBRID = "plug_in_hybrid"


class DriveMode(Enum):
    ECO = "eco"
    NORMAL = "normal"
    SPORT = "sport"
    COMFORT = "comfort"
    OFF_ROAD = "off_road"
    CUSTOM = "custom"


class GearMode(Enum):
    PARK = "P"
    REVERSE = "R"
    NEUTRAL = "N"
    DRIVE = "D"
    LOW = "L"
    SPORT = "S"
    MANUAL = "M"


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DTCStatus(Enum):
    ACTIVE = "active"
    PENDING = "pending"
    PERMANENT = "permanent"
    CLEARED = "cleared"


FUEL_ICONS = {
    FuelType.GASOLINE: "⛽", FuelType.DIESEL: "⛽",
    FuelType.ELECTRIC: "🔋", FuelType.HYBRID: "🔋",
    FuelType.PLUG_IN_HYBRID: "🔌",
}

DRIVE_MODE_ICONS = {
    DriveMode.ECO: "🌿", DriveMode.NORMAL: "⚖️",
    DriveMode.SPORT: "🏎️", DriveMode.COMFORT: "🛋️",
    DriveMode.OFF_ROAD: "🏔️", DriveMode.CUSTOM: "🔧",
}


@dataclass
class SpeedGauge:
    current_speed: float = 0.0  # km/h
    max_speed: float = 260.0
    redline_speed: float = 220.0
    cruise_speed: float = 0.0
    cruise_active: bool = False
    speed_limit: float = 0.0

    @property
    def needle_angle(self) -> float:
        # Map 0-max_speed to 0-270 degrees
        ratio = min(1.0, self.current_speed / self.max_speed)
        return ratio * 270

    @property
    def speed_str(self) -> str:
        return f"{self.current_speed:.0f}"

    @property
    def unit(self) -> str:
        return "km/h"

    @property
    def speed_bar(self) -> str:
        filled = int((self.current_speed / self.max_speed) * 30)
        return "█" * filled + "░" * (30 - filled)

    @property
    def cruise_str(self) -> str:
        if self.cruise_active:
            return f"CRUISE {self.cruise_speed:.0f} km/h"
        return ""

    @property
    def limit_warning(self) -> str:
        if self.speed_limit > 0 and self.current_speed > self.speed_limit:
            return f"⚠️ {self.speed_limit:.0f} km/h LIMIT"
        return ""


@dataclass
class RPMGauge:
    current_rpm: float = 0.0
    max_rpm: float = 8000.0
    redline_rpm: float = 6500.0
    idle_rpm: float = 750.0

    @property
    def needle_angle(self) -> float:
        ratio = min(1.0, self.current_rpm / self.max_rpm)
        return ratio * 270

    @property
    def rpm_str(self) -> str:
        return f"{self.current_rpm:.0f}"

    @property
    def is_redline(self) -> bool:
        return self.current_rpm >= self.redline_rpm

    @property
    def rpm_bar(self) -> str:
        filled = int((self.current_rpm / self.max_rpm) * 30)
        return "█" * filled + "░" * (30 - filled)


@dataclass
class FuelGauge:
    level_percent: float = 0.0  # 0-100
    range_km: float = 0.0
    fuel_type: FuelType = FuelType.GASOLINE
    tank_capacity_l: float = 60.0
    current_liters: float = 0.0
    is_electric: bool = False
    battery_percent: float = 0.0
    battery_range_km: float = 0.0
    charging: bool = False

    @property
    def level_bar(self) -> str:
        filled = int(self.level_percent / 3.33)
        return "█" * filled + "░" * (30 - filled)

    @property
    def fuel_icon(self) -> str:
        return FUEL_ICONS.get(self.fuel_type, "⛽")

    @property
    def level_str(self) -> str:
        return f"{self.level_percent:.0f}%"

    @property
    def range_str(self) -> str:
        return f"{self.range_km:.0f} km"

    @property
    def liters_str(self) -> str:
        return f"{self.current_liters:.1f}/{self.tank_capacity_l:.0f} L"

    @property
    def status_icon(self) -> str:
        if self.level_percent > 50:
            return "🟢"
        elif self.level_percent > 20:
            return "🟡"
        elif self.level_percent > 10:
            return "🟠"
        return "🔴"

    @property
    def charging_str(self) -> str:
        if self.charging:
            return f"⚡ Charging {self.battery_percent:.0f}%"
        return ""


@dataclass
class TripComputer:
    name: str = "Trip A"
    distance_km: float = 0.0
    duration_s: float = 0.0
    avg_speed_kmh: float = 0.0
    max_speed_kmh: float = 0.0
    fuel_consumed_l: float = 0.0
    fuel_economy_l100: float = 0.0
    fuel_economy_mpg: float = 0.0
    avg_rpm: float = 0.0
    idle_time_s: float = 0.0
    start_time: float = 0.0
    co2_kg: float = 0.0

    @property
    def duration_str(self) -> str:
        h = int(self.duration_s // 3600)
        m = int((self.duration_s % 3600) // 60)
        s = int(self.duration_s % 60)
        if h > 0:
            return f"{h}h {m:02d}m"
        return f"{m}m {s:02d}s"

    @property
    def distance_str(self) -> str:
        return f"{self.distance_km:.1f} km"

    @property
    def economy_str(self) -> str:
        return f"{self.fuel_economy_l100:.1f} L/100km"

    @property
    def avg_speed_str(self) -> str:
        return f"{self.avg_speed_kmh:.0f} km/h"

    @property
    def max_speed_str(self) -> str:
        return f"{self.max_speed_kmh:.0f} km/h"

    @property
    def idle_percent(self) -> float:
        if self.duration_s == 0:
            return 0.0
        return (self.idle_time_s / self.duration_s) * 100

    @property
    def idle_bar(self) -> str:
        filled = int(self.idle_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def economy_bar(self) -> str:
        # Good is < 6 L/100km, bad is > 12
        if self.fuel_economy_l100 <= 0:
            return "░░░░░░░░░░░░░░░░░░░░"
        pct = min(100, max(0, (1 - (self.fuel_economy_l100 - 3) / 12) * 100))
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def co2_str(self) -> str:
        return f"{self.co2_kg:.1f} kg"


@dataclass
class TemperatureGauge:
    name: str = "Engine"
    current_c: float = 0.0
    min_c: float = 0.0
    max_c: float = 120.0
    warning_c: float = 100.0
    critical_c: float = 110.0

    @property
    def gauge_bar(self) -> str:
        if self.max_c == 0:
            return ""
        pct = min(100, (self.current_c / self.max_c) * 100)
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
    def temp_str(self) -> str:
        return f"{self.current_c:.0f}°C"


@dataclass
class TirePressure:
    position: str = ""  # FL, FR, RL, RR
    pressure_psi: float = 0.0
    recommended_psi: float = 35.0
    temperature_c: float = 25.0

    @property
    def status(self) -> str:
        diff = abs(self.pressure_psi - self.recommended_psi)
        if diff > 5:
            return "🔴 Low/High"
        elif diff > 2:
            return "🟡 Monitor"
        return "🟢 OK"

    @property
    def pressure_bar(self) -> str:
        pct = min(100, (self.pressure_psi / 50) * 100)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def diff_str(self) -> str:
        diff = self.pressure_psi - self.recommended_psi
        sign = "+" if diff > 0 else ""
        return f"{sign}{diff:.1f} PSI"


@dataclass
class DTC:
    code: str = ""
    description: str = ""
    status: DTCStatus = DTCStatus.ACTIVE
    system: str = ""  # Engine, Transmission, ABS, etc.
    severity: AlertLevel = AlertLevel.WARNING
    detected_at: float = 0.0
    occurrences: int = 1

    @property
    def status_icon(self) -> str:
        icons = {DTCStatus.ACTIVE: "🔴", DTCStatus.PENDING: "🟡",
                 DTCStatus.PERMANENT: "🟠", DTCStatus.CLEARED: "🟢"}
        return icons.get(self.status, "❓")

    @property
    def severity_icon(self) -> str:
        icons = {AlertLevel.INFO: "ℹ️", AlertLevel.WARNING: "⚠️",
                 AlertLevel.CRITICAL: "🔴"}
        return icons.get(self.severity, "❓")


@dataclass
class ServiceInterval:
    name: str = ""
    interval_km: float = 0.0
    interval_days: int = 0
    last_service_km: float = 0.0
    last_service_date: float = 0.0
    current_km: float = 0.0

    @property
    def remaining_km(self) -> float:
        return max(0, self.interval_km - (self.current_km - self.last_service_km))

    @property
    def remaining_days(self) -> int:
        elapsed = (time.time() - self.last_service_date) / 86400
        return max(0, int(self.interval_days - elapsed))

    @property
    def remaining_km_str(self) -> str:
        return f"{self.remaining_km:,.0f} km"

    @property
    def remaining_days_str(self) -> str:
        return f"{self.remaining_days} days"

    @property
    def urgency(self) -> str:
        km_ratio = self.remaining_km / self.interval_km if self.interval_km else 1
        day_ratio = self.remaining_days / self.interval_days if self.interval_days else 1
        ratio = min(km_ratio, day_ratio)
        if ratio <= 0:
            return "🔴 OVERDUE"
        elif ratio <= 0.1:
            return "🔴 Due Soon"
        elif ratio <= 0.25:
            return "🟡 Coming Up"
        return "🟢 OK"

    @property
    def progress_bar(self) -> str:
        km_ratio = self.remaining_km / self.interval_km if self.interval_km else 1
        used = 1 - min(1, km_ratio)
        filled = int(used * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class DrivingScore:
    overall: float = 0.0  # 0-100
    acceleration: float = 0.0
    braking: float = 0.0
    cornering: float = 0.0
    speed_compliance: float = 0.0
    fuel_efficiency: float = 0.0
    eco_score: float = 0.0
    safe_score: float = 0.0
    trips_analyzed: int = 0

    @property
    def overall_bar(self) -> str:
        filled = int(self.overall / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def grade(self) -> str:
        s = self.overall
        if s >= 95:
            return "A+"
        elif s >= 90:
            return "A"
        elif s >= 80:
            return "B"
        elif s >= 70:
            return "C"
        elif s >= 60:
            return "D"
        return "F"

    @property
    def grade_icon(self) -> str:
        g = self.grade
        if "A" in g:
            return "🏆"
        elif g == "B":
            return "🌟"
        elif g == "C":
            return "👍"
        return "⚠️"


class VehicleDashboard:
    def __init__(self):
        self.speed = SpeedGauge()
        self.rpm = RPMGauge()
        self.fuel = FuelGauge()
        self.gear = GearMode.PARK
        self.drive_mode = DriveMode.NORMAL
        self.trips: List[TripComputer] = []
        self.temperatures: List[TemperatureGauge] = []
        self.tires: List[TirePressure] = []
        self.dtc_codes: List[DTC] = []
        self.services: List[ServiceInterval] = []
        self.driving_score = DrivingScore()
        self.odo_km: float = 45230.0
        self.trip_a_km: float = 0.0
        self.trip_b_km: float = 0.0
        self._selected_trip: int = 0
        self._view_mode: str = "gauges"
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.speed = SpeedGauge(current_speed=65.0, cruise_active=True, cruise_speed=65.0, speed_limit=80.0)
        self.rpm = RPMGauge(current_rpm=2200.0)
        self.fuel = FuelGauge(level_percent=72.0, range_km=485.0, fuel_type=FuelType.GASOLINE,
                               tank_capacity_l=60.0, current_liters=43.2)

        self.trips = [
            TripComputer("Trip A", 156.8, 7200, 78.4, 120.0, 11.2, 7.1, 33.1,
                         2100, 1800, now - 7200, 2.6),
            TripComputer("Trip B", 2340.5, 86400, 97.5, 165.0, 168.3, 7.2, 32.7,
                         2300, 21600, now - 86400, 39.1),
            TripComputer("Tank", 412.3, 172800, 85.7, 180.0, 29.3, 7.1, 33.1,
                         2200, 36000, now - 172800, 68.2),
        ]

        self.temperatures = [
            TemperatureGauge("Engine Coolant", 88.0, 60, 120, 100, 110),
            TemperatureGauge("Oil", 92.0, 50, 130, 110, 120),
            TemperatureGauge("Transmission", 75.0, 40, 120, 100, 115),
            TemperatureGauge("Intake Air", 32.0, -10, 60, 45, 55),
            TemperatureGauge("Exhaust", 320.0, 50, 800, 700, 780),
        ]

        self.tires = [
            TirePressure("FL", 34.5, 35.0, 38.0),
            TirePressure("FR", 35.2, 35.0, 39.0),
            TirePressure("RL", 33.8, 35.0, 36.0),
            TirePressure("RR", 34.0, 35.0, 37.0),
        ]

        self.dtc_codes = [
            DTC("P0420", "Catalyst System Efficiency Below Threshold (Bank 1)",
                DTCStatus.ACTIVE, "Engine", AlertLevel.WARNING, now - 86400 * 30, 5),
            DTC("P0301", "Cylinder 1 Misfire Detected",
                DTCStatus.CLEARED, "Engine", AlertLevel.WARNING, now - 86400 * 60, 12),
            DTC("U0100", "Lost Communication with ECM/PCM",
                DTCStatus.PENDING, "Network", AlertLevel.INFO, now - 86400, 1),
        ]

        self.services = [
            ServiceInterval("Oil Change", 15000, 365, 43000, now - 86400 * 60, self.odo_km),
            ServiceInterval("Tire Rotation", 10000, 180, 40000, now - 86400 * 90, self.odo_km),
            ServiceInterval("Brake Inspection", 30000, 730, 30000, now - 86400 * 180, self.odo_km),
            ServiceInterval("Air Filter", 30000, 365, 20000, now - 86400 * 200, self.odo_km),
            ServiceInterval("Transmission Fluid", 60000, 1095, 0, now - 86400 * 400, self.odo_km),
        ]

        self.driving_score = DrivingScore(
            overall=87.5, acceleration=90.0, braking=85.0, cornering=88.0,
            speed_compliance=92.0, fuel_efficiency=82.0, eco_score=85.0,
            safe_score=90.0, trips_analyzed=156,
        )

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_trip(self) -> Optional[TripComputer]:
        if 0 <= self._selected_trip < len(self.trips):
            return self.trips[self._selected_trip]
        return None

    def select_trip(self, idx: int):
        if 0 <= idx < len(self.trips):
            self._selected_trip = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_trip = min(self._selected_trip + 1, len(self.trips) - 1)

    def select_up(self):
        self._selected_trip = max(self._selected_trip - 1, 0)

    # ─── Actions ───────────────────────────────────────────────────────

    def set_drive_mode(self, mode: DriveMode):
        self.drive_mode = mode

    def toggle_cruise(self):
        self.speed.cruise_active = not self.speed.cruise_active

    def set_cruise_speed(self, speed: float):
        self.speed.cruise_speed = max(30, min(200, speed))
        if self.speed.cruise_active:
            self.speed.current_speed = self.speed.cruise_speed

    def clear_dtc(self, idx: int) -> bool:
        if 0 <= idx < len(self.dtc_codes):
            self.dtc_codes[idx].status = DTCStatus.CLEARED
            return True
        return False

    def reset_trip(self, idx: int) -> bool:
        if 0 <= idx < len(self.trips):
            trip = self.trips[idx]
            trip.distance_km = 0
            trip.duration_s = 0
            trip.fuel_consumed_l = 0
            trip.fuel_economy_l100 = 0
            return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_active_dtcs(self) -> List[DTC]:
        return [d for d in self.dtc_codes if d.status == DTCStatus.ACTIVE]

    def get_pending_dtcs(self) -> List[DTC]:
        return [d for d in self.dtc_codes if d.status == DTCStatus.PENDING]

    def get_overdue_services(self) -> List[ServiceInterval]:
        return [s for s in self.services if s.remaining_km <= 0 or s.remaining_days <= 0]

    def get_upcoming_services(self) -> List[ServiceInterval]:
        return [s for s in self.services if 0 < s.remaining_km <= 3000 or 0 < s.remaining_days <= 30]

    def get_stats(self) -> Dict:
        return {
            "speed": self.speed.current_speed,
            "rpm": self.rpm.current_rpm,
            "fuel_percent": self.fuel.level_percent,
            "range_km": self.fuel.range_km,
            "odo_km": self.odo_km,
            "gear": self.gear.value,
            "drive_mode": self.drive_mode.value,
            "active_dtcs": len(self.get_active_dtcs()),
            "pending_dtcs": len(self.get_pending_dtcs()),
            "services_overdue": len(self.get_overdue_services()),
            "driving_score": self.driving_score.overall,
            "trips": len(self.trips),
        }
