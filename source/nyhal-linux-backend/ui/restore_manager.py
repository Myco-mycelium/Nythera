from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RestoreType(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    SYSTEM = "system"
    USER = "user"


class RestoreStatus(Enum):
    COMPLETE = "complete"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class RestorePoint:
    name: str
    restore_type: RestoreType
    description: str
    timestamp: float
    size_mb: float
    status: RestoreStatus = RestoreStatus.COMPLETE
    partitions: list = field(default_factory=list)
    packages: int = 0
    configs: int = 0
    is_protected: bool = False
    expiry_days: int = 90

    @property
    def age_days(self) -> int:
        import time
        return int((time.time() - self.timestamp) / 86400)

    @property
    def display_size(self) -> str:
        if self.size_mb >= 1024:
            return f"{self.size_mb / 1024:.1f} GB"
        return f"{self.size_mb:.0f} MB"

    @property
    def is_expired(self) -> bool:
        return self.age_days > self.expiry_days


class RestoreManager:
    def __init__(self):
        self._points: list[RestorePoint] = []
        self._auto_enabled: bool = True
        self._max_points: int = 10
        self._space_limit_gb: float = 100.0
        self._selected: int = 0
        self._snapshots: list = []
        self._create_samples()

    def _create_samples(self):
        import time
        now = time.time()
        samples = [
            RestorePoint("Before Kernel Update", RestoreType.FULL, "Full backup before kernel 6.12 update", now - 86400 * 2, 4520, partitions=["/ (ext4)", "/boot (vfat)"], packages=1247, configs=89, is_protected=True),
            RestorePoint("After Package Cleanup", RestoreType.INCREMENTAL, "Incremental after removing unused packages", now - 86400 * 5, 890, partitions=["/ (ext4)"], packages=1198, configs=87),
            RestorePoint("System Install Baseline", RestoreType.SYSTEM, "Fresh install baseline snapshot", now - 86400 * 30, 3200, partitions=["/ (ext4)", "/boot (vfat)", "/home (btrfs)"], packages=856, configs=64, is_protected=True),
            RestorePoint("Config Backup - Network", RestoreType.USER, "User-created before network config changes", now - 86400 * 1, 120, partitions=["/etc"], configs=12),
            RestorePoint("Driver Update Pre-Install", RestoreType.FULL, "Before NVIDIA driver 560 update", now - 86400 * 7, 4380, partitions=["/ (ext4)", "/boot (vfat)"], packages=1245, configs=89),
        ]
        self._points = samples

    @property
    def selected_point(self) -> Optional[RestorePoint]:
        if 0 <= self._selected < len(self._points):
            return self._points[self._selected]
        return None

    @property
    def total_size_mb(self) -> float:
        return sum(p.size_mb for p in self._points)

    @property
    def total_size_display(self) -> str:
        mb = self.total_size_mb
        if mb >= 1024:
            return f"{mb / 1024:.1f} GB"
        return f"{mb:.0f} MB"

    @property
    def remaining_space_gb(self) -> float:
        return self._space_limit_gb - (self.total_size_mb / 1024)

    @property
    def expired_count(self) -> int:
        return sum(1 for p in self._points if p.is_expired and not p.is_protected)

    @property
    def protected_count(self) -> int:
        return sum(1 for p in self._points if p.is_protected)

    def select(self, idx: int):
        if 0 <= idx < len(self._points):
            self._selected = idx

    def create(self, name: str, restore_type: RestoreType, description: str = "") -> RestorePoint:
        import time
        point = RestorePoint(
            name=name,
            restore_type=restore_type,
            description=description,
            timestamp=time.time(),
            size_mb=100,
        )
        self._points.append(point)
        return point

    def delete(self, idx: int) -> bool:
        if 0 <= idx < len(self._points):
            point = self._points[idx]
            if point.is_protected:
                return False
            self._points.pop(idx)
            if self._selected >= len(self._points):
                self._selected = max(0, len(self._points) - 1)
            return True
        return False

    def protect(self, idx: int):
        if 0 <= idx < len(self._points):
            self._points[idx].is_protected = True

    def unprotect(self, idx: int):
        if 0 <= idx < len(self._points):
            self._points[idx].is_protected = False

    def cleanup_expired(self) -> int:
        before = len(self._points)
        self._points = [p for p in self._points if not p.is_expired or p.is_protected]
        return before - len(self._points)

    def render(self, width: int = 60, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════╗")
        lines.append("║              NYRQIS RESTORE POINT MANAGER               ║")
        lines.append("╚══════════════════════════════════════════════════════════╝")
        lines.append("")
        status_icon = "🟢" if self._auto_enabled else "🔴"
        lines.append(f"  Auto-snapshots: {status_icon}  Max: {self._max_points}  Limit: {self._space_limit_gb:.0f} GB")
        lines.append(f"  Used: {self.total_size_display}  Remaining: {self.remaining_space_gb:.1f} GB  Protected: {self.protected_count}")
        lines.append("")
        lines.append("  ── Restore Points ──────────────────────────────────────")
        for i, p in enumerate(self._points):
            sel = "▶" if i == self._selected else " "
            type_icons = {RestoreType.FULL: "💿", RestoreType.INCREMENTAL: "📦", RestoreType.SYSTEM: "🖥️", RestoreType.USER: "👤"}
            icon = type_icons.get(p.restore_type, "📋")
            prot = " 🔒" if p.is_protected else ""
            expired = " ⏰" if p.is_expired else ""
            lines.append(f"  {sel} {icon} {p.name}{prot}{expired}")
            lines.append(f"    {p.display_size} · {p.age_days}d ago · {p.restore_type.value}")
        lines.append("")
        return lines

    def render_detail(self) -> list:
        p = self.selected_point
        if not p:
            return ["  No point selected"]
        lines = []
        lines.append(f"  ── {p.name} ──")
        lines.append(f"  Type: {p.restore_type.value}")
        lines.append(f"  Size: {p.display_size}")
        lines.append(f"  Age: {p.age_days} days")
        lines.append(f"  Status: {p.status.value}")
        lines.append(f"  Protected: {'Yes' if p.is_protected else 'No'}")
        lines.append(f"  Packages: {p.packages}")
        lines.append(f"  Configs: {p.configs}")
        if p.partitions:
            lines.append(f"  Partitions: {', '.join(p.partitions)}")
        return lines
