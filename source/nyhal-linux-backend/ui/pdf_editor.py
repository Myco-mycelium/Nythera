"""PDF Editor — annotation, text extraction, form filling for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class AnnotationType(Enum):
    HIGHLIGHT = "Highlight"
    UNDERLINE = "Underline"
    STRIKETHROUGH = "Strikethrough"
    FREEHAND = "Freehand"
    TEXT_BOX = "Text Box"
    CALLOUT = "Callout"
    STAMP = "Stamp"
    SIGNATURE = "Signature"
    ARROW = "Arrow"
    RECTANGLE = "Rectangle"
    CIRCLE = "Circle"
    NOTE = "Note"
    LINK = "Link"
    IMAGE = "Image"
    REDACT = "Redact"


class StampType(Enum):
    APPROVED = "Approved ✅"
    REJECTED = "Rejected ❌"
    DRAFT = "Draft 📝"
    CONFIDENTIAL = "Confidential 🔒"
    FINAL = "Final 📌"
    REVISION = "Revision 🔄"


class FormFieldType(Enum):
    TEXT = "Text"
    TEXTAREA = "TextArea"
    CHECKBOX = "CheckBox"
    RADIO = "Radio"
    DROPDOWN = "DropDown"
    SIGNATURE = "Signature"
    DATE = "Date"
    NUMBER = "Number"
    EMAIL = "Email"
    PHONE = "Phone"


class Permission(Enum):
    PRINT = "Print"
    COPY = "Copy"
    EDIT = "Edit"
    ANNOTATE = "Annotate"
    FORM_FILL = "Form Fill"


class ExportFormat(Enum):
    PDF = "PDF"
    PDF_A = "PDF/A-1b"
    PDF_A2 = "PDF/A-2b"
    TEXT = "Text"
    IMAGES = "Images"
    HTML = "HTML"


@dataclass
class PageText:
    page_num: int
    text: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    font: str = ""
    font_size: float = 12.0
    line_height: float = 14.0

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Annotation:
    id: int
    page_num: int
    annotation_type: AnnotationType = AnnotationType.HIGHLIGHT
    x: float = 0.0
    y: float = 0.0
    width: float = 100.0
    height: float = 20.0
    text: str = ""
    color: str = "#ffff00"
    opacity: float = 0.5
    author: str = ""
    created: float = 0.0
    modified: float = 0.0
    visible: bool = True
    locked: bool = False

    @property
    def type_icon(self) -> str:
        icons = {
            AnnotationType.HIGHLIGHT: "🟡",
            AnnotationType.UNDERLINE: "〰",
            AnnotationType.STRIKETHROUGH: "🗑",
            AnnotationType.FREEHAND: "✏️",
            AnnotationType.TEXT_BOX: "📝",
            AnnotationType.CALLOUT: "💬",
            AnnotationType.STAMP: "📮",
            AnnotationType.SIGNATURE: "✍️",
            AnnotationType.ARROW: "➡",
            AnnotationType.RECTANGLE: "▭",
            AnnotationType.CIRCLE: "⬭",
            AnnotationType.NOTE: "📌",
            AnnotationType.LINK: "🔗",
            AnnotationType.IMAGE: "🖼",
            AnnotationType.REDACT: "⬛",
        }
        return icons.get(self.annotation_type, "?")


@dataclass
class FormField:
    name: str
    field_type: FormFieldType = FormFieldType.TEXT
    value: str = ""
    page_num: int = 1
    x: float = 0.0
    y: float = 0.0
    width: float = 200.0
    height: float = 24.0
    required: bool = False
    readonly: bool = False
    options: List[str] = field(default_factory=list)
    default_value: str = ""
    max_length: int = 0
    tooltip: str = ""

    @property
    def filled(self) -> bool:
        return bool(self.value)

    @property
    def status_icon(self) -> str:
        if self.required and not self.filled:
            return "⚠️"
        if self.filled:
            return "✅"
        return "⬜"


@dataclass
class Bookmark:
    title: str
    page_num: int = 1
    level: int = 0
    children: List["Bookmark"] = field(default_factory=list)


@dataclass
class Page:
    page_num: int
    width: float = 612.0  # US Letter
    height: float = 792.0
    rotation: int = 0
    text_blocks: List[PageText] = field(default_factory=list)
    thumbnail: str = ""

    @property
    def size_str(self) -> str:
        return f"{self.width:.0f}x{self.height:.0f}"


class PDFEditor:
    def __init__(self):
        self._filename: str = "document.pdf"
        self._pages: List[Page] = []
        self._annotations: List[Annotation] = []
        self._form_fields: List[FormField] = []
        self._bookmarks: List[Bookmark] = []
        self._selected_page: int = 0
        self._selected_annotation: int = -1
        self._current_tool: AnnotationType = AnnotationType.HIGHLIGHT
        self._view_mode: str = "pages"
        self._zoom: float = 1.0
        self._permissions: List[Permission] = list(Permission)
        self._metadata: Dict[str, str] = {}
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        self._metadata = {
            "Title": "Nyrqis OS Architecture Document",
            "Author": "Nyrqis Development Team",
            "Subject": "System Architecture and Design Decisions",
            "Creator": "Nyrqis PDF Engine",
            "Producer": "Nyrqis OS v1.4",
            "Created": "2026-01-15",
            "Modified": "2026-09-01",
            "Keywords": "nyrqis,linux,wayland,compositor,architecture",
        }

        self._pages = [
            Page(1, 612, 792, 0, [
                PageText(1, "Nyrqis OS Architecture Document", 72, 72, 468, 24, "Helvetica-Bold", 24),
                PageText(1, "Version 1.4 — September 2026", 72, 108, 468, 14, "Helvetica", 12),
                PageText(1, "1. Overview", 72, 160, 468, 18, "Helvetica-Bold", 18),
                PageText(1, "Nyrqis is a Linux-based operating system designed for creative professionals. It features a custom Wayland compositor built in Rust, a Python-based shell framework, and a modular application ecosystem.", 72, 190, 468, 84, "Helvetica", 12),
                PageText(1, "2. System Components", 72, 290, 468, 18, "Helvetica-Bold", 18),
                PageText(1, "The system is composed of several key components: the Rust compositor (nyrqis-compositor), the HAL layer (nyhal), the Python shell framework, and the application toolkit.", 72, 320, 468, 56, "Helvetica", 12),
            ], "📄"),
            Page(2, 612, 792, 0, [
                PageText(2, "3. Compositor Architecture", 72, 72, 468, 18, "Helvetica-Bold", 18),
                PageText(2, "The compositor handles display output, input routing, and window management. It implements the following Wayland protocols: wl_compositor, wl_shm, xdg_wm_base, wl_seat, wl_output.", 72, 100, 468, 56, "Helvetica", 12),
                PageText(2, "4. Security Model", 72, 172, 468, 18, "Helvetica-Bold", 18),
                PageText(2, "Nyrqis implements a capability-based security model with sandboxed applications, mandatory access control, and encrypted storage by default.", 72, 200, 468, 42, "Helvetica", 12),
            ], "📄"),
            Page(3, 612, 792, 0, [
                PageText(3, "5. Application Framework", 72, 72, 468, 18, "Helvetica-Bold", 18),
                PageText(3, "Applications are built using the NUI schema with component-based architecture. The shell provides runtime semantics for state management, event handling, and data binding.", 72, 100, 468, 56, "Helvetica", 12),
                PageText(3, "6. Future Roadmap", 72, 172, 468, 18, "Helvetica-Bold", 18),
                PageText(3, "Planned features include: hardware-accelerated rendering via Vulkan, mobile device support, real-time collaboration, and AI-assisted development tools.", 72, 200, 468, 42, "Helvetica", 12),
            ], "📄"),
        ]

        # Annotations
        self._annotations = [
            Annotation(1, 1, AnnotationType.HIGHLIGHT, 72, 190, 468, 16,
                       "Nyrqis is a Linux-based operating system", "#ffff00", 0.3,
                       "Alice", time.time() - 86400),
            Annotation(2, 1, AnnotationType.NOTE, 500, 200, 24, 24,
                       "Need to update this section with latest features",
                       "#4a9eff", 1.0, "Bob", time.time() - 3600),
            Annotation(3, 2, AnnotationType.STAMP, 400, 100, 120, 40,
                       "Approved ✅", "#00aa00", 1.0, "Manager",
                       time.time() - 1800),
            Annotation(4, 2, AnnotationType.UNDERLINE, 72, 100, 468, 14,
                       "capability-based security model", "#ff0000", 0.8,
                       "Alice", time.time() - 900),
            Annotation(5, 3, AnnotationType.TEXT_BOX, 72, 250, 300, 60,
                       "TODO: Add diagram for application framework architecture",
                       "#ff6b6b", 1.0, "Developer", time.time() - 300),
        ]

        # Form fields
        self._form_fields = [
            FormField("doc_title", FormFieldType.TEXT, "Nyrqis OS Architecture", 1, 72, 380, 300, 24,
                      required=True, tooltip="Document title"),
            FormField("revision", FormFieldType.DROPDOWN, "1.4", 1, 400, 380, 100, 24,
                      options=["1.0", "1.1", "1.2", "1.3", "1.4"]),
            FormField("approved", FormFieldType.CHECKBOX, "true", 1, 72, 420, 24, 24),
            FormField("reviewer_name", FormFieldType.TEXT, "", 1, 72, 460, 200, 24,
                      required=True, tooltip="Enter reviewer name"),
            FormField("review_date", FormFieldType.DATE, "2026-09-01", 1, 300, 460, 150, 24),
            FormField("confidential", FormFieldType.RADIO, "internal", 1, 72, 500, 200, 24,
                      options=["public", "internal", "confidential", "secret"]),
            FormField("comments", FormFieldType.TEXTAREA, "Architecture looks solid. Ready for review.", 2, 72, 300, 468, 80),
        ]

        # Bookmarks
        self._bookmarks = [
            Bookmark("1. Overview", 1),
            Bookmark("2. System Components", 1),
            Bookmark("3. Compositor Architecture", 2),
            Bookmark("4. Security Model", 2),
            Bookmark("5. Application Framework", 3),
            Bookmark("6. Future Roadmap", 3),
        ]

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def selected_page(self) -> Optional[Page]:
        if 0 <= self._selected_page < len(self._pages):
            return self._pages[self._selected_page]
        return None

    @property
    def total_pages(self) -> int:
        return len(self._pages)

    @property
    def total_annotations(self) -> int:
        return len(self._annotations)

    @property
    def total_form_fields(self) -> int:
        return len(self._form_fields)

    @property
    def filled_fields(self) -> int:
        return sum(1 for f in self._form_fields if f.filled)

    def select_page(self, idx: int):
        if 0 <= idx < len(self._pages):
            self._selected_page = idx

    def add_annotation(self, page: int, anno_type: AnnotationType, x: float, y: float, text: str = ""):
        anno_id = max(a.id for a in self._annotations) + 1 if self._annotations else 1
        anno = Annotation(anno_id, page, anno_type, x, y, text=text,
                          created=time.time(), author="User")
        self._annotations.append(anno)
        self._history.append(f"Added {anno_type.value} on page {page}")

    def delete_annotation(self, idx: int):
        if 0 <= idx < len(self._annotations):
            self._annotations.pop(idx)
            self._history.append("Deleted annotation")

    def fill_form_field(self, name: str, value: str):
        for f in self._form_fields:
            if f.name == name:
                f.value = value
                self._history.append(f"Filled field: {name}")
                return

    def extract_text(self, page_num: int) -> str:
        page = self._pages[page_num - 1] if 0 < page_num <= len(self._pages) else None
        if page:
            return "\n".join(tb.text for tb in page.text_blocks)
        return ""

    def handle_input(self, key: str):
        key = key.lower()
        if key == "h":
            self._current_tool = AnnotationType.HIGHLIGHT
        elif key == "n":
            self._current_tool = AnnotationType.NOTE
        elif key == "t":
            self._current_tool = AnnotationType.TEXT_BOX
        elif key == "s":
            self._current_tool = AnnotationType.STAMP
        elif key == "r":
            self._current_tool = AnnotationType.RECTANGLE
        elif key == "d":
            self._current_tool = AnnotationType.REDACT

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS PDF EDITOR                                         ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Info
        meta = self._metadata
        lines.append(f"  📄 {meta.get('Title', self._filename)}  Pages: {self.total_pages}  Annotations: {self.total_annotations}  Forms: {self.filled_fields}/{self.total_form_fields}")
        lines.append(f"  Author: {meta.get('Author', 'Unknown')}  Modified: {meta.get('Modified', 'Unknown')}  Zoom: {self._zoom:.0%}  Tool: {self._current_tool.value}")
        lines.append("")

        # Page list
        lines.append("  ── Pages ──")
        for i, page in enumerate(self._pages):
            sel = "▶" if i == self._selected_page else " "
            annos = sum(1 for a in self._annotations if a.page_num == page.page_num)
            forms = sum(1 for f in self._form_fields if f.page_num == page.page_num)
            lines.append(f"  {sel} {page.thumbnail} Page {page.page_num}  {page.size_str}  {annos} annotations  {forms} form fields")
        lines.append("")

        # Selected page content
        page = self.selected_page
        if page:
            lines.append(f"  ── Page {page.page_num} Content ──")
            for tb in page.text_blocks[:6]:
                text = tb.text[:65]
                lines.append(f"  │ [{tb.font} {tb.font_size:.0f}pt] {text}")
            lines.append("")

        # Annotations on current page
        page_annos = [a for a in self._annotations if page and a.page_num == page.page_num]
        if page_annos:
            lines.append("  ── Annotations ──")
            for a in page_annos:
                preview = a.text[:40] if a.text else ""
                lines.append(f"  {a.type_icon} {a.annotation_type.value}  ({a.x:.0f},{a.y:.0f}) {preview}  by {a.author}")
            lines.append("")

        # Form fields
        if self._form_fields:
            lines.append("  ── Form Fields ──")
            for f in self._form_fields:
                val = f.value[:30] if f.value else "(empty)"
                lines.append(f"  {f.status_icon} {f.name:<20s} {f.field_type.value:<12s} {val}")
            lines.append("")

        # Bookmarks
        if self._bookmarks:
            lines.append("  ── Bookmarks ──")
            for bm in self._bookmarks:
                lines.append(f"  📑 {bm.title}  (page {bm.page_num})")
            lines.append("")

        lines.append("  [H]ighlight [N]ote [T]ext Box [S]tamp [R]ect [D]Redact")
        lines.append("  [↑↓]Page [Ctrl+E]Export [Ctrl+S]Save [Ctrl+F]Find")
        return lines
