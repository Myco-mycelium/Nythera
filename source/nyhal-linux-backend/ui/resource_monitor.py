"""
Nyrqis OS - Resource Monitor
CPU/RAM/network graphs, process list, and kill controls.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ProcessSortBy(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    NAME = "name"
    PID = "pid"
    THREADS = "threads"


@dataclass
class ResourceProcess:
    pid: int = 0
    name: str = ""
    user: str = ""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    threads: int = 1
    state: str = "S"
    nice: int = 0
    start_time: float = 0.0
    cpu_time_s: float = 0.0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0
    command: str = ""

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def mem_bar(self) -> str:
        filled = int(self.memory_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def state_icon(self) -> str:
        icons = {"R": "🟢", "S": "💤", "D": "🟡", "Z": "🧟", "T": "⏸"}
        return icons.get(self.state, "❓")

    @property
    def uptime_display(self) -> str:
        delta = time.time() - self.start_time if self.start_time else 0
        if delta < 60:
            return f"{delta:.0f}s"
        elif delta < 3600:
            return f"{delta / 60:.0f}m"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h"
        return f"{delta / 86400:.1f}d"


@dataclass
class MemoryInfo:
    total_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    buffers_gb: float = 0.0
    cached_gb: float = 0.0
    swap_total_gb: float = 0.0
    swap_used_gb: float = 0.0

    @property
    def usage_percent(self) -> float:
        if self.total_gb == 0:
            return 0.0
        return (self.used_gb / self.total_gb) * 100

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def swap_percent(self) -> float:
        if self.swap_total_gb == 0:
            return 0.0
        return (self.swap_used_gb / self.swap_total_gb) * 100


@dataclass
class NetworkStats:
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    rx_packets: int = 0
    tx_packets: int = 0

    @property
    def rx_display(self) -> str:
        bps = self.rx_rate
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        return f"{bps / (1024 * 1024):.1f} MB/s"

    @property
    def tx_display(self) -> str:
        bps = self.tx_rate
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        return f"{bps / (1024 * 1024):.1f} MB/s"

    @property
    def total_rx_display(self) -> str:
        gb = self.rx_bytes / (1024 ** 3)
        return f"{gb:.2f} GB"

    @property
    def total_tx_display(self) -> str:
        gb = self.tx_bytes / (1024 ** 3)
        return f"{gb:.2f} GB"


class ResourceMonitor:
    def __init__(self):
        self.processes: List[ResourceProcess] = []
        self.memory = MemoryInfo()
        self.network = NetworkStats()
        self.cpu_history: List[float] = []
        self.memory_history: List[float] = []
        self.network_rx_history: List[float] = []
        self.network_tx_history: List[float] = []
        self.sort_by: ProcessSortBy = ProcessSortBy.CPU
        self.sort_reverse: bool = True
        self.filter_user: str = ""
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.processes = [
            ResourceProcess(pid=1, name="systemd", user="root", cpu_percent=0.1,
                             memory_mb=12.5, memory_percent=0.2, threads=4,
                             state="S", start_time=now - 86400),
            ResourceProcess(pid=2, name="nyrqis-compositor", user="root", cpu_percent=35.2,
                             memory_mb=256.0, memory_percent=3.9, threads=8,
                             state="R", start_time=now - 7200),
            ResourceProcess(pid=3, name="nyrqis-shell", user="zeus", cpu_percent=12.8,
                             memory_mb=128.0, memory_percent=1.9, threads=4,
                             state="S", start_time=now - 7200),
            ResourceProcess(pid=200, name="firefox", user="zeus", cpu_percent=18.5,
                             memory_mb=1024.0, memory_percent=15.4, threads=45,
                             state="S", start_time=now - 3600),
            ResourceProcess(pid=201, name="firefox-content", user="zeus", cpu_percent=5.2,
                             memory_mb=512.0, memory_percent=7.7, threads=12,
                             state="S", start_time=now - 3600),
            ResourceProcess(pid=300, name="code-server", user="zeus", cpu_percent=8.0,
                             memory_mb=384.0, memory_percent=5.8, threads=16,
                             state="S", start_time=now - 1800),
            ResourceProcess(pid=400, name="pulseaudio", user="zeus", cpu_percent=0.1,
                             memory_mb=24.0, memory_percent=0.3, threads=3,
                             state="S", start_time=now - 86400),
            ResourceProcess(pid=500, name="python3", user="zeus", cpu_percent=2.5,
                             memory_mb=48.0, memory_percent=0.7, threads=4,
                             state="S", start_time=now - 600),
        ]

        self.memory = MemoryInfo(total_gb=64.0, used_gb=28.3, available_gb=35.7,
                                  buffers_gb=2.1, cached_gb=8.5,
                                  swap_total_gb=8.0, swap_used_gb=0.5)

        self.network = NetworkStats(rx_bytes=52000000000, tx_bytes=8500000000,
                                     rx_rate=12500000, tx_rate=2500000,
                                     rx_packets=45000, tx_packets=12000)

        self.cpu_history = [25, 30, 45, 60, 55, 40, 35, 32, 28, 30,
                            35, 42, 38, 34, 30, 28, 25, 30, 35, 38]
        self.memory_history = [42, 42.5, 43, 43.2, 43.5, 44, 44.2, 44, 43.8, 43.5]
        self.network_rx_history = [10, 12, 15, 14, 11, 13, 16, 12.5]
        self.network_tx_history = [2, 2.5, 3, 2.8, 2.1, 2.4, 3.2, 2.5]

    def get_sorted_processes(self) -> List[ResourceProcess]:
        procs = list(self.processes)
        if self.filter_user:
            procs = [p for p in procs if p.user == self.filter_user]
        key_map = {
            ProcessSortBy.CPU: lambda p: p.cpu_percent,
            ProcessSortBy.MEMORY: lambda p: p.memory_mb,
            ProcessSortBy.NAME: lambda p: p.name,
            ProcessSortBy.PID: lambda p: p.pid,
            ProcessSortBy.THREADS: lambda p: p.threads,
        }
        procs.sort(key=key_map.get(self.sort_by, lambda p: p.cpu_percent),
                    reverse=self.sort_reverse)
        return procs

    def kill_process(self, pid: int, signal: str = "SIGTERM") -> bool:
        for i, p in enumerate(self.processes):
            if p.pid == pid:
                del self.processes[i]
                return True
        return False

    def search_processes(self, query: str) -> List[ResourceProcess]:
        q = query.lower()
        return [p for p in self.processes if q in p.name.lower() or q in p.command.lower()]

    def get_cpu_usage(self) -> float:
        return self.cpu_history[-1] if self.cpu_history else 0.0

    def get_memory_usage(self) -> float:
        return self.memory.usage_percent

    def get_top_cpu(self, limit: int = 5) -> List[ResourceProcess]:
        return sorted(self.processes, key=lambda p: p.cpu_percent, reverse=True)[:limit]

    def get_top_memory(self, limit: int = 5) -> List[ResourceProcess]:
        return sorted(self.processes, key=lambda p: p.memory_mb, reverse=True)[:limit]

    def get_stats(self) -> Dict:
        return {
            "processes": len(self.processes),
            "cpu_usage": self.get_cpu_usage(),
            "memory_used_gb": round(self.memory.used_gb, 1),
            "memory_total_gb": self.memory.total_gb,
            "network_rx": self.network.rx_display,
            "network_tx": self.network.tx_display,
        }
