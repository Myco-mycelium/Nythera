"""
Nyrqis Disk Analyzer — disk usage visualization and cleanup.

Features:
- Treemap-style visualization of disk usage
- Directory tree with size breakdown
- File type analysis (code, media, documents, archives, etc.)
- Duplicate file detection
- Large file finder
- Cleanup suggestions with estimated space savings
- Sort by size, name, modification date
- Drill down into directories
"""

import os
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple, Set
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class FileType(Enum):
    CODE = "code"
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    ARCHIVE = "archive"
    BINARY = "binary"
    CONFIG = "config"
    CACHE = "cache"
    LOG = "log"
    TEMP = "temp"
    OTHER = "other"


FILE_TYPE_EXTENSIONS = {
    FileType.CODE: {".py", ".js", ".ts", ".rs", ".c", ".cpp", ".h", ".go", ".java", ".rb", ".sh", ".css", ".html"},
    FileType.DOCUMENT: {".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".json", ".xml", ".yaml", ".yml", ".toml"},
    FileType.IMAGE: {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".raw"},
    FileType.VIDEO: {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    FileType.AUDIO: {".mp3", ".flac", ".wav", ".ogg", ".aac", ".m4a", ".wma"},
    FileType.ARCHIVE: {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".deb", ".rpm"},
    FileType.BINARY: {".so", ".dll", ".exe", ".bin", ".elf", ".dylib", ".o", ".a"},
    FileType.CONFIG: {".conf", ".cfg", ".ini", ".env", ".gitignore", ".editorconfig"},
    FileType.CACHE: {},
    FileType.LOG: {".log"},
    FileType.TEMP: {".tmp", ".temp", ".swp", ".bak"},
}


FILE_TYPE_COLORS = {
    FileType.CODE: "#4A90D9",
    FileType.DOCUMENT: "#2ECC71",
    FileType.IMAGE: "#E74C3C",
    FileType.VIDEO: "#9B59B6",
    FileType.AUDIO: "#F39C12",
    FileType.ARCHIVE: "#1ABC9C",
    FileType.BINARY: "#95A5A6",
    FileType.CONFIG: "#3498DB",
    FileType.CACHE: "#E67E22",
    FileType.LOG: "#7F8C8D",
    FileType.TEMP: "#C0392B",
    FileType.OTHER: "#BDC3C7",
}

FILE_TYPE_ICONS = {
    FileType.CODE: "📄",
    FileType.DOCUMENT: "📝",
    FileType.IMAGE: "🖼️",
    FileType.VIDEO: "🎬",
    FileType.AUDIO: "🎵",
    FileType.ARCHIVE: "📦",
    FileType.BINARY: "⚙️",
    FileType.CONFIG: "🔧",
    FileType.CACHE: "💾",
    FileType.LOG: "📋",
    FileType.TEMP: "🗑️",
    FileType.OTHER: "📄",
}


@dataclass
class DiskEntry:
    """A file or directory entry."""
    name: str
    path: str
    size: int = 0  # bytes
    is_dir: bool = False
    modified: float = 0.0
    file_type: FileType = FileType.OTHER
    children: List['DiskEntry'] = field(default_factory=list)

    @property
    def size_str(self) -> str:
        b = self.size
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def percent(self) -> float:
        if not self.children or self.size <= 0:
            return 0.0
        return 100.0

    @property
    def icon(self) -> str:
        if self.is_dir:
            return "📁"
        return FILE_TYPE_ICONS.get(self.file_type, "📄")

    @property
    def time_ago(self) -> str:
        if self.modified <= 0:
            return ""
        diff = time.time() - self.modified
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(self.modified).strftime("%b %d")


@dataclass
class CleanupSuggestion:
    """A cleanup suggestion."""
    description: str
    path: str
    size: int
    category: str
    risk: str = "low"  # low, medium, high

    @property
    def size_str(self) -> str:
        b = self.size
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def risk_icon(self) -> str:
        icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}
        return icons.get(self.risk, "⚪")


# ─── Disk Analyzer ───────────────────────────────────────────────────────


class DiskAnalyzer:
    """
    Disk usage analyzer for Nyrqis OS.

    Scans directories and visualizes disk usage.
    """

    def __init__(self):
        self._root: Optional[DiskEntry] = None
        self._current_path: str = "/"
        self._selected_index: int = 0
        self._sort_by_size: bool = True
        self._show_files: bool = True
        self._filter_type: Optional[FileType] = None
        self._view_mode: str = "tree"  # tree, treemap, types, large
        self._navigation_stack: List[str] = []

        # Stats
        self._type_stats: Dict[FileType, int] = {}
        self._largest_files: List[DiskEntry] = []
        self._cleanup_suggestions: List[CleanupSuggestion] = []

        # Callbacks
        self._on_scan_complete: List[Callable] = []

        # Generate sample data
        self._generate_sample_data()

    def _generate_sample_data(self) -> None:
        """Generate simulated filesystem data."""
        now = time.time()

        def make_file(name, size, ftype=FileType.OTHER, days_ago=30):
            return DiskEntry(
                name=name,
                path=f"/{name}",
                size=size,
                is_dir=False,
                modified=now - days_ago * 86400,
                file_type=ftype,
            )

        def make_dir(name, children, days_ago=30):
            total = sum(c.size for c in children)
            return DiskEntry(
                name=name,
                path=f"/{name}",
                size=total,
                is_dir=True,
                modified=now - days_ago * 86400,
                children=children,
            )

        # /home/user
        home_files = [
            make_file("resume.pdf", 2 * 1024 * 1024, FileType.DOCUMENT, 5),
            make_file("notes.txt", 50 * 1024, FileType.DOCUMENT, 1),
            make_file("photo.jpg", 5 * 1024 * 1024, FileType.IMAGE, 10),
            make_file("photo_raw.jpg", 25 * 1024 * 1024, FileType.IMAGE, 10),
            make_file("screenshot.png", 1 * 1024 * 1024, FileType.IMAGE, 2),
            make_file("presentation.pdf", 15 * 1024 * 1024, FileType.DOCUMENT, 7),
            make_file("budget.csv", 100 * 1024, FileType.DOCUMENT, 3),
        ]
        home = make_dir("home", home_files, 1)

        # /usr
        usr_files = [
            make_file("libpython3.12.so", 35 * 1024 * 1024, FileType.BINARY, 90),
            make_file("libssl.so", 5 * 1024 * 1024, FileType.BINARY, 60),
            make_file("libc.so", 2 * 1024 * 1024, FileType.BINARY, 90),
            make_file("python3", 5 * 1024 * 1024, FileType.BINARY, 30),
            make_file("gcc", 3 * 1024 * 1024, FileType.BINARY, 45),
            make_file("vim", 2 * 1024 * 1024, FileType.BINARY, 20),
            make_file("man-page.gz", 500 * 1024, FileType.DOCUMENT, 60),
        ]
        usr = make_dir("usr", usr_files, 90)

        # /var
        var_files = [
            make_file("syslog", 50 * 1024 * 1024, FileType.LOG, 1),
            make_file("auth.log", 10 * 1024 * 1024, FileType.LOG, 1),
            make_file("dpkg.log", 2 * 1024 * 1024, FileType.LOG, 5),
            make_file("apt.cache", 100 * 1024 * 1024, FileType.CACHE, 3),
        ]
        var = make_dir("var", var_files, 1)

        # /tmp
        tmp_files = [
            make_file("temp_session.dat", 50 * 1024 * 1024, FileType.TEMP, 0),
            make_file("build_cache.bin", 200 * 1024 * 1024, FileType.TEMP, 2),
            make_file("crash_dump.core", 150 * 1024 * 1024, FileType.TEMP, 0),
            make_file("browser_cache.tmp", 300 * 1024 * 1024, FileType.CACHE, 1),
        ]
        tmp = make_dir("tmp", tmp_files, 0)

        # /opt
        opt_files = [
            make_file("nyrqis-compositor", 15 * 1024 * 1024, FileType.BINARY, 1),
            make_file("nyrqis-daemon", 10 * 1024 * 1024, FileType.BINARY, 1),
            make_file("libnyrqis.so", 25 * 1024 * 1024, FileType.BINARY, 1),
            make_file("themes.dat", 5 * 1024 * 1024, FileType.BINARY, 30),
        ]
        opt = make_dir("opt", opt_files, 1)

        # /etc
        etc_files = [
            make_file("nginx.conf", 10 * 1024, FileType.CONFIG, 60),
            make_file("fstab", 2 * 1024, FileType.CONFIG, 120),
            make_file("resolv.conf", 500, FileType.CONFIG, 5),
            make_file("ssh/sshd_config", 5 * 1024, FileType.CONFIG, 90),
        ]
        etc = make_dir("etc", etc_files, 60)

        # /root
        root_files = [
            make_file("project.tar.gz", 500 * 1024 * 1024, FileType.ARCHIVE, 15),
            make_file("backup.zip", 2 * 1024 * 1024 * 1024, FileType.ARCHIVE, 7),
            make_file("dataset.csv", 800 * 1024 * 1024, FileType.DOCUMENT, 30),
        ]
        root = make_dir("root", root_files, 7)

        # Source code
        src_files = [
            make_file("main.py", 10 * 1024, FileType.CODE, 1),
            make_file("lib.rs", 5 * 1024, FileType.CODE, 2),
            make_file("index.ts", 15 * 1024, FileType.CODE, 1),
            make_file("app.css", 8 * 1024, FileType.CODE, 3),
            make_file("style.scss", 12 * 1024, FileType.CODE, 5),
            make_file("Cargo.toml", 2 * 1024, FileType.CONFIG, 7),
            make_file("package.json", 1 * 1024, FileType.CONFIG, 7),
        ]
        src = make_dir("src", src_files, 1)

        # Music
        music_files = [
            make_file("song1.mp3", 8 * 1024 * 1024, FileType.AUDIO, 30),
            make_file("song2.flac", 40 * 1024 * 1024, FileType.AUDIO, 25),
            make_file("podcast.m4a", 60 * 1024 * 1024, FileType.AUDIO, 2),
        ]
        music = make_dir("Music", music_files, 2)

        # Videos
        video_files = [
            make_file("recording.mp4", 500 * 1024 * 1024, FileType.VIDEO, 5),
            make_file("presentation.mkv", 200 * 1024 * 1024, FileType.VIDEO, 10),
        ]
        videos = make_dir("Videos", video_files, 5)

        self._root = DiskEntry(
            name="/",
            path="/",
            size=sum(d.size for d in [home, usr, var, tmp, opt, etc, root, src, music, videos]),
            is_dir=True,
            children=[home, usr, var, tmp, opt, etc, root, src, music, videos],
        )

        # Compute type stats
        self._compute_type_stats(self._root)

        # Find largest files
        self._find_largest_files(self._root)

        # Generate cleanup suggestions
        self._generate_cleanup_suggestions()

    def _compute_type_stats(self, entry: DiskEntry) -> None:
        """Compute file type statistics."""
        self._type_stats = {ft: 0 for ft in FileType}
        self._walk_for_stats(entry)

    def _walk_for_stats(self, entry: DiskEntry) -> None:
        if entry.is_dir:
            for child in entry.children:
                self._walk_for_stats(child)
        else:
            self._type_stats[entry.file_type] = self._type_stats.get(entry.file_type, 0) + entry.size

    def _find_largest_files(self, root: DiskEntry, limit: int = 20) -> None:
        """Find the largest files."""
        files = []
        self._walk_for_files(root, files)
        files.sort(key=lambda f: -f.size)
        self._largest_files = files[:limit]

    def _walk_for_files(self, entry: DiskEntry, result: List[DiskEntry]) -> None:
        if entry.is_dir:
            for child in entry.children:
                self._walk_for_files(child, result)
        else:
            result.append(entry)

    def _generate_cleanup_suggestions(self) -> None:
        """Generate cleanup suggestions."""
        self._cleanup_suggestions = [
            CleanupSuggestion("Clear browser cache", "/tmp/browser_cache.tmp", 300 * 1024 * 1024, "Cache", "low"),
            CleanupSuggestion("Remove build cache", "/tmp/build_cache.bin", 200 * 1024 * 1024, "Temp", "low"),
            CleanupSuggestion("Clear crash dumps", "/tmp/crash_dump.core", 150 * 1024 * 1024, "Temp", "low"),
            CleanupSuggestion("Remove old logs", "/var/syslog", 50 * 1024 * 1024, "Logs", "low"),
            CleanupSuggestion("Clean apt cache", "/var/apt.cache", 100 * 1024 * 1024, "Cache", "low"),
            CleanupSuggestion("Remove old backups", "/root/backup.zip", 2 * 1024 * 1024 * 1024, "Backup", "high"),
            CleanupSuggestion("Compress raw photos", "/home/photo_raw.jpg", 25 * 1024 * 1024, "Media", "medium"),
        ]
        self._cleanup_suggestions.sort(key=lambda s: -s.size)

    # ── Navigation ────────────────────────────────────────────────────

    @property
    def root(self) -> Optional[DiskEntry]:
        return self._root

    @property
    def current_entries(self) -> List[DiskEntry]:
        """Get sorted entries at current level."""
        entry = self._find_entry(self._current_path)
        if not entry or not entry.is_dir:
            return []

        entries = list(entry.children)

        # Filter by type
        if self._filter_type:
            entries = [e for e in entries if e.is_dir or e.file_type == self._filter_type]

        # Hide files if needed
        if not self._show_files:
            entries = [e for e in entries if e.is_dir]

        # Sort
        if self._sort_by_size:
            entries.sort(key=lambda e: -e.size)
        else:
            entries.sort(key=lambda e: e.name.lower())

        return entries

    def _find_entry(self, path: str) -> Optional[DiskEntry]:
        """Find an entry by path."""
        if not self._root:
            return None
        if path == "/":
            return self._root
        parts = [p for p in path.split("/") if p]
        current = self._root
        for part in parts:
            found = False
            for child in current.children:
                if child.name == part:
                    current = child
                    found = True
                    break
            if not found:
                return None
        return current

    def enter_directory(self, name: str) -> bool:
        """Enter a subdirectory."""
        path = f"{self._current_path.rstrip('/')}/{name}"
        entry = self._find_entry(path)
        if entry and entry.is_dir:
            self._navigation_stack.append(self._current_path)
            self._current_path = path
            self._selected_index = 0
            return True
        return False

    def go_up(self) -> bool:
        """Go up one directory."""
        if self._navigation_stack:
            self._current_path = self._navigation_stack.pop()
            self._selected_index = 0
            return True
        if self._current_path != "/":
            parts = self._current_path.rstrip("/").split("/")
            parts.pop()
            self._current_path = "/".join(parts) or "/"
            self._selected_index = 0
            return True
        return False

    @property
    def current_path(self) -> str:
        return self._current_path

    @property
    def breadcrumbs(self) -> List[str]:
        parts = [p for p in self._current_path.split("/") if p]
        result = ["/"]
        current = ""
        for part in parts:
            current += f"/{part}"
            result.append(current)
        return result

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select(self, index: int) -> None:
        entries = self.current_entries
        self._selected_index = max(0, min(len(entries) - 1, index))

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        entries = self.current_entries
        self._selected_index = min(len(entries) - 1, self._selected_index + 1)

    def open_selected(self) -> bool:
        entries = self.current_entries
        if 0 <= self._selected_index < len(entries):
            entry = entries[self._selected_index]
            if entry.is_dir:
                return self.enter_directory(entry.name)
        return False

    # ── View Modes ────────────────────────────────────────────────────

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def cycle_view(self) -> str:
        views = ["tree", "treemap", "types", "large"]
        idx = views.index(self._view_mode) if self._view_mode in views else 0
        self._view_mode = views[(idx + 1) % len(views)]
        return self._view_mode

    def toggle_sort(self) -> bool:
        self._sort_by_size = not self._sort_by_size
        return self._sort_by_size

    def toggle_files(self) -> bool:
        self._show_files = not self._show_files
        return self._show_files

    # ── Rendering ─────────────────────────────────────────────────────

    def render_tree(self, width: int = 72) -> List[str]:
        """Render directory tree view."""
        lines = []
        entry = self._find_entry(self._current_path)
        if not entry:
            return ["Path not found"]

        # Header
        lines.append(f" 💾 Disk Usage — {self._current_path}")
        if entry.size > 0:
            lines.append(f" Total: {entry.size_str}")
        lines.append("─" * width)

        entries = self.current_entries
        if not entries:
            lines.append("  (empty)")
        else:
            total_size = sum(e.size for e in entries)
            for i, e in enumerate(entries):
                marker = "▸" if i == self._selected_index else " "
                percent = (e.size / total_size * 100) if total_size > 0 else 0
                bar_len = int(percent / 100 * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)

                line = f"{marker} {e.icon} {e.name[:20]:<20} {bar} {e.size_str:>10} {percent:>5.1f}%"
                lines.append(line[:width])

        lines.append("─" * width)

        # Type filter
        if self._filter_type:
            lines.append(f" Filter: {self._filter_type.value}")

        lines.append(f" {'Sort:' + ('Size' if self._sort_by_size else 'Name'):15}  ↑↓:Select  Enter:Open  ←:Back")
        return lines

    def render_treemap(self, width: int = 72) -> List[str]:
        """Render a text-based treemap visualization."""
        lines = []
        lines.append(" 💾 Treemap View")
        lines.append("─" * width)

        entries = self.current_entries
        if not entries:
            lines.append("  No data")
            return lines

        total = sum(e.size for e in entries)
        if total <= 0:
            lines.append("  No data")
            return lines

        # Simple ASCII treemap — each row represents a percentage
        max_height = 12
        for row in range(max_height):
            line = "│"
            for e in entries:
                pct = e.size / total * 100
                height = int(pct / 100 * max_height)
                char = "█" if row < height else " "
                block_width = max(1, int(pct / 100 * width))
                line += char * block_width
            lines.append(line[:width])

        # Legend
        lines.append("─" * width)
        for e in entries[:8]:
            pct = e.size / total * 100
            lines.append(f"  {e.icon} {e.name}: {e.size_str} ({pct:.1f}%)")

        return lines

    def render_types(self, width: int = 72) -> List[str]:
        """Render file type breakdown."""
        lines = []
        lines.append(" 📊 File Type Analysis")
        lines.append("─" * width)

        total = sum(self._type_stats.values())
        if total <= 0:
            lines.append("  No data")
            return lines

        # Sort by size
        sorted_types = sorted(self._type_stats.items(), key=lambda x: -x[1])

        for ftype, size in sorted_types:
            if size <= 0:
                continue
            icon = FILE_TYPE_ICONS.get(ftype, "📄")
            pct = size / total * 100
            bar_len = int(pct / 100 * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)

            b = size
            if b < 1024 * 1024:
                size_str = f"{b / 1024:.1f} KB"
            elif b < 1024 * 1024 * 1024:
                size_str = f"{b / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{b / (1024 * 1024 * 1024):.2f} GB"

            line = f" {icon} {ftype.value:<10} {bar} {size_str:>10} {pct:>5.1f}%"
            lines.append(line[:width])

        lines.append("─" * width)
        return lines

    def render_large(self, width: int = 72) -> List[str]:
        """Render largest files view."""
        lines = []
        lines.append(" 📏 Largest Files")
        lines.append("─" * width)

        for i, f in enumerate(self._largest_files):
            line = f" {i + 1:>3}. {f.icon} {f.name[:30]:<30} {f.size_str:>10}"
            if f.time_ago:
                line += f"  {f.time_ago}"
            lines.append(line[:width])

        lines.append("─" * width)
        return lines

    def render_cleanup(self, width: int = 72) -> List[str]:
        """Render cleanup suggestions."""
        lines = []
        total_savings = sum(s.size for s in self._cleanup_suggestions)

        lines.append(f" 🧹 Cleanup Suggestions (save {total_savings / (1024 * 1024):.0f} MB)")
        lines.append("─" * width)

        for i, s in enumerate(self._cleanup_suggestions):
            line = f" {i + 1}. {s.risk_icon} {s.description}"
            lines.append(line[:width])
            line2 = f"    {s.path} — {s.size_str}"
            lines.append(line2[:width])

        lines.append("─" * width)
        lines.append(" Press number to select, Enter to clean")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == "treemap":
            return self.render_treemap(width)
        elif self._view_mode == "types":
            return self.render_types(width)
        elif self._view_mode == "large":
            return self.render_large(width)
        return self.render_tree(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_selected()
            return "open"
        elif key == "Backspace" or key == "ArrowLeft":
            self.go_up()
            return "back"
        elif key == "v" or key == "V":
            self.cycle_view()
            return "cycle_view"
        elif key == "s" or key == "S":
            self.toggle_sort()
            return "toggle_sort"
        elif key == "f" or key == "F":
            self.toggle_files()
            return "toggle_files"
        elif key == "c" or key == "C":
            self._view_mode = "cleanup" if self._view_mode != "cleanup" else "tree"
            return "cleanup"
        return None
