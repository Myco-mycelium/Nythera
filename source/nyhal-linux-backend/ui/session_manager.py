"""
Nyrqis OS - Session Manager
Workspace persistence, application restore, and session history.

Features:
- Session snapshots with window state, positions, and focus
- Workspace persistence (save/restore desktop layouts)
- Application restore with state preservation
- Session history with timestamps
- Auto-save on schedule and before sleep
- Session templates for different workflows
- Multi-monitor workspace support
- Quick-switch between sessions
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class AppState(Enum):
    RUNNING = "running"
    SAVED = "saved"
    RESTORING = "restoring"
    CRASHED = "crashed"
    CLOSED = "closed"
    MINIMIZED = "minimized"


class SessionType(Enum):
    FULL = "full"          # Full desktop session
    WORKSPACE = "workspace" # Single workspace
    APP_BUNDLE = "app_bundle"  # Group of apps
    SNAPSHOT = "snapshot"   # Point-in-time snapshot


class WindowState(Enum):
    NORMAL = "normal"
    MAXIMIZED = "maximized"
    MINIMIZED = "minimized"
    TILED_LEFT = "tiled_left"
    TILED_RIGHT = "tiled_right"
    TILED_TOP = "tiled_top"
    TILED_BOTTOM = "tiled_bottom"
    FLOATING = "floating"
    FULLSCREEN = "fullscreen"


class RestorePriority(Enum):
    CRITICAL = 1   # Must restore (system services)
    HIGH = 2       # User apps with state
    NORMAL = 3     # Regular apps
    LOW = 4        # Background apps
    OPTIONAL = 5   # Nice-to-have


SESSION_TYPE_ICONS = {
    SessionType.FULL: "🖥️",
    SessionType.WORKSPACE: "📁",
    SessionType.APP_BUNDLE: "📦",
    SessionType.SNAPSHOT: "📸",
}

APP_STATE_ICONS = {
    AppState.RUNNING: "🟢", AppState.SAVED: "💾",
    AppState.RESTORING: "🔄", AppState.CRASHED: "❌",
    AppState.CLOSED: "⚫", AppState.MINIMIZED: "➖",
}

WINDOW_STATE_ICONS = {
    WindowState.NORMAL: "⬜", WindowState.MAXIMIZED: "⬜",
    WindowState.MINIMIZED: "➖", WindowState.TILED_LEFT: "◀️",
    WindowState.TILED_RIGHT: "▶️", WindowState.TILED_TOP: "🔼",
    WindowState.TILED_BOTTOM: "🔽", WindowState.FLOATING: "🔲",
    WindowState.FULLSCREEN: "🔳",
}


@dataclass
class WindowInfo:
    title: str = ""
    app_name: str = ""
    pid: int = 0
    state: WindowState = WindowState.NORMAL
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080
    workspace: int = 0
    monitor: int = 0
    focused: bool = False
    sticky: bool = False
    always_on_top: bool = False
    opacity: float = 1.0

    @property
    def state_icon(self) -> str:
        return WINDOW_STATE_ICONS.get(self.state, "❓")

    @property
    def position_str(self) -> str:
        return f"({self.x},{self.y}) {self.width}×{self.height}"

    @property
    def display(self) -> str:
        focused = " ◀️" if self.focused else ""
        return f"{self.state_icon} {self.title} [{self.app_name}]{focused}"


@dataclass
class AppSession:
    app_name: str = ""
    app_path: str = ""
    state: AppState = AppState.SAVED
    pid: int = 0
    priority: RestorePriority = RestorePriority.NORMAL
    windows: List[WindowInfo] = field(default_factory=list)
    command: str = ""
    working_dir: str = ""
    env_vars: Dict[str, str] = field(default_factory=dict)
    open_files: List[str] = field(default_factory=list)
    last_active: float = 0.0
    session_data: Dict = field(default_factory=dict)

    @property
    def state_icon(self) -> str:
        return APP_STATE_ICONS.get(self.state, "❓")

    @property
    def window_count(self) -> int:
        return len(self.windows)

    @property
    def total_size_str(self) -> str:
        if not self.windows:
            return "N/A"
        return f"{len(self.windows)} window(s)"

    @property
    def priority_str(self) -> str:
        icons = {1: "🔴", 2: "🟠", 3: "🟢", 4: "🔵", 5: "⚪"}
        return icons.get(self.priority.value, "❓")

    @property
    def last_active_str(self) -> str:
        if self.last_active == 0:
            return "N/A"
        delta = time.time() - self.last_active
        if delta < 60:
            return "Just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class WorkspaceState:
    id: int = 0
    name: str = ""
    wallpaper: str = ""
    apps: List[AppSession] = field(default_factory=list)
    window_count: int = 0
    is_active: bool = False
    last_used: float = 0.0

    @property
    def active_icon(self) -> str:
        return "🟢" if self.is_active else "⚫"

    @property
    def last_used_str(self) -> str:
        if self.last_used == 0:
            return "Never"
        delta = time.time() - self.last_used
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.1f}h ago"

    @property
    def display(self) -> str:
        return f"{self.active_icon} WS{self.id}: {self.name} ({self.window_count} windows)"


@dataclass
class SessionSnapshot:
    id: int = 0
    name: str = ""
    session_type: SessionType = SessionType.FULL
    timestamp: float = 0.0
    workspaces: List[WorkspaceState] = field(default_factory=list)
    apps: List[AppSession] = field(default_factory=list)
    monitor_config: str = ""
    resolution: str = ""
    theme: str = ""
    notes: str = ""
    auto_created: bool = False
    size_estimate_kb: int = 0

    @property
    def type_icon(self) -> str:
        return SESSION_TYPE_ICONS.get(self.session_type, "❓")

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def age_str(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "Just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def total_windows(self) -> int:
        return sum(a.window_count for a in self.apps)

    @property
    def total_apps(self) -> int:
        return len(self.apps)

    @property
    def size_str(self) -> str:
        if self.size_estimate_kb < 1024:
            return f"{self.size_estimate_kb} KB"
        return f"{self.size_estimate_kb / 1024:.1f} MB"

    @property
    def display(self) -> str:
        auto = " [auto]" if self.auto_created else ""
        return f"{self.type_icon} {self.name}{auto} ({self.total_apps} apps, {self.total_windows} windows)"


@dataclass
class SessionTemplate:
    name: str = ""
    description: str = ""
    apps: List[str] = field(default_factory=list)
    layout: str = "default"  # default, coding, research, presentation
    use_count: int = 0
    created: float = 0.0

    @property
    def icon(self) -> str:
        icons = {
            "default": "🖥️", "coding": "💻", "research": "📚",
            "presentation": "📊", "gaming": "🎮",
        }
        return icons.get(self.layout, "📁")


@dataclass
class SessionEvent:
    timestamp: float = 0.0
    event_type: str = ""  # save, restore, create, delete, auto_save
    session_name: str = ""
    details: str = ""
    success: bool = True

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {
            "save": "💾", "restore": "🔄", "create": "✨",
            "delete": "🗑️", "auto_save": "⏱️",
        }
        return icons.get(self.event_type, "❓")

    @property
    def status_icon(self) -> str:
        return "✅" if self.success else "❌"


class SessionManager:
    def __init__(self):
        self.snapshots: List[SessionSnapshot] = []
        self.templates: List[SessionTemplate] = []
        self.events: List[SessionEvent] = []
        self.active_workspaces: List[WorkspaceState] = []
        self._selected_snapshot: int = 0
        self._view_mode: str = "snapshots"
        self._auto_save_interval_min: int = 15
        self._auto_save_enabled: bool = True
        self._snapshot_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        # Active workspaces
        self.active_workspaces = [
            WorkspaceState(0, "Desktop", "/usr/share/wallpapers/nyrqis-dark.png",
                           [], 8, True, now - 60),
            WorkspaceState(1, "Code", "/usr/share/wallpapers/code-matrix.png",
                           [], 4, False, now - 3600),
            WorkspaceState(2, "Terminal", "/usr/share/wallpapers/terminal-green.png",
                           [], 3, False, now - 7200),
            WorkspaceState(3, "Monitoring", "/usr/share/wallpapers/dashboard.png",
                           [], 2, False, now - 86400),
            WorkspaceState(9, "Kiosk", "/usr/share/wallpapers/kiosk.png",
                           [], 1, False, now - 86400 * 7),
        ]

        # Snapshots
        self.snapshots = [
            SessionSnapshot(
                id=1, name="Daily Work",
                session_type=SessionType.FULL,
                timestamp=now - 300,
                apps=[
                    AppSession("Firefox", "/usr/bin/firefox", AppState.SAVED, 4521,
                               RestorePriority.HIGH,
                               [WindowInfo("Nyrqis Docs", "Firefox", 4521,
                                           WindowState.NORMAL, 100, 50, 1400, 900, 0, 0, True)],
                               command="firefox --restore-session",
                               open_files=["https://docs.nyrqis.dev"],
                               last_active=now - 300),
                    AppSession("VS Code", "/usr/bin/code-server", AppState.SAVED, 1234,
                               RestorePriority.HIGH,
                               [WindowInfo("main.py", "VS Code", 1234,
                                           WindowState.TILED_LEFT, 0, 0, 960, 1080, 1, 0),
                                WindowInfo("test_main.py", "VS Code", 1234,
                                           WindowState.TILED_RIGHT, 960, 0, 960, 1080, 1, 0)],
                               command="code-server --restore",
                               working_dir="/home/user/projects/nyrqis",
                               open_files=["main.py", "test_main.py", "config.yaml"],
                               last_active=now - 120),
                    AppSession("Terminal", "/usr/bin/nyrqis-terminal", AppState.SAVED, 890,
                               RestorePriority.NORMAL,
                               [WindowInfo("~", "Terminal", 890,
                                           WindowState.NORMAL, 200, 200, 800, 600, 0, 0)],
                               command="nyrqis-terminal",
                               working_dir="/home/user",
                               last_active=now - 600),
                    AppSession("Spotify", "/usr/bin/spotify", AppState.SAVED, 7823,
                               RestorePriority.LOW,
                               [WindowInfo("Spotify", "Spotify", 7823,
                                           WindowState.MINIMIZED, 0, 0, 1200, 800, 0, 0)],
                               command="spotify",
                               last_active=now - 1800),
                    AppSession("Discord", "/usr/bin/discord", AppState.SAVED, 5634,
                               RestorePriority.LOW,
                               [WindowInfo("Discord", "Discord", 5634,
                                           WindowState.MINIMIZED, 100, 100, 1000, 700, 0, 0)],
                               command="discord",
                               last_active=now - 3600),
                ],
                monitor_config="Dual: ASUS 2560x1440 + Dell 1920x1080",
                resolution="2560x1440",
                theme="Nyrqis Dark",
                notes="Regular work session",
                size_estimate_kb=125,
            ),
            SessionSnapshot(
                id=2, name="Coding Sprint",
                session_type=SessionType.WORKSPACE,
                timestamp=now - 86400,
                apps=[
                    AppSession("VS Code", "/usr/bin/code-server", AppState.SAVED, 1234,
                               RestorePriority.CRITICAL,
                               [WindowInfo("src/main.rs", "VS Code", 1234,
                                           WindowState.FULLSCREEN, 0, 0, 2560, 1440, 1, 0)],
                               working_dir="/home/user/projects/nyrqis-kernel",
                               open_files=["src/main.rs", "src/compositor.rs", "Cargo.toml"],
                               last_active=now - 86400),
                    AppSession("Terminal", "/usr/bin/nyrqis-terminal", AppState.SAVED, 891,
                               RestorePriority.HIGH,
                               [WindowInfo("cargo build", "Terminal", 891,
                                           WindowState.TILED_BOTTOM, 0, 720, 2560, 720, 1, 0)],
                               working_dir="/home/user/projects/nyrqis-kernel",
                               last_active=now - 86400),
                ],
                resolution="2560x1440",
                notes="Rust kernel development",
                size_estimate_kb=85,
            ),
            SessionSnapshot(
                id=3, name="Research Session",
                session_type=SessionType.FULL,
                timestamp=now - 86400 * 3,
                apps=[
                    AppSession("Firefox", "/usr/bin/firefox", AppState.SAVED, 4521,
                               RestorePriority.HIGH,
                               [WindowInfo("Research Papers", "Firefox", 4521,
                                           WindowState.TILED_LEFT, 0, 0, 1280, 1440, 0, 0),
                                WindowInfo("Wikipedia", "Firefox", 4521,
                                           WindowState.TILED_RIGHT, 1280, 0, 1280, 1440, 0, 0)],
                               open_files=["arxiv.org/...", "en.wikipedia.org/..."],
                               last_active=now - 86400 * 3),
                    AppSession("Obsidian", "/usr/bin/obsidian", AppState.SAVED, 9900,
                               RestorePriority.HIGH,
                               [WindowInfo("Notes", "Obsidian", 9900,
                                           WindowState.NORMAL, 300, 100, 1800, 1000, 0, 0)],
                               working_dir="/home/user/Documents/notes",
                               last_active=now - 86400 * 3),
                ],
                resolution="2560x1440",
                notes="OS design research",
                size_estimate_kb=68,
            ),
            SessionSnapshot(
                id=4, name="System Recovery",
                session_type=SessionType.SNAPSHOT,
                timestamp=now - 86400 * 7,
                apps=[
                    AppSession("nyrqis-compositor", "/usr/bin/nyrqis-compositor",
                               AppState.SAVED, 2, RestorePriority.CRITICAL,
                               [WindowInfo("Desktop", "Compositor", 2,
                                           WindowState.MAXIMIZED, 0, 0, 2560, 1440, 0, 0)],
                               command="nyrqis-compositor --wayland",
                               last_active=now - 86400 * 7),
                    AppSession("nyrqis-shell", "/usr/bin/nyrqis-shell",
                               AppState.SAVED, 3, RestorePriority.CRITICAL,
                               command="nyrqis-shell --panel",
                               last_active=now - 86400 * 7),
                ],
                notes="Pre-update recovery snapshot",
                auto_created=True,
                size_estimate_kb=45,
            ),
        ]
        self._snapshot_counter = 5

        # Templates
        self.templates = [
            SessionTemplate("Developer", "Code + Terminal + Browser",
                            ["code-server", "nyrqis-terminal", "firefox"], "coding", 24,
                            now - 86400 * 30),
            SessionTemplate("Research", "Browser + Notes + PDF Reader",
                            ["firefox", "obsidian", "evince"], "research", 8,
                            now - 86400 * 60),
            SessionTemplate("Presenter", "Slides + Notes + Timer",
                            ["libreoffice-impress", "obsidian", "nyrqis-terminal"], "presentation", 3,
                            now - 86400 * 90),
            SessionTemplate("Gaming", "Game + Discord + Music",
                            ["steam", "discord", "spotify"], "gaming", 12,
                            now - 86400 * 15),
            SessionTemplate("Minimal", "Terminal only",
                            ["nyrqis-terminal"], "default", 5,
                            now - 86400 * 45),
        ]

        # Events
        self.events = [
            SessionEvent(now - 300, "save", "Daily Work", "Auto-saved"),
            SessionEvent(now - 600, "restore", "Daily Work", "Restored 5 apps"),
            SessionEvent(now - 1800, "auto_save", "Daily Work", "15-min auto-save"),
            SessionEvent(now - 3600, "create", "Coding Sprint", "New workspace snapshot"),
            SessionEvent(now - 7200, "save", "Daily Work", "Manual save"),
            SessionEvent(now - 86400, "save", "Coding Sprint", "Manual save"),
            SessionEvent(now - 86400 * 3, "save", "Research Session", "Manual save"),
            SessionEvent(now - 86400 * 7, "auto_save", "System Recovery", "Pre-update snapshot"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_snapshot(self) -> Optional[SessionSnapshot]:
        if 0 <= self._selected_snapshot < len(self.snapshots):
            return self.snapshots[self._selected_snapshot]
        return None

    def select_snapshot(self, idx: int):
        if 0 <= idx < len(self.snapshots):
            self._selected_snapshot = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_snapshot = min(self._selected_snapshot + 1, len(self.snapshots) - 1)

    def select_up(self):
        self._selected_snapshot = max(self._selected_snapshot - 1, 0)

    # ─── Session Actions ───────────────────────────────────────────────

    def save_session(self, name: str = "", notes: str = "") -> SessionSnapshot:
        now = time.time()
        self._snapshot_counter += 1
        snapshot = SessionSnapshot(
            id=self._snapshot_counter,
            name=name or f"Session {self._snapshot_counter}",
            session_type=SessionType.FULL,
            timestamp=now,
            apps=[
                AppSession("Firefox", "/usr/bin/firefox", AppState.SAVED,
                           RestorePriority.HIGH),
                AppSession("VS Code", "/usr/bin/code-server", AppState.SAVED,
                           RestorePriority.HIGH),
                AppSession("Terminal", "/usr/bin/nyrqis-terminal", AppState.SAVED,
                           RestorePriority.NORMAL),
            ],
            resolution="2560x1440",
            theme="Nyrqis Dark",
            notes=notes,
            size_estimate_kb=120,
        )
        self.snapshots.insert(0, snapshot)
        self.events.insert(0, SessionEvent(now, "save", snapshot.name, "Manual save"))
        return snapshot

    def restore_session(self, idx: int) -> bool:
        if 0 <= idx < len(self.snapshots):
            snapshot = self.snapshots[idx]
            app_count = len(snapshot.apps)
            self.events.insert(0, SessionEvent(
                time.time(), "restore", snapshot.name,
                f"Restored {app_count} apps"
            ))
            return True
        return False

    def delete_session(self, idx: int) -> bool:
        if 0 <= idx < len(self.snapshots):
            snapshot = self.snapshots.pop(idx)
            self.events.insert(0, SessionEvent(
                time.time(), "delete", snapshot.name, "Session deleted"
            ))
            if self._selected_snapshot >= len(self.snapshots):
                self._selected_snapshot = max(0, len(self.snapshots) - 1)
            return True
        return False

    def duplicate_session(self, idx: int) -> Optional[SessionSnapshot]:
        if 0 <= idx < len(self.snapshots):
            original = self.snapshots[idx]
            self._snapshot_counter += 1
            copy = SessionSnapshot(
                id=self._snapshot_counter,
                name=f"{original.name} (copy)",
                session_type=original.session_type,
                timestamp=time.time(),
                apps=list(original.apps),
                monitor_config=original.monitor_config,
                resolution=original.resolution,
                theme=original.theme,
                notes=original.notes,
                size_estimate_kb=original.size_estimate_kb,
            )
            self.snapshots.insert(idx + 1, copy)
            return copy
        return None

    def create_from_template(self, template_idx: int) -> Optional[SessionSnapshot]:
        if 0 <= template_idx < len(self.templates):
            template = self.templates[template_idx]
            template.use_count += 1
            now = time.time()
            self._snapshot_counter += 1
            apps = [
                AppSession(app_name, f"/usr/bin/{app_name}", AppState.SAVED,
                           RestorePriority.NORMAL)
                for app_name in template.apps
            ]
            snapshot = SessionSnapshot(
                id=self._snapshot_counter,
                name=template.name,
                session_type=SessionType.APP_BUNDLE,
                timestamp=now, apps=apps,
                notes=f"Created from template: {template.name}",
                size_estimate_kb=60,
            )
            self.snapshots.insert(0, snapshot)
            self.events.insert(0, SessionEvent(now, "create", snapshot.name,
                                                f"From template: {template.name}"))
            return snapshot
        return None

    # ─── Auto-save ─────────────────────────────────────────────────────

    def toggle_auto_save(self):
        self._auto_save_enabled = not self._auto_save_enabled

    def set_auto_save_interval(self, minutes: int):
        self._auto_save_interval_min = max(5, min(120, minutes))

    # ─── Queries ───────────────────────────────────────────────────────

    def get_full_sessions(self) -> List[SessionSnapshot]:
        return [s for s in self.snapshots if s.session_type == SessionType.FULL]

    def get_recent(self, count: int = 3) -> List[SessionSnapshot]:
        return sorted(self.snapshots, key=lambda s: s.timestamp, reverse=True)[:count]

    def get_auto_created(self) -> List[SessionSnapshot]:
        return [s for s in self.snapshots if s.auto_created]

    def search(self, query: str) -> List[SessionSnapshot]:
        q = query.lower()
        return [s for s in self.snapshots
                if q in s.name.lower() or q in s.notes.lower()
                or any(q in a.app_name.lower() for a in s.apps)]

    def get_stats(self) -> Dict:
        return {
            "snapshots": len(self.snapshots),
            "full_sessions": len(self.get_full_sessions()),
            "templates": len(self.templates),
            "total_apps": sum(s.total_apps for s in self.snapshots),
            "total_windows": sum(s.total_windows for s in self.snapshots),
            "events": len(self.events),
            "auto_save": self._auto_save_enabled,
            "auto_save_interval": self._auto_save_interval_min,
        }
