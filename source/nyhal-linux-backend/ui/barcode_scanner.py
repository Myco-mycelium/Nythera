"""
Nyrqis Barcode Scanner — barcode and QR code scanning application.

Features:
- Scan barcodes and QR codes from camera
- Generate barcodes/QR codes
- Scan history with categories
- Batch processing mode
- Multiple barcode formats (QR, Code128, EAN-13, etc.)
- Clipboard copy and sharing
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional
from datetime import datetime


class BarcodeFormat(Enum):
    QR_CODE = "QR Code"
    CODE128 = "Code 128"
    EAN13 = "EAN-13"
    EAN8 = "EAN-8"
    UPC_A = "UPC-A"
    CODE39 = "Code 39"
    ITF = "ITF"
    DATA_MATRIX = "DataMatrix"
    AZTEC = "Aztec"
    PDF417 = "PDF417"


class ScanMode(Enum):
    CAMERA = "camera"
    FILE = "file"
    MANUAL = "manual"


class ContentType(Enum):
    TEXT = "text"
    URL = "url"
    EMAIL = "email"
    PHONE = "phone"
    WIFI = "wifi"
    VCARD = "vcard"
    GEO = "geo"
    PRODUCT = "product"
    OTHER = "other"


FORMAT_ICONS = {
    BarcodeFormat.QR_CODE: "📱",
    BarcodeFormat.CODE128: "📊",
    BarcodeFormat.EAN13: "🏷️",
    BarcodeFormat.UPC_A: "🛒",
    BarcodeFormat.DATA_MATRIX: "📋",
}

CONTENT_ICONS = {
    ContentType.TEXT: "📝",
    ContentType.URL: "🔗",
    ContentType.EMAIL: "📧",
    ContentType.PHONE: "📞",
    ContentType.WIFI: "📶",
    ContentType.PRODUCT: "🛒",
}


@dataclass
class ScanResult:
    """A single scan result."""
    content: str
    barcode_format: BarcodeFormat = BarcodeFormat.QR_CODE
    content_type: ContentType = ContentType.TEXT
    timestamp: float = field(default_factory=time.time)
    # Metadata
    confidence: float = 100.0
    scan_mode: ScanMode = ScanMode.CAMERA
    batch_id: str = ""
    # Actions taken
    copied: bool = False
    shared: bool = False
    bookmarked: bool = False
    scan_id: str = ""

    def __post_init__(self):
        if not self.scan_id:
            self.scan_id = hashlib.md5(f"{self.content}{self.timestamp}".encode()).hexdigest()[:8]

    @property
    def format_icon(self) -> str:
        return FORMAT_ICONS.get(self.barcode_format, "❓")

    @property
    def content_icon(self) -> str:
        return CONTENT_ICONS.get(self.content_type, "❓")

    @property
    def display(self) -> str:
        return f"{self.format_icon} {self.content[:60]}"

    @property
    def preview(self) -> str:
        if len(self.content) > 80:
            return self.content[:80] + "..."
        return self.content

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class BatchJob:
    """A batch scanning job."""
    name: str
    results: List[ScanResult] = field(default_factory=list)
    status: str = "pending"  # pending, running, completed
    created: float = field(default_factory=time.time)
    batch_id: str = ""

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def display(self) -> str:
        status_icon = "✅" if self.status == "completed" else "🔄" if self.status == "running" else "⏳"
        return f"{status_icon} {self.name} ({self.count} scans)"


@dataclass
class BarcodeTemplate:
    """A barcode generation template."""
    name: str
    format: BarcodeFormat
    description: str
    example: str
    icon: str = ""

    @property
    def display(self) -> str:
        return f"{self.icon or FORMAT_ICONS.get(self.format, '❓')} {self.name}"


class BarcodeScanner:
    """Barcode and QR code scanning for Nyrqis OS."""

    def __init__(self):
        self._results: List[ScanResult] = []
        self._batches: List[BatchJob] = []
        self._templates: List[BarcodeTemplate] = []
        self._selected_index: int = 0
        self._view_mode: str = "scan"  # scan, history, batch, generate
        self._scan_mode: ScanMode = ScanMode.CAMERA
        self._camera_active: bool = False
        self._auto_copy: bool = True
        self._generate_format: BarcodeFormat = BarcodeFormat.QR_CODE
        self._generate_content: str = ""

        self._init_templates()
        self._init_sample_data()

    def _init_templates(self) -> None:
        self._templates = [
            BarcodeTemplate("QR Code", BarcodeFormat.QR_CODE, "Universal 2D code", "https://nyrqis.os", "📱"),
            BarcodeTemplate("Code 128", BarcodeFormat.CODE128, "High-density 1D barcode", "NYRQIS-2026", "📊"),
            BarcodeTemplate("EAN-13", BarcodeFormat.EAN13, "International product code", "5901234123457", "🏷️"),
            BarcodeTemplate("UPC-A", BarcodeFormat.UPC_A, "US product code", "012345678905", "🛒"),
            BarcodeTemplate("DataMatrix", BarcodeFormat.DATA_MATRIX, "2D matrix code", "ID:NYRQIS-001", "📋"),
        ]

    def _init_sample_data(self) -> None:
        now = time.time()
        self._results = [
            ScanResult("https://github.com/Myco-mycelium/Nythera", BarcodeFormat.QR_CODE,
                       ContentType.URL, now - 120, 99.8, ScanMode.CAMERA, bookmarked=True),
            ScanResult("WIFI:T:WPA2;S:NyrqisHome;P:myc3l1um;;", BarcodeFormat.QR_CODE,
                       ContentType.WIFI, now - 300, 100.0, ScanMode.CAMERA),
            ScanResult("BEGIN:VCARD\nVERSION:3.0\nFN:Dev Team\nTEL:+15551234567\nEND:VCARD",
                       BarcodeFormat.QR_CODE, ContentType.VCARD, now - 600, 98.5, ScanMode.FILE),
            ScanResult("5901234123457", BarcodeFormat.EAN13,
                       ContentType.PRODUCT, now - 1800, 100.0, ScanMode.CAMERA),
            ScanResult("tel:+1-555-0123", BarcodeFormat.QR_CODE,
                       ContentType.PHONE, now - 3600, 99.9, ScanMode.CAMERA),
            ScanResult("https://nyrqis.os/docs/getting-started", BarcodeFormat.QR_CODE,
                       ContentType.URL, now - 7200, 100.0, ScanMode.CAMERA, copied=True),
            ScanResult("NYRQIS-KERNEL-2026-09", BarcodeFormat.CODE128,
                       ContentType.TEXT, now - 14400, 97.2, ScanMode.FILE),
            ScanResult("mailto:dev@nyrqis.os", BarcodeFormat.QR_CODE,
                       ContentType.EMAIL, now - 28800, 99.5, ScanMode.CAMERA),
            ScanResult("012345678905", BarcodeFormat.UPC_A,
                       ContentType.PRODUCT, now - 43200, 100.0, ScanMode.CAMERA),
            ScanResult("geo:37.7749,-122.4194", BarcodeFormat.QR_CODE,
                       ContentType.GEO, now - 86400, 99.0, ScanMode.CAMERA),
        ]

        self._batches = [
            BatchJob("Inventory Scan", [
                ScanResult("ITEM-001", BarcodeFormat.CODE128, ContentType.TEXT, now - 3600),
                ScanResult("ITEM-002", BarcodeFormat.CODE128, ContentType.TEXT, now - 3500),
                ScanResult("ITEM-003", BarcodeFormat.CODE128, ContentType.TEXT, now - 3400),
            ], "completed"),
            BatchJob("Product Catalog", [
                ScanResult("5901234123457", BarcodeFormat.EAN13, ContentType.PRODUCT, now - 7200),
                ScanResult("4006381333931", BarcodeFormat.EAN13, ContentType.PRODUCT, now - 7100),
            ], "completed"),
        ]

    def simulate_scan(self, content: str, fmt: BarcodeFormat = BarcodeFormat.QR_CODE) -> ScanResult:
        ct = ContentType.TEXT
        if content.startswith("http"):
            ct = ContentType.URL
        elif content.startswith("mailto:"):
            ct = ContentType.EMAIL
        elif content.startswith("tel:"):
            ct = ContentType.PHONE
        elif content.startswith("WIFI:"):
            ct = ContentType.WIFI
        elif content.startswith("geo:"):
            ct = ContentType.GEO
        result = ScanResult(content, fmt, ct, scan_mode=self._scan_mode)
        self._results.insert(0, result)
        if self._auto_copy:
            result.copied = True
        return result

    def delete_result(self, index: int) -> bool:
        if 0 <= index < len(self._results):
            self._results.pop(index)
            return True
        return False

    def clear_history(self) -> int:
        count = len(self._results)
        self._results.clear()
        return count

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
        if self._view_mode == "history":
            return self._results
        elif self._view_mode == "batch":
            return self._batches
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def results(self) -> List[ScanResult]:
        return list(self._results)

    def render_scan(self, width: int = 70) -> List[str]:
        lines = []
        cam = " 🔴 ACTIVE" if self._camera_active else ""
        lines.append(f" 📷 Barcode Scanner{cam}")
        lines.append("─" * width)
        lines.append(f" Mode: {self._scan_mode.value.title()} | Auto-copy: {'✅' if self._auto_copy else '❌'}")
        lines.append("─" * width)
        if self._camera_active:
            lines.append("")
            lines.append("  ┌────────────────────────────────────┐")
            lines.append("  │                                    │")
            lines.append("  │     📷 Camera Viewfinder           │")
            lines.append("  │                                    │")
            lines.append("  │  ┌──────────────────────────┐      │")
            lines.append("  │  │   [ Scan Area ]          │      │")
            lines.append("  │  │   Point camera at code   │      │")
            lines.append("  │  └──────────────────────────┘      │")
            lines.append("  │                                    │")
            lines.append("  └────────────────────────────────────┘")
        else:
            lines.append("")
            lines.append("  Press Space to start camera scan")
            lines.append("  Or type content to generate a barcode")
        lines.append("")
        lines.append(f" Recent: {len(self._results)} scans")
        if self._results:
            for r in self._results[:3]:
                lines.append(f"  {r.format_icon} {r.content[:50]}")
        lines.append("─" * width)
        lines.append(" Space:Start/Stop camera  H:History  B:Batches  G:Generate")
        return lines

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📜 Scan History ({len(self._results)})")
        lines.append("─" * width)
        for i, result in enumerate(self._results[:15]):
            marker = "▸" if i == self._selected_index else " "
            bookmark = " 📌" if result.bookmarked else ""
            copy = " 📋" if result.copied else ""
            lines.append(f"{marker} {result.format_icon} {result.content_icon} {result.content[:50]}{bookmark}{copy}")
            lines.append(f"   {result.barcode_format.value} | {result.confidence:.0f}% | {result.time_ago}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Delete  C:Clear all  Esc:Back")
        return lines

    def render_batch(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📦 Batch Processing ({len(self._batches)} jobs)")
        lines.append("─" * width)
        for i, batch in enumerate(self._batches):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {batch.display}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_generate(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🏭 Barcode Generator")
        lines.append("─" * width)
        lines.append(f" Format: {self._generate_format.value}")
        lines.append(f" Content: {self._generate_content or '(empty)'}")
        lines.append("")
        lines.append(" Formats:")
        for i, tmpl in enumerate(self._templates):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f" {marker} {tmpl.display}")
            lines.append(f"   {tmpl.description} | Example: {tmpl.example}")
        lines.append("─" * width)
        lines.append(" ↑↓:Select format  Enter:Generate  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"history": self.render_history, "batch": self.render_batch, "generate": self.render_generate}
        renderer = renderers.get(self._view_mode, self.render_scan)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            if key == "Escape":
                self.set_view("scan")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            if key == "Delete":
                return "delete" if self.delete_result(self._selected_index) else "delete_failed"
            return None
        if self._view_mode == "batch":
            if key == "Escape":
                self.set_view("scan")
                return "back"
            return None
        if self._view_mode == "generate":
            if key == "Escape":
                self.set_view("scan")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            return None
        if key == " ":
            self._camera_active = not self._camera_active
            return "camera_on" if self._camera_active else "camera_off"
        if key == "h":
            self.set_view("history")
            return "history"
        if key == "b":
            self.set_view("batch")
            return "batch"
        if key == "g":
            self.set_view("generate")
            return "generate"
        return None
