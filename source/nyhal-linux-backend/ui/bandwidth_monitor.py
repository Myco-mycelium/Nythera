"""
Nyrqis OS - Network Bandwidth Monitor
Per-application bandwidth tracking, historical graphs, and traffic analysis.

Features:
- Per-app bandwidth monitoring (upload/download)
- Real-time interface statistics
- Historical bandwidth graphs (1min, 5min, 15min, 1hr)
- Top talkers ranking
- Protocol breakdown (TCP/UDP/ICMP)
- Connection tracking with remote endpoint info
- Traffic alerts and thresholds
- Data usage tracking (daily, weekly, monthly)
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class TrafficDirection(Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    BIDIRECTIONAL = "bidirectional"


class AppCategory(Enum):
    BROWSER = "browser"
    STREAMING = "streaming"
    GAMING = "gaming"
    MESSAGING = "messaging"
    DEVELOPMENT = "development"
    SYSTEM = "system"
    CLOUD = "cloud"
    VPN = "vpn"
    OTHER = "other"


class AlertType(Enum):
    HIGH_BANDWIDTH = "high_bandwidth"
    UNUSUAL_TRAFFIC = "unusual_traffic"
    DATA_LIMIT = "data_limit"
    CONNECTION_spike = "connection_spike"


CATEGORY_ICONS = {
    AppCategory.BROWSER: "🌐", AppCategory.STREAMING: "📺",
    AppCategory.GAMING: "🎮", AppCategory.MESSAGING: "💬",
    AppCategory.DEVELOPMENT: "💻", AppCategory.SYSTEM: "⚙️",
    AppCategory.CLOUD: "☁️", AppCategory.VPN: "🔒",
    AppCategory.OTHER: "📦",
}

ALERT_ICONS = {
    AlertType.HIGH_BANDWIDTH: "⚡", AlertType.UNUSUAL_TRAFFIC: "⚠️",
    AlertType.DATA_LIMIT: "📊", AlertType.CONNECTION_spike: "📈",
}


@dataclass
class BandwidthSample:
    timestamp: float = 0.0
    rx_bytes: float = 0.0
    tx_bytes: float = 0.0
    rx_packets: int = 0
    tx_packets: int = 0

    @property
    def rx_rate_bps(self) -> float:
        """Bytes per second (assuming 1-second interval)."""
        return self.rx_bytes

    @property
    def tx_rate_bps(self) -> float:
        return self.tx_bytes

    @property
    def total_bytes(self) -> float:
        return self.rx_bytes + self.tx_bytes

    @property
    def rx_str(self) -> str:
        return self._fmt(self.rx_bytes) + "/s"

    @property
    def tx_str(self) -> str:
        return self._fmt(self.tx_bytes) + "/s"

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @staticmethod
    def _fmt(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class InterfaceStats:
    name: str = ""
    rx_total: float = 0.0
    tx_total: float = 0.0
    rx_rate: float = 0.0  # bytes/s
    tx_rate: float = 0.0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_dropped: int = 0
    tx_dropped: int = 0
    speed_mbps: int = 0
    mtu: int = 1500
    is_up: bool = True
    mac: str = ""

    @property
    def rx_rate_str(self) -> str:
        return self._fmt(self.rx_rate) + "/s"

    @property
    def tx_rate_str(self) -> str:
        return self._fmt(self.tx_rate) + "/s"

    @property
    def rx_total_str(self) -> str:
        return self._fmt(self.rx_total)

    @property
    def tx_total_str(self) -> str:
        return self._fmt(self.tx_total)

    @property
    def utilization_percent(self) -> float:
        if self.speed_mbps == 0:
            return 0.0
        total_bps = (self.rx_rate + self.tx_rate) * 8
        return (total_bps / (self.speed_mbps * 1_000_000)) * 100

    @property
    def utilization_bar(self) -> str:
        pct = min(100, self.utilization_percent)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def error_rate(self) -> float:
        total = self.rx_packets + self.tx_packets
        if total == 0:
            return 0.0
        return (self.rx_errors + self.tx_errors) / total * 100

    @property
    def state_icon(self) -> str:
        return "🟢" if self.is_up else "🔴"

    @staticmethod
    def _fmt(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class AppBandwidth:
    name: str = ""
    pid: int = 0
    category: AppCategory = AppCategory.OTHER
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    rx_total: float = 0.0
    tx_total: float = 0.0
    connections: int = 0
    remote_host: str = ""
    remote_port: int = 0
    protocol: str = "TCP"

    @property
    def category_icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "📦")

    @property
    def rx_str(self) -> str:
        return self._fmt(self.rx_rate) + "/s"

    @property
    def tx_str(self) -> str:
        return self._fmt(self.tx_rate) + "/s"

    @property
    def total_str(self) -> str:
        return self._fmt(self.rx_total + self.tx_total)

    @property
    def endpoint(self) -> str:
        if self.remote_host:
            return f"{self.remote_host}:{self.remote_port}" if self.remote_port else self.remote_host
        return "N/A"

    @property
    def bar(self) -> str:
        total = self.rx_rate + self.tx_rate
        # Normalize: max expected ~50MB/s
        pct = min(100, (total / (50 * 1024 * 1024)) * 100)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @staticmethod
    def _fmt(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class Connection:
    local_addr: str = ""
    local_port: int = 0
    remote_addr: str = ""
    remote_port: int = 0
    state: str = "ESTABLISHED"
    protocol: str = "TCP"
    process: str = ""
    pid: int = 0
    rx_bytes: float = 0.0
    tx_bytes: float = 0.0
    established: float = 0.0

    @property
    def local_display(self) -> str:
        return f"{self.local_addr}:{self.local_port}"

    @property
    def remote_display(self) -> str:
        return f"{self.remote_addr}:{self.remote_port}"

    @property
    def state_icon(self) -> str:
        icons = {
            "ESTABLISHED": "🟢", "LISTEN": "🔵", "TIME_WAIT": "🟡",
            "CLOSE_WAIT": "🟠", "SYN_SENT": "⚪", "SYN_RECV": "⚪",
            "FIN_WAIT1": "🟡", "FIN_WAIT2": "🟡", "CLOSED": "⚫",
        }
        return icons.get(self.state, "❓")

    @property
    def duration_str(self) -> str:
        if self.established == 0:
            return "N/A"
        delta = time.time() - self.established
        if delta < 60:
            return f"{delta:.0f}s"
        elif delta < 3600:
            return f"{delta / 60:.0f}m"
        return f"{delta / 3600:.1f}h"

    @property
    def traffic_str(self) -> str:
        up = self._fmt(self.tx_bytes)
        down = self._fmt(self.rx_bytes)
        return f"↓{down} ↑{up}"

    @staticmethod
    def _fmt(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class DataUsage:
    period: str = ""  # "today", "this_week", "this_month"
    rx_bytes: float = 0.0
    tx_bytes: float = 0.0
    limit_bytes: float = 0.0

    @property
    def total_str(self) -> str:
        return BandwidthSample._fmt(self.rx_bytes + self.tx_bytes)

    @property
    def rx_str(self) -> str:
        return BandwidthSample._fmt(self.rx_bytes)

    @property
    def tx_str(self) -> str:
        return BandwidthSample._fmt(self.tx_bytes)

    @property
    def usage_percent(self) -> float:
        if self.limit_bytes <= 0:
            return 0.0
        return ((self.rx_bytes + self.tx_bytes) / self.limit_bytes) * 100

    @property
    def bar(self) -> str:
        pct = min(100, self.usage_percent)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class TrafficAlert:
    alert_type: AlertType = AlertType.HIGH_BANDWIDTH
    message: str = ""
    threshold: str = ""
    triggered_at: float = 0.0
    app_name: str = ""
    acknowledged: bool = False

    @property
    def icon(self) -> str:
        return ALERT_ICONS.get(self.alert_type, "❓")

    @property
    def time_str(self) -> str:
        if self.triggered_at == 0:
            return "N/A"
        return time.strftime("%H:%M:%S", time.localtime(self.triggered_at))


@dataclass
class ProtocolStats:
    name: str = ""
    rx_bytes: float = 0.0
    tx_bytes: float = 0.0
    connections: int = 0

    @property
    def total_bytes(self) -> float:
        return self.rx_bytes + self.tx_bytes

    @property
    def total_str(self) -> str:
        return BandwidthSample._fmt(self.total_bytes)

    @property
    def bar(self) -> str:
        max_bytes = 100 * 1024 ** 3  # 100GB baseline
        pct = min(100, (self.total_bytes / max_bytes) * 100)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)


class BandwidthMonitor:
    def __init__(self):
        self.interfaces: List[InterfaceStats] = []
        self.app_bandwidth: List[AppBandwidth] = []
        self.connections: List[Connection] = []
        self.protocol_stats: List[ProtocolStats] = []
        self.data_usage: List[DataUsage] = []
        self.alerts: List[TrafficAlert] = []
        self.history: Dict[str, List[BandwidthSample]] = {}
        self._selected_app: int = 0
        self._selected_connection: int = 0
        self._view_mode: str = "apps"
        self._time_range: str = "1min"
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.interfaces = [
            InterfaceStats(
                name="eth0", rx_total=15.2 * 1024 ** 3, tx_total=3.8 * 1024 ** 3,
                rx_rate=12.5 * 1024 ** 2, tx_rate=2.1 * 1024 ** 2,
                rx_packets=12500000, tx_packets=8200000,
                rx_errors=12, tx_errors=3, speed_mbps=2500,
                is_up=True, mac="AA:BB:CC:DD:EE:01",
            ),
            InterfaceStats(
                name="wlan0", rx_total=8.5 * 1024 ** 3, tx_total=1.2 * 1024 ** 3,
                rx_rate=5.3 * 1024 ** 2, tx_rate=0.8 * 1024 ** 2,
                rx_packets=5400000, tx_packets=2100000,
                speed_mbps=300, is_up=True, mac="AA:BB:CC:DD:EE:02",
            ),
            InterfaceStats(
                name="lo", rx_total=500 * 1024, tx_total=500 * 1024,
                rx_rate=1024, tx_rate=1024, speed_mbps=10000,
                is_up=True, mac="00:00:00:00:00:00",
            ),
        ]

        self.app_bandwidth = [
            AppBandwidth("Firefox", 4521, AppCategory.BROWSER,
                         rx_rate=8.5 * 1024 ** 2, tx_rate=0.3 * 1024 ** 2,
                         rx_total=4.2 * 1024 ** 3, tx_total=150 * 1024 ** 3,
                         connections=24, remote_host="151.101.1.140",
                         remote_port=443, protocol="TCP"),
            AppBandwidth("Spotify", 7823, AppCategory.STREAMING,
                         rx_rate=2.1 * 1024 ** 2, tx_rate=0.01 * 1024 ** 2,
                         rx_total=1.8 * 1024 ** 3, tx_total=2 * 1024 ** 3,
                         connections=4, remote_host="35.186.224.25",
                         remote_port=443, protocol="TCP"),
            AppBandwidth("Discord", 5634, AppCategory.MESSAGING,
                         rx_rate=0.5 * 1024 ** 2, tx_rate=0.1 * 1024 ** 2,
                         rx_total=320 * 1024 ** 3, tx_total=180 * 1024 ** 3,
                         connections=8, remote_host="162.159.136.234",
                         remote_port=443, protocol="TCP"),
            AppBandwidth("Steam", 8901, AppCategory.GAMING,
                         rx_rate=45.2 * 1024 ** 2, tx_rate=0.2 * 1024 ** 2,
                         rx_total=2.1 * 1024 ** 3, tx_total=50 * 1024 ** 3,
                         connections=6, remote_host="155.133.226.18",
                         remote_port=27036, protocol="UDP"),
            AppBandwidth("code-server", 1234, AppCategory.DEVELOPMENT,
                         rx_rate=0.1 * 1024 ** 2, tx_rate=0.05 * 1024 ** 2,
                         rx_total=50 * 1024 ** 3, tx_total=25 * 1024 ** 3,
                         connections=2, remote_host="127.0.0.1",
                         remote_port=8080, protocol="TCP"),
            AppBandwidth("nyrqis-compositor", 2, AppCategory.SYSTEM,
                         rx_rate=100 * 1024, tx_rate=50 * 1024,
                         rx_total=20 * 1024 ** 3, tx_total=10 * 1024 ** 3,
                         connections=1, remote_host="127.0.0.1",
                         remote_port=6200, protocol="TCP"),
            AppBandwidth("WireGuard", 1100, AppCategory.VPN,
                         rx_rate=3.2 * 1024 ** 2, tx_rate=0.8 * 1024 ** 2,
                         rx_total=900 * 1024 ** 3, tx_total=200 * 1024 ** 3,
                         connections=1, remote_host="10.100.0.1",
                         remote_port=51820, protocol="UDP"),
            AppBandwidth("Syncthing", 3456, AppCategory.CLOUD,
                         rx_rate=1.5 * 1024 ** 2, tx_rate=0.6 * 1024 ** 2,
                         rx_total=450 * 1024 ** 3, tx_total=320 * 1024 ** 3,
                         connections=5, remote_host="192.168.1.200",
                         remote_port=22000, protocol="TCP"),
            AppBandwidth("systemd-resolved", 1, AppCategory.SYSTEM,
                         rx_rate=5 * 1024, tx_rate=5 * 1024,
                         rx_total=200 * 1024 ** 3, tx_total=50 * 1024 ** 3,
                         connections=12, remote_host="1.1.1.1",
                         remote_port=53, protocol="UDP"),
            AppBandwidth("Mullvad VPN", 2200, AppCategory.VPN,
                         rx_rate=0, tx_rate=0,
                         rx_total=0, tx_total=0,
                         connections=0, remote_host="",
                         remote_port=0, protocol="UDP"),
        ]

        self.connections = [
            Connection("192.168.1.100", 443, "151.101.1.140", 443,
                       "ESTABLISHED", "TCP", "Firefox", 4521,
                       4.2 * 1024 ** 3, 150 * 1024 ** 3, now - 3600),
            Connection("192.168.1.100", 443, "35.186.224.25", 443,
                       "ESTABLISHED", "TCP", "Spotify", 7823,
                       1.8 * 1024 ** 3, 2 * 1024 ** 3, now - 7200),
            Connection("192.168.1.100", 443, "162.159.136.234", 443,
                       "ESTABLISHED", "TCP", "Discord", 5634,
                       320 * 1024 ** 3, 180 * 1024 ** 3, now - 1800),
            Connection("192.168.1.100", 27036, "155.133.226.18", 27036,
                       "ESTABLISHED", "UDP", "Steam", 8901,
                       2.1 * 1024 ** 3, 50 * 1024 ** 3, now - 600),
            Connection("127.0.0.1", 8080, "127.0.0.1", 54321,
                       "ESTABLISHED", "TCP", "code-server", 1234,
                       50 * 1024 ** 3, 25 * 1024 ** 3, now - 10800),
            Connection("0.0.0.0", 22000, "192.168.1.200", 52341,
                       "ESTABLISHED", "TCP", "Syncthing", 3456,
                       450 * 1024 ** 3, 320 * 1024 ** 3, now - 5400),
            Connection("10.100.0.5", 51820, "10.100.0.1", 51820,
                       "ESTABLISHED", "UDP", "WireGuard", 1100,
                       900 * 1024 ** 3, 200 * 1024 ** 3, now - 7200),
            Connection("192.168.1.100", 53, "1.1.1.1", 53,
                       "TIME_WAIT", "UDP", "systemd-resolved", 1,
                       5 * 1024, 5 * 1024, now - 30),
            Connection("192.168.1.100", 53, "8.8.8.8", 53,
                       "TIME_WAIT", "UDP", "systemd-resolved", 1,
                       3 * 1024, 3 * 1024, now - 60),
            Connection("192.168.1.100", 443, "140.82.121.4", 443,
                       "CLOSE_WAIT", "TCP", "Firefox", 4521,
                       120 * 1024, 80 * 1024, now - 900),
        ]

        self.protocol_stats = [
            ProtocolStats("TCP", 8.5 * 1024 ** 3, 1.2 * 1024 ** 3, 18),
            ProtocolStats("UDP", 2.1 * 1024 ** 3, 0.3 * 1024 ** 3, 8),
            ProtocolStats("ICMP", 5 * 1024, 5 * 1024, 0),
            ProtocolStats("QUIC", 1.8 * 1024 ** 3, 0.2 * 1024 ** 3, 4),
        ]

        self.data_usage = [
            DataUsage("Today", 15.2 * 1024 ** 3, 2.8 * 1024 ** 3, 50 * 1024 ** 3),
            DataUsage("This Week", 85.5 * 1024 ** 3, 12.3 * 1024 ** 3, 350 * 1024 ** 3),
            DataUsage("This Month", 342.0 * 1024 ** 3, 48.5 * 1024 ** 3, 1000 * 1024 ** 3),
        ]

        self.alerts = [
            TrafficAlert(AlertType.HIGH_BANDWIDTH, "Steam downloading at 45 MB/s",
                         "40 MB/s", now - 300, "Steam", True),
            TrafficAlert(AlertType.DATA_LIMIT, "Monthly usage at 39%",
                         "1 TB", now - 3600, "", True),
            TrafficAlert(AlertType.UNUSUAL_TRAFFIC, "Unknown UDP traffic on port 4444",
                         "Pattern match", now - 7200, "", False),
            TrafficAlert(AlertType.CONNECTION_spike, "Firefox has 24 connections",
                         "20 connections", now - 1800, "Firefox", True),
        ]

        # Generate history for each interface
        for iface in ["eth0", "wlan0"]:
            samples = []
            base_rx = 10 * 1024 ** 2 if iface == "eth0" else 5 * 1024 ** 2
            for i in range(60):
                samples.append(BandwidthSample(
                    timestamp=now - (60 - i) * 60,
                    rx_bytes=base_rx + random.uniform(-2, 5) * 1024 ** 2,
                    tx_bytes=base_rx * 0.2 + random.uniform(-1, 2) * 1024 ** 2,
                    rx_packets=random.randint(5000, 15000),
                    tx_packets=random.randint(2000, 8000),
                ))
            self.history[iface] = samples

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_app(self) -> Optional[AppBandwidth]:
        if 0 <= self._selected_app < len(self.app_bandwidth):
            return self.app_bandwidth[self._selected_app]
        return None

    def select_app(self, idx: int):
        if 0 <= idx < len(self.app_bandwidth):
            self._selected_app = idx

    def select_connection(self, idx: int):
        if 0 <= idx < len(self.connections):
            self._selected_connection = idx

    def set_view(self, view: str):
        self._view_mode = view

    def set_time_range(self, range_str: str):
        self._time_range = range_str

    def select_down(self):
        if self._view_mode == "apps":
            self._selected_app = min(self._selected_app + 1, len(self.app_bandwidth) - 1)
        elif self._view_mode == "connections":
            self._selected_connection = min(self._selected_connection + 1, len(self.connections) - 1)

    def select_up(self):
        if self._view_mode == "apps":
            self._selected_app = max(self._selected_app - 1, 0)
        elif self._view_mode == "connections":
            self._selected_connection = max(self._selected_connection - 1, 0)

    # ─── Queries ───────────────────────────────────────────────────────

    def get_top_uploaders(self, limit: int = 5) -> List[AppBandwidth]:
        return sorted(self.app_bandwidth, key=lambda a: a.tx_rate, reverse=True)[:limit]

    def get_top_downloaders(self, limit: int = 5) -> List[AppBandwidth]:
        return sorted(self.app_bandwidth, key=lambda a: a.rx_rate, reverse=True)[:limit]

    def get_active_apps(self) -> List[AppBandwidth]:
        return [a for a in self.app_bandwidth if a.rx_rate > 0 or a.tx_rate > 0]

    def get_active_connections(self) -> List[Connection]:
        return [c for c in self.connections if c.state == "ESTABLISHED"]

    def get_total_rx_rate(self) -> float:
        return sum(a.rx_rate for a in self.app_bandwidth)

    def get_total_tx_rate(self) -> float:
        return sum(a.tx_rate for a in self.app_bandwidth)

    def search_apps(self, query: str) -> List[AppBandwidth]:
        q = query.lower()
        return [a for a in self.app_bandwidth if q in a.name.lower()]

    def search_connections(self, query: str) -> List[Connection]:
        q = query.lower()
        return [c for c in self.connections
                if q in c.process.lower() or q in c.remote_addr.lower()]

    def get_stats(self) -> Dict:
        return {
            "interfaces": len(self.interfaces),
            "active_apps": len(self.get_active_apps()),
            "total_connections": len(self.connections),
            "active_connections": len(self.get_active_connections()),
            "total_rx_rate": self.get_total_rx_rate(),
            "total_tx_rate": self.get_total_tx_rate(),
            "protocols": len(self.protocol_stats),
            "alerts": len(self.alerts),
            "unack_alerts": sum(1 for a in self.alerts if not a.acknowledged),
        }
