#!/usr/bin/env python3
"""text_editor — Nyrqis basic text editor application.

A simple text editor that demonstrates file I/O, text manipulation,
and basic syntax highlighting for the Nyrqis desktop.

Features:
- Open, edit, and save text files
- Basic syntax highlighting (Python, Rust, C++, JSON, Markdown)
- Line numbers
- Undo/redo
- Find/replace
- File status tracking (modified, saved)

This runs as a NUI-compatible app within a DesktopSession window.

Usage::

    from ui.text_editor import TextEditor
    from ui.desktop_session import DesktopSession
    from ui.nstudio import loads

    doc = loads(json.dumps(shell_json))
    session = DesktopSession(doc)
    editor = TextEditor(session)
    editor.open_file('/path/to/file.py')
    editor.insert_text('Hello, world!')
    editor.save()

Architecture:
    The text editor maintains a buffer (list of lines) and provides
    cursor-based editing operations.  It can render its content as
    a PIL Image for the compositor.

References:
    - NFS-001 §5: component vocabulary (CodeEditor)
    - doc #14: Nyrqis Desktop Shell
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# PIL imported lazily to avoid 5-15s import penalty in containers.
_PIL_AVAILABLE: Optional[bool] = None


def _ensure_pil():
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is not None:
        if _PIL_AVAILABLE is False:
            raise ImportError("PIL/Pillow is required: pip install Pillow")
        return
    try:
        from PIL import Image as _Img  # noqa: F401
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False
        raise ImportError("PIL/Pillow is required: pip install Pillow")


def _pil():
    _ensure_pil()
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------

SYNTAX_RULES: Dict[str, List[Dict[str, Any]]] = {
    "python": [
        {"pattern": r"#.*$", "color": (106, 153, 85)},     # comments (green)
        {"pattern": r"\"\"\".*?\"\"\"", "color": (206, 145, 120)},  # docstrings
        {"pattern": r"\"[^\"]*\"", "color": (206, 145, 120)},
        {"pattern": r"'[^']*'", "color": (206, 145, 120)},
        {"pattern": r"\b(def|class|import|from|return|if|else|elif|for|while|try|except|finally|with|as|yield|lambda|pass|break|continue|raise|assert|global|nonlocal|del|in|not|and|or|is|None|True|False)\b",
         "color": (197, 134, 230)},  # keywords (purple)
    ],
    "rust": [
        {"pattern": r"//.*$", "color": (106, 153, 85)},
        {"pattern": r"\"[^\"]*\"", "color": (206, 145, 120)},
        {"pattern": r"\b(fn|let|mut|pub|struct|enum|impl|trait|use|mod|crate|self|super|match|if|else|loop|while|for|return|break|continue|move|ref|unsafe|async|await|where|type|const|static|extern|as)\b",
         "color": (197, 134, 230)},
    ],
    "cpp": [
        {"pattern": r"//.*$", "color": (106, 153, 85)},
        {"pattern": r"/\*.*?\*/", "color": (106, 153, 85)},
        {"pattern": r"\"[^\"]*\"", "color": (206, 145, 120)},
        {"pattern": r"\b(class|struct|namespace|using|template|typename|auto|void|int|float|double|bool|char|string|return|if|else|for|while|do|switch|case|break|continue|new|delete|public|private|protected|virtual|override|const|static|extern|inline|constexpr|noexcept)\b",
         "color": (197, 134, 230)},
    ],
    "json": [
        {"pattern": r"\"[^\"]*\"\s*:", "color": (134, 188, 230)},  # keys
        {"pattern": r":\s*\"[^\"]*\"", "color": (206, 145, 120)},  # string values
        {"pattern": r":\s*\b(true|false|null)\b", "color": (197, 134, 230)},
        {"pattern": r":\s*\d+\.?\d*", "color": (181, 137, 0)},  # numbers
    ],
    "markdown": [
        {"pattern": r"^#{1,6}\s.*$", "color": (197, 134, 230)},  # headings
        {"pattern": r"\*\*.*?\*\*", "color": (230, 230, 230)},
        {"pattern": r"\*.*?\*", "color": (150, 150, 150)},
        {"pattern": r"`[^`]+`", "color": (206, 145, 120)},
        {"pattern": r"```", "color": (106, 153, 85)},
    ],
}


def _detect_language(filename: str) -> str:
    """Detect the language from a filename extension."""
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".py": "python", ".pyw": "python",
        ".rs": "rust",
        ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".h": "cpp", ".hpp": "cpp",
        ".c": "cpp",
        ".json": "json",
        ".md": "markdown", ".markdown": "markdown",
    }.get(ext, "text")


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------

@dataclass
class Cursor:
    """Text cursor position."""
    line: int = 0       # 0-indexed line number
    column: int = 0     # 0-indexed column

    def clamp(self, lines: List[str]) -> None:
        """Clamp the cursor to valid positions."""
        self.line = max(0, min(self.line, len(lines) - 1))
        if lines:
            self.column = max(0, min(self.column, len(lines[self.line])))

    def move_left(self, lines: List[str]) -> None:
        if self.column > 0:
            self.column -= 1
        elif self.line > 0:
            self.line -= 1
            self.column = len(lines[self.line]) if lines else 0

    def move_right(self, lines: List[str]) -> None:
        if lines and self.column < len(lines[self.line]):
            self.column += 1
        elif self.line < len(lines) - 1:
            self.line += 1
            self.column = 0

    def move_up(self) -> None:
        if self.line > 0:
            self.line -= 1

    def move_down(self, lines: List[str]) -> None:
        if self.line < len(lines) - 1:
            self.line += 1


# ---------------------------------------------------------------------------
# Undo/Redo
# ---------------------------------------------------------------------------

@dataclass
class EditAction:
    """A single undoable edit action."""
    type: str           # 'insert', 'delete', 'replace'
    line: int
    column: int
    old_text: str = ""
    new_text: str = ""


# ---------------------------------------------------------------------------
# TextEditor
# ---------------------------------------------------------------------------

class TextEditor:
    """Basic text editor for the Nyrqis desktop.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session (for integration with the shell).
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._lines: List[str] = [""]
        self._cursor = Cursor(0, 0)
        self._selection_start: Optional[Cursor] = None
        self._selection_end: Optional[Cursor] = None
        self._filename: Optional[str] = None
        self._language: str = "text"
        self._modified: bool = False
        self._undo_stack: List[EditAction] = []
        self._redo_stack: List[EditAction] = []
        self._tab_size: int = 4
        self._word_wrap: bool = True
        self._visible: bool = False
        self._callbacks: List[Callable] = []
        self._font_size: int = 14
        self._line_numbers: bool = True

    # -- File operations ----------------------------------------------

    def open_file(self, path: str) -> bool:
        """Open a text file for editing.

        Returns True on success, False on error.
        """
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self._lines = content.split("\n")
            if not self._lines:
                self._lines = [""]
            self._cursor = Cursor(0, 0)
            self._filename = path
            self._language = _detect_language(path)
            self._modified = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._log(f"Opened {path} ({len(self._lines)} lines, {self._language})")
            self._notify("open", path)
            return True
        except Exception as e:
            self._log(f"Failed to open {path}: {e}")
            return False

    def save(self, path: Optional[str] = None) -> bool:
        """Save the current buffer to a file.

        Parameters
        ----------
        path : str, optional
            Destination path.  If None, saves to the current filename.
            If no current filename, returns False.

        Returns True on success.
        """
        dest = path or self._filename
        if not dest:
            self._log("No filename to save to")
            return False
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write("\n".join(self._lines))
            self._filename = dest
            self._language = _detect_language(dest)
            self._modified = False
            self._log(f"Saved to {dest}")
            self._notify("save", dest)
            return True
        except Exception as e:
            self._log(f"Failed to save {dest}: {e}")
            return False

    def new_file(self) -> None:
        """Create a new empty buffer."""
        self._lines = [""]
        self._cursor = Cursor(0, 0)
        self._filename = None
        self._language = "text"
        self._modified = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._log("New file")

    # -- Editing operations -------------------------------------------

    def insert_text(self, text: str) -> None:
        """Insert text at the cursor position."""
        action = EditAction(
            type="insert",
            line=self._cursor.line,
            column=self._cursor.column,
            new_text=text,
        )

        if "\n" in text:
            # Multi-line insert
            parts = text.split("\n")
            current_line = self._lines[self._cursor.line]
            before = current_line[:self._cursor.column]
            after = current_line[self._cursor.column:]

            self._lines[self._cursor.line] = before + parts[0]
            for i, part in enumerate(parts[1:], 1):
                if i == len(parts) - 1:
                    self._lines.insert(self._cursor.line + i, part + after)
                else:
                    self._lines.insert(self._cursor.line + i, part)

            self._cursor.line += len(parts) - 1
            self._cursor.column = len(parts[-1]) if len(parts) > 1 else len(before) + len(parts[0])
        else:
            # Single-line insert
            line = self._lines[self._cursor.line]
            self._lines[self._cursor.line] = (
                line[:self._cursor.column] + text + line[self._cursor.column:]
            )
            self._cursor.column += len(text)

        self._modified = True
        self._undo_stack.append(action)
        self._redo_stack.clear()
        self._notify("edit", "insert")

    def delete_char(self, forward: bool = True) -> None:
        """Delete a character at the cursor (forward or backward)."""
        if forward:
            if self._cursor.column < len(self._lines[self._cursor.line]):
                line = self._lines[self._cursor.line]
                deleted = line[self._cursor.column]
                self._lines[self._cursor.line] = (
                    line[:self._cursor.column] + line[self._cursor.column + 1:]
                )
                self._undo_stack.append(EditAction(
                    type="delete", line=self._cursor.line,
                    column=self._cursor.column, old_text=deleted,
                ))
            elif self._cursor.line < len(self._lines) - 1:
                # Merge with next line
                current = self._lines[self._cursor.line]
                next_line = self._lines[self._cursor.line + 1]
                self._lines[self._cursor.line] = current + next_line
                self._lines.pop(self._cursor.line + 1)
                self._undo_stack.append(EditAction(
                    type="delete", line=self._cursor.line,
                    column=self._cursor.column, old_text="\n",
                ))
        else:
            if self._cursor.column > 0:
                line = self._lines[self._cursor.line]
                deleted = line[self._cursor.column - 1]
                self._lines[self._cursor.line] = (
                    line[:self._cursor.column - 1] + line[self._cursor.column:]
                )
                self._cursor.column -= 1
                self._undo_stack.append(EditAction(
                    type="delete", line=self._cursor.line,
                    column=self._cursor.column, old_text=deleted,
                ))
            elif self._cursor.line > 0:
                # Merge with previous line
                prev_line = self._lines[self._cursor.line - 1]
                current = self._lines[self._cursor.line]
                self._cursor.column = len(prev_line)
                self._lines[self._cursor.line - 1] = prev_line + current
                self._lines.pop(self._cursor.line)
                self._cursor.line -= 1
                self._undo_stack.append(EditAction(
                    type="delete", line=self._cursor.line,
                    column=self._cursor.column, old_text="\n",
                ))

        self._modified = True
        self._redo_stack.clear()
        self._notify("edit", "delete")

    def insert_newline(self) -> None:
        """Insert a newline at the cursor, preserving indentation."""
        current = self._lines[self._cursor.line]
        # Get leading whitespace
        indent = ""
        for ch in current:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break

        line = self._lines[self._cursor.line]
        before = line[:self._cursor.column]
        after = line[self._cursor.column:]

        self._lines[self._cursor.line] = before
        self._lines.insert(self._cursor.line + 1, indent + after)

        self._cursor.line += 1
        self._cursor.column = len(indent)

        self._modified = True
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify("edit", "newline")

    def insert_tab(self) -> None:
        """Insert a tab (spaces) at the cursor."""
        spaces = " " * self._tab_size
        self.insert_text(spaces)

    # -- Undo/Redo ----------------------------------------------------

    def undo(self) -> bool:
        """Undo the last edit. Returns True if something was undone."""
        if not self._undo_stack:
            return False
        action = self._undo_stack.pop()

        if action.type == "insert":
            # Reverse an insert = delete the inserted text
            line = self._lines[action.line]
            self._lines[action.line] = (
                line[:action.column] + line[action.column + len(action.new_text):]
            )
            self._cursor.line = action.line
            self._cursor.column = action.column
        elif action.type == "delete":
            # Reverse a delete = re-insert the deleted text
            line = self._lines[action.line]
            self._lines[action.line] = (
                line[:action.column] + action.old_text + line[action.column:]
            )
            self._cursor.line = action.line
            self._cursor.column = action.column + len(action.old_text)

        self._redo_stack.append(action)
        self._modified = bool(self._undo_stack)
        self._notify("edit", "undo")
        return True

    def redo(self) -> bool:
        """Redo the last undone edit. Returns True if something was redone."""
        if not self._redo_stack:
            return False
        action = self._redo_stack.pop()

        if action.type == "insert":
            line = self._lines[action.line]
            self._lines[action.line] = (
                line[:action.column] + action.new_text + line[action.column:]
            )
            self._cursor.line = action.line
            self._cursor.column = action.column + len(action.new_text)
        elif action.type == "delete":
            line = self._lines[action.line]
            self._lines[action.line] = (
                line[:action.column] + line[action.column + len(action.old_text):]
            )
            self._cursor.line = action.line
            self._cursor.column = action.column

        self._undo_stack.append(action)
        self._modified = True
        self._notify("edit", "redo")
        return True

    # -- Find/Replace -------------------------------------------------

    def find(self, query: str, case_sensitive: bool = False) -> List[Tuple[int, int]]:
        """Find all occurrences of a query string.

        Returns a list of (line, column) positions.
        """
        results = []
        for i, line in enumerate(self._lines):
            search_line = line if case_sensitive else line.lower()
            search_query = query if case_sensitive else query.lower()
            col = 0
            while True:
                idx = search_line.find(search_query, col)
                if idx == -1:
                    break
                results.append((i, idx))
                col = idx + 1
        return results

    def find_next(self, query: str, case_sensitive: bool = False) -> Optional[Tuple[int, int]]:
        """Find the next occurrence after the cursor."""
        results = self.find(query, case_sensitive)
        for line, col in results:
            if (line, col) > (self._cursor.line, self._cursor.column):
                return (line, col)
        # Wrap to beginning
        if results:
            return results[0]
        return None

    def replace(
        self,
        find: str,
        replacement: str,
        case_sensitive: bool = False,
        all_occurrences: bool = False,
    ) -> int:
        """Find and replace text.

        Returns the number of replacements made.
        """
        count = 0
        for i, line in enumerate(self._lines):
            if case_sensitive:
                new_line = line.replace(find, replacement)
            else:
                import re
                pattern = re.compile(re.escape(find), re.IGNORECASE)
                new_line = pattern.sub(replacement, line)
            if new_line != line:
                self._lines[i] = new_line
                count += 1
                if not all_occurrences:
                    break
        if count > 0:
            self._modified = True
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._notify("edit", "replace")
        return count

    # -- Cursor movement ----------------------------------------------

    def move_cursor(self, line: int, column: int) -> None:
        """Move the cursor to a specific position."""
        self._cursor.line = line
        self._cursor.column = column
        self._cursor.clamp(self._lines)

    def move_cursor_left(self) -> None:
        self._cursor.move_left(self._lines)

    def move_cursor_right(self) -> None:
        self._cursor.move_right(self._lines)

    def move_cursor_up(self) -> None:
        self._cursor.move_up()

    def move_cursor_down(self) -> None:
        self._cursor.move_down(self._lines)

    def move_to_line_start(self) -> None:
        self._cursor.column = 0

    def move_to_line_end(self) -> None:
        if self._lines:
            self._cursor.column = len(self._lines[self._cursor.line])

    def move_to_file_start(self) -> None:
        self._cursor.line = 0
        self._cursor.column = 0

    def move_to_file_end(self) -> None:
        self._cursor.line = len(self._lines) - 1
        self.move_to_line_end()

    # -- Selection ----------------------------------------------------

    def start_selection(self) -> None:
        """Start a selection at the current cursor position."""
        self._selection_start = Cursor(self._cursor.line, self._cursor.column)

    def end_selection(self) -> None:
        """End the selection at the current cursor position."""
        self._selection_end = Cursor(self._cursor.line, self._cursor.column)

    def get_selection(self) -> Optional[Tuple[int, int, int, int]]:
        """Get the selected region as (start_line, start_col, end_line, end_col).
        Returns None if no selection.
        """
        if not self._selection_start or not self._selection_end:
            return None
        s, e = self._selection_start, self._selection_end
        if (s.line, s.column) <= (e.line, e.column):
            return (s.line, s.column, e.line, e.column)
        return (e.line, e.column, s.line, s.column)

    def delete_selection(self) -> str:
        """Delete the selected text and return it."""
        sel = self.get_selection()
        if not sel:
            return ""
        sl, sc, el, ec = sel
        if sl == el:
            # Same line
            line = self._lines[sl]
            deleted = line[sc:ec]
            self._lines[sl] = line[:sc] + line[ec:]
        else:
            # Multi-line
            deleted = (
                self._lines[sl][sc:] + "\n" +
                "\n".join(self._lines[sl + 1:el]) + "\n" +
                self._lines[el][:ec]
            )
            self._lines[sl] = self._lines[sl][:sc] + self._lines[el][ec:]
            del self._lines[sl + 1:el + 1]

        self._cursor.line = sl
        self._cursor.column = sc
        self._selection_start = None
        self._selection_end = None
        self._modified = True
        return deleted

    @property
    def has_selection(self) -> bool:
        return self._selection_start is not None and self._selection_end is not None

    # -- Properties ---------------------------------------------------

    @property
    def lines(self) -> List[str]:
        return list(self._lines)

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    @text.setter
    def text(self, value: str) -> None:
        self._lines = value.split("\n")
        self._cursor = Cursor(0, 0)
        self._modified = True

    @property
    def cursor(self) -> Cursor:
        return self._cursor

    @property
    def filename(self) -> Optional[str]:
        return self._filename

    @property
    def language(self) -> str:
        return self._language

    @property
    def modified(self) -> bool:
        return self._modified

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self._lines)

    @property
    def word_count(self) -> int:
        return sum(len(line.split()) for line in self._lines)

    @property
    def tab_size(self) -> int:
        return self._tab_size

    @tab_size.setter
    def tab_size(self, value: int) -> None:
        self._tab_size = max(1, min(8, value))

    @property
    def word_wrap(self) -> bool:
        return self._word_wrap

    @word_wrap.setter
    def word_wrap(self, value: bool) -> None:
        self._word_wrap = value

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def undo_count(self) -> int:
        return len(self._undo_stack)

    @property
    def redo_count(self) -> int:
        return len(self._redo_stack)

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    # -- Rendering (for compositor) -----------------------------------

    def render(
        self,
        width: int = 800,
        height: int = 600,
        theme: Optional[Dict] = None,
    ) -> Image.Image:
        """Render the editor content to a PIL Image.

        Parameters
        ----------
        width, height : int
            Image dimensions.
        theme : dict, optional
            Color theme.  Defaults to Eclipse dark theme.
        """
        Image, ImageDraw, ImageFont = _pil()
        if theme is None:
            theme = {
                "background": (30, 30, 30),
                "surface": (40, 40, 40),
                "text_primary": (230, 230, 230),
                "text_secondary": (150, 150, 150),
                "accent": (100, 149, 237),
                "line_number_bg": (35, 35, 35),
                "line_number_fg": (100, 100, 100),
                "selection_bg": (50, 70, 100),
                "cursor_color": (230, 230, 230),
            }

        img = Image.new("RGB", (width, height), theme["background"])
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                self._font_size)
            font_line_num = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                self._font_size - 2)
        except (OSError, IOError):
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    self._font_size)
                font_line_num = font
            except (OSError, IOError):
                font = ImageFont.load_default()
                font_line_num = font

        line_height = self._font_size + 4
        gutter_width = 60 if self._line_numbers else 0

        # Title bar
        title_h = 32
        draw.rectangle([0, 0, width, title_h], fill=theme["surface"])
        title = os.path.basename(self._filename) if self._filename else "Untitled"
        if self._modified:
            title += " •"
        draw.text((12, 8), title, fill=theme["text_primary"], font=font)
        # Language indicator
        draw.text((width - 100, 8), self._language.upper(),
                  fill=theme["text_secondary"], font=font_line_num)

        # Gutter
        if self._line_numbers:
            draw.rectangle([0, title_h, gutter_width, height],
                           fill=theme["line_number_bg"])

        # Render lines
        visible_lines = (height - title_h) // line_height
        start_line = max(0, self._cursor.line - visible_lines // 2)

        for i in range(visible_lines):
            line_idx = start_line + i
            if line_idx >= len(self._lines):
                break

            y = title_h + i * line_height
            line = self._lines[line_idx]

            # Line number
            if self._line_numbers:
                line_num = str(line_idx + 1)
                color = (theme["accent"] if line_idx == self._cursor.line
                         else theme["line_number_fg"])
                draw.text((gutter_width - 40, y + 2), line_num.rjust(4),
                          fill=color, font=font_line_num)

            # Line content (simplified — no real regex highlighting in render)
            draw.text((gutter_width + 8, y + 2), line[:80],
                      fill=theme["text_primary"], font=font)

            # Cursor line highlight
            if line_idx == self._cursor.line:
                draw.rectangle(
                    [gutter_width, y, width, y + line_height],
                    fill=(40, 40, 50))

        # Cursor
        cursor_y = title_h + (self._cursor.line - start_line) * line_height
        cursor_x = gutter_width + 8 + self._cursor.column * 8  # approximate
        draw.rectangle(
            [cursor_x, cursor_y, cursor_x + 2, cursor_y + line_height],
            fill=theme["cursor_color"])

        # Status bar
        status_h = 24
        draw.rectangle([0, height - status_h, width, height],
                       fill=theme["surface"])
        status = f"Ln {self._cursor.line + 1}, Col {self._cursor.column + 1}"
        status += f"  |  {self.line_count} lines  |  {self.word_count} words"
        draw.text((8, height - status_h + 6), status,
                  fill=theme["text_secondary"], font=font_line_num)

        return img

    # -- Callbacks ----------------------------------------------------

    def on_edit(self, callback: Callable) -> None:
        """Register a callback for edit events."""
        self._callbacks.append(callback)

    # -- Internal -----------------------------------------------------

    def _notify(self, event: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event, data)
            except Exception as e:
                self._log(f"Callback error: {e}")

    def _log(self, msg: str) -> None:
        logger.info("[TextEditor] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the text editor standalone (for testing)."""
    editor = TextEditor()

    print("=== Nyrqis Text Editor ===")

    # New file
    editor.new_file()
    print(f"New file: {editor.filename}")

    # Insert text
    editor.insert_text("def hello():\n")
    editor.insert_text("    print('Hello, Nyrqis!')\n")
    editor.insert_text("\n")
    editor.insert_text("if __name__ == '__main__':\n")
    editor.insert_text("    hello()\n")

    print(f"Lines: {editor.line_count}")
    print(f"Words: {editor.word_count}")
    print(f"Chars: {editor.char_count}")
    print(f"Language: {editor.language}")
    print(f"Modified: {editor.modified}")

    # Find
    results = editor.find("hello")
    print(f"Found 'hello' at {len(results)} locations")

    # Undo
    editor.undo()
    print(f"After undo: {editor.line_count} lines, undo stack: {editor.undo_count}")

    # Redo
    editor.redo()
    print(f"After redo: {editor.line_count} lines, redo stack: {editor.redo_count}")

    # Replace
    count = editor.replace("hello", "world")
    print(f"Replaced {count} occurrences")

    # Cursor movement
    editor.move_to_file_start()
    print(f"Cursor at start: Ln {editor.cursor.line + 1}, Col {editor.cursor.column + 1}")

    editor.move_to_file_end()
    print(f"Cursor at end: Ln {editor.cursor.line + 1}, Col {editor.cursor.column + 1}")

    # Render
    img = editor.render(800, 600)
    print(f"Rendered: {img.size}")

    print("\nAll text editor operations passed!")


if __name__ == "__main__":
    main()
