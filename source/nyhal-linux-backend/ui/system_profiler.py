"""
Nyrqis System Profiler — system profiling and benchmarking application.

Features:
- Hardware information summary
- CPU benchmark suite (single/multi core)
- Disk benchmark (sequential/random read/write)
- Memory bandwidth test
- GPU compute benchmark
- Network throughput test
- System score comparison
- Export hardware report
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


class BenchmarkCategory(Enum):
    CPU = "CPU"
    MEMORY = "Memory"
    DISK = "Disk"
    GPU = "GPU"
    NETWORK = "Network"
    OVERALL = "Overall"


class TestStatus(Enum):
    NOT_RUN = "not_run"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


CATEGORY_ICONS = {
    BenchmarkCategory.CPU: "🖥️",
    BenchmarkCategory.MEMORY: "🧠",
    BenchmarkCategory.DISK: "💾",
    BenchmarkCategory.GPU: "🎮",
    BenchmarkCategory.NETWORK: "🌐",
    BenchmarkCategory.OVERALL: "📊",
}


@dataclass
class BenchmarkResult:
    """Result of a single benchmark test."""
    test_name: str
    category: BenchmarkCategory
    score: float = 0.0
    unit: str = "pts"
    details: Dict[str, float] = field(default_factory=dict)
    status: TestStatus = TestStatus.NOT_RUN
    duration_seconds: float = 0.0
    timestamp: float = 0.0

    @property
    def score_str(self) -> str:
        if self.score >= 1000000:
            return f"{self.score / 1000000:.1f}M {self.unit}"
        elif self.score >= 1000:
            return f"{self.score / 1000:.1f}K {self.unit}"
        return f"{self.score:.1f} {self.unit}"

    @property
    def status_icon(self) -> str:
        return {"not_run": "⬜", "running": "🔄", "completed": "✅", "failed": "❌"}.get(self.status.value, "❓")

    @property
    def bar(self) -> str:
        # Score normalized to 100 (assuming max ~10000 for most benchmarks)
        pct = min(100, self.score / 100)
        filled = int(pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class HardwareInfo:
    """System hardware information."""
    # CPU
    cpu_model: str = "AMD Ryzen 9 7950X"
    cpu_cores: int = 16
    cpu_threads: int = 32
    cpu_base_ghz: float = 4.5
    cpu_boost_ghz: float = 5.7
    cpu_cache_mb: int = 64
    cpu_arch: str = "x86_64"
    # Memory
    ram_total_gb: int = 64
    ram_type: str = "DDR5-6000"
    ram_speed_mhz: int = 6000
    ram_modules: int = 2
    # Storage
    storage_type: str = "NVMe SSD"
    storage_model: str = "Samsung 990 PRO 2TB"
    storage_capacity_gb: int = 2000
    storage_interface: str = "PCIe 4.0 x4"
    # GPU
    gpu_model: str = "NVIDIA GeForce RTX 4070"
    gpu_vram_gb: int = 12
    gpu_driver: str = "550.100"
    # Motherboard
    mobo_model: str = "ASUS ROG STRIX X670E-E"
    mobo_chipset: str = "AMD X670E"
    # Network
    nic_model: str = "Intel I225-V"
    nic_speed: str = "2.5 GbE"
    # OS
    os_name: str = "Nyrqis OS"
    os_version: str = "1.0.0"
    kernel: str = "6.11.0-nyrqis"
    # Benchmarks
    benchmarks: List[BenchmarkResult] = field(default_factory=list)

    @property
    def total_benchmarks(self) -> int:
        return len(self.benchmarks)

    @property
    def completed_benchmarks(self) -> int:
        return sum(1 for b in self.benchmarks if b.status == TestStatus.COMPLETED)

    @property
    def overall_score(self) -> float:
        completed = [b for b in self.benchmarks if b.status == TestStatus.COMPLETED and b.category != BenchmarkCategory.OVERALL]
        if not completed:
            return 0.0
        return sum(b.score for b in completed) / len(completed)


@dataclass
class SystemComparison:
    """Comparison with reference systems."""
    name: str
    cpu_score: float = 0.0
    mem_score: float = 0.0
    disk_score: float = 0.0
    gpu_score: float = 0.0
    overall: float = 0.0

    @property
    def overall_str(self) -> str:
        return f"{self.overall:.0f}"


# ─── System Profiler ─────────────────────────────────────────────────────


class SystemProfiler:
    """
    System profiling and benchmarking for Nyrqis OS.
    """

    def __init__(self):
        self._hardware = HardwareInfo()
        self._comparisons: List[SystemComparison] = []
        self._selected_index: int = 0
        self._view_mode: str = "overview"  # overview, benchmarks, hardware, comparison, export
        self._benchmark_running: bool = False

        self._init_benchmarks()
        self._init_comparisons()

    def _init_benchmarks(self) -> None:
        random.seed(42)
        self._hardware.benchmarks = [
            BenchmarkResult("Single-Core", BenchmarkCategory.CPU,
                            2850, "pts", {"AES": 12500, "SHA-256": 9800, "zstd": 11200},
                            TestStatus.COMPLETED, 45.2),
            BenchmarkResult("Multi-Core", BenchmarkCategory.CPU,
                            38500, "pts", {"AES": 98000, "SHA-256": 85000, "zstd": 92000},
                            TestStatus.COMPLETED, 120.5),
            BenchmarkResult("Read Sequential", BenchmarkCategory.MEMORY,
                            72000, "MB/s", {}, TestStatus.COMPLETED, 30.0),
            BenchmarkResult("Write Sequential", BenchmarkCategory.MEMORY,
                            68000, "MB/s", {}, TestStatus.COMPLETED, 30.0),
            BenchmarkResult("Copy", BenchmarkCategory.MEMORY,
                            65000, "MB/s", {}, TestStatus.COMPLETED, 20.0),
            BenchmarkResult("Latency", BenchmarkCategory.MEMORY,
                            65, "ns", {}, TestStatus.COMPLETED, 10.0),
            BenchmarkResult("Seq Read", BenchmarkCategory.DISK,
                            7100, "MB/s", {"4K Random Read": 1200000, "QD32": 7500},
                            TestStatus.COMPLETED, 60.0),
            BenchmarkResult("Seq Write", BenchmarkCategory.DISK,
                            6900, "MB/s", {"4K Random Write": 1100000, "QD32": 7200},
                            TestStatus.COMPLETED, 60.0),
            BenchmarkResult("CUDA Compute", BenchmarkCategory.GPU,
                            28500, "pts", {"FP32": 29.1, "FP16": 58.2, "Tensor": 232},
                            TestStatus.COMPLETED, 90.0),
            BenchmarkResult("OpenGL Render", BenchmarkCategory.GPU,
                            18500, "fps", {"1080p": 245, "1440p": 168, "4K": 82},
                            TestStatus.COMPLETED, 45.0),
            BenchmarkResult("TCP Throughput", BenchmarkCategory.NETWORK,
                            2350, "Mbps", {"Latency": 0.3, "Jitter": 0.05},
                            TestStatus.COMPLETED, 30.0),
        ]
        # Calculate overall
        completed = [b for b in self._hardware.benchmarks if b.status == TestStatus.COMPLETED]
        if completed:
            avg = sum(b.score for b in completed) / len(completed)
            self._hardware.benchmarks.append(
                BenchmarkResult("Overall Score", BenchmarkCategory.OVERALL, avg, "pts",
                                status=TestStatus.COMPLETED)
            )

    def _init_comparisons(self) -> None:
        self._comparisons = [
            SystemComparison("Your System", 2850, 72000, 7100, 28500, 28500),
            SystemComparison("High-End Desktop", 3200, 80000, 7500, 35000, 35000),
            SystemComparison("Mid-Range Desktop", 2200, 55000, 5500, 20000, 20000),
            SystemComparison("Gaming Laptop", 2500, 48000, 5000, 25000, 25000),
            SystemComparison("Office PC", 1800, 35000, 3000, 5000, 5000),
        ]

    # ── Benchmark Operations ──────────────────────────────────────────

    def run_benchmark(self, index: int) -> bool:
        if 0 <= index < len(self._hardware.benchmarks):
            bm = self._hardware.benchmarks[index]
            if bm.status != TestStatus.RUNNING:
                bm.status = TestStatus.RUNNING
                bm.timestamp = time.time()
                # Simulate completion
                bm.status = TestStatus.COMPLETED
                bm.duration_seconds = random.uniform(10, 120)
                return True
        return False

    def run_all_benchmarks(self) -> int:
        count = 0
        for bm in self._hardware.benchmarks:
            if bm.category != BenchmarkCategory.OVERALL and bm.status != TestStatus.COMPLETED:
                bm.status = TestStatus.COMPLETED
                bm.score = random.uniform(1000, 50000)
                bm.timestamp = time.time()
                bm.duration_seconds = random.uniform(10, 120)
                count += 1
        return count

    def export_report(self) -> str:
        hw = self._hardware
        lines = [
            f"═══ Nyrqis System Report ═══",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "── CPU ──",
            f" Model:    {hw.cpu_model}",
            f" Cores:    {hw.cpu_cores}c/{hw.cpu_threads}t",
            f" Clock:    {hw.cpu_base_ghz} GHz base / {hw.cpu_boost_ghz} GHz boost",
            f" Cache:    {hw.cpu_cache_mb} MB L3",
            f" Arch:     {hw.cpu_arch}",
            "",
            "── Memory ──",
            f" Total:    {hw.ram_total_gb} GB {hw.ram_type}",
            f" Speed:    {hw.ram_speed_mhz} MHz",
            f" Modules:  {hw.ram_modules}",
            "",
            "── Storage ──",
            f" Model:    {hw.storage_model}",
            f" Type:     {hw.storage_type} ({hw.storage_interface})",
            f" Capacity: {hw.storage_capacity_gb} GB",
            "",
            "── GPU ──",
            f" Model:    {hw.gpu_model}",
            f" VRAM:     {hw.gpu_vram_gb} GB",
            f" Driver:   {hw.gpu_driver}",
            "",
            "── Benchmarks ──",
        ]
        for bm in hw.benchmarks:
            if bm.status == TestStatus.COMPLETED:
                lines.append(f" {bm.test_name:<20s} {bm.score_str}")
        return "\n".join(lines)

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
        if self._view_mode == "benchmarks":
            return self._hardware.benchmarks
        elif self._view_mode == "comparison":
            return self._comparisons
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def hardware(self) -> HardwareInfo:
        return self._hardware

    @property
    def comparisons(self) -> List[SystemComparison]:
        return list(self._comparisons)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    # ── Rendering ─────────────────────────────────────────────────────

    def render_overview(self, width: int = 70) -> List[str]:
        hw = self._hardware
        lines = []
        lines.append(" 📊 System Profiler — Overview")
        lines.append("─" * width)

        # CPU
        lines.append(f" 🖥️  CPU: {hw.cpu_model}")
        lines.append(f"    {hw.cpu_cores} cores / {hw.cpu_threads} threads @ {hw.cpu_boost_ghz} GHz")
        lines.append(f"    Cache: {hw.cpu_cache_mb} MB L3")

        # Memory
        lines.append(f" 🧠 RAM: {hw.ram_total_gb} GB {hw.ram_type} @ {hw.ram_speed_mhz} MHz ({hw.ram_modules} modules)")

        # Storage
        lines.append(f" 💾 SSD: {hw.storage_model} ({hw.storage_interface})")
        lines.append(f"    {hw.storage_capacity_gb} GB")

        # GPU
        lines.append(f" 🎮 GPU: {hw.gpu_model} ({hw.gpu_vram_gb} GB VRAM)")

        # Motherboard
        lines.append(f" 🔧 Mobo: {hw.mobo_model} ({hw.mobo_chipset})")

        # Network
        lines.append(f" 🌐 NIC: {hw.nic_model} ({hw.nic_speed})")

        # OS
        lines.append(f" 🐧 OS: {hw.os_name} v{hw.os_version} (kernel {hw.kernel})")

        lines.append("─" * width)

        # Quick scores
        completed = [b for b in hw.benchmarks if b.status == TestStatus.COMPLETED]
        if completed:
            lines.append(f" 📈 Benchmarks: {hw.completed_benchmarks}/{hw.total_benchmarks} completed")
            overall = next((b for b in hw.benchmarks if b.category == BenchmarkCategory.OVERALL), None)
            if overall:
                lines.append(f" 🏆 Overall Score: {overall.score_str}")

        lines.append("─" * width)
        lines.append(" B:Benchmarks  H:Hardware details  C:Comparison  E:Export report")
        return lines

    def render_benchmarks(self, width: int = 70) -> List[str]:
        hw = self._hardware
        lines = []
        lines.append(" ⚡ Benchmark Suite")
        lines.append("─" * width)
        lines.append(f" {hw.completed_benchmarks}/{hw.total_benchmarks} completed")
        lines.append("─" * width)

        current_cat = None
        for i, bm in enumerate(hw.benchmarks):
            if bm.category != current_cat:
                current_cat = bm.category
                icon = CATEGORY_ICONS.get(current_cat, "❓")
                lines.append(f" {icon} {current_cat.value}")

            marker = "▸" if i == self._selected_index else " "
            lines.append(f" {marker} {bm.test_name:<20s} {bm.status_icon} {bm.score_str} [{bm.bar}]")
            if bm.duration_seconds > 0:
                lines.append(f"    Duration: {bm.duration_seconds:.1f}s")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Run selected  A:Run all  Esc:Back")
        return lines

    def render_hardware(self, width: int = 70) -> List[str]:
        hw = self._hardware
        lines = []
        lines.append(" 🔍 Hardware Details")
        lines.append("─" * width)

        lines.append(" CPU:")
        lines.append(f"   Model:      {hw.cpu_model}")
        lines.append(f"   Cores:      {hw.cpu_cores} ({hw.cpu_threads} threads)")
        lines.append(f"   Base:       {hw.cpu_base_ghz} GHz")
        lines.append(f"   Boost:      {hw.cpu_boost_ghz} GHz")
        lines.append(f"   L3 Cache:   {hw.cpu_cache_mb} MB")
        lines.append(f"   Arch:       {hw.cpu_arch}")
        lines.append("")

        lines.append(" Memory:")
        lines.append(f"   Total:      {hw.ram_total_gb} GB")
        lines.append(f"   Type:       {hw.ram_type}")
        lines.append(f"   Speed:      {hw.ram_speed_mhz} MHz")
        lines.append(f"   Modules:    {hw.ram_modules}")
        lines.append("")

        lines.append(" Storage:")
        lines.append(f"   Model:      {hw.storage_model}")
        lines.append(f"   Type:       {hw.storage_type}")
        lines.append(f"   Interface:  {hw.storage_interface}")
        lines.append(f"   Capacity:   {hw.storage_capacity_gb} GB")
        lines.append("")

        lines.append(" GPU:")
        lines.append(f"   Model:      {hw.gpu_model}")
        lines.append(f"   VRAM:       {hw.gpu_vram_gb} GB")
        lines.append(f"   Driver:     {hw.gpu_driver}")
        lines.append("")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_comparison(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📈 System Comparison")
        lines.append("─" * width)

        # Find max scores for scaling
        max_scores = {}
        for cat_name in ["cpu", "mem", "disk", "gpu", "overall"]:
            scores = [getattr(c, f"{cat_name}_score" if cat_name != "overall" else "overall", 0)
                      for c in self._comparisons]
            max_scores[cat_name] = max(scores) if scores else 1

        for comp in self._comparisons:
            is_you = comp.name == "Your System"
            marker = "▸" if self._comparisons.index(comp) == self._selected_index else " "
            highlight = " ★" if is_you else ""
            lines.append(f"{marker} {comp.name}{highlight}")

            # Score bars
            for cat, label in [("cpu", "CPU"), ("mem", "RAM"), ("disk", "SSD"), ("gpu", "GPU")]:
                score = getattr(comp, f"{cat}_score", 0)
                max_s = max_scores.get(cat, 1)
                bar_len = int(score / max_s * 20) if max_s > 0 else 0
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(f"   {label:>4s}: [{bar}] {score:,.0f}")

            lines.append(f"   Overall: {comp.overall_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_export(self, width: int = 70) -> List[str]:
        report = self.export_report()
        lines = []
        lines.append(" 📄 System Report")
        lines.append("─" * width)
        for line in report.split("\n"):
            lines.append(f" {line}")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "benchmarks": self.render_benchmarks,
            "hardware": self.render_hardware,
            "comparison": self.render_comparison,
            "export": self.render_export,
        }
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "benchmarks":
            return self._handle_benchmarks_key(key)
        elif self._view_mode in ("hardware", "comparison", "export"):
            if key == "Escape":
                self.set_view("overview")
                return "back"
            elif key == "ArrowUp":
                self.select_up()
                return "select_up"
            elif key == "ArrowDown":
                self.select_down()
                return "select_down"
            return None
        return self._handle_overview_key(key)

    def _handle_overview_key(self, key: str) -> Optional[str]:
        if key == "b":
            self.set_view("benchmarks")
            return "benchmarks"
        elif key == "h":
            self.set_view("hardware")
            return "hardware"
        elif key == "c":
            self.set_view("comparison")
            return "comparison"
        elif key == "e":
            self.set_view("export")
            return "export"
        return None

    def _handle_benchmarks_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "run_benchmark" if self.run_benchmark(self._selected_index) else "run_failed"
        elif key == "a":
            count = self.run_all_benchmarks()
            return "run_all" if count > 0 else "all_done"
        return None
