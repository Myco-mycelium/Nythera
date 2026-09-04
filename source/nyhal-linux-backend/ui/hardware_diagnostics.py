"""
Nyrqis OS - Hardware Diagnostics
Stress tests, component detection, and failure prediction.

Features:
- Component detection (CPU, RAM, GPU, Storage, Network, USB, PCI)
- Stress tests (CPU, RAM, disk I/O, GPU compute, network)
- Health monitoring with temperature, voltage, error rates
- Failure prediction based on SMART, memory errors, and thermal data
- Benchmark validation against known baselines
- Test history with pass/fail tracking
- Component comparison between current and expected specs
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class ComponentType(Enum):
    CPU = "CPU"
    GPU = "GPU"
    RAM = "RAM"
    STORAGE = "Storage"
    NETWORK = "Network"
    USB = "USB"
    MOTHERBOARD = "Motherboard"
    PSU = "Power Supply"
    AUDIO = "Audio"
    DISPLAY = "Display"


class StressStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class HealthStatus(Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    WARNING = "warning"
    CRITICAL = "critical"
    FAILED = "failed"


class StressType(Enum):
    CPU_MULTI = "CPU Multi-Core"
    CPU_SINGLE = "CPU Single-Core"
    CPU_TORTURE = "CPU Torture"
    RAM_READ = "RAM Read"
    RAM_WRITE = "RAM Write"
    RAM_COPY = "RAM Copy"
    RAM_STRESS = "RAM Stress"
    DISK_SEQ_READ = "Disk Sequential Read"
    DISK_SEQ_WRITE = "Disk Sequential Write"
    DISK_RAND_4K = "Disk Random 4K"
    GPU_COMPUTE = "GPU Compute"
    GPU_RENDER = "GPU Render"
    GPU_MEMORY = "GPU VRAM"
    NET_BANDWIDTH = "Network Bandwidth"
    NET_LATENCY = "Network Latency"


COMPONENT_ICONS = {
    ComponentType.CPU: "🖥️", ComponentType.GPU: "🎮",
    ComponentType.RAM: "💾", ComponentType.STORAGE: "💿",
    ComponentType.NETWORK: "🌐", ComponentType.USB: "🔌",
    ComponentType.MOTHERBOARD: "🔧", ComponentType.PSU: "⚡",
    ComponentType.AUDIO: "🔊", ComponentType.DISPLAY: "🖥️",
}

HEALTH_ICONS = {
    HealthStatus.EXCELLENT: "🟢", HealthStatus.GOOD: "🟢",
    HealthStatus.FAIR: "🟡", HealthStatus.WARNING: "🟠",
    HealthStatus.CRITICAL: "🔴", HealthStatus.FAILED: "❌",
}

STRESS_ICONS = {
    StressStatus.IDLE: "⏸", StressStatus.RUNNING: "🔄",
    StressStatus.COMPLETED: "✅", StressStatus.FAILED: "❌",
    StressStatus.STOPPED: "⏹",
}


@dataclass
class ComponentInfo:
    component_type: ComponentType = ComponentType.CPU
    name: str = ""
    model: str = ""
    vendor: str = ""
    serial: str = ""
    firmware: str = ""
    detected: bool = True
    health: HealthStatus = HealthStatus.GOOD
    temperature_c: float = 0.0
    voltage_v: float = 0.0
    power_w: float = 0.0
    usage_percent: float = 0.0
    speed_mhz: int = 0
    driver_version: str = ""
    pci_id: str = ""
    bus: int = 0
    device: int = 0
    details: Dict[str, str] = field(default_factory=dict)
    errors: int = 0
    warnings: int = 0
    last_test: float = 0.0

    @property
    def type_icon(self) -> str:
        return COMPONENT_ICONS.get(self.component_type, "❓")

    @property
    def health_icon(self) -> str:
        return HEALTH_ICONS.get(self.health, "❓")

    @property
    def health_label(self) -> str:
        return self.health.value.upper()

    @property
    def temp_status(self) -> str:
        if self.temperature_c == 0:
            return "N/A"
        if self.temperature_c < 50:
            return "🟢 Cool"
        elif self.temperature_c < 70:
            return "🟡 Warm"
        elif self.temperature_c < 85:
            return "🟠 Hot"
        return "🔴 Critical"

    @property
    def temp_bar(self) -> str:
        if self.temperature_c == 0:
            return "░░░░░░░░░░░░░░░░░░░░"
        pct = min(100, self.temperature_c)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def error_summary(self) -> str:
        if self.errors == 0 and self.warnings == 0:
            return "✅ Clean"
        parts = []
        if self.errors > 0:
            parts.append(f"❌ {self.errors} errors")
        if self.warnings > 0:
            parts.append(f"⚠️ {self.warnings} warnings")
        return " ".join(parts)

    @property
    def last_test_str(self) -> str:
        if self.last_test == 0:
            return "Never"
        delta = time.time() - self.last_test
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.1f}h ago"


@dataclass
class StressTest:
    stress_type: StressType = StressType.CPU_MULTI
    status: StressStatus = StressStatus.IDLE
    duration_s: float = 60.0
    elapsed_s: float = 0.0
    iterations: int = 0
    errors: int = 0
    score: float = 0.0
    max_score: float = 100.0
    temperature_start_c: float = 0.0
    temperature_max_c: float = 0.0
    started: float = 0.0
    completed: float = 0.0
    details: Dict[str, float] = field(default_factory=dict)

    @property
    def status_icon(self) -> str:
        return STRESS_ICONS.get(self.status, "❓")

    @property
    def progress_percent(self) -> float:
        if self.duration_s == 0:
            return 0.0
        return min(100, (self.elapsed_s / self.duration_s) * 100)

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress_percent / 5)
        return "█" * filled + "░" * (20 - filled)

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
        p = self.score_percent
        if p >= 95:
            return "A+"
        elif p >= 90:
            return "A"
        elif p >= 80:
            return "B"
        elif p >= 70:
            return "C"
        elif p >= 60:
            return "D"
        return "F"

    @property
    def elapsed_str(self) -> str:
        m = int(self.elapsed_s // 60)
        s = int(self.elapsed_s % 60)
        return f"{m}m {s}s"

    @property
    def duration_str(self) -> str:
        m = int(self.duration_s // 60)
        s = int(self.duration_s % 60)
        return f"{m}m {s}s"

    @property
    def temp_delta(self) -> float:
        return self.temperature_max_c - self.temperature_start_c

    @property
    def temp_delta_str(self) -> str:
        d = self.temp_delta
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.1f}°C"


@dataclass
class FailurePrediction:
    component: str = ""
    component_type: ComponentType = ComponentType.STORAGE
    risk_level: HealthStatus = HealthStatus.GOOD
    prediction: str = ""
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    recommended_action: str = ""
    estimated_days: int = 0

    @property
    def risk_icon(self) -> str:
        return HEALTH_ICONS.get(self.risk_level, "❓")

    @property
    def confidence_bar(self) -> str:
        filled = int(self.confidence / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def eta_str(self) -> str:
        if self.estimated_days <= 0:
            return "Unknown"
        if self.estimated_days < 30:
            return f"{self.estimated_days} days"
        elif self.estimated_days < 365:
            return f"{self.estimated_days // 30} months"
        return f"{self.estimated_days // 365} years"

    @property
    def evidence_str(self) -> str:
        return "; ".join(self.evidence[:3])


@dataclass
class TestHistoryEntry:
    timestamp: float = 0.0
    stress_type: StressType = StressType.CPU_MULTI
    passed: bool = True
    score: float = 0.0
    duration_s: float = 0.0
    errors: int = 0
    notes: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def result_icon(self) -> str:
        return "✅" if self.passed else "❌"

    @property
    def score_str(self) -> str:
        return f"{self.score:.1f}"


class HardwareDiagnostics:
    def __init__(self):
        self.components: List[ComponentInfo] = []
        self.stress_tests: List[StressTest] = []
        self.predictions: List[FailurePrediction] = []
        self.history: List[TestHistoryEntry] = []
        self._selected_component: int = 0
        self._selected_test: int = 0
        self._view_mode: str = "components"
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.components = [
            ComponentInfo(
                ComponentType.CPU, "AMD Ryzen 9 7950X", "Ryzen 9 7950X",
                "AMD", "CPU-FN12345", "", True, HealthStatus.EXCELLENT,
                62.0, 1.35, 125.0, 34.5, 5700, "6.10.5",
                details={"Cores": "16", "Threads": "32", "Base": "4.5 GHz",
                         "Boost": "5.7 GHz", "L2": "16 MB", "L3": "64 MB",
                         "TDP": "170 W", "Socket": "AM5", "Architecture": "Zen 4"},
                last_test=now - 3600,
            ),
            ComponentInfo(
                ComponentType.GPU, "NVIDIA GeForce RTX 4090", "RTX 4090",
                "NVIDIA", "GPU-ABC123", "535.129.03", True, HealthStatus.EXCELLENT,
                55.0, 12.0, 285.0, 45.0, 2520, "535.129.03",
                details={"VRAM": "24 GB GDDR6X", "CUDA Cores": "16384",
                         "Base Clock": "2235 MHz", "Boost Clock": "2520 MHz",
                         "Memory BW": "1008 GB/s", "TDP": "450 W",
                         "PCIe": "4.0 x16", "Vulkan": "1.3.250"},
                last_test=now - 7200,
            ),
            ComponentInfo(
                ComponentType.RAM, "G.Skill Trident Z5 RGB 64GB", "DDR5-6000",
                "G.Skill", "RAM-XYZ789", "", True, HealthStatus.GOOD,
                38.0, 1.35, 10.0, 42.0, 6000, "",
                details={"Capacity": "64 GB (2×32)", "Type": "DDR5-6000",
                         "Timings": "30-40-40-96", "Voltage": "1.35V",
                         "XMP": "Profile 1", "Slots Used": "2/4"},
                last_test=now - 86400,
            ),
            ComponentInfo(
                ComponentType.STORAGE, "Samsung 990 Pro 2TB", "NVMe SSD",
                "Samsung", "S5KXNS0T123456", "GXA7B03Q", True,
                HealthStatus.EXCELLENT, 42.0, 3.3, 6.0, 15.0, 0, "",
                details={"Capacity": "2 TB", "Interface": "PCIe 4.0 x4",
                         "Seq Read": "7450 MB/s", "Seq Write": "6900 MB/s",
                         "Random Read": "1.2M IOPS", "TBW": "1200 TB",
                         "Health": "98%", "Power-On Hours": "3200",
                         "Temp": "42°C", "FW": "GXA7B03Q"},
                last_test=now - 172800,
            ),
            ComponentInfo(
                ComponentType.STORAGE, "Samsung 870 EVO 1TB", "SATA SSD",
                "Samsung", "S4EVNX0R123456", "RVT01B6Q", True,
                HealthStatus.GOOD, 35.0, 5.0, 3.0, 8.0, 0, "",
                details={"Capacity": "1 TB", "Interface": "SATA III",
                         "Seq Read": "560 MB/s", "Seq Write": "530 MB/s",
                         "Health": "95%", "Power-On Hours": "12000"},
                last_test=now - 172800,
            ),
            ComponentInfo(
                ComponentType.NETWORK, "Intel I225-V 2.5GbE", "Ethernet",
                "Intel", "", "igc 6.10.5", True, HealthStatus.GOOD,
                45.0, 0, 2.0, 12.0, 2500, "igc 6.10.5",
                details={"Speed": "2.5 Gbps", "MAC": "AA:BB:CC:DD:EE:01",
                         "Driver": "igc", "Firmware": "Intel I225-V",
                         "Link": "Up", "MTU": "1500"},
                errors=12, last_test=now - 86400,
            ),
            ComponentInfo(
                ComponentType.NETWORK, "Qualcomm WCN6855 WiFi 6E", "WiFi",
                "Qualcomm", "", "ath11k_pci", True, HealthStatus.GOOD,
                40.0, 0, 1.5, 5.0, 3000, "ath11k_pci",
                details={"Standard": "WiFi 6E", "Band": "2.4/5/6 GHz",
                         "Max Speed": "3000 Mbps", "MAC": "AA:BB:CC:DD:EE:02",
                         "Driver": "ath11k_pci", "Signal": "Excellent (-35 dBm)"},
                last_test=now - 86400,
            ),
            ComponentInfo(
                ComponentType.USB, "ASMedia ASM4242 USB4", "USB Controller",
                "ASMedia", "", "", True, HealthStatus.EXCELLENT,
                38.0, 5.0, 4.5, 0.0, 0, "xhci_pci 6.10.5",
                details={"Ports": "4× USB4", "Speed": "40 Gbps",
                         "Controller": "ASM4242", "Driver": "xhci_pci"},
                last_test=now - 86400 * 7,
            ),
        ]

        self.stress_tests = [
            StressTest(StressType.CPU_MULTI, StressStatus.COMPLETED,
                       60.0, 60.0, 125000000, 0, 92.5, 100.0,
                       45.0, 82.0, now - 3600, now - 3540,
                       {"Instructions": 28500000000, "Clock Avg": 5420,
                        "Thermal Throttle": 0}),
            StressTest(StressType.CPU_SINGLE, StressStatus.COMPLETED,
                       30.0, 30.0, 45000000, 0, 88.0, 100.0,
                       48.0, 78.0, now - 3600, now - 3570,
                       {"Instructions": 12000000000, "Clock Avg": 5680}),
            StressTest(StressType.RAM_READ, StressStatus.COMPLETED,
                       30.0, 30.0, 85000, 0, 85.2, 100.0,
                       35.0, 42.0, now - 7200, now - 7170,
                       {"Bandwidth": "85.2 GB/s", "Latency": "62.3 ns"}),
            StressTest(StressType.DISK_SEQ_READ, StressStatus.COMPLETED,
                       60.0, 60.0, 500, 0, 93.1, 100.0,
                       38.0, 48.0, now - 86400, now - 8580,
                       {"Read": "7450 MB/s", "Write": "6900 MB/s",
                        "IOPS": "1200000"}),
            StressTest(StressType.GPU_COMPUTE, StressStatus.COMPLETED,
                       60.0, 60.0, 245000, 0, 91.0, 100.0,
                       42.0, 72.0, now - 86400, now - 8540,
                       {"FP32": "485 TFLOPS", "FP16": "970 TFLOPS"}),
            StressTest(StressType.NET_BANDWIDTH, StressStatus.COMPLETED,
                       30.0, 30.0, 1000, 0, 78.5, 100.0,
                       0, 0, now - 86400, now - 8570,
                       {"Download": "2350 Mbps", "Upload": "1180 Mbps"}),
            StressTest(StressType.CPU_TORTURE, StressStatus.IDLE,
                       300.0, 0.0, 0, 0, 0, 100.0),
            StressTest(StressType.RAM_STRESS, StressStatus.IDLE,
                       300.0, 0.0, 0, 0, 0, 100.0),
            StressTest(StressType.DISK_RAND_4K, StressStatus.IDLE,
                       60.0, 0.0, 0, 0, 0, 100.0),
            StressTest(StressType.GPU_MEMORY, StressStatus.IDLE,
                       60.0, 0.0, 0, 0, 0, 100.0),
        ]

        self.predictions = [
            FailurePrediction(
                "Samsung 990 Pro 2TB", ComponentType.STORAGE,
                HealthStatus.GOOD, "No failure predicted",
                85.0, ["Health 98%", "3200h power-on", "12.5 TBW"],
                "Continue monitoring", 1825),
            FailurePrediction(
                "Samsung 870 EVO 1TB", ComponentType.STORAGE,
                HealthStatus.FAIR, "Monitor closely — aging NAND",
                62.0, ["Health 95%", "12000h power-on", "45 TBW",
                       "95% TBW consumed on page 3"],
                "Consider replacing within 6 months", 180),
            FailurePrediction(
                "G.Skill DDR5 64GB", ComponentType.RAM,
                HealthStatus.GOOD, "Stable — no errors detected",
                90.0, ["0 ECC errors", "38°C operating temp",
                       "XMP stable at 6000 MT/s"],
                "No action needed", 3650),
            FailurePrediction(
                "Power Supply", ComponentType.PSU,
                HealthStatus.GOOD, "Operating within spec",
                92.0, ["12V rail stable", "410W system draw",
                       "450W PSU rating"],
                "No action needed", 2555),
            FailurePrediction(
                "Intel I225-V NIC", ComponentType.NETWORK,
                HealthStatus.WARNING, "Known errata — occasional drops",
                45.0, ["12 RX errors", "Known Intel I225 issue",
                       "Driver workaround applied"],
                "Update firmware or replace NIC", 90),
        ]

        self.history = [
            TestHistoryEntry(now - 3600, StressType.CPU_MULTI, True, 92.5, 60, 0),
            TestHistoryEntry(now - 3600, StressType.CPU_SINGLE, True, 88.0, 30, 0),
            TestHistoryEntry(now - 7200, StressType.RAM_READ, True, 85.2, 30, 0),
            TestHistoryEntry(now - 86400, StressType.DISK_SEQ_READ, True, 93.1, 60, 0),
            TestHistoryEntry(now - 86400, StressType.GPU_COMPUTE, True, 91.0, 60, 0),
            TestHistoryEntry(now - 86400, StressType.NET_BANDWIDTH, True, 78.5, 30, 0),
            TestHistoryEntry(now - 86400 * 7, StressType.CPU_TORTURE, True, 89.0, 300, 0),
            TestHistoryEntry(now - 86400 * 7, StressType.RAM_STRESS, True, 87.5, 300, 0),
            TestHistoryEntry(now - 86400 * 30, StressType.CPU_MULTI, True, 91.0, 60, 0),
            TestHistoryEntry(now - 86400 * 30, StressType.DISK_SEQ_READ, False, 65.0, 60, 3,
                            "3 read errors during test"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_component(self) -> Optional[ComponentInfo]:
        if 0 <= self._selected_component < len(self.components):
            return self.components[self._selected_component]
        return None

    def select_component(self, idx: int):
        if 0 <= idx < len(self.components):
            self._selected_component = idx

    def select_test(self, idx: int):
        if 0 <= idx < len(self.stress_tests):
            self._selected_test = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "components":
            self._selected_component = min(self._selected_component + 1, len(self.components) - 1)
        elif self._view_mode == "tests":
            self._selected_test = min(self._selected_test + 1, len(self.stress_tests) - 1)

    def select_up(self):
        if self._view_mode == "components":
            self._selected_component = max(self._selected_component - 1, 0)
        elif self._view_mode == "tests":
            self._selected_test = max(self._selected_test - 1, 0)

    # ─── Stress Tests ──────────────────────────────────────────────────

    def start_test(self, idx: int) -> bool:
        if 0 <= idx < len(self.stress_tests):
            test = self.stress_tests[idx]
            if test.status in (StressStatus.IDLE, StressStatus.COMPLETED, StressStatus.FAILED):
                test.status = StressStatus.RUNNING
                test.elapsed_s = 0
                test.errors = 0
                test.score = 0
                test.started = time.time()
                test.temperature_start_c = 50.0 + random.uniform(-5, 5)
                test.temperature_max_c = test.temperature_start_c
                return True
        return False

    def complete_test(self, idx: int, score: float, errors: int = 0) -> bool:
        if 0 <= idx < len(self.stress_tests):
            test = self.stress_tests[idx]
            test.status = StressStatus.COMPLETED if errors == 0 else StressStatus.FAILED
            test.elapsed_s = test.duration_s
            test.score = score
            test.errors = errors
            test.completed = time.time()
            self.history.insert(0, TestHistoryEntry(
                time.time(), test.stress_type, errors == 0,
                score, test.duration_s, errors
            ))
            return True
        return False

    def stop_test(self, idx: int) -> bool:
        if 0 <= idx < len(self.stress_tests):
            test = self.stress_tests[idx]
            if test.status == StressStatus.RUNNING:
                test.status = StressStatus.STOPPED
                return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_components_by_type(self, ctype: ComponentType) -> List[ComponentInfo]:
        return [c for c in self.components if c.component_type == ctype]

    def get_completed_tests(self) -> List[StressTest]:
        return [t for t in self.stress_tests if t.status == StressStatus.COMPLETED]

    def get_failed_components(self) -> List[ComponentInfo]:
        return [c for c in self.components if c.health in (HealthStatus.CRITICAL, HealthStatus.FAILED)]

    def get_warning_predictions(self) -> List[FailurePrediction]:
        return [p for p in self.predictions if p.risk_level in (HealthStatus.WARNING, HealthStatus.CRITICAL)]

    def search_components(self, query: str) -> List[ComponentInfo]:
        q = query.lower()
        return [c for c in self.components if q in c.name.lower() or q in c.vendor.lower()]

    def get_stats(self) -> Dict:
        return {
            "total_components": len(self.components),
            "healthy": sum(1 for c in self.components if c.health in (HealthStatus.EXCELLENT, HealthStatus.GOOD)),
            "warnings": sum(1 for c in self.components if c.health in (HealthStatus.WARNING, HealthStatus.CRITICAL)),
            "total_tests": len(self.stress_tests),
            "completed_tests": len(self.get_completed_tests()),
            "predictions": len(self.predictions),
            "risk_predictions": len(self.get_warning_predictions()),
            "test_history": len(self.history),
        }
