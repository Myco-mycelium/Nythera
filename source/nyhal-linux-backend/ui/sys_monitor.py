"""System Monitor — Real-time CPU/RAM/network graphs and process manager.

Features:
- CPU usage per core with history graph
- Memory usage (RAM, swap, cache, buffer)
- Network I/O with bandwidth graphs
- Disk I/O and usage
- Process list with sorting and filtering
- System info (hostname, uptime, kernel, CPU model)
- Temperature monitoring
- Top consumers ranked
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class SortBy(Enum):
    CPU = "cpu"
    MEM = "mem"
    PID = "pid"
    NAME = "name"
    STATE = "state"


@dataclass
class CpuCore:
    id: int = 0
    usage_pct: float = 0.0
    user_pct: float = 0.0
    system_pct: float = 0.0
    idle_pct: float = 100.0
    temperature: float = 0.0
    frequency_mhz: float = 0.0

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.usage_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_str(self) -> str:
        return f"{self.temperature:.0f}°C"

    @property
    def temp_status(self) -> str:
        if self.temperature >= 80:
            return "🔴"
        if self.temperature >= 60:
            return "🟡"
        return "🟢"


@dataclass
class MemoryInfo:
    total_mb: float = 0.0
    used_mb: float = 0.0
    free_mb: float = 0.0
    cached_mb: float = 0.0
    buffers_mb: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0

    @property
    def used_pct(self) -> float:
        if self.total_mb == 0:
            return 0.0
        return self.used_mb / self.total_mb * 100

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.used_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def swap_pct(self) -> float:
        if self.swap_total_mb == 0:
            return 0.0
        return self.swap_used_mb / self.swap_total_mb * 100

    @property
    def swap_bar(self) -> str:
        filled = min(20, int(self.swap_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def used_str(self) -> str:
        return f"{self.used_mb / 1024:.1f} GB"

    @property
    def total_str(self) -> str:
        return f"{self.total_mb / 1024:.1f} GB"


@dataclass
class NetworkIO:
    interface: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate: float = 0.0  # bytes/sec
    tx_rate: float = 0.0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_history: List[float] = field(default_factory=list)
    tx_history: List[float] = field(default_factory=list)

    @property
    def rx_rate_str(self) -> str:
        b = self.rx_rate
        if b < 1024:
            return f"{b:.0f} B/s"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB/s"
        return f"{b / (1024 * 1024):.1f} MB/s"

    @property
    def tx_rate_str(self) -> str:
        b = self.tx_rate
        if b < 1024:
            return f"{b:.0f} B/s"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB/s"
        return f"{b / (1024 * 1024):.1f} MB/s"

    @property
    def rx_total_str(self) -> str:
        b = self.rx_bytes
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        if b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def tx_total_str(self) -> str:
        b = self.tx_bytes
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        if b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    def sparkline(self, data: List[float], width: int = 20) -> str:
        if not data:
            return "·" * width
        blocks = "▁▂▃▄▅▆▇█"
        max_val = max(data) if data else 1
        if max_val == 0:
            return "▁" * width
        result = ""
        step = max(1, len(data) // width)
        for i in range(0, min(len(data), width * step), step):
            val = data[i]
            idx = min(7, int(val / max_val * 7))
            result += blocks[idx]
        return result[:width]


@dataclass
class DiskIO:
    device: str = ""
    mount_point: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    read_rate: float = 0.0
    write_rate: float = 0.0

    @property
    def used_pct(self) -> float:
        if self.total_gb == 0:
            return 0.0
        return self.used_gb / self.total_gb * 100

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.used_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def free_gb(self) -> float:
        return self.total_gb - self.used_gb

    @property
    def read_str(self) -> str:
        b = self.read_rate
        if b < 1024 * 1024:
            return f"{b / 1024:.0f} KB/s"
        return f"{b / (1024 * 1024):.1f} MB/s"

    @property
    def write_str(self) -> str:
        b = self.write_rate
        if b < 1024 * 1024:
            return f"{b / 1024:.0f} KB/s"
        return f"{b / (1024 * 1024):.1f} MB/s"


@dataclass
class Process:
    pid: int = 0
    name: str = ""
    state: str = "S"  # R=running, S=sleeping, D=disk sleep, Z=zombie, T=stopped
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    mem_mb: float = 0.0
    user: str = ""
    threads: int = 1
    priority: int = 0
    nice: int = 0
    vsize_kb: int = 0
    started: float = 0.0

    @property
    def state_icon(self) -> str:
        icons = {"R": "🟢", "S": "⚪", "D": "🔵", "Z": "💀", "T": "🟡"}
        return icons.get(self.state, "❓")

    @property
    def cpu_bar(self) -> str:
        filled = min(20, int(self.cpu_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def mem_bar(self) -> str:
        filled = min(20, int(self.mem_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def vsize_str(self) -> str:
        gb = self.vsize_kb / (1024 * 1024)
        if gb < 1:
            return f"{self.vsize_kb / 1024:.0f} MB"
        return f"{gb:.1f} GB"


class SysMonitor:
    def __init__(self):
        self._cpu_cores: List[CpuCore] = []
        self._memory = MemoryInfo()
        self._network: List[NetworkIO] = []
        self._disks: List[DiskIO] = []
        self._processes: List[Process] = []
        self._selected_process: int = 0
        self._view_mode: str = "overview"  # overview, cpu, memory, network, disk, processes
        self._sort_by: SortBy = SortBy.CPU
        self._filter_text: str = ""
        self._history: Dict[str, List[float]] = {"cpu": [], "mem": [], "net_rx": [], "net_tx": []}
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # CPU cores
        for i in range(8):
            usage = random.uniform(5, 85)
            self._cpu_cores.append(CpuCore(
                id=i, usage_pct=usage,
                user_pct=usage * 0.6, system_pct=usage * 0.4,
                idle_pct=100 - usage,
                temperature=random.uniform(40, 75),
                frequency_mhz=random.choice([3400, 3600, 3800, 4200, 4500]),
            ))

        # Memory
        self._memory = MemoryInfo(
            total_mb=32768, used_mb=18432, free_mb=14336,
            cached_mb=4096, buffers_mb=2048,
            swap_total_mb=8192, swap_used_mb=512,
        )

        # Network
        self._network = [
            NetworkIO("eth0",
                       rx_bytes=15_360_000_000, tx_bytes=3_840_000_000,
                       rx_rate=12_500_000, tx_rate=3_200_000,
                       rx_packets=10_000_000, tx_packets=5_000_000,
                       rx_history=[random.uniform(5_000_000, 15_000_000) for _ in range(60)],
                       tx_history=[random.uniform(1_000_000, 5_000_000) for _ in range(60)]),
            NetworkIO("wlan0",
                       rx_bytes=2_560_000_000, tx_bytes=640_000_000,
                       rx_rate=2_500_000, tx_rate=800_000,
                       rx_history=[random.uniform(1_000_000, 5_000_000) for _ in range(60)],
                       tx_history=[random.uniform(200_000, 1_500_000) for _ in range(60)]),
        ]

        # Disks
        self._disks = [
            DiskIO("/dev/nvme0n1p1", "/", 500, 320, 45_000_000, 12_000_000),
            DiskIO("/dev/nvme0n1p2", "/home", 1000, 680, 25_000_000, 8_000_000),
            DiskIO("/dev/sda1", "/mnt/data", 2000, 1200, 5_000_000, 15_000_000),
        ]

        # Processes
        proc_data = [
            (1, "nyrqis-compositor", "R", 15.2, 3.8, 1248, "root", 12),
            (2, "nyrqis-shell", "S", 2.1, 1.5, 512, "zeus", 8),
            (3, "firefox", "S", 5.8, 4.2, 2048, "zeus", 45),
            (4, "code", "S", 3.5, 2.8, 1536, "zeus", 32),
            (5, "postgres", "S", 1.2, 2.1, 1024, "postgres", 15),
            (6, "redis-server", "S", 0.5, 0.8, 256, "redis", 4),
            (7, "nginx", "S", 0.2, 0.3, 128, "www-data", 2),
            (8, "node", "S", 4.2, 1.8, 896, "zeus", 18),
            (9, "python3", "S", 1.8, 0.9, 384, "zeus", 6),
            (10, "Xwayland", "S", 0.8, 0.5, 256, "zeus", 4),
            (11, "systemd", "S", 0.1, 0.2, 64, "root", 1),
            (12, "dbus-daemon", "S", 0.05, 0.1, 32, "dbus", 1),
            (13, "pipewire", "S", 0.3, 0.2, 96, "zeus", 3),
            (14, "pulseaudio", "S", 0.2, 0.1, 64, "zeus", 2),
            (15, "gpg-agent", "S", 0.01, 0.05, 16, "zeus", 1),
        ]
        for (pid, name, state, cpu, mem, mem_mb, user, threads) in proc_data:
            self._processes.append(Process(
                pid=pid, name=name, state=state, cpu_pct=cpu, mem_pct=mem,
                mem_mb=mem_mb, user=user, threads=threads,
                priority=random.choice([-10, 0, 10, 20]),
                nice=random.choice([-20, -10, 0, 10, 19]),
                vsize_kb=random.randint(10240, 8192000),
                started=now - random.uniform(3600, 86400 * 7),
            ))
        self._processes.sort(key=lambda p: p.cpu_pct, reverse=True)

        # History
        for _ in range(60):
            self._history["cpu"].append(random.uniform(20, 80))
            self._history["mem"].append(random.uniform(45, 65))
            self._history["net_rx"].append(random.uniform(5_000_000, 15_000_000))
            self._history["net_tx"].append(random.uniform(1_000_000, 5_000_000))

    @property
    def avg_cpu(self) -> float:
        if not self._cpu_cores:
            return 0.0
        return sum(c.usage_pct for c in self._cpu_cores) / len(self._cpu_cores)

    @property
    def filtered_processes(self) -> List[Process]:
        result = self._processes
        if self._filter_text:
            q = self._filter_text.lower()
            result = [p for p in result if q in p.name.lower() or q in p.user.lower()]
        if self._sort_by == SortBy.CPU:
            result = sorted(result, key=lambda p: p.cpu_pct, reverse=True)
        elif self._sort_by == SortBy.MEM:
            result = sorted(result, key=lambda p: p.mem_mb, reverse=True)
        elif self._sort_by == SortBy.PID:
            result = sorted(result, key=lambda p: p.pid)
        return result

    @property
    def total_cpu_time(self) -> float:
        return sum(c.usage_pct for c in self._cpu_cores)

    def select_process(self, idx: int):
        if 0 <= idx < len(self.filtered_processes):
            self._selected_process = idx

    def set_view(self, mode: str):
        if mode in ("overview", "cpu", "memory", "network", "disk", "processes"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM MONITOR                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # System info
        cpu_avg = self.avg_cpu
        mem = self._memory
        lines.append(f"  🖥 nyrqis-workstation  🐧 6.8.0-nyrqis  ⚙️ AMD Ryzen 9 7950X (16C/32T)  🕐 up 14d 6h")
        lines.append(f"  CPU: [{self._cpu_cores[0].usage_bar}] {cpu_avg:.1f}% avg  RAM: [{mem.usage_bar}] {mem.used_str}/{mem.total_str} ({mem.used_pct:.1f}%)")
        lines.append("")

        if self._view_mode == "overview":
            # CPU per core
            lines.append("  ── CPU Cores ──")
            for core in self._cpu_cores[:8]:
                lines.append(f"  Core {core.id}: [{core.usage_bar}] {core.usage_pct:5.1f}%  {core.temp_status} {core.temp_str}  {core.frequency_mhz:.0f}MHz")
            lines.append("")

            # Memory
            lines.append("  ── Memory ──")
            lines.append(f"  RAM:     [{mem.usage_bar}] {mem.used_str}/{mem.total_str} ({mem.used_pct:.1f}%)")
            lines.append(f"  Swap:    [{mem.swap_bar}] {mem.swap_used_mb:.0f}MB/{mem.swap_total_mb:.0f}MB ({mem.swap_pct:.1f}%)")
            lines.append(f"  Cached:  {mem.cached_mb:.0f}MB  Buffers: {mem.buffers_mb:.0f}MB  Free: {mem.free_mb:.0f}MB")
            lines.append("")

            # Network quick
            for net in self._network:
                lines.append(f"  🌐 {net.interface}: ↓{net.rx_rate_str} ↑{net.tx_rate_str}  Total: ↓{net.rx_total_str} ↑{net.tx_total_str}")

        elif self._view_mode == "cpu":
            lines.append("  ── CPU Usage History ──")
            spark = self._network[0].sparkline(self._history["cpu"])
            lines.append(f"  CPU [{spark}]")
            lines.append("")
            lines.append("  ── Per-Core Details ──")
            for core in self._cpu_cores:
                lines.append(f"  Core {core.id}: [{core.usage_bar}] {core.usage_pct:5.1f}% (usr:{core.user_pct:.1f}% sys:{core.system_pct:.1f}%)  {core.temp_status} {core.temp_str}  {core.frequency_mhz:.0f}MHz")

        elif self._view_mode == "memory":
            lines.append("  ── Memory Breakdown ──")
            lines.append(f"  Total:  {mem.total_str}")
            lines.append(f"  Used:   [{mem.usage_bar}] {mem.used_str} ({mem.used_pct:.1f}%)")
            lines.append(f"  Free:   {mem.free_mb / 1024:.1f} GB")
            lines.append(f"  Cached: {mem.cached_mb / 1024:.1f} GB")
            lines.append(f"  Buffers:{mem.buffers_mb / 1024:.1f} GB")
            lines.append(f"  Swap:   [{mem.swap_bar}] {mem.swap_used_mb:.0f}MB/{mem.swap_total_mb:.0f}MB ({mem.swap_pct:.1f}%)")

        elif self._view_mode == "network":
            lines.append("  ── Network Interfaces ──")
            for net in self._network:
                lines.append(f"  🌐 {net.interface}")
                rx_spark = net.sparkline(net.rx_history)
                tx_spark = net.sparkline(net.tx_history)
                lines.append(f"     ↓ [{rx_spark}] {net.rx_rate_str}  Total: {net.rx_total_str}")
                lines.append(f"     ↑ [{tx_spark}] {net.tx_rate_str}  Total: {net.tx_total_str}")
                lines.append(f"     Packets: ↓{net.rx_packets:,} ↑{net.tx_packets:,}  Errors: ↓{net.rx_errors} ↑{net.tx_errors}")

        elif self._view_mode == "disk":
            lines.append("  ── Disk Usage ──")
            for disk in self._disks:
                lines.append(f"  💾 {disk.device} → {disk.mount_point}")
                lines.append(f"     [{disk.usage_bar}] {disk.used_gb:.0f}GB/{disk.total_gb:.0f}GB ({disk.used_pct:.0f}%) Free: {disk.free_gb:.0f}GB")
                lines.append(f"     Read: {disk.read_str}  Write: {disk.write_str}")

        elif self._view_mode == "processes":
            lines.append("  ── Processes ──")
            for i, proc in enumerate(self.filtered_processes[:12]):
                sel = "▶" if i == self._selected_process else " "
                lines.append(f"  {sel} {proc.state_icon} {proc.pid:<6d} {proc.name:<20s} {proc.user:<10s} CPU:[{proc.cpu_bar}] {proc.cpu_pct:5.1f}%  MEM:{proc.mem_mb:>6.0f}MB  {proc.threads}T")

        lines.append("")
        lines.append("  [O]verview [C]PU [M]emory [N]etwork [D]isk [P]rocesses [/]Filter [↑↓]Nav")
        return lines
