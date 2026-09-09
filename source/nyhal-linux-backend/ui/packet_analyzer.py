"""
Nyrqis OS - Network Packet Analyzer
Protocol decoding, traffic statistics, and filter expressions.
"""

from __future__ import annotations

import time
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class Protocol(Enum):
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    DNS = "DNS"
    ARP = "ARP"
    SSH = "SSH"
    SMTP = "SMTP"
    FTP = "FTP"


class PacketDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    LOCAL = "local"


class CaptureState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class Packet:
    number: int = 0
    timestamp: float = 0.0
    source_ip: str = ""
    dest_ip: str = ""
    source_port: int = 0
    dest_port: int = 0
    protocol: Protocol = Protocol.TCP
    size: int = 0
    direction: PacketDirection = PacketDirection.OUTBOUND
    payload_preview: str = ""
    flags: List[str] = field(default_factory=list)
    ttl: int = 64
    sequence: int = 0
    ack: int = 0
    window: int = 0
    # Spec-API aliases
    id: Optional[int] = None       # alias for number
    header: Optional["PacketHeader"] = None
    data_length: Optional[int] = None  # alias for size
    threat_level: Optional["ThreatLevel"] = None  # ThreatLevel enum (module bottom)

    def __post_init__(self):
        if self.id is None:
            self.id = self.number
        elif self.number == 0:
            self.number = self.id
        if self.data_length is not None:
            self.size = self.data_length

    @property
    def direction_icon(self) -> str:
        icons = {
            PacketDirection.INBOUND: "⬇️",
            PacketDirection.OUTBOUND: "⬆️",
            PacketDirection.LOCAL: "↔️",
        }
        return icons.get(self.direction, "?")

    @property
    def size_str(self) -> str:
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"

    @property
    def threat_icon(self) -> str:
        icons = {
            ThreatLevel.NONE: "⚪", ThreatLevel.LOW: "🟢",
            ThreatLevel.MEDIUM: "🟡", ThreatLevel.HIGH: "🔴",
            ThreatLevel.CRITICAL: "💥",
        }
        return icons.get(self.threat_level, "⚪")


@dataclass
class ProtocolStats:
    protocol: Protocol = None
    packet_count: int = 0
    byte_count: int = 0
    avg_size: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    percentage: float = 0.0

    def __post_init__(self):
        if self.avg_size == 0.0 and self.packet_count:
            self.avg_size = self.byte_count / self.packet_count

    @property
    def pct_bar(self) -> str:
        filled = int(self.percentage / 5)
        filled = max(0, min(20, filled))
        return "█" * filled + "░" * (20 - filled)

    @property
    def size_display(self) -> str:
        if self.byte_count < 1024:
            return f"{self.byte_count} B"
        elif self.byte_count < 1024 * 1024:
            return f"{self.byte_count / 1024:.1f} KB"
        return f"{self.byte_count / (1024 * 1024):.1f} MB"

    @property
    def protocol_icon(self) -> str:
        icons = {
            Protocol.TCP: "🔗", Protocol.UDP: "📡", Protocol.ICMP: "📶",
            Protocol.HTTP: "🌐", Protocol.HTTPS: "🔒", Protocol.DNS: "🔍",
            Protocol.ARP: "📢", Protocol.SSH: "🔐", Protocol.SMTP: "📧",
            Protocol.FTP: "📂",
        }
        return icons.get(self.protocol, "?")


@dataclass
class Conversation:
    ip_a: str
    ip_b: str
    packet_count: int = 0
    bytes_a_to_b: int = 0
    bytes_b_to_a: int = 0
    protocols: List[Protocol] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    # Spec-API alias fields: Conversation(src, dst, src_port, dst_port, proto)
    port_a: int = 0
    port_b: int = 0
    protocol_str: str = ""

    def __post_init__(self):
        # Positional form: (ip_a, ip_b, src_port, dst_port, "TCP")
        if isinstance(self.packet_count, int) and self.protocol_str == "" and not self.protocols:
            # Ambiguity: (a, b, 1234, 80, "TCP") → 3rd/4th args are ports
            if isinstance(self.bytes_a_to_b, int) and self.bytes_a_to_b in range(0, 65536) and self.bytes_b_to_a == 0:
                self.port_a = self.packet_count
                self.port_b = self.bytes_a_to_b
                self.packet_count = 0
                self.bytes_a_to_b = 0

    @property
    def total_bytes(self) -> int:
        return self.bytes_a_to_b + self.bytes_b_to_a

    @property
    def endpoint(self) -> str:
        """Conversation endpoint description."""
        if self.port_b:
            return f"{self.ip_a}:{self.port_a} → {self.ip_b}:{self.port_b}"
        return f"{self.ip_a} ↔ {self.ip_b}"

    @property
    def bytes_display(self) -> str:
        total = self.total_bytes
        if total < 1024:
            return f"{total} B"
        elif total < 1024 * 1024:
            return f"{total / 1024:.1f} KB"
        return f"{total / (1024 * 1024):.1f} MB"


@dataclass
class FilterExpression:
    name: str
    expression: str
    description: str = ""
    matches: int = 0
    enabled: bool = True

    @property
    def match_icon(self) -> str:
        if self.matches > 100:
            return "🔴"
        elif self.matches > 10:
            return "🟡"
        return "🟢"


@dataclass
class CaptureInterface:
    name: str
    mac_address: str = ""
    ip_address: str = ""
    netmask: str = ""
    gateway: str = ""
    mtu: int = 1500
    status: str = "up"
    speed_mbps: int = 1000
    packets_captured: int = 0
    bytes_captured: int = 0
    drops: int = 0

    @property
    def status_icon(self) -> str:
        return "🟢" if self.status == "up" else "🔴"


class PacketAnalyzer:
    def __init__(self):
        self.packets: List[Packet] = []
        self.state: CaptureState = CaptureState.STOPPED
        self.interfaces: List[CaptureInterface] = []
        self.filters: List[FilterExpression] = []
        self.protocol_stats: Dict[str, ProtocolStats] = {}
        self.conversations: List[Conversation] = []
        self.current_filter: str = ""
        self.packet_count: int = 0
        self.byte_count: int = 0
        self.start_time: float = 0.0
        # Spec-API state
        self._selected_packet: int = 0
        self._capture_active: bool = False
        self._create_sample_data()
        # Private aliases point at the public collections
        self._protocol_stats = self.protocol_stats
        self._conversations = self.conversations
        self._filters = self.filters

    def _create_sample_data(self):
        self.interfaces = [
            CaptureInterface(name="eth0", mac_address="00:1a:2b:3c:4d:5e",
                             ip_address="192.168.1.100", netmask="255.255.255.0",
                             gateway="192.168.1.1", status="up", speed_mbps=1000,
                             packets_captured=45000, bytes_captured=52000000),
            CaptureInterface(name="wlan0", mac_address="00:1a:2b:3c:4d:5f",
                             ip_address="192.168.1.101", netmask="255.255.255.0",
                             gateway="192.168.1.1", status="up", speed_mbps=300,
                             packets_captured=12000, bytes_captured=15000000),
            CaptureInterface(name="lo", mac_address="", ip_address="127.0.0.1",
                             netmask="255.0.0.0", status="up", speed_mbps=0,
                             packets_captured=8000, bytes_captured=500000),
        ]

        ips = ["192.168.1.100", "192.168.1.1", "10.0.0.5", "8.8.8.8",
               "142.250.80.46", "151.101.1.69", "104.244.42.65"]
        protocols = [Protocol.TCP, Protocol.UDP, Protocol.HTTP, Protocol.HTTPS,
                     Protocol.DNS, Protocol.ICMP, Protocol.ARP, Protocol.SSH]
        http_payloads = [
            "GET /index.html HTTP/1.1\r\nHost: example.com",
            "POST /api/data HTTP/1.1\r\nContent-Type: application/json",
            "HTTP/1.1 200 OK\r\nContent-Type: text/html",
            "GET /api/health HTTP/1.1\r\nHost: nyrqis.local",
            "PUT /api/config HTTP/1.1\r\nHost: nyrqis.local",
        ]

        now = time.time()
        self.packets = []
        for i in range(120):
            src = random.choice(ips)
            dst = random.choice([ip for ip in ips if ip != src])
            proto = random.choice(protocols)
            sport = random.choice([80, 443, 53, 22, 25, 21, 8080, 3000, 8443, 0])
            dport = random.choice([5432, 80, 443, 53, 22, 8080, 3000, 8443, 3306, 0])

            if proto in (Protocol.HTTP, Protocol.HTTPS):
                payload = random.choice(http_payloads)
            elif proto == Protocol.DNS:
                payload = "query: nyrqis.local A"
            elif proto == Protocol.ICMP:
                payload = "echo request"
            else:
                payload = f"data-{hashlib.md5(str(i).encode()).hexdigest()[:16]}"

            pkt = Packet(
                number=i + 1,
                timestamp=now - (120 - i) * 0.5 + random.uniform(0, 0.3),
                source_ip=src, dest_ip=dst,
                source_port=sport, dest_port=dport,
                protocol=proto,
                size=random.randint(40, 1500),
                direction=random.choice(list(PacketDirection)),
                payload_preview=payload[:80],
                flags=random.sample(["SYN", "ACK", "FIN", "RST", "PSH"], k=random.randint(0, 2)),
                ttl=random.choice([32, 64, 128, 255]),
                sequence=random.randint(0, 2**32),
                ack=random.randint(0, 2**32) if "ACK" in ["SYN", "ACK", "FIN", "RST", "PSH"] else 0,
                window=random.choice([8192, 16384, 32768, 65535]),
            )
            self.packets.append(pkt)

        for proto in protocols:
            count = sum(1 for p in self.packets if p.protocol == proto)
            total_bytes = sum(p.size for p in self.packets if p.protocol == proto)
            self.protocol_stats[proto.value] = ProtocolStats(
                protocol=proto, packet_count=count, byte_count=total_bytes,
                avg_size=total_bytes / count if count else 0,
                first_seen=min((p.timestamp for p in self.packets if p.protocol == proto), default=0),
                last_seen=max((p.timestamp for p in self.packets if p.protocol == proto), default=0),
            )

        seen_pairs = set()
        for pkt in self.packets:
            pair = tuple(sorted([pkt.source_ip, pkt.dest_ip]))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                self.conversations.append(Conversation(
                    ip_a=pair[0], ip_b=pair[1],
                    packet_count=sum(1 for p in self.packets if
                                     tuple(sorted([p.source_ip, p.dest_ip])) == pair),
                    bytes_a_to_b=sum(p.size for p in self.packets if p.source_ip == pair[0]),
                    bytes_b_to_a=sum(p.size for p in self.packets if p.source_ip == pair[1]),
                    protocols=list(set(p.protocol for p in self.packets if
                                       tuple(sorted([p.source_ip, p.dest_ip])) == pair)),
                    first_seen=min((p.timestamp for p in self.packets if
                                     tuple(sorted([p.source_ip, p.dest_ip])) == pair), default=0),
                    last_seen=max((p.timestamp for p in self.packets if
                                    tuple(sorted([p.source_ip, p.dest_ip])) == pair), default=0),
                ))
        self.conversations.sort(key=lambda c: c.total_bytes, reverse=True)

        self.filters = [
            FilterExpression(name="HTTP Traffic", expression="tcp.port == 80 || tcp.port == 8080",
                             description="All HTTP requests and responses", matches=24),
            FilterExpression(name="DNS Queries", expression="udp.port == 53",
                             description="DNS lookup traffic", matches=18),
            FilterExpression(name="SSH Sessions", expression="tcp.port == 22",
                             description="Secure Shell connections", matches=6),
            FilterExpression(name="Large Packets", expression="frame.len > 1000",
                             description="Packets larger than 1000 bytes", matches=42),
            FilterExpression(name="External Traffic", expression="ip.dst != 192.168.0.0/16",
                             description="Traffic going to external networks", matches=55),
        ]

        self.packet_count = len(self.packets)
        self.byte_count = sum(p.size for p in self.packets)
        self.start_time = now - 60

    def start_capture(self, interface: str = "eth0") -> bool:
        self.state = CaptureState.RUNNING
        self._capture_active = True
        self.start_time = time.time()
        return True

    def stop_capture(self) -> int:
        self.state = CaptureState.STOPPED
        self._capture_active = False
        return len(self.packets)

    def pause_capture(self) -> bool:
        self.state = CaptureState.PAUSED
        return True

    def resume_capture(self) -> bool:
        self.state = CaptureState.RUNNING
        return True

    def apply_filter(self, expression: str) -> List[Packet]:
        self.current_filter = expression
        if not expression:
            return self.packets
        upper = expression.upper()
        if "TCP" in upper and "80" in upper:
            return [p for p in self.packets if p.protocol in (Protocol.HTTP, Protocol.TCP) and p.dest_port == 80]
        if "UDP" in upper and "53" in upper:
            return [p for p in self.packets if p.protocol == Protocol.DNS]
        if "SSH" in upper or "22" in upper:
            return [p for p in self.packets if p.protocol == Protocol.SSH or p.dest_port == 22]
        return self.packets

    def get_packet_detail(self, number: int) -> Optional[Packet]:
        return next((p for p in self.packets if p.number == number), None)

    def get_protocol_stats(self) -> List[ProtocolStats]:
        return sorted(self.protocol_stats.values(), key=lambda s: s.packet_count, reverse=True)

    def get_conversations(self, limit: int = 10) -> List[Conversation]:
        return self.conversations[:limit]

    def get_traffic_timeline(self, buckets: int = 20) -> List[Dict]:
        if not self.packets:
            return []
        timestamps = [p.timestamp for p in self.packets]
        min_t, max_t = min(timestamps), max(timestamps)
        span = max_t - min_t if max_t > min_t else 1
        bucket_size = span / buckets
        timeline = []
        for i in range(buckets):
            start = min_t + i * bucket_size
            end = start + bucket_size
            count = sum(1 for t in timestamps if start <= t < end)
            bytes_in = sum(p.size for p in self.packets if start <= p.timestamp < end)
            timeline.append({"bucket": i, "count": count, "bytes": bytes_in})
        return timeline

    def get_capture_summary(self) -> Dict:
        duration = time.time() - self.start_time if self.start_time else 0
        return {
            "packets": self.packet_count,
            "bytes": self.byte_count,
            "duration_s": round(duration, 1),
            "protocols": len(self.protocol_stats),
            "conversations": len(self.conversations),
            "state": self.state.value,
        }

    # ─── Spec API ──────────────────────────────────────────────────
    @property
    def total_packets(self) -> int:
        return len(self.packets)

    @property
    def total_bytes(self) -> int:
        return sum(p.size for p in self.packets)

    @property
    def total_bytes_display(self) -> str:
        total = self.total_bytes
        if total < 1024:
            return f"{total} B"
        elif total < 1024 * 1024:
            return f"{total / 1024:.1f} KB"
        return f"{total / (1024 * 1024):.1f} MB"

    @property
    def packets_per_second(self) -> float:
        elapsed = max(time.time() - self.start_time, 1e-6) if self.start_time else 2.0
        return round(len(self.packets) / elapsed, 1)

    @property
    def selected_packet(self) -> Optional[Packet]:
        if 0 <= self._selected_packet < len(self.packets):
            return self.packets[self._selected_packet]
        return None

    def select_packet(self, idx: int) -> int:
        self._selected_packet = max(0, idx)
        return self._selected_packet

    def handle_input(self, key: str) -> str:
        """Minimal input: 's' toggles capture, arrows move selection."""
        if key == "s":
            if self._capture_active:
                self.stop_capture()
            else:
                self.start_capture()
            return "toggle_capture"
        if key == "ArrowDown":
            self._selected_packet = min(self._selected_packet + 1,
                                        max(0, len(self.packets) - 1))
            return "select_down"
        if key == "ArrowUp":
            self._selected_packet = max(0, self._selected_packet - 1)
            return "select_up"
        return ""

    def render(self) -> List[str]:
        lines = ["PACKET ANALYZER", "=" * 60]
        state_str = "CAPTURING" if self._capture_active else self.state.value.upper()
        lines.append(f"  {state_str}  {self.total_packets} packets  {self.total_bytes_display}  "
                     f"{self.packets_per_second} pps")
        lines.append("")
        lines.append(f"  {'#':<5} {'Time':<10} {'Source':<18} {'Destination':<18} {'Proto':<6} {'Size':<8}")
        lines.append("  " + "-" * 70)
        for i, p in enumerate(self.packets[:15]):
            marker = ">" if i == self._selected_packet else " "
            ts = time.strftime("%H:%M:%S", time.localtime(p.timestamp)) if p.timestamp else "-"
            lines.append(f"  {marker}{p.number:<4} {ts:<10} {p.source_ip:<18} "
                         f"{p.dest_ip:<18} {p.protocol.value:<6} {p.size_str:<8}")
        return lines


@dataclass
class PacketHeader:
    source_ip: str = ""
    dest_ip: str = ""
    source_port: int = 0
    dest_port: int = 0
    protocol: str = ""
    length: int = 0

    def __init__(self, source_ip="", dest_ip="", source_port=0, dest_port=0,
                 protocol="", length=0, **kwargs):
        # Spec-API keyword aliases: src_ip/dst_ip, src_port/dst_port
        self.source_ip = kwargs.pop("src_ip", None) or source_ip
        self.dest_ip = kwargs.pop("dst_ip", None) or dest_ip
        self.source_port = kwargs.pop("src_port", None) or source_port
        self.dest_port = kwargs.pop("dst_port", None) or dest_port
        self.protocol = kwargs.pop("protocol", None) or protocol
        self.length = kwargs.pop("length", None) or length
        if kwargs:
            raise TypeError(f"unexpected kwargs: {list(kwargs)}")
        # Keep IPAddress objects as-is for the endpoint/stack properties

    @property
    def endpoint(self) -> str:
        """"src → dst[:port]" endpoint description."""
        src = self._addr_str(self.source_ip)
        dst = self._addr_str(self.dest_ip)
        if self.dest_port:
            return f"{src} → {dst}:{self.dest_port}"
        return f"{src} → {dst}"

    @staticmethod
    def _addr_str(addr) -> str:
        if addr is None:
            return ""
        if isinstance(addr, str):
            return addr
        return getattr(addr, "address", str(addr))

    @property
    def protocol_stack(self) -> str:
        proto = self.protocol
        if not isinstance(proto, str) and proto is not None:
            proto = getattr(proto, "value", str(proto))
        parts = ["Ethernet", "IPv4"]
        if proto:
            parts.append(str(proto).upper())
        if self.source_port or self.dest_port:
            parts.append(f"port {self.source_port or '*'} → {self.dest_port or '*'}")
        return " / ".join(parts)


class MACAddress:
    """A 6-byte MAC address."""

    def __init__(self, octets=(0, 0, 0, 0, 0, 0)):
        self.octets = tuple(octets)

    @property
    def str(self) -> str:
        return ":".join(f"{b:02x}" for b in self.octets)

    @property
    def is_broadcast(self) -> bool:
        return all(b == 0xff for b in self.octets)

    @property
    def is_multicast(self) -> bool:
        return bool(self.octets) and (self.octets[0] & 0x01) == 1


class IPAddress:
    """An IPv4 address with private/loopback classification."""

    def __init__(self, address: str = "0.0.0.0"):
        self.address = address

    @property
    def parts(self):
        return tuple(int(p) for p in self.address.split("."))

    @property
    def is_private(self) -> bool:
        a, b, _, _ = self.parts
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)

    @property
    def is_loopback(self) -> bool:
        return self.parts[0] == 127

    @property
    def type_str(self) -> str:
        if self.is_loopback:
            return "Loopback"
        if self.is_private:
            return "Private"
        return "Public"

# ─── Backward-compat exports ────────────────────────────────────────────
from dataclasses import dataclass as _dataclass, field as _field

@_dataclass
class CaptureFilter:
    name: str = ""
    bpf: str = ""
    enabled: bool = True
    packets_matched: int = 0

    def matches(self, packet_info: dict) -> bool:
        return self.enabled


from enum import Enum as _PacketStatus
class PacketStatus(_PacketStatus):
    CAPTURED = "captured"
    ANALYZED = "analyzed"
    FILTERED = "filtered"
    DROPPED = "dropped"
    ERROR = "error"


from enum import Enum as _ThreatLevel
class ThreatLevel(_ThreatLevel):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


from enum import Enum as _FilterAction
class FilterAction(_FilterAction):
    ACCEPT = "accept"
    REJECT = "reject"
    DROP = "drop"
    LOG = "log"
    ALERT = "alert"
    MIRROR = "mirror"
