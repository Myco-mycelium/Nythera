"""FileOperations — File operation management for Nyrqis.

Provides file system operations with UI feedback:
- Copy files/directories with progress
- Move files/directories with progress
- Delete files/directories with confirmation
- Rename with conflict detection
- Operation queue and history
- Conflict resolution (overwrite, skip, rename)
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OperationType(Enum):
    COPY = auto()
    MOVE = auto()
    DELETE = auto()
    RENAME = auto()
    MKDIR = auto()


class OperationState(Enum):
    PENDING = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


class ConflictAction(Enum):
    OVERWRITE = auto()
    SKIP = auto()
    RENAME = auto()     # auto-rename (file (1).txt)
    ASK = auto()        # prompt user


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileItem:
    """A file or directory for operations."""
    path: str
    name: str
    is_dir: bool = False
    size: int = 0        # bytes
    modified: float = 0.0  # timestamp
    permissions: str = ""

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return "—"
        if self.size < 1024:
            return f"{self.size} B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        if self.size < 1024 * 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f} MB"
        return f"{self.size / (1024 * 1024 * 1024):.1f} GB"


@dataclass
class FileOperation:
    """A file operation (copy, move, delete, etc.)."""
    id: str
    op_type: OperationType
    sources: List[str]         # source paths
    destination: str = ""      # destination path (for copy/move)
    state: OperationState = OperationState.PENDING
    progress: float = 0.0      # 0.0-1.0
    total_files: int = 0
    processed_files: int = 0
    total_bytes: int = 0
    processed_bytes: int = 0
    errors: List[str] = field(default_factory=list)
    conflict_action: ConflictAction = ConflictAction.ASK
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    cancel_requested: bool = False

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    @property
    def speed_bytes_per_sec(self) -> float:
        if self.started_at == 0 or self.state != OperationState.RUNNING:
            return 0
        elapsed = time.time() - self.started_at
        if elapsed <= 0:
            return 0
        return self.processed_bytes / elapsed

    @property
    def eta_seconds(self) -> float:
        speed = self.speed_bytes_per_sec
        if speed <= 0:
            return 0
        remaining = self.total_bytes - self.processed_bytes
        return remaining / speed

    @property
    def display_speed(self) -> str:
        speed = self.speed_bytes_per_sec
        if speed < 1024:
            return f"{speed:.0f} B/s"
        if speed < 1024 * 1024:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed / (1024 * 1024):.1f} MB/s"

    @property
    def display_eta(self) -> str:
        eta = self.eta_seconds
        if eta <= 0:
            return "—"
        if eta < 60:
            return f"{int(eta)}s"
        if eta < 3600:
            return f"{int(eta / 60)}m {int(eta % 60)}s"
        return f"{int(eta / 3600)}h {int(eta % 3600 / 60)}m"

    @property
    def label(self) -> str:
        labels = {
            OperationType.COPY: "Copying",
            OperationType.MOVE: "Moving",
            OperationType.DELETE: "Deleting",
            OperationType.RENAME: "Renaming",
            OperationType.MKDIR: "Creating folder",
        }
        return labels.get(self.op_type, "Processing")

    @property
    def source_display(self) -> str:
        if len(self.sources) == 1:
            return os.path.basename(self.sources[0])
        return f"{len(self.sources)} items"


@dataclass
class ConflictInfo:
    """Information about a file conflict."""
    source: str
    destination: str
    source_size: int = 0
    dest_size: int = 0
    source_modified: float = 0.0
    dest_modified: float = 0.0


# ---------------------------------------------------------------------------
# FileOperations
# ---------------------------------------------------------------------------

class FileOperations:
    """File operation management for Nyrqis.

    Handles copy, move, delete, rename with progress tracking,
    conflict resolution, and operation history.

    Parameters
    ----------
    max_history : int
        Maximum completed operations to keep.
    """

    def __init__(self, max_history: int = 50):
        self._operations: List[FileOperation] = []
        self._history: List[FileOperation] = []
        self._max_history = max_history
        self._callbacks: Dict[str, List[Callable]] = {}
        self._conflict_handler: Optional[Callable] = None

        # Stats
        self._total_copied = 0
        self._total_moved = 0
        self._total_deleted = 0

    # -- Operation creation ----------------------------------------------

    def copy(self, sources: List[str], destination: str,
             conflict: ConflictAction = ConflictAction.ASK) -> FileOperation:
        """Start a copy operation."""
        op = self._create_op(OperationType.COPY, sources, destination, conflict)
        self._operations.append(op)
        self._start_op(op)
        return op

    def move(self, sources: List[str], destination: str,
             conflict: ConflictAction = ConflictAction.ASK) -> FileOperation:
        """Start a move operation."""
        op = self._create_op(OperationType.MOVE, sources, destination, conflict)
        self._operations.append(op)
        self._start_op(op)
        return op

    def delete(self, sources: List[str],
               requires_confirm: bool = True) -> FileOperation:
        """Start a delete operation.

        If requires_confirm is True, the operation stays in PENDING state
        until confirm_delete() is called.
        """
        op = self._create_op(OperationType.DELETE, sources)
        self._operations.append(op)
        if not requires_confirm:
            self._start_op(op)
        return op

    def rename(self, source: str, new_name: str) -> FileOperation:
        """Start a rename operation."""
        dest_dir = os.path.dirname(source)
        destination = os.path.join(dest_dir, new_name)
        op = self._create_op(OperationType.RENAME, [source], destination)
        self._operations.append(op)
        self._start_op(op)
        return op

    def mkdir(self, path: str) -> FileOperation:
        """Create a directory."""
        op = self._create_op(OperationType.MKDIR, [], path)
        self._operations.append(op)
        self._start_op(op)
        return op

    def _create_op(self, op_type: OperationType, sources: List[str],
                   destination: str = "",
                   conflict: ConflictAction = ConflictAction.ASK) -> FileOperation:
        op_id = f"op-{int(time.time() * 1000) % 1000000}"
        op = FileOperation(
            id=op_id,
            op_type=op_type,
            sources=list(sources),
            destination=destination,
            conflict_action=conflict,
        )
        # Calculate totals from real or simulated file system
        self._calculate_totals(op)
        return op

    def _calculate_totals(self, op: FileOperation) -> None:
        """Calculate total files and bytes for an operation."""
        total_files = 0
        total_bytes = 0

        for src in op.sources:
            if os.path.exists(src):
                if os.path.isfile(src):
                    total_files += 1
                    total_bytes += os.path.getsize(src)
                elif os.path.isdir(src):
                    for root, dirs, files in os.walk(src):
                        total_files += len(files)
                        for f in files:
                            try:
                                total_bytes += os.path.getsize(
                                    os.path.join(root, f))
                            except OSError:
                                pass
            else:
                # Simulated file
                total_files += 1
                total_bytes += 1024 * 1024  # assume 1MB

        op.total_files = max(total_files, len(op.sources))
        op.total_bytes = max(total_bytes, len(op.sources) * 1024 * 1024)

    # -- Operation control -----------------------------------------------

    def confirm_delete(self, op_id: str) -> bool:
        """Confirm a pending delete operation."""
        op = self._find_op(op_id)
        if op and op.state == OperationState.PENDING:
            self._start_op(op)
            return True
        return False

    def pause(self, op_id: str) -> bool:
        """Pause a running operation."""
        op = self._find_op(op_id)
        if op and op.state == OperationState.RUNNING:
            op.state = OperationState.PAUSED
            self._dispatch("paused", op)
            return True
        return False

    def resume(self, op_id: str) -> bool:
        """Resume a paused operation."""
        op = self._find_op(op_id)
        if op and op.state == OperationState.PAUSED:
            op.state = OperationState.RUNNING
            self._dispatch("resumed", op)
            return True
        return False

    def cancel(self, op_id: str) -> bool:
        """Cancel an operation."""
        op = self._find_op(op_id)
        if op and op.state in (OperationState.RUNNING, OperationState.PENDING,
                                OperationState.PAUSED):
            op.cancel_requested = True
            op.state = OperationState.CANCELLED
            op.completed_at = time.time()
            self._dispatch("cancelled", op)
            return True
        return False

    def retry(self, op_id: str) -> bool:
        """Retry a failed operation."""
        op = self._find_op(op_id)
        if op and op.state == OperationState.FAILED:
            op.state = OperationState.PENDING
            op.progress = 0.0
            op.processed_files = 0
            op.processed_bytes = 0
            op.errors.clear()
            self._start_op(op)
            return True
        return False

    # -- Conflict resolution ---------------------------------------------

    def resolve_conflict(self, op_id: str, action: ConflictAction) -> None:
        """Resolve a conflict for an operation."""
        op = self._find_op(op_id)
        if op:
            op.conflict_action = action
            self._dispatch("conflict_resolved", op)

    def detect_conflict(self, source: str, destination: str) -> Optional[ConflictInfo]:
        """Check if a file conflict exists."""
        dest_path = os.path.join(destination, os.path.basename(source))
        if os.path.exists(dest_path):
            return ConflictInfo(
                source=source,
                destination=dest_path,
                source_size=os.path.getsize(source) if os.path.isfile(source) else 0,
                dest_size=os.path.getsize(dest_path),
                source_modified=os.path.getmtime(source) if os.path.exists(source) else 0,
                dest_modified=os.path.getmtime(dest_path),
            )
        return None

    # -- Progress simulation ---------------------------------------------

    def tick(self, elapsed: float = 0.1) -> None:
        """Advance all running operations by one time step.

        In production this would be driven by actual I/O;
        here we simulate progress for demo/testing.
        """
        for op in self._operations:
            if op.state != OperationState.RUNNING:
                continue
            if op.cancel_requested:
                op.state = OperationState.CANCELLED
                op.completed_at = time.time()
                continue

            # Simulate progress
            speed = 50 * 1024 * 1024  # 50 MB/s simulated
            bytes_this_tick = int(speed * elapsed)
            op.processed_bytes = min(op.total_bytes,
                                     op.processed_bytes + bytes_this_tick)
            op.processed_files = min(
                op.total_files,
                int(op.total_files * op.processed_bytes / max(1, op.total_bytes))
            )
            op.progress = op.processed_bytes / max(1, op.total_bytes)

            if op.progress >= 1.0:
                op.progress = 1.0
                op.state = OperationState.COMPLETED
                op.completed_at = time.time()
                self._on_complete(op)

    def _start_op(self, op: FileOperation) -> None:
        op.state = OperationState.RUNNING
        op.started_at = time.time()
        self._dispatch("started", op)

    def _on_complete(self, op: FileOperation) -> None:
        """Handle operation completion."""
        self._operations.remove(op)
        self._history.insert(0, op)
        if len(self._history) > self._max_history:
            self._history.pop()

        if op.op_type == OperationType.COPY:
            self._total_copied += len(op.sources)
        elif op.op_type == OperationType.MOVE:
            self._total_moved += len(op.sources)
        elif op.op_type == OperationType.DELETE:
            self._total_deleted += len(op.sources)

        self._dispatch("completed", op)

    # -- Query -----------------------------------------------------------

    @property
    def active_operations(self) -> List[FileOperation]:
        return [op for op in self._operations
                if op.state in (OperationState.RUNNING, OperationState.PENDING,
                                OperationState.PAUSED)]

    @property
    def history(self) -> List[FileOperation]:
        return list(self._history)

    def _find_op(self, op_id: str) -> Optional[FileOperation]:
        for op in self._operations + self._history:
            if op.id == op_id:
                return op
        return None

    # -- Callbacks -------------------------------------------------------

    def on(self, event: str, callback: Callable) -> None:
        self._callbacks.setdefault(event, []).append(callback)

    def _dispatch(self, event: str, op: FileOperation) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                cb(op)
            except Exception:
                pass

    # -- Rendering -------------------------------------------------------

    def render(self, width: int = 400, height: int = 300) -> Tuple[bytes, int, int]:
        """Render the operations progress UI."""
        buf = bytearray(width * height * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        y = 12
        for op in self.active_operations:
            # Op type label placeholder
            label_color = {
                OperationType.COPY: (80, 140, 255),
                OperationType.MOVE: (255, 200, 60),
                OperationType.DELETE: (255, 80, 80),
            }.get(op.op_type, (180, 180, 200))
            self._fill_rect(buf, width, 12, y, 60, 14, label_color)

            # Progress bar
            bar_y = y + 20
            self._fill_rect(buf, width, 12, bar_y, width - 24, 12, (50, 50, 65))
            fill_w = int((width - 24) * op.progress)
            self._fill_rect(buf, width, 12, bar_y, fill_w, 12, label_color)

            # Speed and ETA
            self._fill_rect(buf, width, 12, bar_y + 18, 80, 10, (120, 120, 140))

            y += 68

        return bytes(buf), width, height

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "active": len(self.active_operations),
            "history": len(self._history),
            "total_copied": self._total_copied,
            "total_moved": self._total_moved,
            "total_deleted": self._total_deleted,
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.get_stats()


__all__ = [
    "FileOperations", "FileOperation", "FileItem", "ConflictInfo",
    "OperationType", "OperationState", "ConflictAction",
]
