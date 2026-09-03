"""
Nyrqis VPN Manager — VPN connection management with profiles and diagnostics.

Features:
- VPN profile management (WireGuard, OpenVPN, IPSec)
- Connect/disconnect with status tracking
- Connection statistics (bytes, duration, speed)
- Kill switch toggle
- DNS leak protection
- Split tunneling configuration
- Auto-connect on startup
- Connection history and logs
- Server ping/latency testing
- Profile import/export
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class VPNProtocol(Enum):
    WIREGUARD = "WireGuard"
    OPENVPN = "OpenVPN"
    IPSEC = "IPSec/L2TP"
    SSTP = "SSTP"
    IKEV2 = "IKEv2"


class VPNStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class VPNRegion(Enum):
    US_EAST = "US East"
    US_WEST = "US West"
    EUROPE = "Europe"
    ASIA = "Asia"
    OCEANIA = "Oceania"
    SOUTH_AMERICA = "South America"


STATUS_ICONS = {
    VPNStatus.DISCONNECTED: "⚫",
    VPNStatus.CONNECTING: "🟡",
    VPNStatus.CONNECTED: "🟢",
    VPNStatus.RECONNECTING: "🟠",
    VPNStatus.ERROR: "🔴",
}


@dataclass
class VPNServer:
    """A VPN server endpoint."""
    name: str
    region: VPNRegion
    address: str = ""
    port: int = 51820
    protocol: VPNProtocol = VPNProtocol.WIREGUARD
    latency_ms: float = 0.0
    load_percent: float = 0.0
    is_premium: bool = False

    @property
    def display(self) -> str:
        return f"{self.name} ({self.region.value})"

    @property
    def load_bar(self) -> str:
        filled = int(self.load_percent / 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class VPNProfile:
    """A VPN connection profile."""
    name: str
    protocol: VPNProtocol = VPNProtocol.WIREGUARD
    server: Optional[VPNServer] = None
    status: VPNStatus = VPNStatus.DISCONNECTED
    auto_connect: bool = False
    kill_switch: bool = True
    dns_leak_protection: bool = True
    split_tunnel: bool = False
    split_tunnel_apps: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_connected: float = 0.0
    profile_id: str = ""

    def __post_init__(self):
        if not self.profile_id:
            self.profile_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def server_str(self) -> str:
        return self.server.display if self.server else "No server"


@dataclass
class VPNStats:
    """Connection statistics."""
    bytes_sent: int = 0
    bytes_received: int = 0
    duration_seconds: float = 0.0
    connection_time: float = 0.0
    reconnections: int = 0
    dns_queries: int = 0

    @property
    def bytes_sent_str(self) -> str:
        return self._fmt_bytes(self.bytes_sent)

    @property
    def bytes_received_str(self) -> str:
        return self._fmt_bytes(self.bytes_received)

    @property
    def duration_str(self) -> str:
        h = int(self.duration_seconds // 3600)
        m = int((self.duration_seconds % 3600) // 60)
        s = int(self.duration_seconds % 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        return f"{m}m {s}s"

    def _fmt_bytes(self, b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class ConnectionLog:
    """A VPN connection log entry."""
    timestamp: float = field(default_factory=time.time)
    event: str = ""
    detail: str = ""
    is_error: bool = False

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    @property
    def icon(self) -> str:
        return "❌" if self.is_error else "ℹ️"


# ─── VPN Manager ─────────────────────────────────────────────────────────


class VPNManager:
    """
    VPN connection manager for Nyrqis OS.

    Manages VPN profiles, servers, and connection state.
    """

    def __init__(self):
        self._profiles: List[VPNProfile] = []
        self._servers: List[VPNServer] = []
        self._stats = VPNStats()
        self._logs: List[ConnectionLog] = []
        self._connected_profile: Optional[VPNProfile] = None

        # View state
        self._view_mode: str = "profiles"  # profiles, servers, stats, logs, settings
        self._selected_index: int = 0

        # Callbacks
        self._on_status_change: List[Callable] = []

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()

        # Servers
        self._servers = [
            VPNServer("New York", VPNRegion.US_EAST, "ny.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 45, 35),
            VPNServer("Los Angeles", VPNRegion.US_WEST, "la.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 62, 28),
            VPNServer("Chicago", VPNRegion.US_EAST, "chi.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 55, 42),
            VPNServer("London", VPNRegion.EUROPE, "lon.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 120, 55),
            VPNServer("Frankfurt", VPNRegion.EUROPE, "fra.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 135, 38),
            VPNServer("Tokyo", VPNRegion.ASIA, "tyo.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 180, 62),
            VPNServer("Singapore", VPNRegion.ASIA, "sgp.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 165, 45),
            VPNServer("Sydney", VPNRegion.OCEANIA, "syd.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 200, 30),
            VPNServer("São Paulo", VPNRegion.SOUTH_AMERICA, "gru.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 190, 22),
            VPNServer("Mumbai", VPNRegion.ASIA, "bom.vpn.nyrqis.os", 51820, VPNProtocol.WIREGUARD, 175, 48),
        ]

        # Profiles
        self._profiles = [
            VPNProfile(
                "Nyrqis VPN (WireGuard)", VPNProtocol.WIREGUARD,
                self._servers[0], VPNStatus.DISCONNECTED,
                auto_connect=True, kill_switch=True, dns_leak_protection=True,
                created=now - 86400 * 30, last_connected=now - 3600,
                profile_id="prof_default",
            ),
            VPNProfile(
                "Work VPN (OpenVPN)", VPNProtocol.OPENVPN,
                self._servers[3], VPNStatus.DISCONNECTED,
                auto_connect=False, kill_switch=False, split_tunnel=True,
                split_tunnel_apps=["firefox", "code"],
                created=now - 86400 * 60, last_connected=now - 86400,
                profile_id="prof_work",
            ),
            VPNProfile(
                "Travel VPN (IKEv2)", VPNProtocol.IKEV2,
                self._servers[5], VPNStatus.DISCONNECTED,
                auto_connect=True, kill_switch=True,
                created=now - 86400 * 15, last_connected=now - 86400 * 2,
                profile_id="prof_travel",
            ),
        ]

        # Sample logs
        events = [
            ("Connected to New York", "WireGuard handshake completed", False),
            ("DNS leak test passed", "All queries routed through tunnel", False),
            ("Connection lost", "Server unreachable, reconnecting...", True),
            ("Reconnected to New York", "Automatic reconnection successful", False),
            ("Kill switch activated", "Blocked non-VPN traffic", False),
            ("Profile imported", "Work VPN configuration loaded", False),
        ]
        for event, detail, is_error in events:
            self._logs.append(ConnectionLog(
                timestamp=now - random.randint(0, 86400),
                event=event, detail=detail, is_error=is_error,
            ))
        self._logs.sort(key=lambda l: -l.timestamp)

    # ── Connection Management ─────────────────────────────────────────

    def connect(self, profile_id: str = None) -> bool:
        """Connect to a VPN profile."""
        target_id = profile_id
        if not target_id and 0 <= self._selected_index < len(self._profiles):
            target_id = self._profiles[self._selected_index].profile_id

        for profile in self._profiles:
            if profile.profile_id == target_id:
                # Disconnect current
                if self._connected_profile:
                    self.disconnect()

                profile.status = VPNStatus.CONNECTING
                self._add_log("Connecting", f"Establishing {profile.protocol.value} tunnel to {profile.server_str}")

                # Simulate connection
                profile.status = VPNStatus.CONNECTED
                profile.last_connected = time.time()
                self._connected_profile = profile

                self._stats = VPNStats(connection_time=time.time())
                self._add_log("Connected", f"Tunnel established to {profile.server_str}")
                self._notify("connect", profile)
                return True
        return False

    def disconnect(self) -> bool:
        """Disconnect the current VPN."""
        if self._connected_profile:
            self._connected_profile.status = VPNStatus.DISCONNECTED
            self._add_log("Disconnected", f"Session duration: {self._stats.duration_str}")
            self._connected_profile = None
            self._stats = VPNStats()
            self._notify("disconnect")
            return True
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected_profile is not None

    @property
    def connected_profile(self) -> Optional[VPNProfile]:
        return self._connected_profile

    def update_stats(self) -> None:
        """Simulate stats update."""
        if self.is_connected:
            self._stats.bytes_sent += random.randint(1000, 50000)
            self._stats.bytes_received += random.randint(5000, 200000)
            self._stats.duration_seconds = time.time() - self._stats.connection_time

    # ── Profile Management ────────────────────────────────────────────

    def create_profile(self, name: str, protocol: VPNProtocol = VPNProtocol.WIREGUARD) -> VPNProfile:
        profile = VPNProfile(name=name, protocol=protocol)
        self._profiles.append(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        for i, p in enumerate(self._profiles):
            if p.profile_id == profile_id:
                if p == self._connected_profile:
                    self.disconnect()
                self._profiles.pop(i)
                return True
        return False

    def toggle_auto_connect(self, profile_id: str) -> bool:
        for p in self._profiles:
            if p.profile_id == profile_id:
                p.auto_connect = not p.auto_connect
                return p.auto_connect
        return False

    def toggle_kill_switch(self, profile_id: str) -> bool:
        for p in self._profiles:
            if p.profile_id == profile_id:
                p.kill_switch = not p.kill_switch
                return p.kill_switch
        return False

    @property
    def profiles(self) -> List[VPNProfile]:
        return list(self._profiles)

    @property
    def servers(self) -> List[VPNServer]:
        return list(self._servers)

    @property
    def stats(self) -> VPNStats:
        return self._stats

    @property
    def logs(self) -> List[ConnectionLog]:
        return list(self._logs)

    def _add_log(self, event: str, detail: str, is_error: bool = False) -> None:
        self._logs.insert(0, ConnectionLog(event=event, detail=detail, is_error=is_error))
        if len(self._logs) > 100:
            self._logs.pop()

    # ── Rendering ─────────────────────────────────────────────────────

    def render_profiles(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🔒 VPN Manager")
        if self.is_connected:
            lines.append(f" 🟢 Connected to {self._connected_profile.server_str}")
        else:
            lines.append(" ⚫ Disconnected")
        lines.append("─" * width)

        for i, profile in enumerate(self._profiles):
            marker = "▸" if i == self._selected_index else " "
            connected = " ←" if profile == self._connected_profile else ""
            lines.append(f"{marker} {profile.status_icon} {profile.name}{connected}")
            lines.append(f"   {profile.protocol.value} · {profile.server_str}")
            lines.append(f"   Auto: {'✅' if profile.auto_connect else '❌'}  "
                         f"Kill: {'✅' if profile.kill_switch else '❌'}  "
                         f"Split: {'✅' if profile.split_tunnel else '❌'}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Connect  D:Disconnect  S:Settings")
        return lines

    def render_servers(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🌐 VPN Servers")
        lines.append("─" * width)

        for i, server in enumerate(self._servers):
            marker = "▸" if i == self._selected_index else " "
            premium = " ⭐" if server.is_premium else ""
            lines.append(f"{marker} {server.display}{premium}")
            lines.append(f"   Latency: {server.latency_ms:.0f}ms  Load: {server.load_bar} {server.load_percent:.0f}%")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Connect  P:Ping  Esc:Back")
        return lines

    def render_stats(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📊 Connection Statistics")
        lines.append("─" * width)

        if self.is_connected:
            s = self._stats
            lines.append(f" Status:     🟢 Connected")
            lines.append(f" Server:     {self._connected_profile.server_str}")
            lines.append(f" Duration:   {s.duration_str}")
            lines.append(f" Sent:       {s.bytes_sent_str}")
            lines.append(f" Received:   {s.bytes_received_str}")
            lines.append(f" Reconnects: {s.reconnections}")
        else:
            lines.append(" Not connected")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_logs(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📋 Connection Logs")
        lines.append(f" {len(self._logs)} entries")
        lines.append("─" * width)

        for log in self._logs[:15]:
            lines.append(f" {log.icon} [{log.time_str}] {log.event}")
            if log.detail:
                lines.append(f"   {log.detail}")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "profiles": self.render_profiles,
            "servers": self.render_servers,
            "stats": self.render_stats,
            "logs": self.render_logs,
        }
        renderer = renderers.get(self._view_mode, self.render_profiles)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "servers":
            return self._handle_servers_key(key)
        elif self._view_mode == "stats":
            return self._handle_stats_key(key)
        elif self._view_mode == "logs":
            return self._handle_logs_key(key)
        return self._handle_profiles_key(key)

    def _handle_profiles_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._profiles) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            if self.is_connected:
                self.disconnect()
            else:
                self.connect()
            return "toggle_connect"
        elif key == "d":
            self.disconnect()
            return "disconnect"
        elif key == "s":
            self._view_mode = "servers"
            self._selected_index = 0
            return "servers"
        elif key == "t":
            self._view_mode = "stats"
            return "stats"
        elif key == "l":
            self._view_mode = "logs"
            return "logs"
        return None

    def _handle_servers_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "profiles"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._servers) - 1, self._selected_index + 1)
            return "select_down"
        return None

    def _handle_stats_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "profiles"
            return "back"
        return None

    def _handle_logs_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "profiles"
            return "back"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_status_change(self, cb: Callable) -> None:
        self._on_status_change.append(cb)

    def _notify(self, event: str, *args) -> None:
        for cb in self._on_status_change:
            try:
                cb(event, *args)
            except Exception:
                pass
