"""
Nyrqis OS - Printer Manager
Queue management, test page printing, driver installation, and print job tracking.

Features:
- Printer discovery and management (local, network, CUPS)
- Print job queue with status tracking
- Test page printing
- Driver/PPD management
- Ink/toner level monitoring
- Paper tray management
- Print history and statistics
- Scanner integration (MFP devices)
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class PrinterType(Enum):
    LOCAL = "local"
    NETWORK = "network"
    CUPS = "cups"
    USB = "usb"
    BLUETOOTH = "bluetooth"
    IPP = "ipp"


class PrinterState(Enum):
    IDLE = "idle"
    PRINTING = "printing"
    STOPPED = "stopped"
    ERROR = "error"
    OFFLINE = "offline"
    WARMING_UP = "warming_up"


class JobState(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HELD = "held"


class PaperSize(Enum):
    A4 = "A4"
    A3 = "A3"
    LETTER = "Letter"
    LEGAL = "Legal"
    TABLOID = "Tabloid"
    EXECUTIVE = "Executive"
    A5 = "A5"
    PHOTO_4x6 = "4x6 Photo"
    PHOTO_5x7 = "5x7 Photo"
    CUSTOM = "Custom"
    LABEL = "Label"
    BANNER = "Banner"


class DuplexMode(Enum):
    NONE = "none"
    LONG_EDGE = "long-edge"
    SHORT_EDGE = "short-edge"


class PrintColorMode(Enum):
    COLOR = "color"
    GRAYSCALE = "grayscale"
    BLACK_ONLY = "black-only"


class MediaType(Enum):
    PLAIN = "plain"
    PHOTO = "photo"
    ENVELOPE = "envelope"
    LABEL = "label"
    CARDSTOCK = "cardstock"
    TRANSPARENCY = "transparency"
    BANNER = "banner"


PRINTER_STATE_ICONS = {
    PrinterState.IDLE: "🟢",
    PrinterState.PRINTING: "🖨️",
    PrinterState.STOPPED: "🔴",
    PrinterState.ERROR: "❌",
    PrinterState.OFFLINE: "⚫",
    PrinterState.WARMING_UP: "🟡",
}

JOB_STATE_ICONS = {
    JobState.PENDING: "⏳",
    JobState.PROCESSING: "🔄",
    JobState.COMPLETED: "✅",
    JobState.FAILED: "❌",
    JobState.CANCELLED: "🚫",
    JobState.HELD: "⏸",
}

PRINTER_TYPE_ICONS = {
    PrinterType.LOCAL: "🔌",
    PrinterType.NETWORK: "🌐",
    PrinterType.CUPS: "🖨️",
    PrinterType.USB: "🔌",
    PrinterType.BLUETOOTH: "📡",
    PrinterType.IPP: "🌐",
}


@dataclass
class InkLevel:
    color: str = "Black"
    current_ml: float = 0.0
    max_ml: float = 0.0

    @property
    def percent(self) -> float:
        if self.max_ml == 0:
            return 0
        return (self.current_ml / self.max_ml) * 100

    @property
    def bar(self) -> str:
        filled = int(self.percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        p = self.percent
        if p > 50:
            return "🟢"
        elif p > 20:
            return "🟡"
        elif p > 5:
            return "🟠"
        return "🔴"

    @property
    def ml_str(self) -> str:
        return f"{self.current_ml:.0f}/{self.max_ml:.0f} mL"


@dataclass
class PaperTray:
    name: str = "Main Tray"
    paper_size: PaperSize = PaperSize.A4
    current_sheets: int = 0
    max_sheets: int = 0
    media_type: MediaType = MediaType.PLAIN

    @property
    def percent(self) -> float:
        if self.max_sheets == 0:
            return 0
        return (self.current_sheets / self.max_sheets) * 100

    @property
    def bar(self) -> str:
        filled = int(self.percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        p = self.percent
        if p > 50:
            return "🟢"
        elif p > 10:
            return "🟡"
        return "🔴"

    @property
    def sheets_str(self) -> str:
        return f"{self.current_sheets}/{self.max_sheets}"


@dataclass
class DriverInfo:
    name: str = ""
    version: str = ""
    ppd_path: str = ""
    manufacturer: str = ""
    is_generic: bool = False
    installed: bool = True
    date: str = ""

    @property
    def status_icon(self) -> str:
        return "✅" if self.installed else "❌"


@dataclass
class PrintJob:
    id: int = 0
    name: str = ""
    printer: str = ""
    user: str = ""
    state: JobState = JobState.PENDING
    pages: int = 0
    copies: int = 1
    color_mode: PrintColorMode = PrintColorMode.COLOR
    duplex: DuplexMode = DuplexMode.NONE
    paper_size: PaperSize = PaperSize.A4
    submitted: float = 0.0
    started: float = 0.0
    completed: float = 0.0
    file_size: int = 0
    file_name: str = ""

    @property
    def state_icon(self) -> str:
        return JOB_STATE_ICONS.get(self.state, "❓")

    @property
    def submitted_str(self) -> str:
        if self.submitted == 0:
            return "N/A"
        return time.strftime("%H:%M:%S", time.localtime(self.submitted))

    @property
    def duration_str(self) -> str:
        if self.started == 0:
            return "N/A"
        end = self.completed if self.completed > 0 else time.time()
        delta = end - self.started
        if delta < 60:
            return f"{delta:.1f}s"
        return f"{delta / 60:.1f}m"

    @property
    def file_size_str(self) -> str:
        b = self.file_size
        if b == 0:
            return "N/A"
        if b < 1024:
            return f"{b} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        return f"{b / 1024 ** 2:.1f} MB"

    @property
    def details(self) -> str:
        parts = []
        parts.append(f"{self.copies}× {self.pages}p")
        parts.append("Color" if self.color_mode == PrintColorMode.COLOR else "BW")
        if self.duplex != DuplexMode.NONE:
            parts.append("Duplex")
        parts.append(self.paper_size.value)
        return " · ".join(parts)


@dataclass
class Printer:
    name: str = ""
    model: str = ""
    printer_type: PrinterType = PrinterType.LOCAL
    state: PrinterState = PrinterState.IDLE
    location: str = ""
    uri: str = ""
    driver: DriverInfo = field(default_factory=DriverInfo)
    ink_levels: List[InkLevel] = field(default_factory=list)
    trays: List[PaperTray] = field(default_factory=list)
    supported_sizes: List[PaperSize] = field(default_factory=list)
    supports_duplex: bool = True
    supports_color: bool = True
    supports_scanner: bool = False
    resolution_dpi: str = "4800x1200"
    ppm_color: int = 0
    ppm_mono: int = 0
    current_job: Optional[PrintJob] = None
    jobs_printed: int = 0
    total_pages: int = 0
    uptime_hours: float = 0.0

    @property
    def type_icon(self) -> str:
        return PRINTER_TYPE_ICONS.get(self.printer_type, "❓")

    @property
    def state_icon(self) -> str:
        return PRINTER_STATE_ICONS.get(self.state, "❓")

    @property
    def speed_str(self) -> str:
        return f"{self.ppm_mono}/{self.ppm_color} ppm (B&W/Color)"

    @property
    def ink_summary(self) -> str:
        if not self.ink_levels:
            return "N/A"
        avg = sum(ink.percent for ink in self.ink_levels) / len(self.ink_levels)
        if avg > 50:
            return f"🟢 {avg:.0f}%"
        elif avg > 20:
            return f"🟡 {avg:.0f}%"
        return f"🔴 {avg:.0f}%"

    @property
    def paper_summary(self) -> str:
        if not self.trays:
            return "N/A"
        total = sum(t.current_sheets for t in self.trays)
        return f"{total} sheets"

    @property
    def is_available(self) -> bool:
        return self.state in (PrinterState.IDLE, PrinterState.WARMING_UP)


class PrinterManager:
    def __init__(self):
        self.printers: List[Printer] = []
        self.jobs: List[PrintJob] = []
        self.drivers: List[DriverInfo] = []
        self._selected_printer: int = 0
        self._selected_job: int = 0
        self._view_mode: str = "printers"
        self._job_counter: int = 100
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.printers = [
            Printer(
                name="HP-LaserJet-Pro", model="HP LaserJet Pro MFP M428fdw",
                printer_type=PrinterType.NETWORK,
                state=PrinterState.IDLE,
                location="Office", uri="ipp://192.168.1.50/ipp/print",
                driver=DriverInfo("HP LaserJet PPD", "3.2.1", "/usr/share/ppd/hp/", "HP", True),
                ink_levels=[
                    InkLevel("Black", 850, 1000),
                ],
                trays=[
                    PaperTray("Main Tray", PaperSize.A4, 250, 500),
                    PaperTray("Multi-Purpose", PaperSize.LETTER, 50, 100),
                ],
                supported_sizes=[PaperSize.A4, PaperSize.LETTER, PaperSize.LEGAL],
                resolution_dpi="4800x1200",
                ppm_mono=40, ppm_color=0,
                jobs_printed=342, total_pages=12580, uptime_hours=4320,
            ),
            Printer(
                name="Canon-PIXMA", model="Canon PIXMA TS9520",
                printer_type=PrinterType.USB,
                state=PrinterState.IDLE,
                location="Desk", uri="usb://Canon/PIXMA%20TS9520",
                driver=DriverInfo("Canon Inkjet PPD", "5.70", "/usr/share/ppd/canon/", "Canon"),
                ink_levels=[
                    InkLevel("Black", 45, 100),
                    InkLevel("Cyan", 32, 100),
                    InkLevel("Magenta", 28, 100),
                    InkLevel("Yellow", 55, 100),
                ],
                trays=[
                    PaperTray("Front Tray", PaperSize.PHOTO_4x6, 20, 100),
                    PaperTray("Rear Tray", PaperSize.A4, 80, 200),
                ],
                supported_sizes=[PaperSize.A4, PaperSize.A5, PaperSize.PHOTO_4x6, PaperSize.PHOTO_5x7, PaperSize.LETTER],
                resolution_dpi="4800x2400",
                ppm_mono=15, ppm_color=10,
                supports_scanner=True,
                jobs_printed=89, total_pages=1240, uptime_hours=890,
            ),
            Printer(
                name="Epson-WorkForce", model="Epson WorkForce WF-2830",
                printer_type=PrinterType.NETWORK,
                state=PrinterState.ERROR,
                location="Shared Office", uri="ipp://192.168.1.60/ipp/print",
                driver=DriverInfo("Epson PPD", "2.1.0", "/usr/share/ppd/epson/", "Epson"),
                ink_levels=[
                    InkLevel("Black", 2, 100),
                    InkLevel("Cyan", 0, 100),
                    InkLevel("Magenta", 0, 100),
                    InkLevel("Yellow", 0, 100),
                ],
                trays=[
                    PaperTray("Main Tray", PaperSize.A4, 0, 100),
                ],
                supported_sizes=[PaperSize.A4, PaperSize.LETTER, PaperSize.A5],
                resolution_dpi="4800x1200",
                ppm_mono=10, ppm_color=4.5,
                supports_scanner=True,
                jobs_printed=567, total_pages=8900, uptime_hours=2100,
            ),
            Printer(
                name="Brother-HL", model="Brother HL-L2370DW",
                printer_type=PrinterType.CUPS,
                state=PrinterState.PRINTING,
                location="Server Room", uri="cups://localhost/brother-hl-l2370dw",
                driver=DriverInfo("Brother PPD", "1.1.4", "/usr/share/ppd/brother/", "Brother", True),
                ink_levels=[
                    InkLevel("Black", 620, 1000),
                ],
                trays=[
                    PaperTray("Main Tray", PaperSize.A4, 180, 250),
                ],
                supported_sizes=[PaperSize.A4, PaperSize.LETTER, PaperSize.LEGAL],
                resolution_dpi="2400x600",
                ppm_mono=36, ppm_color=0,
                current_job=PrintJob(id=101, name="report.pdf", printer="Brother-HL",
                                     user="admin", state=JobState.PROCESSING,
                                     pages=12, submitted=now - 30, started=now - 10),
                jobs_printed=1203, total_pages=45600, uptime_hours=8760,
            ),
        ]

        self.jobs = [
            PrintJob(id=1, name="quarterly_report.pdf", printer="HP-LaserJet-Pro",
                     user="alice", state=JobState.COMPLETED, pages=24, copies=2,
                     color_mode=PrintColorMode.GRAYSCALE, duplex=DuplexMode.LONG_EDGE,
                     paper_size=PaperSize.A4, submitted=now - 3600, started=now - 3595,
                     completed=now - 3580, file_size=2500000,
                     file_name="quarterly_report.pdf"),
            PrintJob(id=2, name="presentation.pptx", printer="Canon-PIXMA",
                     user="bob", state=JobState.COMPLETED, pages=30, copies=1,
                     color_mode=PrintColorMode.COLOR, paper_size=PaperSize.LETTER,
                     submitted=now - 7200, started=now - 7195, completed=now - 7100,
                     file_size=8500000, file_name="presentation.pptx"),
            PrintJob(id=3, name="invoice_2026.pdf", printer="HP-LaserJet-Pro",
                     user="charlie", state=JobState.COMPLETED, pages=2, copies=1,
                     color_mode=PrintColorMode.GRAYSCALE, paper_size=PaperSize.A4,
                     submitted=now - 1800, started=now - 1798, completed=now - 1795,
                     file_size=150000, file_name="invoice_2026.pdf"),
            PrintJob(id=4, name="photo_collage.jpg", printer="Canon-PIXMA",
                     user="alice", state=JobState.COMPLETED, pages=4, copies=1,
                     color_mode=PrintColorMode.COLOR, paper_size=PaperSize.PHOTO_4x6,
                     submitted=now - 5400, started=now - 5390, completed=now - 5340,
                     file_size=12000000, file_name="photo_collage.jpg"),
            PrintJob(id=5, name="contract_draft.pdf", printer="Brother-HL",
                     user="diana", state=JobState.COMPLETED, pages=8, copies=3,
                     color_mode=PrintColorMode.GRAYSCALE, duplex=DuplexMode.LONG_EDGE,
                     paper_size=PaperSize.LEGAL, submitted=now - 900, started=now - 895,
                     completed=now - 870, file_size=540000,
                     file_name="contract_draft.pdf"),
            PrintJob(id=6, name="label_template.pdf", printer="Epson-WorkForce",
                     user="eve", state=JobState.FAILED, pages=1, copies=1,
                     color_mode=PrintColorMode.COLOR, paper_size=PaperSize.A4,
                     submitted=now - 600, file_size=80000,
                     file_name="label_template.pdf"),
            PrintJob(id=7, name="resume_v3.pdf", printer="HP-LaserJet-Pro",
                     user="frank", state=JobState.PENDING, pages=3, copies=1,
                     color_mode=PrintColorMode.GRAYSCALE, paper_size=PaperSize.LETTER,
                     submitted=now - 30, file_size=200000,
                     file_name="resume_v3.pdf"),
            PrintJob(id=8, name="meeting_notes.md", printer="Brother-HL",
                     user="alice", state=JobState.PENDING, pages=5, copies=1,
                     color_mode=PrintColorMode.GRAYSCALE, paper_size=PaperSize.A4,
                     submitted=now - 15, file_size=45000,
                     file_name="meeting_notes.md"),
            PrintJob(id=9, name="banner_design.pdf", printer="Canon-PIXMA",
                     user="bob", state=JobState.HELD, pages=1, copies=1,
                     color_mode=PrintColorMode.COLOR, paper_size=PaperSize.A4,
                     submitted=now - 120, file_size=25000000,
                     file_name="banner_design.pdf"),
            PrintJob(id=10, name="report.pdf", printer="Brother-HL",
                     user="admin", state=JobState.PROCESSING, pages=12, copies=1,
                     color_mode=PrintColorMode.GRAYSCALE, paper_size=PaperSize.A4,
                     submitted=now - 30, started=now - 10,
                     file_size=340000, file_name="report.pdf"),
        ]
        self._job_counter = 101

        self.drivers = [
            DriverInfo("HP LaserJet PPD", "3.2.1", "/usr/share/ppd/hp/", "HP", True, True, "2026-01-15"),
            DriverInfo("Canon Inkjet PPD", "5.70", "/usr/share/ppd/canon/", "Canon", False, True, "2025-11-20"),
            DriverInfo("Epson PPD", "2.1.0", "/usr/share/ppd/epson/", "Epson", False, True, "2025-09-01"),
            DriverInfo("Brother PPD", "1.1.4", "/usr/share/ppd/brother/", "Brother", True, True, "2026-02-10"),
            DriverInfo("Generic PostScript", "1.0", "/usr/share/ppd/generic/", "Generic", True, True, "2024-06-01"),
            DriverInfo("Generic PCL 6", "1.0", "/usr/share/ppd/generic/", "Generic", True, True, "2024-06-01"),
            DriverInfo("Samsung Universal", "4.0.0", "/usr/share/ppd/samsung/", "Samsung", False, False, "2025-03-15"),
            DriverInfo("Xerox WorkCentre", "2.5.1", "/usr/share/ppd/xerox/", "Xerox", False, False, "2025-08-20"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_printer(self) -> Optional[Printer]:
        if 0 <= self._selected_printer < len(self.printers):
            return self.printers[self._selected_printer]
        return None

    @property
    def selected_job(self) -> Optional[PrintJob]:
        if 0 <= self._selected_job < len(self.jobs):
            return self.jobs[self._selected_job]
        return None

    def select_printer(self, idx: int):
        if 0 <= idx < len(self.printers):
            self._selected_printer = idx

    def select_job(self, idx: int):
        if 0 <= idx < len(self.jobs):
            self._selected_job = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "printers":
            self._selected_printer = min(self._selected_printer + 1, len(self.printers) - 1)
        elif self._view_mode == "jobs":
            self._selected_job = min(self._selected_job + 1, len(self.jobs) - 1)

    def select_up(self):
        if self._view_mode == "printers":
            self._selected_printer = max(self._selected_printer - 1, 0)
        elif self._view_mode == "jobs":
            self._selected_job = max(self._selected_job - 1, 0)

    # ─── Printer Actions ───────────────────────────────────────────────

    def print_test_page(self, printer_idx: int = -1) -> Optional[PrintJob]:
        i = printer_idx if printer_idx >= 0 else self._selected_printer
        if 0 <= i < len(self.printers):
            printer = self.printers[i]
            if printer.state == PrinterState.ERROR:
                return None
            self._job_counter += 1
            job = PrintJob(
                id=self._job_counter, name="Test Page",
                printer=printer.name, user="system",
                state=JobState.PENDING, pages=1,
                color_mode=PrintColorMode.COLOR,
                paper_size=PaperSize.A4, submitted=time.time(),
                file_size=50000, file_name="test_page.ps",
            )
            self.jobs.insert(0, job)
            printer.jobs_printed += 1
            printer.total_pages += 1
            return job
        return None

    def cancel_job(self, job_idx: int) -> bool:
        if 0 <= job_idx < len(self.jobs):
            job = self.jobs[job_idx]
            if job.state in (JobState.PENDING, JobState.HELD, JobState.PROCESSING):
                job.state = JobState.CANCELLED
                return True
        return False

    def retry_job(self, job_idx: int) -> Optional[PrintJob]:
        if 0 <= job_idx < len(self.jobs):
            old = self.jobs[job_idx]
            if old.state == JobState.FAILED:
                self._job_counter += 1
                new_job = PrintJob(
                    id=self._job_counter, name=f"{old.name} (retry)",
                    printer=old.printer, user=old.user,
                    state=JobState.PENDING, pages=old.pages,
                    copies=old.copies, color_mode=old.color_mode,
                    duplex=old.duplex, paper_size=old.paper_size,
                    submitted=time.time(), file_size=old.file_size,
                    file_name=old.file_name,
                )
                self.jobs.insert(0, new_job)
                return new_job
        return None

    def clear_completed(self) -> int:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.state not in (JobState.COMPLETED, JobState.CANCELLED)]
        return before - len(self.jobs)

    def stop_printer(self, idx: int) -> bool:
        if 0 <= idx < len(self.printers):
            p = self.printers[idx]
            if p.state != PrinterState.OFFLINE:
                p.state = PrinterState.STOPPED
                return True
        return False

    def start_printer(self, idx: int) -> bool:
        if 0 <= idx < len(self.printers):
            p = self.printers[idx]
            if p.state in (PrinterState.STOPPED, PrinterState.ERROR):
                p.state = PrinterState.IDLE
                return True
        return False

    # ─── Stats ─────────────────────────────────────────────────────────

    def get_idle_printers(self) -> List[Printer]:
        return [p for p in self.printers if p.state == PrinterState.IDLE]

    def get_available_printers(self) -> List[Printer]:
        return [p for p in self.printers if p.is_available]

    def get_pending_jobs(self) -> List[PrintJob]:
        return [j for j in self.jobs if j.state == JobState.PENDING]

    def get_completed_jobs(self) -> List[PrintJob]:
        return [j for j in self.jobs if j.state == JobState.COMPLETED]

    def get_failed_jobs(self) -> List[PrintJob]:
        return [j for j in self.jobs if j.state == JobState.FAILED]

    def get_stats(self) -> Dict:
        return {
            "total_printers": len(self.printers),
            "idle_printers": len(self.get_idle_printers()),
            "total_jobs": len(self.jobs),
            "pending_jobs": len(self.get_pending_jobs()),
            "completed_jobs": len(self.get_completed_jobs()),
            "failed_jobs": len(self.get_failed_jobs()),
            "total_pages_printed": sum(p.total_pages for p in self.printers),
            "drivers": len(self.drivers),
        }

    # ─── Search ────────────────────────────────────────────────────────

    def search_printers(self, query: str) -> List[Printer]:
        q = query.lower()
        return [p for p in self.printers if q in p.name.lower() or q in p.model.lower()]

    def search_jobs(self, query: str) -> List[PrintJob]:
        q = query.lower()
        return [j for j in self.jobs if q in j.name.lower() or q in j.user.lower()]

    def search_drivers(self, query: str) -> List[DriverInfo]:
        q = query.lower()
        return [d for d in self.drivers if q in d.name.lower() or q in d.manufacturer.lower()]
