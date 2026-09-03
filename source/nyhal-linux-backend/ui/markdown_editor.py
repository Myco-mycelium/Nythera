from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class HeadingLevel(Enum):
    H1 = 1
    H2 = 2
    H3 = 3
    H4 = 4
    H5 = 5
    H6 = 6


class ListType(Enum):
    UNORDERED = "unordered"
    ORDERED = "ordered"
    CHECKLIST = "checklist"


class ExportType(Enum):
    HTML = "html"
    PDF = "pdf"
    DOCX = "docx"
    RTF = "rtf"
    LATEX = "latex"
    MANPAGE = "manpage"
    ANSI = "ansi"


class BlockType(Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    CODE_BLOCK = "code_block"
    BLOCKQUOTE = "blockquote"
    LIST = "list"
    TABLE = "table"
    HR = "hr"
    IMAGE = "image"
    HTML = "html"


@dataclass
class InlineStyle:
    bold: bool = False
    italic: bool = False
    code: bool = False
    strikethrough: bool = False
    link: str = ""
    image: str = ""


@dataclass
class MarkdownBlock:
    block_type: BlockType
    content: str
    level: int = 0
    language: str = ""
    checked: bool = False
    items: list = field(default_factory=list)

    @property
    def is_heading(self) -> bool:
        return self.block_type == BlockType.HEADING

    @property
    def heading_level(self) -> HeadingLevel:
        return HeadingLevel(self.level) if 1 <= self.level <= 6 else HeadingLevel.H1


@dataclass
class DocumentStats:
    characters: int = 0
    words: int = 0
    sentences: int = 0
    paragraphs: int = 0
    headings: int = 0
    code_blocks: int = 0
    links: int = 0
    images: int = 0
    lists: int = 0
    tables: int = 0

    @property
    def reading_time_mins(self) -> float:
        return max(1, self.words / 200)

    @property
    def speaking_time_mins(self) -> float:
        return max(1, self.words / 150)


@dataclass
class MarkdownDocument:
    title: str
    filename: str
    blocks: list = field(default_factory=list)
    created_at: float = 0.0
    modified_at: float = 0.0
    is_modified: bool = False
    tags: list = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.modified_at:
            self.modified_at = time.time()

    @property
    def word_count(self) -> int:
        return sum(len(b.content.split()) for b in self.blocks if b.content)

    @property
    def preview(self) -> str:
        for b in self.blocks:
            if b.content:
                return b.content[:60]
        return ""


class MarkdownEditor:
    def __init__(self):
        self._documents: list[MarkdownDocument] = []
        self._selected_doc: int = 0
        self._cursor_line: int = 0
        self._cursor_col: int = 0
        self._show_preview: bool = True
        self._preview_mode: str = "side-by-side"
        self._word_wrap: bool = True
        self._line_numbers: bool = True
        self._syntax_highlight: bool = True
        self._spell_check: bool = False
        self._auto_save: bool = True
        self._view: str = "editor"
        self._export_format: ExportType = ExportType.HTML
        self._find_query: str = ""
        self._replace_query: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        doc1 = MarkdownDocument("Nyrqis OS Documentation", "README.md", created_at=now - 86400 * 7, modified_at=now - 3600, tags=["documentation", "os"])
        doc1.blocks = [
            MarkdownBlock(BlockType.HEADING, "Nyrqis OS", level=1),
            MarkdownBlock(BlockType.PARAGRAPH, "Nyrqis is a modern Linux-based operating system built with a custom Wayland compositor and mycelium-inspired networking."),
            MarkdownBlock(BlockType.HEADING, "Features", level=2),
            MarkdownBlock(BlockType.LIST, "", items=["Custom Wayland compositor with hardware acceleration", "Mycelium mesh networking stack", "Built-in AI assistant", "Native Rust components", "Flatpak app support"]),
            MarkdownBlock(BlockType.HEADING, "Installation", level=2),
            MarkdownBlock(BlockType.PARAGRAPH, "Download the latest ISO from the official website and follow the installation wizard."),
            MarkdownBlock(BlockType.CODE_BLOCK, "```bash\ncurl -LO https://nyrqis.dev/iso/latest\nsudo dd if=nyrqis.iso of=/dev/sdX bs=4M status=progress\n```", language="bash"),
            MarkdownBlock(BlockType.HEADING, "Architecture", level=3),
            MarkdownBlock(BlockType.TABLE, "", items=[["Component", "Technology", "Status"], ["Compositor", "Rust + Wayland", "Stable"], ["Shell", "Python + GTK4", "Beta"], ["Kernel", "Linux 6.12", "Stable"]]),
            MarkdownBlock(BlockType.BLOCKQUOTE, "Nyrqis aims to be the most user-friendly Linux distribution while maintaining full power user capabilities."),
            MarkdownBlock(BlockType.HR, "---"),
            MarkdownBlock(BlockType.PARAGRAPH, "Licensed under GPL-3.0. Contributions welcome!"),
        ]
        self._documents.append(doc1)

        doc2 = MarkdownDocument("Sprint Notes", "sprint-notes.md", created_at=now - 86400 * 3, modified_at=now - 7200, tags=["notes", "sprint"])
        doc2.blocks = [
            MarkdownBlock(BlockType.HEADING, "Sprint 14 - Week 37", level=1),
            MarkdownBlock(BlockType.HEADING, "Completed", level=2),
            MarkdownBlock(BlockType.LIST, "", items=["[x] Wayland compositor stabilization", "[x] GPU driver integration", "[x] Audio subsystem (PipeWire)", "[ ] Network manager UI"]),
            MarkdownBlock(BlockType.HEADING, "Blockers", level=2),
            MarkdownBlock(BlockType.BLOCKQUOTE, "NVIDIA driver 560 needs additional testing with the compositor. ETA: Thursday."),
            MarkdownBlock(BlockType.CODE_BLOCK, "```rust\n// Known issue in compositor\nfn handle_frame() {\n    // TODO: Fix frame pacing\n}\n```", language="rust"),
            MarkdownBlock(BlockType.HEADING, "Metrics", level=2),
            MarkdownBlock(BlockType.TABLE, "", items=[["Metric", "Target", "Actual"], ["Bug count", "< 5", "3"], ["Test coverage", "> 90%", "94%"], ["Build time", "< 2min", "1:42"]]),
        ]
        self._documents.append(doc2)

        doc3 = MarkdownDocument("API Reference", "api.md", created_at=now - 86400, modified_at=now - 1800, tags=["api", "reference"])
        doc3.blocks = [
            MarkdownBlock(BlockType.HEADING, "Nyrqis API Reference", level=1),
            MarkdownBlock(BlockType.PARAGRAPH, "This document covers the core API endpoints for the Nyrqis platform."),
            MarkdownBlock(BlockType.HEADING, "Authentication", level=2),
            MarkdownBlock(BlockType.PARAGRAPH, "All API requests require a valid API token in the Authorization header."),
            MarkdownBlock(BlockType.CODE_BLOCK, '```bash\ncurl -H "Authorization: Bearer $TOKEN" https://api.nyrqis.dev/v1/status\n```', language="bash"),
            MarkdownBlock(BlockType.HEADING, "Endpoints", level=2),
            MarkdownBlock(BlockType.TABLE, "", items=[["Method", "Endpoint", "Description"], ["GET", "/v1/status", "System status"], ["GET", "/v1/processes", "List processes"], ["POST", "/v1/power", "Power management"]]),
        ]
        self._documents.append(doc3)

    @property
    def selected_doc(self) -> Optional[MarkdownDocument]:
        if 0 <= self._selected_doc < len(self._documents):
            return self._documents[self._selected_doc]
        return None

    @property
    def total_documents(self) -> int:
        return len(self._documents)

    @property
    def total_words(self) -> int:
        return sum(d.word_count for d in self._documents)

    def select_doc(self, idx: int):
        if 0 <= idx < len(self._documents):
            self._selected_doc = idx

    def compute_stats(self) -> DocumentStats:
        doc = self.selected_doc
        if not doc:
            return DocumentStats()
        stats = DocumentStats()
        for b in doc.blocks:
            words = b.content.split() if b.content else []
            stats.words += len(words)
            stats.characters += len(b.content)
            if b.block_type == BlockType.HEADING:
                stats.headings += 1
            elif b.block_type == BlockType.CODE_BLOCK:
                stats.code_blocks += 1
            elif b.block_type == BlockType.LIST:
                stats.lists += 1
            elif b.block_type == BlockType.TABLE:
                stats.tables += 1
            elif b.block_type == BlockType.BLOCKQUOTE:
                pass
            elif b.block_type == BlockType.PARAGRAPH:
                stats.paragraphs += 1
            stats.sentences += b.content.count(".") + b.content.count("!") + b.content.count("?")
        stats.links = sum(1 for b in doc.blocks if b.content and "](http" in b.content)
        stats.images = sum(1 for b in doc.blocks if b.block_type == BlockType.IMAGE)
        return stats

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS MARKDOWN EDITOR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        doc = self.selected_doc
        if doc:
            lines.append(f"  Document: {doc.title}  File: {doc.filename}  Words: {doc.word_count}")
            lines.append(f"  Modified: {time.strftime('%Y-%m-%d %H:%M', time.localtime(doc.modified_at))}  Tags: {', '.join(doc.tags)}")
        else:
            lines.append("  No document open")
        lines.append("")
        stats = self.compute_stats()
        lines.append(f"  📊 {stats.words} words  {stats.characters} chars  {stats.sentences} sentences  {stats.paragraphs} paragraphs")
        lines.append(f"  📖 ~{stats.reading_time_mins:.0f} min read  🎤 ~{stats.speaking_time_mins:.0f} min speak")
        lines.append(f"  🔗 {stats.links} links  📷 {stats.images} images  📋 {stats.tables} tables  💻 {stats.code_blocks} code blocks")
        lines.append("")
        lines.append("  ── Documents ──")
        for i, d in enumerate(self._documents):
            sel = "▶" if i == self._selected_doc else " "
            mod = " *" if d.is_modified else ""
            lines.append(f"  {sel} {d.title}{mod}  {d.filename}  {d.word_count} words")
        lines.append("")
        lines.append("  ── Blocks ──")
        if doc:
            for b in doc.blocks[:10]:
                icons = {
                    BlockType.HEADING: "📝", BlockType.PARAGRAPH: "📄", BlockType.CODE_BLOCK: "💻",
                    BlockType.BLOCKQUOTE: "💬", BlockType.LIST: "📋", BlockType.TABLE: "📊",
                    BlockType.HR: "➖", BlockType.IMAGE: "📷", BlockType.HTML: "🌐",
                }
                icon = icons.get(b.block_type, "?")
                preview = b.content[:50] if b.content else ""
                if b.items:
                    preview = f"{len(b.items)} items"
                lines.append(f"  {icon} {b.block_type.value}: {preview}")
        lines.append("")
        lines.append("  [N]ew  [E]dit  [P]review  [F]ind  [S]tats  [X]export  [/]format")
        return lines

    def render_preview(self) -> list:
        doc = self.selected_doc
        if not doc:
            return ["  No document open"]
        lines = []
        lines.append(f"  ── Preview: {doc.title} ──")
        lines.append("")
        for b in doc.blocks:
            if b.block_type == BlockType.HEADING:
                prefix = "#" * b.level
                lines.append(f"  {prefix} {b.content}")
                lines.append("")
            elif b.block_type == BlockType.PARAGRAPH:
                lines.append(f"  {b.content}")
                lines.append("")
            elif b.block_type == BlockType.CODE_BLOCK:
                for code_line in b.content.split("\n"):
                    lines.append(f"  │ {code_line}")
                lines.append("")
            elif b.block_type == BlockType.BLOCKQUOTE:
                lines.append(f"  │ {b.content}")
                lines.append("")
            elif b.block_type == BlockType.LIST:
                for i, item in enumerate(b.items):
                    if b.items and (item.startswith("[") or item.startswith("- [")):
                        lines.append(f"  {'☑' if 'x]' in item[:5] else '☐'} {item[4:] if ']' in item[:5] else item}")
                    else:
                        lines.append(f"  • {item}")
                lines.append("")
            elif b.block_type == BlockType.TABLE:
                for row in b.items:
                    lines.append(f"  │ {'  │  '.join(row)} │")
                lines.append("")
            elif b.block_type == BlockType.HR:
                lines.append(f"  {'─' * 60}")
                lines.append("")
        return lines

    def render_stats(self) -> list:
        stats = self.compute_stats()
        lines = []
        lines.append("  ── Document Statistics ──")
        lines.append("")
        lines.append(f"  Characters:     {stats.characters}")
        lines.append(f"  Words:          {stats.words}")
        lines.append(f"  Sentences:      {stats.sentences}")
        lines.append(f"  Paragraphs:     {stats.paragraphs}")
        lines.append(f"  Headings:       {stats.headings}")
        lines.append(f"  Code Blocks:    {stats.code_blocks}")
        lines.append(f"  Lists:          {stats.lists}")
        lines.append(f"  Tables:         {stats.tables}")
        lines.append(f"  Links:          {stats.links}")
        lines.append(f"  Images:         {stats.images}")
        lines.append(f"  Reading Time:   ~{stats.reading_time_mins:.0f} min")
        lines.append(f"  Speaking Time:  ~{stats.speaking_time_mins:.0f} min")
        return lines
