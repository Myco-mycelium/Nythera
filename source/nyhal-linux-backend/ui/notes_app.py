"""Note-Taking App — Markdown, tags, and linked references (Zettelkasten).

Features:
- Markdown editing with live preview
- Tag system with hierarchical tags
- Linked references (Zettelkasten-style backlinks)
- Note types: permanent, literature, fleeting, project
- Search full-text and by tags
- Daily notes with date navigation
- Star/pin important notes
- Graph view for linked notes
"""

from __future__ import annotations

import copy
import time
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set
from enum import Enum


class NoteType(Enum):
    PERMANENT = "permanent"  # Zettelkasten atomic notes
    LITERATURE = "literature"  # Notes from books/articles
    FLEETING = "fleeting"  # Quick capture
    PROJECT = "project"  # Project-specific notes

    @property
    def icon(self) -> str:
        icons = {
            NoteType.PERMANENT: "💎", NoteType.LITERATURE: "📖",
            NoteType.FLEETING: "⚡", NoteType.PROJECT: "📂",
        }
        return icons.get(self, "?")


@dataclass
class NoteLink:
    source_id: int = 0
    target_id: int = 0
    context: str = ""
    bidirectional: bool = True


@dataclass
class Note:
    id: int = 0
    title: str = ""
    content: str = ""
    note_type: NoteType = NoteType.PERMANENT
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    modified_at: float = 0.0
    starred: bool = False
    pinned: bool = False
    word_count: int = 0
    author: str = ""
    source_url: str = ""
    links_to: List[int] = field(default_factory=list)
    linked_from: List[int] = field(default_factory=list)
    folder: str = "Notes"
    color: "NoteColor" = None  # NoteColor enum, defined at module bottom
    modified: Optional[float] = None  # alias for modified_at (spec API)
    created: Optional[float] = None   # alias for created_at (spec API)

    def __post_init__(self):
        # Alias fields: modified/created → modified_at/created_at
        if self.modified is not None:
            self.modified_at = self.modified
        if self.created is not None:
            self.created_at = self.created
        # Compute word_count from content when not carried over
        if not self.word_count and self.content:
            self.word_count = len(self.content.split())

    @property
    def note_id(self) -> str:
        """Stable 8-character string id (spec API)."""
        return f"{self.id:08d}"

    @property
    def time_ago(self) -> str:
        return self.age_str

    @property
    def modified_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified_at))

    @property
    def created_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.created_at))

    @property
    def age_str(self) -> str:
        age = time.time() - self.modified_at
        if age < 3600:
            return f"{age / 60:.0f}m ago"
        if age < 86400:
            return f"{age / 3600:.0f}h ago"
        return f"{age / 86400:.0f}d ago"

    @property
    def preview(self) -> str:
        text = self.content.replace("\n", " ")
        return text[:80] + "..." if len(text) > 80 else text

    @property
    def word_count_display(self) -> str:
        return f"{self.word_count} words"

    @property
    def link_count(self) -> str:
        total = len(self.links_to) + len(self.linked_from)
        return f"🔗{total}" if total > 0 else ""

    @property
    def tag_str(self) -> str:
        return " ".join(f"#{t}" for t in self.tags) if self.tags else ""

    @property
    def markdown_headings(self) -> List[str]:
        headings = []
        for line in self.content.split("\n"):
            if line.startswith("#"):
                headings.append(line.strip())
        return headings


@dataclass
class Tag:
    name: str = ""
    parent: str = ""
    note_count: int = 0
    color: str = "#666"

    @property
    def display(self) -> str:
        if self.parent:
            return f"{self.parent}/{self.name}"
        return self.name


@dataclass
class DailyNote:
    date: str = ""
    note_id: int = 0
    heading: str = ""
    tasks_done: int = 0
    tasks_total: int = 0

    @property
    def progress_str(self) -> str:
        if self.tasks_total == 0:
            return ""
        return f"☑{self.tasks_done}/{self.tasks_total}"


@dataclass
class SearchMatch:
    note_id: int = 0
    line_number: int = 0
    line_text: str = ""
    context: str = ""


class NotesApp:
    def __init__(self):
        self._notes: List[Note] = []
        self._tags: List[Tag] = []
        self._daily_notes: List[DailyNote] = []
        self._links: List[NoteLink] = []
        self._selected_note: int = 0
        self._view_mode: str = "editor"  # editor, preview, links, tags, graph, daily, list
        self._search_text: str = ""
        self._filter_tag: str = ""
        self._filter_type: Optional[NoteType] = None
        self._filter_color = None
        self._show_linked: bool = True
        self._folders: List[Folder] = [
            Folder(name="Notes"), Folder(name="Archive"), Folder(name="Trash"),
        ]
        self.current_folder: str = "Notes"
        self._sort_mode = SortMode.DATE
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Tags
        self._tags = [
            Tag("compositor", "", 8, "#4A90D9"),
            Tag("vulkan", "gpu", 4, "#2ECC71"),
            Tag("egl", "gpu", 3, "#1ABC9C"),
            Tag("drm", "gpu", 2, "#E67E22"),
            Tag("wayland", "protocol", 5, "#9B59B6"),
            Tag("shell", "ui", 6, "#E74C3C"),
            Tag("security", "", 3, "#F39C12"),
            Tag("architecture", "", 4, "#3498DB"),
            Tag("rust", "lang", 5, "#E67E22"),
            Tag("python", "lang", 4, "#2ECC71"),
            Tag("performance", "", 3, "#E74C3C"),
            Tag("testing", "", 2, "#9B59B6"),
        ]

        # Notes — Zettelkasten style
        self._notes = [
            Note(1, "Vulkan Rendering Pipeline",
                 """# Vulkan Rendering Pipeline

The Nyrqis compositor uses Vulkan for GPU-accelerated rendering.

## Key Components

1. **Surface Management** — VK_KHR_swapchain for display surfaces
2. **Layer Composition** — Each window layer as a Vulkan texture
3. **Alpha Blending** — Hardware-accelerated with VK_BLEND_FACTOR
4. **Frame Sync** — Semaphores for GPU-CPU synchronization

## Performance Results

- Frame latency: 0.8ms average (target: <1ms)
- GPU utilization: 45% at 144Hz
- Memory: 256MB VRAM for 8 layers

## Related

- See [[EGL Fallback Path]] for non-Vulkan hardware
- See [[DRM Buffer Management]] for display output
""",
                 NoteType.PERMANENT, ["vulkan", "compositor", "architecture"], now - 86400, now - 3600, True, True, 128, "Buffy",
                 links_to=[2, 3], linked_from=[4, 5]),

            Note(2, "EGL Fallback Path",
                 """# EGL Fallback Path

For hardware without Vulkan support, we provide an EGL/OpenGL ES 3.0 fallback.

## When to Use

- Older Intel integrated GPUs
- Raspberry Pi (VideoCore)
- VMware/VirtualBox virtual displays

## Implementation

```python
class EGLFallback:
    def render_frame(self, layers):
        egl.make_current(self.display)
        self.compositor.program.use()
        for layer in layers:
            self.compositor.draw_texture(layer.texture)
        egl.swap_buffers(self.display)
```

## Performance

- Frame latency: 1.2ms average
- Falls back to software if EGL unavailable

## Related

- [[Vulkan Rendering Pipeline]] for primary path
- [[Software Rendering]] for CPU-only fallback
""",
                 NoteType.PERMANENT, ["egl", "compositor", "architecture"], now - 86400 * 2, now - 7200, False, False, 95, "Nyx",
                 links_to=[1, 3], linked_from=[1]),

            Note(3, "DRM Buffer Management",
                 """# DRM Buffer Management

Direct Rendering Manager handles display output through kernel interfaces.

## Key Concepts

- **DRM** — Direct Rendering Manager kernel module
- **KMS** — Kernel Mode Setting for display configuration
- **GBM** — Generic Buffer Management for buffer allocation
- **Atomic Modesetting** — Single ioctl for display configuration

## Nyrqis Implementation

We use atomic modesetting via the Rust `drm` crate:

```rust
// Atomic commit
drm.atomic_commit(connector_id, crtc_id, &framebuffer, &properties)
```

## Related

- [[Vulkan Rendering Pipeline]] feeds into DRM
- [[EGL Fallback Path]] also outputs through DRM
""",
                 NoteType.PERMANENT, ["drm", "compositor", "rust"], now - 86400 * 3, now - 86400, False, False, 78, "Nyx",
                 links_to=[1, 2], linked_from=[1, 2]),

            Note(4, "Shell Architecture Overview",
                 """# Shell Architecture Overview

The Nyrqis shell is a Python application suite running on the compositor.

## Layers

1. **Backend Abstraction** — Unified rendering interface
2. **Shell Renderer** — Bridges shell to compositor
3. **Application Modules** — 172 individual UI apps
4. **Plugin System** — Dynamic module loading

## Module Categories

- System utilities (file manager, terminal, settings)
- Development tools (code editor, git client, API tester)
- Creative apps (image editor, music server, 3D viewer)
- Productivity (calendar, kanban, notes, email)

## Related

- [[Vulkan Rendering Pipeline]] provides rendering
- [[Wayland Protocol]] handles display communication
""",
                 NoteType.PROJECT, ["shell", "architecture", "python"], now - 86400 * 5, now - 86400 * 2, True, False, 110, "Buffy",
                 links_to=[1, 5], linked_from=[6]),

            Note(5, "Wayland Protocol Integration",
                 """# Wayland Protocol Integration

Nyrqis implements a Wayland-compatible compositor for client compatibility.

## Supported Protocols

- xdg-shell (window management)
- xdg-decoration (client-side decorations)
- wl_output (display configuration)
- wl_seat (input devices)
- xdg-portal (screen sharing, file picker)

## Bridge Architecture

The Wayland bridge translates between Nyrqis-native surfaces and Wayland protocol messages.

## Known Issues

- Bridge disconnects on suspend (fix in progress)
- Screen sharing needs xdg-desktop-portal

## Related

- [[Shell Architecture Overview]] uses Wayland
- [[Vulkan Rendering Pipeline]] composites Wayland surfaces
""",
                 NoteType.PERMANENT, ["wayland", "protocol", "compositor"], now - 86400 * 4, now - 86400 * 1, False, False, 92, "Grace",
                 links_to=[1, 4], linked_from=[4]),

            Note(6, "Testing Strategy",
                 """# Testing Strategy

Nyrqis uses a multi-layer testing approach.

## Test Levels

1. **Unit Tests** — Individual module testing (4193+ tests)
2. **Integration Tests** — Module interaction testing
3. **Hardware Tests** — Real GPU/display testing
4. **Performance Tests** — Latency and throughput benchmarks

## Current Coverage

- Shell modules: ~95% unit test coverage
- Compositor: Integration + hardware tests
- HAL: Hardware-specific test matrix

## CI/CD

GitHub Actions runs on every push:
- `cargo test` for Rust crates
- `python -m pytest` for Python modules
- Cross-compilation check for ARM

## Related

- [[Shell Architecture Overview]] has the modules under test
""",
                 NoteType.PROJECT, ["testing", "architecture"], now - 86400 * 7, now - 86400 * 3, False, False, 65, "Buffy",
                 links_to=[4], linked_from=[]),

            Note(7, "Welcome to Nyrqis Notes",
                 """# Welcome to Nyrqis Notes

This Zettelkasten-style notebook keeps every idea linked.

## Getting Started

- Press [E] to edit, [P] to preview markdown
- Link notes with [[Note Title]] syntax
- Tag notes to filter the list

Everyone starts here — welcome aboard!
""",
                 NoteType.FLEETING, ["welcome", "meta"], now - 3600, now - 600, True, True, 55, "Buffy",
                 links_to=[4], linked_from=[]),

            Note(8, "Field Journal",
                 """# Field Journal

Observations from the morning hike.

- Found a huge mushroom on the north trail
- Two woodpeckers near the creek
- Sketches pending
""",
                 NoteType.LITERATURE, ["journal"], now - 86400 * 2, now - 86400, False, False, 32, "Grace",
                 links_to=[], linked_from=[]),
        ]

        # Links
        self._links = [
            NoteLink(1, 2, "fallback relationship"),
            NoteLink(1, 3, "feeds into DRM"),
            NoteLink(2, 3, "both output to DRM"),
            NoteLink(4, 1, "uses Vulkan"),
            NoteLink(4, 5, "uses Wayland"),
            NoteLink(5, 1, "composites via Vulkan"),
        ]

        # Daily notes
        today = time.strftime("%Y-%m-%d")
        self._daily_notes = [
            DailyNote(today, 0, "Sprint Planning", 3, 5),
            DailyNote(time.strftime("%Y-%m-%d", time.localtime(now - 86400)), 0, "GPU Testing", 4, 4),
            DailyNote(time.strftime("%Y-%m-%d", time.localtime(now - 86400 * 2)), 0, "Code Review", 2, 6),
        ]

    @property
    def filtered_notes(self) -> List[Note]:
        result = self._notes
        if self._filter_tag:
            result = [n for n in result if self._filter_tag in n.tags]
        if self._filter_type:
            result = [n for n in result if n.note_type == self._filter_type]
        if self._filter_color is not None:
            result = [n for n in result if getattr(n, "color", None) == self._filter_color]
        if self._search_text:
            q = self._search_text.lower()
            result = [n for n in result if q in n.title.lower() or q in n.content.lower() or q in " ".join(n.tags).lower()]
        return result

    @property
    def total_notes(self) -> int:
        return len(self._notes)

    @property
    def total_characters(self) -> int:
        return sum(len(n.content) for n in self._notes)

    @property
    def all_tags(self) -> List[str]:
        tags: Set[str] = set()
        for n in self._notes:
            tags.update(n.tags)
        return sorted(tags)

    @property
    def pinned_notes(self) -> List[Note]:
        return [n for n in self._notes if n.pinned]

    @property
    def notes_by_folder(self) -> Dict[str, List[Note]]:
        by: Dict[str, List[Note]] = {}
        for n in self._notes:
            by.setdefault(getattr(n, "folder", "Notes"), []).append(n)
        return by

    @property
    def folders(self) -> List[Folder]:
        return self._folders

    @property
    def sort_mode(self):
        return self._sort_mode

    # ─── CRUD (spec API) ──────────────────────────────────────────
    def create_note(self, title: str, content: str = "", folder: str = "Notes",
                    tags: Optional[List[str]] = None, **kwargs) -> Note:
        now = time.time()
        note = Note(
            id=max((n.id for n in self._notes), default=0) + 1,
            title=title, content=content,
            note_type=kwargs.get("note_type", NoteType.FLEETING),
            tags=list(tags or []), created_at=now, modified_at=now,
            folder=folder,
        )
        self._notes.append(note)
        return note

    def get_note(self, note_id) -> Optional[Note]:
        """Find a note by int id or 8-char string note_id."""
        if isinstance(note_id, str) and note_id.isdigit():
            note_id = int(note_id)
        return next((n for n in self._notes if n.id == note_id), None)

    def delete_note(self, note_id) -> bool:
        """Move a note to the Trash folder (soft delete)."""
        note = self.get_note(note_id)
        if note:
            note.folder = "Trash"
            return True
        return False

    def permanently_delete(self, note_id) -> bool:
        note = self.get_note(note_id)
        if not note:
            return False
        self._notes = [n for n in self._notes if n is not note]
        return True

    def restore_note(self, note_id: int) -> bool:
        note = self.get_note(note_id)
        if note and note.folder == "Trash":
            note.folder = "Notes"
            return True
        return False

    def archive_note(self, note_id: int) -> bool:
        note = self.get_note(note_id)
        if note:
            note.folder = "Archive"
            return True
        return False

    def unarchive_note(self, note_id: int) -> bool:
        note = self.get_note(note_id)
        if note and note.folder == "Archive":
            note.folder = "Notes"
            return True
        return False

    def duplicate_note(self, note_id: int) -> Optional[Note]:
        note = self.get_note(note_id)
        if not note:
            return None
        dup = copy.copy(note)
        dup.tags = list(note.tags)
        dup.links_to = list(note.links_to)
        dup.linked_from = list(note.linked_from)
        dup.id = max((n.id for n in self._notes), default=0) + 1
        dup.title = f"{note.title} (copy)"
        self._notes.append(dup)
        return dup

    def toggle_pin(self, note_id: int) -> bool:
        note = self.get_note(note_id)
        if note:
            note.pinned = not note.pinned
            return True
        return False

    def add_tag(self, note_id: int, tag: str) -> bool:
        note = self.get_note(note_id)
        if note and tag not in note.tags:
            note.tags.append(tag)
            return True
        return False

    def remove_tag(self, note_id: int, tag: str) -> bool:
        note = self.get_note(note_id)
        if note and tag in note.tags:
            note.tags.remove(tag)
            return True
        return False

    def set_note_color(self, note_id: int, color) -> bool:
        note = self.get_note(note_id)
        if note:
            note.color = color
            return True
        return False

    def export_note(self, note_id: int) -> str:
        note = self.get_note(note_id)
        if not note:
            return ""
        return f"# {note.title}\n\n{note.content}\n"

    def export_all(self) -> Dict[str, str]:
        return {n.title: self.export_note(n.id) for n in self._notes}

    def search(self, query: str) -> List[Note]:
        if not query:
            return []
        q = query.lower()
        return [n for n in self._notes
                if q in n.title.lower() or q in n.content.lower()
                or any(q in t.lower() for t in n.tags)]

    # ─── Folders ──────────────────────────────────────────────────
    def create_folder(self, name: str, color: str = "") -> Folder:
        folder = Folder(name=name, color=color)
        self._folders.append(folder)
        return folder

    def delete_folder(self, name: str) -> bool:
        if name in ("Notes", "Archive", "Trash"):
            return False
        before = len(self._folders)
        self._folders = [f for f in self._folders if f.name != name]
        return len(self._folders) < before

    def get_notes_in_folder(self, folder: str) -> List[Note]:
        return [n for n in self._notes if getattr(n, "folder", "Notes") == folder]

    def folder_note_count(self, folder: str) -> int:
        return len(self.get_notes_in_folder(folder))

    # ─── Sorting / filtering ──────────────────────────────────────
    def set_sort_mode(self, mode) -> None:
        self._sort_mode = mode

    def cycle_sort_mode(self):
        modes = list(SortMode)
        idx = modes.index(self._sort_mode) if self._sort_mode in modes else 0
        self._sort_mode = modes[(idx + 1) % len(modes)]
        return self._sort_mode

    def set_filter_tag(self, tag: str) -> None:
        self._filter_tag = tag

    def set_filter_color(self, color) -> None:
        self._filter_color = color

    # ─── Editor open/close (spec API) ─────────────────────────────
    def open_note(self, note_id: int) -> bool:
        note = self.get_note(note_id)
        if not note:
            return False
        self._view_mode = "edit"
        self._edit_note_id = note_id
        for i, n in enumerate(self.filtered_notes):
            if n is note:
                self._selected_note = i
                break
        return True

    def close_editor(self) -> bool:
        self._view_mode = "list"
        return True

    def select(self, idx: int) -> int:
        self._selected_note = max(0, idx)
        return self._selected_note

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @view_mode.setter
    def view_mode(self, value: str) -> None:
        self._view_mode = value

    def select_up(self) -> int:
        if self._selected_note > 0:
            self._selected_note -= 1
        return self._selected_note

    def select_down(self) -> int:
        notes = self.filtered_notes
        if self._selected_note < len(notes) - 1:
            self._selected_note += 1
        return self._selected_note

    @property
    def selected_index(self) -> int:
        return self._selected_note

    def handle_key(self, key: str) -> str:
        """Minimal key handler: Ctrl+n new note, Escape closes the editor."""
        if key in ("Ctrl+n", "ctrl+n", "N"):
            note = self.create_note("Untitled")
            self.open_note(note.id)
            return "new"
        if key == "Escape":
            if self._view_mode == "edit":
                self.close_editor()
                return "close_editor"
            return ""
        return ""

    def render_list(self) -> List[str]:
        lines = ["NOTES", "=" * 40]
        for i, n in enumerate(self.filtered_notes):
            marker = " > " if i == self._selected_note else "   "
            pin = "📌" if n.pinned else " "
            lines.append(f"{marker}{pin}{n.title} {n.note_type.icon}")
        lines.append(f"\n{len(self._notes)} notes, {self.total_words} words")
        return lines

    def render_editor(self) -> List[str]:
        note = self.get_note(getattr(self, "_edit_note_id", 0)) or self.selected_note
        if not note:
            return ["No note open"]
        lines = [f"Editing: {note.title}", "=" * 40, ""]
        lines.extend(f"  {line}" for line in note.content.split("\n"))
        return lines

    @property
    def selected_note(self) -> Optional[Note]:
        notes = self.filtered_notes
        if 0 <= self._selected_note < len(notes):
            return notes[self._selected_note]
        return None

    @property
    def total_words(self) -> int:
        return sum(n.word_count for n in self._notes)

    @property
    def total_links(self) -> int:
        return len(self._links)

    def select_note(self, idx: int):
        if 0 <= idx < len(self.filtered_notes):
            self._selected_note = idx

    def set_view(self, mode: str):
        if mode in ("editor", "preview", "links", "tags", "graph", "daily"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS NOTES — Zettelkasten                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  📝 {len(self._notes)} notes  🔗 {self.total_links} links  🏷 {len(self._tags)} tags  📊 {self.total_words:,} words")
        lines.append("")

        if self._view_mode == "editor":
            note = self.selected_note
            if note:
                lines.append(f"  ── {note.note_type.icon} {note.title} ──")
                lines.append(f"  Tags: {note.tag_str}  Modified: {note.age_str}  {note.word_count_display}  {note.link_count}")
                lines.append(f"  Author: {note.author}")
                lines.append("")
                for line in note.content.split("\n")[:12]:
                    lines.append(f"  {line[:76]}")
            else:
                lines.append("  No note selected")

        elif self._view_mode == "preview":
            note = self.selected_note
            if note:
                lines.append(f"  ═══ {note.title} ═══")
                for line in note.content.split("\n")[:16]:
                    # Simple markdown rendering
                    if line.startswith("# "):
                        lines.append(f"  {'═' * 40}")
                        lines.append(f"  {line[2:]}")
                        lines.append(f"  {'═' * 40}")
                    elif line.startswith("## "):
                        lines.append(f"  ── {line[3:]} ──")
                    elif line.startswith("- "):
                        lines.append(f"  • {line[2:]}")
                    elif line.startswith("```"):
                        lines.append(f"  ┌─── code ───┐")
                    else:
                        lines.append(f"  {line[:76]}")

        elif self._view_mode == "tags":
            lines.append("  ── Tags ──")
            for tag in self._tags:
                parent = f" ({tag.parent})" if tag.parent else ""
                lines.append(f"  🏷 {tag.display}{parent}  {tag.note_count} notes")
        elif self._view_mode == "links":
            lines.append("  ── Linked References ──")
            for link in self._links:
                src = next((n.title for n in self._notes if n.id == link.source_id), "?")
                tgt = next((n.title for n in self._notes if n.id == link.target_id), "?")
                lines.append(f"  🔗 {src} → {tgt}")
                lines.append(f"     Context: {link.context}")

        elif self._view_mode == "graph":
            lines.append("  ── Knowledge Graph ──")
            for note in self._notes:
                linked = len(note.links_to) + len(note.linked_from)
                bar = "●" * min(10, linked) + "○" * max(0, 10 - linked)
                lines.append(f"  {note.note_type.icon} {note.title:<35s} [{bar}] {linked} connections")

        elif self._view_mode == "daily":
            lines.append("  ── Daily Notes ──")
            for dn in self._daily_notes:
                lines.append(f"  📅 {dn.date} — {dn.heading} {dn.progress_str}")

        lines.append("")
        lines.append("  [E]ditor [P]review [T]ags [L]inks [G]raph [D]aily [/]Search [↑↓]Nav [★]Star")
        return lines


@dataclass
class Folder:
    name: str = ""
    notes: list = field(default_factory=list)
    color: str = ""


class NoteEditor:
    """Cursor-based plain-text editor over a Note's content.

    Operates on a Note in place (note.content) or, when constructed
    with plain text, on its own buffer.
    """

    def __init__(self, note=None, text: str = ""):
        self.note = note
        self._text = text if note is None else note.content
        self._cursor = len(self._text)
        self._undo_stack: List[str] = []
        self._redo_stack: List[str] = []
        self._clean_text = self._text

    @property
    def content(self) -> str:
        return self.note.content if self.note is not None else self._text

    def _set_content(self, value: str) -> None:
        if self.note is not None:
            self.note.content = value
        else:
            self._text = value

    def insert(self, text: str) -> int:
        c = self.content
        self._undo_stack.append(c)
        self._redo_stack.clear()
        self._set_content(c[:self._cursor] + text + c[self._cursor:])
        self._cursor += len(text)
        return self._cursor

    def delete_backward(self, count: int = 1) -> str:
        c = self.content
        start = max(0, self._cursor - count)
        deleted = c[start:self._cursor]
        self._undo_stack.append(c)
        self._redo_stack.clear()
        self._set_content(c[:start] + c[self._cursor:])
        self._cursor = start
        return deleted

    def delete_forward(self, count: int = 1) -> str:
        c = self.content
        end = min(len(c), self._cursor + count)
        deleted = c[self._cursor:end]
        self._undo_stack.append(c)
        self._redo_stack.clear()
        self._set_content(c[:self._cursor] + c[end:])
        return deleted

    def move_cursor(self, pos: int) -> int:
        self._cursor = max(0, min(pos, len(self.content)))
        return self._cursor

    def move_to_start(self) -> int:
        self._cursor = 0
        return self._cursor

    def move_to_end(self) -> int:
        self._cursor = len(self.content)
        return self._cursor

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(self.content)
        self._set_content(self._undo_stack.pop())
        self._cursor = len(self.content)
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(self.content)
        self._set_content(self._redo_stack.pop())
        self._cursor = len(self.content)
        return True

    @property
    def is_dirty(self) -> bool:
        return self.content != self._clean_text

    def mark_clean(self) -> None:
        self._clean_text = self.content

    @property
    def line_col(self):
        before = self.content[:self._cursor]
        line = before.count("\n") + 1
        col = self._cursor - (before.rfind("\n") + 1)
        return (line, col)

# ─── Backward-compat exports ────────────────────────────────────────────
class MarkdownRenderer:
    """Renders markdown text to terminal-friendly plain output.

    Bold/emphasis markers are intentionally preserved (terminals have no
    <strong>); headers are flattened to their text and list items get a
    proper bullet.
    """
    def __init__(self, text: str = ""):
        self.text = text
    def render(self, text: str = None) -> str:
        t = text if text is not None else self.text
        result = t or ""
        # Lists → bullets
        result = re.sub(r'^[-*]\s+(.+)$', r'• \1', result, flags=re.MULTILINE)
        # Headers → plain text
        result = re.sub(r'^#{1,6}\s+(.+)$', r'\1', result, flags=re.MULTILINE)
        # Horizontal rules
        result = re.sub(r'^---+\s*$', '─' * 40, result, flags=re.MULTILINE)
        return result
    def render_inline(self, text: str) -> str:
        return text
    def char_count(self, text: str = "") -> int:
        return len(text or self.text)
    def line_count(self, text: str = "") -> int:
        t = text or self.text
        return len(t.splitlines()) if t else 0
    def word_count(self, text: str = "") -> int:
        t = text or self.text
        return len(t.split()) if t else 0
    def strip_markdown(self, text: str = "") -> str:
        import re
        t = text or self.text
        t = re.sub(r'#+\s*', '', t)
        t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
        t = re.sub(r'\*(.+?)\*', r'\1', t)
        t = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', t)
        t = re.sub(r'`(.+?)`', r'\1', t)
        return t.strip()


from enum import Enum as _SortMode
class SortMode(_SortMode):
    DATE = "date"
    TITLE = "title"
    MODIFIED = "modified"
    SIZE = "size"
    ALPHABETICAL = "alphabetical"


from enum import Enum as _NoteColor
class NoteColor(_NoteColor):
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    PINK = "pink"
    GRAY = "gray"
    WHITE = "white"
