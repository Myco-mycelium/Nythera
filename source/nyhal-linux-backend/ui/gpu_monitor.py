"""
Nyrqis OS - GPU Monitor
VRAM usage, temperature graphs, and compute utilization.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class GPUVendor(Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"


class GPUComputeMode(Enum):
    DEFAULT = "default"
    EXCLUSIVE = "exclusive"
    PROHIBITED = "prohibited"
    EXCLUSIVE_PROCESS = "exclusive_process"


@dataclass
class GPUTemperature:
    current: float = 0.0
    min_recorded: float = 0.0
    max_recorded: float = 0.0
    hotspot: float = 0.0
    memory: float = 0.0
    history: List[float] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.current < 50:
            return "🟢 Cool"
        elif self.current < 70:
            return "🟡 Warm"
        elif self.current < 85:
            return "🟠 Hot"
        return "🔴 Critical"

    @property
    def bar(self) -> str:
        filled = int(self.current / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def trend(self) -> str:
        if len(self.history) < 2:
            return "→"
        if self.history[-1] > self.history[-2] + 1:
            return "↑"
        elif self.history[-1] < self.history[-2] - 1:
            return "↓"
        return "→"


@dataclass
class GPUMemory:
    total_gb: float = 0.0
    used_gb: float = 0.0
    reserved_gb: float = 0.0
    bar1_used_mb: float = 0.0

    @property
    def free_gb(self) -> float:
        return self.total_gb - self.used_gb

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
    def display(self) -> str:
        return f"{self.used_gb:.1f} / {self.total_gb:.0f} GB"


@dataclass
class GPUPower:
    current_watts: float = 0.0
    max_watts: float = 0.0
    tdp_watts: float = 0.0
    power_limit_pct: float = 100.0
    history: List[float] = field(default_factory=list)

    @property
    def usage_percent(self) -> float:
        if self.max_watts == 0:
            return 0.0
        return (self.current_watts / self.max_watts) * 100

    @property
    def bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def display(self) -> str:
        return f"{self.current_watts:.0f} / {self.max_watts:.0f} W"


@dataclass
class GPUProcess:
    pid: int = 0
    process_name: str = ""
    gpu_memory_mb: float = 0.0
    gpu_utilization: float = 0.0
    sm_utilization: float = 0.0
    encoder_utilization: float = 0.0
    decoder_utilization: float = 0.0
    compute_type: str = ""

    @property
    def mem_display(self) -> str:
        return f"{self.gpu_memory_mb:.0f} MB"

    @property
    def util_bar(self) -> str:
        filled = int(self.gpu_utilization / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class GPUMetrics:
    timestamp: float = 0.0
    gpu_utilization: float = 0.0
    memory_utilization: float = 0.0
    temperature: float = 0.0
    power_watts: float = 0.0
    clock_mhz: int = 0
    memory_clock_mhz: int = 0
    encoder_util: float = 0.0
    decoder_util: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class GPUMonitor:
    def __init__(self):
        self.vendor: GPUVendor = GPUVendor.NVIDIA
        self.model: str = ""
        self.driver_version: str = ""
        self.cuda_version: str = ""
        self.vbios: str = ""
        self.compute_mode: GPUComputeMode = GPUComputeMode.DEFAULT
        self.temperature = GPUTemperature()
        self.memory = GPUMemory()
        self.power = GPUPower()
        self.processes: List[GPUProcess] = []
        self.metrics_history: List[GPUMetrics] = []
        self.fan_speed_rpm: int = 0
        self.fan_speed_pct: int = 0
        self.pcie_gen: int = 4
        self.pcie_width: int = 16
        self.pcie_throughput_rx_gbps: float = 0.0
        self.pcie_throughput_tx_gbps: float = 0.0
        self.vulkan_version: str = ""
        self.opengl_version: str = ""
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.vendor = GPUVendor.NVIDIA
        self.model = "NVIDIA GeForce RTX 4090"
        self.driver_version = "535.129.03"
        self.cuda_version = "12.2"
        self.vbios = "94.02.71.00.01"
        self.vulkan_version = "1.3.250"
        self.opengl_version = "4.6"
        self.fan_speed_rpm = 1200
        self.fan_speed_pct = 35
        self.pcie_throughput_rx_gbps = 12.5
        self.pcie_throughput_tx_gbps = 4.8

        self.temperature = GPUTemperature(
            current=55.0, min_recorded=28.0, max_recorded=82.0,
            hotspot=62.0, memory=48.0,
            history=[50, 51, 52, 53, 54, 55, 54, 53, 52, 53, 54, 55])

        self.memory = GPUMemory(total_gb=24.0, used_gb=8.2, reserved_gb=1.5,
                                  bar1_used_mb=256)

        self.power = GPUPower(current_watts=285.0, max_watts=450.0,
                               tdp_watts=450.0, power_limit_pct=100,
                               history=[260, 270, 275, 280, 285, 280, 275, 270])

        self.processes = [
            GPUProcess(pid=200, process_name="firefox", gpu_memory_mb=1200,
                       gpu_utilization=25.0, sm_utilization=30.0,
                       encoder_utilization=15.0, decoder_utilization=20.0,
                       compute_type="Graphics"),
            GPUProcess(pid=300, process_name="code-server", gpu_memory_mb=800,
                       gpu_utilization=12.0, sm_utilization=15.0,
                       compute_type="Graphics"),
            GPUProcess(pid=400, process_name="nyrqis-compositor", gpu_memory_mb=512,
                       gpu_utilization=45.0, sm_utilization=50.0,
                       encoder_utilization=30.0, compute_type="Graphics"),
            GPUProcess(pid=500, process_name="python3 ml-training", gpu_memory_mb=4096,
                       gpu_utilization=85.0, sm_utilization=92.0,
                       compute_type="Compute"),
        ]

        for i in range(30):
            self.metrics_history.append(GPUMetrics(
                timestamp=now - (30 - i) * 60,
                gpu_utilization=random.uniform(20, 90),
                memory_utilization=random.uniform(25, 50),
                temperature=random.uniform(48, 58),
                power_watts=random.uniform(200, 300),
                clock_mhz=random.randint(2000, 2600),
                memory_clock_mhz=random.randint(9000, 10500),
                encoder_util=random.uniform(10, 40),
                decoder_util=random.uniform(5, 30)))

    def get_gpu_utilization(self) -> float:
        if not self.metrics_history:
            return 0.0
        return self.metrics_history[-1].gpu_utilization

    def get_memory_utilization(self) -> float:
        return self.memory.usage_percent

    def get_temperature_history(self) -> List[float]:
        return [m.temperature for m in self.metrics_history]

    def get_power_history(self) -> List[float]:
        return [m.power_watts for m in self.metrics_history]

    def get_utilization_history(self) -> List[float]:
        return [m.gpu_utilization for m in self.metrics_history]

    def get_top_processes(self, limit: int = 5) -> List[GPUProcess]:
        return sorted(self.processes, key=lambda p: p.gpu_memory_mb, reverse=True)[:limit]

    def get_encoder_processes(self) -> List[GPUProcess]:
        return [p for p in self.processes if p.encoder_utilization > 0]

    def get_compute_processes(self) -> List[GPUProcess]:
        return [p for p in self.processes if p.compute_type == "Compute"]

    def get_stats(self) -> Dict:
        return {
            "model": self.model,
            "driver": self.driver_version,
            "temperature": self.temperature.current,
            "memory_used": self.memory.used_gb,
            "memory_total": self.memory.total_gb,
            "power": self.power.current_watts,
            "processes": len(self.processes),
            "fan_speed": self.fan_speed_pct,
        }
