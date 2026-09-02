#!/usr/bin/env python3
"""File manager component for the Nyrqis desktop.

Provides a visual file manager with:
- Directory listing with file/folder icons
- Breadcrumb path navigation
- File metadata (size, permissions, date)
- Pixel rendering for display in desktop windows
- Sorting by name, size, date, type
- Selection and navigation
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# File types
# ---------------------------------------------------------------------------

class FileType(IntEnum):
    """File type categories."""
    FILE = 0
    DIRECTORY = 1
    SYMLINK = 2
    EXECUTABLE = 3
    IMAGE = 4
    VIDEO = 5
    AUDIO = 6
    ARCHIVE = 7
    DOCUMENT = 8
    CODE = 9
    CONFIG = 10


# Extension to file type mapping
EXTENSION_MAP = {
    # Images
    ".png": FileType.IMAGE, ".jpg": FileType.IMAGE, ".jpeg": FileType.IMAGE,
    ".gif": FileType.IMAGE, ".bmp": FileType.IMAGE, ".svg": FileType.IMAGE,
    ".ico": FileType.IMAGE, ".webp": FileType.IMAGE,
    # Video
    ".mp4": FileType.VIDEO, ".avi": FileType.VIDEO, ".mkv": FileType.VIDEO,
    ".mov": FileType.VIDEO, ".webm": FileType.VIDEO,
    # Audio
    ".mp3": FileType.AUDIO, ".wav": FileType.AUDIO, ".flac": FileType.AUDIO,
    ".ogg": FileType.AUDIO, ".m4a": FileType.AUDIO,
    # Archives
    ".zip": FileType.ARCHIVE, ".tar": FileType.ARCHIVE, ".gz": FileType.ARCHIVE,
    ".bz2": FileType.ARCHIVE, ".xz": FileType.ARCHIVE, ".7z": FileType.ARCHIVE,
    # Documents
    ".pdf": FileType.DOCUMENT, ".doc": FileType.DOCUMENT, ".docx": FileType.DOCUMENT,
    ".txt": FileType.DOCUMENT, ".md": FileType.DOCUMENT, ".rst": FileType.DOCUMENT,
    # Code
    ".py": FileType.CODE, ".rs": FileType.CODE, ".c": FileType.CODE,
    ".cpp": FileType.CODE, ".h": FileType.CODE, ".js": FileType.CODE,
    ".ts": FileType.CODE, ".java": FileType.CODE, ".go": FileType.CODE,
    ".rb": FileType.CODE, ".php": FileType.CODE, ".sh": FileType.CODE,
    ".bash": FileType.CODE, ".zsh": FileType.CODE,
    # Config
    ".conf": FileType.CONFIG, ".cfg": FileType.CONFIG, ".ini": FileType.CONFIG,
    ".json": FileType.CONFIG, ".yaml": FileType.CONFIG, ".yml": FileType.CONFIG,
    ".toml": FileType.CONFIG, ".xml": FileType.CONFIG,
}


# File type display colors (R, G, B)
FILE_TYPE_COLORS = {
    FileType.FILE: (180, 180, 200),
    FileType.DIRECTORY: (255, 200, 60),
    FileType.SYMLINK: (100, 200, 255),
    FileType.EXECUTABLE: (60, 200, 120),
    FileType.IMAGE: (200, 120, 255),
    FileType.VIDEO: (255, 100, 100),
    FileType.AUDIO: (100, 200, 255),
    FileType.ARCHIVE: (200, 160, 80),
    FileType.DOCUMENT: (100, 180, 255),
    FileType.CODE: (60, 220, 120),
    FileType.CONFIG: (180, 180, 180),
}

# File type symbols (single character)
FILE_TYPE_SYMBOLS = {
    FileType.FILE: "\u25a1",       # □
    FileType.DIRECTORY: "\U0001f4c1",  # 📁
    FileType.SYMLINK: "\u2192",   # →
    FileType.EXECUTABLE: "\u25b6",  # ▶
    FileType.IMAGE: "\U0001f5bc",   # 🖼
    FileType.VIDEO: "\U0001f3ac",   # 🎬
    FileType.AUDIO: "\U0001f3b5",   # 🎵
    FileType.ARCHIVE: "\U0001f4e6", # 📦
    FileType.DOCUMENT: "\U0001f4c4", # 📄
    FileType.CODE: "\u007b\u007d",   # {}
    FileType.CONFIG: "\u2699",       # ⚙
}


# ---------------------------------------------------------------------------
# File entry
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    """A single file or directory entry."""
    name: str
    path: str
    is_dir: bool = False
    is_link: bool = False
    is_executable: bool = False
    size: int = 0           # bytes
    modified: float = 0.0   # timestamp
    permissions: str = ""   # e.g. "rwxr-xr-x"
    
    @property
    def file_type(self) -> FileType:
        """Determine the file type from extension and flags."""
        if self.is_dir:
            return FileType.DIRECTORY
        if self.is_link:
            return FileType.SYMLINK
        if self.is_executable:
            return FileType.EXECUTABLE
        
        _, ext = os.path.splitext(self.name.lower())
        return EXTENSION_MAP.get(ext, FileType.FILE)
    
    @property
    def extension(self) -> str:
        """Get the file extension."""
        _, ext = os.path.splitext(self.name)
        return ext.lower()
    
    @property
    def display_size(self) -> str:
        """Human-readable file size."""
        if self.is_dir:
            return ""
        size = self.size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
    
    @property
    def display_date(self) -> str:
        """Formatted modification date."""
        if self.modified == 0:
            return ""
        t = time.localtime(self.modified)
        now = time.localtime()
        
        if t.tm_year == now.tm_year:
            return time.strftime("%b %d %H:%M", t)
        return time.strftime("%b %d  %Y", t)


# ---------------------------------------------------------------------------
# Sort modes
# ---------------------------------------------------------------------------

class SortMode(Enum):
    """File sorting modes."""
    NAME = "name"
    SIZE = "size"
    DATE = "date"
    TYPE = "type"


# ---------------------------------------------------------------------------
# File manager
# ---------------------------------------------------------------------------

class FileManager:
    """Visual file manager component.
    
    Parameters
    ----------
    root_path : str
        Initial directory path.
    on_navigate : callable, optional
        Called with (path) when navigating to a directory.
    """
    
    # Layout constants
    ROW_HEIGHT = 28
    ICON_SIZE = 16
    PADDING = 8
    BREADCRUMB_HEIGHT = 32
    HEADER_HEIGHT = 24
    STATUS_HEIGHT = 24
    
    def __init__(
        self,
        root_path: str = "/",
        on_navigate: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._current_path = os.path.abspath(root_path)
        self._entries: List[FileEntry] = []
        self._selected_index: int = 0
        self._selection_start: int = -1
        self._selection_end: int = -1
        self._sort_mode: SortMode = SortMode.NAME
        self._sort_reverse: bool = False
        self._show_hidden: bool = False
        self._show_size_column: bool = True
        self._show_date_column: bool = True
        self._on_navigate = on_navigate
        
        # View state
        self._scroll_offset: int = 0
        self._view_width: int = 800
        self._view_height: int = 600
        
        # Load initial directory
        self._load_directory()
    
    # -- Directory loading -------------------------------------------------
    
    def _load_directory(self) -> None:
        """Load the current directory's contents."""
        self._entries = []
        self._selected_index = 0
        self._scroll_offset = 0
        
        try:
            items = os.listdir(self._current_path)
        except (PermissionError, OSError):
            items = []
        
        for name in sorted(items):
            # Skip hidden files if not showing them
            if not self._show_hidden and name.startswith("."):
                continue
            
            full_path = os.path.join(self._current_path, name)
            
            try:
                stat_info = os.lstat(full_path)
            except (PermissionError, OSError):
                continue
            
            entry = FileEntry(
                name=name,
                path=full_path,
                is_dir=os.path.isdir(full_path),
                is_link=os.path.islink(full_path),
                is_executable=os.access(full_path, os.X_OK),
                size=stat_info.st_size,
                modified=stat_info.st_mtime,
                permissions=self._format_permissions(stat_info.st_mode),
            )
            self._entries.append(entry)
        
        # Add parent directory entry if not at root
        if self._current_path != "/":
            parent = os.path.dirname(self._current_path)
            self._entries.insert(0, FileEntry(
                name="..",
                path=parent,
                is_dir=True,
                permissions="drwxr-xr-x",
            ))
        
        self._sort_entries()
    
    def _format_permissions(self, mode: int) -> str:
        """Format file permissions as rwxrwxrwx string."""
        perms = "d" if os.path.isdir("/dev/null") else "-"  # Placeholder
        for who in ["USR", "GRP", "OTH"]:
            for char, flag in [("r", 4), ("w", 2), ("x", 1)]:
                if mode & (flag << (6 - ["USR", "GRP", "OTH"].index(who) * 3)):
                    perms += char
                else:
                    perms += "-"
        return perms
    
    # -- Sorting -----------------------------------------------------------
    
    def _sort_entries(self) -> None:
        """Sort entries according to current sort mode."""
        # Always keep .. at the top
        parent = None
        entries = self._entries
        
        if entries and entries[0].name == "..":
            parent = entries[0]
            entries = entries[1:]
        
        key_map = {
            SortMode.NAME: lambda e: (not e.is_dir, e.name.lower()),
            SortMode.SIZE: lambda e: (not e.is_dir, e.size),
            SortMode.DATE: lambda e: (not e.is_dir, e.modified),
            SortMode.TYPE: lambda e: (not e.is_dir, e.file_type.value, e.name.lower()),
        }
        
        key = key_map.get(self._sort_mode, key_map[SortMode.NAME])
        entries.sort(key=key, reverse=self._sort_reverse)
        
        # Reassemble with parent at top
        if parent:
            self._entries = [parent] + entries
        else:
            self._entries = entries
    
    # -- Navigation --------------------------------------------------------
    
    def navigate_to(self, path: str) -> bool:
        """Navigate to a directory path.
        
        Returns True if navigation succeeded.
        """
        path = os.path.abspath(path)
        
        if not os.path.isdir(path):
            return False
        
        self._current_path = path
        self._load_directory()
        
        if self._on_navigate:
            self._on_navigate(path)
        
        return True
    
    def go_up(self) -> bool:
        """Navigate to parent directory."""
        parent = os.path.dirname(self._current_path)
        return self.navigate_to(parent)
    
    def go_home(self) -> bool:
        """Navigate to home directory."""
        return self.navigate_to(os.path.expanduser("~"))
    
    # -- Selection ---------------------------------------------------------
    
    def select(self, index: int) -> None:
        """Select an entry by index."""
        if 0 <= index < len(self._entries):
            self._selected_index = index
            self._selection_start = index
            self._selection_end = index
    
    def select_range(self, start: int, end: int) -> None:
        """Select a range of entries."""
        self._selection_start = max(0, min(start, len(self._entries) - 1))
        self._selection_end = max(0, min(end, len(self._entries) - 1))
        self._selected_index = self._selection_start
    
    def get_selected(self) -> Optional[FileEntry]:
        """Get the currently selected entry."""
        if 0 <= self._selected_index < len(self._entries):
            return self._entries[self._selected_index]
        return None
    
    def activate_selected(self) -> bool:
        """Activate the selected entry (open directory or return False for files)."""
        entry = self.get_selected()
        if entry is None:
            return False
        
        if entry.name == "..":
            return self.go_up()
        elif entry.is_dir:
            return self.navigate_to(entry.path)
        
        return False
    
    # -- Scroll ------------------------------------------------------------
    
    def scroll(self, delta: int) -> None:
        """Scroll the view by delta entries."""
        max_scroll = max(0, len(self._entries) - self._visible_rows)
        self._scroll_offset = max(0, min(self._scroll_offset + delta, max_scroll))
    
    def scroll_to_selected(self) -> None:
        """Ensure the selected entry is visible."""
        visible_start = self._scroll_offset
        visible_end = self._scroll_offset + self._visible_rows - 1
        
        if self._selected_index < visible_start:
            self._scroll_offset = self._selected_index
        elif self._selected_index > visible_end:
            self._scroll_offset = self._selected_index - self._visible_rows + 1
    
    @property
    def _visible_rows(self) -> int:
        """Number of visible rows in the view."""
        content_height = self._view_height - self.BREADCRUMB_HEIGHT - self.HEADER_HEIGHT - self.STATUS_HEIGHT
        return max(1, content_height // self.ROW_HEIGHT)
    
    # -- Properties --------------------------------------------------------
    
    @property
    def current_path(self) -> str:
        return self._current_path
    
    @property
    def entries(self) -> List[FileEntry]:
        return list(self._entries)
    
    @property
    def entry_count(self) -> int:
        return len(self._entries)
    
    @property
    def selected_index(self) -> int:
        return self._selected_index
    
    @property
    def sort_mode(self) -> SortMode:
        return self._sort_mode
    
    @sort_mode.setter
    def sort_mode(self, mode: SortMode) -> None:
        if mode != self._sort_mode:
            self._sort_mode = mode
            self._sort_entries()
    
    @property
    def sort_reverse(self) -> bool:
        return self._sort_reverse
    
    @sort_reverse.setter
    def sort_reverse(self, reverse: bool) -> None:
        if reverse != self._sort_reverse:
            self._sort_reverse = reverse
            self._sort_entries()
    
    @property
    def show_hidden(self) -> bool:
        return self._show_hidden
    
    @show_hidden.setter
    def show_hidden(self, show: bool) -> None:
        if show != self._show_hidden:
            self._show_hidden = show
            self._load_directory()
    
    # -- Breadcrumb path ---------------------------------------------------
    
    @property
    def breadcrumbs(self) -> List[Tuple[str, str]]:
        """Get breadcrumb path components as (name, full_path) pairs."""
        parts = []
        path = self._current_path
        
        while path and path != "/":
            name = os.path.basename(path)
            parts.append((name, path))
            path = os.path.dirname(path)
        
        parts.append(("/", "/"))
        parts.reverse()
        return parts
    
    @property
    def directory_size(self) -> int:
        """Total size of visible entries."""
        return sum(e.size for e in self._entries if e.name != "..")
    
    @property
    def directory_count(self) -> int:
        """Number of directories in current listing."""
        return sum(1 for e in self._entries if e.is_dir and e.name != "..")
    
    @property
    def file_count(self) -> int:
        """Number of files in current listing."""
        return sum(1 for e in self._entries if not e.is_dir)
    
    # -- Keyboard input ----------------------------------------------------
    
    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Handle a keyboard event.
        
        Returns
        -------
        str
            Action name ("navigate", "select", "scroll", etc.) or "" if unhandled.
        """
        mods = modifiers or {}
        
        if key == "Up" or key == "k":
            self.select(max(0, self._selected_index - 1))
            self.scroll_to_selected()
            return "select"
        elif key == "Down" or key == "j":
            self.select(min(len(self._entries) - 1, self._selected_index + 1))
            self.scroll_to_selected()
            return "select"
        elif key == "Home" or key == "g":
            self.select(0)
            self.scroll_to_selected()
            return "select"
        elif key == "End" or key == "G":
            self.select(len(self._entries) - 1)
            self.scroll_to_selected()
            return "select"
        elif key == "PageUp":
            self.scroll(-self._visible_rows)
            self.select(self._scroll_offset)
            return "scroll"
        elif key == "PageDown":
            self.scroll(self._visible_rows)
            self.select(self._scroll_offset)
            return "scroll"
        elif key == "Enter" or key == "Return":
            if self.activate_selected():
                return "navigate"
            return "activate"
        elif key == "Backspace":
            if self.go_up():
                return "navigate"
            return ""
        elif key == "h":
            if self.go_up():
                return "navigate"
            return ""
        elif key == "l":
            if self.activate_selected():
                return "navigate"
            return ""
        elif key == "r":
            self._load_directory()
            return "refresh"
        elif key == ".":
            self.show_hidden = not self.show_hidden
            return "toggle_hidden"
        elif key == "s" and mods.get("ctrl"):
            # Cycle sort mode
            modes = list(SortMode)
            idx = modes.index(self._sort_mode)
            self.sort_mode = modes[(idx + 1) % len(modes)]
            return "sort"
        elif key == "n":
            self.sort_mode = SortMode.NAME
            return "sort"
        elif key == "S":
            self.sort_mode = SortMode.SIZE
            self.sort_reverse = True
            return "sort"
        elif key == "t":
            self.sort_mode = SortMode.DATE
            self.sort_reverse = True
            return "sort"
        
        return ""
    
    # -- Rendering ---------------------------------------------------------
    
    def render(
        self,
        x_offset: int = 0,
        y_offset: int = 0,
        width: int = 800,
        height: int = 600,
    ) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the file manager to a pixel buffer.
        
        Returns (pixels, width, height) where pixels is a flat list of
        (r, g, b) tuples in row-major order.
        """
        self._view_width = width
        self._view_height = height
        
        # Colors
        BG_COLOR = (30, 30, 42)
        TEXT_COLOR = (200, 200, 220)
        DIM_COLOR = (120, 120, 140)
        ACCENT_COLOR = (80, 140, 255)
        SELECT_BG = (50, 50, 70)
        HOVER_BG = (45, 45, 65)
        BORDER_COLOR = (60, 60, 80)
        BREADCRUMB_COLOR = (100, 160, 255)
        
        pixels = [BG_COLOR] * (width * height)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < width and 0 <= py < height:
                pixels[py * width + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            """Draw a character using the 5x7 bitmap font."""
            # Simple 5x7 font
            FONT = {
                ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
                '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
                '/': [0x02, 0x02, 0x04, 0x08, 0x08, 0x10, 0x10],
                '~': [0x00, 0x00, 0x04, 0x15, 0x0A, 0x00, 0x00],
                '>': [0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08],
                '<': [0x02, 0x04, 0x08, 0x10, 0x08, 0x04, 0x02],
                '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
                '+': [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
                '0': [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
                '1': [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
                '2': [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
                '3': [0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E],
                '4': [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
                '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
                '6': [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
                '7': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
                '8': [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
                '9': [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
                'A': [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
                'B': [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
                'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
                'D': [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
                'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
                'F': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
                'G': [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
                'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
                'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
                'J': [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
                'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
                'L': [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
                'M': [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
                'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
                'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
                'P': [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
                'Q': [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
                'R': [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
                'S': [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
                'T': [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
                'U': [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
                'V': [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
                'W': [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
                'X': [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
                'Y': [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
                'Z': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
                'a': [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
                'b': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E],
                'c': [0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E],
                'd': [0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F],
                'e': [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
                'f': [0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08],
                'g': [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
                'h': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11],
                'i': [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
                'j': [0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C],
                'k': [0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12],
                'l': [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
                'm': [0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11],
                'n': [0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11],
                'o': [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
                'p': [0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10],
                'q': [0x00, 0x00, 0x0D, 0x13, 0x0F, 0x01, 0x01],
                'r': [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
                's': [0x00, 0x00, 0x0E, 0x10, 0x0E, 0x01, 0x1E],
                't': [0x08, 0x08, 0x1C, 0x08, 0x08, 0x09, 0x06],
                'u': [0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D],
                'v': [0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04],
                'w': [0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A],
                'x': [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
                'y': [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
                'z': [0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F],
                ':': [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x00],
                ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            }
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            """Draw text, return the x position after the last character."""
            cx = tx
            for ch in text:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        # Draw breadcrumb bar
        cy = self.PADDING
        draw_text(self.PADDING, cy, "~", BREADCRUMB_COLOR)
        cx = self.PADDING + 12
        for name, path in self.breadcrumbs[1:]:
            draw_text(cx, cy, ">", DIM_COLOR)
            cx += 12
            cx = draw_text(cx, cy, name, BREADCRUMB_COLOR)
            cx += 8
        
        cy += 14
        
        # Draw header
        fill_rect(0, cy, width, self.HEADER_HEIGHT, (40, 40, 55))
        cy += 4
        
        col_x = [self.PADDING]
        draw_text(col_x[0], cy, "Name", DIM_COLOR)
        col_x.append(width - 120 if self._show_size_column else width - 120)
        if self._show_size_column:
            draw_text(col_x[1], cy, "Size", DIM_COLOR)
        col_x.append(width - 60)
        if self._show_date_column:
            draw_text(col_x[2], cy, "Date", DIM_COLOR)
        
        cy += self.HEADER_HEIGHT
        
        # Draw entries
        visible_entries = self._entries[self._scroll_offset:self._scroll_offset + self._visible_rows]
        
        for i, entry in enumerate(visible_entries):
            actual_idx = self._scroll_offset + i
            entry_y = cy + i * self.ROW_HEIGHT
            
            # Selection highlight
            if actual_idx == self._selected_index:
                fill_rect(0, entry_y, width, self.ROW_HEIGHT, SELECT_BG)
            elif i % 2 == 0:
                fill_rect(0, entry_y, width, self.ROW_HEIGHT, HOVER_BG)
            
            # File type color
            type_color = FILE_TYPE_COLORS.get(entry.file_type, TEXT_COLOR)
            
            # Draw entry name
            name_x = self.PADDING + 4
            draw_text(name_x, entry_y + 8, entry.name[:40], type_color)
            
            # Draw size
            if self._show_size_column:
                size_text = entry.display_size
                if size_text:
                    draw_text(col_x[1], entry_y + 8, size_text, DIM_COLOR)
            
            # Draw date
            if self._show_date_column:
                date_text = entry.display_date
                if date_text:
                    draw_text(col_x[2], entry_y + 8, date_text, DIM_COLOR)
            
            # Draw separator line
            fill_rect(0, entry_y + self.ROW_HEIGHT - 1, width, 1, BORDER_COLOR)
        
        # Draw status bar
        status_y = height - self.STATUS_HEIGHT
        fill_rect(0, status_y, width, self.STATUS_HEIGHT, (40, 40, 55))
        
        # Show entry count and selection info
        status_text = f"{self.file_count} files, {self.directory_count} folders"
        draw_text(self.PADDING, status_y + 6, status_text, DIM_COLOR)
        
        # Show current path on right
        path_text = self._current_path[-50:]  # Truncate long paths
        draw_text(width - len(path_text) * 6 - self.PADDING, status_y + 6, path_text, DIM_COLOR)
        
        return pixels, width, height
    
    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes.
        
        Returns (rgb_bytes, width, height).
        """
        pixels, width, height = self.render()
        buf = bytearray(width * height * 3)
        i = 0
        for r, g, b in pixels:
            buf[i] = r
            buf[i+1] = g
            buf[i+2] = b
            i += 3
        return bytes(buf), width, height
    
    def __repr__(self) -> str:
        return (
            f"FileManager(path='{self._current_path}', "
            f"entries={len(self._entries)}, "
            f"selected={self._selected_index})"
        )
