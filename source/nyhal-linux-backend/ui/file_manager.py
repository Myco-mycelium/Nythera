"""
Nyrqis OS - Virtual File Manager
Tabs, bookmarks, and file operations.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class FileType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


class ViewMode(Enum):
    ICONS = "icons"
    LIST = "list"
    DETAILS = "details"
    TILES = "tiles"
    COLUMNS = "columns"


class SortBy(Enum):
    NAME = "name"
    SIZE = "size"
    MODIFIED = "modified"
    TYPE = "type"
    PERMISSIONS = "permissions"


@dataclass
class FileEntry:
    name: str = ""
    file_type: FileType = FileType.FILE
    size_bytes: int = 0
    modified_time: float = 0.0
    permissions: str = ""
    owner: str = ""
    group: str = ""
    mime_type: str = ""
    is_hidden: bool = False
    is_symlink: bool = False
    symlink_target: str = ""

    @property
    def icon(self) -> str:
        if self.file_type == FileType.DIRECTORY:
            return "📁"
        if self.file_type == FileType.SYMLINK:
            return "🔗"
        ext_map = {".py": "🐍", ".js": "📜", ".rs": "🦀", ".go": "🐹",
                   ".c": "📄", ".h": "📄", ".md": "📝", ".txt": "📄",
                   ".json": "📋", ".yaml": "📋", ".toml": "📋",
                   ".png": "🖼️", ".jpg": "🖼️", ".gif": "🖼️", ".svg": "🖼️",
                   ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵",
                   ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬",
                   ".zip": "📦", ".tar": "📦", ".gz": "📦",
                   ".pdf": "📕", ".doc": "📘", ".xls": "📗",
                   ".sh": "⚙️", ".bash": "⚙️", ".fish": "⚙️"}
        for ext, icon in ext_map.items():
            if self.name.lower().endswith(ext):
                return icon
        return "📄"

    @property
    def size_display(self) -> str:
        if self.file_type == FileType.DIRECTORY:
            return "<DIR>"
        s = self.size_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def modified_display(self) -> str:
        if self.modified_time == 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified_time))

    @property
    def extension(self) -> str:
        parts = self.name.rsplit(".", 1)
        return parts[1] if len(parts) > 1 else ""


@dataclass
class FileTab:
    id: int = 0
    path: str = ""
    title: str = ""
    history: List[str] = field(default_factory=list)
    history_index: int = 0
    view_mode: ViewMode = ViewMode.ICONS
    sort_by: SortBy = SortBy.NAME
    sort_reverse: bool = False
    show_hidden: bool = False
    selected_files: List[str] = field(default_factory=list)
    is_active: bool = False

    @property
    def display_title(self) -> str:
        return self.title if self.title else self.path.split("/")[-1] or "/"


@dataclass
class Bookmark:
    name: str = ""
    path: str = ""
    icon: str = "📌"
    created_at: float = 0.0


@dataclass
class FileOperation:
    name: str = ""
    operation: str = ""  # copy, move, delete, rename
    source: str = ""
    destination: str = ""
    status: str = "pending"
    progress: float = 0.0

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress / 5)
        return "█" * filled + "░" * (20 - filled)


class FileManager:
    def __init__(self):
        self.tabs: List[FileTab] = []
        self.bookmarks: List[Bookmark] = []
        self.operations: List[FileOperation] = []
        self.current_tab: Optional[FileTab] = None
        self.tab_counter: int = 0
        self.clipboard_files: List[str] = []
        self.clipboard_mode: str = ""  # copy, cut
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.tabs = [
            FileTab(id=1, path="/home/zeus", title="Home", is_active=True,
                     history=["/home/zeus"]),
            FileTab(id=2, path="/opt/Nyrqis", title="Nyrqis",
                     history=["/opt/Nyrqis"]),
            FileTab(id=3, path="/tmp", title="Temp",
                     history=["/tmp"]),
        ]
        self.current_tab = self.tabs[0]
        self.tab_counter = 3

        self.bookmarks = [
            Bookmark(name="Home", path="/home/zeus", icon="🏠"),
            Bookmark(name="Desktop", path="/home/zeus/Desktop", icon="🖥️"),
            Bookmark(name="Documents", path="/home/zeus/Documents", icon="📄"),
            Bookmark(name="Downloads", path="/home/zeus/Downloads", icon="📥"),
            Bookmark(name="Nyrqis Source", path="/opt/Nyrqis", icon="🍄"),
            Bookmark(name="Projects", path="/home/zeus/Projects", icon="💻"),
            Bookmark(name="Config", path="/home/zeus/.config", icon="⚙️"),
            Bookmark(name="Root", path="/", icon="💿"),
        ]

    def get_files_for_path(self, path: str) -> List[FileEntry]:
        now = time.time()
        sample_files = {
            "/home/zeus": [
                FileEntry(name="Desktop", file_type=FileType.DIRECTORY,
                           modified_time=now - 3600, permissions="drwxr-xr-x"),
                FileEntry(name="Documents", file_type=FileType.DIRECTORY,
                           modified_time=now - 1800, permissions="drwxr-xr-x"),
                FileEntry(name="Downloads", file_type=FileType.DIRECTORY,
                           modified_time=now - 7200, permissions="drwxr-xr-x"),
                FileEntry(name="Pictures", file_type=FileType.DIRECTORY,
                           modified_time=now - 86400, permissions="drwxr-xr-x"),
                FileEntry(name="Music", file_type=FileType.DIRECTORY,
                           modified_time=now - 86400 * 7, permissions="drwxr-xr-x"),
                FileEntry(name="Videos", file_type=FileType.DIRECTORY,
                           modified_time=now - 86400 * 14, permissions="drwxr-xr-x"),
                FileEntry(name="Projects", file_type=FileType.DIRECTORY,
                           modified_time=now - 600, permissions="drwxr-xr-x"),
                FileEntry(name=".bashrc", file_type=FileType.FILE,
                           size_bytes=3500, modified_time=now - 86400 * 30,
                           permissions="-rw-r--r--", is_hidden=True),
                FileEntry(name=".gitconfig", file_type=FileType.FILE,
                           size_bytes=500, modified_time=now - 86400 * 60,
                           permissions="-rw-r--r--", is_hidden=True),
                FileEntry(name="README.md", file_type=FileType.FILE,
                           size_bytes=8500, modified_time=now - 3600,
                           permissions="-rw-r--r--"),
            ],
            "/opt/Nyrqis": [
                FileEntry(name="Cargo.toml", file_type=FileType.FILE,
                           size_bytes=2500, modified_time=now - 300,
                           permissions="-rw-r--r--"),
                FileEntry(name="source", file_type=FileType.DIRECTORY,
                           modified_time=now - 300, permissions="drwxr-xr-x"),
                FileEntry(name="target", file_type=FileType.DIRECTORY,
                           modified_time=now - 60, permissions="drwxr-xr-x"),
                FileEntry(name="docs", file_type=FileType.DIRECTORY,
                           modified_time=now - 86400, permissions="drwxr-xr-x"),
                FileEntry(name="README.md", file_type=FileType.FILE,
                           size_bytes=12000, modified_time=now - 86400,
                           permissions="-rw-r--r--"),
                FileEntry(name="build.sh", file_type=FileType.FILE,
                           size_bytes=800, modified_time=now - 86400 * 7,
                           permissions="-rwxr-xr-x"),
                FileEntry(name=".gitignore", file_type=FileType.FILE,
                           size_bytes=200, modified_time=now - 86400 * 30,
                           permissions="-rw-r--r--"),
            ],
            "/tmp": [
                FileEntry(name="nyrqis-build.log", file_type=FileType.FILE,
                           size_bytes=45000, modified_time=now - 120,
                           permissions="-rw-r--r--"),
                FileEntry(name="session-cache", file_type=FileType.FILE,
                           size_bytes=2500, modified_time=now - 600,
                           permissions="-rw-------"),
                FileEntry(name="X11-unix", file_type=FileType.DIRECTORY,
                           modified_time=now, permissions="drwxrwxrwt"),
            ],
        }
        return sample_files.get(path, [
            FileEntry(name="..", file_type=FileType.DIRECTORY,
                       modified_time=now, permissions="drwxr-xr-x"),
        ])

    def new_tab(self, path: str = "/home/zeus") -> FileTab:
        self.tab_counter += 1
        tab = FileTab(id=self.tab_counter, path=path, history=[path])
        self.tabs.append(tab)
        return tab

    def close_tab(self, tab_id: int) -> bool:
        if len(self.tabs) <= 1:
            return False
        for i, t in enumerate(self.tabs):
            if t.id == tab_id:
                del self.tabs[i]
                if self.current_tab and self.current_tab.id == tab_id:
                    self.current_tab = self.tabs[min(i, len(self.tabs) - 1)]
                return True
        return False

    def switch_tab(self, tab_id: int) -> bool:
        tab = next((t for t in self.tabs if t.id == tab_id), None)
        if tab:
            for t in self.tabs:
                t.is_active = False
            tab.is_active = True
            self.current_tab = tab
            return True
        return False

    def navigate_to(self, path: str) -> bool:
        if self.current_tab:
            self.current_tab.path = path
            self.current_tab.history.append(path)
            self.current_tab.selected_files.clear()
            return True
        return False

    def go_back(self) -> bool:
        if self.current_tab and self.current_tab.history_index > 0:
            self.current_tab.history_index -= 1
            self.current_tab.path = self.current_tab.history[self.current_tab.history_index]
            return True
        return False

    def go_forward(self) -> bool:
        if self.current_tab and self.current_tab.history_index < len(self.current_tab.history) - 1:
            self.current_tab.history_index += 1
            self.current_tab.path = self.current_tab.history[self.current_tab.history_index]
            return True
        return False

    def select_file(self, name: str) -> bool:
        if self.current_tab:
            if name in self.current_tab.selected_files:
                self.current_tab.selected_files.remove(name)
            else:
                self.current_tab.selected_files.append(name)
            return True
        return False

    def copy_files(self, files: List[str]) -> bool:
        self.clipboard_files = files
        self.clipboard_mode = "copy"
        return True

    def cut_files(self, files: List[str]) -> bool:
        self.clipboard_files = files
        self.clipboard_mode = "cut"
        return True

    def paste_files(self) -> int:
        count = len(self.clipboard_files)
        self.clipboard_files.clear()
        self.clipboard_mode = ""
        return count

    def delete_files(self, files: List[str]) -> bool:
        return len(files) > 0

    def add_bookmark(self, name: str, path: str, icon: str = "📌") -> Bookmark:
        bm = Bookmark(name=name, path=path, icon=icon)
        self.bookmarks.append(bm)
        return bm

    def remove_bookmark(self, name: str) -> bool:
        for i, bm in enumerate(self.bookmarks):
            if bm.name == name:
                del self.bookmarks[i]
                return True
        return False

    def get_stats(self) -> Dict:
        files = self.get_files_for_path(self.current_tab.path) if self.current_tab else []
        dirs = sum(1 for f in files if f.file_type == FileType.DIRECTORY)
        regular = sum(1 for f in files if f.file_type == FileType.FILE)
        return {
            "tabs": len(self.tabs),
            "bookmarks": len(self.bookmarks),
            "directories": dirs,
            "files": regular,
            "clipboard": len(self.clipboard_files),
        }


class SortMode(Enum):
    NAME = "name"
    SIZE = "size"
    DATE = "date"
    TYPE = "type"

EXTENSION_MAP = {}

# ─── Backward-compat exports ────────────────────────────────────────────
# Import the existing FileType enum from this module if it exists
import sys as _sys
_fm = _sys.modules.get(__name__)
FILE_TYPE_COLORS = {}
if hasattr(_fm, 'FileType'):
    for ft in _fm.FileType:
        if ft.name == 'FILE':
            FILE_TYPE_COLORS[ft] = '#90CAF9'
        elif ft.name == 'DIRECTORY':
            FILE_TYPE_COLORS[ft] = '#FFB74D'
        elif ft.name == 'SYMLINK':
            FILE_TYPE_COLORS[ft] = '#CE93D8'
        elif ft.name == 'IMAGE':
            FILE_TYPE_COLORS[ft] = '#81C784'
        else:
            FILE_TYPE_COLORS[ft] = '#BDBDBD'
