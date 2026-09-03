"""QR Code Tool — Generator/decoder with batch processing and logo embedding.

Features:
- QR code generation from text/URLs/vCards/WiFi
- QR code scanning simulation
- Batch generation from list
- Error correction levels (L, M, Q, H)
- Style customization (colors, size, logo)
- History of generated codes
- Export formats (PNG, SVG, PDF)
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class QRMode(Enum):
    TEXT = "text"
    URL = "url"
    VCARD = "vcard"
    WIFI = "wifi"
    EMAIL = "email"
    PHONE = "phone"
    SMS = "sms"
    GEO = "geo"
    EVENT = "event"
    PRODUCT = "product"

    @property
    def icon(self) -> str:
        icons = {
            QRMode.TEXT: "📝", QRMode.URL: "🔗", QRMode.VCARD: "📇",
            QRMode.WIFI: "📶", QRMode.EMAIL: "📧", QRMode.PHONE: "📞",
            QRMode.SMS: "💬", QRMode.GEO: "📍", QRMode.EVENT: "📅",
            QRMode.PRODUCT: "📦",
        }
        return icons.get(self, "?")


class ErrorCorrection(Enum):
    L = "L"  # 7%
    M = "M"  # 15%
    Q = "Q"  # 25%
    H = "H"  # 30%

    @property
    def label(self) -> str:
        labels = {"L": "Low (7%)", "M": "Medium (15%)", "Q": "Quartile (25%)", "H": "High (30%)"}
        return labels.get(self.value, "")

    @property
    def icon(self) -> str:
        icons = {"L": "🟢", "M": "🟡", "Q": "🟠", "H": "🔴"}
        return icons.get(self.value, "?")


@dataclass
class QRStyle:
    fg_color: str = "#000000"
    bg_color: str = "#FFFFFF"
    size: int = 200
    margin: int = 4
    logo_enabled: bool = False
    logo_size_pct: int = 20  # percentage of QR size
    rounded_corners: bool = False
    dot_style: str = "square"  # square, rounded, circle

    @property
    def size_str(self) -> str:
        return f"{self.size}×{self.size}"


@dataclass
class QRCode:
    id: int = 0
    content: str = ""
    mode: QRMode = QRMode.TEXT
    error_correction: ErrorCorrection = ErrorCorrection.M
    style: QRStyle = field(default_factory=QRStyle)
    timestamp: float = 0.0
    name: str = ""
    tags: List[str] = field(default_factory=list)
    scan_count: int = 0
    is_favorite: bool = False
    export_format: str = "PNG"

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def preview(self) -> str:
        return self.content[:40] + "..." if len(self.content) > 40 else self.content

    @property
    def mode_icon(self) -> str:
        return self.mode.icon

    @property
    def ec_label(self) -> str:
        return f"{self.error_correction.value} {self.error_correction.label}"

    @property
    def tag_str(self) -> str:
        return " ".join(f"#{t}" for t in self.tags) if self.tags else ""


@dataclass
class QRScanResult:
    content: str = ""
    mode: QRMode = QRMode.TEXT
    timestamp: float = 0.0
    confidence: float = 0.95
    scan_method: str = "camera"

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def confidence_pct(self) -> str:
        return f"{self.confidence * 100:.0f}%"

    @property
    def mode_icon(self) -> str:
        return self.mode.icon


@dataclass
class BatchItem:
    content: str = ""
    name: str = ""
    generated: bool = False
    error: str = ""

    @property
    def status_icon(self) -> str:
        if self.error:
            return "❌"
        if self.generated:
            return "✅"
        return "⏳"


class QRTool:
    def __init__(self):
        self._codes: List[QRCode] = []
        self._scans: List[QRScanResult] = []
        self._batch_items: List[BatchItem] = []
        self._current_style = QRStyle()
        self._current_mode: QRMode = QRMode.TEXT
        self._current_ec: ErrorCorrection = ErrorCorrection.M
        self._selected_code: int = 0
        self._view_mode: str = "generate"  # generate, history, batch, scans, templates
        self._input_text: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Generated codes
        self._codes = [
            QRCode(1, "https://nyrqis.dev", QRMode.URL, ErrorCorrection.H,
                   QRStyle("#000000", "#FFFFFF", 300, 4, True, 20), now - 86400 * 7, "Nyrqis Website",
                   ["project", "main"], 42, True, "PNG"),
            QRCode(2, "https://github.com/Myco-mycelium/Nythera", QRMode.URL, ErrorCorrection.M,
                   QRStyle("#1A1A2E", "#E94560", 250, 4), now - 86400 * 5, "GitHub Repo",
                   ["project", "code"], 28, True, "SVG"),
            QRCode(3, "WIFI:T:WPA;S:Nyrqis-5G;P:myc0m3l1um;;", QRMode.WIFI, ErrorCorrection.L,
                   QRStyle("#000000", "#FFFFFF", 200, 4), now - 86400 * 3, "Office WiFi",
                   ["wifi", "office"], 15, False, "PNG"),
            QRCode(4, "BEGIN:VCARD\nVERSION:3.0\nFN:Buffy\nORG:Nyrqis\nEND:VCARD", QRMode.VCARD, ErrorCorrection.H,
                   QRStyle("#000000", "#FFFFFF", 250, 4, True, 25), now - 86400 * 2, "Contact Card",
                   ["contact", "team"], 8, False, "PNG"),
            QRCode(5, "mailto:team@nyrqis.dev?subject=Feedback", QRMode.EMAIL, ErrorCorrection.M,
                   QRStyle("#000000", "#FFFFFF", 200, 4), now - 86400, "Feedback Email",
                   ["contact"], 5, False, "PNG"),
            QRCode(6, "https://nyrqis.dev/docs/api", QRMode.URL, ErrorCorrection.M,
                   QRStyle("#000000", "#FFFFFF", 200, 4), now - 7200, "API Docs Link",
                   ["docs", "api"], 12, False, "SVG"),
            QRCode(7, "BEGIN:VEVENT\nSUMMARY:Nyrqis Meetup\nDTSTART:20260915T180000\nEND:VEVENT", QRMode.EVENT, ErrorCorrection.H,
                   QRStyle("#000000", "#FFFFFF", 300, 4, True, 15), now - 3600, "Community Meetup",
                   ["event", "community"], 3, False, "PDF"),
            QRCode(8, "geo:37.7749,-122.4194?q=Nyrqis+HQ", QRMode.GEO, ErrorCorrection.L,
                   QRStyle("#000000", "#FFFFFF", 200, 4), now - 1800, "Office Location",
                   ["location"], 2, False, "PNG"),
        ]

        # Scans
        self._scans = [
            QRScanResult("https://nyrqis.dev", QRMode.URL, now - 3600, 0.98, "camera"),
            QRScanResult("WIFI:T:WPA;S:Nyrqis-5G;P:myc0m3l1um;;", QRMode.WIFI, now - 7200, 0.95, "camera"),
            QRScanResult("BEGIN:VCARD...", QRMode.VCARD, now - 86400, 0.92, "file"),
            QRScanResult("https://github.com/Myco-mycelium/Nythera", QRMode.URL, now - 86400 * 2, 0.97, "camera"),
        ]

        # Batch items
        self._batch_items = [
            BatchItem("https://nyrqis.dev/page/1", "Page 1", True),
            BatchItem("https://nyrqis.dev/page/2", "Page 2", True),
            BatchItem("https://nyrqis.dev/page/3", "Page 3", True),
            BatchItem("https://nyrqis.dev/page/4", "Page 4", True),
            BatchItem("https://nyrqis.dev/invalid", "Invalid URL", True, "Invalid characters"),
        ]

    @property
    def total_scans(self) -> int:
        return sum(c.scan_count for c in self._codes)

    @property
    def favorite_count(self) -> int:
        return sum(1 for c in self._codes if c.is_favorite)

    def select_code(self, idx: int):
        if 0 <= idx < len(self._codes):
            self._selected_code = idx

    def set_view(self, mode: str):
        if mode in ("generate", "history", "batch", "scans", "templates"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS QR CODE TOOL                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  📱 {len(self._codes)} codes  ⭐ {self.favorite_count} favorites  📊 {self.total_scans} total scans  📦 {len(self._batch_items)} batch items")
        lines.append("")

        if self._view_mode == "generate":
            lines.append("  ── Generate QR Code ──")
            lines.append(f"  Mode: {self._current_mode.icon} {self._current_mode.value}  EC: {self._current_ec.icon} {self._current_ec.label}")
            lines.append(f"  Style: {self._current_style.size_str}  FG:{self._current_style.fg_color}  BG:{self._current_style.bg_color}  Logo:{'✓' if self._current_style.logo_enabled else '✗'}")
            lines.append(f"  Dots: {self._current_style.dot_style}  Rounded: {'✓' if self._current_style.rounded_corners else '✗'}")
            lines.append("")
            # ASCII QR preview
            lines.append("  ┌──────────────────┐")
            lines.append("  │ ██████  ████████ │")
            lines.append("  │ ██  ██  ██  ████ │")
            lines.append("  │ ██  ██████  ████ │")
            lines.append("  │ ██████  ████████ │")
            lines.append("  │    ████████      │")
            lines.append("  │ ██ ████ ██  ████ │")
            lines.append("  │ ████████  ██████ │")
            lines.append("  │    ████████      │")
            lines.append("  │ ██████  ████████ │")
            lines.append("  └──────────────────┘")

        elif self._view_mode == "history":
            lines.append("  ── Generated Codes ──")
            for i, code in enumerate(self._codes):
                sel = "▶" if i == self._selected_code else " "
                fav = "⭐" if code.is_favorite else "  "
                lines.append(f"  {sel}{fav} {code.mode_icon} {code.name:<20s} {code.time_str}  {code.ec_label}  {code.export_format}  scans:{code.scan_count}")
                lines.append(f"      {code.preview}")

        elif self._view_mode == "batch":
            lines.append("  ── Batch Processing ──")
            success = sum(1 for item in self._batch_items if item.generated and not item.error)
            failed = sum(1 for item in self._batch_items if item.error)
            lines.append(f"  📦 {len(self._batch_items)} items  ✅ {success} generated  ❌ {failed} failed")
            lines.append("")
            for item in self._batch_items:
                lines.append(f"  {item.status_icon} {item.name}: {item.content[:40]}")
                if item.error:
                    lines.append(f"      ❌ {item.error}")

        elif self._view_mode == "scans":
            lines.append("  ── Scan History ──")
            for scan in self._scans:
                lines.append(f"  {scan.mode_icon} {scan.time_str}  Confidence: {scan.confidence_pct}  Method: {scan.scan_method}")
                lines.append(f"      {scan.content[:60]}")

        elif self._view_mode == "templates":
            lines.append("  ── QR Templates ──")
            templates = [
                ("🔗 URL", QRMode.URL, "Link to a website"),
                ("📶 WiFi", QRMode.WIFI, "Share WiFi credentials"),
                ("📇 vCard", QRMode.VCARD, "Share contact info"),
                ("📧 Email", QRMode.EMAIL, "Pre-filled email"),
                ("📞 Phone", QRMode.PHONE, "Dial a number"),
                ("💬 SMS", QRMode.SMS, "Pre-filled text message"),
                ("📍 Location", QRMode.GEO, "Open map location"),
                ("📅 Event", QRMode.EVENT, "Calendar event"),
                ("📦 Product", QRMode.PRODUCT, "Product info"),
            ]
            for icon, mode, desc in templates:
                lines.append(f"  {icon} {mode.value:<10s}  {desc}")

        lines.append("")
        lines.append("  [G]enerate [H]istory [B]atch [S]cans [T]emplates [↑↓]Nav [N]ew [F]avorite")
        return lines
