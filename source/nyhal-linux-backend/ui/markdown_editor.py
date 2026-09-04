"""Markdown Editor — Live preview, table support, and export.

Features:
- Split-pane editor with live preview
- Markdown syntax highlighting
- Table editing with alignment
- Code block language detection
- Image/links preview
- Export to HTML, PDF (simulated), JSON
- Document outline/TOC generation
- Find and replace
"""

from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class ViewMode(Enum):
    SPLIT = "split"
    EDITOR = "editor"
    PREVIEW = "preview"
    OUTLINE = "outline"
    EXPORT = "export"

    @property
    def icon(self) -> str:
        icons = {
            ViewMode.SPLIT: "⬜", ViewMode.EDITOR: "✏️",
            ViewMode.PREVIEW: "👁", ViewMode.OUTLINE: "📑",
            ViewMode.EXPORT: "📤",
        }
        return icons.get(self, "?")


@dataclass
class MarkdownElement:
    element_type: str = ""  # heading, paragraph, code, list, table, blockquote, hr, image, link
    content: str = ""
    level: int = 0
    language: str = ""
    items: List[str] = field(default_factory=list)
    line_number: int = 0

    @property
    def type_icon(self) -> str:
        icons = {
            "heading": "H", "paragraph": "¶", "code": "⟨⟩",
            "list": "•", "table": "▦", "blockquote": "❝",
            "hr": "—", "image": "🖼", "link": "🔗",
        }
        return icons.get(self.element_type, "?")


@dataclass
class TOCEntry:
    level: int = 0
    title: str = ""
    line_number: int = 0
    anchor: str = ""

    @property
    def indent(self) -> str:
        return "  " * (self.level - 1)


@dataclass
class Document:
    title: str = "Untitled"
    content: str = ""
    created_at: float = 0.0
    modified_at: float = 0.0
    word_count: int = 0
    line_count: int = 0

    @property
    def modified_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified_at))

    @property
    def stats(self) -> str:
        return f"{self.word_count} words, {self.line_count} lines"


class MarkdownEditor:
    def __init__(self):
        self._documents: List[Document] = []
        self._current_doc: int = 0
        self._view_mode: ViewMode = ViewMode.SPLIT
        self._cursor_line: int = 0
        self._cursor_col: int = 0
        self._show_ruler: bool = True
        self._word_wrap: bool = True
        self._spell_check: bool = False
        self._zoom: int = 100
        self._toc: List[TOCEntry] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        content1 = """# Nyrqis OS Development Guide

## Introduction

Nyrqis is a modern operating system built with **Rust** and **Python**. This guide covers the development workflow, architecture, and best practices.

## Getting Started

### Prerequisites

- Rust 1.75+ (via rustup)
- Python 3.10+ (with pip)
- Docker (for containerized builds)
- A Vulkan-capable GPU (NVIDIA, AMD, or Intel)

### Building from Source

```bash
# Clone the repository
git clone https://github.com/Myco-mycelium/Nythera.git
cd Nythera/Nyrqis

# Build the compositor (Rust)
cd source/nyhal-linux-backend/rust/compositor
cargo build --release

# Run the tests
cargo test

# Build the shell (Python)
cd ../../
python -m pytest tests/
```

## Architecture Overview

The system is organized into three layers:

1. **Hardware Abstraction Layer (HAL)** — Direct GPU/display access
2. **Compositor** — Custom Wayland-compatible compositor
3. **Shell** — Rich Python UI with 184+ applications

### Compositor Design

The compositor uses a layered rendering pipeline:

```rust
pub fn render_frame(surface: &Surface) -> Frame {
    let mut renderer = VulkanRenderer::new();
    renderer.begin_frame();
    renderer.composite_layers(surface.layers());
    renderer.end_frame()
}
```

> **Performance Target:** < 1ms frame latency at 144Hz

### Shell Modules

The shell includes modules for:

- **System Tools** — File manager, terminal, settings
- **Development** — Code editor, git client, API tester
- **Creative** — Image editor, music server, 3D viewer
- **Productivity** — Calendar, kanban, notes, email
- **Utilities** — Password manager, QR tool, regex tester

## Testing Strategy

We use a multi-layer approach:

| Level | Coverage | Tool |
|-------|----------|------|
| Unit | ~95% | pytest, cargo test |
| Integration | Module interaction | Custom harness |
| Hardware | Real GPU testing | Manual + CI |
| Performance | Latency benchmarks | Custom profiler |

## Contributing

See the [Contributing Guide](https://github.com/Myco-mycelium/Nythera/CONTRIBUTING.md) for details on:

- Code style and formatting
- Pull request process
- Issue reporting
- Community guidelines

---

*Last updated: September 2026*"""

        doc1 = Document("Nyrqis Development Guide", content1, now - 86400 * 7, now - 3600, 385, 62)
        doc1.word_count = len(content1.split())
        doc1.line_count = content1.count("\n") + 1

        content2 = """# Changelog

## v2.1.0 (2026-09-01)

### New Features

- Triple buffering support
- Hardware-accelerated alpha blending
- Improved EGL fallback path
- Added software renderer fallback

### Bug Fixes

- Fixed DRM memory leak on suspend
- Fixed Wayland bridge disconnect issue
- Fixed memory leak in compositor frame loop

### Performance

- Vulkan renderer: 0.8ms average (was 1.2ms)
- EGL fallback: 1.0ms average (was 1.5ms)
- Memory usage: -15% reduction

## v2.0.0 (2026-07-15)

### Major Changes

- Initial Vulkan renderer
- Wayland compatibility layer
- Shell with 100+ applications
- Backend abstraction layer

## v1.0.0 (2026-06-01)

### Initial Release

- DRM/KMS compositor
- Basic shell UI
- File manager and terminal
- 50+ applications"""

        doc2 = Document("Changelog", content2, now - 86400 * 30, now - 86400, 210, 45)
        doc2.word_count = len(content2.split())
        doc2.line_count = content2.count("\n") + 1

        content3 = """# Architecture Document

## System Components

### Compositor

The compositor handles GPU rendering through a unified pipeline:

```
┌─────────────────────────────────────┐
│           Application Layer         │
├─────────────────────────────────────┤
│         Wayland Protocol            │
├─────────────────────────────────────┤
│        Compositor Engine            │
├──────┬──────┬──────┬───────────────┤
│Vulkan│ EGL  │ GBM  │  Software     │
└──────┴──────┴──────┴───────────────┘
```

### HAL Layer

| Component | Purpose | Status |
|-----------|---------|--------|
| DRM | Display output | ✅ Stable |
| GBM | Buffer allocation | 🔄 Beta |
| EGL | OpenGL ES fallback | ✅ Stable |
| Vulkan | Primary renderer | ✅ Stable |

### Shell Framework

The shell is organized as a plugin system:

```python
class ShellModule:
    def __init__(self):
        self.name = "Module Name"
        self.version = "1.0.0"
    
    def render(self):
        # Module rendering logic
        pass
    
    def handle_input(self, event):
        # Input handling
        pass
```

> The shell currently has **184 modules** with **4099 passing tests**."""

        doc3 = Document("Architecture Document", content3, now - 86400 * 14, now - 86400 * 2, 180, 50)
        doc3.word_count = len(content3.split())
        doc3.line_count = content3.count("\n") + 1

        self._documents = [doc1, doc2, doc3]

        # Generate TOC for current doc
        self._build_toc()

    def _build_toc(self):
        self._toc = []
        doc = self.current_doc
        if not doc:
            return
        for i, line in enumerate(doc.content.split("\n")):
            if line.startswith("#"):
                level = len(line.split(" ")[0])
                title = line.lstrip("#").strip()
                anchor = title.lower().replace(" ", "-")
                self._toc.append(TOCEntry(level, title, i + 1, anchor))

    @property
    def current_doc(self) -> Optional[Document]:
        if 0 <= self._current_doc < len(self._documents):
            return self._documents[self._current_doc]
        return None

    @property
    def preview_lines(self) -> List[str]:
        doc = self.current_doc
        if not doc:
            return []
        return self._render_markdown(doc.content)

    def _render_markdown(self, content: str) -> List[str]:
        lines = []
        in_code_block = False
        code_lang = ""

        for line in content.split("\n"):
            if line.startswith("```"):
                if in_code_block:
                    lines.append("  └─── end code ───┘")
                    in_code_block = False
                else:
                    code_lang = line[3:].strip()
                    lines.append(f"  ┌─── {code_lang} ───┐")
                    in_code_block = True
                continue

            if in_code_block:
                lines.append(f"  │ {line}")
                continue

            if line.startswith("# "):
                lines.append(f"  {'═' * 60}")
                lines.append(f"  {line[2:].upper()}")
                lines.append(f"  {'═' * 60}")
            elif line.startswith("## "):
                lines.append(f"  ── {line[3:]} ──")
            elif line.startswith("### "):
                lines.append(f"  · {line[4:]}")
            elif line.startswith("- "):
                lines.append(f"  • {line[2:]}")
            elif line.startswith("> "):
                lines.append(f"  │ {line[2:]}")
            elif line.startswith("|"):
                lines.append(f"  {line}")
            elif line.startswith("---"):
                lines.append(f"  {'─' * 60}")
            elif line.strip():
                lines.append(f"  {line}")
            else:
                lines.append("")

        return lines

    def select_doc(self, idx: int):
        if 0 <= idx < len(self._documents):
            self._current_doc = idx
            self._build_toc()

    def set_view(self, mode: ViewMode):
        self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS MARKDOWN EDITOR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        doc = self.current_doc
        doc_name = doc.title if doc else "None"
        doc_stats = doc.stats if doc else ""
        lines.append(f"  📄 {doc_name}  📊 {doc_stats}  🔤 {self._zoom}%  Modified: {doc.modified_str if doc else 'N/A'}")
        lines.append("")

        if self._view_mode == ViewMode.SPLIT and doc:
            preview = self.preview_lines[:16]
            for line in preview:
                lines.append(f"  {line}")

        elif self._view_mode == ViewMode.EDITOR and doc:
            lines.append("  ── Editor ──")
            content_lines = doc.content.split("\n")
            for i, line in enumerate(content_lines[:16]):
                marker = "▶" if i == self._cursor_line else " "
                lines.append(f"  {marker}{i + 1:>3d} │ {line[:65]}")

        elif self._view_mode == ViewMode.PREVIEW and doc:
            lines.append("  ── Preview ──")
            for line in self.preview_lines[:16]:
                lines.append(f"  {line}")

        elif self._view_mode == ViewMode.OUTLINE:
            lines.append("  ── Document Outline ──")
            for entry in self._toc:
                indent = entry.indent
                lines.append(f"  {indent}{entry.level * '·'} {entry.title} (line {entry.line_number})")

        elif self._view_mode == ViewMode.EXPORT:
            lines.append("  ── Export Options ──")
            lines.append("  📄 HTML   — Full standalone HTML with CSS")
            lines.append("  📕 PDF    — Formatted PDF document")
            lines.append("  📋 JSON   — Structured document data")
            lines.append("  📝 Plain  — Plain text (stripped markdown)")
            lines.append("")
            if doc:
                md_lines = doc.content.split("\n")[:10]
                lines.append("  ── Markdown Preview ──")
                for line in md_lines:
                    lines.append(f"  {line}")

        lines.append("")
        lines.append("  [S]plit [E]ditor [P]review [O]utline e[X]port [↑↓]Nav [F]ind [Z]oom")
        return lines


@dataclass
class MarkdownDocument:
    title: str = ""
    content: str = ""
    tags: list = field(default_factory=list)
    modified: float = 0.0


class ReadingMode(Enum):
    READING = "reading"
    EDITING = "editing"
    PREVIEW = "preview"


class MarkdownBlock:
    pass  # backward compat stub
