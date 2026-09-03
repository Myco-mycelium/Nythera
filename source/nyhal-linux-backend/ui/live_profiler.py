"""
Nyrqis Live Profiler — real-time system profiling application.

Features:
- Live CPU/memory/disk/network graphs with sparklines
- Process tree view with hierarchy
- Per-process resource usage
- System load averages
- Real-time network connections
- File descriptor tracking
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class ProcessState(Enum):
    RUNNING = "R"
    SLEEPING = "S"
    STOPPED = "T"
    ZOMBIE = "Z"
    IDLE = "I"


STATE_ICONS = {
    ProcessState.RUNNING: "🟢",
    ProcessState.SLEEPING: "💤",
    ProcessState.STOPPED: "⏹️",
    ProcessState.ZOMBIE: "👻",
    ProcessState.IDLE: "😴",
}


@dataclass
class ProcessInfo:
    """A system process."""
    pid: int
    name: str
    state: ProcessState = ProcessState.SLEEPING
    user: str = "user"
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    mem_mb: int = 0
    threads: int = 1
    parent_pid: int = 0
    # I/O
    read_bytes: int = 0
    write_bytes: int = 0
    #_FDs
    open_fds: int = 0
    # Timing
    cpu_time: float = 0.0
    start_time: float = 0.0
    # History for sparklines
    cpu_history: List[float] = field(default_factory=list)
    mem_history: List[float] = field(default_factory=list)

    @property
    def state_icon(self) -> str:
        return STATE_ICONS.get(self.state, "❓")

    @property
    def display(self) -> str:
        return f"{self.state_icon} {self.pid:>6d} {self.name:<25s} {self.cpu_pct:>5.1f}% {self.mem_pct:>5.1f}% {self.mem_mb:>6d}MB"

    @property
    def cpu_sparkline(self) -> str:
        return self._sparkline(self.cpu_history)

    @property
    def mem_sparkline(self) -> str:
        return self._sparkline(self.mem_history)

    @staticmethod
    def _sparkline(data: List[float]) -> str:
        if not data:
            return "░" * 15
        chars = "▁▂▃▄▅▆▇█"
        max_val = max(data) if max(data) > 0 else 1
        result = ""
        step = max(1, len(data) // 15)
        for i in range(0, min(len(data), 15 * step), step):
            val = data[i]
            idx = int(val / max_val * (len(chars) - 1))
            result += chars[min(idx, len(chars) - 1)]
        return result[:15]

    @property
    def io_str(self) -> str:
        r = self.read_bytes
        w = self.write_bytes
        if r >= 1073741824:
            r_str = f"{r / 1073741824:.1f}G"
        elif r >= 1048576:
            r_str = f"{r / 1048576:.0f}M"
        elif r >= 1024:
            r_str = f"{r / 1024:.0f}K"
        else:
            r_str = f"{r}B"
        if w >= 1073741824:
            w_str = f"{w / 1073741824:.1f}G"
        elif w >= 1048576:
            w_str = f"{w / 1048576:.0f}M"
        elif w >= 1024:
            w_str = f"{w / 1024:.0f}K"
        else:
            w_str = f"{w}B"
        return f"R:{r_str} W:{w_str}"


@dataclass
class SystemLoad:
    """System load information."""
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0
    uptime_seconds: float = 0.0
    total_processes: int = 0
    running_processes: int = 0
    # History
    cpu_history: List[float] = field(default_factory=list)
    mem_history: List[float] = field(default_factory=list)
    net_rx_history: List[float] = field(default_factory=list)
    net_tx_history: List[float] = field(default_factory=list)

    @property
    def uptime_str(self) -> str:
        d = int(self.uptime_seconds // 86400)
        h = int((self.uptime_seconds % 86400) // 3600)
        m = int((self.uptime_seconds % 3600) // 60)
        if d > 0:
            return f"{d}d {h}h {m}m"
        elif h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

    @property
    def load_bar(self) -> str:
        filled = int(min(self.load_1 / 16, 1.0) * 20)
        return "█" * filled + "░" * (20 - filled)


class LiveProfiler:
    """Real-time system profiling for Nyrqis OS."""

    def __init__(self):
        self._processes: List[ProcessInfo] = []
        self._load = SystemLoad()
        self._selected_index: int = 0
        self._view_mode: str = "overview"  # overview, processes, tree, io
        self._sort_by: str = "cpu"  # cpu, mem, pid, name
        self._tree_mode: bool = False

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        random.seed(42)
        now = time.time()
        procs = [
            (1, "systemd", ProcessState.SLEEPING, "root", 0.1, 0.3, 12, 1, 0),
            (234, "nyrqis-compositor", ProcessState.RUNNING, "user", 12.5, 4.8, 1540, 8, 1),
            (456, "nyrqis-shell", ProcessState.SLEEPING, "user", 2.3, 2.1, 680, 4, 1),
            (678, "firefox", ProcessState.SLEEPING, "user", 8.7, 6.2, 2000, 24, 1),
            (901, "code", ProcessState.SLEEPING, "user", 5.2, 4.5, 1450, 18, 1),
            (1123, "Xwayland", ProcessState.SLEEPING, "user", 1.8, 1.5, 480, 3, 234),
            (1345, "pipewire", ProcessState.SLEEPING, "user", 0.5, 0.4, 128, 3, 1),
            (1567, "NetworkManager", ProcessState.SLEEPING, "root", 0.2, 0.3, 96, 2, 1),
            (1789, "sshd", ProcessState.SLEEPING, "root", 0.0, 0.1, 32, 1, 1),
            (2011, "dockerd", ProcessState.SLEEPING, "root", 1.2, 1.8, 580, 12, 1),
            (2233, "containerd", ProcessState.SLEEPING, "root", 0.8, 1.0, 320, 8, 2011),
            (2456, "postgres", ProcessState.SLEEPING, "postgres", 2.1, 3.2, 1024, 6, 1),
            (2678, "redis-server", ProcessState.SLEEPING, "redis", 0.4, 1.5, 480, 2, 1),
            (2890, "nginx", ProcessState.SLEEPING, "www-data", 0.1, 0.2, 64, 4, 1),
            (3012, "python3", ProcessState.RUNNING, "user", 3.5, 2.8, 896, 4, 1),
            (3234, "node", ProcessState.SLEEPING, "user", 1.8, 1.5, 480, 6, 1),
            (3456, "cargo", ProcessState.RUNNING, "user", 15.2, 3.5, 1120, 8, 1),
            (3678, "rustc", ProcessState.RUNNING, "user", 45.0, 5.2, 1664, 12, 3456),
            (3890, "pulseaudio", ProcessState.SLEEPING, "user", 0.3, 0.2, 64, 2, 1),
            (4012, "dbus-daemon", ProcessState.SLEEPING, "dbus", 0.0, 0.1, 24, 1, 1),
        ]
        for pid, name, state, user, cpu, mem, mem_mb, threads, ppid in procs:
            proc = ProcessInfo(
                pid=pid, name=name, state=state, user=user,
                cpu_pct=cpu, mem_pct=mem, mem_mb=mem_mb,
                threads=threads, parent_pid=ppid,
                read_bytes=random.randint(0, 500_000_000),
                write_bytes=random.randint(0, 200_000_000),
                open_fds=random.randint(3, 200),
                cpu_history=[random.uniform(0, cpu * 2) for _ in range(60)],
                mem_history=[random.uniform(mem * 0.8, mem * 1.2) for _ in range(60)],
            )
            self._processes.append(proc)

        self._load = SystemLoad(
            load_1=4.2, load_5=3.8, load_15=3.5,
            uptime_seconds=259200 + 50400,
            total_processes=20, running_processes=3,
            cpu_history=[random.uniform(10, 60) for _ in range(60)],
            mem_history=[random.uniform(40, 60) for _ in range(60)],
            net_rx_history=[random.uniform(1, 50) for _ in range(60)],
            net_tx_history=[random.uniform(0.5, 20) for _ in range(60)],
        )

    def get_sorted_processes(self) -> List[ProcessInfo]:
        procs = list(self._processes)
        if self._sort_by == "cpu":
            procs.sort(key=lambda p: p.cpu_pct, reverse=True)
        elif self._sort_by == "mem":
            procs.sort(key=lambda p: p.mem_mb, reverse=True)
        elif self._sort_by == "pid":
            procs.sort(key=lambda p: p.pid)
        elif self._sort_by == "name":
            procs.sort(key=lambda p: p.name.lower())
        return procs

    def get_tree(self) -> List[Tuple[int, ProcessInfo]]:
        """Get process tree with indentation levels."""
        proc_map = {p.pid: p for p in self._processes}
        tree = []
        visited = set()

        def add_children(parent_pid: int, depth: int):
            children = [p for p in self._processes if p.parent_pid == parent_pid and p.pid not in visited]
            children.sort(key=lambda p: p.pid)
            for child in children:
                visited.add(child.pid)
                tree.append((depth, child))
                add_children(child.pid, depth + 1)

        # Start from root processes
        roots = [p for p in self._processes if p.parent_pid == 0]
        roots.sort(key=lambda p: p.pid)
        for root in roots:
            visited.add(root.pid)
            tree.append((0, root))
            add_children(root.pid, 1)
        return tree

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        procs = self.get_sorted_processes()
        self._selected_index = min(len(procs) - 1, self._selected_index + 1)

    def get_selected_process(self) -> Optional[ProcessInfo]:
        procs = self.get_sorted_processes()
        if 0 <= self._selected_index < len(procs):
            return procs[self._selected_index]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def load(self) -> SystemLoad:
        return self._load

    def render_overview(self, width: int = 70) -> List[str]:
        load = self._load
        chars = "▁▂▃▄▅▆▇█"

        def make_spark(data, w=40):
            if not data:
                return "░" * w
            mx = max(data) if max(data) > 0 else 1
            step = max(1, len(data) // w)
            return "".join(chars[min(int(data[i] / mx * 7), 7)] for i in range(0, min(len(data), w * step), step))[:w]

        lines = []
        lines.append(" 📊 Live Profiler")
        lines.append("─" * width)

        # CPU
        cpu = load.cpu_history[-1] if load.cpu_history else 0
        lines.append(f" 🖥️  CPU:  {cpu:>5.1f}%  [{load.cpu_history[-1] if load.cpu_history else 0:.0f}%]  {make_spark(load.cpu_history)}")
        lines.append(f"     Load: {load.load_1:.1f} / {load.load_5:.1f} / {load.load_15:.1f}  [{load.load_bar}]")

        # Memory
        mem = load.mem_history[-1] if load.mem_history else 0
        lines.append(f" 🧠 RAM:  {mem:>5.1f}%  {make_spark(load.mem_history)}")

        # Network
        rx = load.net_rx_history[-1] if load.net_rx_history else 0
        tx = load.net_tx_history[-1] if load.net_tx_history else 0
        lines.append(f" 📥 Net:  RX {rx:>5.1f} Mbps  TX {tx:>5.1f} Mbps")

        lines.append("─" * width)
        lines.append(f" Processes: {load.total_processes} total, {load.running_processes} running")
        lines.append(f" Uptime: {load.uptime_str}")
        lines.append("")
        lines.append("─" * width)
        lines.append(" P:Processes  T:Tree  I:I/O  S:Sort  Esc:Back")
        return lines

    def render_processes(self, width: int = 80) -> List[str]:
        lines = []
        lines.append(f" 📋 Processes ({len(self._processes)} total, sorted by {self._sort_by})")
        lines.append("─" * width)
        lines.append(f" {'State':>5s} {'PID':>6s} {'Name':<25s} {'CPU%':>5s} {'MEM%':>5s} {'MEM':>7s}")
        lines.append("─" * width)

        procs = self.get_sorted_processes()
        for i, proc in enumerate(procs[:20]):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker}{proc.display}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  S:Sort  Esc:Back")
        return lines

    def render_tree(self, width: int = 80) -> List[str]:
        lines = []
        lines.append(" 🌳 Process Tree")
        lines.append("─" * width)

        tree = self.get_tree()
        for depth, proc in tree[:20]:
            indent = "  " * depth
            connector = "└─" if depth > 0 else ""
            state = STATE_ICONS.get(proc.state, "❓")
            lines.append(f" {indent}{connector}{state} {proc.pid} {proc.name} ({proc.cpu_pct:.1f}% CPU, {proc.mem_mb}MB)")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_io(self, width: int = 80) -> List[str]:
        lines = []
        lines.append(" 💾 I/O Overview")
        lines.append("─" * width)

        procs = sorted(self._processes, key=lambda p: p.read_bytes + p.write_bytes, reverse=True)
        for proc in procs[:15]:
            lines.append(f" {proc.pid:>6d} {proc.name:<20s} {proc.io_str:>20s}  FDs:{proc.open_fds}")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"processes": self.render_processes, "tree": self.render_tree, "io": self.render_io}
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "processes":
            if key == "Escape":
                self.set_view("overview")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            if key == "s":
                sorts = ["cpu", "mem", "pid", "name"]
                idx = sorts.index(self._sort_by) if self._sort_by in sorts else 0
                self._sort_by = sorts[(idx + 1) % len(sorts)]
                return "sort"
            return None
        if self._view_mode in ("tree", "io"):
            if key == "Escape":
                self.set_view("overview")
                return "back"
            return None
        if key == "p":
            self.set_view("processes")
            return "processes"
        if key == "t":
            self.set_view("tree")
            return "tree"
        if key == "i":
            self.set_view("io")
            return "io"
        return None
