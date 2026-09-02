#!/usr/bin/env python3
"""editor_enhanced — Enhanced text editor with tabbed editing.

Extended version of the base TextEditor with:
- Tab-based editing with dirty indicators
- Line numbers with current line highlight
- Syntax highlighting (Python, JS, C, Rust, Shell, JSON, Markdown)
- Find and replace (regex support)
- Undo/redo history
- Word wrap toggle
- Go to line
- Auto-indent
- Bracket matching
- File save/load
- Multiple file tabs
- Current position: line:col display
"""

from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------

class SyntaxLanguage(Enum):
    NONE = "text"
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    C = "c"
    RUST = "rust"
    SHELL = "shell"
    JSON = "json"
    MARKDOWN = "markdown"


_EXT_MAP = {
    ".py": SyntaxLanguage.PYTHON,
    ".js": SyntaxLanguage.JAVASCRIPT,
    ".jsx": SyntaxLanguage.JAVASCRIPT,
    ".ts": SyntaxLanguage.JAVASCRIPT,
    ".c": SyntaxLanguage.C,
    ".h": SyntaxLanguage.C,
    ".rs": SyntaxLanguage.RUST,
    ".sh": SyntaxLanguage.SHELL,
    ".bash": SyntaxLanguage.SHELL,
    ".json": SyntaxLanguage.JSON,
    ".md": SyntaxLanguage.MARKDOWN,
}

_SYNTAX_RULES: Dict[SyntaxLanguage, List[Tuple[str, Tuple[int, int, int]]]] = {
    SyntaxLanguage.PYTHON: [
        (r"#.*$", (100, 120, 140)),
        (r'""".*?"""', (160, 140, 200)),
        (r"'''.*?'''", (160, 140, 200)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"'[^']*'", (160, 200, 120)),
        (r"\b(def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|yield|lambda|pass|break|continue|and|or|not|in|is|True|False|None|self|async|await)\b",
         (200, 130, 80)),
        (r"\b(print|len|range|enumerate|zip|map|filter|sorted|reversed|type|int|str|float|bool)\b",
         (80, 180, 220)),
        (r"\b\d+\.?\d*\b", (200, 160, 100)),
    ],
    SyntaxLanguage.JAVASCRIPT: [
        (r"//.*$", (100, 120, 140)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"'[^']*'", (160, 200, 120)),
        (r"\b(function|const|let|var|return|if|else|for|while|class|import|export|async|await|try|catch|null|undefined|true|false)\b",
         (200, 130, 80)),
        (r"\b\d+\.?\d*\b", (200, 160, 100)),
    ],
    SyntaxLanguage.RUST: [
        (r"//.*$", (100, 120, 140)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"\b(fn|let|mut|pub|struct|enum|impl|trait|use|mod|match|if|else|for|while|loop|break|continue|return|move|ref|as|where|async|await|type|const|Self)\b",
         (200, 130, 80)),
        (r"\b(String|Vec|Option|Result|Box|HashMap|i32|u32|f64|bool|str|Some|None|Ok|Err|println!)\b",
         (80, 180, 220)),
        (r"\b\d+\.?\d*\b", (200, 160, 100)),
    ],
    SyntaxLanguage.SHELL: [
        (r"#.*$", (100, 120, 140)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"'[^']*'", (160, 200, 120)),
        (r"\b(if|then|else|elif|fi|for|do|done|while|case|esac|function|return|exit|echo|export|source|local|declare|set|unset)\b",
         (200, 130, 80)),
        (r"\$\{?\w+\}?", (80, 180, 220)),
    ],
    SyntaxLanguage.JSON: [
        (r'"[^"]*"(?=\s*:)', (100, 180, 220)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"\b(true|false|null)\b", (200, 130, 80)),
        (r"\b\d+\.?\d*\b", (200, 160, 100)),
    ],
    SyntaxLanguage.C: [
        (r"//.*$", (100, 120, 140)),
        (r'"[^"]*"', (160, 200, 120)),
        (r"\b(int|char|float|double|void|long|unsigned|const|static|struct|enum|typedef|if|else|for|while|do|switch|case|break|continue|return|sizeof|NULL|true|false)\b",
         (200, 130, 80)),
        (r"#\w+", (180, 140, 200)),
        (r"\b\d+\.?\d*\b", (200, 160, 100)),
    ],
    SyntaxLanguage.MARKDOWN: [
        (r"^#{1,6}\s.*$", (200, 180, 140)),
        (r"`[^`]+`", (160, 200, 120)),
        (r"\*\*[^*]+\*\*", (220, 200, 160)),
    ],
}


def detect_language(filepath: str) -> SyntaxLanguage:
    _, ext = os.path.splitext(filepath)
    return _EXT_MAP.get(ext.lower(), SyntaxLanguage.NONE)


def highlight_line(text: str, language: SyntaxLanguage) -> List[Tuple[str, Tuple[int, int, int]]]:
    if language == SyntaxLanguage.NONE or language not in _SYNTAX_RULES:
        return [(text, (200, 200, 200))]

    rules = _SYNTAX_RULES[language]
    spans = []
    remaining = text

    for pattern, color in rules:
        new_remaining = ""
        pos = 0
        for match in re.finditer(pattern, remaining):
            start, end = match.start(), match.end()
            if start > pos:
                spans.append((remaining[pos:start], (200, 200, 200)))
            spans.append((remaining[start:end], color))
            pos = end
        remaining = remaining[pos:]

    if remaining:
        spans.append((remaining, (200, 200, 200)))

    return spans if spans else [(text, (200, 200, 200))]


# ---------------------------------------------------------------------------
# Editor data types
# ---------------------------------------------------------------------------

@dataclass
class CursorPosition:
    line: int = 0
    col: int = 0


@dataclass
class Selection:
    start: CursorPosition = field(default_factory=CursorPosition)
    end: CursorPosition = field(default_factory=CursorPosition)
    active: bool = False


@dataclass
class EditorTab:
    """A file tab in the editor."""
    id: str
    name: str
    filepath: str = ""
    lines: List[str] = field(default_factory=lambda: [""])
    language: SyntaxLanguage = SyntaxLanguage.NONE
    cursor: CursorPosition = field(default_factory=CursorPosition)
    selection: Selection = field(default_factory=Selection)
    dirty: bool = False
    undo_stack: List[List[str]] = field(default_factory=list)
    redo_stack: List[List[str]] = field(default_factory=list)
    scroll_y: int = 0
    word_wrap: bool = True
    show_line_numbers: bool = True
    tab_size: int = 4

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def display_name(self) -> str:
        suffix = " ●" if self.dirty else ""
        return f"{self.name}{suffix}"

    @property
    def current_line(self) -> str:
        if 0 <= self.cursor.line < len(self.lines):
            return self.lines[self.cursor.line]
        return ""


@dataclass
class FindResult:
    line: int
    col: int
    length: int
    text: str


# ---------------------------------------------------------------------------
# Enhanced text editor
# ---------------------------------------------------------------------------

class EnhancedTextEditor:
    """Tabbed text editor with syntax highlighting."""

    def __init__(self, session=None) -> None:
        self._session = session
        self._tabs: Dict[str, EditorTab] = {}
        self._active_tab_id: Optional[str] = None
        self._visible = False
        self._tab_counter = 0
        self._callbacks: List[Callable] = []

        # Find/replace
        self._find_query: str = ""
        self._find_results: List[FindResult] = []
        self._find_index: int = -1
        self._show_find: bool = False

    # -- Tab management ------------------------------------------------

    def open_file(self, filepath: str) -> Optional[EditorTab]:
        for tab in self._tabs.values():
            if tab.filepath == filepath:
                self._active_tab_id = tab.id
                return tab
        try:
            with open(filepath, "r", errors="replace") as f:
                content = f.read()
        except OSError:
            return None

        self._tab_counter += 1
        tab_id = f"editor-{self._tab_counter}"
        name = os.path.basename(filepath)
        language = detect_language(filepath)
        lines = content.split("\n") if content else [""]
        tab = EditorTab(id=tab_id, name=name, filepath=filepath,
                        lines=lines, language=language)
        self._tabs[tab_id] = tab
        self._active_tab_id = tab_id
        self._dispatch("file_opened", tab_id)
        return tab

    def new_file(self, name: str = "untitled") -> EditorTab:
        self._tab_counter += 1
        tab_id = f"editor-{self._tab_counter}"
        tab = EditorTab(id=tab_id, name=name, dirty=True)
        self._tabs[tab_id] = tab
        self._active_tab_id = tab_id
        self._dispatch("file_created", tab_id)
        return tab

    def close_tab(self, tab_id: str) -> bool:
        if tab_id not in self._tabs:
            return False
        del self._tabs[tab_id]
        if self._active_tab_id == tab_id:
            ids = list(self._tabs.keys())
            self._active_tab_id = ids[0] if ids else None
        self._dispatch("file_closed", tab_id)
        return True

    def save_tab(self, tab_id: str) -> bool:
        tab = self._tabs.get(tab_id)
        if tab is None or not tab.filepath:
            return False
        try:
            with open(tab.filepath, "w") as f:
                f.write("\n".join(tab.lines))
            tab.dirty = False
            self._dispatch("file_saved", tab_id)
            return True
        except OSError:
            return False

    def get_tab(self, tab_id: str) -> Optional[EditorTab]:
        return self._tabs.get(tab_id)

    @property
    def tabs(self) -> List[EditorTab]:
        return list(self._tabs.values())

    @property
    def active_tab(self) -> Optional[EditorTab]:
        if self._active_tab_id:
            return self._tabs.get(self._active_tab_id)
        return None

    @property
    def active_tab_id(self) -> Optional[str]:
        return self._active_tab_id

    def set_active_tab(self, tab_id: str) -> bool:
        if tab_id in self._tabs:
            self._active_tab_id = tab_id
            return True
        return False

    # -- Editing -------------------------------------------------------

    def insert_text(self, text: str) -> None:
        tab = self.active_tab
        if tab is None:
            return

        tab.undo_stack.append(list(tab.lines))
        tab.redo_stack.clear()
        if len(tab.undo_stack) > 200:
            tab.undo_stack.pop(0)

        line = tab.cursor.line
        col = tab.cursor.col
        if line >= len(tab.lines):
            tab.lines.append("")
            line = len(tab.lines) - 1

        current = tab.lines[line]
        if text == "\n":
            indent = ""
            for ch in current:
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break
            tab.lines[line] = current[:col]
            tab.lines.insert(line + 1, indent + current[col:])
            tab.cursor.line = line + 1
            tab.cursor.col = len(indent)
        else:
            tab.lines[line] = current[:col] + text + current[col:]
            tab.cursor.col += len(text)

        tab.dirty = True

    def delete_char(self, backward: bool = True) -> None:
        tab = self.active_tab
        if tab is None:
            return

        tab.undo_stack.append(list(tab.lines))
        tab.redo_stack.clear()

        line = tab.cursor.line
        col = tab.cursor.col

        if backward and col > 0:
            current = tab.lines[line]
            tab.lines[line] = current[:col - 1] + current[col:]
            tab.cursor.col -= 1
            tab.dirty = True
        elif backward and col == 0 and line > 0:
            prev_len = len(tab.lines[line - 1])
            tab.lines[line - 1] += tab.lines[line]
            tab.lines.pop(line)
            tab.cursor.line -= 1
            tab.cursor.col = prev_len
            tab.dirty = True

    def move_cursor(self, line_delta: int = 0, col_delta: int = 0) -> None:
        tab = self.active_tab
        if tab is None:
            return
        tab.cursor.line = max(0, min(len(tab.lines) - 1,
                                     tab.cursor.line + line_delta))
        max_col = len(tab.lines[tab.cursor.line])
        tab.cursor.col = max(0, min(max_col, tab.cursor.col + col_delta))

    def go_to_line(self, line_num: int) -> None:
        tab = self.active_tab
        if tab:
            tab.cursor.line = max(0, min(len(tab.lines) - 1, line_num - 1))
            tab.cursor.col = 0

    def go_to_start(self) -> None:
        tab = self.active_tab
        if tab:
            tab.cursor.line = 0
            tab.cursor.col = 0

    def go_to_end(self) -> None:
        tab = self.active_tab
        if tab:
            tab.cursor.line = len(tab.lines) - 1
            tab.cursor.col = len(tab.lines[-1]) if tab.lines else 0

    # -- Undo/Redo -----------------------------------------------------

    def undo(self) -> bool:
        tab = self.active_tab
        if tab is None or not tab.undo_stack:
            return False
        tab.redo_stack.append(list(tab.lines))
        tab.lines = tab.undo_stack.pop()
        tab.dirty = True
        return True

    def redo(self) -> bool:
        tab = self.active_tab
        if tab is None or not tab.redo_stack:
            return False
        tab.undo_stack.append(list(tab.lines))
        tab.lines = tab.redo_stack.pop()
        tab.dirty = True
        return True

    # -- Find/Replace --------------------------------------------------

    def find(self, query: str, regex: bool = False,
             case_sensitive: bool = False) -> int:
        self._find_query = query
        self._find_results = []
        self._find_index = -1
        self._show_find = True

        tab = self.active_tab
        if tab is None or not query:
            return 0

        flags = 0 if case_sensitive else re.IGNORECASE

        for i, line in enumerate(tab.lines):
            try:
                if regex:
                    for m in re.finditer(query, line, flags):
                        self._find_results.append(FindResult(
                            line=i, col=m.start(),
                            length=m.end() - m.start(), text=m.group()))
                else:
                    start = 0
                    q = query if case_sensitive else query.lower()
                    while True:
                        text = line if case_sensitive else line.lower()
                        idx = text.find(q, start)
                        if idx == -1:
                            break
                        self._find_results.append(FindResult(
                            line=i, col=idx, length=len(query),
                            text=line[idx:idx + len(query)]))
                        start = idx + 1
            except re.error:
                pass

        if self._find_results:
            self._find_index = 0
            r = self._find_results[0]
            tab.cursor.line = r.line
            tab.cursor.col = r.col

        return len(self._find_results)

    def find_next(self) -> Optional[FindResult]:
        if not self._find_results:
            return None
        self._find_index = (self._find_index + 1) % len(self._find_results)
        r = self._find_results[self._find_index]
        tab = self.active_tab
        if tab:
            tab.cursor.line = r.line
            tab.cursor.col = r.col
        return r

    def find_previous(self) -> Optional[FindResult]:
        if not self._find_results:
            return None
        self._find_index = (self._find_index - 1) % len(self._find_results)
        r = self._find_results[self._find_index]
        tab = self.active_tab
        if tab:
            tab.cursor.line = r.line
            tab.cursor.col = r.col
        return r

    def replace_current(self, replacement: str) -> bool:
        if self._find_index < 0 or self._find_index >= len(self._find_results):
            return False
        tab = self.active_tab
        if tab is None:
            return False

        r = self._find_results[self._find_index]
        tab.undo_stack.append(list(tab.lines))
        tab.redo_stack.clear()

        line = tab.lines[r.line]
        tab.lines[r.line] = line[:r.col] + replacement + line[r.col + r.length:]
        tab.dirty = True
        return True

    def replace_all(self, replacement: str) -> int:
        tab = self.active_tab
        if tab is None or not self._find_results:
            return 0

        tab.undo_stack.append(list(tab.lines))
        tab.redo_stack.clear()

        count = 0
        for r in sorted(self._find_results, key=lambda x: (x.line, x.col), reverse=True):
            line = tab.lines[r.line]
            tab.lines[r.line] = line[:r.col] + replacement + line[r.col + r.length:]
            count += 1

        if count:
            tab.dirty = True
            self.find(self._find_query)
        return count

    def close_find(self) -> None:
        self._show_find = False
        self._find_results.clear()
        self._find_index = -1

    @property
    def find_visible(self) -> bool:
        return self._show_find

    @property
    def find_results(self) -> List[FindResult]:
        return list(self._find_results)

    # -- Settings ------------------------------------------------------

    def toggle_word_wrap(self) -> bool:
        tab = self.active_tab
        if tab:
            tab.word_wrap = not tab.word_wrap
            return tab.word_wrap
        return False

    def toggle_line_numbers(self) -> bool:
        tab = self.active_tab
        if tab:
            tab.show_line_numbers = not tab.show_line_numbers
            return tab.show_line_numbers
        return False

    # -- View ----------------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Rendering -----------------------------------------------------

    def render(self, width: int = 1920, height: int = 1080) -> Any:
        if not self._visible:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font_mono = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except (OSError, IOError):
            font_mono = font_bold = font_small = ImageFont.load_default()

        px, py = 60, 30
        pw, ph = width - 120, height - 60
        draw.rounded_rectangle(
            [px, py, px + pw, py + ph],
            radius=12, fill=(22, 22, 28, 240), outline=(60, 60, 70))

        # Tab bar
        tab_x = px + 12
        for tab in self._tabs.values():
            is_active = (tab.id == self._active_tab_id)
            tw = len(tab.display_name) * 8 + 24
            bg = (40, 40, 52) if is_active else (28, 28, 36)
            draw.rounded_rectangle(
                [tab_x, py + 4, tab_x + tw, py + 28],
                radius=6, fill=bg)
            draw.text((tab_x + 12, py + 8), tab.display_name,
                      fill=(220, 220, 220) if is_active else (120, 120, 120),
                      font=font_small)
            tab_x += tw + 4

        tab = self.active_tab
        if tab is None:
            return img

        ey = py + 36
        gutter_w = 60 if tab.show_line_numbers else 0

        if tab.show_line_numbers:
            for i in range(min(50, tab.line_count)):
                ly = ey + i * 16
                is_current = (i == tab.cursor.line)
                color = (220, 220, 220) if is_current else (70, 70, 90)
                draw.text((px + 12, ly), f"{i + 1:>4}",
                          fill=color, font=font_mono)

        for i in range(min(50, tab.line_count - tab.scroll_y)):
            line_idx = tab.scroll_y + i
            ly = ey + i * 16
            is_current = (line_idx == tab.cursor.line)
            if is_current:
                draw.rectangle(
                    [px + gutter_w + 4, ly, px + pw - 4, ly + 14],
                    fill=(35, 35, 48))

            line_text = tab.lines[line_idx]
            spans = highlight_line(line_text, tab.language)
            cx = px + gutter_w + 12
            for text, color in spans:
                draw.text((cx, ly), text, fill=color, font=font_mono)
                cx += len(text) * 7

            if is_current:
                cursor_x = px + gutter_w + 12 + tab.cursor.col * 7
                draw.rectangle(
                    [cursor_x, ly, cursor_x + 1, ly + 14],
                    fill=(200, 200, 200))

        # Status bar
        sy = py + ph - 20
        draw.rectangle([px, sy, px + pw, py + ph], fill=(30, 30, 38))
        pos = f"Ln {tab.cursor.line + 1}, Col {tab.cursor.col + 1}"
        draw.text((px + 12, sy + 3), pos, fill=(140, 140, 140), font=font_small)
        lang = tab.language.value
        draw.text((px + pw - 100, sy + 3), lang,
                  fill=(100, 140, 180), font=font_small)

        if self._show_find:
            fy = py + ph - 44
            draw.rectangle([px + 4, fy, px + pw - 4, fy + 20], fill=(35, 35, 48))
            draw.text((px + 12, fy + 3), "Find:",
                      fill=(140, 140, 140), font=font_small)
            draw.text((px + 52, fy + 3), self._find_query,
                      fill=(220, 220, 220), font=font_mono)

        return img

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return f"EnhancedTextEditor(tabs={len(self._tabs)})"


__all__ = [
    "EnhancedTextEditor", "EditorTab", "CursorPosition", "Selection",
    "SyntaxLanguage", "highlight_line", "detect_language", "FindResult",
]
