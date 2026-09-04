"""
Nyrqis OS - Sensor Monitor
Temperature, fan speed, and hardware alerts.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class SensorType(Enum):
    TEMPERATURE = "temperature"
    FAN_SPEED = "fan_speed"
    VOLTAGE = "voltage"
    POWER = "power"
    CURRENT = "current"
    HUMIDITY = "humidity"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class SensorReading:
    name: str
    sensor_type: SensorType = SensorType.TEMPERATURE
    value: float = 0.0
    unit: str = ""
    min_value: float = 0.0
    max_value: float = 0.0
    high_threshold: float = 0.0
    critical_threshold: float = 0.0
    label: str = ""
    chip: str = ""
    history: List[float] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        if self.sensor_type == SensorType.TEMPERATURE:
            if self.value < self.high_threshold * 0.7:
                return "🟢"
            elif self.value < self.high_threshold:
                return "🟡"
            elif self.value < self.critical_threshold:
                return "🟠"
            return "🔴"
        elif self.sensor_type == SensorType.FAN_SPEED:
            if self.value > 0:
                return "🟢"
            return "⚪"
        return "🟢"

    @property
    def value_display(self) -> str:
        return f"{self.value:.1f}{self.unit}"

    @property
    def range_display(self) -> str:
        return f"{self.min_value:.0f}-{self.max_value:.0f}{self.unit}"

    @property
    def bar(self) -> str:
        if self.max_value == 0:
            return "░" * 20
        pct = min(100, (self.value / self.max_value) * 100)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def trend(self) -> str:
        if len(self.history) < 2:
            return "→"
        if self.history[-1] > self.history[-2]:
            return "↑"
        elif self.history[-1] < self.history[-2]:
            return "↓"
        return "→"


@dataclass
class FanProfile:
    name: str
    curve: List[tuple] = field(default_factory=list)  # (temp, speed%)
    target_temp: float = 75.0
    hysteresis: float = 3.0

    @property
    def description(self) -> str:
        if self.name == "Silent":
            return "Quiet operation, higher temps"
        elif self.name == "Balanced":
            return "Balance noise and cooling"
        elif self.name == "Performance":
            return "Maximum cooling"
        return "Custom fan curve"


@dataclass
class SensorAlert:
    sensor_name: str
    severity: AlertSeverity = AlertSeverity.WARNING
    message: str = ""
    value: float = 0.0
    threshold: float = 0.0
    timestamp: float = 0.0
    acknowledged: bool = False

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def severity_icon(self) -> str:
        icons = {
            AlertSeverity.INFO: "ℹ️", AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨", AlertSeverity.EMERGENCY: "💀",
        }
        return icons.get(self.severity, "?")


class SensorMonitor:
    def __init__(self):
        self.sensors: List[SensorReading] = []
        self.fan_profiles: List[FanProfile] = []
        self.active_profile: Optional[FanProfile] = None
        self.alerts: List[SensorAlert] = []
        self.auto_fan: bool = True
        self.poll_interval_ms: int = 1000
        self._create_sample_data()

    def _create_sample_data(self):
        self.sensors = [
            SensorReading(name="CPU Package", sensor_type=SensorType.TEMPERATURE,
                          value=62.0, unit="°C", min_value=25.0, max_value=105.0,
                          high_threshold=80.0, critical_threshold=95.0,
                          label="CPU", chip="k10temp-pci-00c3",
                          history=[58, 59, 60, 61, 62]),
            SensorReading(name="GPU Core", sensor_type=SensorType.TEMPERATURE,
                          value=55.0, unit="°C", min_value=25.0, max_value=93.0,
                          high_threshold=80.0, critical_threshold=90.0,
                          label="GPU", chip="nvidia-pci-0100",
                          history=[52, 53, 54, 54, 55]),
            SensorReading(name="NVMe SSD", sensor_type=SensorType.TEMPERATURE,
                          value=42.0, unit="°C", min_value=20.0, max_value=70.0,
                          high_threshold=55.0, critical_threshold=65.0,
                          label="Storage", chip="nvme-pci-0200",
                          history=[40, 40, 41, 41, 42]),
            SensorReading(name="Motherboard", sensor_type=SensorType.TEMPERATURE,
                          value=38.0, unit="°C", min_value=20.0, max_value=80.0,
                          high_threshold=55.0, critical_threshold=70.0,
                          label="Mobo", chip="acpitz-acpi-0",
                          history=[36, 37, 37, 38, 38]),
            SensorReading(name="DIMM A1", sensor_type=SensorType.TEMPERATURE,
                          value=45.0, unit="°C", min_value=20.0, max_value=85.0,
                          high_threshold=60.0, critical_threshold=80.0,
                          label="RAM", chip="stick-0",
                          history=[43, 44, 44, 45, 45]),
            SensorReading(name="CPU Fan", sensor_type=SensorType.FAN_SPEED,
                          value=1250.0, unit=" RPM", min_value=0.0, max_value=3000.0,
                          high_threshold=2500.0, critical_threshold=2800.0,
                          label="Fan1", chip="nct6798",
                          history=[1100, 1150, 1200, 1230, 1250]),
            SensorReading(name="Case Fan", sensor_type=SensorType.FAN_SPEED,
                          value=800.0, unit=" RPM", min_value=0.0, max_value=2000.0,
                          high_threshold=1800.0, critical_threshold=1900.0,
                          label="Fan2", chip="nct6798",
                          history=[750, 770, 780, 790, 800]),
            SensorReading(name="GPU Fan", sensor_type=SensorType.FAN_SPEED,
                          value=1400.0, unit=" RPM", min_value=0.0, max_value=4000.0,
                          high_threshold=3500.0, critical_threshold=3800.0,
                          label="Fan3", chip="nvidia-pci-0100",
                          history=[1200, 1250, 1300, 1350, 1400]),
            SensorReading(name="CPU Core", sensor_type=SensorType.POWER,
                          value=125.0, unit="W", min_value=0.0, max_value=250.0,
                          high_threshold=200.0, critical_threshold=230.0,
                          label="TDP", chip="k10temp",
                          history=[110, 115, 120, 122, 125]),
            SensorReading(name="GPU Power", sensor_type=SensorType.POWER,
                          value=285.0, unit="W", min_value=0.0, max_value=450.0,
                          high_threshold=380.0, critical_threshold=420.0,
                          label="TGP", chip="nvidia",
                          history=[260, 270, 275, 280, 285]),
            SensorReading(name="CPU Vcore", sensor_type=SensorType.VOLTAGE,
                          value=1.25, unit="V", min_value=0.8, max_value=1.5,
                          high_threshold=1.4, critical_threshold=1.45,
                          label="Vcore", chip="nct6798"),
            SensorReading(name="+12V Rail", sensor_type=SensorType.VOLTAGE,
                          value=12.08, unit="V", min_value=10.0, max_value=14.0,
                          high_threshold=13.2, critical_threshold=13.8,
                          label="12V", chip="nct6798"),
        ]

        self.fan_profiles = [
            FanProfile(name="Silent", curve=[(40, 30), (55, 40), (70, 55), (80, 70), (90, 100)],
                       target_temp=80.0),
            FanProfile(name="Balanced", curve=[(30, 25), (50, 40), (65, 55), (75, 70), (85, 90), (95, 100)],
                       target_temp=75.0),
            FanProfile(name="Performance", curve=[(25, 40), (45, 55), (60, 70), (70, 85), (80, 100)],
                       target_temp=65.0),
            FanProfile(name="Aggressive", curve=[(20, 50), (40, 70), (55, 85), (70, 100)],
                       target_temp=60.0),
        ]
        self.active_profile = self.fan_profiles[1]

        self.alerts = [
            SensorAlert(sensor_name="CPU Package", severity=AlertSeverity.WARNING,
                        message="CPU temperature above normal", value=62.0,
                        threshold=80.0, timestamp=time.time() - 300),
            SensorAlert(sensor_name="GPU Power", severity=AlertSeverity.INFO,
                        message="GPU power draw elevated", value=285.0,
                        threshold=380.0, timestamp=time.time() - 600),
        ]

    def get_sensors_by_type(self, sensor_type: SensorType) -> List[SensorReading]:
        return [s for s in self.sensors if s.sensor_type == sensor_type]

    def get_temperatures(self) -> List[SensorReading]:
        return self.get_sensors_by_type(SensorType.TEMPERATURE)

    def get_fans(self) -> List[SensorReading]:
        return self.get_sensors_by_type(SensorType.FAN_SPEED)

    def get_power(self) -> List[SensorReading]:
        return self.get_sensors_by_type(SensorType.POWER)

    def get_voltages(self) -> List[SensorReading]:
        return self.get_sensors_by_type(SensorType.VOLTAGE)

    def get_max_temperature(self) -> Optional[SensorReading]:
        temps = self.get_temperatures()
        return max(temps, key=lambda s: s.value) if temps else None

    def set_fan_profile(self, name: str) -> bool:
        profile = next((p for p in self.fan_profiles if p.name == name), None)
        if profile:
            self.active_profile = profile
            return True
        return False

    def acknowledge_alert(self, index: int) -> bool:
        if 0 <= index < len(self.alerts):
            self.alerts[index].acknowledged = True
            return True
        return False

    def check_thresholds(self) -> List[SensorAlert]:
        new_alerts = []
        for sensor in self.sensors:
            if sensor.sensor_type == SensorType.TEMPERATURE:
                if sensor.value >= sensor.critical_threshold:
                    alert = SensorAlert(sensor_name=sensor.name,
                                         severity=AlertSeverity.CRITICAL,
                                         message=f"{sensor.name} critical: {sensor.value_display}",
                                         value=sensor.value, threshold=sensor.critical_threshold)
                    self.alerts.append(alert)
                    new_alerts.append(alert)
                elif sensor.value >= sensor.high_threshold:
                    alert = SensorAlert(sensor_name=sensor.name,
                                         severity=AlertSeverity.WARNING,
                                         message=f"{sensor.name} high: {sensor.value_display}",
                                         value=sensor.value, threshold=sensor.high_threshold)
                    self.alerts.append(alert)
                    new_alerts.append(alert)
        return new_alerts

    def search(self, query: str) -> List[SensorReading]:
        q = query.lower()
        return [s for s in self.sensors if q in s.name.lower() or q in s.label.lower()]

    def get_stats(self) -> Dict:
        temps = self.get_temperatures()
        max_temp = max((t.value for t in temps), default=0)
        total_power = sum(p.value for p in self.get_power())
        return {
            "total_sensors": len(self.sensors),
            "temperatures": len(temps),
            "fans": len(self.get_fans()),
            "max_temp": max_temp,
            "total_power_w": round(total_power, 1),
            "alerts": len([a for a in self.alerts if not a.acknowledged]),
            "active_profile": self.active_profile.name if self.active_profile else "None",
        }
