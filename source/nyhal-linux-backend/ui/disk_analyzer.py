"""Disk Usage Analyzer — treemap, duplicate detection, cleanup for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
import time


class FileType(Enum):
    DIRECTORY = "Directory"
    FILE = "File"
    SYMLINK = "Symlink"
    HARDLINK = "Hardlink"


class CleanupCategory(Enum):
    TEMP_FILES = "Temp Files"
    CACHE = "Cache"
    LOGS = "Log Files"
    OLD_DOWNLOADS = "Old Downloads"
    DUPLICATES = "Duplicates"
    EMPTY_DIRS = "Empty Directories"
    LARGE_FILES = "Large Files"
    BROWSER_CACHE = "Browser Cache"
    PACKAGE_CACHE = "Package Cache"
    CORE_DUMPS = "Core Dumps"


@dataclass
class FileEntry:
    name: str
    path: str
    size_bytes: int = 0
    file_type: FileType = FileType.FILE
    modified: float = 0.0
    permissions: str = ""
    owner: str = ""
    group: str = ""
    children: List["FileEntry"] = field(default_factory=list)
    hash_value: str = ""
    duplicate_of: str = ""
    depth: int = 0

    @property
    def size_human(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def size_bar(self) -> str:
        return f"[{'█' * min(int(self.size_bytes / (1024*1024)), 30)}]"

    @property
    def is_duplicate(self) -> bool:
        return bool(self.duplicate_of)

    @property
    def type_icon(self) -> str:
        icons = {
            FileType.DIRECTORY: "📁", FileType.FILE: "📄",
            FileType.SYMLINK: "🔗", FileType.HARDLINK: "🔗",
        }
        return icons.get(self.file_type, "?")


@dataclass
class CleanupSuggestion:
    category: CleanupCategory
    description: str
    size_bytes: int = 0
    file_count: int = 0
    paths: List[str] = field(default_factory=list)
    auto_cleanable: bool = False
    risk_level: str = "Low"  # Low, Medium, High

    @property
    def size_human(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def risk_icon(self) -> str:
        icons = {"Low": "🟢", "Medium": "🟡", "High": "🔴"}
        return icons.get(self.risk_level, "⚪")

    @property
    def category_icon(self) -> str:
        icons = {
            CleanupCategory.TEMP_FILES: "🗑", CleanupCategory.CACHE: "📦",
            CleanupCategory.LOGS: "📝", CleanupCategory.OLD_DOWNLOADS: "⬇",
            CleanupCategory.DUPLICATES: "👯", CleanupCategory.EMPTY_DIRS: "📂",
            CleanupCategory.LARGE_FILES: "📏", CleanupCategory.BROWSER_CACHE: "🌐",
            CleanupCategory.PACKAGE_CACHE: "📦", CleanupCategory.CORE_DUMPS: "💀",
        }
        return icons.get(self.category, "?")


@dataclass
class DiskPartition:
    mount_point: str
    device: str
    fs_type: str = "ext4"
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0

    @property
    def usage_percent(self) -> float:
        if self.total_bytes == 0:
            return 0
        return self.used_bytes / self.total_bytes * 100

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def total_human(self) -> str:
        return self._fmt(self.total_bytes)

    @property
    def used_human(self) -> str:
        return self._fmt(self.used_bytes)

    @property
    def free_human(self) -> str:
        return self._fmt(self.free_bytes)

    @staticmethod
    def _fmt(b: int) -> str:
        if b < 1024**3:
            return f"{b / 1024**2:.0f} MB"
        return f"{b / 1024**3:.1f} GB"


@dataclass
class DuplicateGroup:
    hash_value: str
    files: List[FileEntry] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        if len(self.files) < 2:
            return 0
        return self.files[0].size_bytes * (len(self.files) - 1)

    @property
    def wasted_human(self) -> str:
        b = self.wasted_bytes
        if b < 1024**2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"


class DiskAnalyzer:
    def __init__(self):
        self._root: Optional[FileEntry] = None
        self._partitions: List[DiskPartition] = []
        self._duplicates: List[DuplicateGroup] = []
        self._suggestions: List[CleanupSuggestion] = []
        self._selected_entry: int = 0
        self._current_path: str = "/"
        self._view_mode: str = "treemap"
        self._show_hidden: bool = False
        self._sort_by: str = "size"
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        self._partitions = [
            DiskPartition("/", "/dev/nvme0n1p2", "ext4", 500 * 1024**3, 320 * 1024**3, 180 * 1024**3),
            DiskPartition("/home", "/dev/nvme0n1p3", "ext4", 1000 * 1024**3, 680 * 1024**3, 320 * 1024**3),
            DiskPartition("/boot", "/dev/nvme0n1p1", "vfat", 512 * 1024**2, 180 * 1024**2, 332 * 1024**2),
            DiskPartition("/tmp", "/dev/zram0", "tmpfs", 16 * 1024**3, 2 * 1024**3, 14 * 1024**3),
        ]

        self._root = FileEntry("/", "/", 320 * 1024**3, FileType.DIRECTORY, children=[
            FileEntry("usr", "/usr", 45 * 1024**3, FileType.DIRECTORY, children=[
                FileEntry("lib", "/usr/lib", 28 * 1024**3, FileType.DIRECTORY),
                FileEntry("bin", "/usr/bin", 5 * 1024**3, FileType.DIRECTORY),
                FileEntry("share", "/usr/share", 12 * 1024**3, FileType.DIRECTORY),
            ]),
            FileEntry("home", "/home", 180 * 1024**3, FileType.DIRECTORY, children=[
                FileEntry("user", "/home/user", 150 * 1024**3, FileType.DIRECTORY, children=[
                    FileEntry("Documents", "/home/user/Documents", 25 * 1024**3, FileType.DIRECTORY),
                    FileEntry("Downloads", "/home/user/Downloads", 45 * 1024**3, FileType.DIRECTORY),
                    FileEntry("Pictures", "/home/user/Pictures", 35 * 1024**3, FileType.DIRECTORY),
                    FileEntry("Videos", "/home/user/Videos", 28 * 1024**3, FileType.DIRECTORY),
                    FileEntry(".cache", "/home/user/.cache", 12 * 1024**3, FileType.DIRECTORY),
                    FileEntry(".local", "/home/user/.local", 5 * 1024**3, FileType.DIRECTORY),
                ]),
            ]),
            FileEntry("var", "/var", 35 * 1024**3, FileType.DIRECTORY, children=[
                FileEntry("log", "/var/log", 8 * 1024**3, FileType.DIRECTORY),
                FileEntry("cache", "/var/cache", 18 * 1024**3, FileType.DIRECTORY),
                FileEntry("lib", "/var/lib", 9 * 1024**3, FileType.DIRECTORY),
            ]),
            FileEntry("opt", "/opt", 25 * 1024**3, FileType.DIRECTORY),
            FileEntry("tmp", "/tmp", 2 * 1024**3, FileType.DIRECTORY),
            FileEntry("snap", "/snap", 33 * 1024**3, FileType.DIRECTORY),
        ])

        self._duplicates = [
            DuplicateGroup("abc123", [
                FileEntry("photo.jpg", "/home/user/Pictures/photo.jpg", 4 * 1024**2),
                FileEntry("photo (1).jpg", "/home/user/Downloads/photo (1).jpg", 4 * 1024**2),
            ]),
            DuplicateGroup("def456", [
                FileEntry("setup.deb", "/home/user/Downloads/setup.deb", 85 * 1024**2),
                FileEntry("setup.deb", "/tmp/setup.deb", 85 * 1024**2),
            ]),
            DuplicateGroup("ghi789", [
                FileEntry("report.pdf", "/home/user/Documents/report.pdf", 2 * 1024**2),
                FileEntry("report_backup.pdf", "/home/user/Documents/report_backup.pdf", 2 * 1024**2),
            ]),
        ]

        self._suggestions = [
            CleanupSuggestion(CleanupCategory.PACKAGE_CACHE, "APT package cache (stale .deb files)", 850 * 1024**2, 234, ["/var/cache/apt/archives/"], True, "Low"),
            CleanupSuggestion(CleanupCategory.BROWSER_CACHE, "Firefox/Chrome cached files", 2.1 * 1024**3, 45000, ["/home/user/.cache/mozilla/", "/home/user/.cache/google-chrome/"], True, "Low"),
            CleanupSuggestion(CleanupCategory.LOGS, "Rotated and old log files", 3.2 * 1024**3, 1200, ["/var/log/"], True, "Low"),
            CleanupSuggestion(CleanupCategory.TEMP_FILES, "Temporary files older than 7 days", 450 * 1024**2, 890, ["/tmp/"], True, "Low"),
            CleanupSuggestion(CleanupCategory.DUPLICATES, "Duplicate files wasting disk space", 194 * 1024**2, 3, [], False, "Medium"),
            CleanupSuggestion(CleanupCategory.OLD_DOWNLOADS, "Downloads older than 30 days", 12.5 * 1024**3, 450, ["/home/user/Downloads/"], False, "Medium"),
            CleanupSuggestion(CleanupCategory.LARGE_FILES, "Files larger than 1GB", 8.2 * 1024**3, 12, [], False, "High"),
            CleanupSuggestion(CleanupCategory.CORE_DUMPS, "Core dump files", 1.8 * 1024**3, 5, ["/var/crash/"], True, "Low"),
        ]

    @property
    def selected_entry(self) -> Optional[FileEntry]:
        if self._root and 0 <= self._selected_entry < len(self._root.children):
            return self._root.children[self._selected_entry]
        return None

    @property
    def total_disk(self) -> str:
        return self._partitions[0].total_human if self._partitions else "0"

    @property
    def used_disk(self) -> str:
        return self._partitions[0].used_human if self._partitions else "0"

    @property
    def free_disk(self) -> str:
        return self._partitions[0].free_human if self._partitions else "0"

    @property
    def total_duplicates_wasted(self) -> str:
        total = sum(d.wasted_bytes for d in self._duplicates)
        if total < 1024**2:
            return f"{total / 1024:.1f} KB"
        return f"{total / 1024**2:.1f} MB"

    @property
    def total_cleanup_savings(self) -> str:
        total = sum(s.size_bytes for s in self._suggestions)
        if total < 1024**3:
            return f"{total / 1024**2:.0f} MB"
        return f"{total / 1024**3:.1f} GB"

    def select_entry(self, idx: int):
        self._selected_entry = idx

    def handle_input(self, key: str):
        key = key.lower()
        if key == "h":
            self._show_hidden = not self._show_hidden
        elif key == "d":
            self._view_mode = "duplicates"
        elif key == "c":
            self._view_mode = "cleanup"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DISK USAGE ANALYZER                               ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Disk overview
        lines.append(f"  💾 Disk: {self.used_disk} / {self.total_disk} used  Free: {self.free_disk}")
        lines.append(f"  👯 Duplicates: {len(self._duplicates)} groups ({self.total_duplicates_wasted} wasted)  🧹 Cleanup: {self.total_cleanup_savings} recoverable")
        lines.append("")

        # Partitions
        lines.append("  ── Partitions ──")
        for p in self._partitions:
            warn = "🔴" if p.usage_percent > 90 else "🟡" if p.usage_percent > 75 else "🟢"
            lines.append(f"  {warn} {p.mount_point:<12s} {p.device:<16s} [{p.usage_bar}] {p.usage_percent:.1f}%  {p.used_human}/{p.total_human}  {p.fs_type}")
        lines.append("")

        # Treemap visualization
        if self._root:
            lines.append("  ── Treemap ──")
            for child in self._root.children:
                ratio = child.size_bytes / self._root.size_bytes if self._root.size_bytes else 0
                bar_len = int(ratio * 50)
                bar = "█" * bar_len
                lines.append(f"  {child.name:<12s} {bar} {child.size_human} ({ratio * 100:.1f}%)")
                if child.children:
                    for sub in child.children[:4]:
                        sub_ratio = sub.size_bytes / child.size_bytes if child.size_bytes else 0
                        sub_bar = "▓" * int(sub_ratio * 30)
                        lines.append(f"    {sub.name:<12s} {sub_bar} {sub.size_human}")
            lines.append("")

        # Cleanup suggestions
        if self._suggestions:
            lines.append("  ── Cleanup Suggestions ──")
            for s in self._suggestions:
                auto = "🔄" if s.auto_cleanable else "  "
                lines.append(f"  {s.category_icon} {s.category.value:<20s} {s.size_human:>10s}  {s.file_count:>6d} files  {auto} Risk: {s.risk_level}")
            lines.append("")

        # Duplicates
        if self._duplicates:
            lines.append("  ── Duplicate Files ──")
            for dg in self._duplicates:
                lines.append(f"  👯 {dg.files[0].name}  {len(dg.files)} copies  {dg.wasted_human} wasted")
                for f in dg.files:
                    lines.append(f"      📄 {f.path}")
            lines.append("")

        lines.append("  [↑↓]Select [D]Duplicates [C]Cleanup [H]Hidden [S]Sort [R]Rescan")
        return lines
