"""Network Packet Analyzer — protocol dissection, filtering, statistics for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class Protocol(Enum):
    ETHERNET = "Ethernet"
    ARP = "ARP"
    IPV4 = "IPv4"
    IPV6 = "IPv6"
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    DNS = "DNS"
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    SSH = "SSH"
    TLS = "TLS"
    DHCP = "DHCP"
    OSPF = "OSPF"
    BGP = "BGP"
    SNMP = "SNMP"
    MQTT = "MQTT"
    WebSocket = "WebSocket"


class PacketDirection(Enum):
    INBOUND = "Inbound"
    OUTBOUND = "Outbound"
    LOCAL = "Local"
    BROADCAST = "Broadcast"
    MULTICAST = "Multicast"


class PacketStatus(Enum):
    CAPTURED = "Captured"
    ANALYZED = "Analyzed"
    FLAGGED = "Flagged"
    DROPPED = "Dropped"
    REASSEMBLED = "Reassembled"


class FilterAction(Enum):
    CAPTURE = "Capture"
    DISPLAY = "Display"
    EXPORT = "Export"


class ThreatLevel(Enum):
    NONE = "None"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


@dataclass
class MACAddress:
    octets: Tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)

    @property
    def str(self) -> str:
        return ":".join(f"{o:02x}" for o in self.octets)

    @property
    def is_broadcast(self) -> bool:
        return self.octets == (0xff, 0xff, 0xff, 0xff, 0xff, 0xff)

    @property
    def is_multicast(self) -> bool:
        return (self.octets[0] & 0x01) == 1


@dataclass
class IPAddress:
    addr: str = "0.0.0.0"
    version: int = 4

    @property
    def is_private(self) -> bool:
        parts = self.addr.split(".")
        if len(parts) != 4:
            return False
        try:
            first = int(parts[0])
            second = int(parts[1])
            if first == 10:
                return True
            if first == 172 and 16 <= second <= 31:
                return True
            if first == 192 and second == 168:
                return True
        except ValueError:
            pass
        return False

    @property
    def is_loopback(self) -> bool:
        return self.addr.startswith("127.")

    @property
    def type_str(self) -> str:
        if self.is_loopback:
            return "Loopback"
        if self.is_private:
            return "Private"
        return "Public"


@dataclass
class PacketHeader:
    src_mac: MACAddress = field(default_factory=MACAddress)
    dst_mac: MACAddress = field(default_factory=MACAddress)
    src_ip: IPAddress = field(default_factory=IPAddress)
    dst_ip: IPAddress = field(default_factory=IPAddress)
    src_port: int = 0
    dst_port: int = 0
    protocol: Protocol = Protocol.IPV4
    ttl: int = 64
    tos: int = 0
    flags: str = ""
    window_size: int = 0
    sequence: int = 0
    ack: int = 0
    length: int = 0

    @property
    def protocol_stack(self) -> str:
        parts = []
        if self.src_port > 0 or self.dst_port > 0:
            parts.append(f"{self.protocol.value}")
        else:
            parts.append(self.protocol.value)
        return " → ".join(parts)

    @property
    def endpoint(self) -> str:
        if self.src_port > 0:
            return f"{self.src_ip.addr}:{self.src_port} → {self.dst_ip.addr}:{self.dst_port}"
        return f"{self.src_ip.addr} → {self.dst_ip.addr}"


@dataclass
class Packet:
    id: int
    timestamp: float = 0.0
    header: PacketHeader = field(default_factory=PacketHeader)
    data_length: int = 0
    raw_data: bytes = b""
    direction: PacketDirection = PacketDirection.INBOUND
    status: PacketStatus = PacketStatus.CAPTURED
    threat_level: ThreatLevel = ThreatLevel.NONE
    annotations: List[str] = field(default_factory=list)
    dissected: bool = False

    @property
    def size_str(self) -> str:
        if self.data_length < 1024:
            return f"{self.data_length} B"
        elif self.data_length < 1024 * 1024:
            return f"{self.data_length / 1024:.1f} KB"
        else:
            return f"{self.data_length / (1024 * 1024):.1f} MB"

    @property
    def time_str(self) -> str:
        t = time.localtime(self.timestamp)
        return time.strftime("%H:%M:%S", t)

    @property
    def threat_icon(self) -> str:
        icons = {
            ThreatLevel.NONE: "",
            ThreatLevel.LOW: "🟡",
            ThreatLevel.MEDIUM: "🟠",
            ThreatLevel.HIGH: "🔴",
            ThreatLevel.CRITICAL: "🚨",
        }
        return icons.get(self.threat_level, "")

    @property
    def direction_icon(self) -> str:
        icons = {
            PacketDirection.INBOUND: "⬇",
            PacketDirection.OUTBOUND: "⬆",
            PacketDirection.LOCAL: "↔",
            PacketDirection.BROADCAST: "📢",
            PacketDirection.MULTICAST: "📡",
        }
        return icons.get(self.direction, "?")


@dataclass
class CaptureFilter:
    name: str
    expression: str = ""
    action: FilterAction = FilterAction.DISPLAY
    match_count: int = 0
    enabled: bool = True

    @property
    def match_bar(self) -> str:
        return f"{self.match_count}"


@dataclass
class ProtocolStats:
    protocol: Protocol
    packet_count: int = 0
    byte_count: int = 0
    avg_size: float = 0.0
    percentage: float = 0.0

    @property
    def size_str(self) -> str:
        if self.byte_count < 1024:
            return f"{self.byte_count} B"
        elif self.byte_count < 1024 * 1024:
            return f"{self.byte_count / 1024:.1f} KB"
        else:
            return f"{self.byte_count / (1024 * 1024):.1f} MB"

    @property
    def pct_bar(self) -> str:
        filled = int(self.percentage / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class Conversation:
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    protocol: str = ""
    packet_count: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    duration_s: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def total_bytes(self) -> int:
        return self.bytes_sent + self.bytes_received

    @property
    def endpoint(self) -> str:
        return f"{self.src_ip}:{self.src_port} ↔ {self.dst_ip}:{self.dst_port}"


class PacketAnalyzer:
    def __init__(self):
        self._packets: List[Packet] = []
        self._selected_packet: int = 0
        self._filters: List[CaptureFilter] = []
        self._protocol_stats: Dict[str, ProtocolStats] = {}
        self._conversations: List[Conversation] = []
        self._view_mode: str = "packet_list"
        self._auto_scroll: bool = True
        self._capture_active: bool = False
        self._packet_counter: int = 0
        self._total_bytes: int = 0
        self._capture_start: float = 0.0
        self._display_filter: str = ""
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        self._capture_start = time.time() - 300

        # Sample packets
        protocols = [Protocol.TCP, Protocol.UDP, Protocol.HTTPS, Protocol.DNS, Protocol.ICMP, Protocol.ARP]
        src_ips = ["192.168.1.100", "10.0.0.50", "172.16.0.25", "8.8.8.8", "1.1.1.1"]
        dst_ips = ["93.184.216.34", "142.250.80.46", "104.244.42.193", "8.8.4.4", "192.168.1.1"]

        for i in range(50):
            self._packet_counter += 1
            proto = random.choice(protocols)
            src_ip = random.choice(src_ips)
            dst_ip = random.choice(dst_ips)
            src_port = random.randint(1024, 65535)
            dst_port = {Protocol.TCP: 443, Protocol.UDP: 53, Protocol.HTTPS: 443,
                        Protocol.DNS: 53, Protocol.ICMP: 0, Protocol.ARP: 0}.get(proto, 80)
            length = random.randint(64, 1500)

            pkt = Packet(
                self._packet_counter,
                self._capture_start + i * 0.6,
                PacketHeader(
                    MACAddress((0x00, 0x1a, 0x2b, random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))),
                    MACAddress((0x00, 0x3c, 0x4d, random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))),
                    IPAddress(src_ip), IPAddress(dst_ip),
                    src_port, dst_port, proto, random.choice([64, 128, 255]),
                ),
                length,
                direction=random.choice(list(PacketDirection)),
                threat_level=random.choices(
                    [ThreatLevel.NONE, ThreatLevel.LOW, ThreatLevel.MEDIUM],
                    weights=[80, 15, 5]
                )[0],
            )
            self._packets.append(pkt)
            self._total_bytes += length

            # Update protocol stats
            proto_name = proto.value
            if proto_name not in self._protocol_stats:
                self._protocol_stats[proto_name] = ProtocolStats(proto, 0, 0, 0, 0)
            ps = self._protocol_stats[proto_name]
            ps.packet_count += 1
            ps.byte_count += length
            ps.avg_size = ps.byte_count / ps.packet_count

        # Calculate percentages
        total_pkts = len(self._packets)
        for ps in self._protocol_stats.values():
            ps.percentage = (ps.packet_count / total_pkts * 100) if total_pkts > 0 else 0

        # Sample conversations
        self._conversations = [
            Conversation("192.168.1.100", "93.184.216.34", 49152, 443, "TCP/TLS", 45, 12800, 85000, 280),
            Conversation("192.168.1.100", "142.250.80.46", 49153, 443, "TCP/TLS", 32, 8400, 62000, 195),
            Conversation("10.0.0.50", "8.8.8.8", 49154, 53, "UDP/DNS", 12, 840, 2400, 45),
            Conversation("172.16.0.25", "1.1.1.1", 49155, 443, "TCP/TLS", 18, 3200, 24000, 90),
            Conversation("192.168.1.100", "192.168.1.1", 0, 0, "ICMP", 8, 640, 640, 12),
        ]

        # Sample filters
        self._filters = [
            CaptureFilter("HTTP Traffic", "tcp.port == 80", FilterAction.DISPLAY, 120),
            CaptureFilter("DNS Queries", "udp.port == 53", FilterAction.DISPLAY, 45),
            CaptureFilter("External IPs", "not (ip.src == 192.168.0.0/16)", FilterAction.DISPLAY, 200),
            CaptureFilter("Suspicious", "tcp.flags.syn == 1 and tcp.flags.ack == 0", FilterAction.DISPLAY, 15),
            CaptureFilter("Large Packets", "frame.len > 1000", FilterAction.CAPTURE, 35),
        ]

    @property
    def selected_packet(self) -> Optional[Packet]:
        if 0 <= self._selected_packet < len(self._packets):
            return self._packets[self._selected_packet]
        return None

    @property
    def total_packets(self) -> int:
        return len(self._packets)

    @property
    def total_bytes_display(self) -> str:
        if self._total_bytes < 1024:
            return f"{self._total_bytes} B"
        elif self._total_bytes < 1024 * 1024:
            return f"{self._total_bytes / 1024:.1f} KB"
        else:
            return f"{self._total_bytes / (1024 * 1024):.1f} MB"

    @property
    def packets_per_second(self) -> float:
        elapsed = time.time() - self._capture_start
        return len(self._packets) / max(1, elapsed)

    def select_packet(self, idx: int):
        if 0 <= idx < len(self._packets):
            self._selected_packet = idx

    def start_capture(self):
        self._capture_active = True
        self._capture_start = time.time()
        self._history.append("Capture started")

    def stop_capture(self):
        self._capture_active = False
        self._history.append("Capture stopped")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "s":
            if self._capture_active:
                self.stop_capture()
            else:
                self.start_capture()
        elif key == "f":
            self._view_mode = "filter"
        elif key == "t":
            self._view_mode = "statistics"
        elif key == "c":
            self._view_mode = "conversations"
        elif key == "p":
            self._view_mode = "packet_list"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS PACKET ANALYZER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Capture status
        status = "⏺ CAPTURING" if self._capture_active else "⏸ Stopped"
        lines.append(f"  {status}  Packets: {self.total_packets}  Bytes: {self.total_bytes_display}  Rate: {self.packets_per_second:.1f} pkt/s  Filters: {len(self._filters)}")
        if self._display_filter:
            lines.append(f"  Filter: {self._display_filter}")
        lines.append("")

        # Packet list
        lines.append("  ── Packets ──")
        lines.append(f"  {'No':<6s} {'Time':<10s} {'Proto':<8s} {'Source':<22s} {'Destination':<22s} {'Len':<8s} {'Info'}")
        lines.append(f"  {'─' * 100}")

        for pkt in self._packets[:15]:
            sel = "▶" if pkt.id - 1 == self._selected_packet else " "
            dir_icon = pkt.direction_icon
            threat = pkt.threat_icon
            h = pkt.header
            src = f"{h.src_ip.addr}:{h.src_port}" if h.src_port else h.src_ip.addr
            dst = f"{h.dst_ip.addr}:{h.dst_port}" if h.dst_port else h.dst_ip.addr
            info = f"{h.flags}" if h.flags else ""
            lines.append(f"  {sel}{pkt.id:<5d} {pkt.time_str:<10s} {h.protocol.value:<8s} {src:<22s} {dst:<22s} {pkt.size_str:<8s} {dir_icon}{threat} {info}")

        if self.total_packets > 15:
            lines.append(f"  ... ({self.total_packets - 15} more packets)")
        lines.append("")

        # Protocol statistics
        lines.append("  ── Protocol Statistics ──")
        for name, ps in sorted(self._protocol_stats.items(), key=lambda x: -x[1].packet_count)[:8]:
            lines.append(f"  {name:<10s} {ps.pct_bar} {ps.percentage:5.1f}%  {ps.packet_count:>5d} pkts  {ps.size_str}")
        lines.append("")

        # Selected packet detail
        pkt = self.selected_packet
        if pkt:
            h = pkt.header
            lines.append(f"  ── Packet #{pkt.id} Detail ──")
            lines.append(f"  {h.src_ip.addr} ({h.src_ip.type_str}) → {h.dst_ip.addr} ({h.dst_ip.type_str})")
            lines.append(f"  Protocol: {h.protocol.value}  TTL: {h.ttl}  Length: {pkt.data_length}B  Direction: {pkt.direction.value}")
            if h.src_port:
                lines.append(f"  Port: {h.src_port} → {h.dst_port}  Flags: {h.flags or 'None'}  Window: {h.window_size}")
            if pkt.annotations:
                lines.append(f"  Annotations: {', '.join(pkt.annotations)}")
            lines.append("")

        lines.append("  [S]tart/Stop Capture [P]ackets [T]Statistics [C]Conversations [F]Filter")
        lines.append("  [↑↓]Select Packet [Ctrl+F]Display Filter")
        return lines
