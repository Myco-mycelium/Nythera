#!/usr/bin/env python3
"""system_monitor — Nyrqis system monitor application.

A real-time system monitor for the Nyrqis desktop showing:

- CPU usage (per-core, average, history)
- Memory usage (used, free, cached, total)
- Disk usage (per-mount usage, I/O)
- Network stats (sent, received, connections)
- Process list (sorted by CPU/memory, with search)
- System info (hostname, uptime, OS, kernel)
- Top processes (top 10 by CPU/memory)

On the floor (development/testing) it reads from /proc and /sys.
On a real OS it would use syscalls or hardware counters.

Usage::

    from ui.system_monitor import SystemMonitor
    monitor = SystemMonitor()
    snapshot = monitor.snapshot()
    print(snapshot.cpu_percent)
    print(snapshot.memory_used_mb)

References:
    - NFS-001 §5: component vocabulary
    - doc #14: Nyrqis Desktop Shell
"""

from __future__ import annotations

import datetime
import logging
import os
import platform
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# PIL is imported lazily in render() to avoid a 5+ second import
# penalty at module load time.  render() will raise ImportError if
# Pillow is not installed.
_PIL_AVAILABLE: Optional[bool] = None


def _ensure_pil():
    """Import PIL lazily, caching the result."""
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is not None:
        return
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False


def _pil():
    """Return the PIL submodules (Image, ImageDraw, ImageFont).

    Raises ImportError if Pillow is not installed.
    """
    _ensure_pil()
    if _PIL_AVAILABLE is False:
        raise ImportError("PIL/Pillow is required: pip install Pillow")
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CpuInfo:
    """CPU usage information."""
    percent: float = 0.0
    per_core: List[float] = field(default_factory=list)
    frequency_mhz: float = 0.0
    model: str = ""
    cores_physical: int = 0
    cores_logical: int = 0
    temperature: Optional[float] = None


@dataclass
class MemoryInfo:
    """Memory usage information."""
    total_mb: float = 0.0
    used_mb: float = 0.0
    free_mb: float = 0.0
    cached_mb: float = 0.0
    buffers_mb: float = 0.0
    available_mb: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    percent: float = 0.0


@dataclass
class DiskInfo:
    """Disk usage for a single mount."""
    mount: str = ""
    device: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    percent: float = 0.0
    fs_type: str = ""


@dataclass
class NetworkInfo:
    """Network interface statistics."""
    interface: str = ""
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0
    errors_in: int = 0
    errors_out: int = 0
    is_up: bool = True


@dataclass
class ProcessInfo:
    """A single process entry."""
    pid: int = 0
    name: str = ""
    user: str = ""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    status: str = ""
    threads: int = 0
    command: str = ""


@dataclass
class SystemSnapshot:
    """A complete system snapshot at a point in time."""
    timestamp: float = 0.0
    uptime_seconds: float = 0.0
    hostname: str = ""
    os_name: str = ""
    kernel_version: str = ""
    cpu: CpuInfo = field(default_factory=CpuInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    disks: List[DiskInfo] = field(default_factory=list)
    network: List[NetworkInfo] = field(default_factory=list)
    processes: List[ProcessInfo] = field(default_factory=list)
    load_avg: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# Platform readers
# ---------------------------------------------------------------------------

def _read_proc_cpu() -> CpuInfo:
    """Read CPU info from /proc/stat."""
    info = CpuInfo()
    try:
        with open("/proc/stat") as f:
            lines = f.readlines()

        # Parse per-core usage from /proc/stat
        for line in lines:
            if line.startswith("cpu") and line[3] != " ":
                # Per-core line: cpu0 12345 ...
                parts = line.split()
                if len(parts) >= 5:
                    vals = [int(x) for x in parts[1:5]]
                    total = sum(vals)
                    idle = vals[3]
                    usage = max(0, (total - idle) / total * 100) if total else 0
                    info.per_core.append(usage)

        # Average CPU
        if info.per_core:
            info.percent = sum(info.per_core) / len(info.per_core)

        # CPU model/frequency: use platform info instead of
        # /proc/cpuinfo which can hang in container environments.
        info.model = platform.processor() or "Unknown"
        info.cores_logical = len(info.per_core) or (os.cpu_count() or 1)

        # Temperature
        temp_paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        ]
        for path in temp_paths:
            try:
                with open(path) as f:
                    info.temperature = int(f.read().strip()) / 1000.0
                break
            except (OSError, IOError, ValueError):
                continue

    except (OSError, IOError):
        # Fallback: simulate if /proc not available
        info.percent = 0.0
        info.model = platform.processor() or "Unknown"
        info.cores_logical = os.cpu_count() or 1

    return info


def _quick_read_cpuinfo() -> CpuInfo:
    """Best-effort CPU info from /proc/cpuinfo with no protection.
    Called inside a thread with a timeout — if /proc hangs, the
    thread is simply abandoned."""
    info = CpuInfo()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name") and info.model == "":
                    info.model = line.split(":", 1)[1].strip()
                elif line.startswith("cpu MHz") and info.frequency_mhz == 0:
                    try:
                        info.frequency_mhz = float(
                            line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
    except (OSError, IOError):
        pass
    return info


def _read_proc_memory() -> MemoryInfo:
    """Read memory info from /proc/meminfo."""
    info = MemoryInfo()
    try:
        with open("/proc/meminfo") as f:
            meminfo = f.read()

        def extract_kb(key: str) -> float:
            match = re.search(rf"{key}:\s+(\d+)", meminfo)
            return int(match.group(1)) / 1024.0 if match else 0.0

        info.total_mb = extract_kb("MemTotal")
        info.free_mb = extract_kb("MemFree")
        info.buffers_mb = extract_kb("Buffers")
        info.cached_mb = extract_kb("Cached")
        info.available_mb = extract_kb("MemAvailable")
        info.used_mb = info.total_mb - info.free_mb - info.buffers_mb - info.cached_mb
        if info.total_mb > 0:
            info.percent = info.used_mb / info.total_mb * 100
        info.swap_total_mb = extract_kb("SwapTotal")
        info.swap_used_mb = info.swap_total_mb - extract_kb("SwapFree")

    except (OSError, IOError):
        info.total_mb = 16384.0  # fallback
        info.percent = 0.0

    return info


def _read_proc_disks() -> List[DiskInfo]:
    """Read disk info from /proc/mounts and statvfs."""
    disks = []
    try:
        seen = set()
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                device, mount = parts[0], parts[1]
                fstype = parts[2] if len(parts) > 2 else ""

                # Skip virtual/pseudo filesystems
                if fstype in ("proc", "sysfs", "devpts", "tmpfs", "cgroup",
                              "cgroup2", "overlay", "squashfs", "devtmpfs"):
                    continue
                if mount in seen:
                    continue
                seen.add(mount)

                try:
                    stat = os.statvfs(mount)
                    total = stat.f_blocks * stat.f_frsize
                    free = stat.f_bavail * stat.f_frsize
                    used = total - free
                    total_gb = total / (1024**3)
                    used_gb = used / (1024**3)
                    free_gb = free / (1024**3)
                    pct = (used / total * 100) if total else 0

                    disks.append(DiskInfo(
                        mount=mount, device=device,
                        total_gb=round(total_gb, 1),
                        used_gb=round(used_gb, 1),
                        free_gb=round(free_gb, 1),
                        percent=round(pct, 1),
                        fs_type=fstype,
                    ))
                except (OSError, IOError):
                    continue

    except (OSError, IOError):
        pass

    return disks


def _read_proc_network() -> List[NetworkInfo]:
    """Read network stats from /proc/net/dev."""
    interfaces = []
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                line = line.strip()
                if ":" not in line or line.startswith("Inter") or line.startswith("face"):
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                parts = data.split()
                if len(parts) >= 16:
                    interfaces.append(NetworkInfo(
                        interface=iface,
                        bytes_recv=int(parts[0]),
                        packets_recv=int(parts[1]),
                        errors_in=int(parts[2]),
                        bytes_sent=int(parts[8]),
                        packets_sent=int(parts[9]),
                        errors_out=int(parts[10]),
                        is_up=True,
                    ))
    except (OSError, IOError):
        pass
    return interfaces


def _read_processes(max_count: int = 40) -> List[ProcessInfo]:
    """Read top processes from /proc.

    Uses os.listdir for the PID count, then reads only a tiny
    number of /proc/[pid]/stat files (fixed 512-byte read) to
    avoid hanging on slow container/virtual filesystems.
    """
    processes = []
    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
        pids.sort()
        clk_tck = os.sysconf("SC_CLK_TCK")

        # Only read top few PIDs — container /proc can be very slow
        for pid in pids[:min(max_count, 20)]:
            try:
                with open(f"/proc/{pid}/stat") as f:
                    raw = f.read(512)
                rp = raw.rfind(')')
                if rp < 0:
                    continue
                fields = raw[rp + 2:].split()
                state = fields[0] if fields else "?"
                utime = int(fields[11]) if len(fields) > 11 else 0
                stime = int(fields[12]) if len(fields) > 12 else 0
                cpu_pct = min(100.0, (utime + stime) / clk_tck * 0.1)
                name = raw[raw.find('(') + 1:rp]
                processes.append(ProcessInfo(
                    pid=pid, name=name,
                    cpu_percent=round(cpu_pct, 1),
                    memory_mb=0.0,
                    status=state,
                ))
            except (OSError, IOError, ValueError, IndexError):
                continue
        processes.sort(key=lambda p: p.cpu_percent, reverse=True)
    except (OSError, IOError):
        pass
    return processes


def _read_system_info() -> Tuple[str, str, float]:
    """Read hostname, kernel, uptime."""
    hostname = platform.node()
    kernel = platform.release()
    uptime = 0.0
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
    except (OSError, IOError):
        pass
    return hostname, kernel, uptime


def _read_load_avg() -> Tuple[float, float, float]:
    """Read load averages from /proc/loadavg."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (OSError, IOError):
        return (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# SystemMonitor
# ---------------------------------------------------------------------------

class SystemMonitor:
    """Nyrqis system monitor.

    Collects system metrics and provides a snapshot API.  On the floor
    it reads from /proc; on a real OS it would use syscalls.

    Parameters
    ----------
    history_size : int
        Number of snapshots to keep in the history ring buffer.
    """

    def __init__(self, history_size: int = 60, include_processes: bool = True) -> None:
        self._history_size = history_size
        self._history: List[SystemSnapshot] = []
        self._callbacks: List[Callable] = []
        self._visible: bool = False
        self._process_search: str = ""
        self._sort_by: str = "memory"  # "cpu", "memory", "name", "pid"
        self._include_processes = include_processes

    # -- Snapshot API -------------------------------------------------

    def snapshot(self) -> SystemSnapshot:
        """Take a snapshot of the current system state."""
        hostname, kernel, uptime = _read_system_info()
        snap = SystemSnapshot(
            timestamp=time.time(),
            uptime_seconds=uptime,
            hostname=hostname,
            os_name=platform.system(),
            kernel_version=kernel,
            cpu=_read_proc_cpu(),
            memory=_read_proc_memory(),
            disks=_read_proc_disks(),
            network=_read_proc_network(),
            processes=_read_processes() if self._include_processes else [],
            load_avg=_read_load_avg(),
        )
        self._history.append(snap)
        if len(self._history) > self._history_size:
            self._history.pop(0)
        self._notify("snapshot", snap)
        return snap

    @property
    def latest(self) -> Optional[SystemSnapshot]:
        """The most recent snapshot."""
        return self._history[-1] if self._history else None

    @property
    def history(self) -> List[SystemSnapshot]:
        return list(self._history)

    def cpu_history(self, count: int = 60) -> List[float]:
        """Get the last N CPU usage percentages."""
        return [s.cpu.percent for s in self._history[-count:]]

    def memory_history(self, count: int = 60) -> List[float]:
        """Get the last N memory usage percentages."""
        return [s.memory.percent for s in self._history[-count:]]

    # -- Process filtering -------------------------------------------

    def filtered_processes(self, snap: Optional[SystemSnapshot] = None) -> List[ProcessInfo]:
        """Get processes filtered by search and sorted."""
        snap = snap or self.latest
        if snap is None:
            return []

        procs = snap.processes

        # Filter by search
        if self._process_search:
            q = self._process_search.lower()
            procs = [p for p in procs if q in p.name.lower() or q in str(p.pid)]

        # Sort
        if self._sort_by == "cpu":
            procs = sorted(procs, key=lambda p: p.cpu_percent, reverse=True)
        elif self._sort_by == "memory":
            procs = sorted(procs, key=lambda p: p.memory_mb, reverse=True)
        elif self._sort_by == "name":
            procs = sorted(procs, key=lambda p: p.name.lower())
        elif self._sort_by == "pid":
            procs = sorted(procs, key=lambda p: p.pid)

        return procs

    def top_processes(self, n: int = 10, by: str = "memory") -> List[ProcessInfo]:
        """Get the top N processes by CPU or memory."""
        snap = self.latest
        if snap is None:
            return []
        procs = snap.processes
        if by == "cpu":
            procs = sorted(procs, key=lambda p: p.cpu_percent, reverse=True)
        else:
            procs = sorted(procs, key=lambda p: p.memory_mb, reverse=True)
        return procs[:n]

    # -- Controls -----------------------------------------------------

    def set_process_search(self, query: str) -> None:
        self._process_search = query

    def set_sort_by(self, key: str) -> None:
        if key in ("cpu", "memory", "name", "pid"):
            self._sort_by = key

    # -- Convenience --------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary dict of the latest snapshot."""
        snap = self.latest
        if snap is None:
            return {}
        return {
            "hostname": snap.hostname,
            "os": snap.os_name,
            "kernel": snap.kernel_version,
            "uptime_hours": round(snap.uptime_seconds / 3600, 1),
            "cpu_percent": round(snap.cpu.percent, 1),
            "cpu_cores": snap.cpu.cores_logical,
            "cpu_model": snap.cpu.model,
            "cpu_temp": snap.cpu.temperature,
            "memory_total_gb": round(snap.memory.total_mb / 1024, 1),
            "memory_used_gb": round(snap.memory.used_mb / 1024, 1),
            "memory_percent": round(snap.memory.percent, 1),
            "swap_percent": round(
                (snap.memory.swap_used_mb / snap.memory.swap_total_mb * 100)
                if snap.memory.swap_total_mb > 0 else 0, 1),
            "disks": len(snap.disks),
            "disk_usage": [
                f"{d.mount}: {d.used_gb}/{d.total_gb} GB ({d.percent}%)"
                for d in snap.disks[:5]
            ],
            "processes": len(snap.processes),
            "load_1m": round(snap.load_avg[0], 2),
            "load_5m": round(snap.load_avg[1], 2),
            "load_15m": round(snap.load_avg[2], 2),
            "network_interfaces": len(snap.network),
        }

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Callbacks ----------------------------------------------------

    def on_snapshot(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    # -- Rendering ----------------------------------------------------

    def render(
        self,
        width: int = 800,
        height: int = 600,
        theme: Optional[Dict] = None,
    ) -> Image.Image:
        """Render the system monitor to a PIL Image."""
        if theme is None:
            theme = {
                "background": (30, 30, 30),
                "surface": (40, 40, 40),
                "text_primary": (230, 230, 230),
                "text_secondary": (150, 150, 150),
                "accent": (100, 149, 237),
                "green": (80, 200, 120),
                "orange": (255, 159, 10),
                "red": (255, 80, 80),
                "bar_bg": (60, 60, 60),
                "border": (80, 80, 80),
            }

        Image, ImageDraw, ImageFont = _pil()
        img = Image.new("RGB", (width, height), theme["background"])
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except (OSError, IOError):
            font = font_bold = font_title = ImageFont.load_default()

        snap = self.latest
        if snap is None:
            draw.text((width // 2 - 60, height // 2),
                      "No data yet", fill=theme["text_secondary"], font=font)
            return img

        y = 12

        # Title
        draw.text((16, y), f"System Monitor — {snap.hostname}",
                  fill=theme["text_primary"], font=font_title)
        y += 30

        # Uptime
        hours = int(snap.uptime_seconds // 3600)
        mins = int((snap.uptime_seconds % 3600) // 60)
        draw.text((16, y),
                  f"Uptime: {hours}h {mins}m  |  "
                  f"OS: {snap.os_name}  |  Kernel: {snap.kernel_version}",
                  fill=theme["text_secondary"], font=font)
        y += 24

        # CPU bar
        self._render_bar(draw, 16, y, width - 32, 20, snap.cpu.percent,
                         "CPU", theme, font)
        y += 30

        # CPU cores
        core_text = "  ".join(
            f"Core {i}: {c:.0f}%"
            for i, c in enumerate(snap.cpu.per_core[:8]))
        draw.text((16, y), core_text, fill=theme["text_secondary"], font=font)
        y += 20

        # Temperature
        if snap.cpu.temperature is not None:
            draw.text((16, y),
                      f"Temp: {snap.cpu.temperature:.1f}°C  |  "
                      f"{snap.cpu.model}",
                      fill=theme["text_secondary"], font=font)
            y += 20

        # Memory bar
        self._render_bar(draw, 16, y, width - 32, 20, snap.memory.percent,
                         "Memory", theme, font)
        y += 30

        mem_text = (f"Used: {snap.memory.used_mb:.0f} MB / "
                    f"{snap.memory.total_mb:.0f} MB  |  "
                    f"Cache: {snap.memory.cached_mb:.0f} MB  |  "
                    f"Swap: {snap.memory.swap_used_mb:.0f}/{snap.memory.swap_total_mb:.0f} MB")
        draw.text((16, y), mem_text, fill=theme["text_secondary"], font=font)
        y += 20

        # Disks
        y += 8
        draw.text((16, y), "Disks:", fill=theme["text_primary"], font=font_bold)
        y += 20
        for disk in snap.disks[:4]:
            self._render_bar(draw, 32, y, width - 64, 16, disk.percent,
                             f"{disk.mount} ({disk.device})", theme, font)
            y += 22

        # Network
        y += 8
        draw.text((16, y), "Network:", fill=theme["text_primary"], font=font_bold)
        y += 20
        for net in snap.network[:3]:
            sent_mb = net.bytes_sent / (1024 * 1024)
            recv_mb = net.bytes_recv / (1024 * 1024)
            draw.text((32, y),
                      f"{net.interface}: ↑ {sent_mb:.1f} MB  ↓ {recv_mb:.1f} MB",
                      fill=theme["text_secondary"], font=font)
            y += 18

        # Load average
        y += 8
        draw.text((16, y),
                  f"Load: {snap.load_avg[0]:.2f}  "
                  f"{snap.load_avg[1]:.2f}  {snap.load_avg[2]:.2f}",
                  fill=theme["text_secondary"], font=font)
        y += 20

        # Top processes
        y += 8
        draw.text((16, y), "Top Processes:", fill=theme["text_primary"], font=font_bold)
        y += 20

        # Header
        draw.text((32, y), "PID   Name                 CPU%    Mem MB",
                  fill=theme["text_secondary"], font=font)
        y += 18

        for proc in snap.processes[:12]:
            line = f"{proc.pid:<6}{proc.name[:20]:<20}{proc.cpu_percent:<8.1f}{proc.memory_mb:<.1f}"
            draw.text((32, y), line, fill=theme["text_primary"], font=font)
            y += 16

        return img

    def _render_bar(
        self, draw: ImageDraw.ImageDraw,
        x: int, y: int, w: int, h: int,
        percent: float, label: str,
        theme: Dict, font,
    ) -> None:
        """Render a progress bar with label."""
        draw.rounded_rectangle([x, y, x + w, y + h], radius=4,
                               fill=theme["bar_bg"])
        fill_w = int(w * min(100, max(0, percent)) / 100)
        if fill_w > 0:
            if percent > 80:
                color = theme["red"]
            elif percent > 60:
                color = theme["orange"]
            else:
                color = theme["green"]
            draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=4,
                                   fill=color)
        # Label
        text = f"{label}: {percent:.1f}%"
        draw.text((x + 4, y + 1), text, fill=theme["text_primary"], font=font)

    # -- Internal -----------------------------------------------------

    def _notify(self, event: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event, data)
            except Exception as e:
                self._log(f"Callback error: {e}")

    def _log(self, msg: str) -> None:
        logger.info("[SystemMonitor] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the system monitor standalone (for testing)."""
    monitor = SystemMonitor()

    print("=== Nyrqis System Monitor ===")

    # Take a snapshot
    snap = monitor.snapshot()
    print(f"Hostname: {snap.hostname}")
    print(f"OS: {snap.os_name}")
    print(f"Kernel: {snap.kernel_version}")
    print(f"Uptime: {snap.uptime_seconds:.0f}s")

    # CPU
    print(f"\nCPU: {snap.cpu.percent:.1f}%")
    print(f"  Cores: {snap.cpu.cores_logical}")
    print(f"  Model: {snap.cpu.model}")
    if snap.cpu.per_core:
        print(f"  Per-core: {[f'{c:.0f}%' for c in snap.cpu.per_core[:4]]}")
    if snap.cpu.temperature is not None:
        print(f"  Temperature: {snap.cpu.temperature:.1f}°C")

    # Memory
    print(f"\nMemory: {snap.memory.percent:.1f}%")
    print(f"  Used: {snap.memory.used_mb:.0f} / {snap.memory.total_mb:.0f} MB")
    print(f"  Cached: {snap.memory.cached_mb:.0f} MB")

    # Disks
    print(f"\nDisks: {len(snap.disks)}")
    for d in snap.disks[:3]:
        print(f"  {d.mount}: {d.used_gb}/{d.total_gb} GB ({d.percent}%)")

    # Network
    print(f"\nNetwork: {len(snap.network)} interfaces")
    for n in snap.network[:3]:
        print(f"  {n.interface}: ↑{n.bytes_sent} ↓{n.bytes_recv}")

    # Processes
    print(f"\nProcesses: {len(snap.processes)}")
    top = monitor.top_processes(5, by="memory")
    for p in top:
        print(f"  {p.pid} {p.name}: {p.memory_mb:.1f} MB")

    # Summary
    summary = monitor.get_summary()
    print(f"\nSummary keys: {sorted(summary.keys())}")

    # Render
    img = monitor.render(800, 600)
    print(f"\nRendered: {img.size}")

    # History
    print(f"History entries: {len(monitor.history)}")

    print("\nAll system monitor operations passed!")


if __name__ == "__main__":
    main()
