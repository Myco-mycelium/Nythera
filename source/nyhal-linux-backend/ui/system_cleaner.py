from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class CleanCategory(Enum):
    TEMP_FILES = "temp-files"
    PACKAGE_CACHE = "package-cache"
    LOG_FILES = "log-files"
    THUMBNAILS = "thumbnails"
    BROWSER_CACHE = "browser-cache"
    TRASH = "trash"
    OLD_KERNELS = "old-kernels"
    ORPHAN_PACKAGES = "orphan-packages"
    CORE_DUMPS = "core-dumps"
    CRASH_REPORTS = "crash-reports"


class CleanPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CleanStatus(Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    CLEANED = "cleaned"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class CleanItem:
    path: str
    category: CleanCategory
    size_bytes: int
    priority: CleanPriority
    status: CleanStatus
    description: str = ""
    last_modified: float = 0
    is_safe: bool = True

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
    def age_display(self) -> str:
        if self.last_modified == 0:
            return "N/A"
        age = int((time.time() - self.last_modified) / 86400)
        if age == 0:
            return "today"
        return f"{age}d ago"

    @property
    def filename(self) -> str:
        return self.path.split("/")[-1]

    @property
    def priority_icon(self) -> str:
        icons = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
        return icons.get(self.priority.value, "?")


@dataclass
class CleanRule:
    name: str
    category: CleanCategory
    pattern: str
    max_age_days: int
    enabled: bool = True
    auto_clean: bool = False


@dataclass
class CleanJob:
    name: str
    timestamp: float
    items_cleaned: int
    bytes_freed: int
    duration_ms: int
    errors: int = 0

    @property
    def freed_display(self) -> str:
        s = self.bytes_freed
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        return f"{s / 1024:.1f} KB"


class SystemCleaner:
    def __init__(self):
        self._items: list[CleanItem] = []
        self._selected_item: int = 0
        self._rules: list[CleanRule] = []
        self._jobs: list[CleanJob] = []
        self._selected_rule: int = 0
        self._dry_run: bool = False
        self._auto_clean_enabled: bool = False
        self._min_free_gb: float = 5.0
        self._preserve_recent_days: int = 7
        self._view: str = "scan"
        self._total_scanned: int = 0
        self._total_cleanable: int = 0
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._items = [
            CleanItem("/tmp/nyrqis-build-*.log", CleanCategory.TEMP_FILES, 157_286_400, CleanPriority.HIGH, CleanStatus.PENDING, "Build logs from compilation", now - 3),
            CleanItem("/var/cache/apt/archives/*.deb", CleanCategory.PACKAGE_CACHE, 536_870_912, CleanPriority.HIGH, CleanStatus.PENDING, "APT package cache", now - 14),
            CleanItem("/var/log/syslog.*.gz", CleanCategory.LOG_FILES, 268_435_456, CleanPriority.MEDIUM, CleanStatus.PENDING, "Compressed syslog rotations", now - 30),
            CleanItem("~/.cache/thumbnails/", CleanCategory.THUMBNAILS, 83_886_080, CleanPriority.LOW, CleanStatus.PENDING, "Thumbnail cache for file manager", now - 1),
            CleanItem("~/.cache/firefox/", CleanCategory.BROWSER_CACHE, 335_544_320, CleanPriority.MEDIUM, CleanStatus.PENDING, "Firefox browser cache", now - 7),
            CleanItem("~/.local/share/Trash/", CleanCategory.TRASH, 1_073_741_824, CleanPriority.HIGH, CleanStatus.PENDING, "Files in trash bin", now - 14),
            CleanItem("/boot/vmlinuz-6.10*", CleanCategory.OLD_KERNELS, 134_217_728, CleanPriority.MEDIUM, CleanStatus.PENDING, "Old kernel images (6.10 series)", now - 60),
            CleanItem("/var/crash/", CleanCategory.CRASH_REPORTS, 52_428_800, CleanPriority.LOW, CleanStatus.PENDING, "Crash report files", now - 30),
            CleanItem("/var/lib/systemd/coredump/", CleanCategory.CORE_DUMPS, 2_147_483_648, CleanPriority.CRITICAL, CleanStatus.PENDING, "System core dumps", now - 45),
            CleanItem("/var/cache/pacman/sync/", CleanCategory.PACKAGE_CACHE, 67_108_864, CleanPriority.LOW, CleanStatus.CLEANED, "Pacman sync database cache", now - 2),
            CleanItem("~/.cache/pip/", CleanCategory.PACKAGE_CACHE, 419_430_400, CleanPriority.MEDIUM, CleanStatus.CLEANED, "Python pip cache", now - 10),
            CleanItem("/tmp/lost+found/", CleanCategory.TEMP_FILES, 4_096, CleanPriority.LOW, CleanStatus.SKIPPED, "System temp (protected)", now - 90),
        ]
        self._total_scanned = len(self._items)
        self._total_cleanable = sum(1 for i in self._items if i.status == CleanStatus.PENDING and i.is_safe)

        self._rules = [
            CleanRule("Build Logs", CleanCategory.TEMP_FILES, "/tmp/*.log", 3, True, True),
            CleanRule("Package Cache", CleanCategory.PACKAGE_CACHE, "/var/cache/apt/*", 14, True, False),
            CleanRule("Old Logs", CleanCategory.LOG_FILES, "/var/log/*.gz", 30, True, True),
            CleanRule("Thumbnails", CleanCategory.THUMBNAILS, "~/.cache/thumbnails/*", 7, True, True),
            CleanRule("Browser Cache", CleanCategory.BROWSER_CACHE, "~/.cache/firefox/*", 7, True, False),
            CleanRule("Trash", CleanCategory.TRASH, "~/.local/share/Trash/*", 30, True, False),
            CleanRule("Core Dumps", CleanCategory.CORE_DUMPS, "/var/lib/systemd/coredump/*", 7, True, False),
        ]

        self._jobs = [
            CleanJob("Quick Clean", now - 86400, 45, 1_288_490_188, 2500, 0),
            CleanJob("Deep Clean", now - 86400 * 7, 128, 3_221_225_472, 8500, 1),
            CleanJob("Auto Clean", now - 86400 * 3, 12, 268_435_456, 1200, 0),
        ]

    @property
    def selected_item(self) -> Optional[CleanItem]:
        if 0 <= self._selected_item < len(self._items):
            return self._items[self._selected_item]
        return None

    @property
    def total_size_cleanable(self) -> int:
        return sum(i.size_bytes for i in self._items if i.status == CleanStatus.PENDING and i.is_safe)

    @property
    def total_size_display(self) -> str:
        s = self.total_size_cleanable
        if s >= 1073741824:
            return f"{s / 1073741824:.2f} GB"
        if s >= 1048576:
            return f"{s / 1048576:.1f} MB"
        return f"{s / 1024:.1f} KB"

    @property
    def category_totals(self) -> dict:
        totals = {}
        for item in self._items:
            if item.status == CleanStatus.PENDING:
                cat = item.category.value
                totals[cat] = totals.get(cat, 0) + item.size_bytes
        return totals

    def select_item(self, idx: int):
        if 0 <= idx < len(self._items):
            self._selected_item = idx

    def clean_item(self, idx: int) -> bool:
        if 0 <= idx < len(self._items):
            item = self._items[idx]
            if item.status == CleanStatus.PENDING and item.is_safe:
                item.status = CleanStatus.CLEANED
                return True
        return False

    def clean_all_safe(self) -> int:
        count = 0
        for item in self._items:
            if item.status == CleanStatus.PENDING and item.is_safe:
                item.status = CleanStatus.CLEANED
                count += 1
        return count

    def skip_item(self, idx: int):
        if 0 <= idx < len(self._items):
            self._items[idx].status = CleanStatus.SKIPPED

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM CLEANER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Scanned: {self._total_scanned} items  Cleanable: {self._total_cleanable}  Size: {self.total_size_display}")
        lines.append(f"  Auto-clean: {'ON' if self._auto_clean_enabled else 'OFF'}  Min Free: {self._min_free_gb}GB  Preserve: {self._preserve_recent_days}d  Dry-run: {'ON' if self._dry_run else 'OFF'}")
        lines.append("")
        lines.append("  ── Cleanable Items ──")
        for i, item in enumerate(self._items):
            sel = "▶" if i == self._selected_item else " "
            status = {"pending": "⏳", "cleaned": "✅", "skipped": "⏭️", "failed": "❌", "scanning": "🔍"}.get(item.status.value, "?")
            safe = " " if item.is_safe else "⚠️"
            lines.append(f"  {sel}{status}{safe} {item.filename}")
            lines.append(f"    {item.display_size}  {item.category.value}  {item.priority_icon} {item.age_display}")
        lines.append("")
        lines.append("  ── Size by Category ──")
        for cat, size in sorted(self.category_totals.items(), key=lambda x: -x[1]):
            if size >= 1073741824:
                s = f"{size / 1073741824:.2f} GB"
            elif size >= 1048576:
                s = f"{size / 1048576:.1f} MB"
            else:
                s = f"{size / 1024:.1f} KB"
            bar_len = min(int(size / self.total_size_cleanable * 20), 20) if self.total_size_cleanable else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {cat:<18s} {bar} {s}")
        lines.append("")
        lines.append("  ── Recent Jobs ──")
        for j in self._jobs[:3]:
            age = int((time.time() - j.timestamp) / 86400)
            lines.append(f"  ✅ {j.name}  {j.items_cleaned} items  {j.freed_display} freed  {age}d ago")
        lines.append("")
        lines.append("  [S]can  [C]lean all  [D]ry-run  [R]ules  [E]xclude  [H]istory")
        return lines

    def render_rules(self) -> list:
        lines = []
        lines.append("  ── Cleanup Rules ──")
        lines.append("")
        for i, r in enumerate(self._rules):
            sel = "▶" if i == self._selected_rule else " "
            auto = "🔄" if r.auto_clean else "  "
            status = "🟢" if r.enabled else "⚪"
            lines.append(f"  {sel}{status}{auto} {r.name}")
            lines.append(f"    Pattern: {r.pattern}  Category: {r.category.value}  Max Age: {r.max_age_days}d")
        return lines
