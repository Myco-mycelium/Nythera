from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ReportFormat(Enum):
    HTML = "html"
    PDF = "pdf"
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "txt"
    CSV = "csv"
    XML = "xml"


class ReportSection(Enum):
    OVERVIEW = "overview"
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    STORAGE = "storage"
    NETWORK = "network"
    BENCHMARKS = "benchmarks"
    SOFTWARE = "software"
    SECURITY = "security"
    POWER = "power"


@dataclass
class HardwareInfo:
    category: str
    name: str
    manufacturer: str
    details: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return f"{self.manufacturer} {self.name}"


@dataclass
class BenchmarkData:
    name: str
    score: float
    unit: str
    percentile: str
    timestamp: float


@dataclass
class ReportTemplate:
    name: str
    format: ReportFormat
    sections: list = field(default_factory=list)
    include_charts: bool = True
    include_benchmarks: bool = True
    theme: str = "default"


class SystemReport:
    def __init__(self):
        self._hardware: list[HardwareInfo] = []
        self._benchmarks: list[BenchmarkData] = []
        self._templates: list[ReportTemplate] = []
        self._selected_template: int = 0
        self._selected_section: int = 0
        self._hostname: str = "nyrqis-workstation"
        self._os_version: str = "Nyrqis OS 1.1.0"
        self._kernel: str = "6.12.0-nyrqis"
        self._uptime: str = "3d 7h 2m"
        self._generated_reports: list = []
        self._view: str = "templates"
        self._create_samples()

    def _create_samples(self):
        self._hardware = [
            HardwareInfo("CPU", "Ryzen 9 7950X", "AMD", {"cores": "16 (32 threads)", "clock": "4.5/5.7 GHz", "tdp": "170W", "cache": "80 MB"}),
            HardwareInfo("GPU", "GeForce RTX 4070", "NVIDIA", {"vram": "12 GB GDDR6X", "clock": "2550 MHz", "cuda": "5888", "tdp": "200W"}),
            HardwareInfo("RAM", "DDR5-6000 CL30", "Corsair", {"total": "64 GB", "speed": "6000 MT/s", "latency": "CL30"}),
            HardwareInfo("Storage", "990 PRO 2TB", "Samsung", {"capacity": "2 TB", "interface": "PCIe 4.0 NVMe", "seq_read": "7450 MB/s"}),
            HardwareInfo("Motherboard", "ROG STRIX X670E-F", "ASUS", {"socket": "AM5", "chipset": "X670E"}),
            HardwareInfo("Display", "27GP850-B", "LG", {"resolution": "2560x1440", "refresh": "165 Hz", "panel": "Nano IPS"}),
        ]
        self._benchmarks = [
            BenchmarkData("CPU Single-Core", 2850, "pts", "Top 10%", time.time() - 86400),
            BenchmarkData("CPU Multi-Core", 38500, "pts", "Top 10%", time.time() - 86400),
            BenchmarkData("Memory Read", 82500, "MB/s", "Above Avg", time.time() - 86400),
            BenchmarkData("Disk Sequential Read", 7100, "MB/s", "Top 10%", time.time() - 86400),
            BenchmarkData("GPU Compute", 18500, "pts", "Top 10%", time.time() - 86400),
        ]
        self._templates = [
            ReportTemplate("Full System Report", ReportFormat.HTML, list(ReportSection), True, True, "default"),
            ReportTemplate("Quick Summary", ReportFormat.MARKDOWN, [ReportSection.OVERVIEW, ReportSection.CPU, ReportSection.GPU], False, False, "minimal"),
            ReportTemplate("Benchmark Report", ReportFormat.PDF, [ReportSection.BENCHMARKS, ReportSection.OVERVIEW], True, True, "charts"),
            ReportTemplate("Hardware Inventory", ReportFormat.CSV, [ReportSection.OVERVIEW], False, False, "table"),
            ReportTemplate("Security Audit", ReportFormat.HTML, [ReportSection.SECURITY, ReportSection.OVERVIEW], False, False, "security"),
            ReportTemplate("JSON Export", ReportFormat.JSON, list(ReportSection), False, False, "raw"),
        ]
        self._generated_reports = [
            {"name": "system-report-2026-09-01.html", "format": "html", "size": "45 KB", "date": "2d ago"},
            {"name": "benchmark-report.pdf", "format": "pdf", "size": "128 KB", "date": "5d ago"},
        ]

    @property
    def selected_template(self) -> Optional[ReportTemplate]:
        if 0 <= self._selected_template < len(self._templates):
            return self._templates[self._selected_template]
        return None

    @property
    def total_sections(self) -> int:
        return len(ReportSection)

    def select_template(self, idx: int):
        if 0 <= idx < len(self._templates):
            self._selected_template = idx

    def generate_report(self, template_idx: int = -1) -> str:
        t = self._templates[template_idx] if template_idx >= 0 else self.selected_template
        if not t:
            return "No template selected"
        lines = []
        if t.format == ReportFormat.HTML:
            lines.append("<!DOCTYPE html>")
            lines.append("<html><head><title>System Report</title></head><body>")
            lines.append(f"<h1>{self._hostname} - System Report</h1>")
            lines.append(f"<p>OS: {self._os_version} | Kernel: {self._kernel} | Uptime: {self._uptime}</p>")
            for section in t.sections:
                lines.append(f"<h2>{section.value.title()}</h2>")
            lines.append("</body></html>")
        elif t.format == ReportFormat.MARKDOWN:
            lines.append(f"# {self._hostname} System Report\n")
            lines.append(f"**OS:** {self._os_version}  **Kernel:** {self._kernel}  **Uptime:** {self._uptime}\n")
            for section in t.sections:
                lines.append(f"## {section.value.title()}\n")
                if section == ReportSection.CPU:
                    for h in self._hardware:
                        if h.category == "CPU":
                            lines.append(f"- {h.summary}")
                            for k, v in h.details.items():
                                lines.append(f"  - {k}: {v}")
            lines.append(f"\n*Generated: {time.strftime('%Y-%m-%d %H:%M')}*")
        elif t.format == ReportFormat.JSON:
            import json
            data = {"hostname": self._hostname, "os": self._os_version, "kernel": self._kernel,
                    "hardware": [{"category": h.category, "name": h.summary, "details": h.details} for h in self._hardware]}
            lines.append(json.dumps(data, indent=2))
        elif t.format == ReportFormat.CSV:
            lines.append("Category,Manufacturer,Name,Details")
            for h in self._hardware:
                details = "; ".join(f"{k}={v}" for k, v in h.details.items())
                lines.append(f"{h.category},{h.manufacturer},{h.name},{details}")
        else:
            lines.append(f"System Report: {self._hostname}")
            lines.append(f"OS: {self._os_version}")
            for h in self._hardware:
                lines.append(f"  {h.category}: {h.summary}")
        report_name = f"report-{time.strftime('%Y%m%d')}.{t.format.value}"
        self._generated_reports.append({"name": report_name, "format": t.format.value, "size": f"{len(lines) * 20} B", "date": "just now"})
        return "\n".join(lines)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM REPORT GENERATOR                          ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Host: {self._hostname}  OS: {self._os_version}  Kernel: {self._kernel}  Uptime: {self._uptime}")
        lines.append(f"  Hardware: {len(self._hardware)} devices  Benchmarks: {len(self._benchmarks)}  Sections: {self.total_sections}")
        lines.append("")
        lines.append("  ── Templates ──")
        for i, t in enumerate(self._templates):
            sel = "▶" if i == self._selected_template else " "
            lines.append(f"  {sel} {t.name}  [{t.format.value.upper()}]  {len(t.sections)} sections  charts: {'ON' if t.include_charts else 'OFF'}")
        lines.append("")
        lines.append("  ── Hardware ──")
        for h in self._hardware:
            lines.append(f"  📦 {h.summary}  {', '.join(f'{k}={v}' for k, v in list(h.details.items())[:2])}")
        lines.append("")
        lines.append("  ── Benchmarks ──")
        for b in self._benchmarks:
            lines.append(f"  📊 {b.name:<20s} {b.score:>10,.0f} {b.unit}  ({b.percentile})")
        lines.append("")
        lines.append("  ── Generated Reports ──")
        for r in self._generated_reports[-3:]:
            lines.append(f"  📄 {r['name']}  {r['format'].upper()}  {r['size']}  {r['date']}")
        lines.append("")
        lines.append("  [G]enerate  [P]review  [S]ections  [F]ormat  [E]xport  [T]emplate")
        return lines

    def render_preview(self) -> list:
        report = self.generate_report()
        lines = []
        lines.append("  ── Report Preview ──")
        lines.append("")
        for line in report.split("\n")[:20]:
            lines.append(f"  {line}")
        if len(report.split("\n")) > 20:
            lines.append(f"  ... ({len(report.split(chr(10)))} total lines)")
        return lines

    def render_sections(self) -> list:
        lines = []
        lines.append("  ── Available Sections ──")
        lines.append("")
        for i, section in enumerate(ReportSection):
            included = any(section in t.sections for t in self._templates if t == self.selected_template)
            status = "✅" if included else "  "
            lines.append(f"  {status} {section.value}")
        return lines
