"""Markdown Wiki — backlinks, table of contents, search for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set
import time
import re


class PageStatus(Enum):
    DRAFT = "Draft"
    PUBLISHED = "Published"
    ARCHIVED = "Archived"
    ORPHANED = "Orphaned"


class LinkType(Enum):
    INTERNAL = "Internal"
    EXTERNAL = "External"
    ANCHOR = "Anchor"
    IMAGE = "Image"
    FILE = "File"


@dataclass
class WikiLink:
    target: str = ""
    text: str = ""
    link_type: LinkType = LinkType.INTERNAL
    valid: bool = True

    @property
    def icon(self) -> str:
        icons = {
            LinkType.INTERNAL: "📄", LinkType.EXTERNAL: "🌐",
            LinkType.ANCHOR: "#", LinkType.IMAGE: "🖼", LinkType.FILE: "📎",
        }
        return icons.get(self.link_type, "?")


@dataclass
class TOCEntry:
    level: int = 1
    title: str = ""
    anchor: str = ""
    page: str = ""

    @property
    def indent(self) -> str:
        return "  " * (self.level - 1)

    @property
    def bullet(self) -> str:
        return "•" if self.level == 1 else "◦" if self.level == 2 else "▪"


@dataclass
class WikiPage:
    title: str = ""
    slug: str = ""
    content: str = ""
    raw_markdown: str = ""
    status: PageStatus = PageStatus.DRAFT
    created: float = 0.0
    modified: float = 0.0
    author: str = ""
    tags: List[str] = field(default_factory=list)
    links: List[WikiLink] = field(default_factory=list)
    backlinks: List[str] = field(default_factory=list)
    toc: List[TOCEntry] = field(default_factory=list)
    word_count: int = 0
    read_time_min: int = 0
    revision: int = 1

    @property
    def status_icon(self) -> str:
        icons = {
            PageStatus.DRAFT: "📝", PageStatus.PUBLISHED: "✅",
            PageStatus.ARCHIVED: "📦", PageStatus.ORPHANED: "🔗",
        }
        return icons.get(self.status, "?")

    @property
    def tag_str(self) -> str:
        return " ".join(f"[{t}]" for t in self.tags) if self.tags else ""

    @property
    def preview(self) -> str:
        lines = self.raw_markdown.split("\n")
        for line in lines:
            clean = line.strip().lstrip("#").strip()
            if clean and not clean.startswith("```"):
                return clean[:60]
        return ""

    def extract_toc(self):
        self.toc = []
        for line in self.raw_markdown.split("\n"):
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                anchor = re.sub(r'[^\w\s-]', '', title.lower()).replace(' ', '-')
                self.toc.append(TOCEntry(level, title, anchor, self.title))

    def extract_links(self):
        self.links = []
        for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', self.raw_markdown):
            text, target = m.group(1), m.group(2)
            if target.startswith("#"):
                self.links.append(WikiLink(target, text, LinkType.ANCHOR))
            elif target.startswith("http"):
                self.links.append(WikiLink(target, text, LinkType.EXTERNAL))
            elif target.endswith((".png", ".jpg", ".gif", ".svg")):
                self.links.append(WikiLink(target, text, LinkType.IMAGE))
            else:
                self.links.append(WikiLink(target, text, LinkType.INTERNAL))

    def extract_words(self):
        clean = re.sub(r'#{1,6}\s+', '', self.raw_markdown)
        clean = re.sub(r'[*_`\[\]()]', '', clean)
        self.word_count = len(clean.split())
        self.read_time_min = max(1, self.word_count // 200)


@dataclass
class SearchMatch:
    page: str
    line_num: int
    line: str
    score: float = 1.0

    @property
    def preview(self) -> str:
        return self.line[:70]


@dataclass
class WikiStats:
    total_pages: int = 0
    published: int = 0
    drafts: int = 0
    total_words: int = 0
    total_links: int = 0
    broken_links: int = 0
    orphan_pages: int = 0
    avg_read_time: float = 0.0


class MarkdownWiki:
    def __init__(self):
        self._pages: List[WikiPage] = []
        self._selected_page: int = 0
        self._search_results: List[SearchMatch] = []
        self._search_query: str = ""
        self._view_mode: str = "pages"
        self._show_toc: bool = True
        self._show_backlinks: bool = True
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._pages = [
            WikiPage("Nyrqis OS Architecture", "architecture", status=PageStatus.PUBLISHED,
                     author="architect", tags=["architecture", "design", "system"],
                     created=now - 86400 * 30, modified=now - 3600, word_count=1200, read_time_min=6,
                     raw_markdown="""# Nyrqis OS Architecture

## Overview

Nyrqis is a Linux-based operating system designed for creative professionals.

## System Components

### Compositor
The [Wayland compositor](compositor) is built in Rust for performance.
It implements [wl_compositor](protocols) and xdg_wm_base protocols.

### HAL Layer
The [Hardware Abstraction Layer](hal) provides unified APIs for
[GPU access](gpu), input devices, and display management.

### Shell Framework
The [Python shell](shell) uses [NUI Schema](nui-schema) for
component-based UI design with state management.

## Security Model

Nyrqis implements capability-based security with sandboxed applications.
See [Security Documentation](security) for details.

## References

- [Wayland Protocol](https://wayland.freedesktop.org)
- [DRM/KMS](https://docs.kernel.org/gpu/drm-internals.html)
""",
                     backlinks=["compositor", "hal", "shell", "gpu"]),
            WikiPage("Wayland Compositor", "compositor", status=PageStatus.PUBLISHED,
                     author="compositor-dev", tags=["compositor", "wayland", "rust"],
                     created=now - 86400 * 20, modified=now - 7200, word_count=800, read_time_min=4,
                     raw_markdown="""# Wayland Compositor

## Overview

The Nyrqis compositor handles display output and input routing.

## Architecture

### Surface Management
Each client gets a [surface](surface-management) backed by SHM buffers.

### Frame Submission
Frames are submitted via the [frame submission pipeline](frame-pipeline).

### Input Routing
Input events are routed through the [input handler](input-handler).

## Implementation

Built in Rust with FFI bindings for Python:
```rust
pub fn nyrqis_compositor_init(width: i32, height: i32) -> *mut CompositorState
```

See [Architecture Overview](architecture) for system context.
""",
                     backlinks=["architecture", "surface-management"]),
            WikiPage("Hardware Abstraction Layer", "hal", status=PageStatus.PUBLISHED,
                     author="hal-dev", tags=["hal", "hardware", "linux"],
                     created=now - 86400 * 15, modified=now - 86400, word_count=600, read_time_min=3,
                     raw_markdown="""# Hardware Abstraction Layer

## Overview

The HAL provides unified access to hardware resources.

## Components

### GPU Access
DRM/KMS for display, EGL/Vulkan for rendering.
See [GPU Documentation](gpu).

### Input Devices
Keyboard, mouse, touch, and gamepad support.

### Audio
PulseAudio/PipeWire integration.

## Backend Switching

The [Backend Abstraction](backend) allows switching between
Linux and Nyrqis backends at runtime.

See [Architecture](architecture) for context.
""",
                     backlinks=["architecture", "gpu", "backend"]),
            WikiPage("GPU Integration", "gpu", status=PageStatus.DRAFT,
                     author="gpu-dev", tags=["gpu", "vulkan", "drm"],
                     created=now - 86400 * 10, modified=now - 172800, word_count=400, read_time_min=2,
                     raw_markdown="""# GPU Integration

## DRM/KMS

Direct Rendering Manager for display output.

## Vulkan

Hardware-accelerated rendering pipeline.

## EGL

OpenGL ES context management.

See [HAL](hal) and [Architecture](architecture).
""",
                     backlinks=["hal", "architecture"]),
            WikiPage("Shell Framework", "shell", status=PageStatus.PUBLISHED,
                     author="shell-dev", tags=["shell", "python", "nui"],
                     created=now - 86400 * 25, modified=now - 14400, word_count=900, read_time_min=5,
                     raw_markdown="""# Shell Framework

## NUI Schema

Components are defined using the [NUI Schema](nui-schema).

## Runtime

The [NyrqisRuntime](runtime) manages state and event dispatch.

## Backend Integration

Uses [Backend Abstraction](backend) for rendering.

See [Architecture](architecture) for system overview.
""",
                     backlinks=["architecture", "backend", "nui-schema"]),
            WikiPage("Security Documentation", "security", status=PageStatus.DRAFT,
                     author="security", tags=["security", "sandbox"],
                     created=now - 86400 * 5, modified=now - 86400 * 2, word_count=300, read_time_min=2,
                     raw_markdown="""# Security Documentation

## Capability-Based Security

Nyrqis uses capability-based security model.

## Sandboxing

Applications run in sandboxed environments.

See [Architecture](architecture).
""",
                     backlinks=["architecture"]),
        ]

        # Extract TOC and links
        for page in self._pages:
            page.extract_toc()
            page.extract_links()

    @property
    def selected_page(self) -> Optional[WikiPage]:
        if 0 <= self._selected_page < len(self._pages):
            return self._pages[self._selected_page]
        return None

    @property
    def total_pages(self) -> int:
        return len(self._pages)

    @property
    def published_pages(self) -> int:
        return sum(1 for p in self._pages if p.status == PageStatus.PUBLISHED)

    @property
    def total_words(self) -> int:
        return sum(p.word_count for p in self._pages)

    @property
    def stats(self) -> WikiStats:
        s = WikiStats()
        s.total_pages = self.total_pages
        s.published = self.published_pages
        s.drafts = sum(1 for p in self._pages if p.status == PageStatus.DRAFT)
        s.total_words = self.total_words
        s.total_links = sum(len(p.links) for p in self._pages)
        s.broken_links = sum(1 for p in self._pages for l in p.links if not l.valid)
        s.orphan_pages = sum(1 for p in self._pages if not p.backlinks and p.status != PageStatus.ORPHANED)
        s.avg_read_time = sum(p.read_time_min for p in self._pages) / max(1, self.total_pages)
        return s

    def select_page(self, idx: int):
        if 0 <= idx < len(self._pages):
            self._selected_page = idx

    def search(self, query: str):
        self._search_query = query
        self._search_results = []
        q = query.lower()
        for page in self._pages:
            for i, line in enumerate(page.raw_markdown.split("\n")):
                if q in line.lower():
                    self._search_results.append(SearchMatch(page.title, i + 1, line))
        self._history.append(f"Searched: {query}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "t":
            self._show_toc = not self._show_toc
        elif key == "b":
            self._show_backlinks = not self._show_backlinks

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS MARKDOWN WIKI                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        s = self.stats
        lines.append(f"  📚 Pages: {s.total_pages} ({s.published} published, {s.drafts} drafts)  Words: {s.total_words:,}  Links: {s.total_links}  Broken: {s.broken_links}  Avg Read: {s.avg_read_time:.0f}min")
        lines.append("")

        # Page list
        lines.append("  ── Pages ──")
        for i, page in enumerate(self._pages):
            sel = "▶" if i == self._selected_page else " "
            lines.append(f"  {sel} {page.status_icon} {page.title:<35s} {page.tag_str}  {page.word_count}w  {page.read_time_min}min")
        lines.append("")

        # Selected page
        page = self.selected_page
        if page:
            lines.append(f"  ── {page.title} ──")
            lines.append(f"  Status: {page.status.value}  Author: {page.author}  Rev: {page.revision}  Modified: {time.strftime('%Y-%m-%d', time.localtime(page.modified))}")
            lines.append(f"  Words: {page.word_count}  Read: {page.read_time_min}min  Links: {len(page.links)}  Backlinks: {len(page.backlinks)}")
            lines.append("")

            # TOC
            if self._show_toc and page.toc:
                lines.append("  ── Table of Contents ──")
                for entry in page.toc:
                    lines.append(f"  {entry.indent}{entry.bullet} {entry.title}")
                lines.append("")

            # Content preview
            content_lines = page.raw_markdown.split("\n")
            lines.append("  ── Content ──")
            for cl in content_lines[:8]:
                clean = cl[:65]
                lines.append(f"  │ {clean}")
            if len(content_lines) > 8:
                lines.append(f"  │ ... ({len(content_lines) - 8} more lines)")
            lines.append("")

            # Links
            if page.links:
                lines.append("  ── Links ──")
                for link in page.links:
                    valid = "✅" if link.valid else "❌"
                    lines.append(f"  {valid} {link.icon} {link.text} → {link.target[:40]}")
                lines.append("")

            # Backlinks
            if self._show_backlinks and page.backlinks:
                lines.append("  ── Backlinks ──")
                for bl in page.backlinks:
                    lines.append(f"  📄 {bl}")
                lines.append("")

        # Search results
        if self._search_results:
            lines.append(f"  ── Search: \"{self._search_query}\" ({len(self._search_results)} results) ──")
            for r in self._search_results[:5]:
                lines.append(f"  📄 {r.page}:{r.line_num}  {r.preview}")
            lines.append("")

        lines.append("  [↑↓]Page [T]TOC [B]Backlinks [S]Search [/]New Page")
        return lines
