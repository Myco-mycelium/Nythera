from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class ProfilerView(Enum):
    OVERVIEW = "overview"
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    TEMPERATURE = "temperature"
    ALERTS = "alerts"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class GraphStyle(Enum):
    LINE = "line"
    BAR = "bar"
    SPARKLINE = "sparkline"
    HEATMAP = "heatmap"


@dataclass
class LiveDataPoint:
    timestamp: float
    value: float
    label: str = ""


@dataclass
class HardwareMetric:
    name: str
    current: float
    min_val: float
    max_val: float
    avg: float
    unit: str
    history: list = field(default_factory=list)
    threshold_warn: float = 0
    threshold_crit: float = 0

    @property
    def current_display(self) -> str:
        if self.current >= 1_000_000:
            return f"{self.current / 1_000_000:.2f}M {self.unit}"
        if self.current >= 1_000:
            return f"{self.current / 1_000:.1f}K {self.unit}"
        return f"{self.current:.1f} {self.unit}"

    @property
    def bar(self) -> str:
        max_display = max(self.max_val, 1)
        pct = min(self.current / max_display, 1.0)
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def sparkline(self) -> str:
        if not self.history:
            return "░" * 32
        recent = self.history[-32:]
        max_val = max(p.value for p in recent) if recent else 1
        if max_val == 0:
            return "░" * 32
        chars = " ▁▂▃▄▅▆▇█"
        return "".join(chars[min(int(p.value / max_val * 8), 8)] for p in recent)

    @property
    def status(self) -> str:
        if self.threshold_crit > 0 and self.current >= self.threshold_crit:
            return "🔴"
        if self.threshold_warn > 0 and self.current >= self.threshold_warn:
            return "🟡"
        return "🟢"


@dataclass
class ProfilerAlert:
    severity: AlertSeverity
    title: str
    message: str
    timestamp: float
    source: str
    acknowledged: bool = False

    @property
    def icon(self) -> str:
        icons = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
        return icons.get(self.severity.value, "?")


class LiveProfiler:
    def __init__(self):
        self._view: ProfilerView = ProfilerView.OVERVIEW
        self._metrics: dict[str, HardwareMetric] = {}
        self._alerts: list[ProfilerAlert] = []
        self._selected_metric: str = ""
        self._refresh_rate: int = 1
        self._graph_style: GraphStyle = GraphStyle.SPARKLINE
        self._is_running: bool = False
        self._start_time: float = 0
        self._uptime_secs: int = 86400 * 3 + 3600 * 7
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        def make_history(base, variance, n=60):
            return [LiveDataPoint(now - (n - i), base + math.sin(i * 0.3) * variance) for i in range(n)]

        self._metrics = {
            "cpu_usage": HardwareMetric("CPU Usage", 35.2, 5, 100, 32.5, "%", make_history(35, 15), 75, 90),
            "cpu_temp": HardwareMetric("CPU Temperature", 62.0, 35, 95, 58.3, "°C", make_history(62, 8), 75, 90),
            "cpu_freq": HardwareMetric("CPU Frequency", 4850, 3600, 5700, 4650, "MHz", make_history(4850, 500)),
            "gpu_usage": HardwareMetric("GPU Usage", 45.2, 0, 100, 38.7, "%", make_history(45, 20), 85, 95),
            "gpu_temp": HardwareMetric("GPU Temperature", 62.0, 30, 95, 58.1, "°C", make_history(62, 6), 80, 90),
            "gpu_vram": HardwareMetric("GPU VRAM", 7680, 0, 12288, 7200, "MB", make_history(7680, 500)),
            "gpu_power": HardwareMetric("GPU Power", 120.0, 30, 200, 105.0, "W", make_history(120, 25)),
            "mem_used": HardwareMetric("Memory Used", 38700, 16000, 64000, 36500, "MB", make_history(38700, 2000), 48000, 58000),
            "mem_cached": HardwareMetric("Memory Cached", 12400, 4000, 20000, 11800, "MB", make_history(12400, 1000)),
            "swap_used": HardwareMetric("Swap Used", 500, 0, 8192, 450, "MB", make_history(500, 200), 4096, 6144),
            "disk_read": HardwareMetric("Disk Read", 125.4, 0, 500, 98.2, "MB/s", make_history(125, 40)),
            "disk_write": HardwareMetric("Disk Write", 89.2, 0, 500, 75.1, "MB/s", make_history(89, 30)),
            "net_rx": HardwareMetric("Network RX", 850.0, 0, 1000, 720.0, "Mbps", make_history(850, 150)),
            "net_tx": HardwareMetric("Network TX", 120.0, 0, 500, 95.0, "Mbps", make_history(120, 50)),
            "fan_cpu": HardwareMetric("CPU Fan", 1800, 800, 3000, 1650, "RPM", make_history(1800, 200)),
            "fan_gpu": HardwareMetric("GPU Fan", 1400, 800, 3500, 1350, "RPM", make_history(1400, 150)),
        }

        self._alerts = [
            ProfilerAlert(AlertSeverity.WARNING, "High CPU Temperature", "CPU temperature reached 62°C", now - 300, "cpu_temp"),
            ProfilerAlert(AlertSeverity.INFO, "GPU Memory High", "GPU VRAM usage at 63%", now - 600, "gpu_vram"),
            ProfilerAlert(AlertSeverity.WARNING, "Memory Pressure", "System using 60% of 64GB RAM", now - 900, "mem_used"),
        ]

    @property
    def uptime_display(self) -> str:
        d, rem = divmod(self._uptime_secs, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        return f"{d}d {h}h {m}m"

    @property
    def unacked_alerts(self) -> int:
        return sum(1 for a in self._alerts if not a.acknowledged)

    def select_metric(self, name: str):
        self._selected_metric = name

    def acknowledge_alert(self, idx: int):
        if 0 <= idx < len(self._alerts):
            self._alerts[idx].acknowledged = True

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS LIVE PROFILER                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        status = "🟢 RUNNING" if self._is_running else "⏹ STOPPED"
        lines.append(f"  Status: {status}  Uptime: {self.uptime_display}  Refresh: {self._refresh_rate}s  Graph: {self._graph_style.value}")
        lines.append(f"  Alerts: {self.unacked_alerts} unacknowledged  Metrics: {len(self._metrics)}")
        lines.append("")
        lines.append(f"  ── {self._view.value.upper()} ──")
        lines.append("")
        cpu = self._metrics.get("cpu_usage")
        gpu = self._metrics.get("gpu_usage")
        mem = self._metrics.get("mem_used")
        if cpu:
            lines.append(f"  CPU  {cpu.status} [{cpu.bar}] {cpu.current_display}  {cpu.sparkline}")
        if gpu:
            lines.append(f"  GPU  {gpu.status} [{gpu.bar}] {gpu.current_display}  {gpu.sparkline}")
        if mem:
            lines.append(f"  MEM  {mem.status} [{mem.bar}] {mem.current_display}  {mem.sparkline}")
        lines.append("")
        lines.append("  ── Temperatures ──")
        for name in ["cpu_temp", "gpu_temp"]:
            m = self._metrics.get(name)
            if m:
                lines.append(f"  {m.name:<20s} {m.status} [{m.bar}] {m.current_display}  {m.sparkline}")
        lines.append("")
        lines.append("  ── Fans ──")
        for name in ["fan_cpu", "fan_gpu"]:
            m = self._metrics.get(name)
            if m:
                lines.append(f"  {m.name:<20s} [{m.bar}] {m.current_display}  {m.sparkline}")
        lines.append("")
        lines.append("  ── Disk I/O ──")
        for name in ["disk_read", "disk_write"]:
            m = self._metrics.get(name)
            if m:
                lines.append(f"  {m.name:<20s} [{m.bar}] {m.current_display}  {m.sparkline}")
        lines.append("")
        lines.append("  ── Network ──")
        for name in ["net_rx", "net_tx"]:
            m = self._metrics.get(name)
            if m:
                lines.append(f"  {m.name:<20s} [{m.bar}] {m.current_display}  {m.sparkline}")
        lines.append("")
        lines.append("  [V]iew  [R]efresh  [G]raph style  [A]lerts  [E]xport  [S]tart/Stop")
        return lines

    def render_metric_detail(self, name: str) -> list:
        m = self._metrics.get(name)
        if not m:
            return ["  Metric not found"]
        lines = []
        lines.append(f"  ── {m.name} ──")
        lines.append(f"  Current: {m.current_display}")
        lines.append(f"  Min: {m.min_val:.1f} {m.unit}  Max: {m.max_val:.1f} {m.unit}  Avg: {m.avg:.1f} {m.unit}")
        lines.append(f"  Status: {m.status}")
        if m.threshold_warn > 0:
            lines.append(f"  Warning: {m.threshold_warn} {m.unit}  Critical: {m.threshold_crit} {m.unit}")
        lines.append("")
        lines.append(f"  History: {m.sparkline}")
        return lines

    def render_alerts(self) -> list:
        lines = []
        lines.append("  ── Alerts ──")
        lines.append("")
        for i, a in enumerate(self._alerts):
            ack = "✅" if a.acknowledged else "  "
            age = int((time.time() - a.timestamp) / 60)
            lines.append(f"  {ack}{a.icon} {a.title}")
            lines.append(f"    {a.message}  ({a.source}, {age}m ago)")
        return lines
