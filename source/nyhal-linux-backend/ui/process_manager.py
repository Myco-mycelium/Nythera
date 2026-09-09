"""
Nyrqis OS - Process Manager
Resource limits, priority scheduling, and tree view.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class ProcessState(Enum):
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPED = "stopped"
    ZOMBIE = "zombie"
    IDLE = "idle"


class ProcessPriority(Enum):
    REALTIME = "realtime"
    HIGH = "high"
    ABOVE_NORMAL = "above_normal"
    NORMAL = "normal"
    BELOW_NORMAL = "below_normal"
    LOW = "low"
    IDLE = "idle"


@dataclass
class Process:
    pid: int
    name: str
    state: ProcessState = ProcessState.RUNNING
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    user: str = "root"
    uid: int = 0
    ppid: int = 0
    priority: ProcessPriority = ProcessPriority.NORMAL
    nice_value: int = 0
    threads: int = 1
    open_files: int = 0
    uptime_s: float = 0.0
    cmdline: str = ""
    start_time: float = 0.0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    children: List[int] = field(default_factory=list)

    @property
    def state_icon(self) -> str:
        icons = {
            ProcessState.RUNNING: "🟢",
            ProcessState.SLEEPING: "💤",
            ProcessState.STOPPED: "⏸",
            ProcessState.ZOMBIE: "🧟",
            ProcessState.IDLE: "⚪",
        }
        return icons.get(self.state, "?")

    @property
    def nice(self) -> int:
        """Alias for nice_value (spec API)."""
        return self.nice_value

    @nice.setter
    def nice(self, value: int) -> None:
        self.nice_value = value

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_bar(self) -> str:
        filled = int(self.memory_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def priority_icon(self) -> str:
        icons = {
            ProcessPriority.REALTIME: "🔴",
            ProcessPriority.HIGH: "🟠",
            ProcessPriority.ABOVE_NORMAL: "🟡",
            ProcessPriority.NORMAL: "🟢",
            ProcessPriority.BELOW_NORMAL: "⚪",
            ProcessPriority.LOW: "🔵",
            ProcessPriority.IDLE: "⚫",
        }
        return icons.get(self.priority, "?")

    @property
    def uptime_display(self) -> str:
        if self.uptime_s < 60:
            return f"{self.uptime_s:.0f}s"
        elif self.uptime_s < 3600:
            return f"{self.uptime_s / 60:.1f}m"
        elif self.uptime_s < 86400:
            return f"{self.uptime_s / 3600:.1f}h"
        return f"{self.uptime_s / 86400:.1f}d"

    @property
    def io_read_display(self) -> str:
        if self.io_read_bytes < 1024:
            return f"{self.io_read_bytes} B"
        elif self.io_read_bytes < 1024 * 1024:
            return f"{self.io_read_bytes / 1024:.1f} KB"
        elif self.io_read_bytes < 1024 * 1024 * 1024:
            return f"{self.io_read_bytes / (1024 * 1024):.1f} MB"
        return f"{self.io_read_bytes / (1024 * 1024 * 1024):.2f} GB"

    @property
    def io_write_display(self) -> str:
        if self.io_write_bytes < 1024:
            return f"{self.io_write_bytes} B"
        elif self.io_write_bytes < 1024 * 1024:
            return f"{self.io_write_bytes / 1024:.1f} KB"
        elif self.io_write_bytes < 1024 * 1024 * 1024:
            return f"{self.io_write_bytes / (1024 * 1024):.1f} MB"
        return f"{self.io_write_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class ResourceLimit:
    pid: int
    process_name: str = ""
    max_memory_mb: float = 0.0
    max_cpu_percent: float = 100.0
    max_open_files: int = 1024
    max_threads: int = 256
    oom_score_adj: int = 0
    io_weight: int = 100
    cpu_shares: int = 1024
    cgroup_path: str = ""


@dataclass
class ProcessGroup:
    name: str
    pids: List[int] = field(default_factory=list)
    total_cpu: float = 0.0
    total_memory_mb: float = 0.0
    process_count: int = 0
    description: str = ""

    # Group-filter pseudo-groups (spec API): USER = non-root, SYSTEM = root
    USER = "user"
    SYSTEM = "system"


@dataclass
class SystemResources:
    total_cpu_percent: float = 0.0
    total_memory_gb: float = 0.0
    used_memory_gb: float = 0.0
    swap_total_gb: float = 0.0
    swap_used_gb: float = 0.0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    uptime_hours: float = 0.0
    total_processes: int = 0
    running_processes: int = 0
    sleeping_processes: int = 0
    zombie_processes: int = 0
    total_memory_mb: float = 0.0
    used_memory_mb: float = 0.0
    total_disk_gb: float = 0.0
    used_disk_gb: float = 0.0
    uptime_seconds: float = 0.0
    disk_read_bytes: float = 0.0
    disk_write_bytes: float = 0.0

    @property
    def memory_str(self) -> str:
        total = self.total_memory_mb or (self.total_memory_gb * 1024)
        used = self.used_memory_mb or (self.used_memory_gb * 1024)
        if total >= 1024:
            return f"{used / 1024:.1f} / {total / 1024:.1f} GB"
        return f"{used:.0f} / {total:.0f} MB"

    @property
    def memory_percent(self) -> float:
        total = self.total_memory_mb or (self.total_memory_gb * 1024)
        used = self.used_memory_mb or (self.used_memory_gb * 1024)
        return (used / total * 100) if total > 0 else 0.0

    @property
    def disk_percent(self) -> float:
        return (self.used_disk_gb / self.total_disk_gb * 100) if self.total_disk_gb > 0 else 0.0

    @property
    def uptime_str(self) -> str:
        seconds = self.uptime_seconds or (self.uptime_hours * 3600)
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.0f}m"
        elif seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        days = seconds / 86400
        return f"{days:.0f}d {seconds % 86400 / 3600:.0f}h"


class ProcessManager:
    def __init__(self):
        self.processes: List[Process] = []
        self.resource_limits: List[ResourceLimit] = []
        self.groups: List[ProcessGroup] = []
        self.system = SystemResources()
        self.selected_pid: Optional[int] = None
        self.sort_by: str = "cpu_percent"
        self.sort_reverse: bool = True
        self.filter_user: str = ""
        self.filter_state: Optional[ProcessState] = None
        self.show_tree: bool = True
        # Spec-API state
        self._view_mode: str = "list"
        self._selected_index: int = 0
        self._confirm_kill_target: Optional[Process] = None
        self._filter_text: str = ""
        self._group_filter = None
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_processes = [
            (1, "systemd", ProcessState.RUNNING, 0.1, 12.5, 0.2, "root", 0, 0,
             ProcessPriority.NORMAL, 0, 4, 15, 86400, "/sbin/init"),
            (2, "nyrqis-compositor", ProcessState.RUNNING, 35.2, 256.0, 3.9, "root", 0, 1,
             ProcessPriority.HIGH, -5, 8, 45, 7200, "/usr/bin/nyrqis-compositor --wayland"),
            (3, "nyrqis-shell", ProcessState.RUNNING, 12.8, 128.0, 1.9, "user", 1000, 2,
             ProcessPriority.NORMAL, 0, 4, 32, 7200, "/usr/bin/nyrqis-shell"),
            (4, "wayland-bridge", ProcessState.RUNNING, 8.5, 64.0, 0.9, "root", 0, 2,
             ProcessPriority.NORMAL, 0, 2, 18, 7200, "/usr/libexec/wayland-bridge"),
            (5, "Xwayland", ProcessState.RUNNING, 2.1, 45.0, 0.6, "root", 0, 4,
             ProcessPriority.NORMAL, 0, 1, 8, 7200, "Xwayland"),
            (100, "dbus-daemon", ProcessState.SLEEPING, 0.0, 8.0, 0.1, "user", 1000, 1,
             ProcessPriority.NORMAL, 0, 1, 5, 86400, "dbus-daemon --system"),
            (101, "NetworkManager", ProcessState.SLEEPING, 0.2, 15.0, 0.2, "root", 0, 100,
             ProcessPriority.NORMAL, 0, 1, 12, 86400, "NetworkManager"),
            (102, "sshd", ProcessState.SLEEPING, 0.0, 5.0, 0.0, "root", 0, 1,
             ProcessPriority.NORMAL, 0, 1, 3, 86400, "/usr/sbin/sshd"),
            (200, "firefox", ProcessState.RUNNING, 18.5, 1024.0, 15.4, "user", 1000, 2,
             ProcessPriority.BELOW_NORMAL, 5, 45, 128, 3600, "firefox"),
            (201, "firefox-content", ProcessState.RUNNING, 5.2, 512.0, 7.7, "user", 1000, 200,
             ProcessPriority.BELOW_NORMAL, 5, 12, 64, 3600, "firefox-content"),
            (202, "firefox-gpu", ProcessState.RUNNING, 3.1, 256.0, 3.9, "user", 1000, 200,
             ProcessPriority.BELOW_NORMAL, 5, 2, 8, 3600, "firefox-gpu-process"),
            (300, "code-server", ProcessState.RUNNING, 8.0, 384.0, 5.8, "user", 1000, 3,
             ProcessPriority.NORMAL, 0, 16, 42, 1800, "code-server"),
            (301, "node", ProcessState.RUNNING, 4.5, 192.0, 2.9, "user", 1000, 300,
             ProcessPriority.NORMAL, 0, 8, 24, 1800, "node /usr/lib/code-server/lib/vscode/out/main.js"),
            (400, "pulseaudio", ProcessState.SLEEPING, 0.1, 24.0, 0.3, "user", 1000, 2,
             ProcessPriority.NORMAL, 0, 3, 8, 86400, "pulseaudio"),
            (500, "cron", ProcessState.SLEEPING, 0.0, 3.0, 0.0, "root", 0, 1,
             ProcessPriority.LOW, 10, 1, 2, 86400, "/usr/sbin/cron"),
            (600, "python3", ProcessState.SLEEPING, 0.3, 48.0, 0.7, "user", 1000, 3,
             ProcessPriority.NORMAL, 0, 4, 6, 600, "python3 /opt/nyrqis/tools/monitor.py"),
            (700, "containerd", ProcessState.SLEEPING, 0.5, 64.0, 0.9, "root", 0, 1,
             ProcessPriority.NORMAL, 0, 8, 16, 86400, "containerd"),
            (701, "docker-proxy", ProcessState.SLEEPING, 0.0, 16.0, 0.2, "root", 0, 700,
             ProcessPriority.NORMAL, 0, 2, 4, 43200, "docker-proxy"),
            (800, "thermald", ProcessState.SLEEPING, 0.1, 8.0, 0.1, "root", 0, 1,
             ProcessPriority.LOW, 10, 1, 3, 86400, "thermald"),
        ]
        for (pid, name, state, cpu, mem, mem_pct, user, uid, ppid,
             prio, nice, threads, files, uptime, cmd) in sample_processes:
            self.processes.append(Process(
                pid=pid, name=name, state=state, cpu_percent=cpu, memory_mb=mem,
                memory_percent=mem_pct, user=user, uid=uid, ppid=ppid,
                priority=prio, nice_value=nice, threads=threads, open_files=files,
                uptime_s=uptime + random.uniform(-60, 60), cmdline=cmd,
                start_time=now - uptime,
                io_read_bytes=random.randint(1024, 1024 * 1024 * 100),
                io_write_bytes=random.randint(512, 1024 * 1024 * 50),
            ))
        self.processes[3].children = [4, 5]

        self.groups = [
            ProcessGroup(name="Nyrqis Core", pids=[2, 3, 4, 5],
                         description="Nyrqis OS compositor, shell, and Wayland"),
            ProcessGroup(name="Browsers", pids=[200, 201, 202],
                         description="Firefox and content processes"),
            ProcessGroup(name="Development", pids=[300, 301, 600],
                         description="Code server, Node.js, Python"),
            ProcessGroup(name="System Services", pids=[100, 101, 102, 500, 800],
                         description="DBus, NetworkManager, SSH, Cron, Thermald"),
        ]

        self.resource_limits = [
            ResourceLimit(pid=2, process_name="nyrqis-compositor",
                          max_memory_mb=1024, max_cpu_percent=50, oom_score_adj=-1000),
            ResourceLimit(pid=200, process_name="firefox",
                          max_memory_mb=4096, max_cpu_percent=80, max_open_files=2048),
            ResourceLimit(pid=300, process_name="code-server",
                          max_memory_mb=2048, max_cpu_percent=60),
        ]

        self.system = SystemResources(
            total_cpu_percent=81.2, total_memory_gb=64.0, used_memory_gb=28.3,
            swap_total_gb=8.0, swap_used_gb=0.5,
            load_1m=4.2, load_5m=3.8, load_15m=3.5,
            uptime_hours=72.0, total_processes=len(self.processes),
            running_processes=sum(1 for p in self.processes if p.state == ProcessState.RUNNING),
            sleeping_processes=sum(1 for p in self.processes if p.state == ProcessState.SLEEPING),
        )

    def get_tree_view(self) -> List[Tuple[int, Process]]:
        result = []
        def add_children(ppid, depth):
            children = [p for p in self.processes if p.ppid == ppid]
            children.sort(key=lambda p: getattr(p, self.sort_by, 0), reverse=self.sort_reverse)
            for child in children:
                result.append((depth, child))
                add_children(child.pid, depth + 1)
        roots = [p for p in self.processes if p.ppid == 0]
        roots.sort(key=lambda p: getattr(p, self.sort_by, 0), reverse=self.sort_reverse)
        for root in roots:
            result.append((0, root))
            add_children(root.pid, 1)
        return result

    def get_filtered_processes(self) -> List[Process]:
        procs = self.processes
        if self.filter_user:
            procs = [p for p in procs if p.user == self.filter_user]
        if self.filter_state:
            procs = [p for p in procs if p.state == self.filter_state]
        if self._filter_text:
            q = self._filter_text.lower()
            procs = [p for p in procs if q in p.name.lower()]
        if self._group_filter is not None:
            gf = self._group_filter
            name = getattr(gf, "value", gf) if not isinstance(gf, str) else gf
            if str(name).upper() == "USER":
                procs = [p for p in procs if p.user != "root"]
            elif str(name).upper() == "SYSTEM":
                procs = [p for p in procs if p.user == "root"]
        procs = list(procs)
        procs.sort(key=lambda p: getattr(p, self.sort_by, 0), reverse=self.sort_reverse)
        return procs

    def kill_process(self, pid: int, signal: str = "TERM") -> bool:
        idx = next((i for i, p in enumerate(self.processes) if p.pid == pid), None)
        if idx is not None:
            del self.processes[idx]
            return True
        return False

    def set_priority(self, pid: int, priority: ProcessPriority) -> bool:
        proc = next((p for p in self.processes if p.pid == pid), None)
        if proc:
            proc.priority = priority
            priority_nice = {
                ProcessPriority.REALTIME: -20, ProcessPriority.HIGH: -10,
                ProcessPriority.ABOVE_NORMAL: -5, ProcessPriority.NORMAL: 0,
                ProcessPriority.BELOW_NORMAL: 5, ProcessPriority.LOW: 10,
                ProcessPriority.IDLE: 19,
            }
            proc.nice_value = priority_nice.get(priority, 0)
            return True
        return False

    def set_resource_limit(self, pid: int, **kwargs) -> bool:
        limit = next((l for l in self.resource_limits if l.pid == pid), None)
        if not limit:
            proc = next((p for p in self.processes if p.pid == pid), None)
            if not proc:
                return False
            limit = ResourceLimit(pid=pid, process_name=proc.name)
            self.resource_limits.append(limit)
        for k, v in kwargs.items():
            if hasattr(limit, k):
                setattr(limit, k, v)
        return True

    def get_process(self, pid: int) -> Optional[Process]:
        return next((p for p in self.processes if p.pid == pid), None)

    def get_group_stats(self) -> List[Dict]:
        result = []
        for group in self.groups:
            procs = [p for p in self.processes if p.pid in group.pids]
            result.append({
                "name": group.name,
                "description": group.description,
                "process_count": len(procs),
                "total_cpu": round(sum(p.cpu_percent for p in procs), 1),
                "total_memory_mb": round(sum(p.memory_mb for p in procs), 1),
            })
        return result

    def get_top_cpu(self, limit: int = 5) -> List[Process]:
        return sorted(self.processes, key=lambda p: p.cpu_percent, reverse=True)[:limit]

    def get_top_memory(self, limit: int = 5) -> List[Process]:
        return sorted(self.processes, key=lambda p: p.memory_mb, reverse=True)[:limit]

    # ─── Spec API (test_process_weather_disk) ─────────────────────
    @property
    def process_count(self) -> int:
        return len(self.processes)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @view_mode.setter
    def view_mode(self, value: str) -> None:
        self._view_mode = value

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def confirm_kill_target(self) -> Optional[Process]:
        return self._confirm_kill_target

    def get_processes(self) -> List[Process]:
        """Processes visible under the current sort/filter/group."""
        return self.get_filtered_processes()

    def set_sort(self, field) -> None:
        """Sort by a SortField enum (or attribute name)."""
        key = field.value if isinstance(field, _Enum) else str(field)
        attr_map = {
            "pid": "pid", "name": "name", "cpu": "cpu_percent",
            "memory": "memory_percent", "disk": "io_read_bytes",
            "network": "io_write_bytes", "threads": "threads", "user": "user",
        }
        self.sort_by = attr_map.get(key, key)
        self.sort_reverse = key != "name"

    def set_filter(self, text: str) -> None:
        """Filter by process name substring."""
        self._filter_text = text

    def set_group(self, group) -> None:
        """Filter by ProcessGroup-style grouping (USER shows non-root, SYSTEM shows root)."""
        self._group_filter = group

    def set_nice(self, pid: int, nice: int) -> bool:
        proc = self.get_process(pid)
        if proc:
            proc.nice_value = nice
            return True
        return False

    def confirm_kill(self, pid: int) -> Optional[Process]:
        """Arm the kill confirmation for a pid."""
        proc = self.get_process(pid)
        self._confirm_kill_target = proc
        return proc

    def execute_kill(self) -> bool:
        """Kill the armed target and disarm."""
        if self._confirm_kill_target is None:
            return False
        pid = self._confirm_kill_target.pid
        self._confirm_kill_target = None
        return self.kill_process(pid, "KILL")

    def cancel_kill(self) -> None:
        self._confirm_kill_target = None

    def open_detail(self, pid: int) -> Optional[Process]:
        proc = self.get_process(pid)
        if proc:
            self.selected_pid = pid
            self._view_mode = "detail"
        return proc

    def close_detail(self) -> None:
        self._view_mode = "list"
        self.selected_pid = None

    def select(self, idx: int) -> int:
        self._selected_index = max(0, idx)
        return self._selected_index

    def select_up(self) -> int:
        if self._selected_index > 0:
            self._selected_index -= 1
        return self._selected_index

    def select_down(self) -> int:
        if self._selected_index < len(self.get_processes()) - 1:
            self._selected_index += 1
        return self._selected_index

    @staticmethod
    def sparkline(values, width: int = 20) -> str:
        """Tiny inline bar chart using block characters."""
        if not values:
            return "░" * width
        blocks = "▁▂▃▄▅▆▇█"
        lo, hi = min(values), max(values)
        rng = (hi - lo) or 1.0
        step = len(values) / width if len(values) > width else 1
        chars = []
        for i in range(width):
            v = values[min(int(i * step), len(values) - 1)] if step > 1 else (
                values[i] if i < len(values) else values[-1])
            idx = int((v - lo) / rng * (len(blocks) - 1))
            chars.append(blocks[idx])
        return "".join(chars)

    def update_processes(self) -> None:
        """Refresh sample metrics (mock of a real /proc poll)."""
        for p in self.processes:
            if p.state == ProcessState.RUNNING:
                p.cpu_percent = max(0.0, p.cpu_percent + random.uniform(-1.5, 1.5))

    def handle_key(self, key: str) -> str:
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        if key == "s":
            return "sort"
        if key == "Enter":
            procs = self.get_processes()
            if procs and self._selected_index < len(procs):
                self.open_detail(procs[self._selected_index].pid)
                return "open_detail"
            return "open_detail"
        if key == "Escape":
            if self._view_mode == "detail":
                self.close_detail()
                return "close_detail"
            return ""
        return ""

    def render_summary(self) -> List[str]:
        r = self.system
        lines = ["SYSTEM SUMMARY", "=" * 40]
        lines.append(f"CPU: {r.total_cpu_percent:.1f}%  Memory: {r.memory_str}")
        lines.append(f"Processes: {len(self.processes)} total, "
                     f"{r.running_processes} running, {r.zombie_processes} zombie")
        lines.append(f"Load: {r.load_1m:.2f} {r.load_5m:.2f} {r.load_15m:.2f}  "
                     f"Uptime: {r.uptime_str}")
        return lines

    def render_list(self) -> List[str]:
        lines = ["PROCESS MANAGER", "=" * 40]
        for i, p in enumerate(self.get_processes()):
            marker = " > " if i == self._selected_index else "   "
            lines.append(f"{marker}{p.state_icon} {p.pid:>6} {p.name:<24} "
                         f"{p.cpu_percent:5.1f}% {p.memory_mb:8.1f} MB {p.user}")
        return lines

    def render_detail(self) -> List[str]:
        proc = self.get_process(self.selected_pid) if self.selected_pid else None
        if not proc:
            return ["No process selected"]
        lines = [f"Process {proc.pid}: {proc.name}", "=" * 40]
        lines.append(f"State: {proc.state.value} {proc.state_icon}")
        lines.append(f"User: {proc.user} (uid {proc.uid})")
        lines.append(f"CPU: {proc.cpu_percent:.1f}%  Memory: {proc.memory_mb:.1f} MB")
        lines.append(f"Threads: {proc.threads}  Open files: {proc.open_files}")
        lines.append(f"Nice: {proc.nice_value}  Priority: {proc.priority.value}")
        if proc.cmdline:
            lines.append(f"Command: {proc.cmdline}")
        return lines

    def render(self) -> List[str]:
        if self._view_mode == "detail":
            return self.render_detail()
        return self.render_summary() + [""] + self.render_list()


@dataclass
class ProcessInfo:
    pid: int = 0
    name: str = ""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    status: str = ""
    nice: int = 0
    user: str = ""
    cpu_time: float = 0.0
    memory_percent: float = 0.0
    disk_read: float = 0.0
    disk_write: float = 0.0
    net_sent: float = 0.0
    net_recv: float = 0.0
    threads: int = 1
    command: str = ""
    start_time: float = 0.0

    def __post_init__(self):
        self.cpu_history: List[float] = []

    @property
    def memory_str(self) -> str:
        if self.memory_mb >= 1024:
            return f"{self.memory_mb / 1024:.1f} GB"
        return f"{self.memory_mb:.0f} MB"

    @property
    def status_icon(self) -> str:
        icons = {
            "running": "🟢", "sleeping": "💤", "stopped": "⏸",
            "zombie": "🧟", "idle": "⚪",
        }
        status = self.status
        # Accept ProcessState/ProcessStatus enums as well as plain strings
        if not isinstance(status, str):
            status = getattr(status, "value", str(status))
        return icons.get(str(status).lower(), "?")

    @property
    def uptime_str(self) -> str:
        if self.start_time <= 0:
            return "N/A"
        import time
        delta = time.time() - self.start_time
        if delta < 60:
            return f"{delta:.0f}s"
        elif delta < 3600:
            return f"{delta / 60:.0f}m"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h"
        return f"{delta / 86400:.1f}d"

    @property
    def nice_str(self) -> str:
        if self.nice == 0:
            return "NORMAL (0)"
        elif self.nice < 0:
            return f"HIGH ({self.nice})"
        return f"LOW (+{self.nice})"

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_bar(self) -> str:
        filled = int(self.memory_percent / 5) if self.memory_percent else int(self.memory_mb / 10)
        return "█" * min(filled, 20) + "░" * max(0, 20 - filled)

    @property
    def disk_bar(self) -> str:
        total = self.disk_read + self.disk_write
        filled = int(total / 1024 / 5)
        return "█" * min(filled, 20) + "░" * max(0, 20 - filled)

    @property
    def net_bar(self) -> str:
        total = self.net_sent + self.net_recv
        filled = int(total / 1024 / 5)
        return "█" * min(filled, 20) + "░" * max(0, 20 - filled)

    def update_history(self) -> None:
        """Append the current CPU usage to the rolling history."""
        self.cpu_history.append(self.cpu_percent)

ProcessStatus = ProcessState

# ─── Backward-compat exports ────────────────────────────────────────────
from enum import Enum as _Enum

class SortField(_Enum):
    PID = "pid"
    NAME = "name"
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    THREADS = "threads"
    USER = "user"
