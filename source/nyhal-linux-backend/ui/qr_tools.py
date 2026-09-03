"""
Nyrqis QR Tools — QR code generator and scanner utility.

Features:
- Generate QR codes from text, URLs, contacts, Wi-Fi configs
- Multiple QR types (text, URL, vCard, Wi-Fi, email, phone)
- Customizable colors and size
- ASCII art rendering of QR codes
- Scan history with timestamps
- Batch generation
- Export options (text representation)
- QR code content preview
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class QRType(Enum):
    TEXT = "Text"
    URL = "URL"
    VCARD = "vCard"
    WIFI = "Wi-Fi"
    EMAIL = "Email"
    PHONE = "Phone"
    SMS = "SMS"
    GEO = "Location"


QR_TYPE_ICONS = {
    QRType.TEXT: "📝",
    QRType.URL: "🔗",
    QRType.VCARD: "👤",
    QRType.WIFI: "📶",
    QRType.EMAIL: "📧",
    QRType.PHONE: "📞",
    QRType.SMS: "💬",
    QRType.GEO: "📍",
}


@dataclass
class QRCode:
    """A generated QR code."""
    content: str
    qr_type: QRType = QRType.TEXT
    title: str = ""
    fg_color: str = "#000000"
    bg_color: str = "#FFFFFF"
    size: int = 21  # Module count (21=small, 25=medium, 29=large)
    error_correction: str = "M"  # L, M, Q, H
    created: float = field(default_factory=time.time)
    qr_id: str = ""

    def __post_init__(self):
        if not self.qr_id:
            self.qr_id = hashlib.md5(f"{self.content}{self.created}".encode()).hexdigest()[:8]

    @property
    def display_title(self) -> str:
        icon = QR_TYPE_ICONS.get(self.qr_type, "❓")
        title = self.title or self.content[:30]
        return f"{icon} {title}"

    @property
    def preview(self) -> str:
        return self.content[:60] + "..." if len(self.content) > 60 else self.content

    @property
    def size_label(self) -> str:
        if self.size <= 21:
            return "Small (21×21)"
        elif self.size <= 25:
            return "Medium (25×25)"
        return "Large (29×29)"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")


@dataclass
class QRScanResult:
    """A scanned QR code result."""
    content: str
    qr_type: QRType = QRType.TEXT
    timestamp: float = field(default_factory=time.time)
    scan_id: str = ""

    def __post_init__(self):
        if not self.scan_id:
            self.scan_id = hashlib.md5(f"{self.content}{self.timestamp}".encode()).hexdigest()[:8]

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


# ─── QR Generator ────────────────────────────────────────────────────────


class QRGenerator:
    """Generate ASCII art QR codes."""

    @staticmethod
    def generate_ascii(text: str, size: int = 21) -> List[str]:
        """Generate a simple ASCII art QR-like pattern from text."""
        # Create a deterministic pattern from the text
        seed = hashlib.md5(text.encode()).digest()
        modules = []

        # Generate pattern
        for row in range(size):
            line = ""
            for col in range(size):
                # Finder patterns (corners)
                if (row < 7 and col < 7) or (row < 7 and col >= size - 7) or (row >= size - 7 and col < 7):
                    # Finder pattern
                    r = row if row < 7 else row - (size - 7)
                    c = col if col < 7 else col - (size - 7)
                    if (r == 0 or r == 6 or c == 0 or c == 6 or
                            (2 <= r <= 4 and 2 <= c <= 4)):
                        line += "██"
                    else:
                        line += "  "
                else:
                    # Data area - use seed for deterministic pattern
                    idx = (row * size + col) % len(seed)
                    bit = (seed[idx] >> (col % 8)) & 1
                    if bit:
                        line += "██"
                    else:
                        line += "  "
            modules.append(line)

        return modules

    @staticmethod
    def generate_compact(text: str, width: int = 20) -> List[str]:
        """Generate a compact QR representation using Unicode blocks."""
        seed = hashlib.md5(text.encode()).digest()
        lines = []
        for row in range(width // 2):
            line = ""
            for col in range(width):
                idx = (row * width + col) % len(seed)
                bit = (seed[idx] >> (col % 8)) & 1
                line += "█" if bit else " "
            lines.append(line)
        return lines


# ─── QR Tools App ────────────────────────────────────────────────────────


class QRTools:
    """
    QR code generator and scanner for Nyrqis OS.
    """

    def __init__(self):
        self._generated: List[QRCode] = []
        self._scan_history: List[QRScanResult] = []
        self._current_qr: Optional[QRCode] = None
        self._qr_preview: List[str] = []

        # Generator state
        self._qr_type: QRType = QRType.TEXT
        self._input_text: str = ""
        self._title: str = ""
        self._fg_color: str = "#000000"
        self._bg_color: str = "#FFFFFF"
        self._qr_size: int = 21
        self._error_correction: str = "M"

        # Wi-Fi specific
        self._wifi_ssid: str = ""
        self._wifi_password: str = ""
        self._wifi_security: str = "WPA2"

        # Contact specific
        self._contact_name: str = ""
        self._contact_phone: str = ""
        self._contact_email: str = ""

        # View state
        self._view_mode: str = "generator"  # generator, preview, history, scan
        self._selected_index: int = 0

        # QR type templates
        self._type_fields = {
            QRType.TEXT: ["Content"],
            QRType.URL: ["URL"],
            QRType.VCARD: ["Name", "Phone", "Email"],
            QRType.WIFI: ["SSID", "Password", "Security"],
            QRType.EMAIL: ["Email", "Subject", "Body"],
            QRType.PHONE: ["Phone Number"],
            QRType.SMS: ["Phone", "Message"],
            QRType.GEO: ["Latitude", "Longitude"],
        }

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._generated = [
            QRCode("https://github.com/Myco-mycelium/Nythera", QRType.URL, "Nyrqis GitHub", created=now - 3600),
            QRCode("WIFI:T:WPA2;S:NyrqisHome;P:myc3l1um;;", QRType.WIFI, "Home Wi-Fi",
                   created=now - 7200),
            QRCode("BEGIN:VCARD\nVERSION:3.0\nFN:User\nTEL:+1234567890\nEMAIL:user@nyrqis.os\nEND:VCARD",
                   QRType.VCARD, "My Contact", created=now - 86400),
            QRCode("https://nyrqis.os/docs", QRType.URL, "Nyrqis Docs", created=now - 172800),
            QRCode("Hello, this is a test QR code for Nyrqis OS!", QRType.TEXT, "Test Message",
                   created=now - 259200),
        ]

        self._scan_history = [
            QRScanResult("https://example.com", QRType.URL, now - 300),
            QRScanResult("WIFI:T:WPA;S:CoffeeShop;P:welcome123;;", QRType.WIFI, now - 3600),
            QRScanResult("tel:+15551234567", QRType.PHONE, now - 7200),
        ]

    # ── Generation ────────────────────────────────────────────────────

    def generate(self) -> Optional[QRCode]:
        """Generate a QR code from current input."""
        content = self._build_content()
        if not content:
            return None

        qr = QRCode(
            content=content,
            qr_type=self._qr_type,
            title=self._title,
            fg_color=self._fg_color,
            bg_color=self._bg_color,
            size=self._qr_size,
            error_correction=self._error_correction,
        )
        self._generated.insert(0, qr)
        self._current_qr = qr
        self._qr_preview = QRGenerator.generate_compact(content, qr.size)
        self._view_mode = "preview"
        return qr

    def _build_content(self) -> str:
        """Build content string based on QR type."""
        if self._qr_type == QRType.TEXT:
            return self._input_text
        elif self._qr_type == QRType.URL:
            url = self._input_text
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            return url
        elif self._qr_type == QRType.WIFI:
            return f"WIFI:T:{self._wifi_security};S:{self._wifi_ssid};P:{self._wifi_password};;"
        elif self._qr_type == QRType.VCARD:
            return f"BEGIN:VCARD\nVERSION:3.0\nFN:{self._contact_name}\nTEL:{self._contact_phone}\nEMAIL:{self._contact_email}\nEND:VCARD"
        elif self._qr_type == QRType.EMAIL:
            return f"mailto:{self._input_text}"
        elif self._qr_type == QRType.PHONE:
            return f"tel:{self._input_text}"
        elif self._qr_type == QRType.SMS:
            return f"smsto:{self._input_text}"
        elif self._qr_type == QRType.GEO:
            return f"geo:{self._input_text}"
        return self._input_text

    def delete_qr(self, index: int) -> bool:
        if 0 <= index < len(self._generated):
            self._generated.pop(index)
            return True
        return False

    # ── Scanning (Simulated) ──────────────────────────────────────────

    def simulate_scan(self, content: str, qr_type: QRType = QRType.TEXT) -> QRScanResult:
        result = QRScanResult(content=content, qr_type=qr_type)
        self._scan_history.insert(0, result)
        return result

    def clear_scan_history(self) -> int:
        count = len(self._scan_history)
        self._scan_history.clear()
        return count

    # ── Properties ────────────────────────────────────────────────────

    @property
    def generated(self) -> List[QRCode]:
        return list(self._generated)

    @property
    def scan_history(self) -> List[QRScanResult]:
        return list(self._scan_history)

    @property
    def current_qr(self) -> Optional[QRCode]:
        return self._current_qr

    @property
    def qr_preview(self) -> List[str]:
        return list(self._qr_preview)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def qr_type(self) -> QRType:
        return self._qr_type

    @property
    def qr_size(self) -> int:
        return self._qr_size

    @property
    def size_label(self) -> str:
        if self._qr_size <= 21:
            return "Small (21×21)"
        elif self._qr_size <= 25:
            return "Medium (25×25)"
        return "Large (29×29)"

    # ── Rendering ─────────────────────────────────────────────────────

    def render_generator(self, width: int = 60) -> List[str]:
        lines = []
        icon = QR_TYPE_ICONS.get(self._qr_type, "❓")
        lines.append(f" {icon} QR Code Generator — {self._qr_type.value}")
        lines.append("─" * width)

        # Type selector
        types = list(QRType)
        type_str = " ".join(
            f"[{'▸' if t == self._qr_type else ' '}] {t.value}"
            for t in types[:4]
        )
        lines.append(f" Type: {type_str}")

        lines.append("─" * width)

        # Input fields based on type
        if self._qr_type == QRType.TEXT:
            lines.append(f" Content: {self._input_text[:width - 12]}")
        elif self._qr_type == QRType.URL:
            lines.append(f" URL: {self._input_text[:width - 8]}")
        elif self._qr_type == QRType.WIFI:
            lines.append(f" SSID:     {self._wifi_ssid}")
            lines.append(f" Password: {'•' * len(self._wifi_password) if self._wifi_password else '(empty)'}")
            lines.append(f" Security: {self._wifi_security}")
        elif self._qr_type == QRType.VCARD:
            lines.append(f" Name:  {self._contact_name}")
            lines.append(f" Phone: {self._contact_phone}")
            lines.append(f" Email: {self._contact_email}")

        lines.append("")
        lines.append(f" Title:      {self._title or '(optional)'}")
        lines.append(f" Size:       {self.size_label}")
        lines.append(f" Error Corr: {self._error_correction}")
        lines.append(f" Colors:     FG:{self._fg_color} BG:{self._bg_color}")

        lines.append("─" * width)
        lines.append(" T:Type  Enter:Generate  H:History  ←→:Navigate")
        return lines

    def render_preview(self, width: int = 60) -> List[str]:
        lines = []
        if not self._current_qr:
            return ["No QR code generated"]

        qr = self._current_qr
        lines.append(f" {qr.display_title}")
        lines.append("─" * width)

        # QR code preview
        lines.append(" ┌" + "─" * (width - 4) + "┐")
        for row in self._qr_preview:
            padded = row.center(width - 4)
            lines.append(f" │{padded}│")
        lines.append(" └" + "─" * (width - 4) + "┘")

        lines.append("")
        lines.append(f" Content: {qr.preview}")
        lines.append(f" Size: {qr.size_label}  EC: {qr.error_correction}")

        lines.append("─" * width)
        lines.append(" Esc:Back  D:Delete  C:Copy  N:New")
        return lines

    def render_history(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" 📜 QR History ({len(self._generated)} generated, {len(self._scan_history)} scanned)")
        lines.append("─" * width)

        # Generated
        if self._generated:
            lines.append(" Generated:")
            for i, qr in enumerate(self._generated[:5]):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f" {marker} {qr.display_title}")
                lines.append(f"   {qr.time_ago} · {qr.size_label}")
                lines.append("")

        # Scanned
        if self._scan_history:
            lines.append(" Scanned:")
            for scan in self._scan_history[:5]:
                icon = QR_TYPE_ICONS.get(scan.qr_type, "❓")
                lines.append(f" {icon} {scan.content[:width - 5]}")
                lines.append(f"   {scan.time_ago}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:View  Del:Delete  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "preview": self.render_preview,
            "history": self.render_history,
        }
        renderer = renderers.get(self._view_mode, self.render_generator)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "preview":
            return self._handle_preview_key(key)
        elif self._view_mode == "history":
            return self._handle_history_key(key)
        return self._handle_generator_key(key)

    def _handle_generator_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.generate()
            return "generate"
        elif key == "t":
            types = list(QRType)
            idx = types.index(self._qr_type)
            self._qr_type = types[(idx + 1) % len(types)]
            return "cycle_type"
        elif key == "h":
            self._view_mode = "history"
            self._selected_index = 0
            return "history"
        elif key == "+":
            self._qr_size = min(29, self._qr_size + 4)
            return "increase_size"
        elif key == "-":
            self._qr_size = max(21, self._qr_size - 4)
            return "decrease_size"
        return None

    def _handle_preview_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "generator"
            return "back"
        elif key == "n":
            self._view_mode = "generator"
            return "new_qr"
        elif key == "d":
            if self._current_qr:
                for i, qr in enumerate(self._generated):
                    if qr.qr_id == self._current_qr.qr_id:
                        self.delete_qr(i)
                        break
                self._current_qr = None
                self._view_mode = "generator"
            return "delete"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "generator"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._generated) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            if 0 <= self._selected_index < len(self._generated):
                self._current_qr = self._generated[self._selected_index]
                self._qr_preview = QRGenerator.generate_compact(
                    self._current_qr.content, self._current_qr.size)
                self._view_mode = "preview"
            return "view_qr"
        return None
