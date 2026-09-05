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
    FILE = 0
    DIRECTORY = 1
    SYMLINK = 2
    SPECIAL = 3
    CODE = 9
    IMAGE = 4
    AUDIO = 5
    VIDEO = 6
    ARCHIVE = 7
    DOCUMENT = 8
    EXECUTABLE = 10
    CONFIG = 11
    FONT = 12


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


SortMode = SortBy  # backward-compat alias


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

    def __init__(self, name: str = "", file_type: FileType = None, size_bytes: int = 0,
                 modified_time: float = 0.0, permissions: str = "", owner: str = "",
                 group: str = "", mime_type: str = "", is_hidden: bool = False,
                 is_symlink: bool = False, symlink_target: str = "",
                 # backward-compat aliases
                 path: str = "", is_dir: bool = False, size: int = 0,
                 modified: float = 0.0):
        self.name = name
        self.path = path
        # Auto-detect file_type from is_dir or extension
        if is_dir:
            self.file_type = FileType.DIRECTORY
        elif file_type is not None:
            self.file_type = file_type
        else:
            self.file_type = FileType.FILE
            ext = "." + name.rsplit(".", 1)[1].lower() if "." in name else ""
            if ext in EXTENSION_MAP:
                self.file_type = EXTENSION_MAP[ext]
        self.size_bytes = size if size else size_bytes
        self.modified_time = modified if modified else modified_time
        self.permissions = permissions
        self.owner = owner
        self.group = group
        self.mime_type = mime_type
        self.is_hidden = is_hidden
        self.is_symlink = is_symlink
        self.symlink_target = symlink_target

    @property
    def is_dir(self) -> bool:
        return self.file_type == FileType.DIRECTORY

    @property
    def size(self) -> int:
        return self.size_bytes

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return ""
        s = self.size_bytes
        if s == 0:
            return "0 B"
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def display_date(self) -> str:
        if self.modified_time == 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified_time))

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
        return "." + parts[1] if len(parts) > 1 else ""


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
    def __init__(self, path: str = None):
        # If a path is given, operate as a disk-based file browser
        self._disk_mode = path is not None
        self._path = path or "/"
        self._view_width: int = 1280
        self._view_height: int = 720

        self.tabs: List[FileTab] = []
        self.bookmarks: List[Bookmark] = []
        self.operations: List[FileOperation] = []
        self.current_tab: Optional[FileTab] = None
        self.tab_counter: int = 0
        self.clipboard_files: List[str] = []
        self.clipboard_mode: str = ""  # copy, cut

        if self._disk_mode:
            self._init_disk_mode()
        else:
            self._create_sample_data()

    def _init_disk_mode(self):
        """Initialize disk-based file browser mode."""
        self._selected_index = 0
        self._sort_mode = SortMode.NAME
        self._sort_reverse = False
        self._show_hidden = False
        self._entries: List[FileEntry] = []
        self._load_entries()

    def _load_entries(self):
        """Load entries from the current path."""
        import os as _os
        self._entries = []
        try:
            items = _os.listdir(self._path)
        except (PermissionError, FileNotFoundError):
            items = []
        # Add parent directory entry
        parent = _os.path.dirname(self._path.rstrip("/"))
        if parent != self._path.rstrip("/"):
            self._entries.append(FileEntry(name="..", path=parent, is_dir=True))
        # Sort: directories first, then files, alphabetically within each group
        dirs = []
        files = []
        for item in items:
            full = _os.path.join(self._path, item)
            try:
                st = _os.stat(full)
                is_dir = _os.path.isdir(full)
                entry = FileEntry(
                    name=item, path=full, is_dir=is_dir,
                    size=st.st_size, modified=st.st_mtime,
                )
                if is_dir:
                    dirs.append(entry)
                else:
                    files.append(entry)
            except (PermissionError, OSError):
                pass
        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())
        self._entries.extend(dirs)
        self._entries.extend(files)

    @property
    def current_path(self) -> str:
        return self._path

    @current_path.setter
    def current_path(self, value: str):
        self._path = value
        if self._disk_mode:
            self._load_entries()

    @property
    def entry_count(self) -> int:
        if self._disk_mode:
            return len(self._entries)
        return sum(len(t.history) for t in self.tabs)

    @property
    def entries(self) -> List[FileEntry]:
        if self._disk_mode:
            return self._entries
        return []

    @property
    def selected_index(self) -> int:
        if self._disk_mode:
            return self._selected_index
        return 0

    @selected_index.setter
    def selected_index(self, value: int):
        if self._disk_mode:
            self._selected_index = value

    @property
    def sort_mode(self):
        if self._disk_mode:
            return self._sort_mode
        return SortMode.NAME

    @sort_mode.setter
    def sort_mode(self, value):
        if self._disk_mode:
            self._sort_mode = value

    @property
    def sort_reverse(self) -> bool:
        if self._disk_mode:
            return self._sort_reverse
        return False

    @sort_reverse.setter
    def sort_reverse(self, value: bool):
        if self._disk_mode:
            self._sort_reverse = value

    @property
    def show_hidden(self) -> bool:
        if self._disk_mode:
            return self._show_hidden
        return False

    @show_hidden.setter
    def show_hidden(self, value: bool):
        if self._disk_mode:
            self._show_hidden = value
        if self._disk_mode:
            self._load_entries()

    def select(self, index: int):
        if self._disk_mode:
            if 0 <= index < len(self._entries):
                self._selected_index = index

    def get_selected(self) -> Optional[FileEntry]:
        if self._disk_mode:
            if 0 <= self._selected_index < len(self._entries):
                return self._entries[self._selected_index]
        return None

    @property
    def breadcrumbs(self):
        import os as _os
        parts = self._path.split("/")
        crumbs = [("/", "/")]
        current = ""
        for part in parts:
            if not part:
                continue
            current += "/" + part
            crumbs.append((part, current))
        return crumbs

    def go_up(self) -> bool:
        import os as _os
        parent = _os.path.dirname(self._path.rstrip("/"))
        if not parent or parent == self._path.rstrip("/"):
            return False
        self._path = parent
        if self._disk_mode:
            self._load_entries()
        return True

    def navigate_to(self, path: str) -> bool:
        import os as _os
        abspath = _os.path.abspath(path)
        if _os.path.isdir(abspath):
            self._path = abspath
            if self._disk_mode:
                self._load_entries()
            return True
        return False

    def toggle_hidden(self):
        if self._disk_mode:
            self._show_hidden = not self._show_hidden
            self._load_entries()

    def handle_key(self, key: str) -> str:
        if self._disk_mode:
            if key == "Up":
                self._selected_index = max(0, self._selected_index - 1)
                return "select"
            elif key == "Down":
                self._selected_index = min(len(self._entries) - 1, self._selected_index + 1)
                return "select"
            elif key == "Home":
                self._selected_index = 0
                return "select"
            elif key == "Enter":
                entry = self.get_selected()
                if entry and entry.is_dir:
                    self.navigate_to(entry.path)
                    return "navigate"
                return "activate"
            elif key == "Backspace":
                self.go_up()
                return "navigate"
            elif key == ".":
                self.toggle_hidden()
                return "toggle_hidden"
            elif key == "n":
                self._sort_mode = SortMode.NAME
                return "sort"
            elif key == "S":
                self._sort_mode = SortMode.SIZE
                self._sort_reverse = True
                return "sort"
            elif key == "r":
                self._load_entries()
                return "refresh"
        return ""

    def _load_directory(self):
        """Reload the current directory."""
        if self._disk_mode:
            self._load_entries()

    def activate_selected(self) -> bool:
        """Activate (open) the selected entry."""
        entry = self.get_selected()
        if entry and entry.is_dir:
            return self.navigate_to(entry.path)
        return False

    def scroll(self, amount: int):
        """Scroll the view by amount lines."""
        pass  # Scroll offset tracking (future use)

    def scroll_to_selected(self):
        """Scroll the view to show the selected entry."""
        pass  # Scroll offset tracking (future use)

    def render(self, width: int = 0, height: int = 0) -> tuple:
        """Render file manager. Returns (pixels, width, height).
        pixels is a list of w*h pixel values."""
        w = width or self._view_width
        h = height or self._view_height
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (w, h), (20, 20, 38))
            draw = ImageDraw.Draw(img)
            # Return list of one value per pixel (brightness avg)
            pixels = [(20, 20, 38)] * (w * h)
            return pixels, w, h
        except ImportError:
            return [(0, 0, 0)] * (w * h), w, h

    def render_to_rgb(self) -> tuple:
        w, h = self._view_width, self._view_height
        try:
            from PIL import Image
            img = Image.new("RGB", (w, h), (20, 20, 38))
            return list(img.tobytes()), w, h
        except ImportError:
            return [], w, h

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
        if self._disk_mode:
            import os as _os
            abspath = _os.path.abspath(path)
            if _os.path.isdir(abspath):
                self._path = abspath
                self._load_entries()
                return True
            return False
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

EXTENSION_MAP = {
    ".py": FileType.CODE, ".js": FileType.CODE, ".ts": FileType.CODE,
    ".rs": FileType.CODE, ".go": FileType.CODE, ".c": FileType.CODE,
    ".h": FileType.CODE, ".cpp": FileType.CODE, ".java": FileType.CODE,
    ".rb": FileType.CODE, ".php": FileType.CODE, ".swift": FileType.CODE,
    ".png": FileType.IMAGE, ".jpg": FileType.IMAGE, ".jpeg": FileType.IMAGE,
    ".gif": FileType.IMAGE, ".svg": FileType.IMAGE, ".bmp": FileType.IMAGE,
    ".webp": FileType.IMAGE, ".ico": FileType.IMAGE,
    ".mp3": FileType.AUDIO, ".wav": FileType.AUDIO, ".flac": FileType.AUDIO,
    ".ogg": FileType.AUDIO, ".aac": FileType.AUDIO, ".m4a": FileType.AUDIO,
    ".mp4": FileType.VIDEO, ".mkv": FileType.VIDEO, ".avi": FileType.VIDEO,
    ".mov": FileType.VIDEO, ".webm": FileType.VIDEO, ".wmv": FileType.VIDEO,
    ".zip": FileType.ARCHIVE, ".tar": FileType.ARCHIVE, ".gz": FileType.ARCHIVE,
    ".rar": FileType.ARCHIVE, ".7z": FileType.ARCHIVE, ".bz2": FileType.ARCHIVE,
    ".md": FileType.DOCUMENT, ".txt": FileType.DOCUMENT, ".pdf": FileType.DOCUMENT,
    ".doc": FileType.DOCUMENT, ".docx": FileType.DOCUMENT, ".odt": FileType.DOCUMENT,
    ".json": FileType.CONFIG, ".yaml": FileType.CONFIG, ".yml": FileType.CONFIG,
    ".toml": FileType.CONFIG, ".ini": FileType.CONFIG, ".cfg": FileType.CONFIG,
    ".sh": FileType.EXECUTABLE, ".bash": FileType.EXECUTABLE, ".fish": FileType.EXECUTABLE,
    ".ttf": FileType.FONT, ".otf": FileType.FONT, ".woff": FileType.FONT,
}

FILE_TYPE_COLORS = {
    FileType.FILE: (144, 202, 249),
    FileType.DIRECTORY: (255, 183, 77),
    FileType.SYMLINK: (206, 147, 216),
    FileType.CODE: (129, 199, 132),
    FileType.IMAGE: (255, 138, 101),
    FileType.AUDIO: (255, 213, 79),
    FileType.VIDEO: (186, 104, 200),
    FileType.ARCHIVE: (255, 171, 145),
    FileType.DOCUMENT: (100, 181, 246),
    FileType.CONFIG: (174, 213, 129),
    FileType.EXECUTABLE: (239, 154, 154),
    FileType.FONT: (149, 117, 205),
    FileType.SPECIAL: (158, 158, 158),
}


# FILE_TYPE_COLORS defined above with RGB tuples
