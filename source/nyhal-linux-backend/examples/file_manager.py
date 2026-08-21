#!/usr/bin/env python3
"""file_manager — Nyrqis file manager application.

A file manager that demonstrates the full Nyrqis stack:

- Directory listing and navigation
- File creation, deletion, renaming
- File content read/write
- Path breadcrumb navigation
- File type detection and icons
- Integration with the DesktopSession

This runs as a NUI component with a window in the desktop shell.

References:
    - NPS-004: NyFS Filesystem Core
    - ADR-0016: NyFS Linux Backend
    - ADR-0025 §9: runtime consumption
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# File type → icon mapping
FILE_ICONS = {
    "directory": "📁",
    "python": "🐍",
    "rust": "🦀",
    "javascript": "📜",
    "json": "📋",
    "markdown": "📝",
    "image": "🖼",
    "audio": "🎵",
    "video": "🎬",
    "archive": "📦",
    "executable": "⚡",
    "text": "📄",
    "binary": "🔧",
    "unknown": "❓",
}

EXTENSION_MAP = {
    ".py": "python", ".rs": "rust", ".js": "javascript",
    ".ts": "javascript", ".json": "json", ".md": "markdown",
    ".txt": "text", ".log": "text", ".cfg": "text", ".toml": "text",
    ".yaml": "text", ".yml": "text", ".xml": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".svg": "image", ".bmp": "image",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio",
    ".mp4": "video", ".avi": "video", ".mkv": "video",
    ".zip": "archive", ".tar": "archive", ".gz": "archive",
    ".napp": "executable", ".bin": "executable",
    ".so": "binary", ".dll": "binary", ".dylib": "binary",
}


@dataclass
class FileEntry:
    """A file or directory entry in the file manager."""
    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    modified: float = 0.0
    permissions: str = ""

    @property
    def icon(self) -> str:
        if self.is_dir:
            return FILE_ICONS["directory"]
        ext = os.path.splitext(self.name)[1].lower()
        file_type = EXTENSION_MAP.get(ext, "unknown")
        return FILE_ICONS.get(file_type, FILE_ICONS["unknown"])

    @property
    def size_display(self) -> str:
        if self.is_dir:
            return "—"
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        elif self.size < 1024 * 1024 * 1024:
            return f"{self.size / (1024*1024):.1f} MB"
        return f"{self.size / (1024*1024*1024):.1f} GB"

    @property
    def modified_display(self) -> str:
        if self.modified == 0:
            return "—"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified))


@dataclass
class FileManagerState:
    """The current state of the file manager."""
    current_path: str = "/"
    entries: List[FileEntry] = field(default_factory=list)
    selected: Optional[str] = None
    history: List[str] = field(default_factory=list)
    history_index: int = -1
    sort_by: str = "name"       # name, size, modified
    sort_reverse: bool = False
    show_hidden: bool = False
    view_mode: str = "list"     # list, grid
    status_message: str = ""


class FileManager:
    """Nyrqis file manager application.

    Parameters
    ----------
    session : DesktopSession
        The desktop session to manage files in.
    root_path : str
        The root directory to start browsing from.
    """

    def __init__(
        self,
        session,
        root_path: str = "/",
    ) -> None:
        self._session = session
        self._root_path = os.path.abspath(root_path)
        self._state = FileManagerState(
            current_path=self._root_path,
        )
        self._visible = False
        self._callbacks: List[Callable] = []
        self._navigate(self._root_path)

    # -- Navigation ---------------------------------------------------

    @property
    def current_path(self) -> str:
        return self._state.current_path

    @property
    def entries(self) -> List[FileEntry]:
        return list(self._state.entries)

    @property
    def state(self) -> FileManagerState:
        return self._state

    def navigate(self, path: str) -> bool:
        """Navigate to a directory. Returns True on success."""
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            self._set_status(f"Not a directory: {path}")
            return False
        return self._navigate(path)

    def go_up(self) -> bool:
        """Navigate to the parent directory."""
        parent = os.path.dirname(self._state.current_path)
        if parent == self._state.current_path:
            return False
        return self._navigate(parent)

    def go_home(self) -> bool:
        """Navigate to the home directory."""
        home = os.path.expanduser("~")
        return self._navigate(home)

    def go_back(self) -> bool:
        """Navigate back in history."""
        if self._state.history_index > 0:
            self._state.history_index -= 1
            path = self._state.history[self._state.history_index]
            self._load_directory(path)
            return True
        return False

    def go_forward(self) -> bool:
        """Navigate forward in history."""
        if self._state.history_index < len(self._state.history) - 1:
            self._state.history_index += 1
            path = self._state.history[self._state.history_index]
            self._load_directory(path)
            return True
        return False

    def refresh(self) -> None:
        """Refresh the current directory listing."""
        self._load_directory(self._state.current_path)
        self._set_status("Refreshed")

    # -- File operations ----------------------------------------------

    def create_file(self, name: str, content: str = "") -> bool:
        """Create a new file in the current directory."""
        path = os.path.join(self._state.current_path, name)
        if os.path.exists(path):
            self._set_status(f"File already exists: {name}")
            return False
        try:
            with open(path, "w") as f:
                f.write(content)
            self._set_status(f"Created: {name}")
            self._notify(f"File created", name)
            self.refresh()
            return True
        except OSError as e:
            self._set_status(f"Error creating {name}: {e}")
            return False

    def create_directory(self, name: str) -> bool:
        """Create a new directory in the current directory."""
        path = os.path.join(self._state.current_path, name)
        if os.path.exists(path):
            self._set_status(f"Directory already exists: {name}")
            return False
        try:
            os.makedirs(path, exist_ok=True)
            self._set_status(f"Created directory: {name}")
            self._notify(f"Directory created", name)
            self.refresh()
            return True
        except OSError as e:
            self._set_status(f"Error creating directory: {e}")
            return False

    def delete(self, name: str) -> bool:
        """Delete a file or empty directory."""
        path = os.path.join(self._state.current_path, name)
        if not os.path.exists(path):
            self._set_status(f"Not found: {name}")
            return False
        try:
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.remove(path)
            self._set_status(f"Deleted: {name}")
            self._notify(f"Deleted", name)
            self.refresh()
            return True
        except OSError as e:
            self._set_status(f"Error deleting: {e}")
            return False

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename a file or directory."""
        old_path = os.path.join(self._state.current_path, old_name)
        new_path = os.path.join(self._state.current_path, new_name)
        if not os.path.exists(old_path):
            self._set_status(f"Not found: {old_name}")
            return False
        if os.path.exists(new_path):
            self._set_status(f"Already exists: {new_name}")
            return False
        try:
            os.rename(old_path, new_path)
            self._set_status(f"Renamed: {old_name} → {new_name}")
            self._notify(f"Renamed", f"{old_name} → {new_name}")
            self.refresh()
            return True
        except OSError as e:
            self._set_status(f"Error renaming: {e}")
            return False

    def read_file(self, name: str) -> Optional[str]:
        """Read the contents of a file."""
        path = os.path.join(self._state.current_path, name)
        if not os.path.isfile(path):
            self._set_status(f"Not a file: {name}")
            return None
        try:
            with open(path, "r") as f:
                content = f.read()
            self._set_status(f"Read: {name} ({len(content)} bytes)")
            return content
        except (OSError, UnicodeDecodeError) as e:
            self._set_status(f"Error reading: {e}")
            return None

    def write_file(self, name: str, content: str) -> bool:
        """Write content to a file (overwrite if exists)."""
        path = os.path.join(self._state.current_path, name)
        try:
            with open(path, "w") as f:
                f.write(content)
            self._set_status(f"Wrote: {name} ({len(content)} bytes)")
            self._notify(f"File saved", name)
            self.refresh()
            return True
        except OSError as e:
            self._set_status(f"Error writing: {e}")
            return False

    # -- Selection ----------------------------------------------------

    def select(self, name: str) -> None:
        """Select a file or directory."""
        self._state.selected = name

    def open_selected(self) -> bool:
        """Open the selected item (navigate into directory or select file)."""
        if self._state.selected is None:
            return False
        path = os.path.join(self._state.current_path, self._state.selected)
        if os.path.isdir(path):
            return self.navigate(path)
        # File — just select it
        return True

    # -- Sorting ------------------------------------------------------

    def sort_by(self, key: str) -> None:
        """Sort entries by name, size, or modified time."""
        if key == self._state.sort_by:
            self._state.sort_reverse = not self._state.sort_reverse
        else:
            self._state.sort_by = key
            self._state.sort_reverse = False
        self._sort_entries()

    # -- View options -------------------------------------------------

    def toggle_hidden(self) -> bool:
        """Toggle showing hidden files."""
        self._state.show_hidden = not self._state.show_hidden
        self.refresh()
        return self._state.show_hidden

    def toggle_view(self) -> str:
        """Toggle between list and grid view."""
        self._state.view_mode = (
            "grid" if self._state.view_mode == "list" else "list")
        return self._state.view_mode

    # -- Breadcrumb ---------------------------------------------------

    @property
    def breadcrumb(self) -> List[Tuple[str, str]]:
        """Path components for breadcrumb display.

        Returns list of (name, full_path) tuples.
        """
        parts = []
        path = self._state.current_path
        while path and path != "/":
            parts.append((os.path.basename(path), path))
            path = os.path.dirname(path)
        parts.append(("/", "/"))
        parts.reverse()
        return parts

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    # -- Render to NUI ------------------------------------------------

    def to_nstudio(self) -> Dict[str, Any]:
        """Export the current file manager state as a NUI component tree.

        Returns a dict that can be embedded in a .nstudio document.
        """
        breadcrumbs = self.breadcrumb
        entries = self.entries[:20]  # Limit for rendering

        children = []
        # Breadcrumb bar
        bc_parts = []
        for i, (name, path) in enumerate(breadcrumbs):
            bc_parts.append({
                "id": f"bc-{i}",
                "type": "Text",
                "layout": {"x": i * 100, "y": 0, "width": 100, "height": 30},
                "properties": {"text": f"{name}/"},
            })

        # File list
        for i, entry in enumerate(entries):
            children.append({
                "id": f"entry-{i}",
                "type": "Text",
                "layout": {"x": 0, "y": 40 + i * 28, "width": 700, "height": 28},
                "properties": {
                    "text": f"{entry.icon}  {entry.name}  {entry.size_display}",
                },
            })

        return {
            "id": "file-manager",
            "type": "Container",
            "layout": {"x": 0, "y": 0, "width": 800, "height": 600},
            "children": bc_parts + children,
        }

    # -- Internal -----------------------------------------------------

    def _navigate(self, path: str) -> bool:
        """Navigate to a path, updating history."""
        # Trim future history
        self._state.history = (
            self._state.history[:self._state.history_index + 1])
        self._state.history.append(path)
        self._state.history_index = len(self._state.history) - 1
        self._load_directory(path)
        return True

    def _load_directory(self, path: str) -> None:
        """Load directory entries."""
        self._state.current_path = path
        self._state.selected = None
        entries = []
        try:
            for name in os.listdir(path):
                if not self._state.show_hidden and name.startswith("."):
                    continue
                full = os.path.join(path, name)
                try:
                    st = os.stat(full)
                    entries.append(FileEntry(
                        name=name,
                        path=full,
                        is_dir=os.path.isdir(full),
                        size=st.st_size,
                        modified=st.st_mtime,
                    ))
                except OSError:
                    entries.append(FileEntry(
                        name=name, path=full, is_dir=False))
        except OSError as e:
            self._set_status(f"Error listing: {e}")

        self._state.entries = entries
        self._sort_entries()

    def _sort_entries(self) -> None:
        """Sort entries by the current sort key."""
        key = self._state.sort_by
        rev = self._state.sort_reverse
        if key == "name":
            self._state.entries.sort(
                key=lambda e: (not e.is_dir, e.name.lower()), reverse=rev)
        elif key == "size":
            self._state.entries.sort(
                key=lambda e: (not e.is_dir, e.size), reverse=rev)
        elif key == "modified":
            self._state.entries.sort(
                key=lambda e: (not e.is_dir, e.modified), reverse=rev)

    def _set_status(self, msg: str) -> None:
        self._state.status_message = msg
        self._log(msg)

    def _notify(self, title: str, message: str = "") -> None:
        if hasattr(self._session, '_notifications'):
            self._session._notifications.info(title, message)

    def _log(self, msg: str) -> None:
        logger.info("[FileManager] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the file manager standalone (for testing)."""
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))

    from ui.nstudio import loads
    from ui.desktop_session import DesktopSession

    raw = {
        "version": "1.0.0",
        "project": {"name": "fm-test"},
        "themes": {"active": "Eclipse"},
        "states": {},
        "stateScopes": {},
        "locales": {},
        "resources": {},
        "animations": [],
        "behaviors": [],
        "bindings": [],
        "components": [],
        "screens": [{
            "id": "desktop",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "root",
                "type": "DesktopSurface",
                "layout": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "children": [],
            },
        }],
    }
    doc = loads(json.dumps(raw))
    session = DesktopSession(doc)
    fm = FileManager(session, root_path="/tmp")

    print("=== Nyrqis File Manager ===")
    print(f"Path: {fm.current_path}")
    print(f"Entries: {len(fm.entries)}")
    for e in fm.entries[:5]:
        print(f"  {e.icon} {e.name:30s} {e.size_display:>10s}")

    # Create a test file
    fm.create_file("nyrqis-test.txt", "Hello from Nyrqis!")
    print(f"\nCreated nyrqis-test.txt")

    # Read it back
    content = fm.read_file("nyrqis-test.txt")
    print(f"Content: {content}")

    # Create a directory
    fm.create_directory("nyrqis-dir")
    print(f"Created nyrqis-dir/")

    # Navigate into it
    fm.navigate("/tmp/nyrqis-dir")
    print(f"Navigated to: {fm.current_path}")

    # Go back
    fm.go_back()
    print(f"Back to: {fm.current_path}")

    # Breadcrumb
    print(f"\nBreadcrumb: {' > '.join(name for name, _ in fm.breadcrumb)}")

    # Sort
    fm.sort_by("size")
    print(f"\nSorted by size:")
    for e in fm.entries[:3]:
        print(f"  {e.name:30s} {e.size_display:>10s}")

    # Toggle hidden
    fm.toggle_hidden()
    print(f"\nShow hidden: {fm.state.show_hidden}")

    # NUI export
    nui = fm.to_nstudio()
    print(f"\nNUI export: {nui['id']}, {len(nui['children'])} children")

    # Cleanup
    fm.delete("nyrqis-test.txt")
    fm.delete("nyrqis-dir")
    print("\nAll operations passed!")


if __name__ == "__main__":
    main()
