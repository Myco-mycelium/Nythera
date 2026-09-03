"""WYSIWYG Document Editor — Rich text, tables, and image embedding.

Features:
- Document with heading levels, paragraphs, lists, and code blocks
- Rich text formatting: bold, italic, underline, strikethrough, code, highlight
- Tables with alignment and sorting
- Image embedding with alignment and captions
- Table of contents generation
- Export to Markdown, HTML, JSON
- Document metadata and word count
- Undo/redo history
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class BlockType(Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CODE = "code"
    QUOTE = "quote"
    TABLE = "table"
    IMAGE = "image"
    DIVIDER = "divider"
    TOC = "toc"


class TextAlign(Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class ListStyle(Enum):
    BULLET = "bullet"
    NUMBERED = "numbered"
    CHECKLIST = "checklist"


@dataclass
class TextRun:
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    code: bool = False
    highlight: bool = False
    link: str = ""
    color: str = ""

    @property
    def format_tags(self) -> str:
        tags = []
        if self.bold:
            tags.append("B")
        if self.italic:
            tags.append("I")
        if self.underline:
            tags.append("U")
        if self.strikethrough:
            tags.append("S")
        if self.code:
            tags.append("<>")
        if self.highlight:
            tags.append("🟡")
        if self.link:
            tags.append("🔗")
        return "".join(tags) if tags else ""

    @property
    def preview(self) -> str:
        prefix = ""
        suffix = ""
        if self.bold:
            prefix += "**"
            suffix += "**"
        if self.italic:
            prefix += "_"
            suffix += "_"
        if self.code:
            prefix += "`"
            suffix += "`"
        return f"{prefix}{self.text}{suffix}"


@dataclass
class ImageBlock:
    url: str = ""
    alt: str = "Image"
    caption: str = ""
    width: int = 400
    height: int = 300
    align: TextAlign = TextAlign.CENTER
    border: bool = False

    @property
    def align_str(self) -> str:
        return self.align.value


@dataclass
class TableCell:
    runs: List[TextRun] = field(default_factory=list)
    align: TextAlign = TextAlign.LEFT
    header: bool = False

    @property
    def text(self) -> str:
        return " ".join(r.text for r in self.runs)


@dataclass
class TableRow:
    cells: List[TableCell] = field(default_factory=list)


@dataclass
class Block:
    block_type: BlockType
    runs: List[TextRun] = field(default_factory=list)
    level: int = 1  # heading level, list indent
    list_style: ListStyle = ListStyle.BULLET
    checked: bool = False  # for checklist
    code_language: str = ""
    table_rows: List[TableRow] = field(default_factory=list)
    image: Optional[ImageBlock] = None
    children: List['Block'] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(r.text for r in self.runs)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def type_icon(self) -> str:
        icons = {
            BlockType.HEADING: f"H{self.level}",
            BlockType.PARAGRAPH: "¶",
            BlockType.LIST: {"bullet": "•", "numbered": "1.", "checklist": "☑"}[self.list_style.value],
            BlockType.CODE: "⟨⟩",
            BlockType.QUOTE: "❝",
            BlockType.TABLE: "▦",
            BlockType.IMAGE: "🖼",
            BlockType.DIVIDER: "—",
            BlockType.TOC: "📑",
        }
        return icons.get(self.block_type, "?")


class Document:
    def __init__(self, title: str = "Untitled"):
        self.title = title
        self.author: str = ""
        self.created_at: float = time.time()
        self.modified_at: float = time.time()
        self._blocks: List[Block] = []
        self._undo_stack: List[str] = []
        self._redo_stack: List[str] = []

    @property
    def blocks(self) -> List[Block]:
        return self._blocks

    @property
    def word_count(self) -> int:
        return sum(b.word_count for b in self._blocks)

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    @property
    def heading_count(self) -> int:
        return sum(1 for b in self._blocks if b.block_type == BlockType.HEADING)

    @property
    def image_count(self) -> int:
        return sum(1 for b in self._blocks if b.block_type == BlockType.IMAGE)

    @property
    def table_count(self) -> int:
        return sum(1 for b in self._blocks if b.block_type == BlockType.TABLE)

    @property
    def toc_entries(self) -> List[Tuple[int, str]]:
        entries = []
        for b in self._blocks:
            if b.block_type == BlockType.HEADING:
                entries.append((b.level, b.text))
        return entries

    def add_block(self, block: Block, index: int = -1):
        if index < 0:
            self._blocks.append(block)
        else:
            self._blocks.insert(index, block)
        self.modified_at = time.time()

    def remove_block(self, index: int):
        if 0 <= index < len(self._blocks):
            self._blocks.pop(index)
            self.modified_at = time.time()

    def move_block(self, from_idx: int, to_idx: int):
        if 0 <= from_idx < len(self._blocks) and 0 <= to_idx < len(self._blocks):
            block = self._blocks.pop(from_idx)
            self._blocks.insert(to_idx, block)
            self.modified_at = time.time()

    def to_markdown(self) -> str:
        lines = []
        for b in self._blocks:
            if b.block_type == BlockType.HEADING:
                lines.append(f"{'#' * b.level} {b.text}")
                lines.append("")
            elif b.block_type == BlockType.PARAGRAPH:
                lines.append(b.text)
                lines.append("")
            elif b.block_type == BlockType.CODE:
                lines.append(f"```{b.code_language}")
                lines.append(b.text)
                lines.append("```")
                lines.append("")
            elif b.block_type == BlockType.QUOTE:
                lines.append(f"> {b.text}")
                lines.append("")
            elif b.block_type == BlockType.DIVIDER:
                lines.append("---")
                lines.append("")
            elif b.block_type == BlockType.IMAGE:
                if b.image:
                    lines.append(f"![{b.image.alt}]({b.image.url})")
                    if b.image.caption:
                        lines.append(f"*{b.image.caption}*")
                lines.append("")
            elif b.block_type == BlockType.TABLE:
                for i, row in enumerate(b.table_rows):
                    cells = [c.text for c in row.cells]
                    lines.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                lines.append("")
            elif b.block_type == BlockType.LIST:
                indent = "  " * (b.level - 1)
                if b.list_style == ListStyle.NUMBERED:
                    lines.append(f"{indent}1. {b.text}")
                elif b.list_style == ListStyle.CHECKLIST:
                    mark = "x" if b.checked else " "
                    lines.append(f"{indent}- [{mark}] {b.text}")
                else:
                    lines.append(f"{indent}- {b.text}")
        return "\n".join(lines)


@dataclass
class EditorCommand:
    name: str
    shortcut: str = ""
    description: str = ""
    icon: str = ""

    @property
    def display(self) -> str:
        return f"{self.icon} {self.name} ({self.shortcut})" if self.shortcut else f"{self.icon} {self.name}"


class DocEditor:
    def __init__(self):
        self._documents: List[Document] = []
        self._current_doc: int = 0
        self._selected_block: int = 0
        self._view_mode: str = "editor"  # editor, outline, preview, export
        self._format_bar: bool = True
        self._word_wrap: bool = True
        self._show_rulers: bool = False
        self._zoom: int = 100
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        doc = Document("Nyrqis OS Architecture Document")
        doc.author = "Nyrqis Team"
        doc.created_at = now - 86400 * 14
        doc.modified_at = now - 3600

        # TOC
        doc.add_block(Block(BlockType.TOC))

        # H1
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="Nyrqis OS Architecture")
        ], level=1))

        # Paragraph
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="Nyrqis"),
            TextRun(text=" is a modern operating system built from the ground up with ", italic=True),
            TextRun(text="Rust", bold=True),
            TextRun(text=" and Python. It features a custom compositor, Wayland compatibility, and a rich application ecosystem."),
        ]))

        # H2
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="System Overview")
        ], level=2))

        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="The system is organized into three main layers:"),
        ]))

        # List
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Hardware Abstraction Layer (HAL) — Direct GPU/display access")], list_style=ListStyle.BULLET))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Compositor — Custom Wayland-compatible compositor in Rust")], list_style=ListStyle.BULLET))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Shell — Rich Python UI with 160+ built-in applications")], list_style=ListStyle.BULLET))

        # H2
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="Compositor Design")
        ], level=2))

        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="The ", bold=True),
            TextRun(text="compositor", code=True),
            TextRun(text=" handles all GPU rendering through a unified pipeline:"),
        ]))

        # Code block
        doc.add_block(Block(BlockType.CODE, runs=[TextRun(text="pub fn render_frame(surface: &Surface) -> Frame {\n    let mut renderer = VulkanRenderer::new();\n    renderer.begin_frame();\n    renderer.composite_layers(surface.layers());\n    renderer.end_frame()\n}")], code_language="rust"))

        # Quote
        doc.add_block(Block(BlockType.QUOTE, runs=[
            TextRun(text="Performance target: ", italic=True),
            TextRun(text="< 1ms", bold=True),
            TextRun(text=" frame-to-frame latency at 144Hz", italic=True),
        ]))

        # H3
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="GPU Pipeline")
        ], level=3))

        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="The GPU pipeline supports ", underline=True),
            TextRun(text="Vulkan", bold=True),
            TextRun(text=", ", underline=True),
            TextRun(text="EGL/OpenGL", bold=True),
            TextRun(text=", and ", underline=True),
            TextRun(text="GBM", bold=True),
            TextRun(text=" for maximum hardware compatibility."),
        ]))

        # Table
        header = TableRow(cells=[
            TableCell(runs=[TextRun(text="Component", bold=True)], header=True, align=TextAlign.LEFT),
            TableCell(runs=[TextRun(text="Backend", bold=True)], header=True, align=TextAlign.LEFT),
            TableCell(runs=[TextRun(text="Status", bold=True)], header=True, align=TextAlign.CENTER),
            TableCell(runs=[TextRun(text="Performance", bold=True)], header=True, align=TextAlign.RIGHT),
        ])
        rows = [
            [TableCell(runs=[TextRun(text="Vulkan")]), TableCell(runs=[TextRun(text="libvulkan.so")]), TableCell(runs=[TextRun(text="✅ Stable")]), TableCell(runs=[TextRun(text="0.8ms avg")])],
            [TableCell(runs=[TextRun(text="EGL")]), TableCell(runs=[TextRun(text="libEGL.so")]), TableCell(runs=[TextRun(text="✅ Stable")]), TableCell(runs=[TextRun(text="1.2ms avg")])],
            [TableCell(runs=[TextRun(text="GBM")]), TableCell(runs=[TextRun(text="libgbm.so")]), TableCell(runs=[TextRun(text="🔄 Beta")]), TableCell(runs=[TextRun(text="1.5ms avg")])],
            [TableCell(runs=[TextRun(text="DRM")]), TableCell(runs=[TextRun(text="libdrm.so")]), TableCell(runs=[TextRun(text="✅ Stable")]), TableCell(runs=[TextRun(text="0.3ms avg")])],
        ]
        doc.add_block(Block(BlockType.TABLE, table_rows=[header] + [TableRow(cells=r) for r in rows]))

        # H2
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="Shell Applications")
        ], level=2))

        # Checklist
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="File Manager with dual panes")], list_style=ListStyle.CHECKLIST, checked=True))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Terminal Emulator with tabs")], list_style=ListStyle.CHECKLIST, checked=True))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Web Browser")], list_style=ListStyle.CHECKLIST, checked=True))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Code Editor")], list_style=ListStyle.CHECKLIST, checked=False))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Email Client")], list_style=ListStyle.CHECKLIST, checked=False))

        # H2
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="Hardware Support")
        ], level=2))

        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="Nyrqis supports ", strikethrough=True),
            TextRun(text="legacy drivers", strikethrough=True),
            TextRun(text=" modern GPU drivers through direct kernel interfaces:"),
        ]))

        # Image
        doc.add_block(Block(BlockType.IMAGE, image=ImageBlock(
            url="/docs/architecture.png",
            alt="Nyrqis Architecture Diagram",
            caption="Figure 1: High-level system architecture",
            width=640, height=480,
            align=TextAlign.CENTER,
        )))

        # H2
        doc.add_block(Block(BlockType.HEADING, runs=[
            TextRun(text="Roadmap")
        ], level=2))

        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="Version 1.0", bold=True),
            TextRun(text=" is scheduled for "),
            TextRun(text="Q3 2026", bold=True, highlight=True),
            TextRun(text=" with the following milestones:"),
        ]))

        # Numbered list
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Core compositor and shell")], list_style=ListStyle.NUMBERED))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Basic application suite")], list_style=ListStyle.NUMBERED))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Package manager")], list_style=ListStyle.NUMBERED))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Network management")], list_style=ListStyle.NUMBERED))
        doc.add_block(Block(BlockType.LIST, runs=[TextRun(text="Security hardening")], list_style=ListStyle.NUMBERED))

        # Divider
        doc.add_block(Block(BlockType.DIVIDER))

        # Footer paragraph
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="© 2026 Nyrqis Project — Built with ", italic=True),
            TextRun(text="🍄", highlight=True),
            TextRun(text=" by the Myco-mycelium community", italic=True),
        ]))

        self._documents.append(doc)

        # Second doc
        doc2 = Document("Developer Guide")
        doc2.author = "Nyrqis Team"
        doc2.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Getting Started")], level=1))
        doc2.add_block(Block(BlockType.PARAGRAPH, runs=[
            TextRun(text="This guide covers setting up the Nyrqis development environment."),
        ]))
        doc2.add_block(Block(BlockType.CODE, runs=[TextRun(text="# Clone and build\ngit clone https://github.com/Myco-mycelium/Nythera.git\ncd Nythera/Nyrqis\nmake build\nmake test")], code_language="bash"))
        doc2.add_block(Block(BlockType.QUOTE, runs=[TextRun(text="Requires Rust 1.75+ and Python 3.10+")]))
        self._documents.append(doc2)

        # Third doc
        doc3 = Document("Release Notes v2.1.0")
        doc3.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Changelog")], level=1))
        doc3.add_block(Block(BlockType.HEADING, runs=[TextRun(text="New Features")], level=2))
        doc3.add_block(Block(BlockType.LIST, runs=[TextRun(text="Added Vulkan renderer for native GPU acceleration")], list_style=ListStyle.BULLET))
        doc3.add_block(Block(BlockType.LIST, runs=[TextRun(text="New network topology mapper")], list_style=ListStyle.BULLET))
        doc3.add_block(Block(BlockType.LIST, runs=[TextRun(text="Kubernetes dashboard integration")], list_style=ListStyle.BULLET))
        doc3.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Bug Fixes")], level=2))
        doc3.add_block(Block(BlockType.LIST, runs=[TextRun(text="Fixed Wayland bridge disconnection on suspend")], list_style=ListStyle.BULLET))
        doc3.add_block(Block(BlockType.LIST, runs=[TextRun(text="Fixed memory leak in compositor frame loop")], list_style=ListStyle.BULLET))
        self._documents.append(doc3)

    @property
    def current_doc(self) -> Optional[Document]:
        if 0 <= self._current_doc < len(self._documents):
            return self._documents[self._current_doc]
        return None

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def select_document(self, idx: int):
        if 0 <= idx < len(self._documents):
            self._current_doc = idx
            self._selected_block = 0

    def select_block(self, idx: int):
        doc = self.current_doc
        if doc and 0 <= idx < len(doc.blocks):
            self._selected_block = idx

    def set_view(self, mode: str):
        if mode in ("editor", "outline", "preview", "export"):
            self._view_mode = mode

    def cycle_zoom(self):
        zooms = [75, 100, 125, 150, 200]
        idx = zooms.index(self._zoom) if self._zoom in zooms else 1
        self._zoom = zooms[(idx + 1) % len(zooms)]

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DOCUMENT EDITOR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        doc = self.current_doc
        if not doc:
            lines.append("  No document selected")
            return lines

        # Toolbar
        lines.append(f"  📄 {doc.title}  ✏️ {doc.author}  📝 {doc.word_count} words  🧱 {doc.block_count} blocks  🔤 {self._zoom}%")
        lines.append("")

        # Format bar
        if self._format_bar:
            lines.append("  B I U S ⟨⟩ 🟡 🔗 | H1 H2 H3 | • 1. ☑ | ▦ 🖼 ❝ ⟨⟩ | ← → ↔ | ↶ ↷")
            lines.append("")

        if self._view_mode == "editor":
            for i, block in enumerate(doc.blocks):
                sel = "▶" if i == self._selected_block else " "
                if block.block_type == BlockType.HEADING:
                    prefix = "#" * block.level
                    lines.append(f"  {sel} {prefix} {block.text}")
                elif block.block_type == BlockType.PARAGRAPH:
                    text = block.text[:72] + "..." if len(block.text) > 75 else block.text
                    lines.append(f"  {sel} {text}")
                elif block.block_type == BlockType.CODE:
                    lines.append(f"  {sel} ⟨{block.code_language}⟩ {block.text[:60]}...")
                elif block.block_type == BlockType.QUOTE:
                    lines.append(f"  {sel} ❝ {block.text[:68]}")
                elif block.block_type == BlockType.DIVIDER:
                    lines.append(f"  {sel} ──────────────────────────────────────────")
                elif block.block_type == BlockType.IMAGE:
                    img = block.image
                    lines.append(f"  {sel} 🖼 [{img.width}×{img.height}] {img.alt}")
                    lines.append(f"      Caption: {img.caption}  Align: {img.align_str}")
                elif block.block_type == BlockType.TABLE:
                    lines.append(f"  {sel} ▦ Table ({len(block.table_rows)} rows × {len(block.table_rows[0].cells) if block.table_rows else 0} cols)")
                    if block.table_rows:
                        for row in block.table_rows[:3]:
                            cells = [c.text[:15] for c in row.cells]
                            lines.append(f"      │ {'│ '.join(cells)} │")
                elif block.block_type == BlockType.LIST:
                    style_icons = {"bullet": "•", "numbered": "1.", "checklist": "☑" if block.checked else "☐"}
                    icon = style_icons.get(block.list_style.value, "•")
                    indent = "  " * (block.level - 1)
                    lines.append(f"  {sel} {indent}{icon} {block.text}")
                elif block.block_type == BlockType.TOC:
                    entries = doc.toc_entries
                    lines.append(f"  {sel} 📑 Table of Contents ({len(entries)} entries)")
                    for level, title in entries[:6]:
                        indent = "  " * level
                        lines.append(f"      {indent}{'·' * level} {title}")

        elif self._view_mode == "outline":
            lines.append("  ── Document Outline ──")
            for b in doc.blocks:
                if b.block_type == BlockType.HEADING:
                    indent = "  " * (b.level - 1)
                    lines.append(f"  {indent}{b.type_icon} {b.text}")
                elif b.block_type == BlockType.LIST:
                    lines.append(f"    {b.type_icon} {b.text[:50]}")

        elif self._view_mode == "preview":
            lines.append("  ── Preview ──")
            for b in doc.blocks[:15]:
                if b.block_type == BlockType.HEADING:
                    lines.append(f"  {'=' * b.level} {b.text}")
                elif b.block_type == BlockType.PARAGRAPH:
                    lines.append(f"  {b.text}")
                elif b.block_type == BlockType.CODE:
                    lines.append(f"  ┌── {b.code_language} ──┐")
                    lines.append(f"  │ {b.text[:65]}")
                    lines.append(f"  └──────┘")
                elif b.block_type == BlockType.QUOTE:
                    lines.append(f"  │ {b.text}")
                elif b.block_type == BlockType.DIVIDER:
                    lines.append(f"  {'─' * 60}")
                elif b.block_type == BlockType.IMAGE:
                    lines.append(f"  ┌{'─' * 40}┐")
                    lines.append(f"  │{'🖼':^40s}│")
                    lines.append(f"  │{(b.image.alt if b.image else ''):^40s}│")
                    lines.append(f"  └{'─' * 40}┘")

        elif self._view_mode == "export":
            lines.append("  ── Export Preview (Markdown) ──")
            md = doc.to_markdown()
            for line in md.split("\n")[:18]:
                lines.append(f"  {line}")

        lines.append("")
        lines.append("  [E]ditor [O]utline [P]review e[X]port [↑↓]Nav [Z]oom [F]ormat [N]ew")
        return lines
