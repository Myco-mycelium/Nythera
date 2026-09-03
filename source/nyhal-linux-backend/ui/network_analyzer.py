"""
Nyrqis Network Analyzer — network traffic analysis application.

Features:
- Real-time bandwidth monitoring with sparklines
- Protocol breakdown (TCP, UDP, ICMP, DNS, HTTP, HTTPS)
- Connection tracking with source/destination
- Packet capture with filtering
- Network interface statistics
- Latency/ping monitoring
- Bandwidth history and peak tracking
- Top talkers identification
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class Protocol(Enum):
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    DNS = "DNS"
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    SSH = "SSH"
    FTP = "FTP"
    SMTP = "SMTP"
    QUIC = "QUIC"
    OTHER = "Other"


class ConnectionState(Enum):
    ESTABLISHED = "ESTABLISHED"
    LISTENING = "LISTENING"
    TIME_WAIT = "TIME_WAIT"
    CLOSE_WAIT = "CLOSE_WAIT"
    SYN_SENT = "SYN_SENT"
    SYN_RECV = "SYN_RECV"
    CLOSED = "CLOSED"


class InterfaceStatus(Enum):
    UP = "up"
    DOWN = "down"
    TESTING = "testing"


PROTO_ICONS = {
    Protocol.TCP: "🔗",
    Protocol.UDP: "📡",
    Protocol.ICMP: "🔔",
    Protocol.DNS: "🌍",
    Protocol.HTTP: "🌐",
    Protocol.HTTPS: "🔒",
    Protocol.SSH: "🔐",
    Protocol.FTP: "📁",
    Protocol.SMTP: "📧",
    Protocol.QUIC: "⚡",
    Protocol.OTHER: "❓",
}

PROTO_COLORS = {
    Protocol.TCP: "#7aa2f7",
    Protocol.UDP: "#9ece6a",
    Protocol.HTTP: "#e0af68",
    Protocol.HTTPS: "#bb9af7",
    Protocol.DNS: "#7dcfff",
    Protocol.SSH: "#f7768e",
}


@dataclass
class NetworkInterface:
    """A network interface."""
    name: str
    mac_address: str = ""
    ipv4: str = ""
    ipv6: str = ""
    status: InterfaceStatus = InterfaceStatus.UP
    speed_mbps: int = 1000
    # Stats
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_dropped: int = 0
    tx_dropped: int = 0
    # Monitoring
    rx_rate_bps: float = 0.0  # current receive rate
    tx_rate_bps: float = 0.0  # current transmit rate
    rx_history: List[float] = field(default_factory=list)  # 60 data points
    tx_history: List[float] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        return "🟢" if self.status == InterfaceStatus.UP else "🔴"

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} ({self.ipv4 or 'no IP'})"

    @property
    def rx_rate_str(self) -> str:
        bps = self.rx_rate_bps
        if bps >= 1073741824:
            return f"{bps / 1073741824:.1f} Gbps"
        elif bps >= 1048576:
            return f"{bps / 1048576:.1f} Mbps"
        elif bps >= 1024:
            return f"{bps / 1024:.1f} Kbps"
        return f"{bps:.0f} bps"

    @property
    def tx_rate_str(self) -> str:
        bps = self.tx_rate_bps
        if bps >= 1073741824:
            return f"{bps / 1073741824:.1f} Gbps"
        elif bps >= 1048576:
            return f"{bps / 1048576:.1f} Mbps"
        elif bps >= 1024:
            return f"{bps / 1024:.1f} Kbps"
        return f"{bps:.0f} bps"

    @property
    def total_rx_str(self) -> str:
        return self._format_bytes(self.rx_bytes)

    @property
    def total_tx_str(self) -> str:
        return self._format_bytes(self.tx_bytes)

    @staticmethod
    def _format_bytes(b: int) -> str:
        if b >= 1073741824:
            return f"{b / 1073741824:.2f} GB"
        elif b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        elif b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"

    def sparkline_rx(self, width: int = 30) -> str:
        return self._sparkline(self.rx_history, width)

    def sparkline_tx(self, width: int = 30) -> str:
        return self._sparkline(self.tx_history, width)

    @staticmethod
    def _sparkline(data: List[float], width: int) -> str:
        if not data:
            return "░" * width
        chars = "▁▂▃▄▅▆▇█"
        max_val = max(data) if max(data) > 0 else 1
        result = ""
        step = max(1, len(data) // width)
        for i in range(0, min(len(data), width * step), step):
            val = data[i] if i < len(data) else 0
            idx = int(val / max_val * (len(chars) - 1))
            result += chars[min(idx, len(chars) - 1)]
        return result[:width]


@dataclass
class Connection:
    """A network connection."""
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    protocol: Protocol = Protocol.TCP
    state: ConnectionState = ConnectionState.ESTABLISHED
    process: str = ""
    pid: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    duration: float = 0.0
    created: float = field(default_factory=time.time)

    @property
    def local_str(self) -> str:
        return f"{self.local_addr}:{self.local_port}"

    @property
    def remote_str(self) -> str:
        return f"{self.remote_addr}:{self.remote_port}"

    @property
    def display(self) -> str:
        proto = PROTO_ICONS.get(self.protocol, "❓")
        return f"{proto} {self.local_str} → {self.remote_str} [{self.state.value}]"

    @property
    def duration_str(self) -> str:
        if self.duration < 60:
            return f"{self.duration:.0f}s"
        elif self.duration < 3600:
            return f"{self.duration / 60:.0f}m"
        return f"{self.duration / 3600:.1f}h"

    @property
    def traffic_str(self) -> str:
        total = self.rx_bytes + self.tx_bytes
        if total >= 1048576:
            return f"{total / 1048576:.1f} MB"
        elif total >= 1024:
            return f"{total / 1024:.1f} KB"
        return f"{total} B"


@dataclass
class CapturedPacket:
    """A captured network packet."""
    timestamp: float
    source: str
    destination: str
    protocol: Protocol
    size: int
    info: str = ""
    packet_id: int = 0

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S.%f")[:-3]

    @property
    def size_str(self) -> str:
        if self.size >= 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size} B"

    @property
    def display(self) -> str:
        proto = PROTO_ICONS.get(self.protocol, "❓")
        return f"{self.time_str} {proto} {self.source} → {self.destination} {self.size_str} {self.info}"


@dataclass
class PingResult:
    """A ping measurement."""
    host: str
    latency_ms: float
    packet_loss: float
    timestamp: float = field(default_factory=time.time)
    ttl: int = 64

    @property
    def latency_str(self) -> str:
        return f"{self.latency_ms:.1f}ms"

    @property
    def quality(self) -> str:
        if self.latency_ms < 20:
            return "Excellent"
        elif self.latency_ms < 50:
            return "Good"
        elif self.latency_ms < 100:
            return "Fair"
        return "Poor"


# ─── Network Analyzer ────────────────────────────────────────────────────


class NetworkAnalyzer:
    """
    Network traffic analyzer for Nyrqis OS.
    """

    def __init__(self):
        self._interfaces: List[NetworkInterface] = []
        self._connections: List[Connection] = []
        self._packets: List[CapturedPacket] = []
        self._ping_results: List[PingResult] = []
        self._selected_index: int = 0
        self._view_mode: str = "overview"  # overview, interfaces, connections, capture, ping
        self._capture_active: bool = False
        self._capture_filter: str = ""
        self._capture_limit: int = 500
        self._packet_id_counter: int = 0

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()

        # Interfaces
        self._interfaces = [
            NetworkInterface(
                "eth0", "02:42:ac:11:00:02", "192.168.1.100", "fe80::1",
                InterfaceStatus.UP, 1000,
                rx_bytes=15_728_640_000, tx_bytes=8_388_608_000,
                rx_packets=12_450_000, tx_packets=8_230_000,
                rx_errors=12, tx_errors=3, rx_dropped=45, tx_dropped=2,
                rx_rate_bps=125_000_000, tx_rate_bps=45_000_000,
            ),
            NetworkInterface(
                "wlan0", "02:42:ac:11:00:03", "192.168.1.105", "fe80::2",
                InterfaceStatus.UP, 866,
                rx_bytes=5_242_880_000, tx_bytes=2_097_152_000,
                rx_packets=4_120_000, tx_packets=2_890_000,
                rx_errors=2, tx_errors=0, rx_dropped=8, tx_dropped=1,
                rx_rate_bps=35_000_000, tx_rate_bps=12_000_000,
            ),
            NetworkInterface(
                "docker0", "02:42:ac:11:00:01", "172.17.0.1", "",
                InterfaceStatus.UP, 10000,
                rx_bytes=2_147_483_648, tx_bytes=1_073_741_824,
                rx_packets=3_200_000, tx_packets=2_100_000,
                rx_rate_bps=8_000_000, tx_rate_bps=3_500_000,
            ),
            NetworkInterface(
                "lo", "00:00:00:00:00:00", "127.0.0.1", "::1",
                InterfaceStatus.UP, 10000,
                rx_bytes=524_288, tx_bytes=524_288,
                rx_rate_bps=0, tx_rate_bps=0,
            ),
        ]

        # Generate sparkline history
        random.seed(42)
        for iface in self._interfaces[:3]:
            base_rx = iface.rx_rate_bps
            base_tx = iface.tx_rate_bps
            for i in range(60):
                iface.rx_history.append(base_rx * random.uniform(0.3, 1.5))
                iface.tx_history.append(base_tx * random.uniform(0.3, 1.5))

        # Connections
        self._connections = [
            Connection("192.168.1.100", 22, "10.0.0.5", 54321, Protocol.SSH,
                       ConnectionState.ESTABLISHED, "sshd", 1234, 1_048_576, 524_288, 3600),
            Connection("192.168.1.100", 443, "142.250.80.46", 443, Protocol.HTTPS,
                       ConnectionState.ESTABLISHED, "firefox", 5678, 15_728_640, 2_097_152, 1200),
            Connection("192.168.1.100", 8080, "192.168.1.50", 22, Protocol.TCP,
                       ConnectionState.ESTABLISHED, "code", 9012, 1_048_576, 262_144, 7200),
            Connection("192.168.1.100", 53, "8.8.8.8", 53, Protocol.DNS,
                       ConnectionState.ESTABLISHED, "systemd-resolve", 456, 1_024, 512, 60),
            Connection("192.168.1.100", 0, "0.0.0.0", 80, Protocol.HTTP,
                       ConnectionState.LISTENING, "nginx", 789, 0, 0, 0),
            Connection("192.168.1.100", 0, "0.0.0.0", 443, Protocol.HTTPS,
                       ConnectionState.LISTENING, "nginx", 789, 0, 0, 0),
            Connection("192.168.1.100", 3306, "172.17.0.2", 54321, Protocol.TCP,
                       ConnectionState.ESTABLISHED, "python3", 3456, 4_194_304, 1_048_576, 1800),
            Connection("192.168.1.100", 6379, "172.17.0.3", 43210, Protocol.TCP,
                       ConnectionState.ESTABLISHED, "redis-server", 2345, 2_097_152, 524_288, 900),
            Connection("192.168.1.100", 51820, "203.0.113.1", 51820, Protocol.UDP,
                       ConnectionState.ESTABLISHED, "wireguard", 1111, 10_485_760, 5_242_880, 86400),
            Connection("192.168.1.100", 8443, "93.184.216.34", 443, Protocol.QUIC,
                       ConnectionState.ESTABLISHED, "firefox", 5678, 8_388_608, 2_097_152, 300),
        ]

        # Captured packets
        self._generate_sample_packets(100)

        # Ping results
        ping_hosts = [
            ("8.8.8.8", 12.5, 0.0),
            ("1.1.1.1", 8.3, 0.0),
            ("192.168.1.1", 1.2, 0.0),
            ("github.com", 25.8, 0.0),
            ("google.com", 15.2, 0.0),
        ]
        for host, latency, loss in ping_hosts:
            self._ping_results.append(PingResult(host, latency, loss))

    def _generate_sample_packets(self, count: int) -> None:
        now = time.time()
        proto_weights = [
            (Protocol.HTTPS, 35), (Protocol.TCP, 25), (Protocol.UDP, 15),
            (Protocol.DNS, 10), (Protocol.HTTP, 8), (Protocol.SSH, 5),
            (Protocol.ICMP, 2),
        ]
        protocols = []
        weights = []
        for p, w in proto_weights:
            protocols.append(p)
            weights.append(w)

        for i in range(count):
            proto = random.choices(protocols, weights=weights)[0]
            size = random.randint(40, 1500)
            src = f"192.168.1.{random.randint(100, 110)}"
            dst = f"{random.randint(1, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
            if proto == Protocol.DNS:
                info = random.choice(["A query: github.com", "AAAA query: google.com", "Response: 142.250.80.46"])
            elif proto in (Protocol.HTTP, Protocol.HTTPS):
                info = random.choice(["GET /api/v1/data", "POST /login", "GET /index.html", "200 OK"])
            elif proto == Protocol.SSH:
                info = "Encrypted transfer"
            else:
                info = f"seq={random.randint(1, 10000)}"

            packet = CapturedPacket(
                timestamp=now - (count - i) * 0.1,
                source=src, destination=dst,
                protocol=proto, size=size, info=info,
                packet_id=i + 1,
            )
            self._packets.append(packet)

    # ── Capture Operations ────────────────────────────────────────────

    def start_capture(self) -> None:
        self._capture_active = True
        self._packet_id_counter = len(self._packets)

    def stop_capture(self) -> int:
        self._capture_active = False
        return len(self._packets)

    def add_packet(self, src: str, dst: str, proto: Protocol, size: int, info: str = "") -> CapturedPacket:
        self._packet_id_counter += 1
        packet = CapturedPacket(
            timestamp=time.time(),
            source=src, destination=dst,
            protocol=proto, size=size, info=info,
            packet_id=self._packet_id_counter,
        )
        self._packets.insert(0, packet)
        if len(self._packets) > self._capture_limit:
            self._packets.pop()
        return packet

    def clear_capture(self) -> int:
        count = len(self._packets)
        self._packets.clear()
        return count

    def set_capture_filter(self, filter_str: str) -> None:
        self._capture_filter = filter_str

    def get_filtered_packets(self) -> List[CapturedPacket]:
        if not self._capture_filter:
            return list(self._packets)
        f = self._capture_filter.lower()
        return [p for p in self._packets
                if f in p.source or f in p.destination or f in p.protocol.value.lower()
                or f in p.info.lower()]

    # ── Ping Operations ──────────────────────────────────────────────

    def ping(self, host: str) -> PingResult:
        latency = random.uniform(5.0, 50.0)
        result = PingResult(host, latency, 0.0)
        self._ping_results.insert(0, result)
        return result

    # ── Statistics ────────────────────────────────────────────────────

    def get_protocol_stats(self) -> Dict[Protocol, Dict]:
        stats: Dict[Protocol, Dict] = {}
        for conn in self._connections:
            if conn.protocol not in stats:
                stats[conn.protocol] = {"count": 0, "rx_bytes": 0, "tx_bytes": 0}
            stats[conn.protocol]["count"] += 1
            stats[conn.protocol]["rx_bytes"] += conn.rx_bytes
            stats[conn.protocol]["tx_bytes"] += conn.tx_bytes
        return stats

    def get_top_talkers(self, limit: int = 5) -> List[Tuple[str, int]]:
        talkers: Dict[str, int] = {}
        for conn in self._connections:
            key = conn.remote_addr
            talkers[key] = talkers.get(key, 0) + conn.rx_bytes + conn.tx_bytes
        sorted_talkers = sorted(talkers.items(), key=lambda x: x[1], reverse=True)
        return sorted_talkers[:limit]

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
        if self._view_mode == "interfaces":
            return self._interfaces
        elif self._view_mode == "connections":
            return self._connections
        elif self._view_mode == "capture":
            return self.get_filtered_packets()
        elif self._view_mode == "ping":
            return self._ping_results
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def interfaces(self) -> List[NetworkInterface]:
        return list(self._interfaces)

    @property
    def connections(self) -> List[Connection]:
        return list(self._connections)

    @property
    def packets(self) -> List[CapturedPacket]:
        return list(self._packets)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def capture_active(self) -> bool:
        return self._capture_active

    @property
    def total_rx_rate(self) -> float:
        return sum(iface.rx_rate_bps for iface in self._interfaces)

    @property
    def total_tx_rate(self) -> float:
        return sum(iface.tx_rate_bps for iface in self._interfaces)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_overview(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🌐 Network Analyzer — Overview")
        lines.append("─" * width)

        # Total bandwidth
        rx = self.total_rx_rate
        tx = self.total_tx_rate
        lines.append(f" 📥 RX: {self._format_rate(rx)} | 📤 TX: {self._format_rate(tx)}")
        lines.append(f" Connections: {len(self._connections)} | Interfaces: {len([i for i in self._interfaces if i.status == InterfaceStatus.UP])}")
        lines.append("─" * width)

        # Interface sparklines
        lines.append(" Interfaces:")
        for iface in self._interfaces[:3]:
            if iface.status == InterfaceStatus.UP:
                lines.append(f"  {iface.name:<8s} RX {iface.sparkline_rx(30)} {iface.rx_rate_str}")
                lines.append(f"           TX {iface.sparkline_tx(30)} {iface.tx_rate_str}")

        lines.append("")
        # Protocol breakdown
        proto_stats = self.get_protocol_stats()
        if proto_stats:
            lines.append(" Protocol Breakdown:")
            for proto, stats in sorted(proto_stats.items(), key=lambda x: x[1]["rx_bytes"], reverse=True):
                icon = PROTO_ICONS.get(proto, "❓")
                total = stats["rx_bytes"] + stats["tx_bytes"]
                if total >= 1048576:
                    size_str = f"{total / 1048576:.1f} MB"
                elif total >= 1024:
                    size_str = f"{total / 1024:.1f} KB"
                else:
                    size_str = f"{total} B"
                lines.append(f"  {icon} {proto.value:<8s} {stats['count']:>3d} conns  {size_str}")

        lines.append("")
        # Top talkers
        talkers = self.get_top_talkers(3)
        if talkers:
            lines.append(" Top Talkers:")
            for addr, bytes_total in talkers:
                lines.append(f"  {addr:<20s} {self._format_bytes(bytes_total)}")

        lines.append("─" * width)
        lines.append(" I:Interfaces  C:Connections  P:Capture  G:Ping  Esc:Back")
        return lines

    def render_interfaces(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📡 Network Interfaces")
        lines.append("─" * width)

        for i, iface in enumerate(self._interfaces):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {iface.display}")
            lines.append(f"   MAC: {iface.mac_address} | Speed: {iface.speed_mbps} Mbps")
            if iface.ipv4:
                lines.append(f"   IPv4: {iface.ipv4} | IPv6: {iface.ipv6 or 'N/A'}")
            lines.append(f"   RX: {iface.total_rx_str} ({iface.rx_rate_str}) | TX: {iface.total_tx_str} ({iface.tx_rate_str})")
            lines.append(f"   Errors: RX {iface.rx_errors} TX {iface.tx_errors} | Dropped: RX {iface.rx_dropped} TX {iface.tx_dropped}")
            lines.append(f"   Packets: RX {iface.rx_packets:,} | TX {iface.tx_packets:,}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_connections(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🔗 Connections ({len(self._connections)})")
        lines.append("─" * width)

        for i, conn in enumerate(self._connections):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {conn.display}")
            lines.append(f"   Process: {conn.process} (PID {conn.pid}) | Duration: {conn.duration_str} | Traffic: {conn.traffic_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_capture(self, width: int = 80) -> List[str]:
        lines = []
        status = " 🔴 CAPTURING" if self._capture_active else ""
        lines.append(f" 📦 Packet Capture ({len(self._packets)} packets){status}")
        lines.append("─" * width)

        if self._capture_filter:
            lines.append(f" Filter: {self._capture_filter}")

        packets = self.get_filtered_packets()
        for i, pkt in enumerate(packets[:20]):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {pkt.display}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Space:Start/Stop capture  F:Filter  C:Clear  Esc:Back")
        return lines

    def render_ping(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🔔 Ping Monitor")
        lines.append("─" * width)

        for i, result in enumerate(self._ping_results):
            marker = "▸" if i == self._selected_index else " "
            quality = result.quality
            icon = "🟢" if quality in ("Excellent", "Good") else "🟡" if quality == "Fair" else "🔴"
            lines.append(f"{marker} {icon} {result.host}")
            lines.append(f"   Latency: {result.latency_str} ({quality}) | Loss: {result.packet_loss:.1f}% | TTL: {result.ttl}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  P:Ping selected  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "interfaces": self.render_interfaces,
            "connections": self.render_connections,
            "capture": self.render_capture,
            "ping": self.render_ping,
        }
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    @staticmethod
    def _format_rate(bps: float) -> str:
        if bps >= 1073741824:
            return f"{bps / 1073741824:.2f} Gbps"
        elif bps >= 1048576:
            return f"{bps / 1048576:.1f} Mbps"
        elif bps >= 1024:
            return f"{bps / 1024:.1f} Kbps"
        return f"{bps:.0f} bps"

    @staticmethod
    def _format_bytes(b: int) -> str:
        if b >= 1073741824:
            return f"{b / 1073741824:.2f} GB"
        elif b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        elif b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "interfaces":
            return self._handle_interfaces_key(key)
        elif self._view_mode == "connections":
            return self._handle_connections_key(key)
        elif self._view_mode == "capture":
            return self._handle_capture_key(key)
        elif self._view_mode == "ping":
            return self._handle_ping_key(key)
        return self._handle_overview_key(key)

    def _handle_overview_key(self, key: str) -> Optional[str]:
        if key == "i":
            self.set_view("interfaces")
            return "interfaces"
        elif key == "c":
            self.set_view("connections")
            return "connections"
        elif key == "p":
            self.set_view("capture")
            return "capture"
        elif key == "g":
            self.set_view("ping")
            return "ping"
        return None

    def _handle_interfaces_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_connections_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_capture_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == " ":
            if self._capture_active:
                self.stop_capture()
                return "capture_stop"
            else:
                self.start_capture()
                return "capture_start"
        elif key == "c":
            self.clear_capture()
            return "capture_clear"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_ping_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "p":
            result = self.get_selected_item()
            if result:
                self.ping(result.host)
                return "ping"
        return None
