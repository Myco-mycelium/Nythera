"""
Nyrqis Notes — lightweight note-taking app with markdown support.

Features:
- Rich text notes with markdown formatting
- Folder organization with nested folders
- Full-text search across all notes
- Pin/star important notes
- Sort by date modified, created, or title
- Word/character count
- Export notes as markdown files
- Undo/redo editing
- Auto-save with dirty tracking
"""

import os
import re
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Set, Callable, Tuple
from datetime import datetime


# ─── Markdown Renderer (text-based) ─────────────────────────────────────


class MarkdownRenderer:
    """Simple text-based markdown renderer."""

    def render(self, text: str, width: int = 72) -> str:
        """Render markdown to styled text representation."""
        lines = text.split("\n")
        output = []
        in_code_block = False
        code_lang = ""

        for line in lines:
            # Code blocks
            if line.startswith("```"):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line[3:].strip()
                    output.append(f" ┌─ {code_lang or 'code'} {'─' * max(0, width - len(code_lang) - 6)}")
                else:
                    in_code_block = False
                    output.append(f" └{'─' * (width - 2)}")
                continue

            if in_code_block:
                output.append(f" │ {line}")
                continue

            # Headers
            if line.startswith("######"):
                output.append(f" {line[6:].strip()}")
            elif line.startswith("#####"):
                output.append(f" {line[5:].strip()}")
            elif line.startswith("####"):
                output.append(f" {line[4:].strip()}")
            elif line.startswith("###"):
                output.append(f" {line[3:].strip()}")
            elif line.startswith("##"):
                output.append(f"  {line[2:].strip()}")
            elif line.startswith("#"):
                output.append(f"   {line[1:].strip()}")
            # Horizontal rule
            elif re.match(r'^[-*_]{3,}$', line.strip()):
                output.append(f" {'─' * (width - 2)}")
            # Blockquote
            elif line.startswith(">"):
                output.append(f" │ {line[1:].strip()}")
            # Unordered list
            elif re.match(r'^[-*+]\s', line):
                output.append(f"  • {line[2:]}")
            # Ordered list
            elif re.match(r'^\d+\.\s', line):
                output.append(f"  {line}")
            # Checkbox
            elif line.startswith("- [x]"):
                output.append(f"  ☑ {line[5:]}")
            elif line.startswith("- [ ]"):
                output.append(f"  ☐ {line[5:]}")
            else:
                output.append(line)

        return "\n".join(output)

    def strip_markdown(self, text: str) -> str:
        """Strip markdown formatting to plain text."""
        text = re.sub(r'```.*?```', '[code]', text, flags=re.DOTALL)
        text = re.sub(r'#{1,6}\s*', '', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'^[-*+]\s', '• ', text, flags=re.MULTILINE)
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n{2,}', '\n', text)
        return text.strip()

    def word_count(self, text: str) -> int:
        """Count words in text."""
        plain = self.strip_markdown(text)
        return len(plain.split()) if plain.strip() else 0

    def char_count(self, text: str) -> int:
        """Count characters."""
        return len(text)

    def line_count(self, text: str) -> int:
        """Count lines."""
        return len(text.split("\n"))


# ─── Data Classes ────────────────────────────────────────────────────────


class SortMode(Enum):
    MODIFIED = "modified"
    CREATED = "created"
    TITLE = "title"


class NoteColor(Enum):
    DEFAULT = "default"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    PINK = "pink"
    ORANGE = "orange"


@dataclass
class Note:
    """A single note."""
    title: str
    content: str = ""
    folder: str = "Notes"
    color: NoteColor = NoteColor.DEFAULT
    pinned: bool = False
    archived: bool = False
    deleted: bool = False
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    note_id: str = ""

    def __post_init__(self):
        if not self.note_id:
            self.note_id = hashlib.md5(f"{self.title}{self.created}".encode()).hexdigest()[:8]

    @property
    def word_count(self) -> int:
        renderer = MarkdownRenderer()
        return renderer.word_count(self.content)

    @property
    def char_count(self) -> int:
        renderer = MarkdownRenderer()
        return renderer.char_count(self.content)

    @property
    def preview(self) -> str:
        """Get a text preview of the note content."""
        renderer = MarkdownRenderer()
        plain = renderer.strip_markdown(self.content)
        # First non-empty line
        for line in plain.split("\n"):
            line = line.strip()
            if line:
                return line[:80]
        return "(empty)"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.modified
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        else:
            return datetime.fromtimestamp(self.modified).strftime("%b %d")


@dataclass
class Folder:
    """A note folder."""
    name: str
    parent: str = ""
    created: float = field(default_factory=time.time)
    color: str = "#4A90D9"
    icon: str = "📁"

    @property
    def path(self) -> str:
        if self.parent:
            return f"{self.parent}/{self.name}"
        return self.name


# ─── Note Editor ────────────────────────────────────────────────────────


class NoteEditor:
    """Text editing state for a note."""

    def __init__(self, note: Note):
        self.note = note
        self.cursor_pos: int = len(note.content)
        self.selection_start: int = -1
        self.selection_end: int = -1
        self.scroll_y: int = 0
        self.undo_stack: List[str] = []
        self.redo_stack: List[str] = []
        self._max_undo: int = 100
        self._dirty: bool = False

    def insert(self, text: str) -> None:
        """Insert text at cursor position."""
        self._save_undo()
        content = self.note.content
        self.note.content = content[:self.cursor_pos] + text + content[self.cursor_pos:]
        self.cursor_pos += len(text)
        self.note.modified = time.time()
        self._dirty = True

    def delete_backward(self, count: int = 1) -> str:
        """Delete characters before cursor."""
        if self.cursor_pos <= 0:
            return ""
        self._save_undo()
        start = max(0, self.cursor_pos - count)
        deleted = self.note.content[start:self.cursor_pos]
        self.note.content = self.note.content[:start] + self.note.content[self.cursor_pos:]
        self.cursor_pos = start
        self.note.modified = time.time()
        self._dirty = True
        return deleted

    def delete_forward(self, count: int = 1) -> str:
        """Delete characters after cursor."""
        end = min(len(self.note.content), self.cursor_pos + count)
        self._save_undo()
        deleted = self.note.content[self.cursor_pos:end]
        self.note.content = self.note.content[:self.cursor_pos] + self.note.content[end:]
        self.note.modified = time.time()
        self._dirty = True
        return deleted

    def move_cursor(self, offset: int) -> int:
        """Move cursor by offset."""
        self.cursor_pos = max(0, min(len(self.note.content), self.cursor_pos + offset))
        return self.cursor_pos

    def move_to_start(self) -> int:
        self.cursor_pos = 0
        return 0

    def move_to_end(self) -> int:
        self.cursor_pos = len(self.note.content)
        return self.cursor_pos

    def move_to_line_start(self) -> int:
        content = self.note.content
        pos = self.cursor_pos
        while pos > 0 and content[pos - 1] != "\n":
            pos -= 1
        self.cursor_pos = pos
        return pos

    def move_to_line_end(self) -> int:
        content = self.note.content
        pos = self.cursor_pos
        while pos < len(content) and content[pos] != "\n":
            pos += 1
        self.cursor_pos = pos
        return pos

    def undo(self) -> bool:
        """Undo last change."""
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.note.content)
        self.note.content = self.undo_stack.pop()
        self.note.modified = time.time()
        self._dirty = True
        return True

    def redo(self) -> bool:
        """Redo last undone change."""
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.note.content)
        self.note.content = self.redo_stack.pop()
        self.note.modified = time.time()
        self._dirty = True
        return True

    def _save_undo(self) -> None:
        self.undo_stack.append(self.note.content)
        if len(self.undo_stack) > self._max_undo:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    @property
    def line_col(self) -> Tuple[int, int]:
        """Current line and column (1-indexed)."""
        content = self.note.content[:self.cursor_pos]
        lines = content.split("\n")
        return (len(lines), len(lines[-1]) + 1)


# ─── Notes App ───────────────────────────────────────────────────────────


class NotesApp:
    """
    Note-taking application for Nyrqis OS.

    Manages notes with folders, search, and markdown editing.
    """

    def __init__(self):
        self._notes: List[Note] = []
        self._folders: List[Folder] = [
            Folder("Notes"),
            Folder("Archive"),
            Folder("Trash"),
        ]
        self._current_note: Optional[Note] = None
        self._editor: Optional[NoteEditor] = None
        self._current_folder: str = "Notes"

        # View state
        self._sort_mode: SortMode = SortMode.MODIFIED
        self._search_query: str = ""
        self._search_results: List[Note] = []
        self._show_archived: bool = False
        self._selected_index: int = 0
        self._view_mode: str = "list"  # list, edit, split

        # Filters
        self._filter_tag: str = ""
        self._filter_color: Optional[NoteColor] = None

        # Callbacks
        self._on_save: List[Callable] = []
        self._on_delete: List[Callable] = []

        # Renderer
        self._md = MarkdownRenderer()

        # Initialize with sample notes
        self._init_sample_notes()

    def _init_sample_notes(self) -> None:
        """Create sample notes."""
        samples = [
            ("Welcome to Nyrqis Notes",
             "# Welcome to Nyrqis Notes 🍄\n\n"
             "This is your **markdown-powered** note-taking app.\n\n"
             "## Features\n\n"
             "- Rich markdown editing\n"
             "- Folder organization\n"
             "- Full-text search\n"
             "- Pin important notes\n"
             "- Undo/redo support\n\n"
             "## Keyboard Shortcuts\n\n"
             "| Key | Action |\n"
             "|-----|--------|\n"
             "| Ctrl+S | Save note |\n"
             "| Ctrl+Z | Undo |\n"
             "| Ctrl+F | Search |\n"
             "| Ctrl+N | New note |\n"
             "| Esc | Back to list |\n\n"
             "> Start typing to create your first note!"),
            ("Shopping List",
             "# Shopping List\n\n"
             "- [ ] Mushrooms (button, shiitake, oyster)\n"
             "- [ ] Bread (sourdough)\n"
             "- [ ] Cheese (cheddar, gruyère)\n"
             "- [x] Coffee beans\n"
             "- [ ] Olive oil\n"
             "- [ ] Fresh basil"),
            ("Meeting Notes",
             "## Sprint Planning — Sep 3\n\n"
             "**Attendees:** Alice, Bob, Charlie\n\n"
             "### Action Items\n\n"
             "1. Finish UI components\n"
             "2. Write integration tests\n"
             "3. Update documentation\n\n"
             "### Notes\n\n"
             "The new theme engine is ready for review.\n"
             "Plugin system needs API documentation.\n\n"
             "---\n\n"
             "Next meeting: Friday 2pm"),
            ("Code Snippets",
             "# Code Snippets\n\n"
             "## Python — File Reading\n\n"
             "```python\n"
             "def read_file(path: str) -> str:\n"
             "    with open(path) as f:\n"
             "        return f.read()\n"
             "```\n\n"
             "## Rust — Hello World\n\n"
             "```rust\n"
             "fn main() {\n"
             '    println!("Hello, Nyrqis!");\n'
             "}\n"
             "```\n\n"
             "## Shell — Find Large Files\n\n"
             "```bash\n"
             "find . -size +1M -type f | head -20\n"
             "```"),
            ("Project Ideas",
             "# Project Ideas 💡\n\n"
             "## Nyrqis OS Features\n\n"
             "- [x] Terminal emulator\n"
             "- [x] File manager\n"
             "- [x] Window manager\n"
             "- [ ] Web browser with tabs\n"
             "- [ ] Email client\n"
             "- [ ] Music player\n"
             "- [ ] Video player\n\n"
             "## Mycelium Network\n\n"
             "Decentralized knowledge sharing between Nyrqis instances."),
        ]

        for i, (title, content) in enumerate(samples):
            note = Note(
                title=title,
                content=content,
                folder="Notes",
                pinned=(i == 0),
                created=time.time() - (len(samples) - i) * 3600,
                modified=time.time() - (len(samples) - i) * 1800,
            )
            if i == 0:
                note.color = NoteColor.GREEN
            elif i == 2:
                note.color = NoteColor.BLUE
            self._notes.append(note)

    # ── Note CRUD ─────────────────────────────────────────────────────

    def create_note(self, title: str = "Untitled", content: str = "", folder: str = "") -> Note:
        """Create a new note."""
        note = Note(
            title=title,
            content=content,
            folder=folder or self._current_folder,
        )
        self._notes.append(note)
        return note

    def delete_note(self, note_id: str) -> bool:
        """Move a note to trash (soft delete)."""
        for note in self._notes:
            if note.note_id == note_id:
                note.deleted = True
                note.folder = "Trash"
                if self._current_note and self._current_note.note_id == note_id:
                    self.close_editor()
                self._notify("delete")
                return True
        return False

    def permanently_delete(self, note_id: str) -> bool:
        """Permanently delete a note."""
        for i, note in enumerate(self._notes):
            if note.note_id == note_id and note.deleted:
                self._notes.pop(i)
                return True
        return False

    def restore_note(self, note_id: str) -> bool:
        """Restore a note from trash."""
        for note in self._notes:
            if note.note_id == note_id and note.deleted:
                note.deleted = False
                note.folder = "Notes"
                return True
        return False

    def archive_note(self, note_id: str) -> bool:
        """Archive a note."""
        for note in self._notes:
            if note.note_id == note_id:
                note.archived = True
                note.folder = "Archive"
                return True
        return False

    def unarchive_note(self, note_id: str) -> bool:
        """Unarchive a note."""
        for note in self._notes:
            if note.note_id == note_id and note.archived:
                note.archived = False
                note.folder = "Notes"
                return True
        return False

    def get_note(self, note_id: str) -> Optional[Note]:
        """Get a note by ID."""
        for note in self._notes:
            if note.note_id == note_id:
                return note
        return None

    def duplicate_note(self, note_id: str) -> Optional[Note]:
        """Duplicate a note."""
        original = self.get_note(note_id)
        if not original:
            return None
        return self.create_note(
            title=f"{original.title} (copy)",
            content=original.content,
            folder=original.folder,
        )

    # ── Pinning ───────────────────────────────────────────────────────

    def toggle_pin(self, note_id: str) -> bool:
        """Toggle pin status."""
        note = self.get_note(note_id)
        if note:
            note.pinned = not note.pinned
            return note.pinned
        return False

    @property
    def pinned_notes(self) -> List[Note]:
        return [n for n in self._notes if n.pinned and not n.deleted and not n.archived]

    # ── Tags ──────────────────────────────────────────────────────────

    def add_tag(self, note_id: str, tag: str) -> bool:
        note = self.get_note(note_id)
        if note and tag not in note.tags:
            note.tags.append(tag)
            return True
        return False

    def remove_tag(self, note_id: str, tag: str) -> bool:
        note = self.get_note(note_id)
        if note and tag in note.tags:
            note.tags.remove(tag)
            return True
        return False

    @property
    def all_tags(self) -> List[str]:
        tags = set()
        for note in self._notes:
            tags.update(note.tags)
        return sorted(tags)

    # ── Folders ───────────────────────────────────────────────────────

    def create_folder(self, name: str, parent: str = "") -> Folder:
        folder = Folder(name=name, parent=parent)
        self._folders.append(folder)
        return folder

    def delete_folder(self, name: str) -> bool:
        """Delete a folder (moves notes to Notes)."""
        if name in ("Notes", "Trash", "Archive"):
            return False
        for note in self._notes:
            if note.folder == name:
                note.folder = "Notes"
        self._folders = [f for f in self._folders if f.name != name]
        return True

    @property
    def folders(self) -> List[Folder]:
        return list(self._folders)

    @property
    def folder_names(self) -> List[str]:
        return [f.name for f in self._folders]

    def get_notes_in_folder(self, folder: str = "") -> List[Note]:
        """Get notes in a folder."""
        target = folder or self._current_folder
        return [n for n in self._notes if n.folder == target and not n.deleted]

    @property
    def current_folder(self) -> str:
        return self._current_folder

    def set_folder(self, name: str) -> None:
        self._current_folder = name

    def folder_note_count(self, name: str) -> int:
        return len([n for n in self._notes if n.folder == name and not n.deleted])

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: str) -> List[Note]:
        """Search notes by title and content."""
        self._search_query = query
        if not query:
            self._search_results = []
            return []

        q = query.lower()
        results = []
        for note in self._notes:
            if note.deleted:
                continue
            if (q in note.title.lower() or
                    q in note.content.lower() or
                    any(q in tag.lower() for tag in note.tags)):
                results.append(note)

        # Sort by relevance (title match first)
        def score(n: Note) -> int:
            s = 0
            if q in n.title.lower():
                s += 10
            if q in n.content.lower():
                s += 1
            return -s

        results.sort(key=score)
        self._search_results = results
        return results

    @property
    def search_query(self) -> str:
        return self._search_query

    @property
    def search_results(self) -> List[Note]:
        return list(self._search_results)

    # ── Sorting & Filtering ───────────────────────────────────────────

    @property
    def sort_mode(self) -> SortMode:
        return self._sort_mode

    def set_sort_mode(self, mode: SortMode) -> None:
        self._sort_mode = mode

    def cycle_sort_mode(self) -> SortMode:
        modes = list(SortMode)
        idx = modes.index(self._sort_mode)
        self._sort_mode = modes[(idx + 1) % len(modes)]
        return self._sort_mode

    def set_filter_tag(self, tag: str) -> None:
        self._filter_tag = tag

    def set_filter_color(self, color: Optional[NoteColor]) -> None:
        self._filter_color = color

    def _get_visible_notes(self) -> List[Note]:
        """Get notes visible in the current view."""
        if self._search_query:
            return self._search_results

        notes = [n for n in self._notes
                 if not n.deleted and
                 n.folder == self._current_folder]

        if self._filter_tag:
            notes = [n for n in notes if self._filter_tag in n.tags]
        if self._filter_color:
            notes = [n for n in notes if n.color == self._filter_color]

        # Sort
        if self._sort_mode == SortMode.MODIFIED:
            notes.sort(key=lambda n: -n.modified)
        elif self._sort_mode == SortMode.CREATED:
            notes.sort(key=lambda n: -n.created)
        elif self._sort_mode == SortMode.TITLE:
            notes.sort(key=lambda n: n.title.lower())

        # Pinned first
        pinned = [n for n in notes if n.pinned]
        unpinned = [n for n in notes if not n.pinned]
        return pinned + unpinned

    # ── Editor ────────────────────────────────────────────────────────

    def open_note(self, note_id: str) -> bool:
        """Open a note for editing."""
        note = self.get_note(note_id)
        if not note:
            return False
        self._current_note = note
        self._editor = NoteEditor(note)
        self._view_mode = "edit"
        return True

    def close_editor(self) -> bool:
        """Close the editor."""
        if self._editor and self._editor.is_dirty:
            self._editor.mark_clean()
        self._current_note = None
        self._editor = None
        self._view_mode = "list"
        return True

    @property
    def editor(self) -> Optional[NoteEditor]:
        return self._editor

    @property
    def current_note(self) -> Optional[Note]:
        return self._current_note

    @property
    def view_mode(self) -> str:
        return self._view_mode

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select(self, index: int) -> None:
        notes = self._get_visible_notes()
        self._selected_index = max(0, min(len(notes) - 1, index))

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        notes = self._get_visible_notes()
        self._selected_index = min(len(notes) - 1, self._selected_index + 1)

    def get_selected_note(self) -> Optional[Note]:
        notes = self._get_visible_notes()
        if 0 <= self._selected_index < len(notes):
            return notes[self._selected_index]
        return None

    def open_selected(self) -> bool:
        note = self.get_selected_note()
        if note:
            return self.open_note(note.note_id)
        return False

    # ── Export ────────────────────────────────────────────────────────

    def export_note(self, note_id: str) -> str:
        """Export a note as markdown string."""
        note = self.get_note(note_id)
        if not note:
            return ""
        header = f"# {note.title}\n\n"
        tags = ""
        if note.tags:
            tags = f"Tags: {', '.join(note.tags)}\n\n"
        return header + tags + note.content

    def export_all(self, folder: str = "") -> Dict[str, str]:
        """Export all notes as {title: content} dict."""
        result = {}
        for note in self._notes:
            if note.deleted:
                continue
            if folder and note.folder != folder:
                continue
            result[note.title] = self.export_note(note.note_id)
        return result

    # ── Statistics ────────────────────────────────────────────────────

    @property
    def total_notes(self) -> int:
        return len([n for n in self._notes if not n.deleted])

    @property
    def total_words(self) -> int:
        return sum(n.word_count for n in self._notes if not n.deleted)

    @property
    def total_characters(self) -> int:
        return sum(n.char_count for n in self._notes if not n.deleted)

    @property
    def notes_by_folder(self) -> Dict[str, int]:
        counts = {}
        for note in self._notes:
            if not note.deleted:
                counts[note.folder] = counts.get(note.folder, 0) + 1
        return counts

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_save(self, cb: Callable) -> None:
        self._on_save.append(cb)

    def on_delete(self, cb: Callable) -> None:
        self._on_delete.append(cb)

    def _notify(self, event: str) -> None:
        cbs = {"save": self._on_save, "delete": self._on_delete}
        for cb in cbs.get(event, []):
            try:
                cb()
            except Exception:
                pass

    # ── Rendering ─────────────────────────────────────────────────────

    def render_list(self, width: int = 60) -> List[str]:
        """Render the note list view."""
        lines = []

        # Header
        header = f" 🍄 Nyrqis Notes — {self._current_folder}"
        lines.append(header[:width])
        lines.append(f" {self.total_notes} notes · {self.total_words} words")
        lines.append("─" * width)

        # Search
        if self._search_query:
            lines.append(f" 🔍 \"{self._search_query}\" ({len(self._search_results)} results)")
            lines.append("─" * width)

        # Notes
        notes = self._get_visible_notes()
        if not notes:
            lines.append("")
            lines.append("  No notes yet. Press Ctrl+N to create one.")
        else:
            for i, note in enumerate(notes):
                marker = "▸" if i == self._selected_index else " "
                pin = "📌" if note.pinned else "  "
                color_icon = {
                    NoteColor.YELLOW: "🟡", NoteColor.GREEN: "🟢",
                    NoteColor.BLUE: "🔵", NoteColor.PURPLE: "🟣",
                    NoteColor.PINK: "🩷", NoteColor.ORANGE: "🟠",
                }.get(note.color, "  ")

                line = f"{marker}{pin}{color_icon} {note.title[:width - 10]}"
                lines.append(line[:width])

                # Preview
                preview = f"   {note.preview[:width - 4]}"
                lines.append(preview[:width])

                # Meta
                meta = f"   {note.time_ago} · {note.word_count} words"
                lines.append(meta[:width])
                lines.append("")

        # Footer
        lines.append("─" * width)
        lines.append(" Ctrl+N:New  Enter:Open  Del:Delete  /:Search")

        return lines

    def render_editor(self, width: int = 60, height: int = 20) -> List[str]:
        """Render the note editor view."""
        lines = []

        if not self._current_note or not self._editor:
            return ["No note open"]

        note = self._current_note

        # Title bar
        title = f" 📝 {note.title}"
        if self._editor.is_dirty:
            title += " •"
        lines.append(title[:width])

        # Markdown rendered content
        rendered = self._md.render(note.content, width)
        content_lines = rendered.split("\n")

        # Scroll
        start = max(0, self._editor.scroll_y)
        visible = content_lines[start:start + height - 2]

        # Pad
        while len(visible) < height - 2:
            visible.append("")

        lines.extend(visible)

        # Status bar
        line, col = self._editor.line_col
        status = f" Ln {line}, Col {col} | {note.word_count} words | {note.char_count} chars"
        if note.tags:
            status += f" | {', '.join(note.tags)}"
        lines.append("─" * width)
        lines.append(status[:width])

        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        """Render the current view."""
        if self._view_mode == "edit":
            return self.render_editor(width, height - 1)
        return self.render_list(width)

    def handle_key(self, key: str) -> Optional[str]:
        """Handle keyboard input."""
        if self._view_mode == "edit" and self._editor:
            return self._handle_editor_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_selected()
            return "open"
        elif key == "Ctrl+n":
            note = self.create_note()
            self.open_note(note.note_id)
            return "new"
        elif key == "/":
            self._search_query = ""
            return "search_focus"
        elif key == "Ctrl+f":
            return "search_focus"
        elif key == "Delete":
            note = self.get_selected_note()
            if note:
                self.delete_note(note.note_id)
            return "delete"
        elif key == "Ctrl+p":
            note = self.get_selected_note()
            if note:
                self.toggle_pin(note.note_id)
            return "toggle_pin"
        elif key == "Ctrl+d":
            note = self.get_selected_note()
            if note:
                self.duplicate_note(note.note_id)
            return "duplicate"
        elif key == "1":
            self.set_folder("Notes")
            return "folder_notes"
        elif key == "2":
            self.set_folder("Archive")
            return "folder_archive"
        elif key == "3":
            self.set_folder("Trash")
            return "folder_trash"
        elif key == "Tab":
            self.cycle_sort_mode()
            return "sort"
        return None

    def _handle_editor_key(self, key: str) -> Optional[str]:
        editor = self._editor
        if not editor:
            return None

        if key == "Escape":
            self.close_editor()
            return "close_editor"
        elif key == "Ctrl+s":
            editor.mark_clean()
            self._notify("save")
            return "save"
        elif key == "Ctrl+z":
            editor.undo()
            return "undo"
        elif key == "Ctrl+y":
            editor.redo()
            return "redo"
        elif key == "ArrowLeft":
            editor.move_cursor(-1)
            return "cursor_left"
        elif key == "ArrowRight":
            editor.move_cursor(1)
            return "cursor_right"
        elif key == "ArrowUp":
            # Move up one line
            editor.move_to_line_start()
            editor.move_cursor(-1)
            editor.move_to_line_start()
            return "cursor_up"
        elif key == "ArrowDown":
            editor.move_to_line_end()
            editor.move_cursor(1)
            return "cursor_down"
        elif key == "Home":
            editor.move_to_line_start()
            return "cursor_home"
        elif key == "End":
            editor.move_to_line_end()
            return "cursor_end"
        elif key == "Backspace":
            editor.delete_backward()
            return "backspace"
        elif key == "Delete":
            editor.delete_forward()
            return "delete"
        elif key == "Enter":
            # Auto-indent
            content = editor.note.content
            pos = editor.cursor_pos
            line_start = content.rfind("\n", 0, pos) + 1
            line = content[line_start:pos]
            indent = ""
            for ch in line:
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break
            editor.insert("\n" + indent)
            return "enter"
        elif key == "Tab":
            editor.insert("    ")
            return "tab"
        elif len(key) == 1:
            editor.insert(key)
            return "insert"
        return None

    # ── Color ─────────────────────────────────────────────────────────

    def set_note_color(self, note_id: str, color: NoteColor) -> bool:
        note = self.get_note(note_id)
        if note:
            note.color = color
            return True
        return False
