"""
Nyrqis OS - Disk Health Monitor
SMART data, temperature tracking, and failure prediction.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class SMARTStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DiskType(Enum):
    NVME = "nvme"
    SATA_SSD = "sata_ssd"
    HDD = "hdd"


@dataclass
class SMARTAttribute:
    id: int = 0
    name: str = ""
    value: int = 0
    worst: int = 0
    threshold: int = 0
    raw_value: int = 0
    failed: bool = False

    @property
    def status_icon(self) -> str:
        return "🔴" if self.failed else "🟢"

    @property
    def health_pct(self) -> float:
        if self.worst == 0:
            return 100.0
        return (self.value / 100) * 100

    @property
    def status(self) -> str:
        """OK / WARN / FAIL based on value vs threshold."""
        if self.failed or (self.threshold and self.value <= self.threshold):
            return "FAIL"
        if self.threshold and self.value <= self.threshold * 1.2:
            return "WARN"
        return "OK"

    @property
    def bar(self) -> str:
        filled = int(max(0, min(100, self.health_pct)) / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class TemperatureRecord:
    timestamp: float = 0.0
    temperature_c: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class DiskHealth:
    device: str = ""
    model: str = ""
    serial: str = ""
    disk_type: DiskType = DiskType.NVME
    capacity_gb: float = 0.0
    firmware: str = ""
    interface: str = ""
    temperature_c: float = 0.0
    temperature_history: List[TemperatureRecord] = field(default_factory=list)
    power_on_hours: int = 0
    power_cycle_count: int = 0
    total_lbas_written: int = 0
    total_lbas_read: int = 0
    smart_status: SMARTStatus = SMARTStatus.PASSED
    smart_attributes: List[SMARTAttribute] = field(default_factory=list)
    health_percent: float = 100.0
    wear_leveling: int = 0
    available_spare: int = 0
    percentage_used: int = 0
    media_errors: int = 0
    uncorrectable_errors: int = 0
    reallocated_sectors: int = 0
    pending_sectors: int = 0
    # Spec-API aliases/fields
    health_score: float = 0.0
    wear_level_pct: float = 0.0
    benchmark: Optional["BenchmarkResult"] = None

    def __post_init__(self):
        if not self.health_score:
            self.health_score = self.health_percent
        if not self.wear_level_pct:
            self.wear_level_pct = self.percentage_used

    @property
    def attributes(self) -> List[SMARTAttribute]:
        """Alias for smart_attributes (spec API)."""
        return self.smart_attributes

    @attributes.setter
    def attributes(self, value: List[SMARTAttribute]) -> None:
        self.smart_attributes = value

    @property
    def wear_bar(self) -> str:
        filled = int(max(0, min(100, self.wear_level_pct)) / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def lifespan_pct(self) -> float:
        return max(0.0, 100.0 - self.wear_level_pct)

    @property
    def health_status(self):
        """HealthStatus enum member (spec API)."""
        if self.health_percent > 90:
            return HealthStatus.EXCELLENT
        elif self.health_percent > 70:
            return HealthStatus.GOOD
        elif self.health_percent > 50:
            return HealthStatus.FAIR
        return HealthStatus.POOR

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 40:
            return "🟢 Cool"
        elif self.temperature_c < 55:
            return "🟡 Warm"
        elif self.temperature_c < 70:
            return "🟠 Hot"
        return "🔴 Critical"

    @property
    def health_bar(self) -> str:
        filled = int(self.health_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_bar(self) -> str:
        filled = int(self.temperature_c / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def tb_written(self) -> float:
        if self.disk_type == DiskType.NVME:
            return self.total_lbas_written * 512 / (1024 ** 4)
        return self.total_lbas_written * 512 / (1024 ** 4)

    @property
    def tb_read(self) -> float:
        return self.total_lbas_read * 512 / (1024 ** 4)

    @property
    def predicted_life_pct(self) -> float:
        return max(0, 100 - self.percentage_used)

    @property
    def failure_risk(self) -> str:
        if self.uncorrectable_errors > 10 or self.reallocated_sectors > 100:
            return "🔴 High Risk"
        elif self.uncorrectable_errors > 0 or self.reallocated_sectors > 10:
            return "🟡 Medium Risk"
        return "🟢 Low Risk"


@dataclass
class TemperatureAlert:
    device: str = ""
    temperature_c: float = 0.0
    threshold: float = 0.0
    timestamp: float = 0.0
    severity: str = "warning"

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def severity_icon(self) -> str:
        icons = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
        return icons.get(self.severity, "?")


class DiskHealthMonitor:
    def __init__(self):
        self.disks: List[DiskHealth] = []
        self.alerts: List[TemperatureAlert] = []
        self.monitoring_enabled: bool = True
        self.poll_interval_s: int = 30
        self.temp_warning_c: float = 55.0
        self.temp_critical_c: float = 70.0
        # Spec-API state
        self._view_mode: str = "overview"
        self._selected_disk: int = 0
        self._create_sample_data()
        # Attach sample benchmarks
        for disk in self.disks:
            disk.benchmark = BenchmarkResult(
                disk_name=disk.device,
                read_speed_mbps=3500.0 if disk.disk_type == DiskType.NVME else 550.0,
                write_speed_mbps=3000.0 if disk.disk_type == DiskType.NVME else 520.0,
                random_4k_iops=90000.0 if disk.disk_type == DiskType.NVME else 95000.0,
                latency_ms=0.03 if disk.disk_type == DiskType.NVME else 0.4,
            )

    def _create_sample_data(self):
        now = time.time()
        self.disks = [
            DiskHealth(device="/dev/nvme0n1", model="Samsung 990 Pro 2TB",
                        serial="S6BFNX0T123456", disk_type=DiskType.NVME,
                        capacity_gb=2000.0, firmware="5B2QGXD7", interface="PCIe 4.0 x4",
                        temperature_c=42, power_on_hours=3200,
                        power_cycle_count=850, total_lbas_written=26214400000,
                        total_lbas_read=18000000000, smart_status=SMARTStatus.PASSED,
                        health_percent=98, wear_leveling=98, available_spare=100,
                        percentage_used=2, media_errors=0, uncorrectable_errors=0),
            DiskHealth(device="/dev/sda", model="Samsung 870 EVO 1TB",
                        serial="S4EWNX0R654321", disk_type=DiskType.SATA_SSD,
                        capacity_gb=1000.0, firmware="SVT02B6Q", interface="SATA III",
                        temperature_c=35, power_on_hours=12000,
                        power_cycle_count=4500, total_lbas_written=98765432100,
                        total_lbas_read=65432100000, smart_status=SMARTStatus.PASSED,
                        health_percent=95, wear_leveling=95,
                        percentage_used=5, media_errors=0, uncorrectable_errors=0),
            DiskHealth(device="/dev/sdb", model="WD Red Plus 4TB",
                        serial="WD-CC4H3456", disk_type=DiskType.HDD,
                        capacity_gb=4000.0, firmware="82.00A82", interface="SATA III",
                        temperature_c=38, power_on_hours=25000,
                        power_cycle_count=12000, total_lbas_written=350000000000,
                        total_lbas_read=280000000000, smart_status=SMARTStatus.PASSED,
                        health_percent=92, percentage_used=8,
                        media_errors=2, uncorrectable_errors=0,
                        reallocated_sectors=15, pending_sectors=0),
        ]

        for disk in self.disks:
            for i in range(24):
                disk.temperature_history.append(TemperatureRecord(
                    timestamp=now - (24 - i) * 3600,
                    temperature_c=disk.temperature_c + random.uniform(-3, 3)))

            disk.smart_attributes = [
                SMARTAttribute(id=1, name="Raw Read Error Rate",
                                value=100, worst=100, threshold=6, raw_value=0),
                SMARTAttribute(id=5, name="Reallocated Sectors",
                                value=max(1, 100 - disk.reallocated_sectors),
                                worst=100, threshold=10, raw_value=disk.reallocated_sectors),
                SMARTAttribute(id=9, name="Power-On Hours",
                                value=95, worst=95, threshold=0,
                                raw_value=disk.power_on_hours),
                SMARTAttribute(id=12, name="Power Cycle Count",
                                value=98, worst=98, threshold=0,
                                raw_value=disk.power_cycle_count),
                SMARTAttribute(id=194, name="Temperature",
                                value=max(60, 100 - int(disk.temperature_c * 0.5)),
                                worst=60, threshold=0,
                                raw_value=disk.temperature_c),
                SMARTAttribute(id=197, name="Current Pending Sectors",
                                value=100, worst=100, threshold=0,
                                raw_value=disk.pending_sectors),
                SMARTAttribute(id=198, name="Offline Uncorrectable",
                                value=100, worst=100, threshold=0,
                                raw_value=disk.uncorrectable_errors),
            ]

        self.alerts = [
            TemperatureAlert(device="/dev/nvme0n1", temperature_c=55.2,
                              threshold=55.0, severity="warning"),
        ]

    def get_disk(self, device: str) -> Optional[DiskHealth]:
        return next((d for d in self.disks if d.device == device), None)

    def get_nvme_disks(self) -> List[DiskHealth]:
        return [d for d in self.disks if d.disk_type == DiskType.NVME]

    def get_ssd_disks(self) -> List[DiskHealth]:
        return [d for d in self.disks if d.disk_type == DiskType.SATA_SSD]

    def get_hdd_disks(self) -> List[DiskHealth]:
        return [d for d in self.disks if d.disk_type == DiskType.HDD]

    def get_failed_disks(self) -> List[DiskHealth]:
        return [d for d in self.disks if d.smart_status == SMARTStatus.FAILED]

    def get_high_risk_disks(self) -> List[DiskHealth]:
        return [d for d in self.disks if "High Risk" in d.failure_risk]

    def get_worst_disk(self) -> Optional[DiskHealth]:
        if not self.disks:
            return None
        return min(self.disks, key=lambda d: d.health_percent)

    def get_total_capacity(self) -> float:
        return sum(d.capacity_gb for d in self.disks)

    def get_total_written(self) -> float:
        return sum(d.tb_written for d in self.disks)

    def get_stats(self) -> Dict:
        return {
            "disks": len(self.disks),
            "total_capacity_gb": self.get_total_capacity(),
            "total_written_tb": round(self.get_total_written(), 2),
            "avg_health": round(sum(d.health_percent for d in self.disks) / len(self.disks), 1) if self.disks else 0,
            "alerts": len(self.alerts),
        }

    # ─── Spec API (test_network_disk_scheduler) ───────────────────
    @property
    def view_mode(self) -> str:
        return self._view_mode

    @view_mode.setter
    def view_mode(self, value: str) -> None:
        self._view_mode = value

    @property
    def selected_disk(self) -> int:
        return self._selected_disk

    @property
    def total_alerts(self) -> int:
        return len(self.alerts)

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def select_disk_down(self) -> int:
        if self._selected_disk < len(self.disks) - 1:
            self._selected_disk += 1
        return self._selected_disk

    def select_disk_up(self) -> int:
        if self._selected_disk > 0:
            self._selected_disk -= 1
        return self._selected_disk

    def handle_key(self, key: str) -> str:
        if key == "Enter":
            self._view_mode = "smart"
            return "smart"
        if key == "Escape":
            self._view_mode = "overview"
            return "overview"
        return ""

    def _selected(self) -> DiskHealth:
        if 0 <= self._selected_disk < len(self.disks):
            return self.disks[self._selected_disk]
        return self.disks[0] if self.disks else None

    def render_overview(self) -> List[str]:
        lines = ["DISK HEALTH — Overview", "=" * 50]
        for i, d in enumerate(self.disks):
            marker = ">" if i == self._selected_disk else " "
            lines.append(f" {marker}{d.device:<16} {d.model:<26} {d.health_bar} {d.health_percent}%")
        lines.append("")
        lines.append(f"  Capacity: {self.get_total_capacity() / 1000:.1f} TB  "
                     f"Alerts: {len(self.alerts)}")
        return lines

    def render_smart(self) -> List[str]:
        disk = self._selected()
        lines = [f"SMART — {disk.device} ({disk.model})", "=" * 50]
        for a in disk.attributes:
            lines.append(f"  {a.id:>4} {a.name:<32} {a.value:>4} {a.bar} {a.status}")
        return lines

    def render_benchmark(self) -> List[str]:
        disk = self._selected()
        lines = [f"BENCHMARK — {disk.device}", "=" * 50]
        bm = disk.benchmark
        if bm:
            lines.append(f"  Read:  {bm.read_speed_str}")
            lines.append(f"  Write: {bm.write_speed_str}")
            lines.append(f"  4K IOPS: {bm.random_4k_iops:.0f}  Latency: {bm.latency_ms:.2f} ms")
        else:
            lines.append("  No benchmark data")
        return lines

    def render_temperature(self) -> List[str]:
        disk = self._selected()
        lines = [f"TEMPERATURE — {disk.device}", "=" * 50]
        for rec in disk.temperature_history:
            ts = time.strftime("%m-%d %H:%M", time.localtime(rec.timestamp))
            lines.append(f"  {ts}  {rec.temperature_c:5.1f} °C")
        return lines

    def render_alerts(self) -> List[str]:
        lines = ["ALERTS", "=" * 50]
        if not self.alerts:
            lines.append("  No alerts")
        for a in self.alerts:
            lines.append(f"  {a.severity_icon} {a.device}: {a.temperature_c:.1f} °C "
                         f"(threshold {a.threshold:.0f})")
        return lines


@dataclass
class TemperatureReading:
    timestamp: float = 0.0
    temperature_c: float = 0.0
    disk: str = ""


@dataclass
class BenchmarkResult:
    disk_name: str = ""
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    random_4k_iops: float = 0.0
    latency_ms: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def read_speed_str(self) -> str:
        if self.read_speed_mbps >= 1000:
            return f"{self.read_speed_mbps / 1000:.1f} GB/s"
        return f"{self.read_speed_mbps:.0f} MB/s"

    @property
    def write_speed_str(self) -> str:
        if self.write_speed_mbps >= 1000:
            return f"{self.write_speed_mbps / 1000:.1f} GB/s"
        return f"{self.write_speed_mbps:.0f} MB/s"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

# ─── Backward-compat exports ────────────────────────────────────────────
from enum import Enum as _Enum
from dataclasses import dataclass as _dataclass
from typing import Optional as _Optional

class DiskAlertLevel(_Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

@_dataclass
class DiskAlert:
    disk: str = ""
    level: DiskAlertLevel = DiskAlertLevel.INFO
    message: str = ""
    timestamp: float = 0.0
    resolved: bool = False


from enum import Enum as _HealthStatus
class HealthStatus(str, _HealthStatus):
    """Health status; str-valued so emoji containment checks work."""
    EXCELLENT = "🟢 Excellent"
    GOOD = "🟡 Good"
    FAIR = "🟠 Fair"
    POOR = "🔴 Poor"
    HEALTHY = "🟢 healthy"
    WARNING = "🟡 warning"
    CRITICAL = "🟠 critical"
    FAILED = "🔴 failed"
    UNKNOWN = "❓ unknown"
    DEGRADED = "🟠 degraded"
