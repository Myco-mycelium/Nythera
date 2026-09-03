"""
Nyrqis Process Manager — system process viewer and manager.

Features:
- Process list with CPU, memory, PID, name, user, status
- Sort by CPU, memory, PID, name
- Kill/terminate processes with confirmation
- Process detail view with resource graphs, open files, environment
- Real-time CPU/memory sparklines
- Group by application or user
- Search and filter
- Resource usage summaries (total CPU, memory, swap, disk I/O)
- Process priority management (nice values)
"""

import os
import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ProcessStatus(Enum):
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPED = "stopped"
    ZOMBIE = "zombie"
    IDLE = "idle"


class SortField(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    PID = "pid"
    NAME = "name"
    USER = "user"


class ProcessGroup(Enum):
    ALL = "all"
    USER = "user"
    SYSTEM = "system"
    APPS = "apps"


@dataclass
class ProcessInfo:
    """Information about a running process."""
    pid: int
    name: str
    user: str = "user"
    status: ProcessStatus = ProcessStatus.RUNNING
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    virtual_mb: float = 0.0
    threads: int = 1
    nice: int = 0
    parent_pid: int = 0
    start_time: float = 0.0
    command: str = ""
    cpu_history: List[float] = field(default_factory=list)
    mem_history: List[float] = field(default_factory=list)
    open_files: int = 0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0

    @property
    def uptime_str(self) -> str:
        if self.start_time <= 0:
            return "unknown"
        diff = time.time() - self.start_time
        if diff < 60:
            return f"{int(diff)}s"
        elif diff < 3600:
            return f"{int(diff // 60)}m"
        elif diff < 86400:
            return f"{int(diff // 3600)}h {int((diff % 3600) // 60)}m"
        else:
            return f"{int(diff // 86400)}d {int((diff % 86400) // 3600)}h"

    @property
    def memory_str(self) -> str:
        if self.memory_mb < 1024:
            return f"{self.memory_mb:.0f} MB"
        return f"{self.memory_mb / 1024:.1f} GB"

    @property
    def cpu_str(self) -> str:
        return f"{self.cpu_percent:.1f}%"

    @property
    def status_icon(self) -> str:
        icons = {
            ProcessStatus.RUNNING: "🟢",
            ProcessStatus.SLEEPING: "💤",
            ProcessStatus.STOPPED: "⏸",
            ProcessStatus.ZOMBIE: "💀",
            ProcessStatus.IDLE: "🟡",
        }
        return icons.get(self.status, "❓")

    @property
    def nice_str(self) -> str:
        if self.nice < 0:
            return f"HIGH ({self.nice})"
        elif self.nice > 0:
            return f"LOW (+{self.nice})"
        return "NORMAL"

    def update_history(self) -> None:
        """Add current values to history."""
        self.cpu_history.append(self.cpu_percent)
        self.mem_history.append(self.memory_percent)
        if len(self.cpu_history) > 60:
            self.cpu_history = self.cpu_history[-60:]
        if len(self.mem_history) > 60:
            self.mem_history = self.mem_history[-60:]


@dataclass
class SystemResources:
    """System-wide resource usage."""
    total_cpu: float = 0.0
    total_memory_mb: float = 0.0
    used_memory_mb: float = 0.0
    total_swap_mb: float = 0.0
    used_swap_mb: float = 0.0
    total_disk_gb: float = 0.0
    used_disk_gb: float = 0.0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    uptime_seconds: float = 0.0

    @property
    def memory_percent(self) -> float:
        if self.total_memory_mb <= 0:
            return 0.0
        return (self.used_memory_mb / self.total_memory_mb) * 100

    @property
    def swap_percent(self) -> float:
        if self.total_swap_mb <= 0:
            return 0.0
        return (self.used_swap_mb / self.total_swap_mb) * 100

    @property
    def disk_percent(self) -> float:
        if self.total_disk_gb <= 0:
            return 0.0
        return (self.used_disk_gb / self.total_disk_gb) * 100

    @property
    def memory_str(self) -> str:
        used = self.used_memory_mb / 1024
        total = self.total_memory_mb / 1024
        return f"{used:.1f} / {total:.1f} GB"

    @property
    def disk_str(self) -> str:
        return f"{self.used_disk_gb:.1f} / {self.total_disk_gb:.1f} GB"

    @property
    def uptime_str(self) -> str:
        d = int(self.uptime_seconds // 86400)
        h = int((self.uptime_seconds % 86400) // 3600)
        m = int((self.uptime_seconds % 3600) // 60)
        if d > 0:
            return f"{d}d {h}h {m}m"
        return f"{h}h {m}m"


# ─── Process Manager ─────────────────────────────────────────────────────


class ProcessManager:
    """
    Process manager for Nyrqis OS.

    Displays and manages system processes.
    """

    def __init__(self):
        self._processes: List[ProcessInfo] = []
        self._resources = SystemResources()
        self._selected_index: int = 0
        self._sort_field: SortField = SortField.CPU
        self._sort_reverse: bool = True
        self._filter_text: str = ""
        self._group_mode: ProcessGroup = ProcessGroup.ALL
        self._view_mode: str = "list"  # list, detail
        self._detail_process: Optional[ProcessInfo] = None
        self._confirm_kill: Optional[ProcessInfo] = None

        # Callbacks
        self._on_kill: List[Callable] = []

        # Init sample data
        self._init_sample_processes()
        self._init_sample_resources()

    def _init_sample_processes(self) -> None:
        """Create simulated process list."""
        now = time.time()
        samples = [
            (1, "systemd", "root", ProcessStatus.SLEEPING, 0.1, 1.2, 24, 0, 1, now - 864000),
            (2, "kthreadd", "root", ProcessStatus.SLEEPING, 0.0, 0.0, 0, 0, 0, now - 864000),
            (100, "nyrqis-compositor", "user", ProcessStatus.RUNNING, 12.5, 4.8, 280, 512, 8, now - 86400),
            (150, "nyrqis-shell", "user", ProcessStatus.RUNNING, 3.2, 2.1, 150, 320, 4, now - 86400),
            (200, "nyrqis-terminal", "user", ProcessStatus.RUNNING, 1.8, 1.5, 120, 256, 2, now - 7200),
            (201, "bash", "user", ProcessStatus.SLEEPING, 0.1, 0.3, 8, 32, 1, now - 7200),
            (202, "zsh", "user", ProcessStatus.SLEEPING, 0.2, 0.4, 12, 40, 1, now - 3600),
            (300, "firefox", "user", ProcessStatus.RUNNING, 8.3, 6.2, 420, 1024, 12, now - 1800),
            (301, "firefox - Content", "user", ProcessStatus.RUNNING, 5.1, 3.8, 310, 768, 8, now - 1800),
            (302, "firefox - GPU", "user", ProcessStatus.RUNNING, 2.1, 1.2, 95, 256, 4, now - 1800),
            (400, "code", "user", ProcessStatus.RUNNING, 4.2, 3.5, 290, 640, 6, now - 5400),
            (401, "code - Extension", "user", ProcessStatus.SLEEPING, 0.8, 1.1, 85, 192, 3, now - 5400),
            (500, "spotify", "user", ProcessStatus.SLEEPING, 1.2, 2.0, 180, 384, 4, now - 10800),
            (501, "pulseaudio", "user", ProcessStatus.RUNNING, 0.5, 0.3, 18, 64, 2, now - 86400),
            (600, "Xwayland", "user", ProcessStatus.SLEEPING, 0.3, 0.5, 32, 96, 2, now - 86400),
            (700, "NetworkManager", "root", ProcessStatus.SLEEPING, 0.1, 0.2, 14, 48, 1, now - 86400),
            (701, "wpa_supplicant", "root", ProcessStatus.SLEEPING, 0.0, 0.1, 6, 24, 1, now - 86400),
            (800, "dbus-daemon", "user", ProcessStatus.SLEEPING, 0.1, 0.1, 8, 32, 1, now - 86400),
            (900, "dockerd", "root", ProcessStatus.SLEEPING, 0.8, 1.5, 120, 256, 3, now - 43200),
            (901, "containerd", "root", ProcessStatus.SLEEPING, 0.3, 0.6, 48, 128, 2, now - 43200),
            (1000, "mysqld", "root", ProcessStatus.SLEEPING, 1.5, 2.8, 220, 512, 8, now - 172800),
            (1100, "nginx", "root", ProcessStatus.SLEEPING, 0.2, 0.3, 16, 48, 2, now - 86400),
            (1200, "cron", "root", ProcessStatus.SLEEPING, 0.0, 0.1, 4, 16, 1, now - 86400),
            (1300, "rsyslogd", "root", ProcessStatus.SLEEPING, 0.1, 0.2, 10, 40, 1, now - 86400),
            (1400, "udisksd", "root", ProcessStatus.SLEEPING, 0.0, 0.2, 12, 48, 1, now - 86400),
            (1500, "thermald", "root", ProcessStatus.SLEEPING, 0.0, 0.1, 6, 32, 1, now - 86400),
            (1600, "gdm3", "root", ProcessStatus.SLEEPING, 0.1, 0.3, 18, 64, 2, now - 86400),
            (1700, "snapd", "root", ProcessStatus.SLEEPING, 0.2, 0.4, 24, 96, 2, now - 43200),
        ]

        for pid, name, user, status, cpu, mem, mem_mb, virt, threads, start in samples:
            # Generate realistic CPU history
            cpu_hist = [max(0, cpu + random.uniform(-cpu * 0.3, cpu * 0.3)) for _ in range(30)]
            mem_hist = [max(0, mem + random.uniform(-0.5, 0.5)) for _ in range(30)]

            self._processes.append(ProcessInfo(
                pid=pid,
                name=name,
                user=user,
                status=status,
                cpu_percent=cpu,
                memory_percent=mem,
                memory_mb=mem_mb,
                virtual_mb=virt,
                threads=threads,
                parent_pid=1 if pid > 1 else 0,
                start_time=start,
                command=f"/usr/bin/{name}",
                cpu_history=cpu_hist,
                mem_history=mem_hist,
                open_files=random.randint(3, 50),
                io_read_mb=random.uniform(0.1, 100),
                io_write_mb=random.uniform(0.01, 50),
            ))

    def _init_sample_resources(self) -> None:
        self._resources = SystemResources(
            total_cpu=800.0,  # 8 cores * 100%
            total_memory_mb=16384.0,
            used_memory_mb=8192.0,
            total_swap_mb=4096.0,
            used_swap_mb=512.0,
            total_disk_gb=500.0,
            used_disk_gb=215.0,
            io_read_mb=1234.5,
            io_write_mb=567.8,
            load_1m=2.35,
            load_5m=1.89,
            load_15m=1.42,
            uptime_seconds=2592000,
        )

    # ── Process Operations ────────────────────────────────────────────

    def get_processes(self) -> List[ProcessInfo]:
        """Get filtered and sorted process list."""
        procs = list(self._processes)

        # Filter
        if self._filter_text:
            q = self._filter_text.lower()
            procs = [p for p in procs if q in p.name.lower() or q in str(p.pid) or q in p.user.lower()]

        # Group
        if self._group_mode == ProcessGroup.USER:
            procs = [p for p in procs if p.user == "user"]
        elif self._group_mode == ProcessGroup.SYSTEM:
            procs = [p for p in procs if p.user == "root"]
        elif self._group_mode == ProcessGroup.APPS:
            procs = [p for p in procs if p.pid >= 200]

        # Sort
        key_map = {
            SortField.CPU: lambda p: p.cpu_percent,
            SortField.MEMORY: lambda p: p.memory_percent,
            SortField.PID: lambda p: p.pid,
            SortField.NAME: lambda p: p.name.lower(),
            SortField.USER: lambda p: p.user.lower(),
        }
        procs.sort(key=key_map.get(self._sort_field, lambda p: p.pid), reverse=self._sort_reverse)

        return procs

    def get_process(self, pid: int) -> Optional[ProcessInfo]:
        for p in self._processes:
            if p.pid == pid:
                return p
        return None

    def kill_process(self, pid: int, signal: str = "SIGTERM") -> bool:
        """Kill a process."""
        for i, p in enumerate(self._processes):
            if p.pid == pid:
                self._processes.pop(i)
                self._notify("kill", p)
                return True
        return False

    def confirm_kill(self, pid: int) -> Optional[ProcessInfo]:
        """Get process for kill confirmation."""
        p = self.get_process(pid)
        if p:
            self._confirm_kill = p
        return p

    def execute_kill(self) -> bool:
        """Execute confirmed kill."""
        if self._confirm_kill:
            result = self.kill_process(self._confirm_kill.pid)
            self._confirm_kill = None
            return result
        return False

    def cancel_kill(self) -> None:
        self._confirm_kill = None

    @property
    def confirm_kill_target(self) -> Optional[ProcessInfo]:
        return self._confirm_kill

    def set_nice(self, pid: int, nice: int) -> bool:
        """Set process priority."""
        p = self.get_process(pid)
        if p:
            p.nice = max(-20, min(19, nice))
            return True
        return False

    def update_processes(self) -> None:
        """Simulate process resource updates."""
        for p in self._processes:
            # Slight random variation
            p.cpu_percent = max(0, p.cpu_percent + random.uniform(-0.5, 0.5))
            p.memory_percent = max(0, p.memory_percent + random.uniform(-0.1, 0.1))
            p.update_history()

        # Update system resources
        total_cpu = sum(p.cpu_percent for p in self._processes)
        self._resources.total_cpu = max(100, total_cpu + random.uniform(-5, 5))
        self._resources.used_memory_mb = sum(p.memory_mb for p in self._processes)
        self._resources.load_1m = max(0, self._resources.load_1m + random.uniform(-0.1, 0.1))

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select(self, index: int) -> None:
        procs = self.get_processes()
        self._selected_index = max(0, min(len(procs) - 1, index))

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        procs = self.get_processes()
        self._selected_index = min(len(procs) - 1, self._selected_index + 1)

    def get_selected_process(self) -> Optional[ProcessInfo]:
        procs = self.get_processes()
        if 0 <= self._selected_index < len(procs):
            return procs[self._selected_index]
        return None

    # ── View Mode ─────────────────────────────────────────────────────

    def open_detail(self, pid: int = None) -> Optional[ProcessInfo]:
        if pid:
            p = self.get_process(pid)
        else:
            p = self.get_selected_process()
        if p:
            self._detail_process = p
            self._view_mode = "detail"
        return p

    def close_detail(self) -> None:
        self._detail_process = None
        self._view_mode = "list"

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def detail_process(self) -> Optional[ProcessInfo]:
        return self._detail_process

    # ── Sorting & Filtering ───────────────────────────────────────────

    def set_sort(self, field: SortField) -> None:
        if self._sort_field == field:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_field = field
            self._sort_reverse = True

    def set_filter(self, text: str) -> None:
        self._filter_text = text
        self._selected_index = 0

    def set_group(self, group: ProcessGroup) -> None:
        self._group_mode = group
        self._selected_index = 0

    @property
    def resources(self) -> SystemResources:
        return self._resources

    @property
    def process_count(self) -> int:
        return len(self._processes)

    @property
    def running_count(self) -> int:
        return len([p for p in self._processes if p.status == ProcessStatus.RUNNING])

    # ── Sparkline ─────────────────────────────────────────────────────

    @staticmethod
    def sparkline(values: List[float], width: int = 20) -> str:
        """Create a text sparkline from values."""
        if not values:
            return " " * width
        blocks = " ▁▂▃▄▅▆▇█"
        max_val = max(values) if max(values) > 0 else 1
        # Resize to width
        if len(values) > width:
            step = len(values) / width
            values = [values[int(i * step)] for i in range(width)]
        elif len(values) < width:
            values = values + [0] * (width - len(values))

        return "".join(blocks[min(int(v / max_val * 8), 8)] for v in values)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_summary(self, width: int = 72) -> List[str]:
        """Render resource summary."""
        lines = []
        r = self._resources

        # CPU bar
        cpu_pct = min(100, sum(p.cpu_percent for p in self._processes))
        cpu_bar = self._bar(cpu_pct, 30)
        lines.append(f" CPU   {cpu_bar} {cpu_pct:5.1f}%  load: {r.load_1m:.2f}")

        # Memory bar
        mem_pct = r.memory_percent
        mem_bar = self._bar(mem_pct, 30)
        lines.append(f" MEM   {mem_bar} {r.memory_str}")

        # Swap bar
        if r.total_swap_mb > 0:
            swap_pct = r.swap_percent
            swap_bar = self._bar(swap_pct, 30)
            lines.append(f" SWAP  {swap_bar} {r.used_swap_mb / 1024:.1f} / {r.total_swap_mb / 1024:.1f} GB")

        # Disk bar
        disk_pct = r.disk_percent
        disk_bar = self._bar(disk_pct, 30)
        lines.append(f" DISK  {disk_bar} {r.disk_str}")

        lines.append(f" I/O   R: {r.io_read_mb:.1f} MB  W: {r.io_write_mb:.1f} MB")
        lines.append(f" Up: {r.uptime_str}  Processes: {self.process_count}  Running: {self.running_count}")

        return lines

    def render_list(self, width: int = 72) -> List[str]:
        """Render process list."""
        lines = []

        # Summary
        lines.extend(self.render_summary(width))
        lines.append("─" * width)

        # Column header
        sort_marker = " ▲" if self._sort_reverse else " ▼"
        header = f" {'PID':>7}  {'User':<8} {'CPU%':>6} {'MEM%':>6} {'MEM':>8} {'Status':<4} {'Name'}"
        lines.append(header[:width])
        lines.append("─" * width)

        # Processes
        procs = self.get_processes()
        for i, p in enumerate(procs):
            marker = "▸" if i == self._selected_index else " "
            cpu_spark = self.sparkline(p.cpu_history[-10:], 8)
            line = (
                f"{marker} {p.pid:>7}  {p.user:<8} "
                f"{p.cpu_percent:>5.1f}% {p.memory_percent:>5.1f}% "
                f"{p.memory_str:>8} {p.status_icon} {p.name}"
            )
            lines.append(line[:width])

        lines.append("─" * width)
        lines.append(f" {self.process_count} processes | ↑↓:Select  Enter:Detail  K:Kill  S:Sort  /:Filter")
        return lines

    def render_detail(self, width: int = 72) -> List[str]:
        """Render process detail view."""
        p = self._detail_process
        if not p:
            return ["No process selected"]

        lines = []
        lines.append(f" 📊 {p.name} (PID {p.pid})")
        lines.append("─" * width)

        # Basic info
        lines.append(f"  Command: {p.command}")
        lines.append(f"  User:    {p.user}")
        lines.append(f"  Status:  {p.status_icon} {p.status.value}")
        lines.append(f"  Parent:  {p.parent_pid}")
        lines.append(f"  Threads: {p.threads}")
        lines.append(f"  Nice:    {p.nice_str}")
        lines.append(f"  Uptime:  {p.uptime_str}")
        lines.append(f"  Files:   {p.open_files} open")
        lines.append("")

        # Resource usage
        lines.append("  ── CPU ──")
        cpu_spark = self.sparkline(p.cpu_history, 40)
        lines.append(f"  Current: {p.cpu_str}")
        lines.append(f"  History: {cpu_spark}")
        lines.append("")

        lines.append("  ── Memory ──")
        mem_spark = self.sparkline(p.mem_history, 40)
        lines.append(f"  Current: {p.memory_str} ({p.memory_percent:.1f}%)")
        lines.append(f"  Virtual: {p.virtual_mb:.0f} MB")
        lines.append(f"  History: {mem_spark}")
        lines.append("")

        lines.append("  ── I/O ──")
        lines.append(f"  Read:  {p.io_read_mb:.1f} MB")
        lines.append(f"  Write: {p.io_write_mb:.1f} MB")

        lines.append("")
        lines.append("─" * width)
        lines.append(" K:Kill  P:Priority  Esc:Back")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == "detail":
            return self.render_detail(width)
        return self.render_list(width)

    def _bar(self, percent: float, width: int = 30) -> str:
        """Render a progress bar."""
        filled = int(min(100, percent) / 100 * width)
        empty = width - filled
        if percent > 90:
            color_bar = "█" * filled + "░" * empty
        elif percent > 70:
            color_bar = "█" * filled + "░" * empty
        else:
            color_bar = "█" * filled + "░" * empty
        return f"[{color_bar}]"

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "detail":
            return self._handle_detail_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_detail()
            return "detail"
        elif key == "k" or key == "K":
            p = self.get_selected_process()
            if p:
                self.confirm_kill(p.pid)
            return "kill_confirm"
        elif key == "s" or key == "S":
            fields = [SortField.CPU, SortField.MEMORY, SortField.PID, SortField.NAME]
            idx = fields.index(self._sort_field) if self._sort_field in fields else 0
            self.set_sort(fields[(idx + 1) % len(fields)])
            return "sort"
        elif key == "/":
            return "filter"
        elif key == "g":
            groups = [ProcessGroup.ALL, ProcessGroup.USER, ProcessGroup.SYSTEM, ProcessGroup.APPS]
            idx = groups.index(self._group_mode)
            self.set_group(groups[(idx + 1) % len(groups)])
            return "group"
        return None

    def _handle_detail_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.close_detail()
            return "back"
        elif key == "k" or key == "K":
            if self._detail_process:
                self.confirm_kill(self._detail_process.pid)
            return "kill_confirm"
        elif key == "p":
            if self._detail_process:
                self._detail_process.nice = max(-20, self._detail_process.nice - 1)
            return "priority"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_kill(self, cb: Callable) -> None:
        self._on_kill.append(cb)

    def _notify(self, event: str, *args) -> None:
        if event == "kill":
            for cb in self._on_kill:
                try:
                    cb(*args)
                except Exception:
                    pass
