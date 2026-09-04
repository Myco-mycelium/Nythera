"""
Nyrqis OS - Network Traffic Analyzer
Protocol breakdown, bandwidth graphs, and connection tracking.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class Protocol(Enum):
    TCP = "tcp"
    UDP = "udp"
    HTTP = "http"
    HTTPS = "https"
    DNS = "dns"
    SSH = "ssh"
    ICMP = "icmp"
    QUIC = "quic"
    OTHER = "other"


class TrafficDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    LOCAL = "local"


@dataclass
class ProtocolStats:
    protocol: Protocol = Protocol.TCP
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    connections: int = 0
    avg_packet_size: float = 0.0

    @property
    def total_bytes(self) -> int:
        return self.rx_bytes + self.tx_bytes

    @property
    def total_display(self) -> str:
        s = self.total_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def share_pct(self) -> float:
        return 0.0

    @property
    def protocol_icon(self) -> str:
        icons = {
            Protocol.TCP: "🔗", Protocol.UDP: "📡", Protocol.HTTP: "🌐",
            Protocol.HTTPS: "🔒", Protocol.DNS: "🔍", Protocol.SSH: "🔐",
            Protocol.ICMP: "📶", Protocol.QUIC: "⚡", Protocol.OTHER: "❓",
        }
        return icons.get(self.protocol, "?")


@dataclass
class TrafficFlow:
    src_ip: str = ""
    src_port: int = 0
    dst_ip: str = ""
    dst_port: int = 0
    protocol: Protocol = Protocol.TCP
    direction: TrafficDirection = TrafficDirection.OUTBOUND
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    process: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    duration_s: float = 0.0

    @property
    def src_display(self) -> str:
        return f"{self.src_ip}:{self.src_port}"

    @property
    def dst_display(self) -> str:
        return f"{self.dst_ip}:{self.dst_port}"

    @property
    def direction_icon(self) -> str:
        icons = {
            TrafficDirection.INBOUND: "⬇️", TrafficDirection.OUTBOUND: "⬆️",
            TrafficDirection.LOCAL: "↔️",
        }
        return icons.get(self.direction, "?")


@dataclass
class BandwidthSample:
    timestamp: float = 0.0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    total_rate: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def rate_display(self) -> str:
        bps = self.total_rate
        if bps < 1024:
            return f"{bps:.0f} B/s"
        elif bps < 1024 * 1024:
            return f"{bps / 1024:.1f} KB/s"
        elif bps < 1024 * 1024 * 1024:
            return f"{bps / (1024 * 1024):.1f} MB/s"
        return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"


@dataclass
class GeoLocation:
    ip: str = ""
    country: str = ""
    city: str = ""
    lat: float = 0.0
    lon: float = 0.0
    org: str = ""
    flag: str = ""


class TrafficAnalyzer:
    def __init__(self):
        self.protocol_stats: Dict[str, ProtocolStats] = {}
        self.flows: List[TrafficFlow] = []
        self.bandwidth_history: List[BandwidthSample] = []
        self.geo_locations: List[GeoLocation] = []
        self.total_rx: int = 0
        self.total_tx: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_flows = [
            ("192.168.1.100", 42312, "140.82.121.3", 443, Protocol.HTTPS,
             TrafficDirection.OUTBOUND, 45000000, 8000000, "firefox"),
            ("192.168.1.100", 51234, "151.101.1.69", 443, Protocol.HTTPS,
             TrafficDirection.OUTBOUND, 12000000, 3000000, "firefox"),
            ("192.168.1.100", 22, "192.168.1.50", 42312, Protocol.SSH,
             TrafficDirection.INBOUND, 2500000, 1200000, "sshd"),
            ("192.168.1.100", 53, "8.8.8.8", 53, Protocol.DNS,
             TrafficDirection.OUTBOUND, 500000, 200000, "systemd-resolved"),
            ("192.168.1.100", 8080, "192.168.1.50", 51234, Protocol.HTTP,
             TrafficDirection.INBOUND, 12000000, 3000000, "code-server"),
            ("192.168.1.100", 3000, "127.0.0.1", 54321, Protocol.TCP,
             TrafficDirection.LOCAL, 800000, 200000, "node"),
            ("192.168.1.100", 8443, "10.0.0.5", 8443, Protocol.QUIC,
             TrafficDirection.OUTBOUND, 28000000, 5000000, "firefox"),
            ("192.168.1.100", 5060, "10.0.0.10", 5060, Protocol.UDP,
             TrafficDirection.INBOUND, 1500000, 1500000, "discord"),
        ]
        for src, sport, dst, dport, proto, direction, rx, tx, proc in sample_flows:
            self.flows.append(TrafficFlow(
                src_ip=src, src_port=sport, dst_ip=dst, dst_port=dport,
                protocol=proto, direction=direction, rx_bytes=rx, tx_bytes=tx,
                process=proc, first_seen=now - random.uniform(3600, 86400),
                last_seen=now - random.uniform(0, 300),
                duration_s=random.uniform(60, 7200)))

        for proto in Protocol:
            flows = [f for f in self.flows if f.protocol == proto]
            if flows:
                self.protocol_stats[proto.value] = ProtocolStats(
                    protocol=proto,
                    rx_bytes=sum(f.rx_bytes for f in flows),
                    tx_bytes=sum(f.tx_bytes for f in flows),
                    connections=len(flows))

        self.total_rx = sum(f.rx_bytes for f in self.flows)
        self.total_tx = sum(f.tx_bytes for f in self.flows)

        total = self.total_rx + self.total_tx
        for key, stats in self.protocol_stats.items():
            stats_copy = ProtocolStats(protocol=stats.protocol)
            stats_copy.rx_bytes = stats.rx_bytes
            stats_copy.tx_bytes = stats.tx_bytes
            stats_copy.connections = stats.connections
            stats_copy.avg_packet_size = 0
            self.protocol_stats[key] = stats_copy

        for i in range(60):
            self.bandwidth_history.append(BandwidthSample(
                timestamp=now - (60 - i) * 60,
                rx_rate=random.uniform(500000, 20000000),
                tx_rate=random.uniform(100000, 5000000),
                total_rate=random.uniform(600000, 25000000)))

        self.geo_locations = [
            GeoLocation(ip="140.82.121.3", country="US", city="San Francisco",
                         lat=37.77, lon=-122.42, org="GitHub Inc.", flag="🇺🇸"),
            GeoLocation(ip="8.8.8.8", country="US", city="Mountain View",
                         lat=37.39, lon=-122.08, org="Google LLC", flag="🇺🇸"),
            GeoLocation(ip="151.101.1.69", country="US", city="San Francisco",
                         lat=37.77, lon=-122.42, org="Fastly", flag="🇺🇸"),
            GeoLocation(ip="10.0.0.5", country="US", city="Local",
                         lat=0, lon=0, org="Private Network", flag="🏠"),
        ]

    def get_protocol_stats(self) -> List[ProtocolStats]:
        return sorted(self.protocol_stats.values(), key=lambda s: s.total_bytes, reverse=True)

    def get_flows_by_protocol(self, protocol: Protocol) -> List[TrafficFlow]:
        return [f for f in self.flows if f.protocol == protocol]

    def get_flows_by_process(self, process: str) -> List[TrafficFlow]:
        return [f for f in self.flows if f.process.lower() == process.lower()]

    def search_flows(self, query: str) -> List[TrafficFlow]:
        q = query.lower()
        return [f for f in self.flows if q in f.process.lower()
                or q in f.src_ip or q in f.dst_ip]

    def get_top_flows(self, limit: int = 5) -> List[TrafficFlow]:
        return sorted(self.flows, key=lambda f: f.rx_bytes + f.tx_bytes, reverse=True)[:limit]

    def get_bandwidth_summary(self) -> Dict:
        if not self.bandwidth_history:
            return {}
        latest = self.bandwidth_history[-1]
        return {
            "rx_rate": BandwidthSample(rx_rate=latest.rx_rate).rate_display,
            "tx_rate": BandwidthSample(tx_rate=latest.tx_rate).rate_display,
            "total_rx": f"{self.total_rx / (1024 ** 3):.2f} GB",
            "total_tx": f"{self.total_tx / (1024 ** 3):.2f} GB",
        }

    def get_stats(self) -> Dict:
        return {
            "protocols": len(self.protocol_stats),
            "flows": len(self.flows),
            "total_rx_gb": round(self.total_rx / (1024 ** 3), 2),
            "total_tx_gb": round(self.total_tx / (1024 ** 3), 2),
            "bandwidth_samples": len(self.bandwidth_history),
            "geo_locations": len(self.geo_locations),
        }
