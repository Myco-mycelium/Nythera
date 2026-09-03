from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class MonitorView(Enum):
    OVERVIEW = "overview"
    CPU = "cpu"
    MEMORY = "memory"
    GPU = "gpu"
    DISK = "disk"
    NETWORK = "network"
    PROCESSES = "processes"
    ALERTS = "alerts"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class ProcessSort(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    PID = "pid"
    NAME = "name"


class ProcessState(Enum):
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPED = "stopped"
    ZOMBIE = "zombie"


@dataclass
class CpuCore:
    core_id: int
    usage_percent: float
    frequency_mhz: int
    temperature_c: float
    history: list = field(default_factory=list)

    @property
    def thermal_status(self) -> str:
        if self.temperature_c >= 90:
            return "🔴 Critical"
        if self.temperature_c >= 75:
            return "🟠 Hot"
        if self.temperature_c >= 60:
            return "🟡 Warm"
        if self.temperature_c >= 40:
            return "🟢 Normal"
        return "❄️ Cool"

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class GpuInfo:
    name: str
    driver: str
    usage_percent: float
    memory_used_mb: int
    memory_total_mb: int
    temperature_c: float
    power_watts: float
    fan_speed_rpm: int
    core_clock_mhz: int
    memory_clock_mhz: int
    history: list = field(default_factory=list)

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_bar(self) -> str:
        pct = (self.memory_used_mb / self.memory_total_mb * 100) if self.memory_total_mb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_display(self) -> str:
        return f"{self.memory_used_mb / 1024:.1f} / {self.memory_total_mb / 1024:.1f} GB"

    @property
    def temp_status(self) -> str:
        if self.temperature_c >= 90:
            return "🔴"
        if self.temperature_c >= 75:
            return "🟠"
        if self.temperature_c >= 60:
            return "🟡"
        return "🟢"


@dataclass
class MemoryInfo:
    total_gb: float
    used_gb: float
    cached_gb: float
    buffers_gb: float
    swap_total_gb: float
    swap_used_gb: float
    hugepages_total: int
    hugepages_used: int
    history: list = field(default_factory=list)

    @property
    def usage_bar(self) -> str:
        pct = (self.used_gb / self.total_gb * 100) if self.total_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def available_gb(self) -> float:
        return self.total_gb - self.used_gb

    @property
    def swap_bar(self) -> str:
        pct = (self.swap_used_gb / self.swap_total_gb * 100) if self.swap_total_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class DiskInfo:
    device: str
    mount: str
    filesystem: str
    total_gb: float
    used_gb: float
    io_read_mbps: float
    io_write_mbps: float
    iops_read: int
    iops_write: int

    @property
    def usage_bar(self) -> str:
        pct = (self.used_gb / self.total_gb * 100) if self.total_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def available_gb(self) -> float:
        return self.total_gb - self.used_gb


@dataclass
class ProcessInfo:
    pid: int
    name: str
    user: str
    cpu_percent: float
    memory_mb: float
    state: ProcessState
    threads: int
    parent_pid: int = 0
    command: str = ""

    @property
    def memory_display(self) -> str:
        if self.memory_mb >= 1024:
            return f"{self.memory_mb / 1024:.1f} GB"
        return f"{self.memory_mb:.0f} MB"

    @property
    def state_icon(self) -> str:
        icons = {ProcessState.RUNNING: "🟢", ProcessState.SLEEPING: "💤", ProcessState.STOPPED: "⏹", ProcessState.ZOMBIE: "👻"}
        return icons.get(self.state, "?")


@dataclass
class Alert:
    severity: AlertSeverity
    title: str
    message: str
    timestamp: float
    source: str
    acknowledged: bool = False

    @property
    def icon(self) -> str:
        icons = {AlertSeverity.INFO: "ℹ️", AlertSeverity.WARNING: "⚠️", AlertSeverity.CRITICAL: "🔴", AlertSeverity.EMERGENCY: "🚨"}
        return icons.get(self.severity, "?")


class SystemMonitorPro:
    def __init__(self):
        self._view: MonitorView = MonitorView.OVERVIEW
        self._selected_process: int = 0
        self._process_sort: ProcessSort = ProcessSort.CPU
        self._cpu_cores: list[CpuCore] = []
        self._gpu: Optional[GpuInfo] = None
        self._memory: Optional[MemoryInfo] = None
        self._disks: list[DiskInfo] = []
        self._processes: list[ProcessInfo] = []
        self._alerts: list[Alert] = []
        self._uptime_secs: int = 86400 * 3 + 3600 * 7 + 120
        self._load_avg: tuple = (2.45, 1.89, 1.67)
        self._total_cpu: float = 0
        self._refresh_interval: int = 1
        self._create_samples()

    def _create_samples(self):
        import math
        now = time.time()
        self._cpu_cores = []
        for i in range(16):
            usage = 5 + math.sin(i * 0.7) * 30 + 15
            temp = 35 + usage * 0.4
            freq = 3600 + int(usage * 20)
            history = [max(0, min(100, usage + math.sin(j * 0.3) * 20)) for j in range(60)]
            self._cpu_cores.append(CpuCore(i, round(usage, 1), freq, round(temp, 1), history))
        self._total_cpu = sum(c.usage_percent for c in self._cpu_cores) / len(self._cpu_cores)

        self._gpu = GpuInfo("NVIDIA GeForce RTX 4070", "560.50", 45.2, 7680, 12288, 62.0, 120.0, 1400, 2550, 10501,
                             history=[45 + math.sin(i * 0.2) * 15 for i in range(60)])

        self._memory = MemoryInfo(64.0, 38.7, 12.4, 4.2, 8.0, 0.5, 0, 0,
                                   history=[38 + math.sin(i * 0.1) * 2 for i in range(60)])

        self._disks = [
            DiskInfo("/dev/nvme0n1p2", "/", "ext4", 1920.0, 847.0, 125.4, 89.2, 15420, 8930),
            DiskInfo("/dev/nvme0n1p1", "/boot/efi", "vfat", 0.5, 0.1, 0.0, 0.0, 12, 3),
            DiskInfo("/dev/sda1", "/data", "ext4", 4000.0, 2150.0, 45.2, 23.8, 5230, 3120),
        ]

        self._processes = [
            ProcessInfo(1, "systemd", "root", 0.1, 12.4, ProcessState.SLEEPING, 1),
            ProcessInfo(456, "nyrqis-compositor", "user", 35.2, 1536.0, ProcessState.RUNNING, 8),
            ProcessInfo(789, "firefox", "user", 28.5, 3276.8, ProcessState.RUNNING, 42),
            ProcessInfo(1011, "code", "user", 22.1, 2867.2, ProcessState.RUNNING, 35),
            ProcessInfo(1234, "dockerd", "root", 8.3, 256.0, ProcessState.SLEEPING, 12),
            ProcessInfo(1345, "postgres", "user", 4.2, 512.0, ProcessState.SLEEPING, 6),
            ProcessInfo(1456, "redis-server", "user", 1.5, 64.0, ProcessState.SLEEPING, 3),
            ProcessInfo(1567, "rustc", "user", 65.0, 1024.0, ProcessState.RUNNING, 4),
            ProcessInfo(1678, "Xwayland", "user", 12.8, 384.0, ProcessState.SLEEPING, 3),
            ProcessInfo(1789, "pipewire", "user", 2.1, 48.0, ProcessState.SLEEPING, 4),
            ProcessInfo(1890, "NetworkManager", "root", 0.5, 32.0, ProcessState.SLEEPING, 2),
            ProcessInfo(1901, "systemd-journald", "root", 0.3, 64.0, ProcessState.SLEEPING, 1),
            ProcessInfo(2012, "bash", "user", 0.1, 8.0, ProcessState.SLEEPING, 1),
            ProcessInfo(2123, "python3", "user", 15.4, 256.0, ProcessState.RUNNING, 2),
            ProcessInfo(2234, "cargo", "user", 45.0, 2048.0, ProcessState.RUNNING, 16),
        ]

        self._alerts = [
            Alert(AlertSeverity.WARNING, "High CPU Usage", "nyrqis-compositor using 35% CPU", now - 300, "cpu"),
            Alert(AlertSeverity.CRITICAL, "High Temperature", "GPU temperature reached 62°C", now - 600, "gpu"),
            Alert(AlertSeverity.INFO, "Service Started", "docker.service started successfully", now - 1800, "system"),
            Alert(AlertSeverity.WARNING, "Memory Pressure", "System using 60% of 64GB RAM", now - 900, "memory"),
            Alert(AlertSeverity.INFO, "Disk Cleanup", "Freed 2.3GB from /tmp", now - 3600, "disk"),
        ]

    @property
    def selected_process(self) -> Optional[ProcessInfo]:
        if 0 <= self._selected_process < len(self._processes):
            return self._processes[self._selected_process]
        return None

    @property
    def total_processes(self) -> int:
        return len(self._processes)

    @property
    def running_processes(self) -> int:
        return sum(1 for p in self._processes if p.state == ProcessState.RUNNING)

    @property
    def unacked_alerts(self) -> int:
        return sum(1 for a in self._alerts if not a.acknowledged)

    @property
    def uptime_display(self) -> str:
        d, rem = divmod(self._uptime_secs, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        return f"{d}d {h}h {m}m {s}s"

    @property
    def load_display(self) -> str:
        return f"{self._load_avg[0]:.2f} {self._load_avg[1]:.2f} {self._load_avg[2]:.2f}"

    def select_process(self, idx: int):
        if 0 <= idx < len(self._processes):
            self._selected_process = idx

    def sort_processes(self, sort_by: ProcessSort):
        self._process_sort = sort_by
        key_funcs = {
            ProcessSort.CPU: lambda p: -p.cpu_percent,
            ProcessSort.MEMORY: lambda p: -p.memory_mb,
            ProcessSort.PID: lambda p: p.pid,
            ProcessSort.NAME: lambda p: p.name,
        }
        self._processes.sort(key=key_funcs[sort_by])

    def acknowledge_alert(self, idx: int):
        if 0 <= idx < len(self._alerts):
            self._alerts[idx].acknowledged = True

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                   NYRQIS SYSTEM MONITOR PRO                                ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  View: {self._view.value}  Uptime: {self.uptime_display}  Load: {self.load_display}")
        lines.append(f"  Processes: {self.total_processes} ({self.running_processes} running)  Alerts: {self.unacked_alerts} unacknowledged")
        lines.append("")
        if self._view == MonitorView.OVERVIEW:
            lines.extend(self._render_overview())
        elif self._view == MonitorView.CPU:
            lines.extend(self._render_cpu())
        elif self._view == MonitorView.MEMORY:
            lines.extend(self._render_memory())
        elif self._view == MonitorView.GPU:
            lines.extend(self._render_gpu())
        elif self._view == MonitorView.PROCESSES:
            lines.extend(self._render_processes())
        elif self._view == MonitorView.ALERTS:
            lines.extend(self._render_alerts())
        lines.append("")
        lines.append("  [V]iew  [S]ort  [K]ill process  [A]ck alert  [R]efresh")
        return lines

    def _render_overview(self) -> list:
        lines = []
        lines.append(f"  ── CPU ({self._total_cpu:.1f}%) ──")
        for i in range(0, 16, 4):
            line = "  "
            for j in range(4):
                c = self._cpu_cores[i + j] if i + j < len(self._cpu_cores) else None
                if c:
                    line += f"Core{c.core_id:2d}: {c.usage_bar[:10]} {c.usage_percent:5.1f}% "
            lines.append(line)
        lines.append("")
        if self._gpu:
            lines.append(f"  ── GPU ({self._gpu.name}) ──")
            lines.append(f"  Usage: {self._gpu.usage_bar} {self._gpu.usage_percent:.1f}%")
            lines.append(f"  Memory: {self._gpu.memory_bar} {self._gpu.memory_display}")
            lines.append(f"  Temp: {self._gpu.temp_status} {self._gpu.temperature_c:.0f}°C  Power: {self._gpu.power_watts:.0f}W")
        lines.append("")
        if self._memory:
            lines.append(f"  ── Memory ──")
            lines.append(f"  RAM:   {self._memory.usage_bar} {self._memory.used_gb:.1f}/{self._memory.total_gb:.1f} GB ({self._memory.used_gb/self._memory.total_gb*100:.0f}%)")
            lines.append(f"  Swap:  {self._memory.swap_bar} {self._memory.swap_used_gb:.1f}/{self._memory.swap_total_gb:.1f} GB")
        return lines

    def _render_cpu(self) -> list:
        lines = []
        lines.append("  ── CPU Details ──")
        lines.append("")
        for c in self._cpu_cores:
            history = "".join(["▁▂▃▄▅▆▇█"[min(int(v / 12.5), 7)] for v in c.history[-32:]])
            lines.append(f"  Core {c.core_id:2d} {c.usage_bar} {c.usage_percent:5.1f}%  {c.frequency_mhz:5d}MHz  {c.thermal_status} {c.temperature_c:.0f}°C  {history}")
        return lines

    def _render_memory(self) -> list:
        lines = []
        if self._memory:
            m = self._memory
            lines.append(f"  ── Memory ──")
            lines.append(f"  Total:     {m.total_gb:.1f} GB")
            lines.append(f"  Used:      {m.usage_bar} {m.used_gb:.1f} GB ({m.used_gb/m.total_gb*100:.0f}%)")
            lines.append(f"  Available: {m.available_gb:.1f} GB")
            lines.append(f"  Cached:    {m.cached_gb:.1f} GB")
            lines.append(f"  Buffers:   {m.buffers_gb:.1f} GB")
            lines.append(f"  Swap:      {m.swap_bar} {m.swap_used_gb:.1f}/{m.swap_total_gb:.1f} GB")
        lines.append("")
        lines.append("  ── Top Memory Consumers ──")
        top = sorted(self._processes, key=lambda p: -p.memory_mb)[:5]
        for p in top:
            bar_len = int(p.memory_mb / 2048 * 20)
            bar = "█" * min(bar_len, 20)
            lines.append(f"  {p.name:<20s} {bar} {p.memory_display}")
        return lines

    def _render_gpu(self) -> list:
        lines = []
        if self._gpu:
            g = self._gpu
            lines.append(f"  ── {g.name} ──")
            lines.append(f"  Driver: {g.driver}")
            lines.append(f"  Usage:  {g.usage_bar} {g.usage_percent:.1f}%")
            lines.append(f"  Memory: {g.memory_bar} {g.memory_display}")
            lines.append(f"  Temp:   {g.temp_status} {g.temperature_c:.0f}°C")
            lines.append(f"  Power:  {g.power_watts:.0f}W")
            lines.append(f"  Fan:    {g.fan_speed_rpm} RPM")
            lines.append(f"  Core:   {g.core_clock_mhz} MHz")
            lines.append(f"  Memory: {g.memory_clock_mhz} MHz (effective)")
        return lines

    def _render_processes(self) -> list:
        lines = []
        lines.append(f"  ── Processes (sorted by {self._process_sort.value}) ──")
        lines.append("")
        for i, p in enumerate(self._processes):
            sel = "▶" if i == self._selected_process else " "
            lines.append(f"  {sel} {p.state_icon} {p.pid:<6d} {p.name:<20s} {p.user:<10s} {p.cpu_percent:5.1f}%  {p.memory_display:>8s}  {p.threads} threads")
        return lines

    def _render_alerts(self) -> list:
        lines = []
        lines.append("  ── Alerts ──")
        lines.append("")
        for i, a in enumerate(self._alerts):
            ack = "✅" if a.acknowledged else "  "
            age = int((time.time() - a.timestamp) / 60)
            lines.append(f"  {ack} {a.icon} {a.title}")
            lines.append(f"    {a.message}  ({age}m ago  source: {a.source})")
        return lines
