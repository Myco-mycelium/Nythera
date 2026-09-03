"""
Nyrqis Disk Health — disk health monitoring dashboard.

Features:
- S.M.A.R.T. attribute monitoring with alerts
- Temperature tracking with history
- Performance benchmarks (sequential/random read/write)
- Disk health score and prediction
- Wear level monitoring (SSD NAND cycles)
- Bad sector tracking
- Temperature threshold alerts
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class HealthStatus(Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    CRITICAL = "critical"
    FAILING = "failing"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class DiskType(Enum):
    NVME = "NVMe SSD"
    SATA_SSD = "SATA SSD"
    HDD = "HDD"
    USB = "USB Flash"


HEALTH_ICONS = {
    HealthStatus.EXCELLENT: "🟢",
    HealthStatus.GOOD: "🔵",
    HealthStatus.FAIR: "🟡",
    HealthStatus.POOR: "🟠",
    HealthStatus.CRITICAL: "🔴",
    HealthStatus.FAILING: "💀",
}

ALERT_ICONS = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.CRITICAL: "❌",
    AlertSeverity.EMERGENCY: "🔥",
}


@dataclass
class SMARTAttribute:
    """A S.M.A.R.T. attribute."""
    attribute_id: int
    name: str
    value: int  # normalized 1-253
    worst: int
    threshold: int
    raw_value: int = 0
    unit: str = ""
    is_failed: bool = False

    @property
    def status(self) -> str:
        if self.is_failed:
            return "FAIL"
        if self.value <= self.threshold:
            return "WARN"
        if self.value < self.worst * 0.8:
            return "AGE"
        return "OK"

    @property
    def status_icon(self) -> str:
        s = self.status
        if s == "OK":
            return "✅"
        elif s == "AGE":
            return "⚠️"
        elif s == "WARN":
            return "⚠️"
        return "❌"

    @property
    def bar(self) -> str:
        filled = int(self.value / 253 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def display_value(self) -> str:
        if self.unit:
            return f"{self.raw_value} {self.unit}"
        return str(self.raw_value)


@dataclass
class TemperatureReading:
    """A temperature reading."""
    timestamp: float
    temperature_c: int
    power_on_hours: int = 0

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M")

    @property
    def temp_bar(self) -> str:
        # Scale: 0-100°C mapped to 20 chars
        filled = min(20, int(self.temperature_c / 100 * 20))
        if self.temperature_c < 40:
            return "█" * filled + "░" * (20 - filled)  # green area
        elif self.temperature_c < 60:
            return "█" * filled + "░" * (20 - filled)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class BenchmarkResult:
    """A disk benchmark result."""
    test_name: str
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    iops_read: int = 0
    iops_write: int = 0
    latency_read_us: float = 0.0
    latency_write_us: float = 0.0
    test_size_mb: int = 1024
    duration_seconds: float = 0.0

    @property
    def read_speed_str(self) -> str:
        if self.read_speed_mbps >= 1000:
            return f"{self.read_speed_mbps / 1000:.1f} GB/s"
        return f"{self.read_speed_mbps:.1f} MB/s"

    @property
    def write_speed_str(self) -> str:
        if self.write_speed_mbps >= 1000:
            return f"{self.write_speed_mbps / 1000:.1f} GB/s"
        return f"{self.write_speed_mbps:.1f} MB/s"


@dataclass
class DiskAlert:
    """A disk health alert."""
    severity: AlertSeverity
    message: str
    attribute: str = ""
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False

    @property
    def icon(self) -> str:
        return ALERT_ICONS.get(self.severity, "❓")

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


@dataclass
class DiskHealth:
    """Complete health information for a disk."""
    device: str
    model: str
    serial: str
    disk_type: DiskType
    capacity_gb: int
    firmware: str = ""
    interface: str = ""
    # Health
    health_score: int = 100  # 0-100
    health_status: HealthStatus = HealthStatus.EXCELLENT
    temperature_c: int = 35
    power_on_hours: int = 0
    power_cycles: int = 0
    # Wear
    wear_level_pct: int = 0  # SSD
    spare_blocks: int = 0
    reallocated_sectors: int = 0
    pending_sectors: int = 0
    uncorrectable_errors: int = 0
    # Data
    total_lbas_written: int = 0
    total_lbas_read: int = 0
    # Attributes
    attributes: List[SMARTAttribute] = field(default_factory=list)
    temperature_history: List[TemperatureReading] = field(default_factory=list)
    benchmark: Optional[BenchmarkResult] = None
    # Alerts
    alerts: List[DiskAlert] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    @property
    def health_icon(self) -> str:
        return HEALTH_ICONS.get(self.health_status, "❓")

    @property
    def health_bar(self) -> str:
        filled = int(self.health_score / 100 * 30)
        return "█" * filled + "░" * (30 - filled)

    @property
    def temperature_status(self) -> str:
        if self.temperature_c < 35:
            return "Cool"
        elif self.temperature_c < 45:
            return "Normal"
        elif self.temperature_c < 55:
            return "Warm"
        elif self.temperature_c < 65:
            return "Hot"
        return "Critical"

    @property
    def wear_bar(self) -> str:
        filled = int(self.wear_level_pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def written_str(self) -> str:
        tb = self.total_lbas_written * 512 / (1024 ** 4)
        return f"{tb:.1f} TB"

    @property
    def read_str(self) -> str:
        tb = self.total_lbas_read * 512 / (1024 ** 4)
        return f"{tb:.1f} TB"

    @property
    def lifespan_pct(self) -> float:
        """Estimated remaining lifespan."""
        if self.disk_type in (DiskType.NVME, DiskType.SATA_SSD):
            return max(0, 100 - self.wear_level_pct)
        return 100.0


# ─── Disk Health Dashboard ───────────────────────────────────────────────


class DiskHealthMonitor:
    """
    Disk health monitoring dashboard for Nyrqis OS.
    """

    def __init__(self):
        self._disks: List[DiskHealth] = []
        self._selected_disk: int = 0
        self._selected_attr: int = 0
        self._view_mode: str = "overview"  # overview, smart, benchmark, alerts, temperature

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()

        # NVMe SSD
        nvme_attrs = [
            SMARTAttribute(1, "Read Error Rate", 200, 200, 0, 0),
            SMARTAttribute(5, "Reallocated Sectors", 100, 100, 0, 0),
            SMARTAttribute(9, "Power-On Hours", 99, 99, 0, 2150, "hours"),
            SMARTAttribute(12, "Power Cycle Count", 98, 98, 0, 456),
            SMARTAttribute(173, "Wear Leveling Count", 95, 95, 0, 5, "%"),
            SMARTAttribute(175, "Program Fail Count", 100, 100, 0, 0),
            SMARTAttribute(176, "Erase Fail Count", 100, 100, 0, 0),
            SMARTAttribute(177, "Wear Range Delta", 99, 99, 0, 2),
            SMARTAttribute(194, "Temperature", 95, 85, 0, 38, "°C"),
            SMARTAttribute(199, "U_CRC_Error_Count", 100, 100, 0, 0),
            SMARTAttribute(241, "Total_LBAs_Written", 98, 98, 0, 15_000_000_000),
            SMARTAttribute(242, "Total_LBAs_Read", 98, 98, 0, 8_000_000_000),
        ]
        nvme_temp_history = [TemperatureReading(now - i * 3600, random.randint(33, 42)) for i in range(24)]

        nvme = DiskHealth(
            "/dev/nvme0n1", "Samsung 990 PRO 1TB", "S5JYNS0T123456",
            DiskType.NVME, 1000, "5B2QGXA7", "PCIe 4.0 x4",
            health_score=98, health_status=HealthStatus.EXCELLENT,
            temperature_c=38, power_on_hours=2150, power_cycles=456,
            wear_level_pct=5, spare_blocks=100,
            total_lbas_written=15_000_000_000, total_lbas_read=8_000_000_000,
            attributes=nvme_attrs, temperature_history=nvme_temp_history,
            benchmark=BenchmarkResult(
                "Sequential", 7100.0, 6900.0, 1_200_000, 1_100_000,
                15.2, 8.5, 10240, 10.0),
        )

        # SATA SSD
        sata_attrs = [
            SMARTAttribute(1, "Read Error Rate", 200, 200, 0, 0),
            SMARTAttribute(5, "Reallocated Sectors", 100, 100, 0, 2),
            SMARTAttribute(9, "Power-On Hours", 95, 95, 0, 8760, "hours"),
            SMARTAttribute(12, "Power Cycle Count", 96, 96, 0, 1234),
            SMARTAttribute(173, "Wear Leveling Count", 88, 88, 0, 12, "%"),
            SMARTAttribute(177, "Wear Range Delta", 92, 92, 0, 8),
            SMARTAttribute(194, "Temperature", 92, 82, 0, 32, "°C"),
            SMARTAttribute(199, "U_CRC_Error_Count", 100, 100, 0, 0),
            SMARTAttribute(241, "Total_LBAs_Written", 92, 92, 0, 25_000_000_000),
            SMARTAttribute(242, "Total_LBAs_Read", 94, 94, 0, 12_000_000_000),
        ]
        sata_temp_history = [TemperatureReading(now - i * 3600, random.randint(28, 38)) for i in range(24)]

        sata = DiskHealth(
            "/dev/sda", "WD Red Plus 2TB", "WD-WMC4T0123456",
            DiskType.SATA_SSD, 2000, "0320", "SATA III",
            health_score=92, health_status=HealthStatus.GOOD,
            temperature_c=32, power_on_hours=8760, power_cycles=1234,
            wear_level_pct=12, spare_blocks=98,
            reallocated_sectors=2, total_lbas_written=25_000_000_000,
            total_lbas_read=12_000_000_000,
            attributes=sata_attrs, temperature_history=sata_temp_history,
            benchmark=BenchmarkResult(
                "Sequential", 560.0, 530.0, 95_000, 85_000,
                45.0, 35.0, 4096, 10.0),
        )

        # HDD
        hdd_attrs = [
            SMARTAttribute(1, "Read Error Rate", 180, 180, 0, 12),
            SMARTAttribute(5, "Reallocated Sectors", 90, 85, 10, 48),
            SMARTAttribute(9, "Power-On Hours", 88, 88, 0, 17520, "hours"),
            SMARTAttribute(12, "Power Cycle Count", 94, 94, 0, 2345),
            SMARTAttribute(187, "Reported Uncorrectable Errors", 98, 98, 0, 3),
            SMARTAttribute(188, "Command Timeout", 99, 99, 0, 0),
            SMARTAttribute(194, "Temperature", 88, 75, 0, 42, "°C"),
            SMARTAttribute(197, "Current Pending Sectors", 96, 96, 0, 4),
            SMARTAttribute(198, "Offline Uncorrectable", 96, 96, 0, 2),
        ]
        hdd_temp_history = [TemperatureReading(now - i * 3600, random.randint(38, 48)) for i in range(24)]

        hdd = DiskHealth(
            "/dev/sdb", "Seagate Barracuda 4TB", "ZA500CM10002",
            DiskType.HDD, 4000, "0001", "SATA III",
            health_score=78, health_status=HealthStatus.FAIR,
            temperature_c=42, power_on_hours=17520, power_cycles=2345,
            wear_level_pct=0, reallocated_sectors=48,
            pending_sectors=4, uncorrectable_errors=3,
            total_lbas_written=40_000_000_000,
            total_lbas_read=35_000_000_000,
            attributes=hdd_attrs, temperature_history=hdd_temp_history,
            benchmark=BenchmarkResult(
                "Sequential", 195.0, 185.0, 120, 115,
                5200.0, 4800.0, 4096, 30.0),
        )

        self._disks = [nvme, sata, hdd]

        # Generate alerts for HDD
        hdd.alerts = [
            DiskAlert(AlertSeverity.WARNING, "48 reallocated sectors detected",
                      "Reallocated Sectors", now - 86400),
            DiskAlert(AlertSeverity.WARNING, "4 pending sectors",
                      "Current Pending Sectors", now - 7200),
            DiskAlert(AlertSeverity.INFO, "Temperature 42°C — above average",
                      "Temperature", now - 3600),
            DiskAlert(AlertSeverity.CRITICAL, "3 uncorrectable errors logged",
                      "Uncorrectable Errors", now - 172800),
        ]

    # ── Navigation ────────────────────────────────────────────────────

    def select_disk_up(self) -> None:
        self._selected_disk = max(0, self._selected_disk - 1)

    def select_disk_down(self) -> None:
        self._selected_disk = min(len(self._disks) - 1, self._selected_disk + 1)

    def select_attr_up(self) -> None:
        self._selected_attr = max(0, self._selected_attr - 1)

    def select_attr_down(self) -> None:
        disk = self.get_selected_disk()
        if disk:
            self._selected_attr = min(len(disk.attributes) - 1, self._selected_attr + 1)

    def get_selected_disk(self) -> Optional[DiskHealth]:
        if 0 <= self._selected_disk < len(self._disks):
            return self._disks[self._selected_disk]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_attr = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def disks(self) -> List[DiskHealth]:
        return list(self._disks)

    @property
    def selected_disk(self) -> int:
        return self._selected_disk

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def total_alerts(self) -> int:
        return sum(len(d.alerts) for d in self._disks)

    @property
    def critical_alerts(self) -> int:
        return sum(1 for d in self._disks for a in d.alerts
                   if a.severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY))

    # ── Rendering ─────────────────────────────────────────────────────

    def render_overview(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 💾 Disk Health Dashboard")
        lines.append("─" * width)

        alerts_str = ""
        if self.total_alerts > 0:
            alerts_str = f" | ⚠️ {self.total_alerts} alerts"
        lines.append(f" {len(self._disks)} disks monitored{alerts_str}")
        lines.append("─" * width)

        for i, disk in enumerate(self._disks):
            marker = "▸" if i == self._selected_disk else " "
            lines.append(f"{marker} {disk.health_icon} {disk.model}")
            lines.append(f"   {disk.device} | {disk.disk_type.value} | {disk.capacity_gb} GB | {disk.interface}")

            # Health bar
            lines.append(f"   Health: [{disk.health_bar}] {disk.health_score}/100 ({disk.health_status.value})")

            # Temperature
            temp_icon = "🟢" if disk.temperature_c < 40 else "🟡" if disk.temperature_c < 55 else "🔴"
            lines.append(f"   Temp: {temp_icon} {disk.temperature_c}°C ({disk.temperature_status}) | Power: {disk.power_on_hours:,}h")

            # SSD wear
            if disk.disk_type in (DiskType.NVME, DiskType.SATA_SSD):
                lines.append(f"   Wear: [{disk.wear_bar}] {disk.wear_level_pct}% | Remaining: {disk.lifespan_pct:.0f}%")

            # HDD sectors
            if disk.reallocated_sectors > 0 or disk.pending_sectors > 0:
                lines.append(f"   ⚠️ Reallocated: {disk.reallocated_sectors} | Pending: {disk.pending_sectors} | Errors: {disk.uncorrectable_errors}")

            # Alerts
            if disk.alerts:
                recent = disk.alerts[0]
                lines.append(f"   {recent.icon} {recent.message}")

            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:S.M.A.R.T.  B:Benchmark  T:Temp  A:Alerts")
        return lines

    def render_smart(self, width: int = 70) -> List[str]:
        disk = self.get_selected_disk()
        if not disk:
            return ["No disk selected"]

        lines = []
        lines.append(f" 📊 S.M.A.R.T. — {disk.model}")
        lines.append("─" * width)
        lines.append(f" Device: {disk.device} | Firmware: {disk.firmware}")
        lines.append(f" Health: [{disk.health_bar}] {disk.health_score}/100")
        lines.append("─" * width)

        # Headers
        lines.append(f" {'ID':>3s}  {'Attribute':<28s} {'Value':>6s} {'Worst':>6s} {'Raw':>14s}  Status")
        lines.append("─" * width)

        for i, attr in enumerate(disk.attributes):
            marker = "▸" if i == self._selected_attr else " "
            lines.append(f"{marker}{attr.attribute_id:>3d}  {attr.name:<28s} {attr.value:>5d}  {attr.worst:>5d}  {attr.display_value:>14s}  {attr.status_icon}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_benchmark(self, width: int = 70) -> List[str]:
        disk = self.get_selected_disk()
        if not disk:
            return ["No disk selected"]

        lines = []
        lines.append(f" ⚡ Benchmark — {disk.model}")
        lines.append("─" * width)

        if disk.benchmark:
            bm = disk.benchmark
            lines.append(f" Test: {bm.test_name} ({bm.test_size_mb} MB)")
            lines.append(f" Duration: {bm.duration_seconds:.1f}s")
            lines.append("")
            lines.append(f" Read:")
            lines.append(f"   Speed: {bm.read_speed_str}")
            lines.append(f"   IOPS:  {bm.iops_read:,}")
            lines.append(f"   Latency: {bm.latency_read_us:.1f} µs")
            lines.append("")
            lines.append(f" Write:")
            lines.append(f"   Speed: {bm.write_speed_str}")
            lines.append(f"   IOPS:  {bm.iops_write:,}")
            lines.append(f"   Latency: {bm.latency_write_us:.1f} µs")
        else:
            lines.append("  No benchmark data available.")

        # Data volume
        lines.append("")
        lines.append(f" 📊 Data Volume:")
        lines.append(f"   Written: {disk.written_str}")
        lines.append(f"   Read:    {disk.read_str}")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_temperature(self, width: int = 70) -> List[str]:
        disk = self.get_selected_disk()
        if not disk:
            return ["No disk selected"]

        lines = []
        lines.append(f" 🌡️  Temperature History — {disk.model}")
        lines.append("─" * width)
        lines.append(f" Current: {disk.temperature_c}°C ({disk.temperature_status})")
        lines.append("─" * width)

        # Temperature chart (ASCII)
        history = disk.temperature_history[-24:]
        if history:
            # Find min/max
            temps = [h.temperature_c for h in history]
            min_t = min(temps)
            max_t = max(temps)
            avg_t = sum(temps) / len(temps)

            lines.append(f" Min: {min_t}°C | Max: {max_t}°C | Avg: {avg_t:.1f}°C")
            lines.append("")

            # Simple ASCII chart
            chart_height = 10
            for row in range(chart_height, -1, -1):
                threshold = min_t + (max_t - min_t) * row / chart_height
                line = f" {threshold:>4.0f}°C │"
                for h in history:
                    if h.temperature_c >= threshold:
                        line += "█ "
                    else:
                        line += "  "
                lines.append(line)

            # Time axis
            lines.append(f"       └{'──' * len(history)}")
            time_labels = f"        {history[0].time_str}"
            lines.append(time_labels)

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_alerts(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🚨 Disk Alerts ({self.total_alerts})")
        lines.append("─" * width)

        for disk in self._disks:
            if disk.alerts:
                lines.append(f" 💽 {disk.model} ({disk.device})")
                for alert in disk.alerts:
                    ack = " ✓" if alert.acknowledged else ""
                    lines.append(f"   {alert.icon} [{alert.severity.value.upper()}] {alert.message}{ack}")
                    lines.append(f"     {alert.time_ago} | Attribute: {alert.attribute}")
                lines.append("")

        if not any(d.alerts for d in self._disks):
            lines.append("  No alerts! All disks healthy. 🎉")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "smart": self.render_smart,
            "benchmark": self.render_benchmark,
            "temperature": self.render_temperature,
            "alerts": self.render_alerts,
        }
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "smart":
            return self._handle_smart_key(key)
        elif self._view_mode == "benchmark":
            return self._handle_benchmark_key(key)
        elif self._view_mode == "temperature":
            return self._handle_temperature_key(key)
        elif self._view_mode == "alerts":
            return self._handle_alerts_key(key)
        return self._handle_overview_key(key)

    def _handle_overview_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_disk_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_disk_down()
            return "select_down"
        elif key == "Enter":
            self.set_view("smart")
            return "smart"
        elif key == "b":
            self.set_view("benchmark")
            return "benchmark"
        elif key == "t":
            self.set_view("temperature")
            return "temperature"
        elif key == "a":
            self.set_view("alerts")
            return "alerts"
        return None

    def _handle_smart_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_attr_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_attr_down()
            return "select_down"
        return None

    def _handle_benchmark_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        return None

    def _handle_temperature_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        return None

    def _handle_alerts_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        return None
