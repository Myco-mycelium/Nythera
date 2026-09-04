"""
Nyrqis OS - Network Packet Analyzer
Protocol decoding, traffic statistics, and filter expressions.
"""

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
    number: int
    timestamp: float
    source_ip: str
    dest_ip: str
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

    @property
    def direction_icon(self) -> str:
        icons = {
            PacketDirection.INBOUND: "⬇️",
            PacketDirection.OUTBOUND: "⬆️",
            PacketDirection.LOCAL: "↔️",
        }
        return icons.get(self.direction, "?")


@dataclass
class ProtocolStats:
    protocol: Protocol
    packet_count: int = 0
    byte_count: int = 0
    avg_size: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0

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

    @property
    def total_bytes(self) -> int:
        return self.bytes_a_to_b + self.bytes_b_to_a

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
        self._create_sample_data()

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
        self.start_time = time.time()
        return True

    def stop_capture(self) -> int:
        self.state = CaptureState.STOPPED
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


@dataclass
class PacketHeader:
    source_ip: str = ""
    dest_ip: str = ""
    source_port: int = 0
    dest_port: int = 0
    protocol: str = ""
    length: int = 0


class MACAddress:
    pass  # backward compat stub

IPAddress = MACAddress
