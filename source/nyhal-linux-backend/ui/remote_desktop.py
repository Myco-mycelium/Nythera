"""
Nyrqis Remote Desktop — remote desktop connection application.

Features:
- VNC and RDP connection support
- Saved connections with groups
- Session recording and playback
- Multi-monitor selection
- Connection quality settings
- Keyboard/mouse input settings
- Clipboard sharing
- File transfer
- Connection history
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ConnectionProtocol(Enum):
    VNC = "VNC"
    RDP = "RDP"
    SSH = "SSH X11"
    SPICE = "SPICE"


class ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    SAVED = "saved"


class ConnectionQuality(Enum):
    AUTO = "auto"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ULTRA = "ultra"


PROTOCOL_ICONS = {
    ConnectionProtocol.VNC: "🖥️",
    ConnectionProtocol.RDP: "🪟",
    ConnectionProtocol.SSH: "🔐",
    ConnectionProtocol.SPICE: "📡",
}

STATUS_ICONS = {
    ConnectionStatus.DISCONNECTED: "⚫",
    ConnectionStatus.CONNECTING: "🟡",
    ConnectionStatus.CONNECTED: "🟢",
    ConnectionStatus.ERROR: "🔴",
    ConnectionStatus.SAVED: "💾",
}

QUALITY_PRESETS = {
    ConnectionQuality.AUTO: {"color_depth": 32, "compression": 6, "fps": 60},
    ConnectionQuality.HIGH: {"color_depth": 24, "compression": 4, "fps": 60},
    ConnectionQuality.MEDIUM: {"color_depth": 16, "compression": 6, "fps": 30},
    ConnectionQuality.LOW: {"color_depth": 8, "compression": 9, "fps": 15},
    ConnectionQuality.ULTRA: {"color_depth": 32, "compression": 0, "fps": 120},
}


@dataclass
class RemoteConnection:
    """A saved remote desktop connection."""
    name: str
    host: str
    port: int = 5900
    protocol: ConnectionProtocol = ConnectionProtocol.VNC
    username: str = ""
    password: str = ""  # stored encrypted in real impl
    status: ConnectionStatus = ConnectionStatus.SAVED
    quality: ConnectionQuality = ConnectionQuality.AUTO
    # Settings
    color_depth: int = 32
    fullscreen: bool = True
    clipboard_sharing: bool = True
    audio_sharing: bool = False
    file_transfer: bool = True
    # Multi-monitor
    use_all_monitors: bool = False
    monitor_index: int = 0
    # Connection info
    last_connected: float = 0.0
    connect_count: int = 0
    last_duration: float = 0.0
    group: str = "Default"
    notes: str = ""
    # Tags
    tags: List[str] = field(default_factory=list)
    connection_id: str = ""

    def __post_init__(self):
        if not self.connection_id:
            self.connection_id = hashlib.md5(f"{self.host}{self.port}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def protocol_icon(self) -> str:
        return PROTOCOL_ICONS.get(self.protocol, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} ({self.protocol.value})"

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def last_connected_str(self) -> str:
        if self.last_connected <= 0:
            return "never"
        diff = time.time() - self.last_connected
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.last_connected).strftime("%b %d")

    @property
    def duration_str(self) -> str:
        if self.last_duration <= 0:
            return "—"
        m = int(self.last_duration // 60)
        s = int(self.last_duration % 60)
        return f"{m}m {s}s"


@dataclass
class SessionRecording:
    """A recorded remote desktop session."""
    connection_name: str
    filename: str
    duration_seconds: float = 0.0
    size_kb: int = 0
    created: float = field(default_factory=time.time)
    recording_id: str = ""

    def __post_init__(self):
        if not self.recording_id:
            self.recording_id = hashlib.md5(f"{self.filename}{self.created}".encode()).hexdigest()[:8]

    @property
    def size_str(self) -> str:
        if self.size_kb >= 1024:
            return f"{self.size_kb / 1024:.1f} MB"
        return f"{self.size_kb} KB"

    @property
    def duration_str(self) -> str:
        m = int(self.duration_seconds // 60)
        s = int(self.duration_seconds % 60)
        return f"{m}:{s:02d}"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")

    @property
    def display(self) -> str:
        return f"🎬 {self.connection_name} — {self.duration_str} ({self.size_str})"


@dataclass
class ConnectionHistory:
    """A connection history entry."""
    connection_name: str
    host: str
    protocol: ConnectionProtocol
    started_at: float = 0.0
    ended_at: float = 0.0
    status: str = "normal"  # normal, error, timeout
    duration: float = 0.0

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M")

    @property
    def duration_str(self) -> str:
        m = int(self.duration // 60)
        s = int(self.duration % 60)
        return f"{m}m {s}s"


# ─── Remote Desktop Client ───────────────────────────────────────────────


class RemoteDesktop:
    """
    Remote desktop connection manager for Nyrqis OS.
    """

    def __init__(self):
        self._connections: List[RemoteConnection] = []
        self._recordings: List[SessionRecording] = []
        self._history: List[ConnectionHistory] = []
        self._selected_index: int = 0
        self._view_mode: str = "connections"  # connections, recordings, history, settings
        self._active_connection: Optional[RemoteConnection] = None

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._connections = [
            RemoteConnection("Home Server", "192.168.1.50", 5900, ConnectionProtocol.VNC,
                             "admin", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.HIGH,
                             last_connected=now - 3600, connect_count=45,
                             last_duration=3600, group="Home",
                             tags=["server", "linux"]),
            RemoteConnection("Work Desktop", "10.0.0.100", 3389, ConnectionProtocol.RDP,
                             "john.doe", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.AUTO,
                             last_connected=now - 86400, connect_count=120,
                             last_duration=28800, group="Work",
                             tags=["windows", "office"]),
            RemoteConnection("NAS Admin", "192.168.1.10", 5901, ConnectionProtocol.VNC,
                             "root", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.MEDIUM,
                             last_connected=now - 604800, connect_count=12,
                             last_duration=1800, group="Home",
                             tags=["nas", "storage"]),
            RemoteConnection("Cloud VM", "203.0.113.50", 3389, ConnectionProtocol.RDP,
                             "azure-admin", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.AUTO,
                             last_connected=now - 172800, connect_count=8,
                             last_duration=7200, group="Cloud",
                             tags=["azure", "vm"]),
            RemoteConnection("Dev Container", "localhost", 5902, ConnectionProtocol.VNC,
                             "", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.HIGH,
                             last_connected=now - 14400, connect_count=30,
                             last_duration=5400, group="Dev",
                             tags=["docker", "local"]),
            RemoteConnection("Raspberry Pi", "192.168.1.80", 5900, ConnectionProtocol.VNC,
                             "pi", status=ConnectionStatus.SAVED,
                             quality=ConnectionQuality.LOW,
                             last_connected=now - 259200, connect_count=5,
                             last_duration=600, group="Home",
                             tags=["arm", "iot"]),
        ]

        # Recordings
        self._recordings = [
            SessionRecording("Work Desktop", "work-session-2026-09-01.webm",
                             1800, 25000, now - 172800),
            SessionRecording("Home Server", "server-maint-2026-09-02.mkv",
                             3600, 52000, now - 86400),
            SessionRecording("Cloud VM", "deploy-2026-09-03.webm",
                             900, 12000, now - 3600),
        ]

        # History
        self._history = [
            ConnectionHistory("Work Desktop", "10.0.0.100", ConnectionProtocol.RDP,
                              now - 86400, now - 86400 + 28800, "normal", 28800),
            ConnectionHistory("Home Server", "192.168.1.50", ConnectionProtocol.VNC,
                              now - 3600, now - 3600 + 3600, "normal", 3600),
            ConnectionHistory("Cloud VM", "203.0.113.50", ConnectionProtocol.RDP,
                              now - 172800, now - 172800 + 7200, "normal", 7200),
            ConnectionHistory("Dev Container", "localhost", ConnectionProtocol.VNC,
                              now - 14400, now - 14400 + 5400, "normal", 5400),
            ConnectionHistory("Home Server", "192.168.1.50", ConnectionProtocol.VNC,
                              now - 259200, now - 259200 + 600, "error", 600),
        ]

    # ── Connection Operations ─────────────────────────────────────────

    def connect(self, index: int) -> bool:
        if 0 <= index < len(self._connections):
            conn = self._connections[index]
            conn.status = ConnectionStatus.CONNECTED
            conn.last_connected = time.time()
            conn.connect_count += 1
            self._active_connection = conn
            return True
        return False

    def disconnect(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._connections):
            conn = self._connections[idx]
            if conn.status == ConnectionStatus.CONNECTED:
                conn.status = ConnectionStatus.SAVED
                self._active_connection = None
                return True
        return False

    def add_connection(self, name: str, host: str, port: int,
                       protocol: ConnectionProtocol = ConnectionProtocol.VNC) -> RemoteConnection:
        conn = RemoteConnection(name=name, host=host, port=port, protocol=protocol)
        self._connections.append(conn)
        return conn

    def delete_connection(self, index: int) -> bool:
        if 0 <= index < len(self._connections):
            conn = self._connections[index]
            if conn.status != ConnectionStatus.CONNECTED:
                self._connections.pop(index)
                return True
        return False

    def toggle_setting(self, index: int, setting: str) -> bool:
        if 0 <= index < len(self._connections):
            conn = self._connections[index]
            if setting == "fullscreen":
                conn.fullscreen = not conn.fullscreen
            elif setting == "clipboard":
                conn.clipboard_sharing = not conn.clipboard_sharing
            elif setting == "audio":
                conn.audio_sharing = not conn.audio_sharing
            elif setting == "file_transfer":
                conn.file_transfer = not conn.file_transfer
            elif setting == "all_monitors":
                conn.use_all_monitors = not conn.use_all_monitors
            return True
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "recordings":
            return self._recordings
        elif self._view_mode == "history":
            return self._history
        return self._connections

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def connections(self) -> List[RemoteConnection]:
        return list(self._connections)

    @property
    def recordings(self) -> List[SessionRecording]:
        return list(self._recordings)

    @property
    def history(self) -> List[ConnectionHistory]:
        return list(self._history)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections if c.status == ConnectionStatus.CONNECTED)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_connections(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🖥️  Remote Desktop ({len(self._connections)} connections, {self.connected_count} active)")
        lines.append("─" * width)

        for i, conn in enumerate(self._connections):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {conn.display}")
            lines.append(f"   {conn.protocol_icon} {conn.address} | User: {conn.username or '—'}")
            lines.append(f"   Group: {conn.group} | Quality: {conn.quality.value}")
            lines.append(f"   Last: {conn.last_connected_str} | Sessions: {conn.connect_count} | Duration: {conn.duration_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Connect  X:Disconnect  S:Settings")
        lines.append(" R:Recordings  H:History  Del:Delete  Esc:Back")
        return lines

    def render_detail(self, width: int = 70) -> List[str]:
        conn = self.get_selected_item()
        if not conn:
            return ["No connection selected"]

        lines = []
        lines.append(f" {conn.protocol_icon} {conn.name}")
        lines.append("─" * width)
        lines.append(f" Host:       {conn.host}:{conn.port}")
        lines.append(f" Protocol:   {conn.protocol.value}")
        lines.append(f" Username:   {conn.username or '—'}")
        lines.append(f" Password:   {'•' * 8 if conn.password else '—'}")
        lines.append(f" Status:     {conn.status.value}")
        lines.append(f" Group:      {conn.group}")
        lines.append(f" Quality:    {conn.quality.value}")
        lines.append(f" Fullscreen: {'✅' if conn.fullscreen else '❌'}")
        lines.append(f" Clipboard:  {'✅' if conn.clipboard_sharing else '❌'}")
        lines.append(f" Audio:      {'✅' if conn.audio_sharing else '❌'}")
        lines.append(f" Files:      {'✅' if conn.file_transfer else '❌'}")
        lines.append(f" Monitors:   {'All' if conn.use_all_monitors else f'#{conn.monitor_index}'}")
        lines.append("")
        lines.append(f" Sessions:   {conn.connect_count}")
        lines.append(f" Last:       {conn.last_connected_str}")
        lines.append(f" Duration:   {conn.duration_str}")
        if conn.notes:
            lines.append(f" Notes:      {conn.notes}")
        if conn.tags:
            lines.append(f" Tags:       {', '.join(conn.tags)}")

        lines.append("─" * width)
        if conn.status == ConnectionStatus.CONNECTED:
            lines.append(" X:Disconnect")
        else:
            lines.append(" Enter:Connect")
        lines.append(" Esc:Back")
        return lines

    def render_recordings(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🎬 Session Recordings ({len(self._recordings)})")
        lines.append("─" * width)

        for i, rec in enumerate(self._recordings):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {rec.display}")
            lines.append(f"   File: {rec.filename} | Created: {rec.time_ago}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Delete  Esc:Back")
        return lines

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📜 Connection History ({len(self._history)})")
        lines.append("─" * width)

        for i, entry in enumerate(self._history):
            marker = "▸" if i == self._selected_index else " "
            status_icon = "✅" if entry.status == "normal" else "❌"
            lines.append(f"{marker} {status_icon} {entry.connection_name} ({entry.protocol.value})")
            lines.append(f"   {entry.host} | {entry.time_str} | Duration: {entry.duration_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "detail": self.render_detail,
            "recordings": self.render_recordings,
            "history": self.render_history,
        }
        renderer = renderers.get(self._view_mode, self.render_connections)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "detail":
            return self._handle_detail_key(key)
        elif self._view_mode == "recordings":
            return self._handle_recordings_key(key)
        elif self._view_mode == "history":
            return self._handle_history_key(key)
        return self._handle_connections_key(key)

    def _handle_connections_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            conn = self.get_selected_item()
            if conn and conn.status != ConnectionStatus.CONNECTED:
                return "connect" if self.connect(self._selected_index) else "connect_failed"
            self.set_view("detail")
            return "detail"
        elif key == "x":
            return "disconnect" if self.disconnect() else "disconnect_failed"
        elif key == "r":
            self.set_view("recordings")
            return "recordings"
        elif key == "h":
            self.set_view("history")
            return "history"
        elif key == "Delete":
            return "delete" if self.delete_connection(self._selected_index) else "delete_failed"
        return None

    def _handle_detail_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("connections")
            return "back"
        elif key == "Enter":
            conn = self.get_selected_item()
            if conn and conn.status != ConnectionStatus.CONNECTED:
                return "connect" if self.connect(self._selected_index) else "connect_failed"
        elif key == "x":
            return "disconnect" if self.disconnect() else "disconnect_failed"
        return None

    def _handle_recordings_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("connections")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("connections")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None
