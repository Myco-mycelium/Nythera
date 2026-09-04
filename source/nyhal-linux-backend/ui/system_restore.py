"""
Nyrqis OS - System Restore
Snapshots, rollback, and backup scheduling.
"""

import time
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class SnapshotType(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"
    MEMORY = "memory"


class SnapshotStatus(Enum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    RESTORED = "restored"
    EXPIRED = "expired"


class RestoreStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class BackupSchedule(Enum):
    NONE = "none"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    ON_BOOT = "on_boot"
    ON_INSTALL = "on_install"


@dataclass
class Snapshot:
    id: str = ""
    name: str = ""
    description: str = ""
    snapshot_type: SnapshotType = SnapshotType.INCREMENTAL
    status: SnapshotStatus = SnapshotStatus.COMPLETED
    timestamp: float = 0.0
    size_gb: float = 0.0
    parent_id: str = ""
    checksum: str = ""
    paths: List[str] = field(default_factory=list)
    packages_affected: int = 0
    config_files: int = 0
    can_rollback: bool = True
    bootable: bool = False

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.id:
            self.id = hashlib.md5(str(self.timestamp).encode()).hexdigest()[:12]

    @property
    def size_display(self) -> str:
        if self.size_gb < 1:
            return f"{self.size_gb * 1024:.0f} MB"
        return f"{self.size_gb:.2f} GB"

    @property
    def status_icon(self) -> str:
        icons = {
            SnapshotStatus.COMPLETED: "✅", SnapshotStatus.IN_PROGRESS: "🔄",
            SnapshotStatus.FAILED: "❌", SnapshotStatus.RESTORED: "🔄",
            SnapshotStatus.EXPIRED: "⏰",
        }
        return icons.get(self.status, "?")

    @property
    def type_icon(self) -> str:
        icons = {
            SnapshotType.FULL: "💿", SnapshotType.INCREMENTAL: "📀",
            SnapshotType.DIFFERENTIAL: "📀", SnapshotType.MEMORY: "🧠",
        }
        return icons.get(self.snapshot_type, "?")


@dataclass
class RestorePoint:
    name: str
    snapshot_id: str = ""
    timestamp: float = 0.0
    reason: str = ""
    system_state: str = ""
    packages: List[str] = field(default_factory=list)
    config_changes: List[str] = field(default_factory=list)

    @property
    def packages_display(self) -> str:
        if len(self.packages) <= 3:
            return ", ".join(self.packages)
        return f"{', '.join(self.packages[:3])} +{len(self.packages) - 3} more"


@dataclass
class BackupScheduleEntry:
    name: str
    schedule: BackupSchedule = BackupSchedule.DAILY
    enabled: bool = True
    paths: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    retention_days: int = 30
    max_snapshots: int = 10
    last_backup: float = 0.0
    next_backup: float = 0.0
    size_gb: float = 0.0

    @property
    def schedule_display(self) -> str:
        return self.schedule.value

    @property
    def status_icon(self) -> str:
        return "🟢" if self.enabled else "⚪"

    @property
    def time_until_backup(self) -> str:
        if self.next_backup == 0:
            return "N/A"
        delta = self.next_backup - time.time()
        if delta < 0:
            return "Overdue"
        if delta < 3600:
            return f"{delta / 60:.0f}m"
        return f"{delta / 3600:.1f}h"


class SystemRestore:
    def __init__(self):
        self.snapshots: List[Snapshot] = []
        self.restore_points: List[RestorePoint] = []
        self.backup_schedules: List[BackupScheduleEntry] = []
        self.current_snapshot: Optional[Snapshot] = None
        self.total_size_gb: float = 0.0
        self.auto_snapshot_on_install: bool = True
        self.auto_snapshot_on_upgrade: bool = True
        self.compression_enabled: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.snapshots = [
            Snapshot(name="Initial Install", description="Fresh Nyrqis OS installation",
                     snapshot_type=SnapshotType.FULL, timestamp=now - 86400 * 30,
                     size_gb=4.5, packages_affected=1250, config_files=45,
                     bootable=True, paths=["/"]),
            Snapshot(name="After System Update", description="Updated kernel and packages",
                     snapshot_type=SnapshotType.INCREMENTAL, parent_id="a1b2c3d4e5f6",
                     timestamp=now - 86400 * 14, size_gb=0.8,
                     packages_affected=25, config_files=3,
                     bootable=True, paths=["/"]),
            Snapshot(name="Wayland Bridge Install", description="Installed Nyrqis Wayland bridge",
                     snapshot_type=SnapshotType.INCREMENTAL, parent_id="b2c3d4e5f6a7",
                     timestamp=now - 86400 * 7, size_gb=0.3,
                     packages_affected=5, config_files=2,
                     paths=["/usr", "/etc"]),
            Snapshot(name="Theme Changes", description="Applied Dracula theme and custom fonts",
                     snapshot_type=SnapshotType.INCREMENTAL, parent_id="c3d4e5f6a7b8",
                     timestamp=now - 86400 * 3, size_gb=0.05,
                     packages_affected=0, config_files=8,
                     paths=["/home/zeus/.config"]),
            Snapshot(name="GPU Driver Update", description="Updated NVIDIA driver to 535.129",
                     snapshot_type=SnapshotType.INCREMENTAL, parent_id="d4e5f6a7b8c9",
                     timestamp=now - 86400, size_gb=0.4,
                     packages_affected=3, config_files=1,
                     bootable=True, paths=["/usr", "/etc/modprobe.d"]),
            Snapshot(name="Pre-Breaking-Change", description="Before manual Rust toolchain update",
                     snapshot_type=SnapshotType.FULL, timestamp=now - 3600,
                     size_gb=3.2, packages_affected=12, config_files=4,
                     bootable=True, can_rollback=True, paths=["/"]),
        ]
        self.total_size_gb = sum(s.size_gb for s in self.snapshots)

        self.restore_points = [
            RestorePoint(name="Clean Boot", snapshot_id=self.snapshots[0].id,
                          reason="Fresh install baseline",
                          packages=["nyrqis-kernel", "nyrqis-compositor", "nyrqis-shell"]),
            RestorePoint(name="Post-Update Stable", snapshot_id=self.snapshots[1].id,
                          reason="All tests passing after update",
                          packages=["nyrqis-kernel", "linux-headers", "mesa", "vulkan-tools"]),
            RestorePoint(name="Current (Pre-Breaking)", snapshot_id=self.snapshots[5].id,
                          reason="Last known good state before Rust update",
                          packages=["rust", "cargo", "nyrqis-backend"]),
        ]

        self.backup_schedules = [
            BackupScheduleEntry(name="System Snapshot", schedule=BackupSchedule.DAILY,
                                 enabled=True, paths=["/", "/home"],
                                 exclude_patterns=["/tmp", "/var/cache", "/proc", "/sys"],
                                 retention_days=30, max_snapshots=30,
                                 last_backup=now - 3600, next_backup=now + 82800,
                                 size_gb=4.5),
            BackupScheduleEntry(name="Config Backup", schedule=BackupSchedule.ON_BOOT,
                                 enabled=True, paths=["/etc", "/home/zeus/.config"],
                                 exclude_patterns=[], retention_days=90, max_snapshots=20,
                                 last_backup=now - 7200, next_backup=0,
                                 size_gb=0.2),
            BackupScheduleEntry(name="Home Directory", schedule=BackupSchedule.WEEKLY,
                                 enabled=True, paths=["/home/zeus"],
                                 exclude_patterns=["*.cache", "*.tmp", "node_modules"],
                                 retention_days=60, max_snapshots=8,
                                 last_backup=now - 86400 * 5, next_backup=now + 86400 * 2,
                                 size_gb=12.5),
        ]

    def create_snapshot(self, name: str, description: str = "",
                        snapshot_type: SnapshotType = SnapshotType.INCREMENTAL,
                        **kwargs) -> Snapshot:
        snap = Snapshot(name=name, description=description,
                         snapshot_type=snapshot_type, **kwargs)
        self.snapshots.append(snap)
        self.total_size_gb += snap.size_gb
        return snap

    def delete_snapshot(self, snapshot_id: str) -> bool:
        for i, s in enumerate(self.snapshots):
            if s.id == snapshot_id:
                self.total_size_gb -= s.size_gb
                del self.snapshots[i]
                return True
        return False

    def rollback(self, snapshot_id: str) -> bool:
        snap = next((s for s in self.snapshots if s.id == snapshot_id), None)
        if snap and snap.can_rollback:
            snap.status = SnapshotStatus.RESTORED
            return True
        return False

    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        return next((s for s in self.snapshots if s.id == snapshot_id), None)

    def get_rollback_snapshots(self) -> List[Snapshot]:
        return [s for s in self.snapshots if s.can_rollback and s.status == SnapshotStatus.COMPLETED]

    def get_bootable_snapshots(self) -> List[Snapshot]:
        return [s for s in self.snapshots if s.bootable]

    def search(self, query: str) -> List[Snapshot]:
        q = query.lower()
        return [s for s in self.snapshots if q in s.name.lower() or q in s.description.lower()]

    def get_stats(self) -> Dict:
        return {
            "total_snapshots": len(self.snapshots),
            "total_size_gb": round(self.total_size_gb, 2),
            "rollback_available": len(self.get_rollback_snapshots()),
            "bootable": len(self.get_bootable_snapshots()),
            "schedules": len(self.backup_schedules),
            "restore_points": len(self.restore_points),
        }
