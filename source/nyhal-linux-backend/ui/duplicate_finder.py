from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import hashlib


class HashMethod(Enum):
    MD5 = "md5"
    SHA256 = "sha256"
    CRC32 = "crc32"
    QUICK_SIZE = "size-only"


class DuplicateGroup(Enum):
    EXACT = "exact"
    SIMILAR = "similar"
    NAME_ONLY = "name-only"


class FileStatus(Enum):
    ORIGINAL = "original"
    DUPLICATE = "duplicate"
    KEEP = "keep"
    DELETE = "delete"
    UNDECIDED = "undecided"


@dataclass
class DuplicateEntry:
    path: str
    size_bytes: int
    hash_value: str
    modified: float
    status: FileStatus = FileStatus.UNDECIDED
    group_id: int = 0
    is_protected: bool = False

    @property
    def display_size(self) -> str:
        s = self.size_bytes
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        if s >= 1024:
            return f"{s / 1024:.1f} KB"
        return f"{s} B"

    @property
    def filename(self) -> str:
        return self.path.split("/")[-1]

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.modified) / 86400)
        if age == 0:
            return "today"
        return f"{age}d ago"

    @property
    def hash_display(self) -> str:
        return self.hash_value[:16] + "..."


@dataclass
class DuplicateGroupEntry:
    group_id: int
    files: list = field(default_factory=list)
    total_size: int = 0
    waste_size: int = 0
    kept_count: int = 0

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def waste_display(self) -> str:
        s = self.waste_size
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        return f"{s / 1024:.1f} KB"


@dataclass
class ScanResult:
    timestamp: float
    total_scanned: int
    duplicate_groups: int
    total_duplicates: int
    total_waste_bytes: int
    duration_ms: int

    @property
    def waste_display(self) -> str:
        s = self.total_waste_bytes
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        return f"{s / 1024:.1f} KB"


class DuplicateFinder:
    def __init__(self):
        self._entries: list[DuplicateEntry] = []
        self._groups: list[DuplicateGroupEntry] = []
        self._selected_entry: int = 0
        self._selected_group: int = 0
        self._hash_method: HashMethod = HashMethod.SHA256
        self._scan_results: list[ScanResult] = []
        self._scan_dirs: list[str] = ["/home/user", "/tmp", "/var/cache"]
        self._ignore_patterns: list[str] = ["*.pyc", "__pycache__", ".git", "node_modules"]
        self._min_size: int = 0
        self._max_size: int = 0
        self._is_scanning: bool = False
        self._progress: float = 0
        self._view: str = "groups"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        g1 = DuplicateGroupEntry(1, [
            DuplicateEntry("/home/user/Documents/report.pdf", 2_457_600, "a1b2c3d4e5f6", now - 30, FileStatus.KEEP, 1),
            DuplicateEntry("/home/user/Downloads/report.pdf", 2_457_600, "a1b2c3d4e5f6", now - 7, FileStatus.DUPLICATE, 1),
            DuplicateEntry("/tmp/report_backup.pdf", 2_457_600, "a1b2c3d4e5f6", now - 3, FileStatus.DELETE, 1),
        ], 7_372_800, 4_915_200)
        self._groups.append(g1)

        g2 = DuplicateGroupEntry(2, [
            DuplicateEntry("/home/user/Pictures/photo.jpg", 5_242_880, "b2c3d4e5f6a7", now - 60, FileStatus.KEEP, 2),
            DuplicateEntry("/home/user/Pictures/photo(1).jpg", 5_242_880, "b2c3d4e5f6a7", now - 5, FileStatus.DUPLICATE, 2),
        ], 10_485_760, 5_242_880)
        self._groups.append(g2)

        g3 = DuplicateGroupEntry(3, [
            DuplicateEntry("/home/user/Projects/main.py", 4_096, "c3d4e5f6a7b8", now - 14, FileStatus.KEEP, 3),
            DuplicateEntry("/home/user/Projects/main_backup.py", 4_096, "c3d4e5f6a7b8", now - 14, FileStatus.DUPLICATE, 3),
            DuplicateEntry("/home/user/old_projects/main.py", 4_096, "c3d4e5f6a7b8", now - 90, FileStatus.DELETE, 3),
        ], 12_288, 8_192)
        self._groups.append(g3)

        g4 = DuplicateGroupEntry(4, [
            DuplicateEntry("/home/user/Music/song.mp3", 10_485_760, "d4e5f6a7b8c9", now - 120, FileStatus.KEEP, 4),
            DuplicateEntry("/home/user/Music/song - Copy.mp3", 10_485_760, "d4e5f6a7b8c9", now - 50, FileStatus.DUPLICATE, 4),
        ], 20_971_520, 10_485_760)
        self._groups.append(g4)

        self._entries = [f for g in self._groups for f in g.files]

        self._scan_results = [
            ScanResult(now - 86400, 45000, 12, 28, 35_651_584, 12500),
            ScanResult(now - 86400 * 7, 42000, 8, 18, 18_874_368, 11200),
        ]

    @property
    def selected_entry(self) -> Optional[DuplicateEntry]:
        if 0 <= self._selected_entry < len(self._entries):
            return self._entries[self._selected_entry]
        return None

    @property
    def selected_group(self) -> Optional[DuplicateGroupEntry]:
        if 0 <= self._selected_group < len(self._groups):
            return self._groups[self._selected_group]
        return None

    @property
    def total_duplicates(self) -> int:
        return sum(g.count - 1 for g in self._groups)

    @property
    def total_waste(self) -> int:
        return sum(g.waste_size for g in self._groups)

    @property
    def total_waste_display(self) -> str:
        s = self.total_waste
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        return f"{s / 1024:.1f} KB"

    @property
    def total_files(self) -> int:
        return len(self._entries)

    def select_entry(self, idx: int):
        if 0 <= idx < len(self._entries):
            self._selected_entry = idx

    def select_group(self, idx: int):
        if 0 <= idx < len(self._groups):
            self._selected_group = idx

    def mark_delete(self, entry_idx: int):
        if 0 <= entry_idx < len(self._entries):
            entry = self._entries[entry_idx]
            if not entry.is_protected:
                entry.status = FileStatus.DELETE

    def mark_keep(self, entry_idx: int):
        if 0 <= entry_idx < len(self._entries):
            self._entries[entry_idx].status = FileStatus.KEEP

    def auto_select(self):
        for group in self._groups:
            for file in group.files:
                file.status = FileStatus.DUPLICATE
            if group.files:
                group.files[0].status = FileStatus.KEEP

    @staticmethod
    def compute_hash(data: str, method: HashMethod = HashMethod.SHA256) -> str:
        h = hashlib.new(method.value if method.value != "size-only" else "md5")
        h.update(data.encode())
        return h.hexdigest()

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DUPLICATE FINDER                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Hash: {self._hash_method.value}  Groups: {len(self._groups)}  Duplicates: {self.total_duplicates}  Waste: {self.total_waste_display}")
        lines.append(f"  Scanning: {'YES' if self._is_scanning else 'NO'}  Min Size: {self._min_size}  Dirs: {len(self._scan_dirs)}")
        lines.append("")
        lines.append("  ── Duplicate Groups ──")
        for i, g in enumerate(self._groups):
            sel = "▶" if i == self._selected_group else " "
            lines.append(f"  {sel} Group {g.group_id}: {g.count} files  Waste: {g.waste_display}")
            for f in g.files[:3]:
                status = {"keep": "✅", "duplicate": "🔄", "delete": "🗑️", "original": "📄", "undecided": "❓"}.get(f.status.value, "?")
                lines.append(f"    {status} {f.path}")
                lines.append(f"       {f.display_size}  {f.hash_display}  {f.age_display}")
        lines.append("")
        lines.append("  ── Scan History ──")
        for r in self._scan_results[-3:]:
            age = int((time.time() - r.timestamp) / 86400)
            lines.append(f"  📊 {r.duplicate_groups} groups ({r.total_duplicates} dupes)  {r.waste_display} waste  {r.total_scanned} files  {age}d ago")
        lines.append("")
        lines.append("  [S]can  [D]elete  [K]eep  [A]uto-select  [I]gnore  [H]istory  [E]xport")
        return lines

    def render_group_detail(self) -> list:
        g = self.selected_group
        if not g:
            return ["  No group selected"]
        lines = []
        lines.append(f"  ── Group {g.group_id} ({g.count} files) ──")
        lines.append(f"  Total Size: {DuplicateEntry('', g.total_size, '', 0).display_size}  Waste: {g.waste_display}")
        lines.append("")
        for i, f in enumerate(g.files):
            status = {"keep": "✅ KEEP", "duplicate": "🔄 DUPE", "delete": "🗑️ DEL", "undecided": "❓ UNDECIDED"}.get(f.status.value, "?")
            lines.append(f"  {f.path}")
            lines.append(f"    {f.display_size}  {f.hash_display}  Modified: {f.age_display}  {status}")
        return lines
