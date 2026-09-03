"""
Nyrqis Hardware Monitor — system hardware monitoring dashboard.

Features:
- CPU usage, frequency, and per-core stats
- GPU usage, memory, temperature, and driver info
- RAM/Swap usage with process breakdown
- Fan speed control and monitoring
- Temperature sensors with alerts
- Power consumption tracking
- Real-time sparkline graphs
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ThermalStatus(Enum):
    COOL = "cool"
    NORMAL = "normal"
    WARM = "warm"
    HOT = "hot"
    CRITICAL = "critical"


class FanMode(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    SILENT = "silent"
    PERFORMANCE = "performance"
    FULL = "full"


THERMAL_ICONS = {
    ThermalStatus.COOL: "❄️",
    ThermalStatus.NORMAL: "🟢",
    ThermalStatus.WARM: "🟡",
    ThermalStatus.HOT: "🟠",
    ThermalStatus.CRITICAL: "🔴",
}

FAN_MODE_ICONS = {
    FanMode.AUTO: "🔄",
    FanMode.MANUAL: "🎛️",
    FanMode.SILENT: "🤫",
    FanMode.PERFORMANCE: "⚡",
    FanMode.FULL: "💨",
}


@dataclass
class CPUCore:
    """A single CPU core."""
    core_id: int
    usage_pct: float = 0.0
    frequency_mhz: int = 3600
    temperature_c: int = 45
    is_online: bool = True
    # History for sparklines
    usage_history: List[float] = field(default_factory=list)

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_pct / 100 * 16)
        return "█" * filled + "░" * (16 - filled)

    @property
    def temp_status(self) -> ThermalStatus:
        if self.temperature_c < 40:
            return ThermalStatus.COOL
        elif self.temperature_c < 55:
            return ThermalStatus.NORMAL
        elif self.temperature_c < 70:
            return ThermalStatus.WARM
        elif self.temperature_c < 85:
            return ThermalStatus.HOT
        return ThermalStatus.CRITICAL

    @property
    def sparkline(self) -> str:
        if not self.usage_history:
            return "░" * 20
        chars = "▁▂▃▄▅▆▇█"
        max_val = max(self.usage_history) if max(self.usage_history) > 0 else 1
        result = ""
        step = max(1, len(self.usage_history) // 20)
        for i in range(0, min(len(self.usage_history), 20 * step), step):
            val = self.usage_history[i]
            idx = int(val / max_val * (len(chars) - 1))
            result += chars[min(idx, len(chars) - 1)]
        return result[:20]


@dataclass
class GPUInfo:
    """GPU information."""
    name: str = "NVIDIA GeForce RTX 4070"
    driver_version: str = "550.100"
    vram_total_mb: int = 12288
    vram_used_mb: int = 4800
    usage_pct: float = 45.0
    temperature_c: int = 62
    power_watts: int = 120
    power_limit_watts: int = 200
    fan_speed_pct: int = 40
    clock_core_mhz: int = 2400
    clock_memory_mhz: int = 10501
    clock_shader_mhz: int = 2400
    encoder_usage_pct: float = 0.0
    decoder_usage_pct: float = 12.0
    # History
    usage_history: List[float] = field(default_factory=list)
    temp_history: List[int] = field(default_factory=list)

    @property
    def vram_bar(self) -> str:
        pct = self.vram_used_mb / self.vram_total_mb * 100 if self.vram_total_mb > 0 else 0
        filled = int(pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def vram_str(self) -> str:
        return f"{self.vram_used_mb} / {self.vram_total_mb} MB"

    @property
    def power_pct(self) -> float:
        return (self.power_watts / self.power_limit_watts * 100) if self.power_limit_watts > 0 else 0

    @property
    def power_bar(self) -> str:
        filled = int(self.power_pct / 100 * 16)
        return "█" * filled + "░" * (16 - filled)

    @property
    def temp_status(self) -> ThermalStatus:
        if self.temperature_c < 40:
            return ThermalStatus.COOL
        elif self.temperature_c < 60:
            return ThermalStatus.NORMAL
        elif self.temperature_c < 75:
            return ThermalStatus.WARM
        elif self.temperature_c < 85:
            return ThermalStatus.HOT
        return ThermalStatus.CRITICAL

    @property
    def sparkline(self) -> str:
        if not self.usage_history:
            return "░" * 20
        chars = "▁▂▃▄▅▆▇█"
        max_val = max(self.usage_history) if max(self.usage_history) > 0 else 1
        result = ""
        step = max(1, len(self.usage_history) // 20)
        for i in range(0, min(len(self.usage_history), 20 * step), step):
            val = self.usage_history[i]
            idx = int(val / max_val * (len(chars) - 1))
            result += chars[min(idx, len(chars) - 1)]
        return result[:20]


@dataclass
class FanInfo:
    """Fan information."""
    name: str
    speed_rpm: int = 0
    max_rpm: int = 3000
    speed_pct: int = 0
    mode: FanMode = FanMode.AUTO
    target_temp: int = 70
    temp_curve: List[Tuple[int, int]] = field(default_factory=list)  # (temp, fan%)

    @property
    def rpm_bar(self) -> str:
        filled = int(self.speed_pct / 100 * 16)
        return "█" * filled + "░" * (16 - filled)

    @property
    def display(self) -> str:
        mode_icon = FAN_MODE_ICONS.get(self.mode, "🔄")
        return f"{mode_icon} {self.name}: {self.speed_rpm} RPM ({self.speed_pct}%)"

    @property
    def status(self) -> str:
        if self.speed_pct > 80:
            return "loud"
        elif self.speed_pct > 50:
            return "normal"
        elif self.speed_pct > 20:
            return "quiet"
        return "silent"


@dataclass
class TemperatureSensor:
    """A temperature sensor."""
    name: str
    temperature_c: int
    critical_c: int = 95
    high_c: int = 85
    low_c: int = 0
    # History
    history: List[int] = field(default_factory=list)

    @property
    def status(self) -> ThermalStatus:
        if self.temperature_c < self.low_c + 20:
            return ThermalStatus.COOL
        elif self.temperature_c < self.high_c:
            return ThermalStatus.NORMAL
        elif self.temperature_c < self.critical_c - 10:
            return ThermalStatus.WARM
        elif self.temperature_c < self.critical_c:
            return ThermalStatus.HOT
        return ThermalStatus.CRITICAL

    @property
    def bar(self) -> str:
        max_temp = self.critical_c
        filled = int(self.temperature_c / max_temp * 20)
        return "█" * min(filled, 20) + "░" * max(0, 20 - filled)

    @property
    def icon(self) -> str:
        return THERMAL_ICONS.get(self.status, "❓")


@dataclass
class RAMInfo:
    """RAM information."""
    total_mb: int = 32768
    used_mb: int = 18432
    available_mb: int = 14336
    cached_mb: int = 4096
    buffers_mb: int = 1024
    swap_total_mb: int = 8192
    swap_used_mb: int = 512
    # Per-process top consumers
    top_processes: List[Tuple[str, int]] = field(default_factory=list)  # (name, mb)

    @property
    def usage_pct(self) -> float:
        return (self.used_mb / self.total_mb * 100) if self.total_mb > 0 else 0

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def total_str(self) -> str:
        return f"{self.total_mb / 1024:.1f} GB"

    @property
    def used_str(self) -> str:
        return f"{self.used_mb / 1024:.1f} GB"

    @property
    def swap_usage_pct(self) -> float:
        return (self.swap_used_mb / self.swap_total_mb * 100) if self.swap_total_mb > 0 else 0

    @property
    def swap_bar(self) -> str:
        filled = int(self.swap_usage_pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)


# ─── Hardware Monitor ────────────────────────────────────────────────────


class HardwareMonitor:
    """
    Hardware monitoring dashboard for Nyrqis OS.
    """

    def __init__(self):
        self._cpu_cores: List[CPUCore] = []
        self._gpu: GPUInfo = GPUInfo()
        self._ram: RAMInfo = RAMInfo()
        self._fans: List[FanInfo] = []
        self._sensors: List[TemperatureSensor] = []
        self._selected_index: int = 0
        self._view_mode: str = "overview"  # overview, cpu, gpu, memory, fans, temps

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        random.seed(42)

        # CPU cores
        for i in range(16):
            usage = random.uniform(5, 80) if i < 8 else random.uniform(0, 30)
            temp = random.randint(35, 65)
            freq = random.choice([3600, 4200, 4500, 5200])
            history = [random.uniform(5, 80) for _ in range(60)]
            self._cpu_cores.append(CPUCore(
                i, usage, freq, temp, True, history
            ))

        # GPU history
        self._gpu.usage_history = [random.uniform(20, 80) for _ in range(60)]
        self._gpu.temp_history = [random.randint(50, 70) for _ in range(60)]

        # RAM
        self._ram = RAMInfo(
            32768, 18432, 14336, 4096, 1024,
            8192, 512,
            [("firefox", 3200), ("code", 2800), ("nyrqis-compositor", 1500),
             ("electron", 1200), ("node", 800), ("python3", 600), ("Xwayland", 400)]
        )

        # Fans
        self._fans = [
            FanInfo("CPU Fan", 1800, 3000, 60, FanMode.AUTO, 70,
                    [(40, 20), (50, 30), (60, 45), (70, 60), (80, 80), (90, 100)]),
            FanInfo("GPU Fan", 1400, 3500, 40, FanMode.AUTO, 75,
                    [(30, 15), (40, 25), (50, 35), (60, 50), (70, 65), (80, 85)]),
            FanInfo("Case Fan 1", 900, 1500, 60, FanMode.AUTO, 65),
            FanInfo("Case Fan 2", 900, 1500, 60, FanMode.AUTO, 65),
            FanInfo("PSU Fan", 600, 2000, 30, FanMode.AUTO, 70),
        ]

        # Temperature sensors
        self._sensors = [
            TemperatureSensor("CPU Package", 58, 100, 85, -40),
            TemperatureSensor("CPU Core 0", 55, 100, 85, -40),
            TemperatureSensor("CPU Core 1", 52, 100, 85, -40),
            TemperatureSensor("CPU Core 2", 57, 100, 85, -40),
            TemperatureSensor("CPU Core 3", 54, 100, 85, -40),
            TemperatureSensor("GPU Core", 62, 95, 83, -40),
            TemperatureSensor("GPU Hotspot", 72, 105, 95, -40),
            TemperatureSensor("Memory", 42, 85, 75, -40),
            TemperatureSensor("NVMe SSD", 45, 70, 60, -40),
            TemperatureSensor("Chipset", 50, 100, 90, -40),
            TemperatureSensor("VRM", 65, 115, 100, -40),
            TemperatureSensor("Ambient", 28, 50, 40, 0),
        ]
        # Generate history for sensors
        for sensor in self._sensors:
            sensor.history = [sensor.temperature_c + random.randint(-5, 5) for _ in range(60)]

    # ── Fan Control ───────────────────────────────────────────────────

    def set_fan_mode(self, fan_idx: int, mode: FanMode) -> bool:
        if 0 <= fan_idx < len(self._fans):
            self._fans[fan_idx].mode = mode
            return True
        return False

    def set_fan_speed(self, fan_idx: int, speed_pct: int) -> bool:
        if 0 <= fan_idx < len(self._fans):
            fan = self._fans[fan_idx]
            fan.speed_pct = max(0, min(100, speed_pct))
            fan.speed_rpm = int(fan.max_rpm * fan.speed_pct / 100)
            return True
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "fans":
            return self._fans
        elif self._view_mode == "temps":
            return self._sensors
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def cpu_cores(self) -> List[CPUCore]:
        return list(self._cpu_cores)

    @property
    def gpu(self) -> GPUInfo:
        return self._gpu

    @property
    def ram(self) -> RAMInfo:
        return self._ram

    @property
    def fans(self) -> List[FanInfo]:
        return list(self._fans)

    @property
    def sensors(self) -> List[TemperatureSensor]:
        return list(self._sensors)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def cpu_avg_usage(self) -> float:
        if not self._cpu_cores:
            return 0.0
        return sum(c.usage_pct for c in self._cpu_cores) / len(self._cpu_cores)

    @property
    def cpu_avg_temp(self) -> int:
        if not self._cpu_cores:
            return 0
        return sum(c.temperature_c for c in self._cpu_cores) // len(self._cpu_cores)

    @property
    def cpu_avg_freq(self) -> int:
        online = [c for c in self._cpu_cores if c.is_online]
        if not online:
            return 0
        return sum(c.frequency_mhz for c in online) // len(online)

    @property
    def total_power(self) -> int:
        cpu_power = int(self.cpu_avg_usage * 1.5)  # rough estimate
        gpu_power = self._gpu.power_watts
        other = 80  # motherboard, RAM, etc.
        return cpu_power + gpu_power + other

    # ── Rendering ─────────────────────────────────────────────────────

    def render_overview(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🌡️  Hardware Monitor")
        lines.append("─" * width)

        # CPU summary
        usage = self.cpu_avg_usage
        temp = self.cpu_avg_temp
        freq = self.cpu_avg_freq
        temp_icon = THERMAL_ICONS.get(
            ThermalStatus.COOL if temp < 40 else ThermalStatus.NORMAL if temp < 55 else
            ThermalStatus.WARM if temp < 70 else ThermalStatus.HOT if temp < 85 else
            ThermalStatus.CRITICAL, "❓")
        lines.append(f" 🖥️  CPU: {usage:.0f}% @ {freq} MHz  {temp_icon} {temp}°C  ({len(self._cpu_cores)} cores)")

        # GPU summary
        gpu = self._gpu
        gpu_temp_icon = THERMAL_ICONS.get(gpu.temp_status, "❓")
        lines.append(f" 🎮 GPU: {gpu.usage_pct:.0f}% @ {gpu.clock_core_mhz} MHz  {gpu_temp_icon} {gpu.temperature_c}°C  {gpu.power_watts}W")

        # RAM summary
        ram = self._ram
        lines.append(f" 🧠 RAM: {ram.used_str} / {ram.total_str} ({ram.usage_pct:.0f}%)  Swap: {ram.swap_used_mb} / {ram.swap_total_mb} MB")

        # Power
        lines.append(f" ⚡ Power: ~{self.total_power}W")

        lines.append("─" * width)

        # CPU sparkline
        avg_history = []
        for i in range(60):
            usages = [c.usage_history[i] for c in self._cpu_cores if i < len(c.usage_history)]
            if usages:
                avg_history.append(sum(usages) / len(usages))
        if avg_history:
            chars = "▁▂▃▄▅▆▇█"
            max_val = max(avg_history) if max(avg_history) > 0 else 1
            spark = ""
            step = max(1, len(avg_history) // 40)
            for i in range(0, min(len(avg_history), 40 * step), step):
                val = avg_history[i]
                idx = int(val / max_val * (len(chars) - 1))
                spark += chars[min(idx, len(chars) - 1)]
            lines.append(f" CPU History: {spark[:40]}")

        # GPU sparkline
        if gpu.usage_history:
            chars = "▁▂▃▄▅▆▇█"
            max_val = max(gpu.usage_history) if max(gpu.usage_history) > 0 else 1
            spark = ""
            step = max(1, len(gpu.usage_history) // 40)
            for i in range(0, min(len(gpu.usage_history), 40 * step), step):
                val = gpu.usage_history[i]
                idx = int(val / max_val * (len(chars) - 1))
                spark += chars[min(idx, len(chars) - 1)]
            lines.append(f" GPU History: {spark[:40]}")

        lines.append("")
        lines.append("─" * width)
        lines.append(" C:CPU  G:GPU  R:Memory  F:Fans  T:Temps")
        return lines

    def render_cpu(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🖥️  CPU — {self.cpu_avg_usage:.0f}% avg @ {self.cpu_avg_freq} MHz")
        lines.append("─" * width)

        for core in self._cpu_cores[:8]:
            temp_icon = THERMAL_ICONS.get(core.temp_status, "❓")
            lines.append(f" Core {core.core_id:>2d}: {core.usage_bar} {core.usage_pct:>5.1f}%  {temp_icon} {core.temperature_c}°C  {core.frequency_mhz} MHz")
            lines.append(f"          {core.sparkline}")

        if len(self._cpu_cores) > 8:
            lines.append(f" ... and {len(self._cpu_cores) - 8} more cores")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_gpu(self, width: int = 70) -> List[str]:
        gpu = self._gpu
        lines = []
        lines.append(f" 🎮 GPU — {gpu.name}")
        lines.append("─" * width)
        lines.append(f" Driver:  {gpu.driver_version}")
        lines.append(f" Usage:   {gpu.usage_pct:.1f}%  {gpu.sparkline}")
        lines.append(f" Clock:   Core {gpu.clock_core_mhz} MHz / Memory {gpu.clock_memory_mhz} MHz / Shader {gpu.clock_shader_mhz} MHz")
        lines.append("")
        lines.append(f" VRAM:    [{gpu.vram_bar}] {gpu.vram_str}")
        lines.append(f" Power:   [{gpu.power_bar}] {gpu.power_watts} / {gpu.power_limit_watts} W ({gpu.power_pct:.0f}%)")
        lines.append("")
        temp_icon = THERMAL_ICONS.get(gpu.temp_status, "❓")
        lines.append(f" Temp:    {temp_icon} {gpu.temperature_c}°C")
        lines.append(f" Fan:     {gpu.fan_speed_pct}%")
        lines.append(f" Encoder: {gpu.encoder_usage_pct:.0f}%  Decoder: {gpu.decoder_usage_pct:.0f}%")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_memory(self, width: int = 70) -> List[str]:
        ram = self._ram
        lines = []
        lines.append(" 🧠 Memory")
        lines.append("─" * width)
        lines.append(f" RAM:     [{ram.usage_bar}] {ram.used_str} / {ram.total_str} ({ram.usage_pct:.0f}%)")
        lines.append(f" Cached:  {ram.cached_mb} MB  Buffers: {ram.buffers_mb} MB")
        lines.append(f" Swap:    [{ram.swap_bar}] {ram.swap_used_mb} / {ram.swap_total_mb} MB ({ram.swap_usage_pct:.0f}%)")
        lines.append("")

        if ram.top_processes:
            lines.append(" Top Processes:")
            max_name = max(len(name) for name, _ in ram.top_processes)
            for name, mb in ram.top_processes[:8]:
                bar_len = int(mb / max(p[1] for p in ram.top_processes) * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(f"  {name:<{max_name}} {bar} {mb} MB")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_fans(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 💨 Fan Control")
        lines.append("─" * width)

        for i, fan in enumerate(self._fans):
            marker = "▸" if i == self._selected_index else " "
            mode_icon = FAN_MODE_ICONS.get(fan.mode, "🔄")
            lines.append(f"{marker} {mode_icon} {fan.name}")
            lines.append(f"   Speed: [{fan.rpm_bar}] {fan.speed_rpm} / {fan.max_rpm} RPM ({fan.speed_pct}%)")
            lines.append(f"   Mode:  {fan.mode.value.title()} | Status: {fan.status}")
            if fan.temp_curve:
                curve_str = " → ".join(f"{t}°C:{f}%" for t, f in fan.temp_curve[:4])
                lines.append(f"   Curve: {curve_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  M:Mode cycle  +/-:Speed  Esc:Back")
        return lines

    def render_temps(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🌡️  Temperature Sensors")
        lines.append("─" * width)

        for sensor in self._sensors:
            lines.append(f" {sensor.icon} {sensor.name:<18s} {sensor.temperature_c:>3d}°C  [{sensor.bar}]")
            lines.append(f"   High: {sensor.high_c}°C  Critical: {sensor.critical_c}°C")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "cpu": self.render_cpu,
            "gpu": self.render_gpu,
            "memory": self.render_memory,
            "fans": self.render_fans,
            "temps": self.render_temps,
        }
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "fans":
            return self._handle_fans_key(key)
        elif self._view_mode == "temps":
            if key == "Escape":
                self.set_view("overview")
                return "back"
            return None
        elif self._view_mode in ("cpu", "gpu", "memory"):
            if key == "Escape":
                self.set_view("overview")
                return "back"
            return None
        return self._handle_overview_key(key)

    def _handle_overview_key(self, key: str) -> Optional[str]:
        if key == "c":
            self.set_view("cpu")
            return "cpu"
        elif key == "g":
            self.set_view("gpu")
            return "gpu"
        elif key == "r":
            self.set_view("memory")
            return "memory"
        elif key == "f":
            self.set_view("fans")
            return "fans"
        elif key == "t":
            self.set_view("temps")
            return "temps"
        return None

    def _handle_fans_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "m":
            fan = self.get_selected_item()
            if fan:
                modes = list(FanMode)
                idx = modes.index(fan.mode)
                fan.mode = modes[(idx + 1) % len(modes)]
                return "cycle_mode"
        elif key == "+" or key == "=":
            fan = self.get_selected_item()
            if fan:
                self.set_fan_speed(self._selected_index, min(100, fan.speed_pct + 10))
                return "speed_up"
        elif key == "-":
            fan = self.get_selected_item()
            if fan:
                self.set_fan_speed(self._selected_index, max(0, fan.speed_pct - 10))
                return "speed_down"
        return None
