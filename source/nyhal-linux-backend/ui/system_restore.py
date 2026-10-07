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
    """Desktop data layer for the Restore app.

    0.29.45 (RST-001 §7(5)): the 0.28.0-era sample-data simulation is
    RETIRED — this class no longer fabricates snapshots in memory.
    Without an attached volume it honestly reports zero restore points;
    with one, ``attach_volume`` binds a real
    ``backend.restore_engine.RestoreEngine`` and every mutating verb
    (create/delete/rollback) goes through the engine — pinned,
    audit-chained, GC-safe. The read-side API (search/stats/list
    shapes) is preserved so the desktop surface's tests keep passing;
    ``rollback`` maps onto the engine's restore (dry-run default, the
    engine commits only when the caller forces it).
    """

    def __init__(self):
        self.snapshots: List[Snapshot] = []
        self.restore_points: List[RestorePoint] = []
        self.backup_schedules: List[BackupScheduleEntry] = []
        self.current_snapshot: Optional[Snapshot] = None
        self.total_size_gb: float = 0.0
        self.auto_snapshot_on_install: bool = True
        self.auto_snapshot_on_upgrade: bool = True
        self.compression_enabled: bool = True
        self._engine = None
        self._volume_path: str = ""

    # ------------------------------------------------------------------
    # Real engine attach (RST-001 §7(5))
    # ------------------------------------------------------------------

    def attach_volume(self, volume_path: str) -> bool:
        """Bind a real NyFS volume through backend.restore_engine.
        ``load`` is the classmethod constructor (raises ``NyFSError``
        when no valid metadata exists — never fabricates an empty
        filesystem); a never-saved path therefore attaches honestly
        FALSE unless ``initialize`` is requested. Returns False (never
        raises) on any refusal — the app reports an honestly empty
        list."""
        try:
            from fuse.nyfs import NyFSFilesystem
            from backend.restore_engine import RestoreEngine
            fs = NyFSFilesystem.load(volume_path)
            self._engine = RestoreEngine(fs)
            self._volume_path = volume_path
            return True
        except Exception:  # noqa: BLE001 — the surface degrades honestly
            self._engine = None
            self._volume_path = ""
            return False

    @property
    def volume_attached(self) -> bool:
        return self._engine is not None

    def _sync_from_engine(self) -> None:
        """Mirror the engine's registry into the UI snapshot shapes."""
        self.snapshots = []
        self.total_size_gb = 0.0
        if self._engine is None:
            return
        for entry in self._engine.list_snapshots():
            snap = Snapshot(
                name=entry.get("label", entry["snap_id"]),
                description=entry.get("reason", ""),
                snapshot_type=SnapshotType.INCREMENTAL,
                status=SnapshotStatus.COMPLETED,
                timestamp=entry.get("created", 0.0),
                size_gb=0.0,
            )
            snap.id = entry["snap_id"]
            self.snapshots.append(snap)

    def create_snapshot(self, name: str, description: str = "",
                        snapshot_type: SnapshotType = SnapshotType.INCREMENTAL,
                        **kwargs) -> Snapshot:
        if self._engine is not None:
            entry = self._engine.create_snapshot(
                label=name, reason=description or "desktop capture")
            self._sync_from_engine()
            snap = self.get_snapshot(entry["snap_id"])
            return snap if snap is not None else Snapshot(name=name,
                                                          description=description)
        snap = Snapshot(name=name, description=description,
                        snapshot_type=snapshot_type, **kwargs)
        self.snapshots.append(snap)
        self.total_size_gb += snap.size_gb
        return snap

    def delete_snapshot(self, snapshot_id: str) -> bool:
        if self._engine is not None:
            deleted = self._engine.delete_snapshot(snapshot_id)
            self._sync_from_engine()
            return deleted
        for i, s in enumerate(self.snapshots):
            if s.id == snapshot_id:
                self.total_size_gb -= s.size_gb
                del self.snapshots[i]
                return True
        return False

    def rollback(self, snapshot_id: str, dry_run: bool = True) -> bool:
        """Restore the volume to the snapshot (engine-attached) — the
        DESKTOP DEFAULT IS DRY-RUN (preview-first, the same operator
        posture the CLI and the engine floor); committing requires the
        explicit ``dry_run=False``."""
        if self._engine is not None:
            try:
                self._engine.restore_to(snapshot_id,
                                        dry_run=bool(dry_run))
                self._sync_from_engine()
                if not dry_run:
                    snap = self.get_snapshot(snapshot_id)
                    if snap is not None:
                        snap.status = SnapshotStatus.RESTORED
                return True
            except Exception:  # noqa: BLE001 — honest refusal to the UI
                return False
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
