from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class HardwareCategory(Enum):
    CPU = "cpu"
    GPU = "gpu"
    RAM = "ram"
    STORAGE = "storage"
    MOTHERBOARD = "motherboard"
    NETWORK = "network"
    AUDIO = "audio"
    DISPLAY = "display"
    PERIPHERALS = "peripherals"


class BenchmarkType(Enum):
    CPU_SINGLE = "cpu-single-core"
    CPU_MULTI = "cpu-multi-core"
    MEMORY_READ = "memory-read"
    MEMORY_WRITE = "memory-write"
    MEMORY_COPY = "memory-copy"
    DISK_READ = "disk-sequential-read"
    DISK_WRITE = "disk-sequential-write"
    GPU_COMPUTE = "gpu-compute"
    GPU_OPENGL = "gpu-opengl"
    NETWORK_TCP = "network-tcp-throughput"
    OVERALL = "overall"


@dataclass
class HardwareSpec:
    category: HardwareCategory
    name: str
    manufacturer: str
    model: str
    specs: dict = field(default_factory=dict)

    @property
    def icon(self) -> str:
        icons = {
            HardwareCategory.CPU: "🔲", HardwareCategory.GPU: "🎮", HardwareCategory.RAM: "🧠",
            HardwareCategory.STORAGE: "💾", HardwareCategory.MOTHERBOARD: "📟", HardwareCategory.NETWORK: "🌐",
            HardwareCategory.AUDIO: "🔊", HardwareCategory.DISPLAY: "🖥️", HardwareCategory.PERIPHERALS: "⌨️",
        }
        return icons.get(self.category, "?")


@dataclass
class BenchmarkResult:
    benchmark_type: BenchmarkType
    score: float
    unit: str
    timestamp: float
    duration_ms: int = 0
    percentiles: dict = field(default_factory=dict)

    @property
    def score_display(self) -> str:
        if self.score >= 1_000_000:
            return f"{self.score / 1_000_000:.2f}M {self.unit}"
        if self.score >= 1_000:
            return f"{self.score / 1_000:.1f}K {self.unit}"
        return f"{self.score:.1f} {self.unit}"

    @property
    def percentile_display(self) -> str:
        p50 = self.percentiles.get("p50", 0)
        p90 = self.percentiles.get("p90", 0)
        if self.score >= p90:
            return "Top 10%"
        if self.score >= p50:
            return "Above Average"
        return "Below Average"


@dataclass
class SystemInfo:
    hostname: str
    os_name: str
    os_version: str
    kernel: str
    architecture: str
    uptime_secs: int
    boot_time: float
    install_date: float

    @property
    def uptime_display(self) -> str:
        d, rem = divmod(self.uptime_secs, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        if d:
            return f"{d}d {h}h {m}m"
        return f"{h}h {m}m {s}s"


class SystemInfoDashboard:
    def __init__(self):
        self._hardware: list[HardwareSpec] = []
        self._benchmarks: list[BenchmarkResult] = []
        self._system: Optional[SystemInfo] = None
        self._selected_hardware: int = 0
        self._selected_benchmark: int = 0
        self._view: str = "overview"
        self._view_tab: int = 0
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._system = SystemInfo(
            "nyrqis-workstation", "Nyrqis OS", "1.1.0", "6.12.0-nyrqis", "x86_64",
            86400 * 3 + 3600 * 7 + 120, now - 86400 * 3 - 3600 * 7 - 120, now - 86400 * 60
        )

        self._hardware = [
            HardwareSpec(HardwareCategory.CPU, "Processor", "AMD", "Ryzen 9 7950X", {
                "Cores": "16 (32 threads)", "Base Clock": "4.5 GHz", "Boost Clock": "5.7 GHz",
                "L2 Cache": "16 MB", "L3 Cache": "64 MB", "TDP": "170W", "Socket": "AM5",
                "Process": "5nm TSMC", "Instruction Set": "x86-64-v4"
            }),
            HardwareSpec(HardwareCategory.GPU, "Graphics Card", "NVIDIA", "GeForce RTX 4070", {
                "VRAM": "12 GB GDDR6X", "Core Clock": "2550 MHz", "Memory Clock": "10501 MHz",
                "CUDA Cores": "5888", "RT Cores": "46", "Tensor Cores": "184",
                "TDP": "200W", "Bus": "PCIe 4.0 x16", "Driver": "560.50"
            }),
            HardwareSpec(HardwareCategory.RAM, "Memory", "Corsair", "DDR5-6000 CL30", {
                "Total": "64 GB (2×32 GB)", "Speed": "6000 MT/s", "CAS Latency": "CL30",
                "Voltage": "1.35V", "Type": "DDR5", "Bandwidth": "48 GB/s"
            }),
            HardwareSpec(HardwareCategory.STORAGE, "Primary Storage", "Samsung", "990 PRO 2TB", {
                "Capacity": "2 TB", "Interface": "PCIe 4.0 NVMe", "Seq Read": "7,450 MB/s",
                "Seq Write": "6,900 MB/s", "IOPS Read": "1,400K", "IOPS Write": "1,550K",
                "Endurance": "1,200 TBW", "Form Factor": "M.2 2280"
            }),
            HardwareSpec(HardwareCategory.STORAGE, "Data Drive", "WD", "Red Plus 4TB", {
                "Capacity": "4 TB", "Interface": "SATA III", "RPM": "5640",
                "Cache": "256 MB", "Seq Read": "185 MB/s", "Seq Write": "185 MB/s",
                "Workload": "NAS/RAID", "Form Factor": "3.5\""
            }),
            HardwareSpec(HardwareCategory.MOTHERBOARD, "Motherboard", "ASUS", "ROG STRIX X670E-F", {
                "Socket": "AM5", "Chipset": "X670E", "Form Factor": "ATX",
                "PCIe Slots": "2× PCIe 5.0 x16", "M.2 Slots": "4× M.2",
                "USB Ports": "12 (rear) + 7 (front)", "Audio": "Realtek ALC4082"
            }),
            HardwareSpec(HardwareCategory.NETWORK, "Network", "Intel", "I226-V 2.5GbE", {
                "Speed": "2.5 Gbps", "Interface": "PCIe 3.0 x1", "Connector": "RJ-45",
                "Wake-on-LAN": "Yes", "SR-IOV": "Yes"
            }),
            HardwareSpec(HardwareCategory.DISPLAY, "Primary Display", "LG", "27GP850-B", {
                "Size": "27\"", "Resolution": "2560×1440", "Refresh Rate": "165 Hz",
                "Panel": "Nano IPS", "HDR": "HDR 400", "Response": "1ms GtG",
                "Color": "98% DCI-P3", "Sync": "FreeSync Premium"
            }),
        ]

        self._benchmarks = [
            BenchmarkResult(BenchmarkType.CPU_SINGLE, 2850, "pts", now - 86400, 15000, {"p50": 2100, "p90": 2600}),
            BenchmarkResult(BenchmarkType.CPU_MULTI, 38500, "pts", now - 86400, 120000, {"p50": 28000, "p90": 35000}),
            BenchmarkResult(BenchmarkType.MEMORY_READ, 82_500, "MB/s", now - 86400, 5000, {"p50": 65000, "p90": 78000}),
            BenchmarkResult(BenchmarkType.MEMORY_WRITE, 72_300, "MB/s", now - 86400, 5000, {"p50": 58000, "p90": 70000}),
            BenchmarkResult(BenchmarkType.MEMORY_COPY, 78_100, "MB/s", now - 86400, 5000, {"p50": 60000, "p90": 75000}),
            BenchmarkResult(BenchmarkType.DISK_READ, 7_100, "MB/s", now - 86400, 10000, {"p50": 5000, "p90": 6500}),
            BenchmarkResult(BenchmarkType.DISK_WRITE, 6_800, "MB/s", now - 86400, 10000, {"p50": 4500, "p90": 6000}),
            BenchmarkResult(BenchmarkType.GPU_COMPUTE, 18_500, "pts", now - 86400, 30000, {"p50": 14000, "p90": 17000}),
            BenchmarkResult(BenchmarkType.GPU_OPENGL, 22_400, "fps", now - 86400, 20000, {"p50": 16000, "p90": 20000}),
            BenchmarkResult(BenchmarkType.NETWORK_TCP, 9_400, "Mbps", now - 86400, 5000, {"p50": 8000, "p90": 9200}),
        ]

    @property
    def selected_hardware(self) -> Optional[HardwareSpec]:
        if 0 <= self._selected_hardware < len(self._hardware):
            return self._hardware[self._selected_hardware]
        return None

    @property
    def selected_benchmark(self) -> Optional[BenchmarkResult]:
        if 0 <= self._selected_benchmark < len(self._benchmarks):
            return self._benchmarks[self._selected_benchmark]
        return None

    @property
    def overall_score(self) -> float:
        if not self._benchmarks:
            return 0
        return sum(b.score for b in self._benchmarks) / len(self._benchmarks)

    def select_hardware(self, idx: int):
        if 0 <= idx < len(self._hardware):
            self._selected_hardware = idx

    def select_benchmark(self, idx: int):
        if 0 <= idx < len(self._benchmarks):
            self._selected_benchmark = idx

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM INFORMATION                               ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        if self._system:
            s = self._system
            lines.append(f"  Hostname: {s.hostname}  OS: {s.os_name} {s.os_version}")
            lines.append(f"  Kernel: {s.kernel}  Arch: {s.architecture}")
            lines.append(f"  Uptime: {s.uptime_display}")
        lines.append("")
        lines.append(f"  ── Hardware ({len(self._hardware)} devices) ──")
        for i, h in enumerate(self._hardware):
            sel = "▶" if i == self._selected_hardware else " "
            lines.append(f"  {sel} {h.icon} {h.manufacturer} {h.name}")
        lines.append("")
        lines.append(f"  ── Benchmarks ({len(self._benchmarks)} results) ──")
        for i, b in enumerate(self._benchmarks):
            sel = "▶" if i == self._selected_benchmark else " "
            lines.append(f"  {sel} {b.benchmark_type.value:<20s} {b.score_display:>20s}  {b.percentile_display}")
        lines.append("")
        lines.append("  [H]ardware  [B]enchmarks  [D]etails  [R]un benchmark  [E]xport report")
        return lines

    def render_hardware_detail(self) -> list:
        h = self.selected_hardware
        if not h:
            return ["  No hardware selected"]
        lines = []
        lines.append(f"  {h.icon} ── {h.manufacturer} {h.name} ({h.category.value}) ──")
        lines.append("")
        for key, value in h.specs.items():
            lines.append(f"  {key:<20s} {value}")
        return lines

    def render_benchmark_detail(self) -> list:
        b = self.selected_benchmark
        if not b:
            return ["  No benchmark selected"]
        lines = []
        lines.append(f"  ── {b.benchmark_type.value} ──")
        lines.append(f"  Score: {b.score_display}")
        lines.append(f"  Percentile: {b.percentile_display}")
        lines.append(f"  Duration: {b.duration_ms / 1000:.1f}s")
        if b.percentiles:
            lines.append(f"  P50: {b.percentiles.get('p50', 0):,.0f}  P90: {b.percentiles.get('p90', 0):,.0f}")
        return lines

    def render_benchmarks(self) -> list:
        lines = []
        lines.append("  ── Benchmark Results ──")
        lines.append("")
        for i, b in enumerate(self._benchmarks):
            sel = "▶" if i == self._selected_benchmark else " "
            bar_len = min(int(b.score / max(1, b.percentiles.get("p90", b.score)) * 20), 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {sel} {b.benchmark_type.value:<20s} {bar} {b.score_display}")
        return lines

    def render_system_report(self) -> list:
        lines = []
        lines.append("  ── System Report ──")
        lines.append("")
        if self._system:
            s = self._system
            lines.append(f"  Hostname:      {s.hostname}")
            lines.append(f"  OS:            {s.os_name} {s.os_version}")
            lines.append(f"  Kernel:        {s.kernel}")
            lines.append(f"  Architecture:  {s.architecture}")
            lines.append(f"  Uptime:        {s.uptime_display}")
        lines.append("")
        lines.append("  ── Hardware Summary ──")
        for h in self._hardware:
            lines.append(f"  {h.icon} {h.manufacturer} {h.name}")
        lines.append("")
        lines.append("  ── Benchmark Summary ──")
        lines.append(f"  Overall Score: {self.overall_score:,.0f}")
        for b in self._benchmarks:
            lines.append(f"  {b.benchmark_type.value:<20s} {b.score_display:>15s}  {b.percentile_display}")
        return lines
