"""System Info Tool — Hardware details, driver info, and benchmark results.

Features:
- CPU information with cache and topology
- GPU details with driver version
- Memory configuration
- Storage device information
- Network adapter details
- Motherboard and BIOS info
- OS and kernel information
- Benchmark results (CPU, GPU, disk, memory)
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class InfoCategory(Enum):
    OVERVIEW = "overview"
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    STORAGE = "storage"
    NETWORK = "network"
    MOTHERBOARD = "motherboard"
    DRIVERS = "drivers"
    BENCHMARKS = "benchmarks"

    @property
    def icon(self) -> str:
        icons = {
            InfoCategory.OVERVIEW: "🖥", InfoCategory.CPU: "⚙️", InfoCategory.GPU: "🎮",
            InfoCategory.MEMORY: "💾", InfoCategory.STORAGE: "💿", InfoCategory.NETWORK: "🌐",
            InfoCategory.MOTHERBOARD: "🔧", InfoCategory.DRIVERS: "📦", InfoCategory.BENCHMARKS: "📊",
        }
        return icons.get(self, "?")


@dataclass
class CPUInfo:
    model: str = ""
    manufacturer: str = ""
    cores: int = 0
    threads: int = 0
    base_clock_ghz: float = 0.0
    boost_clock_ghz: float = 0.0
    tdp_watts: int = 0
    socket: str = ""
    architecture: str = ""
    cache_l1_kb: int = 0
    cache_l2_kb: int = 0
    cache_l3_mb: int = 0
    extensions: List[str] = field(default_factory=list)

    @property
    def core_config(self) -> str:
        return f"{self.cores}C/{self.threads}T"

    @property
    def clock_str(self) -> str:
        return f"{self.base_clock_ghz:.2f} / {self.boost_clock_ghz:.2f} GHz"

    @property
    def cache_str(self) -> str:
        return f"L1: {self.cache_l1_kb}KB  L2: {self.cache_l2_kb}KB  L3: {self.cache_l3_mb}MB"


@dataclass
class GPUInfo:
    model: str = ""
    manufacturer: str = ""
    vram_gb: float = 0.0
    vram_type: str = ""
    driver_version: str = ""
    cuda_cores: int = 0
    clock_mhz: int = 0
    memory_clock_mhz: int = 0
    bus_width: int = 0
    tdp_watts: int = 0
    vulkan_version: str = ""
    opengl_version: str = ""
    compute_capability: str = ""

    @property
    def vram_str(self) -> str:
        return f"{self.vram_gb:.0f}GB {self.vram_type}"

    @property
    def clock_str(self) -> str:
        return f"{self.clock_mhz}MHz / {self.memory_clock_mhz}MHz"

    @property
    def bandwidth_str(self) -> str:
        bw = self.memory_clock_mhz * 2 * (self.bus_width / 8) / 1000
        return f"{bw:.0f} GB/s"


@dataclass
class MemoryInfo:
    total_gb: float = 0.0
    speed_mhz: int = 0
    type: str = ""
    slots_used: int = 0
    slots_total: int = 0
    manufacturer: str = ""
    timings: str = ""

    @property
    def config_str(self) -> str:
        return f"{self.slots_used}/{self.slots_total} slots"

    @property
    def speed_str(self) -> str:
        return f"{self.speed_mhz}MHz {self.type}"


@dataclass
class StorageDevice:
    model: str = ""
    type: str = ""  # NVMe, SATA SSD, HDD
    capacity_gb: float = 0.0
    interface: str = ""
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    health_pct: float = 100.0
    temperature: float = 35.0
    firmware: str = ""
    serial: str = ""

    @property
    def capacity_str(self) -> str:
        if self.capacity_gb < 1000:
            return f"{self.capacity_gb:.0f}GB"
        return f"{self.capacity_gb / 1000:.0f}TB"

    @property
    def health_bar(self) -> str:
        filled = min(20, int(self.health_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def health_icon(self) -> str:
        if self.health_pct >= 90:
            return "🟢"
        if self.health_pct >= 70:
            return "🟡"
        return "🔴"


@dataclass
class NetworkAdapter:
    name: str = ""
    type: str = ""  # Ethernet, WiFi
    mac: str = ""
    ip: str = ""
    speed_mbps: int = 0
    driver: str = ""
    status: str = "up"

    @property
    def status_icon(self) -> str:
        return "🟢" if self.status == "up" else "🔴"

    @property
    def speed_str(self) -> str:
        if self.speed_mbps >= 1000:
            return f"{self.speed_mbps // 1000} Gbps"
        return f"{self.speed_mbps} Mbps"


@dataclass
class DriverInfo:
    component: str = ""
    name: str = ""
    version: str = ""
    status: str = ""  # loaded, active, error
    date: str = ""

    @property
    def status_icon(self) -> str:
        icons = {"loaded": "🟢", "active": "🟢", "error": "🔴", "missing": "⚪"}
        return icons.get(self.status, "❓")


@dataclass
class BenchmarkResult:
    test_name: str = ""
    score: float = 0.0
    max_score: float = 100.0
    unit: str = "points"
    percentile: float = 0.0
    timestamp: float = 0.0

    @property
    def score_pct(self) -> float:
        if self.max_score == 0:
            return 0.0
        return min(100, self.score / self.max_score * 100)

    @property
    def score_bar(self) -> str:
        filled = min(20, int(self.score_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def percentile_str(self) -> str:
        return f"Top {100 - self.percentile:.0f}%"

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.timestamp))


class SysInfo:
    def __init__(self):
        self._cpu = CPUInfo()
        self._gpu = GPUInfo()
        self._memory = MemoryInfo()
        self._storage: List[StorageDevice] = []
        self._network: List[NetworkAdapter] = []
        self._drivers: List[DriverInfo] = []
        self._benchmarks: List[BenchmarkResult] = []
        self._view_mode: InfoCategory = InfoCategory.OVERVIEW
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._cpu = CPUInfo(
            "AMD Ryzen 9 7950X", "AMD", 16, 32, 4.5, 5.7, 170,
            "AM5", "Zen 4", 1024, 16384, 64,
            ["SSE4.2", "AVX2", "AVX-512", "BMI2", "AES-NI", "SHA"],
        )

        self._gpu = GPUInfo(
            "NVIDIA GeForce RTX 4070", "NVIDIA", 12, "GDDR6X",
            "551.86", 5888, 2310, 10501, 192, 200,
            "1.3.253", "4.6", "8.9",
        )

        self._memory = MemoryInfo(
            64.0, 5600, "DDR5", 2, 4,
            "G.Skill Trident Z5", "36-36-36-96",
        )

        self._storage = [
            StorageDevice("Samsung 990 PRO 2TB", "NVMe", 2000, "PCIe 4.0 x4",
                          7450, 6900, 97, 38, "5B2QGXA7", "S6EFNX0T123456"),
            StorageDevice("Samsung 970 EVO Plus 1TB", "NVMe", 1000, "PCIe 3.0 x4",
                          3500, 3300, 92, 42, "2B2QEXM7", "S5EVNX0R654321"),
            StorageDevice("WD Red Plus 4TB", "HDD", 4000, "SATA III",
                          180, 180, 85, 35, "81.00A81", "WD-WCC7K0VE1234"),
        ]

        self._network = [
            NetworkAdapter("enp5s0", "Ethernet", "AA:BB:CC:DD:EE:FF", "192.168.1.100",
                           2500, "r8169", "up"),
            NetworkAdapter("wlp6s0", "WiFi 6E", "11:22:33:44:55:66", "192.168.1.101",
                           2400, "iwlwifi", "up"),
        ]

        self._drivers = [
            DriverInfo("GPU", "nvidia", "551.86", "loaded", "2024-05-15"),
            DriverInfo("GPU (Open)", "nouveau", "1.0.17", "loaded", "2024-01-10"),
            DriverInfo("Network", "r8169", "6.038", "loaded", "2024-03-20"),
            DriverInfo("WiFi", "iwlwifi", "7.5.0", "loaded", "2024-04-15"),
            DriverInfo("Audio", "snd_hda_intel", "6.8.0", "loaded", "2024-02-28"),
            DriverInfo("Storage", "nvme", "6.8.0", "loaded", "2024-02-28"),
            DriverInfo("Input", "evdev", "6.8.0", "loaded", "2024-02-28"),
        ]

        self._benchmarks = [
            BenchmarkResult("CPU Single Core", 2150, 3000, "points", 85, now - 86400),
            BenchmarkResult("CPU Multi Core", 38500, 45000, "points", 82, now - 86400),
            BenchmarkResult("GPU (Vulkan)", 18500, 25000, "points", 75, now - 86400 * 2),
            BenchmarkResult("Memory Bandwidth", 85.2, 100, "GB/s", 80, now - 86400 * 3),
            BenchmarkResult("Disk Sequential Read", 7200, 8000, "MB/s", 70, now - 86400 * 4),
            BenchmarkResult("Disk Sequential Write", 6800, 7500, "MB/s", 72, now - 86400 * 4),
            BenchmarkResult("Disk Random 4K", 1200, 1500, "KOPS", 65, now - 86400 * 4),
        ]

    def set_view(self, category: InfoCategory):
        self._view_mode = category

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM INFORMATION                               ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🖥 AMD Ryzen 9 7950X  🎮 RTX 4070  💾 64GB DDR5  💿 3 drives  🌐 2 NICs  📦 {len(self._drivers)} drivers")
        lines.append("")

        if self._view_mode == InfoCategory.OVERVIEW:
            lines.append("  ── System Overview ──")
            lines.append(f"  OS:     Nyrqis OS v2.1.0 (Linux 6.8.0-nyrqis)")
            lines.append(f"  CPU:    {self._cpu.model} ({self._cpu.core_config}) @ {self._cpu.clock_str}")
            lines.append(f"  GPU:    {self._gpu.model} {self._gpu.vram_str}")
            lines.append(f"  RAM:    {self._memory.total_gb:.0f}GB {self._memory.speed_str} ({self._memory.config_str})")
            lines.append(f"  Boot:   {self._storage[0].model} ({self._storage[0].capacity_str})")
            lines.append(f"  Net:    {self._network[0].name} ({self._network[0].speed_str})")

        elif self._view_mode == InfoCategory.CPU:
            lines.append("  ── CPU Information ──")
            c = self._cpu
            lines.append(f"  Model:    {c.model}")
            lines.append(f"  Cores:    {c.core_config}  Base: {c.base_clock_ghz}GHz  Boost: {c.boost_clock_ghz}GHz")
            lines.append(f"  Socket:   {c.socket}  Architecture: {c.architecture}  TDP: {c.tdp_watts}W")
            lines.append(f"  Cache:    {c.cache_str}")
            lines.append(f"  Extensions: {', '.join(c.extensions)}")

        elif self._view_mode == InfoCategory.GPU:
            lines.append("  ── GPU Information ──")
            g = self._gpu
            lines.append(f"  Model:    {g.manufacturer} {g.model}")
            lines.append(f"  VRAM:     {g.vram_str}")
            lines.append(f"  Clocks:   {g.clock_str}")
            lines.append(f"  Cores:    {g.cuda_cores} CUDA cores  Bus: {g.bus_width}bit  BW: {g.bandwidth_str}")
            lines.append(f"  Driver:   v{g.driver_version}  TDP: {g.tdp_watts}W")
            lines.append(f"  Vulkan:   {g.vulkan_version}  OpenGL: {g.opengl_version}")

        elif self._view_mode == InfoCategory.MEMORY:
            lines.append("  ── Memory Information ──")
            m = self._memory
            lines.append(f"  Total:    {m.total_gb:.0f}GB")
            lines.append(f"  Type:     {m.speed_str}")
            lines.append(f"  Config:   {m.config_str}")
            lines.append(f"  Modules:  {m.manufacturer}")
            lines.append(f"  Timings:  {m.timings}")

        elif self._view_mode == InfoCategory.STORAGE:
            lines.append("  ── Storage Devices ──")
            for s in self._storage:
                lines.append(f"  💿 {s.model}")
                lines.append(f"     Type: {s.type}  Interface: {s.interface}  Capacity: {s.capacity_str}")
                lines.append(f"     Read: {s.read_speed_mbps:.0f} MB/s  Write: {s.write_speed_mbps:.0f} MB/s")
                lines.append(f"     Health: {s.health_icon} [{s.health_bar}] {s.health_pct:.0f}%  Temp: {s.temperature:.0f}°C  FW: {s.firmware}")

        elif self._view_mode == InfoCategory.NETWORK:
            lines.append("  ── Network Adapters ──")
            for n in self._network:
                lines.append(f"  {n.status_icon} {n.name} ({n.type})")
                lines.append(f"     MAC: {n.mac}  IP: {n.ip}  Speed: {n.speed_str}  Driver: {n.driver}")

        elif self._view_mode == InfoCategory.DRIVERS:
            lines.append("  ── Loaded Drivers ──")
            for d in self._drivers:
                lines.append(f"  {d.status_icon} {d.component:<12s} {d.name:<20s} v{d.version:<10s} {d.date}")

        elif self._view_mode == InfoCategory.BENCHMARKS:
            lines.append("  ── Benchmark Results ──")
            for b in self._benchmarks:
                lines.append(f"  📊 {b.test_name:<24s} [{b.score_bar}] {b.score:.0f} {b.unit}  {b.percentile_str}")
            lines.append("")
            avg_score = sum(b.score_pct for b in self._benchmarks) / len(self._benchmarks)
            lines.append(f"  Overall Score: {avg_score:.0f}/100")

        lines.append("")
        lines.append("  [O]verview [C]PU [G]PU [M]emory [S]torage [N]etwork [D]rivers [B]enchmarks")
        return lines
