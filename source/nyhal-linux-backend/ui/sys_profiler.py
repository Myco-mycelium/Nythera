"""
Nyrqis OS - System Profiler
CPU/RAM/GPU benchmarks, hardware info, and performance analysis.
"""

import time
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class BenchmarkStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CPUInfo:
    model: str = ""
    vendor: str = ""
    cores: int = 0
    threads: int = 0
    base_clock_ghz: float = 0.0
    boost_clock_ghz: float = 0.0
    cache_l2_kb: int = 0
    cache_l3_kb: int = 0
    architecture: str = ""
    instruction_set: str = ""
    temperature_c: float = 0.0
    usage_percent: float = 0.0
    power_watts: float = 0.0

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 50:
            return "🟢 Cool"
        elif self.temperature_c < 75:
            return "🟡 Warm"
        return "🔴 Hot"

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class RAMInfo:
    total_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    type: str = "DDR5"
    speed_mhz: int = 0
    slots: int = 0
    modules: int = 0
    manufacturer: str = ""
    timings: str = ""
    ecc: bool = False

    @property
    def usage_percent(self) -> float:
        if self.total_gb == 0:
            return 0.0
        return (self.used_gb / self.total_gb) * 100

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class GPUInfo:
    model: str = ""
    vendor: str = ""
    vram_gb: float = 0.0
    vram_used_gb: float = 0.0
    driver_version: str = ""
    temperature_c: float = 0.0
    usage_percent: float = 0.0
    fan_speed_rpm: int = 0
    power_watts: float = 0.0
    clock_mhz: int = 0
    memory_clock_mhz: int = 0
    vulkan_version: str = ""
    opengl_version: str = ""
    compute_units: int = 0

    @property
    def vram_bar(self) -> str:
        pct = (self.vram_used_gb / self.vram_gb * 100) if self.vram_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 50:
            return "🟢 Cool"
        elif self.temperature_c < 80:
            return "🟡 Warm"
        return "🔴 Hot"


@dataclass
class StorageInfo:
    device: str = ""
    model: str = ""
    type: str = "NVMe"
    capacity_gb: float = 0.0
    used_gb: float = 0.0
    temperature_c: float = 0.0
    health_percent: float = 100.0
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    total_bytes_written_tb: float = 0.0
    power_on_hours: int = 0

    @property
    def usage_bar(self) -> str:
        pct = (self.used_gb / self.capacity_gb * 100) if self.capacity_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def health_status(self) -> str:
        if self.health_percent > 90:
            return "🟢 Excellent"
        elif self.health_percent > 70:
            return "🟡 Good"
        return "🔴 Warning"


@dataclass
class BenchmarkResult:
    name: str
    score: float = 0.0
    max_score: float = 100.0
    duration_s: float = 0.0
    status: BenchmarkStatus = BenchmarkStatus.IDLE
    details: Dict[str, float] = field(default_factory=dict)

    @property
    def score_percent(self) -> float:
        if self.max_score == 0:
            return 0.0
        return (self.score / self.max_score) * 100

    @property
    def score_bar(self) -> str:
        filled = int(self.score_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def grade(self) -> str:
        pct = self.score_percent
        if pct >= 90:
            return "A+"
        elif pct >= 80:
            return "A"
        elif pct >= 70:
            return "B"
        elif pct >= 60:
            return "C"
        elif pct >= 50:
            return "D"
        return "F"

    @property
    def status_icon(self) -> str:
        icons = {
            BenchmarkStatus.IDLE: "⏸",
            BenchmarkStatus.RUNNING: "🔄",
            BenchmarkStatus.COMPLETED: "✅",
            BenchmarkStatus.FAILED: "❌",
        }
        return icons.get(self.status, "?")


class SystemProfiler:
    def __init__(self):
        self.cpu = CPUInfo()
        self.ram = RAMInfo()
        self.gpu = GPUInfo()
        self.storage: List[StorageInfo] = []
        self.benchmarks: List[BenchmarkResult] = []
        self.benchmark_history: List[List[BenchmarkResult]] = []
        self.active_benchmark: Optional[BenchmarkResult] = None
        self._create_sample_data()

    def _create_sample_data(self):
        self.cpu = CPUInfo(
            model="AMD Ryzen 9 7950X", vendor="AMD",
            cores=16, threads=32, base_clock_ghz=4.5, boost_clock_ghz=5.7,
            cache_l2_kb=16384, cache_l3_kb=65536, architecture="Zen 4",
            instruction_set="x86-64-v4", temperature_c=62,
            usage_percent=34.5, power_watts=125.0,
        )
        self.ram = RAMInfo(
            total_gb=64.0, used_gb=28.3, available_gb=35.7,
            type="DDR5-6000", speed_mhz=6000, slots=4, modules=2,
            manufacturer="G.Skill", timings="30-40-40-96", ecc=False,
        )
        self.gpu = GPUInfo(
            model="NVIDIA GeForce RTX 4090", vendor="NVIDIA",
            vram_gb=24.0, vram_used_gb=8.2, driver_version="535.129.03",
            temperature_c=55, usage_percent=45.0, fan_speed_rpm=1200,
            power_watts=285.0, clock_mhz=2520, memory_clock_mhz=10501,
            vulkan_version="1.3.250", opengl_version="4.6", compute_units=128,
        )
        self.storage = [
            StorageInfo(device="/dev/nvme0n1", model="Samsung 990 Pro 2TB",
                         type="NVMe", capacity_gb=2000.0, used_gb=850.0,
                         temperature_c=42, health_percent=98,
                         read_speed_mbps=7450.0, write_speed_mbps=6900.0,
                         total_bytes_written_tb=12.5, power_on_hours=3200),
            StorageInfo(device="/dev/sda", model="Samsung 870 EVO 1TB",
                         type="SATA SSD", capacity_gb=1000.0, used_gb=420.0,
                         temperature_c=35, health_percent=95,
                         read_speed_mbps=560.0, write_speed_mbps=530.0,
                         total_bytes_written_tb=45.0, power_on_hours=12000),
            StorageInfo(device="/dev/sdb", model="WD Red Plus 4TB",
                         type="HDD", capacity_gb=4000.0, used_gb=2800.0,
                         temperature_c=38, health_percent=92,
                         read_speed_mbps=180.0, write_speed_mbps=175.0,
                         total_bytes_written_tb=120.0, power_on_hours=25000),
        ]
        self.benchmarks = [
            BenchmarkResult(name="CPU Single-Core", score=2180, max_score=3000,
                            duration_s=45.0, status=BenchmarkStatus.COMPLETED,
                            details={"Integer": 2350, "Float": 2100, "String": 1980, "Sort": 2200}),
            BenchmarkResult(name="CPU Multi-Core", score=24500, max_score=30000,
                            duration_s=120.0, status=BenchmarkStatus.COMPLETED,
                            details={"Integer": 25000, "Float": 23800, "Thread": 24200, "Encrypt": 25100}),
            BenchmarkResult(name="Memory Bandwidth", score=85.2, max_score=100.0,
                            duration_s=30.0, status=BenchmarkStatus.COMPLETED,
                            details={"Read": 89.5, "Write": 82.1, "Copy": 84.0, "Latency": 62.3}),
            BenchmarkResult(name="Disk Sequential", score=6800, max_score=8000,
                            duration_s=60.0, status=BenchmarkStatus.COMPLETED,
                            details={"Read": 7450, "Write": 6900, "IOPS": 1200000}),
            BenchmarkResult(name="GPU Compute", score=320000, max_score=400000,
                            duration_s=90.0, status=BenchmarkStatus.COMPLETED,
                            details={"FP32": 350000, "FP16": 680000, "INT8": 890000}),
            BenchmarkResult(name="GPU Render", score=185, max_score=250,
                            duration_s=60.0, status=BenchmarkStatus.COMPLETED,
                            details={"OpenGL": 190, "Vulkan": 185, "RayTracing": 165}),
            BenchmarkResult(name="Network Throughput", score=9.2, max_score=10.0,
                            duration_s=30.0, status=BenchmarkStatus.COMPLETED,
                            details={"Download": 9.4, "Upload": 8.8, "Latency": 2.1}),
        ]

    def run_benchmark(self, name: str) -> BenchmarkResult:
        result = next((b for b in self.benchmarks if b.name == name), None)
        if not result:
            result = BenchmarkResult(name=name, status=BenchmarkStatus.RUNNING)
            self.benchmarks.append(result)
        result.status = BenchmarkStatus.RUNNING
        result.score = result.max_score * random.uniform(0.7, 0.95)
        result.status = BenchmarkStatus.COMPLETED
        self.active_benchmark = result
        return result

    def run_all_benchmarks(self) -> List[BenchmarkResult]:
        results = []
        for bench in self.benchmarks:
            results.append(self.run_benchmark(bench.name))
        self.benchmark_history.append(results)
        return results

    def get_overall_score(self) -> float:
        if not self.benchmarks:
            return 0.0
        scores = [b.score_percent for b in self.benchmarks if b.status == BenchmarkStatus.COMPLETED]
        return sum(scores) / len(scores) if scores else 0.0

    def get_system_summary(self) -> Dict:
        return {
            "cpu": f"{self.cpu.model} ({self.cpu.cores}C/{self.cpu.threads}T)",
            "ram": f"{self.ram.total_gb} GB {self.ram.type}",
            "gpu": f"{self.gpu.model} ({self.gpu.vram_gb} GB)",
            "storage": f"{len(self.storage)} drives ({sum(s.capacity_gb for s in self.storage):.0f} GB total)",
            "overall_score": round(self.get_overall_score(), 1),
        }

    def get_power_consumption(self) -> Dict:
        return {
            "cpu_watts": self.cpu.power_watts,
            "gpu_watts": self.gpu.power_watts,
            "estimated_system_watts": self.cpu.power_watts + self.gpu.power_watts + 80,
        }

    def compare_benchmarks(self, idx1: int, idx2: int) -> Dict:
        if idx1 >= len(self.benchmark_history) or idx2 >= len(self.benchmark_history):
            return {}
        h1 = self.benchmark_history[idx1]
        h2 = self.benchmark_history[idx2]
        result = {}
        for b1 in h1:
            b2 = next((b for b in h2 if b.name == b1.name), None)
            if b2:
                diff = b2.score - b1.score
                pct = (diff / b1.score * 100) if b1.score else 0
                result[b1.name] = {"before": b1.score, "after": b2.score, "diff": diff, "pct": round(pct, 1)}
        return result
