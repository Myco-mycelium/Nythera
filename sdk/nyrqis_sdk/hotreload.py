"""Nyrqis SDK hot reload for shell designs.

Watches .nstudio files for changes and triggers automatic reload.
This enables live preview during development without restarting
the preview server.

Usage:
    from nyrqis_sdk.hotreload import HotReloader
    
    reloader = HotReloader("design.nstudio")
    reloader.on_change(lambda path: print(f"Changed: {path}"))
    reloader.start()
    
    # ... development ...
    
    reloader.stop()

References:
    - ADR-0025: NUI runtime consumption
    - NUI-SCHEMA: Shell design format
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional


class HotReloader:
    """Watch a file for changes and trigger callbacks.
    
    Parameters
    ----------
    file_path : str or Path
        Path to the file to watch.
    interval : float
        Check interval in seconds (default: 0.5).
    """
    
    def __init__(
        self,
        file_path: str | Path,
        interval: float = 0.5,
    ) -> None:
        self._file_path = Path(file_path).resolve()
        self._interval = interval
        self._callbacks: list[Callable[[Path], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_hash: Optional[str] = None
        self._last_mtime: float = 0.0
        
        # Compute initial hash
        if self._file_path.exists():
            self._last_hash = self._compute_hash()
            self._last_mtime = self._file_path.stat().st_mtime
    
    @property
    def file_path(self) -> Path:
        """The file being watched."""
        return self._file_path
    
    @property
    def is_running(self) -> bool:
        """Whether the reloader is active."""
        return self._running
    
    def on_change(self, callback: Callable[[Path], None]) -> None:
        """Register a callback for file changes.
        
        Parameters
        ----------
        callback : callable
            Function called with the file path when changes are detected.
        """
        self._callbacks.append(callback)
    
    def start(self) -> None:
        """Start watching for changes."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name=f"hotreload-{self._file_path.name}",
        )
        self._thread.start()
    
    def stop(self) -> None:
        """Stop watching for changes."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def check_now(self) -> bool:
        """Check for changes immediately (non-blocking).
        
        Returns True if changes were detected.
        """
        if not self._file_path.exists():
            return False
        
        current_hash = self._compute_hash()
        current_mtime = self._file_path.stat().st_mtime
        
        if current_hash != self._last_hash or current_mtime != self._last_mtime:
            self._last_hash = current_hash
            self._last_mtime = current_mtime
            self._notify_callbacks()
            return True
        
        return False
    
    def _watch_loop(self) -> None:
        """Background thread that polls for changes."""
        while self._running:
            try:
                self.check_now()
            except Exception:
                pass  # Don't crash the watcher thread
            time.sleep(self._interval)
    
    def _compute_hash(self) -> str:
        """Compute SHA-256 hash of the file content."""
        if not self._file_path.exists():
            return ""
        
        hasher = hashlib.sha256()
        with open(self._file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(self._file_path)
            except Exception:
                pass  # Don't crash on callback errors


class DirectoryReloader:
    """Watch a directory for .nstudio file changes.
    
    Parameters
    ----------
    directory : str or Path
        Directory to watch.
    interval : float
        Check interval in seconds (default: 1.0).
    """
    
    def __init__(
        self,
        directory: str | Path,
        interval: float = 1.0,
    ) -> None:
        self._directory = Path(directory).resolve()
        self._interval = interval
        self._callbacks: list[Callable[[Path], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._file_hashes: dict[Path, str] = {}
        
        # Scan initial state
        self._scan_directory()
    
    @property
    def directory(self) -> Path:
        """The directory being watched."""
        return self._directory
    
    @property
    def is_running(self) -> bool:
        """Whether the reloader is active."""
        return self._running
    
    def on_change(self, callback: Callable[[Path], None]) -> None:
        """Register a callback for file changes."""
        self._callbacks.append(callback)
    
    def start(self) -> None:
        """Start watching for changes."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name=f"hotreload-dir-{self._directory.name}",
        )
        self._thread.start()
    
    def stop(self) -> None:
        """Stop watching for changes."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def _scan_directory(self) -> None:
        """Scan directory and compute hashes for all .nstudio files."""
        self._file_hashes.clear()
        if not self._directory.exists():
            return
        
        for nstudio_file in self._directory.glob("**/*.nstudio"):
            self._file_hashes[nstudio_file] = self._compute_hash(nstudio_file)
    
    def _watch_loop(self) -> None:
        """Background thread that polls for changes."""
        while self._running:
            try:
                self._check_for_changes()
            except Exception:
                pass
            time.sleep(self._interval)
    
    def _check_for_changes(self) -> None:
        """Check all .nstudio files for changes."""
        if not self._directory.exists():
            return
        
        current_files = set(self._directory.glob("**/*.nstudio"))
        
        # Check for new or modified files
        for nstudio_file in current_files:
            current_hash = self._compute_hash(nstudio_file)
            previous_hash = self._file_hashes.get(nstudio_file)
            
            if current_hash != previous_hash:
                self._file_hashes[nstudio_file] = current_hash
                self._notify_callbacks(nstudio_file)
        
        # Check for deleted files
        for deleted_file in set(self._file_hashes.keys()) - current_files:
            del self._file_hashes[deleted_file]
    
    def _compute_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        if not file_path.exists():
            return ""
        
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def _notify_callbacks(self, file_path: Path) -> None:
        """Notify all registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(file_path)
            except Exception:
                pass


__all__ = ["HotReloader", "DirectoryReloader"]
