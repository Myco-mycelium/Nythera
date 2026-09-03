from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class BenchCategory(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    GPU = "gpu"
    NETWORK = "network"
    COMPOSITE = "composite"


class BenchStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SystemTier(Enum):
    HIGH_END = "high-end"
    MID_RANGE = "mid-range"
    BUDGET = "budget"
    WORKSTATION = "workstation"
    SERVER = "server"


@dataclass
class BenchResult:
    name: str
    category: BenchCategory
    score: float
    unit: str
    timestamp: float
    duration_ms: int = 0
    system_name: str = "nyrqis-workstation"
    tier: SystemTier = SystemTier.HIGH_END
    percentiles: dict = field(default_factory=dict)

    @property
    def score_display(self) -> str:
        if self.score >= 1_000_000:
            return f"{self.score / 1_000_000:.2f}M {self.unit}"
        if self.score >= 1_000:
            return f"{self.score / 1_000:.1f}K {self.unit}"
        return f"{self.score:.1f} {self.unit}"

    @property
    def percentile(self) -> str:
        p50 = self.percentiles.get("p50", 0)
        p90 = self.percentiles.get("p90", 0)
        p99 = self.percentiles.get("p99", 0)
        if self.score >= p99:
            return "Top 1%"
        if self.score >= p90:
            return "Top 10%"
        if self.score >= p50:
            return "Above Avg"
        return "Below Avg"

    @property
    def score_bar(self) -> str:
        p90 = self.percentiles.get("p90", self.score)
        ratio = min(self.score / max(p90, 1), 1.5)
        filled = int(ratio * 20)
        return "█" * min(filled, 20) + "░" * max(20 - filled, 0)


@dataclass
class BenchJob:
    name: str
    category: BenchCategory
    status: BenchStatus = BenchStatus.PENDING
    progress: float = 0
    start_time: float = 0
    end_time: float = 0
    iterations: int = 1
    current_iteration: int = 0

    @property
    def elapsed_display(self) -> str:
        if self.start_time == 0:
            return "N/A"
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        return f"{elapsed / 60:.1f}m"


@dataclass
class ComparisonEntry:
    system_name: str
    tier: SystemTier
    cpu_score: float
    mem_score: float
    disk_score: float
    gpu_score: float

    @property
    def overall_score(self) -> float:
        return (self.cpu_score + self.mem_score + self.disk_score + self.gpu_score) / 4

    @property
    def tier_icon(self) -> str:
        icons = {"high-end": "🏆", "mid-range": "⚖️", "budget": "💰", "workstation": "🖥️", "server": "📦"}
        return icons.get(self.tier.value, "?")


class BenchmarkSuite:
    def __init__(self):
        self._results: list[BenchResult] = []
        self._selected_result: int = 0
        self._jobs: list[BenchJob] = []
        self._comparisons: list[ComparisonEntry] = []
        self._selected_comparison: int = 0
        self._category_filter: Optional[BenchCategory] = None
        self._view: str = "results"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._results = [
            BenchResult("CPU Single-Core", BenchCategory.CPU, 2850, "pts", now - 86400, 15000, percentiles={"p50": 2100, "p90": 2600, "p99": 2800}),
            BenchResult("CPU Multi-Core", BenchCategory.CPU, 38500, "pts", now - 86400, 120000, percentiles={"p50": 28000, "p90": 35000, "p99": 38000}),
            BenchResult("Memory Read", BenchCategory.MEMORY, 82500, "MB/s", now - 86400, 5000, percentiles={"p50": 65000, "p90": 78000, "p99": 82000}),
            BenchResult("Memory Write", BenchCategory.MEMORY, 72300, "MB/s", now - 86400, 5000, percentiles={"p50": 58000, "p90": 70000, "p99": 72000}),
            BenchResult("Memory Latency", BenchCategory.MEMORY, 62.5, "ns", now - 86400, 3000, percentiles={"p50": 75, "p90": 65, "p99": 60}),
            BenchResult("Disk Sequential Read", BenchCategory.DISK, 7100, "MB/s", now - 86400, 10000, percentiles={"p50": 5000, "p90": 6500, "p99": 7000}),
            BenchResult("Disk Sequential Write", BenchCategory.DISK, 6800, "MB/s", now - 86400, 10000, percentiles={"p50": 4500, "p90": 6000, "p99": 6800}),
            BenchResult("Disk Random 4K", BenchCategory.DISK, 1250000, "IOPS", now - 86400, 8000, percentiles={"p50": 800000, "p90": 1100000, "p99": 1250000}),
            BenchResult("GPU Compute", BenchCategory.GPU, 18500, "pts", now - 86400, 30000, percentiles={"p50": 14000, "p90": 17000, "p99": 18500}),
            BenchResult("GPU OpenGL", BenchCategory.GPU, 22400, "fps", now - 86400, 20000, percentiles={"p50": 16000, "p90": 20000, "p99": 22000}),
            BenchResult("Network TCP", BenchCategory.NETWORK, 9400, "Mbps", now - 86400, 5000, percentiles={"p50": 8000, "p90": 9200, "p99": 9400}),
        ]

        self._jobs = [
            BenchJob("Full System Benchmark", BenchCategory.COMPOSITE, BenchStatus.COMPLETE, 100, now - 86400, now - 86400 + 180000, 1, 1),
            BenchJob("CPU Quick Test", BenchCategory.CPU, BenchStatus.COMPLETE, 100, now - 3600, now - 3600 + 15000, 1, 1),
        ]

        self._comparisons = [
            ComparisonEntry("Nyrqis Workstation", SystemTier.HIGH_END, 2850, 82500, 7100, 18500),
            ComparisonEntry("Mid-Range Desktop", SystemTier.MID_RANGE, 1800, 55000, 3500, 10000),
            ComparisonEntry("Budget Laptop", SystemTier.BUDGET, 900, 35000, 1500, 4000),
            ComparisonEntry("Workstation Pro", SystemTier.WORKSTATION, 3200, 95000, 8500, 22000),
            ComparisonEntry("Cloud Server", SystemTier.SERVER, 2200, 70000, 5000, 0),
        ]

    @property
    def selected_result(self) -> Optional[BenchResult]:
        if 0 <= self._selected_result < len(self._results):
            return self._results[self._selected_result]
        return None

    @property
    def selected_comparison(self) -> Optional[ComparisonEntry]:
        if 0 <= self._selected_comparison < len(self._comparisons):
            return self._comparisons[self._selected_comparison]
        return None

    @property
    def total_benchmarks(self) -> int:
        return len(self._results)

    @property
    def overall_score(self) -> float:
        if not self._results:
            return 0
        return sum(r.score for r in self._results) / len(self._results)

    def select_result(self, idx: int):
        if 0 <= idx < len(self._results):
            self._selected_result = idx

    def select_comparison(self, idx: int):
        if 0 <= idx < len(self._comparisons):
            self._selected_comparison = idx

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS BENCHMARK SUITE                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Benchmarks: {self.total_benchmarks}  Overall: {self.overall_score:,.0f}  Jobs: {len(self._jobs)}  Comparisons: {len(self._comparisons)}")
        lines.append("")
        lines.append("  ── Results ──")
        for i, r in enumerate(self._results):
            sel = "▶" if i == self._selected_result else " "
            cat_icons = {"cpu": "🔲", "memory": "🧠", "disk": "💾", "gpu": "🎮", "network": "🌐"}
            icon = cat_icons.get(r.category.value, "?")
            lines.append(f"  {sel}{icon} {r.name:<20s} {r.score_bar} {r.score_display:>15s}  {r.percentile}")
        lines.append("")
        lines.append("  ── Comparison ──")
        for i, c in enumerate(self._comparisons):
            sel = "▶" if i == self._selected_comparison else " "
            lines.append(f"  {sel} {c.tier_icon} {c.system_name:<25s}  CPU:{c.cpu_score:>5.0f}  MEM:{c.mem_score:>6.0f}  DISK:{c.disk_score:>5.0f}  GPU:{c.gpu_score:>5.0f}  AVG:{c.overall_score:>7.0f}")
        lines.append("")
        lines.append("  ── Jobs ──")
        for j in self._jobs:
            status = {"complete": "✅", "running": "🔄", "pending": "⏳", "failed": "❌"}.get(j.status.value, "?")
            lines.append(f"  {status} {j.name}  {j.elapsed_display}  {j.category.value}")
        lines.append("")
        lines.append("  [R]un benchmark  [C]ompare  [E]xport  [H]istory  [F]ilter")
        return lines

    def render_result_detail(self) -> list:
        r = self.selected_result
        if not r:
            return ["  No result selected"]
        lines = []
        lines.append(f"  ── {r.name} ({r.category.value}) ──")
        lines.append(f"  Score: {r.score_display}")
        lines.append(f"  Percentile: {r.percentile}")
        lines.append(f"  System: {r.system_name}")
        lines.append(f"  Tier: {r.tier.value}")
        lines.append(f"  Duration: {r.duration_ms / 1000:.1f}s")
        if r.percentiles:
            lines.append(f"  P50: {r.percentiles.get('p50', 0):,.0f}")
            lines.append(f"  P90: {r.percentiles.get('p90', 0):,.0f}")
            lines.append(f"  P99: {r.percentiles.get('p99', 0):,.0f}")
        return lines

    def render_comparison(self) -> list:
        lines = []
        lines.append("  ── System Comparison ──")
        lines.append("")
        lines.append(f"  {'System':<25s} {'Tier':<15s} {'CPU':>8s} {'Memory':>10s} {'Disk':>8s} {'GPU':>8s} {'Overall':>10s}")
        lines.append(f"  {'─'*25} {'─'*15} {'─'*8} {'─'*10} {'─'*8} {'─'*8} {'─'*10}")
        for c in sorted(self._comparisons, key=lambda x: -x.overall_score):
            lines.append(f"  {c.system_name:<25s} {c.tier.value:<15s} {c.cpu_score:>8.0f} {c.mem_score:>10.0f} {c.disk_score:>8.0f} {c.gpu_score:>8.0f} {c.overall_score:>10.0f}")
        return lines
