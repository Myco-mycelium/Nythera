from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class InterfaceType(Enum):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    VPN = "vpn"
    BRIDGE = "bridge"
    LOOPBACK = "loopback"
    VIRTUAL = "virtual"


class InterfaceStatus(Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"


class SpeedUnit(Enum):
    BPS = "bps"
    Kbps = "Kbps"
    Mbps = "Mbps"
    Gbps = "Gbps"
    Tbps = "Tbps"


class GraphType(Enum):
    REALTIME = "realtime"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Protocol(Enum):
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    DNS = "DNS"
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    SSH = "SSH"
    QUIC = "QUIC"
    WIREGUARD = "WireGuard"
    OTHER = "Other"


@dataclass
class NetworkInterface:
    name: str
    interface_type: InterfaceType
    status: InterfaceStatus
    mac_address: str
    ipv4: str
    ipv6: str
    speed_mbps: int
    mtu: int = 1500
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate_bps: float = 0
    tx_rate_bps: float = 0
    errors_rx: int = 0
    errors_tx: int = 0
    drops_rx: int = 0
    drops_tx: int = 0
    duplex: str = "full"
    driver: str = ""
    is_default_gateway: bool = False
    connected_since: float = 0

    @property
    def rx_rate_display(self) -> str:
        return self._format_rate(self.rx_rate_bps)

    @property
    def tx_rate_display(self) -> str:
        return self._format_rate(self.tx_rate_bps)

    @property
    def total_rx_display(self) -> str:
        return self._format_bytes(self.rx_bytes)

    @property
    def total_tx_display(self) -> str:
        return self._format_bytes(self.tx_bytes)

    @property
    def uptime_display(self) -> str:
        if not self.connected_since:
            return "N/A"
        secs = int(time.time() - self.connected_since)
        d, secs = divmod(secs, 86400)
        h, secs = divmod(secs, 3600)
        m, s = divmod(secs, 60)
        if d:
            return f"{d}d {h}h"
        if h:
            return f"{h}h {m}m"
        return f"{m}m {s}s"

    @staticmethod
    def _format_rate(bps: float) -> str:
        if bps >= 1_000_000_000:
            return f"{bps / 1_000_000_000:.1f} Gbps"
        if bps >= 1_000_000:
            return f"{bps / 1_000_000:.1f} Mbps"
        if bps >= 1_000:
            return f"{bps / 1_000:.0f} Kbps"
        return f"{bps:.0f} bps"

    @staticmethod
    def _format_bytes(b: int) -> str:
        if b >= 1_099_511_627_776:
            return f"{b / 1_099_511_627_776:.1f} TB"
        if b >= 1_073_741_824:
            return f"{b / 1_073_741_824:.1f} GB"
        if b >= 1_048_576:
            return f"{b / 1_048_576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"


@dataclass
class TrafficSample:
    timestamp: float
    rx_bytes: int
    tx_bytes: int
    rx_rate_bps: float = 0
    tx_rate_bps: float = 0


@dataclass
class ProtocolStats:
    protocol: Protocol
    connections: int
    rx_bytes: int
    tx_bytes: int
    avg_latency_ms: float = 0

    @property
    def total_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes


@dataclass
class ConnectionEntry:
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    protocol: Protocol
    state: str
    pid: int = 0
    process: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0


class NetworkMonitor:
    def __init__(self):
        self._interfaces: list[NetworkInterface] = []
        self._selected_interface: int = 0
        self._history: dict[str, list[TrafficSample]] = {}
        self._protocol_stats: list[ProtocolStats] = []
        self._connections: list[ConnectionEntry] = []
        self._graph_type: GraphType = GraphType.REALTIME
        self._max_history: int = 60
        self._auto_refresh: bool = True
        self._refresh_interval_secs: int = 1
        self._total_rx: int = 0
        self._total_tx: int = 0
        self._alert_threshold_mbps: float = 900
        self._selected_connection: int = 0
        self._view: str = "interfaces"
        self._dns_cache_hits: int = 1247
        self._dns_cache_misses: int = 89
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._interfaces = [
            NetworkInterface("eth0", InterfaceType.ETHERNET, InterfaceStatus.UP, "AA:BB:CC:DD:EE:01", "192.168.1.100", "fe80::1", 10000, 1500,
                             rx_bytes=85_899_345_920, tx_bytes=12_884_901_888, rx_rate_bps=850_000_000, tx_rate_bps=120_000_000,
                             errors_rx=0, errors_tx=0, driver="e1000e", is_default_gateway=True, connected_since=now - 86400 * 3),
            NetworkInterface("wlan0", InterfaceType.WIFI, InterfaceStatus.UP, "AA:BB:CC:DD:EE:02", "192.168.1.101", "fe80::2", 866, 1500,
                             rx_bytes=42_949_672_960, tx_bytes=8_589_934_592, rx_rate_bps=45_000_000, tx_rate_bps=8_000_000,
                             errors_rx=2, errors_tx=0, drops_rx=5, driver="iwlwifi", connected_since=now - 86400),
            NetworkInterface("wg0", InterfaceType.VPN, InterfaceStatus.UP, "", "10.0.0.2", "", 1000, 1420,
                             rx_bytes=5_368_709_120, tx_bytes=5_368_709_120, rx_rate_bps=15_000_000, tx_rate_bps=15_000_000,
                             driver="wireguard", connected_since=now - 3600 * 6),
            NetworkInterface("docker0", InterfaceType.BRIDGE, InterfaceStatus.UP, "02:42:AC:11:00:01", "172.17.0.1", "", 10000, 1500,
                             rx_bytes=21_474_836_480, tx_bytes=10_737_418_240, rx_rate_bps=200_000_000, tx_rate_bps=50_000_000,
                             driver="bridge", connected_since=now - 86400 * 7),
            NetworkInterface("lo", InterfaceType.LOOPBACK, InterfaceStatus.UP, "", "127.0.0.1", "::1", 0, 65536,
                             rx_bytes=1_073_741_824, tx_bytes=1_073_741_824, rx_rate_bps=0, tx_rate_bps=0, connected_since=now - 86400 * 30),
        ]
        self._total_rx = sum(i.rx_bytes for i in self._interfaces)
        self._total_tx = sum(i.tx_bytes for i in self._interfaces)

        # Generate sparkline history
        rng_seed = 42
        for iface in self._interfaces:
            samples = []
            base_rx = iface.rx_rate_bps
            base_tx = iface.tx_rate_bps
            for i in range(self._max_history):
                t = now - (self._max_history - i) * self._refresh_interval_secs
                rng_val = math.sin(i * 0.3 + rng_seed) * 0.3 + 0.7
                samples.append(TrafficSample(t, int(base_rx * rng_val), int(base_tx * rng_val),
                                            base_rx * rng_val, base_tx * rng_val))
            self._history[iface.name] = samples

        self._protocol_stats = [
            ProtocolStats(Protocol.HTTPS, 245, 32_212_254_720, 4_294_967_296, 12.3),
            ProtocolStats(Protocol.HTTP, 38, 2_147_483_648, 536_870_912, 8.7),
            ProtocolStats(Protocol.DNS, 1247, 125_829_120, 125_829_120, 2.1),
            ProtocolStats(Protocol.SSH, 12, 5_368_709_120, 5_368_709_120, 45.6),
            ProtocolStats(Protocol.QUIC, 89, 8_589_934_592, 4_294_967_296, 5.2),
            ProtocolStats(Protocol.WIREGUARD, 3, 5_368_709_120, 5_368_709_120, 32.1),
            ProtocolStats(Protocol.TCP, 124, 1_073_741_824, 268_435_456, 15.8),
            ProtocolStats(Protocol.UDP, 56, 536_870_912, 268_435_456, 3.4),
        ]

        self._connections = [
            ConnectionEntry("192.168.1.100", 443, "142.250.80.46", 443, Protocol.HTTPS, "ESTABLISHED", 12345, "firefox", 2_147_483_648, 134_217_728),
            ConnectionEntry("192.168.1.100", 22, "10.0.0.1", 22, Protocol.SSH, "ESTABLISHED", 9876, "ssh", 1_073_741_824, 268_435_456),
            ConnectionEntry("192.168.1.100", 53, "8.8.8.8", 53, Protocol.DNS, "TIME_WAIT", 0, "", 1024, 1024),
            ConnectionEntry("172.17.0.2", 8080, "172.17.0.1", 38476, Protocol.HTTP, "ESTABLISHED", 54321, "nginx", 536_870_912, 134_217_728),
            ConnectionEntry("10.0.0.2", 51820, "10.0.0.1", 51820, Protocol.WIREGUARD, "ESTABLISHED", 0, "", 2_684_354_560, 2_684_354_560),
            ConnectionEntry("192.168.1.101", 443, "13.107.42.14", 443, Protocol.HTTPS, "ESTABLISHED", 12345, "code", 1_073_741_824, 67_108_864),
            ConnectionEntry("192.168.1.100", 993, "142.250.74.165", 993, Protocol.TCP, "ESTABLISHED", 23456, "thunderbird", 268_435_456, 134_217_728),
            ConnectionEntry("172.17.0.3", 5432, "172.17.0.1", 42891, Protocol.TCP, "ESTABLISHED", 11111, "postgres", 536_870_912, 134_217_728),
        ]

    @property
    def selected_interface(self) -> Optional[NetworkInterface]:
        if 0 <= self._selected_interface < len(self._interfaces):
            return self._interfaces[self._selected_interface]
        return None

    @property
    def total_interfaces(self) -> int:
        return len(self._interfaces)

    @property
    def up_interfaces(self) -> int:
        return sum(1 for i in self._interfaces if i.status == InterfaceStatus.UP)

    @property
    def total_connections(self) -> int:
        return len(self._connections)

    @property
    def total_rx_display(self) -> str:
        return NetworkInterface._format_bytes(self._total_rx)

    @property
    def total_tx_display(self) -> str:
        return NetworkInterface._format_bytes(self._total_tx)

    def select_interface(self, idx: int):
        if 0 <= idx < len(self._interfaces):
            self._selected_interface = idx

    def get_sparkline(self, interface_name: str, is_rx: bool = True, length: int = 32) -> str:
        samples = self._history.get(interface_name, [])
        if not samples:
            return "░" * length
        recent = samples[-length:]
        rates = [s.rx_rate_bps if is_rx else s.tx_rate_bps for s in recent]
        max_rate = max(rates) if rates else 1
        if max_rate == 0:
            return "░" * length
        chars = " ▁▂▃▄▅▆▇█"
        result = []
        for r in recent:
            val = r.rx_rate_bps if is_rx else r.tx_rate_bps
            idx = min(int((val / max_rate) * 8), 8)
            result.append(chars[idx])
        return "".join(result)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS NETWORK MONITOR                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Interfaces: {self.up_interfaces}/{self.total_interfaces} up  Connections: {self.total_connections}")
        lines.append(f"  Total RX: {self.total_rx_display}  TX: {self.total_tx_display}")
        lines.append(f"  DNS Cache: {self._dns_cache_hits} hits / {self._dns_cache_misses} misses")
        lines.append("")
        for i, iface in enumerate(self._interfaces):
            sel = "▶" if i == self._selected_interface else " "
            type_icons = {"ethernet": "🔌", "wifi": "📶", "vpn": "🔒", "bridge": "🌉", "loopback": "🔄", "virtual": "💻"}
            icon = type_icons.get(iface.interface_type.value, "?")
            status = "🟢" if iface.status == InterfaceStatus.UP else "🔴"
            gw = " ⭐" if iface.is_default_gateway else ""
            lines.append(f"  {sel} {icon} {iface.name} {status}{gw}")
            lines.append(f"    ↓ {iface.rx_rate_display}  ↑ {iface.tx_rate_display}  {iface.ipv4}")
        lines.append("")
        spark_rx = self.get_sparkline(self.interfaces[self._selected_interface].name if self._interfaces else "eth0", True)
        spark_tx = self.get_sparkline(self.interfaces[self._selected_interface].name if self._interfaces else "eth0", False)
        lines.append(f"  RX ▁{spark_rx}")
        lines.append(f"  TX ▁{spark_tx}")
        lines.append("")
        lines.append("  [C]onnections  [P]rotocols  [D]etails  [G]raph  [S]tats  [F]ilter")
        return lines

    @property
    def interfaces(self):
        return self._interfaces

    def render_connections(self) -> list:
        lines = []
        lines.append("  ── Active Connections ──")
        lines.append("")
        for i, c in enumerate(self._connections):
            sel = "▶" if i == self._selected_connection else " "
            state_icon = "🟢" if c.state == "ESTABLISHED" else "🟡"
            lines.append(f"  {sel} {state_icon} {c.protocol.value} {c.local_addr}:{c.local_port} → {c.remote_addr}:{c.remote_port}")
            if c.process:
                lines.append(f"    📱 {c.process} (PID {c.pid})  ↓{NetworkInterface._format_bytes(c.rx_bytes)} ↑{NetworkInterface._format_bytes(c.tx_bytes)}")
        return lines

    def render_protocols(self) -> list:
        lines = []
        lines.append("  ── Protocol Breakdown ──")
        lines.append("")
        total = sum(p.total_bytes for p in self._protocol_stats)
        for p in sorted(self._protocol_stats, key=lambda x: x.total_bytes, reverse=True):
            pct = (p.total_bytes / total * 100) if total else 0
            bar_len = int(pct / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {p.protocol.value:<12} {bar} {pct:5.1f}%  ({p.connections} conns)")
            lines.append(f"    ↓{NetworkInterface._format_bytes(p.rx_bytes)} ↑{NetworkInterface._format_bytes(p.tx_bytes)}  avg: {p.avg_latency_ms:.1f}ms")
        return lines

    def render_interface_detail(self) -> list:
        iface = self.selected_interface
        if not iface:
            return ["  No interface selected"]
        lines = []
        lines.append(f"  ── {iface.name} ({iface.interface_type.value}) ──")
        lines.append(f"  Status: {iface.status.value}")
        lines.append(f"  MAC: {iface.mac_address}")
        lines.append(f"  IPv4: {iface.ipv4}")
        lines.append(f"  IPv6: {iface.ipv6}")
        lines.append(f"  Speed: {iface.speed_mbps} Mbps  MTU: {iface.mtu}  Duplex: {iface.duplex}")
        lines.append(f"  Driver: {iface.driver}")
        lines.append(f"  RX: {iface.rx_rate_display} (total: {iface.total_rx_display})")
        lines.append(f"  TX: {iface.tx_rate_display} (total: {iface.total_tx_display})")
        lines.append(f"  Errors RX: {iface.errors_rx}  TX: {iface.errors_tx}")
        lines.append(f"  Drops RX: {iface.drops_rx}  TX: {iface.drops_tx}")
        lines.append(f"  Connected: {iface.uptime_display}")
        return lines
