"""
Nyrqis OS - Benchmark Runner
CPU/GPU/disk tests, comparison charts, and export.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class BenchmarkCategory(Enum):
    CPU = "cpu"
    GPU = "gpu"
    DISK = "disk"
    MEMORY = "memory"
    NETWORK = "network"
    COMPOSITE = "composite"


class BenchmarkStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TestType(Enum):
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    SORT = "sort"
    CRYPTO = "crypto"
    COMPRESSION = "compression"
    PARSE = "parse"
    REGEX = "regex"


@dataclass
class BenchmarkTest:
    name: str
    category: BenchmarkCategory = BenchmarkCategory.CPU
    test_type: TestType = TestType.INTEGER
    score: float = 0.0
    unit: str = "pts"
    duration_s: float = 0.0
    status: BenchmarkStatus = BenchmarkStatus.QUEUED
    iteration: int = 1
    details: Dict[str, float] = field(default_factory=dict)
    error: str = ""

    @property
    def score_display(self) -> str:
        if self.score >= 1000000:
            return f"{self.score / 1000000:.2f}M {self.unit}"
        elif self.score >= 1000:
            return f"{self.score / 1000:.1f}K {self.unit}"
        return f"{self.score:.1f} {self.unit}"

    @property
    def status_icon(self) -> str:
        icons = {
            BenchmarkStatus.QUEUED: "⏳",
            BenchmarkStatus.RUNNING: "🔄",
            BenchmarkStatus.COMPLETED: "✅",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.CANCELLED: "⬜",
        }
        return icons.get(self.status, "?")


@dataclass
class BenchmarkSuite:
    name: str
    category: BenchmarkCategory = BenchmarkCategory.CPU
    tests: List[BenchmarkTest] = field(default_factory=list)
    total_score: float = 0.0
    status: BenchmarkStatus = BenchmarkStatus.QUEUED
    started_at: float = 0.0
    completed_at: float = 0.0
    system_info: Dict[str, str] = field(default_factory=dict)

    @property
    def total_score_display(self) -> str:
        if self.total_score >= 1000000:
            return f"{self.total_score / 1000000:.2f}M pts"
        elif self.total_score >= 1000:
            return f"{self.total_score / 1000:.1f}K pts"
        return f"{self.total_score:.0f} pts"

    @property
    def duration_s(self) -> float:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return sum(t.duration_s for t in self.tests)

    @property
    def grade(self) -> float:
        if self.total_score >= 500000:
            return 100.0
        return (self.total_score / 500000) * 100


@dataclass
class BenchmarkResult:
    suite_name: str
    timestamp: float = 0.0
    total_score: float = 0.0
    tests_passed: int = 0
    tests_failed: int = 0
    duration_s: float = 0.0
    system_info: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def score_bar(self) -> str:
        pct = min(100, (self.total_score / 500000) * 100)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class ComparisonEntry:
    label: str
    score: float
    color: str = "#4fc3f7"

    @property
    def bar(self) -> str:
        max_score = 500000
        pct = min(100, (self.score / max_score) * 100)
        filled = int(pct / 2)
        return "█" * filled + "░" * (50 - filled)


@dataclass
class ExportConfig:
    format: str = "json"  # json, csv, html, pdf
    include_details: bool = True
    include_system_info: bool = True
    include_comparison: bool = True


class BenchmarkRunner:
    def __init__(self):
        self.suites: List[BenchmarkSuite] = []
        self.results: List[BenchmarkResult] = []
        self.comparisons: List[ComparisonEntry] = []
        self.current_suite: Optional[BenchmarkSuite] = None
        self.is_running: bool = False
        self.system_info: Dict[str, str] = {}
        self._create_sample_data()

    def _create_sample_data(self):
        self.system_info = {
            "OS": "Nyrqis OS 0.1.0",
            "Kernel": "nyrqis-kernel 1.0.0-rc1",
            "CPU": "AMD Ryzen 9 7950X 16C/32T @ 5.7GHz",
            "RAM": "64 GB DDR5-6000",
            "GPU": "NVIDIA RTX 4090 24GB",
            "Storage": "Samsung 990 Pro 2TB NVMe",
        }

        cpu_tests = [
            BenchmarkTest(name="Integer Sort", category=BenchmarkCategory.CPU,
                           test_type=TestType.INTEGER, score=28500, duration_s=8.2,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Float Math", category=BenchmarkCategory.CPU,
                           test_type=TestType.FLOAT, score=31200, duration_s=7.5,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="String Processing", category=BenchmarkCategory.CPU,
                           test_type=TestType.STRING, score=22800, duration_s=9.1,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Data Sorting", category=BenchmarkCategory.CPU,
                           test_type=TestType.SORT, score=25600, duration_s=6.8,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Encryption", category=BenchmarkCategory.CPU,
                           test_type=TestType.CRYPTO, score=18900, duration_s=11.2,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Compression", category=BenchmarkCategory.CPU,
                           test_type=TestType.COMPRESSION, score=15400, duration_s=8.5,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="JSON Parse", category=BenchmarkCategory.CPU,
                           test_type=TestType.PARSE, score=33100, duration_s=5.2,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Regex Match", category=BenchmarkCategory.CPU,
                           test_type=TestType.REGEX, score=20700, duration_s=7.8,
                           status=BenchmarkStatus.COMPLETED),
        ]

        gpu_tests = [
            BenchmarkTest(name="Compute Shader", category=BenchmarkCategory.GPU,
                           score=485000, duration_s=15.0,
                           status=BenchmarkStatus.COMPLETED,
                           details={"FP32": 350000, "FP16": 680000, "INT8": 890000}),
            BenchmarkTest(name="Rasterization", category=BenchmarkCategory.GPU,
                           score=285, unit="fps", duration_s=10.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Ray Tracing", category=BenchmarkCategory.GPU,
                           score=165, unit="fps", duration_s=12.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Vulkan Rendering", category=BenchmarkCategory.GPU,
                           score=12500, unit="draw calls/s", duration_s=8.0,
                           status=BenchmarkStatus.COMPLETED),
        ]

        disk_tests = [
            BenchmarkTest(name="Sequential Read", category=BenchmarkCategory.DISK,
                           score=7450, unit="MB/s", duration_s=10.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Sequential Write", category=BenchmarkCategory.DISK,
                           score=6900, unit="MB/s", duration_s=10.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Random 4K Read", category=BenchmarkCategory.DISK,
                           score=1200000, unit="IOPS", duration_s=8.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Random 4K Write", category=BenchmarkCategory.DISK,
                           score=950000, unit="IOPS", duration_s=8.0,
                           status=BenchmarkStatus.COMPLETED),
        ]

        mem_tests = [
            BenchmarkTest(name="Read Bandwidth", category=BenchmarkCategory.MEMORY,
                           score=89.5, unit="GB/s", duration_s=5.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Write Bandwidth", category=BenchmarkCategory.MEMORY,
                           score=82.1, unit="GB/s", duration_s=5.0,
                           status=BenchmarkStatus.COMPLETED),
            BenchmarkTest(name="Latency", category=BenchmarkCategory.MEMORY,
                           score=62.3, unit="ns", duration_s=3.0,
                           status=BenchmarkStatus.COMPLETED),
        ]

        self.suites = [
            BenchmarkSuite(name="CPU Benchmark", category=BenchmarkCategory.CPU,
                            tests=cpu_tests, total_score=sum(t.score for t in cpu_tests),
                            status=BenchmarkStatus.COMPLETED, system_info=self.system_info),
            BenchmarkSuite(name="GPU Benchmark", category=BenchmarkCategory.GPU,
                            tests=gpu_tests, total_score=sum(t.score for t in gpu_tests),
                            status=BenchmarkStatus.COMPLETED, system_info=self.system_info),
            BenchmarkSuite(name="Disk Benchmark", category=BenchmarkCategory.DISK,
                            tests=disk_tests, total_score=sum(t.score for t in disk_tests),
                            status=BenchmarkStatus.COMPLETED, system_info=self.system_info),
            BenchmarkSuite(name="Memory Benchmark", category=BenchmarkCategory.MEMORY,
                            tests=mem_tests, total_score=sum(t.score for t in mem_tests),
                            status=BenchmarkStatus.COMPLETED, system_info=self.system_info),
        ]

        self.results = [
            BenchmarkResult(suite_name="CPU Benchmark", total_score=196200,
                             tests_passed=8, tests_failed=0,
                             duration_s=64.3, system_info=self.system_info),
            BenchmarkResult(suite_name="GPU Benchmark", total_score=485750,
                             tests_passed=4, tests_failed=0,
                             duration_s=45.0, system_info=self.system_info),
        ]

        self.comparisons = [
            ComparisonEntry(label="Nyrqis (RTX 4090)", score=485750, color="#6bcb77"),
            ComparisonEntry(label="Avg Desktop (RTX 3070)", score=180000, color="#4fc3f7"),
            ComparisonEntry(label="Avg Laptop (RTX 4060)", score=120000, color="#ffb74d"),
            ComparisonEntry(label="Integrated (UHD 770)", score=25000, color="#e57373"),
            ComparisonEntry(label="Budget (GTX 1650)", score=45000, color="#ce93d8"),
        ]

    def run_suite(self, name: str) -> Optional[BenchmarkSuite]:
        suite = next((s for s in self.suites if s.name == name), None)
        if not suite:
            return None
        self.current_suite = suite
        self.is_running = True
        suite.status = BenchmarkStatus.RUNNING
        suite.started_at = time.time()
        for test in suite.tests:
            test.status = BenchmarkStatus.RUNNING
            test.status = BenchmarkStatus.COMPLETED
        suite.status = BenchmarkStatus.COMPLETED
        suite.completed_at = time.time()
        self.is_running = False
        result = BenchmarkResult(
            suite_name=suite.name,
            total_score=suite.total_score,
            tests_passed=sum(1 for t in suite.tests if t.status == BenchmarkStatus.COMPLETED),
            tests_failed=sum(1 for t in suite.tests if t.status == BenchmarkStatus.FAILED),
            duration_s=suite.duration_s,
            system_info=suite.system_info,
        )
        self.results.append(result)
        return suite

    def run_all(self) -> List[BenchmarkSuite]:
        results = []
        for suite in self.suites:
            result = self.run_suite(suite.name)
            if result:
                results.append(result)
        return results

    def get_suite(self, name: str) -> Optional[BenchmarkSuite]:
        return next((s for s in self.suites if s.name == name), None)

    def get_results(self, limit: int = 10) -> List[BenchmarkResult]:
        return sorted(self.results, key=lambda r: r.timestamp, reverse=True)[:limit]

    def get_comparison(self) -> List[ComparisonEntry]:
        return sorted(self.comparisons, key=lambda c: c.score, reverse=True)

    def get_overall_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.total_score for r in self.results) / len(self.results)

    def export_results(self, config: ExportConfig) -> str:
        if config.format == "json":
            import json
            data = {
                "system_info": self.system_info,
                "results": [{"suite": r.suite_name, "score": r.total_score,
                              "passed": r.tests_passed, "failed": r.tests_failed}
                             for r in self.results],
                "overall_score": self.get_overall_score(),
            }
            return json.dumps(data, indent=2)
        elif config.format == "csv":
            lines = ["Suite,Score,Passed,Failed,Duration"]
            for r in self.results:
                lines.append(f"{r.suite_name},{r.total_score},{r.tests_passed},{r.tests_failed},{r.duration_s:.1f}")
            return "\n".join(lines)
        return f"[Export as {config.format}]"

    def get_stats(self) -> Dict:
        return {
            "suites": len(self.suites),
            "total_tests": sum(len(s.tests) for s in self.suites),
            "results": len(self.results),
            "overall_score": round(self.get_overall_score(), 0),
            "comparisons": len(self.comparisons),
        }
