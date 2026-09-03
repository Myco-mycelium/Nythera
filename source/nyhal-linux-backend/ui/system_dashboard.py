from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class DashboardWidget(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    GPU = "gpu"
    DISK = "disk"
    NETWORK = "network"
    PROCESSES = "processes"
    TEMPERATURE = "temperature"
    POWER = "power"


class ExportFormat(Enum):
    HTML = "html"
    PDF = "pdf"
    JSON = "json"
    CSV = "csv"
    PNG = "png"


@dataclass
class MetricSnapshot:
    name: str
    value: float
    unit: str
    timestamp: float
    min_val: float = 0
    max_val: float = 100

    @property
    def display(self) -> str:
        if self.value >= 1_000_000:
            return f"{self.value / 1_000_000:.2f}M {self.unit}"
        if self.value >= 1_000:
            return f"{self.value / 1_000:.1f}K {self.unit}"
        return f"{self.value:.1f} {self.unit}"

    @property
    def bar(self) -> str:
        pct = min(self.value / max(self.max_val, 1), 1.0)
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def sparkline(self) -> str:
        # Generate from value
        chars = " ▁▂▃▄▅▆▇█"
        result = ""
        for i in range(16):
            val = math.sin(i * 0.5 + self.value * 0.01) * 0.4 + 0.5
            result += chars[min(int(val * 8), 8)]
        return result


@dataclass
class DashboardWidgetConfig:
    widget: DashboardWidget
    enabled: bool = True
    position: tuple = (0, 0)
    size: tuple = (200, 150)
    refresh_ms: int = 1000


@dataclass
class ProcessInfo:
    pid: int
    name: str
    cpu: float
    memory_mb: float
    status: str


class SystemDashboard:
    def __init__(self):
        self._widgets: list[DashboardWidgetConfig] = []
        self._metrics: dict[str, MetricSnapshot] = {}
        self._processes: list[ProcessInfo] = []
        self._selected_widget: int = 0
        self._theme: str = "dark"
        self._refresh_rate: int = 1000
        self._is_fullscreen: bool = False
        self._generated_exports: list = []
        self._view: str = "dashboard"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._widgets = [
            DashboardWidgetConfig(DashboardWidget.CPU),
            DashboardWidgetConfig(DashboardWidget.MEMORY),
            DashboardWidgetConfig(DashboardWidget.GPU),
            DashboardWidgetConfig(DashboardWidget.DISK),
            DashboardWidgetConfig(DashboardWidget.NETWORK),
            DashboardWidgetConfig(DashboardWidget.PROCESSES),
            DashboardWidgetConfig(DashboardWidget.TEMPERATURE),
            DashboardWidgetConfig(DashboardWidget.POWER),
        ]

        self._metrics = {
            "cpu_usage": MetricSnapshot("CPU Usage", 35.2, "%", now, 0, 100),
            "cpu_temp": MetricSnapshot("CPU Temp", 62.0, "°C", now, 30, 100),
            "mem_used": MetricSnapshot("Memory Used", 38700, "MB", now, 0, 65536),
            "gpu_usage": MetricSnapshot("GPU Usage", 45.2, "%", now, 0, 100),
            "gpu_temp": MetricSnapshot("GPU Temp", 62.0, "°C", now, 30, 100),
            "disk_read": MetricSnapshot("Disk Read", 125.4, "MB/s", now, 0, 500),
            "disk_write": MetricSnapshot("Disk Write", 89.2, "MB/s", now, 0, 500),
            "net_rx": MetricSnapshot("Network RX", 850.0, "Mbps", now, 0, 1000),
            "net_tx": MetricSnapshot("Network TX", 120.0, "Mbps", now, 0, 500),
            "power_total": MetricSnapshot("Total Power", 285.0, "W", now, 0, 500),
        }

        self._processes = [
            ProcessInfo(456, "nyrqis-compositor", 35.2, 1536, "running"),
            ProcessInfo(789, "firefox", 28.5, 3276, "running"),
            ProcessInfo(1011, "code", 22.1, 2867, "running"),
            ProcessInfo(1234, "dockerd", 8.3, 256, "sleeping"),
            ProcessInfo(1567, "rustc", 65.0, 1024, "running"),
        ]

        self._generated_exports = [
            {"format": "html", "name": "dashboard-2026-09-03.html", "size": "125 KB"},
            {"format": "json", "name": "metrics-2026-09-03.json", "size": "18 KB"},
        ]

    @property
    def selected_widget(self) -> Optional[DashboardWidgetConfig]:
        if 0 <= self._selected_widget < len(self._widgets):
            return self._widgets[self._selected_widget]
        return None

    @property
    def enabled_widgets(self) -> int:
        return sum(1 for w in self._widgets if w.enabled)

    def select_widget(self, idx: int):
        if 0 <= idx < len(self._widgets):
            self._selected_widget = idx

    def toggle_widget(self, idx: int):
        if 0 <= idx < len(self._widgets):
            self._widgets[idx].enabled = not self._widgets[idx].enabled

    def export_dashboard(self, fmt: ExportFormat) -> str:
        name = f"dashboard-{time.strftime('%Y-%m-%d')}.{fmt.value}"
        self._generated_exports.append({"format": fmt.value, "name": name, "size": "exported"})
        return name

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM DASHBOARD                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Theme: {self._theme}  Refresh: {self._refresh_rate}ms  Widgets: {self.enabled_widgets}/{len(self._widgets)}  Fullscreen: {'ON' if self._is_fullscreen else 'OFF'}")
        lines.append("")
        lines.append("  ── System Metrics ──")
        for name, m in self._metrics.items():
            lines.append(f"  {m.name:<16s} [{m.bar}] {m.display:>15s}  {m.sparkline}")
        lines.append("")
        lines.append("  ── Top Processes ──")
        for p in self._processes[:5]:
            cpu_bar = "█" * int(p.cpu / 5) + "░" * (20 - int(p.cpu / 5))
            mem = f"{p.memory_mb:.0f}MB" if p.memory_mb < 1024 else f"{p.memory_mb/1024:.1f}GB"
            lines.append(f"  {p.pid:<6d} {p.name:<20s} CPU:{cpu_bar} {p.cpu:5.1f}%  MEM:{mem:>8s}")
        lines.append("")
        lines.append("  ── Widgets ──")
        for i, w in enumerate(self._widgets):
            sel = "▶" if i == self._selected_widget else " "
            status = "🟢" if w.enabled else "⚪"
            lines.append(f"  {sel}{status} {w.widget.value}")
        lines.append("")
        lines.append("  ── Exports ──")
        for e in self._generated_exports[-3:]:
            lines.append(f"  📄 {e['name']}  {e['format'].upper()}  {e['size']}")
        lines.append("")
        lines.append("  [W]idget  [T]heme  [R]efresh  [E]xport  [F]ullscreen  [S]ave layout  [L]oad")
        return lines

    def render_widget_detail(self) -> list:
        w = self.selected_widget
        if not w:
            return ["  No widget selected"]
        lines = []
        lines.append(f"  ── {w.widget.value.upper()} Widget ──")
        lines.append(f"  Enabled: {'Yes' if w.enabled else 'No'}")
        lines.append(f"  Position: {w.position}")
        lines.append(f"  Size: {w.size}")
        lines.append(f"  Refresh: {w.refresh_ms}ms")
        return lines
