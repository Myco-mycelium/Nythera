#!/usr/bin/env python3
"""system_monitor — Nyrqis real-time system monitor.

A full system monitoring panel with live graphs:

- CPU usage per-core with history graph
- Memory usage (used/cached/buffers/swap)
- Disk usage per mount with I/O stats
- Network usage (sent/received) with history
- Process list sorted by CPU/memory
- Temperature sensors (if available)
- Uptime and load average
- Compact and expanded view modes
- Historical data buffer for sparkline graphs

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import platform
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class MonitorView(Enum):
    """Monitor display modes."""
    COMPACT = "compact"      # Small widget in taskbar
    OVERVIEW = "overview"    # Single-page summary
    DETAILED = "detailed"    # Full multi-tab view


@dataclass
class CpuInfo:
    """Per-core CPU information."""
    core_id: int
    usage: float = 0.0      # 0-100%
    frequency: float = 0.0   # MHz
    temperature: float = 0.0 # Celsius (0 = unavailable)
    history: List[float] = field(default_factory=list)


@dataclass
class MemoryInfo:
    """Memory usage information."""
    total_mb: float = 0.0
    used_mb: float = 0.0
    available_mb: float = 0.0
    buffers_mb: float = 0.0
    cached_mb: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    usage_percent: float = 0.0
    history: List[float] = field(default_factory=list)


@dataclass
class DiskInfo:
    """Disk partition information."""
    device: str
    mount_point: str
    fs_type: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    usage_percent: float = 0.0
    read_speed: float = 0.0   # MB/s
    write_speed: float = 0.0  # MB/s


@dataclass
class NetworkInfo:
    """Network interface information."""
    interface: str
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_speed: float = 0.0   # KB/s
    tx_speed: float = 0.0   # KB/s
    rx_history: List[float] = field(default_factory=list)
    tx_history: List[float] = field(default_factory=list)
    ip_address: str = ""
    is_up: bool = True


@dataclass
class ProcessInfo:
    """Process information."""
    pid: int
    name: str
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    status: str = "R"
    user: str = ""


@dataclass
class SystemInfo:
    """System overview information."""
    hostname: str = ""
    os_name: str = ""
    kernel: str = ""
    architecture: str = ""
    uptime_seconds: float = 0.0
    load_avg: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    cpu_count: int = 0
    boot_time: float = 0.0


# ---------------------------------------------------------------------------
# System monitor
# ---------------------------------------------------------------------------

class SystemMonitor:
    """Real-time system monitor.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    history_size : int
        Number of data points to keep in history buffers.
    update_interval : float
        Minimum seconds between data refreshes.
    """

    def __init__(
        self,
        session=None,
        history_size: int = 60,
        update_interval: float = 1.0,
    ) -> None:
        self._session = session
        self._history_size = history_size
        self._update_interval = update_interval

        # State
        self._view: MonitorView = MonitorView.OVERVIEW
        self._visible: bool = False
        self._last_update: float = 0.0
        self._callbacks: List[Callable] = []

        # Data
        self._system = SystemInfo()
        self._cpu_cores: List[CpuInfo] = []
        self._memory = MemoryInfo()
        self._disks: List[DiskInfo] = []
        self._networks: List[NetworkInfo] = []
        self._processes: List[ProcessInfo] = []
        self._temperatures: Dict[str, float] = {}

        # Previous CPU times for delta calculation
        self._prev_cpu_times: List[Tuple[int, int]] = []
        self._prev_net_bytes: Dict[str, Tuple[int, int]] = {}
        self._prev_disk_io: Dict[str, Tuple[int, int]] = {}

        # View state
        self._selected_tab: str = "overview"
        self._process_sort_key: str = "cpu"
        self._process_reverse: bool = True
        self._scroll_offset: int = 0

    # -- Public API ----------------------------------------------------

    def show(self) -> None:
        """Show the system monitor."""
        self._visible = True
        self.update()
        self._dispatch("shown")

    def hide(self) -> None:
        """Hide the system monitor."""
        self._visible = False
        self._dispatch("hidden")

    def toggle(self) -> bool:
        """Toggle visibility."""
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    def update(self) -> bool:
        """Refresh all system data.

        Returns True if data was actually refreshed.
        """
        now = time.time()
        if now - self._last_update < self._update_interval:
            return False

        self._last_update = now
        self._read_system_info()
        self._read_cpu()
        self._read_memory()
        self._read_disk()
        self._read_network()
        self._read_processes()
        self._read_temperatures()
        self._dispatch("updated")
        return True

    # -- Tab/view navigation -------------------------------------------

    def set_view(self, view: MonitorView) -> None:
        self._view = view

    def set_tab(self, tab: str) -> None:
        """Switch between overview, cpu, memory, disk, network, processes."""
        self._selected_tab = tab

    @property
    def process_reverse(self) -> bool:
        return self._process_reverse

    def sort_processes(self, key: str) -> None:
        """Sort processes by key (cpu, memory, name, pid)."""
        if self._process_sort_key == key:
            self._process_reverse = not self._process_reverse
        else:
            self._process_sort_key = key
            self._process_reverse = True

    def scroll(self, delta: int) -> None:
        """Scroll the process list."""
        self._scroll_offset = max(0, self._scroll_offset + delta)

    # -- Data access ---------------------------------------------------

    @property
    def system(self) -> SystemInfo:
        return self._system

    @property
    def cpu_cores(self) -> List[CpuInfo]:
        return list(self._cpu_cores)

    @property
    def cpu_overall(self) -> float:
        """Overall CPU usage across all cores."""
        if not self._cpu_cores:
            return 0.0
        return sum(c.usage for c in self._cpu_cores) / len(self._cpu_cores)

    @property
    def memory(self) -> MemoryInfo:
        return self._memory

    @property
    def disks(self) -> List[DiskInfo]:
        return list(self._disks)

    @property
    def networks(self) -> List[NetworkInfo]:
        return list(self._networks)

    @property
    def processes(self) -> List[ProcessInfo]:
        """Get sorted process list."""
        procs = list(self._processes)
        reverse = self._process_reverse
        if self._process_sort_key == "cpu":
            procs.sort(key=lambda p: p.cpu_percent, reverse=reverse)
        elif self._process_sort_key == "memory":
            procs.sort(key=lambda p: p.memory_mb, reverse=reverse)
        elif self._process_sort_key == "name":
            procs.sort(key=lambda p: p.name.lower(), reverse=reverse)
        elif self._process_sort_key == "pid":
            procs.sort(key=lambda p: p.pid, reverse=reverse)
        return procs

    @property
    def temperatures(self) -> Dict[str, float]:
        return dict(self._temperatures)

    @property
    def uptime(self) -> str:
        """Formatted uptime string."""
        secs = self._system.uptime_seconds
        if secs <= 0:
            return "unknown"
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    # -- System data reading -------------------------------------------

    def _read_system_info(self) -> None:
        """Read basic system information."""
        self._system.hostname = platform.node()
        self._system.os_name = platform.system()
        self._system.kernel = platform.release()
        self._system.architecture = platform.machine()
        self._system.cpu_count = os.cpu_count() or 1

        # Boot time and uptime from /proc
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if line.startswith("btime"):
                        self._system.boot_time = float(line.split()[1])
                        break
            self._system.uptime_seconds = time.time() - self._system.boot_time
        except (OSError, IndexError, ValueError):
            self._system.uptime_seconds = 0.0

        # Load average
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                self._system.load_avg = (
                    float(parts[0]), float(parts[1]), float(parts[2]))
        except (OSError, IndexError):
            self._system.load_avg = (0.0, 0.0, 0.0)

    def _read_cpu(self) -> None:
        """Read CPU usage from /proc/stat."""
        try:
            with open("/proc/stat") as f:
                lines = f.readlines()
        except OSError:
            return

        cores = []
        for i, line in enumerate(lines):
            if not line.startswith("cpu"):
                break
            parts = line.split()
            if parts[0] == "cpu":
                continue  # Skip aggregate for per-core
            try:
                values = [int(x) for x in parts[1:]]
            except ValueError:
                continue

            core_id = i - 1
            idle = values[3] if len(values) > 3 else 0
            total = sum(values)

            usage = 0.0
            if core_id < len(self._prev_cpu_times):
                prev_total, prev_idle = self._prev_cpu_times[core_id]
                d_total = total - prev_total
                d_idle = idle - prev_idle
                if d_total > 0:
                    usage = (d_total - d_idle) / d_total * 100.0

            # Ensure prev_cpu_times is long enough
            while len(self._prev_cpu_times) <= core_id:
                self._prev_cpu_times.append((0, 0))
            self._prev_cpu_times[core_id] = (total, idle)

            # Build or update CpuInfo
            if core_id < len(self._cpu_cores):
                info = self._cpu_cores[core_id]
                info.usage = round(min(100.0, max(0.0, usage)), 1)
                info.history.append(info.usage)
                if len(info.history) > self._history_size:
                    info.history.pop(0)
            else:
                info = CpuInfo(
                    core_id=core_id,
                    usage=round(min(100.0, max(0.0, usage)), 1),
                    history=[round(usage, 1)],
                )
                self._cpu_cores.append(info)

    def _read_memory(self) -> None:
        """Read memory from /proc/meminfo."""
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
        except OSError:
            return

        data = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                data[key] = int(parts[1])

        total = data.get("MemTotal", 0)
        available = data.get("MemAvailable", 0)
        free = data.get("MemFree", 0)
        buffers = data.get("Buffers", 0)
        cached = data.get("Cached", 0)
        swap_total = data.get("SwapTotal", 0)
        swap_free = data.get("SwapFree", 0)

        used = total - available if available else total - free
        usage_pct = (used / total * 100) if total > 0 else 0.0

        self._memory = MemoryInfo(
            total_mb=round(total / 1024),
            used_mb=round(used / 1024),
            available_mb=round(available / 1024),
            buffers_mb=round(buffers / 1024),
            cached_mb=round(cached / 1024),
            swap_total_mb=round(swap_total / 1024),
            swap_used_mb=round((swap_total - swap_free) / 1024),
            usage_percent=round(usage_pct, 1),
            history=self._memory.history + [round(usage_pct, 1)]
            if hasattr(self._memory, 'history') and self._memory.history else [round(usage_pct, 1)],
        )
        # Trim history
        while len(self._memory.history) > self._history_size:
            self._memory.history.pop(0)

    def _read_disk(self) -> None:
        """Read disk usage from statvfs."""
        self._disks = []
        try:
            with open("/proc/mounts") as f:
                mounts = f.readlines()
        except OSError:
            return

        seen = set()
        for line in mounts:
            parts = line.split()
            if len(parts) < 2:
                continue
            device, mount = parts[0], parts[1]
            if not device.startswith("/"):
                continue
            if mount in seen:
                continue
            seen.add(mount)

            try:
                stat = os.statvfs(mount)
                total = stat.f_blocks * stat.f_frsize
                free = stat.f_bavail * stat.f_frsize
                used = total - free
                total_gb = total / (1024 ** 3)
                used_gb = used / (1024 ** 3)
                free_gb = free / (1024 ** 3)
                usage_pct = (used / total * 100) if total > 0 else 0.0

                self._disks.append(DiskInfo(
                    device=device,
                    mount_point=mount,
                    fs_type=parts[2] if len(parts) > 2 else "",
                    total_gb=round(total_gb, 1),
                    used_gb=round(used_gb, 1),
                    free_gb=round(free_gb, 1),
                    usage_percent=round(usage_pct, 1),
                ))
            except (OSError, ValueError):
                continue

    def _read_network(self) -> None:
        """Read network statistics from /proc/net/dev."""
        try:
            with open("/proc/net/dev") as f:
                lines = f.readlines()
        except OSError:
            return

        for line in lines[2:]:  # Skip headers
            parts = line.split()
            if len(parts) < 17:
                continue

            iface = parts[0].rstrip(":")
            if iface == "lo":
                continue

            rx_bytes = int(parts[1])
            tx_bytes = int(parts[9])

            # Calculate speed (bytes/sec)
            rx_speed = 0.0
            tx_speed = 0.0
            if iface in self._prev_net_bytes:
                prev_rx, prev_tx = self._prev_net_bytes[iface]
                dt = self._update_interval
                if dt > 0:
                    rx_speed = (rx_bytes - prev_rx) / dt / 1024  # KB/s
                    tx_speed = (tx_bytes - prev_tx) / dt / 1024

            self._prev_net_bytes[iface] = (rx_bytes, tx_bytes)

            # Find or create
            existing = None
            for n in self._networks:
                if n.interface == iface:
                    existing = n
                    break

            if existing:
                existing.rx_bytes = rx_bytes
                existing.tx_bytes = tx_bytes
                existing.rx_speed = round(max(0, rx_speed), 1)
                existing.tx_speed = round(max(0, tx_speed), 1)
                existing.rx_history.append(existing.rx_speed)
                existing.tx_history.append(existing.tx_speed)
                while len(existing.rx_history) > self._history_size:
                    existing.rx_history.pop(0)
                while len(existing.tx_history) > self._history_size:
                    existing.tx_history.pop(0)
            else:
                self._networks.append(NetworkInfo(
                    interface=iface,
                    rx_bytes=rx_bytes,
                    tx_bytes=tx_bytes,
                    rx_speed=round(max(0, rx_speed), 1),
                    tx_speed=round(max(0, tx_speed), 1),
                    rx_history=[round(max(0, rx_speed), 1)],
                    tx_history=[round(max(0, tx_speed), 1)],
                ))

    def _read_processes(self) -> None:
        """Read process list from /proc."""
        self._processes = []
        try:
            pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
        except OSError:
            return

        for pid in pids[:200]:  # Limit to 200 processes
            try:
                stat_path = f"/proc/{pid}/stat"
                status_path = f"/proc/{pid}/status"

                with open(stat_path) as f:
                    stat = f.read()
                # Parse: pid (comm) state ...
                match = re.match(r"(\d+) \((.+)\) (.)", stat)
                if not match:
                    continue
                p_pid = int(match.group(1))
                name = match.group(2)[:20]
                state = match.group(3)

                # Read status for memory
                with open(status_path) as f:
                    status = f.read()

                rss_kb = 0
                for line in status.split("\n"):
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break

                user = ""
                try:
                    import pwd
                    uid = os.stat(stat_path).st_uid
                    user = pwd.getpwuid(uid).pw_name
                except (OSError, KeyError):
                    user = str(os.stat(stat_path).st_uid)

                self._processes.append(ProcessInfo(
                    pid=p_pid,
                    name=name,
                    memory_mb=round(rss_kb / 1024, 1),
                    status=state,
                    user=user,
                ))
            except (OSError, ValueError):
                continue

    def _read_temperatures(self) -> None:
        """Read temperature sensors from sysfs."""
        self._temperatures = {}
        thermal_base = "/sys/class/thermal"
        try:
            for entry in os.listdir(thermal_base):
                if not entry.startswith("thermal_zone"):
                    continue
                type_path = os.path.join(thermal_base, entry, "type")
                temp_path = os.path.join(thermal_base, entry, "temp")
                try:
                    with open(type_path) as f:
                        sensor = f.read().strip()
                    with open(temp_path) as f:
                        temp_raw = int(f.read().strip())
                    self._temperatures[sensor] = round(temp_raw / 1000, 1)
                except (OSError, ValueError):
                    continue
        except OSError:
            pass

    # -- History utilities ---------------------------------------------

    def sparkline(self, data: List[float], width: int = 40, height: int = 16) -> str:
        """Generate a text sparkline from a list of values."""
        if not data:
            return ""
        blocks = " ▁▂▃▄▅▆▇█"
        mn = min(data)
        mx = max(data)
        rng = mx - mn if mx != mn else 1.0

        # Sample data to fit width
        if len(data) > width:
            step = len(data) / width
            sampled = [data[int(i * step)] for i in range(width)]
        else:
            sampled = data

        return "".join(
            blocks[min(len(blocks) - 1, int((v - mn) / rng * (len(blocks) - 1)))]
            for v in sampled
        )

    def format_bytes(self, bytes_val: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(bytes_val) < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"

    def format_speed(self, kb_per_sec: float) -> str:
        """Format KB/s to human-readable speed."""
        if kb_per_sec < 1024:
            return f"{kb_per_sec:.1f} KB/s"
        return f"{kb_per_sec / 1024:.1f} MB/s"

    # -- Rendering -----------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the system monitor to a PIL Image."""
        if not self._visible:
            return None

        self.update()

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_mono = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except (OSError, IOError):
            font = font_bold = font_title = font_mono = font_small = ImageFont.load_default()

        # Main panel
        panel_x, panel_y = 100, 50
        panel_w = screen_width - 200
        panel_h = screen_height - 100

        # Background
        draw.rounded_rectangle(
            [panel_x, panel_y, panel_x + panel_w, panel_y + panel_h],
            radius=16, fill=(25, 25, 30, 240), outline=(60, 60, 70))

        # Title bar
        draw.text((panel_x + 20, panel_y + 16), "System Monitor",
                  fill=(220, 220, 220), font=font_title)

        # System info line
        sys_line = (f"{self._system.hostname} | "
                    f"{self._system.kernel} | "
                    f"Uptime: {self.uptime} | "
                    f"Load: {self._system.load_avg[0]:.1f} "
                    f"{self._system.load_avg[1]:.1f} "
                    f"{self._system.load_avg[2]:.1f}")
        draw.text((panel_x + 20, panel_y + 44), sys_line,
                  fill=(140, 140, 140), font=font_small)

        content_y = panel_y + 70
        col_w = (panel_w - 60) // 2
        left_x = panel_x + 20
        right_x = panel_x + col_w + 40

        # CPU section
        self._render_cpu(draw, left_x, content_y, col_w, font, font_bold, font_mono, font_small)

        # Memory section
        self._render_memory(draw, right_x, content_y, col_w, font, font_bold, font_mono, font_small)

        # Disk section
        disk_y = content_y + 180
        self._render_disk(draw, left_x, disk_y, col_w, font, font_bold, font_small)

        # Network section
        self._render_network(draw, right_x, disk_y, col_w, font, font_bold, font_mono, font_small)

        # Processes section
        proc_y = disk_y + 180
        self._render_processes(draw, left_x, proc_y, panel_w - 40, font, font_bold, font_mono, font_small)

        return img

    def _render_cpu(self, draw, x, y, w, font, font_bold, font_mono, font_small):
        """Render CPU section."""
        # Section header
        draw.rounded_rectangle(
            [x, y, x + w, y + 28], radius=8, fill=(40, 40, 50))
        draw.text((x + 12, y + 5), "CPU", fill=(100, 149, 237), font=font_bold)

        # Overall usage
        overall = self.cpu_overall
        pct_text = f"{overall:.1f}%"
        draw.text((x + w - 60, y + 5), pct_text,
                  fill=(220, 220, 220), font=font_bold)

        # Usage bar
        bar_y = y + 34
        bar_h = 8
        draw.rounded_rectangle(
            [x, bar_y, x + w, bar_y + bar_h], radius=4, fill=(50, 50, 60))
        fill_w = int(w * overall / 100)
        color = (100, 200, 100) if overall < 70 else (220, 180, 60) if overall < 90 else (220, 80, 80)
        if fill_w > 0:
            draw.rounded_rectangle(
                [x, bar_y, x + fill_w, bar_y + bar_h], radius=4, fill=color)

        # Per-core sparklines
        core_y = bar_y + 16
        cols = min(4, len(self._cpu_cores) or 1)
        col_w = w // cols
        for i, core in enumerate(self._cpu_cores[:8]):
            cx = x + (i % cols) * col_w
            cy = core_y + (i // cols) * 30
            spark = self.sparkline(core.history, width=col_w // 8, height=12)
            usage_text = f"C{core.core_id}: {core.usage:.0f}%"
            draw.text((cx, cy), usage_text, fill=(180, 180, 180), font=font_small)
            spark_x = cx + len(usage_text) * 7 + 4
            if spark_x + len(spark) * 7 < x + w:
                draw.text((spark_x, cy), spark,
                          fill=(100, 149, 237), font=font_mono)

    def _render_memory(self, draw, x, y, w, font, font_bold, font_mono, font_small):
        """Render memory section."""
        draw.rounded_rectangle(
            [x, y, x + w, y + 28], radius=8, fill=(40, 40, 50))
        draw.text((x + 12, y + 5), "Memory", fill=(100, 149, 237), font=font_bold)

        m = self._memory
        pct_text = f"{m.usage_percent:.1f}%"
        draw.text((x + w - 60, y + 5), pct_text,
                  fill=(220, 220, 220), font=font_bold)

        # Usage bar
        bar_y = y + 34
        bar_h = 8
        draw.rounded_rectangle(
            [x, bar_y, x + w, bar_y + bar_h], radius=4, fill=(50, 50, 60))
        fill_w = int(w * m.usage_percent / 100)
        color = (100, 149, 237) if m.usage_percent < 80 else (220, 180, 60) if m.usage_percent < 95 else (220, 80, 80)
        if fill_w > 0:
            draw.rounded_rectangle(
                [x, bar_y, x + fill_w, bar_y + bar_h], radius=4, fill=color)

        # Details
        detail_y = bar_y + 14
        details = [
            f"Used: {m.used_mb:.0f} MB",
            f"Available: {m.available_mb:.0f} MB",
            f"Buffers: {m.buffers_mb:.0f} MB  Cached: {m.cached_mb:.0f} MB",
        ]
        if m.swap_total_mb > 0:
            details.append(f"Swap: {m.swap_used_mb:.0f} / {m.swap_total_mb:.0f} MB")
        for i, d in enumerate(details):
            draw.text((x, detail_y + i * 16), d,
                      fill=(160, 160, 160), font=font_small)

        # History sparkline
        spark_y = detail_y + len(details) * 16 + 4
        spark = self.sparkline(m.history, width=w // 7, height=14)
        if spark:
            draw.text((x, spark_y), spark, fill=(100, 149, 237), font=font_mono)

    def _render_disk(self, draw, x, y, w, font, font_bold, font_small):
        """Render disk section."""
        draw.rounded_rectangle(
            [x, y, x + w, y + 28], radius=8, fill=(40, 40, 50))
        draw.text((x + 12, y + 5), "Disk", fill=(100, 149, 237), font=font_bold)

        dy = y + 34
        for disk in self._disks[:4]:
            # Mount point and usage
            mount = disk.mount_point
            if len(mount) > 20:
                mount = "..." + mount[-17:]
            draw.text((x, dy), mount, fill=(180, 180, 180), font=font)

            pct = f"{disk.usage_percent:.0f}%"
            draw.text((x + w - 40, dy), pct,
                      fill=(220, 220, 220), font=font)

            # Bar
            by = dy + 16
            draw.rounded_rectangle(
                [x, by, x + w, by + 6], radius=3, fill=(50, 50, 60))
            fw = int(w * disk.usage_percent / 100)
            color = (100, 200, 100) if disk.usage_percent < 80 else (220, 180, 60) if disk.usage_percent < 95 else (220, 80, 80)
            if fw > 0:
                draw.rounded_rectangle(
                    [x, by, x + fw, by + 6], radius=3, fill=color)

            info = f"{disk.used_gb:.1f} / {disk.total_gb:.1f} GB"
            draw.text((x, by + 8), info,
                      fill=(140, 140, 140), font=font_small)
            dy += 36

        if not self._disks:
            draw.text((x, dy), "No disks detected", fill=(120, 120, 120), font=font)

    def _render_network(self, draw, x, y, w, font, font_bold, font_mono, font_small):
        """Render network section."""
        draw.rounded_rectangle(
            [x, y, x + w, y + 28], radius=8, fill=(40, 40, 50))
        draw.text((x + 12, y + 5), "Network", fill=(100, 149, 237), font=font_bold)

        ny = y + 34
        for net in self._networks[:3]:
            # Interface name
            draw.text((x, ny), net.interface, fill=(180, 180, 180), font=font)

            # RX/TX
            rx = f"↓ {self.format_speed(net.rx_speed)}"
            tx = f"↑ {self.format_speed(net.tx_speed)}"
            draw.text((x + w - len(rx) * 7, ny), rx,
                      fill=(100, 200, 100), font=font_small)
            draw.text((x + w - len(tx) * 7, ny + 14), tx,
                      fill=(100, 149, 237), font=font_small)

            # Sparklines
            spark_y = ny + 30
            rx_spark = self.sparkline(net.rx_history, width=w // 8, height=10)
            tx_spark = self.sparkline(net.tx_history, width=w // 8, height=10)
            if rx_spark:
                draw.text((x, spark_y), f"RX {rx_spark}",
                          fill=(100, 200, 100), font=font_mono)
            if tx_spark:
                draw.text((x, spark_y + 12), f"TX {tx_spark}",
                          fill=(100, 149, 237), font=font_mono)

            ny += 52

        if not self._networks:
            draw.text((x, ny), "No interfaces", fill=(120, 120, 120), font=font)

    def _render_processes(self, draw, x, y, w, font, font_bold, font_mono, font_small):
        """Render process list section."""
        draw.rounded_rectangle(
            [x, y, x + w, y + 28], radius=8, fill=(40, 40, 50))
        draw.text((x + 12, y + 5), f"Processes ({len(self._processes)})",
                  fill=(100, 149, 237), font=font_bold)

        # Header
        hy = y + 34
        col_pids = x + 8
        col_name = x + 70
        col_cpu = x + w - 200
        col_mem = x + w - 120
        col_user = x + w - 50

        draw.text((col_pids, hy), "PID", fill=(120, 120, 120), font=font_small)
        draw.text((col_name, hy), "Name", fill=(120, 120, 120), font=font_small)
        draw.text((col_cpu, hy), "CPU%", fill=(120, 120, 120), font=font_small)
        draw.text((col_mem, hy), "RSS", fill=(120, 120, 120), font=font_small)
        draw.text((col_user, hy), "User", fill=(120, 120, 120), font=font_small)

        # Process rows
        row_y = hy + 18
        max_rows = min(15, (1080 - y - 40) // 16)

        sorted_procs = self.processes
        for proc in sorted_procs[self._scroll_offset:self._scroll_offset + max_rows]:
            draw.text((col_pids, row_y), str(proc.pid),
                      fill=(160, 160, 160), font=font_mono)
            name = proc.name[:20]
            draw.text((col_name, row_y), name,
                      fill=(200, 200, 200), font=font)
            draw.text((col_cpu, row_y), f"{proc.cpu_percent:.1f}",
                      fill=(200, 200, 200), font=font_mono)
            draw.text((col_mem, row_y), f"{proc.memory_mb:.0f}M",
                      fill=(200, 200, 200), font=font_mono)
            draw.text((col_user, row_y), proc.user[:10],
                      fill=(160, 160, 160), font=font_small)
            row_y += 16

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"SystemMonitor(cpu={len(self._cpu_cores)} cores, "
            f"mem={self._memory.usage_percent:.0f}%, "
            f"disks={len(self._disks)}, "
            f"nets={len(self._networks)})"
        )


__all__ = ["SystemMonitor", "MonitorView", "SystemInfo", "CpuInfo",
           "MemoryInfo", "DiskInfo", "NetworkInfo", "ProcessInfo"]
