"""Nyrqis SDK system restore points.

Provides snapshot and restore functionality for Nyrqis system state:
- Create restore points before major changes
- Restore to a previous state
- List available restore points
- Automatic cleanup of old restore points

Usage:
    from nyrqis_sdk.restore import RestoreManager
    
    manager = RestoreManager()
    
    # Create a restore point
    point = manager.create("Before updating shell design")
    
    # ... make changes ...
    
    # Restore if needed
    manager.restore(point.id)
    
    # List available points
    for point in manager.list_points():
        print(f"{point.id}: {point.description}")

References:
    - NPC-010: Governance expansion
    - NPS-026: Package format (§6 integrity)
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RestorePoint:
    """A system restore point."""
    id: str
    timestamp: float
    description: str
    state_dir: Path
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def age_hours(self) -> float:
        """Age of the restore point in hours."""
        return (time.time() - self.timestamp) / 3600
    
    @property
    def size_bytes(self) -> int:
        """Total size of the restore point."""
        if not self.state_dir.exists():
            return 0
        
        total = 0
        for file in self.state_dir.rglob("*"):
            if file.is_file():
                total += file.stat().st_size
        return total
    
    @property
    def size_display(self) -> str:
        """Human-readable size."""
        size = self.size_bytes
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "description": self.description,
            "state_dir": str(self.state_dir),
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RestorePoint:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            description=data["description"],
            state_dir=Path(data["state_dir"]),
            metadata=data.get("metadata", {}),
        )


class RestoreManager:
    """Manage system restore points.
    
    Parameters
    ----------
    state_dir : str or Path, optional
        Directory to store restore points. Defaults to ~/.nyrqis/restore
    max_points : int
        Maximum number of restore points to keep (default: 10)
    """
    
    def __init__(
        self,
        state_dir: Optional[str | Path] = None,
        max_points: int = 10,
    ) -> None:
        self._state_dir = Path(state_dir) if state_dir else Path.home() / ".nyrqis" / "restore"
        self._max_points = max_points
        self._index_file = self._state_dir / "index.json"
        
        # Ensure directory exists
        self._state_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing index
        self._index: Dict[str, RestorePoint] = {}
        self._load_index()
    
    @property
    def state_dir(self) -> Path:
        """Directory storing restore points."""
        return self._state_dir
    
    @property
    def point_count(self) -> int:
        """Number of restore points."""
        return len(self._index)
    
    def create(
        self,
        description: str,
        paths: Optional[List[str | Path]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RestorePoint:
        """Create a new restore point.
        
        Parameters
        ----------
        description : str
            Human-readable description of the restore point.
        paths : list of paths, optional
            Specific paths to snapshot. If None, uses default system paths.
        metadata : dict, optional
            Additional metadata to store.
            
        Returns
        -------
        RestorePoint
            The created restore point.
        """
        # Generate ID
        point_id = f"rp_{int(time.time() * 1000)}"
        
        # Create state directory
        point_dir = self._state_dir / point_id
        point_dir.mkdir(parents=True, exist_ok=True)
        
        # Create restore point
        point = RestorePoint(
            id=point_id,
            timestamp=time.time(),
            description=description,
            state_dir=point_dir,
            metadata=metadata or {},
        )
        
        # Snapshot paths
        if paths:
            for path_str in paths:
                path = Path(path_str)
                if path.exists():
                    self._snapshot_path(path, point_dir)
        
        # Store in index
        self._index[point_id] = point
        self._save_index()
        
        # Cleanup old points
        self._cleanup_old_points()
        
        return point
    
    def restore(self, point_id: str) -> bool:
        """Restore to a previous restore point.
        
        Parameters
        ----------
        point_id : str
            ID of the restore point to restore.
            
        Returns
        -------
        bool
            True on success, False if point not found.
        """
        point = self._index.get(point_id)
        if not point or not point.state_dir.exists():
            return False
        
        # Restore files
        for snapshot_file in point.state_dir.rglob("*"):
            if snapshot_file.is_file():
                # Calculate relative path
                rel_path = snapshot_file.relative_to(point.state_dir)
                target_path = Path("/") / rel_path
                
                # Restore file
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snapshot_file, target_path)
                except OSError:
                    pass
        
        return True
    
    def list_points(self) -> List[RestorePoint]:
        """List all available restore points, newest first."""
        points = sorted(
            self._index.values(),
            key=lambda p: p.timestamp,
            reverse=True,
        )
        return points
    
    def get_point(self, point_id: str) -> Optional[RestorePoint]:
        """Get a specific restore point."""
        return self._index.get(point_id)
    
    def delete_point(self, point_id: str) -> bool:
        """Delete a restore point.
        
        Parameters
        ----------
        point_id : str
            ID of the restore point to delete.
            
        Returns
        -------
        bool
            True on success, False if point not found.
        """
        point = self._index.get(point_id)
        if not point:
            return False
        
        # Remove directory
        if point.state_dir.exists():
            shutil.rmtree(point.state_dir, ignore_errors=True)
        
        # Remove from index
        del self._index[point_id]
        self._save_index()
        
        return True
    
    def _snapshot_path(self, path: Path, target_dir: Path) -> None:
        """Snapshot a path to the restore point."""
        if path.is_file():
            # Copy file
            rel_path = path.relative_to(path.anchor) if path.is_absolute() else path
            target_file = target_dir / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_file)
        elif path.is_dir():
            # Copy directory
            for item in path.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(path.parent) if item.is_absolute() else item
                    target_file = target_dir / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(item, target_file)
                    except OSError:
                        pass
    
    def _cleanup_old_points(self) -> None:
        """Remove oldest restore points if over limit."""
        if len(self._index) <= self._max_points:
            return
        
        # Sort by timestamp
        sorted_points = sorted(
            self._index.values(),
            key=lambda p: p.timestamp,
        )
        
        # Remove oldest
        while len(sorted_points) > self._max_points:
            point = sorted_points.pop(0)
            self.delete_point(point.id)
    
    def _load_index(self) -> None:
        """Load restore point index from disk."""
        if not self._index_file.exists():
            return
        
        try:
            with open(self._index_file) as f:
                data = json.load(f)
            
            for point_data in data.get("points", []):
                point = RestorePoint.from_dict(point_data)
                # Verify state directory exists
                if point.state_dir.exists():
                    self._index[point.id] = point
        except (OSError, json.JSONDecodeError):
            pass
    
    def _save_index(self) -> None:
        """Save restore point index to disk."""
        data = {
            "points": [point.to_dict() for point in self._index.values()]
        }
        
        try:
            with open(self._index_file, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass


__all__ = ["RestoreManager", "RestorePoint"]
