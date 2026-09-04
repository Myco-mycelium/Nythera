"""
Nyrqis OS - Network Monitoring Dashboard
Bandwidth graphs, connection table, and latency tracking.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ConnectionState(Enum):
    ESTABLISHED = "established"
    LISTEN = "listen"
    TIME_WAIT = "time_wait"
    CLOSE_WAIT = "close_wait"
    SYN_SENT = "syn_sent"
    SYN_RECV = "syn_recv"
    FINISHED = "finished"


class Protocol(Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    DNS = "dns"
    HTTP = "http"
    HTTPS = "https"
    SSH = "ssh"
    TLS = "tls"


@dataclass
class BandwidthSample:
    timestamp: float = 0.0
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate_bps: float = 0.0
    tx_rate_bps: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def rx_display(self) -> str:
        bps = self.rx_rate_bps
        if bps < 1000:
            return f"{bps:.0f} B/s"
        elif bps < 1000000:
            return f"{bps / 1000:.1f} KB/s"
        elif bps < 1000000000:
            return f"{bps / 1000000:.1f} MB/s"
        return f"{bps / 1000000000:.2f} GB/s"

    @property
    def tx_display(self) -> str:
        bps = self.tx_rate_bps
        if bps < 1000:
            return f"{bps:.0f} B/s"
        elif bps < 1000000:
            return f"{bps / 1000:.1f} KB/s"
        elif bps < 1000000000:
            return f"{bps / 1000000:.1f} MB/s"
        return f"{bps / 1000000000:.2f} GB/s"


@dataclass
class Connection:
    local_addr: str = ""
    local_port: int = 0
    remote_addr: str = ""
    remote_port: int = 0
    state: ConnectionState = ConnectionState.ESTABLISHED
    protocol: Protocol = Protocol.TCP
    pid: int = 0
    process: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0
    uptime_s: float = 0.0

    @property
    def state_icon(self) -> str:
        icons = {
            ConnectionState.ESTABLISHED: "🟢", ConnectionState.LISTEN: "🔵",
            ConnectionState.TIME_WAIT: "🟡", ConnectionState.CLOSE_WAIT: "🟠",
            ConnectionState.SYN_SENT: "⚪", ConnectionState.SYN_RECV: "⚪",
            ConnectionState.FINISHED: "🔴",
        }
        return icons.get(self.state, "?")

    @property
    def local_display(self) -> str:
        return f"{self.local_addr}:{self.local_port}"

    @property
    def remote_display(self) -> str:
        return f"{self.remote_addr}:{self.remote_port}"

    @property
    def rx_display(self) -> str:
        if self.rx_bytes < 1024:
            return f"{self.rx_bytes} B"
        elif self.rx_bytes < 1024 * 1024:
            return f"{self.rx_bytes / 1024:.1f} KB"
        elif self.rx_bytes < 1024 * 1024 * 1024:
            return f"{self.rx_bytes / (1024 * 1024):.1f} MB"
        return f"{self.rx_bytes / (1024 * 1024 * 1024):.2f} GB"

    @property
    def tx_display(self) -> str:
        if self.tx_bytes < 1024:
            return f"{self.tx_bytes} B"
        elif self.tx_bytes < 1024 * 1024:
            return f"{self.tx_bytes / 1024:.1f} KB"
        elif self.tx_bytes < 1024 * 1024 * 1024:
            return f"{self.tx_bytes / (1024 * 1024):.1f} MB"
        return f"{self.tx_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class LatencyProbe:
    host: str = ""
    ip: str = ""
    avg_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss_pct: float = 0.0
    hops: int = 0
    last_probe: float = 0.0

    @property
    def latency_status(self) -> str:
        if self.avg_ms < 20:
            return "🟢 Excellent"
        elif self.avg_ms < 50:
            return "🟡 Good"
        elif self.avg_ms < 100:
            return "🟠 Fair"
        return "🔴 Poor"

    @property
    def latency_bar(self) -> str:
        pct = min(100, self.avg_ms / 2)
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class NetworkInterface:
    name: str = ""
    ip_address: str = ""
    mac_address: str = ""
    netmask: str = ""
    gateway: str = ""
    dns: List[str] = field(default_factory=list)
    speed_mbps: int = 0
    rx_total: int = 0
    tx_total: int = 0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    is_up: bool = True
    is_wireless: bool = False
    ssid: str = ""
    signal_strength: int = 0

    @property
    def status_icon(self) -> str:
        return "🟢" if self.is_up else "🔴"

    @property
    def rx_total_display(self) -> str:
        if self.rx_total < 1024 * 1024:
            return f"{self.rx_total / 1024:.1f} KB"
        elif self.rx_total < 1024 * 1024 * 1024:
            return f"{self.rx_total / (1024 * 1024):.1f} MB"
        return f"{self.rx_total / (1024 * 1024 * 1024):.2f} GB"


class NetworkMonitor:
    def __init__(self):
        self.interfaces: List[NetworkInterface] = []
        self.connections: List[Connection] = []
        self.probes: List[LatencyProbe] = []
        self.bandwidth_history: List[BandwidthSample] = []
        self.total_rx: int = 0
        self.total_tx: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        self.interfaces = [
            NetworkInterface(name="eth0", ip_address="192.168.1.100",
                             mac_address="00:1a:2b:3c:4d:5e", netmask="255.255.255.0",
                             gateway="192.168.1.1", dns=["8.8.8.8", "1.1.1.1"],
                             speed_mbps=1000, rx_total=52000000000, tx_total=8500000000,
                             rx_rate=12500000, tx_rate=2500000, is_up=True),
            NetworkInterface(name="wlan0", ip_address="192.168.1.101",
                             mac_address="00:1a:2b:3c:4d:5f", netmask="255.255.255.0",
                             gateway="192.168.1.1", dns=["8.8.8.8", "1.1.1.1"],
                             speed_mbps=300, rx_total=15000000000, tx_total=3200000000,
                             rx_rate=5000000, tx_rate=800000, is_up=True,
                             is_wireless=True, ssid="Nyrqis-5G", signal_strength=85),
        ]

        now = time.time()
        self.connections = [
            Connection(local_addr="192.168.1.100", local_port=22, remote_addr="192.168.1.50",
                       remote_port=42312, state=ConnectionState.ESTABLISHED, protocol=Protocol.SSH,
                       pid=1024, process="sshd", rx_bytes=2500000, tx_bytes=1200000, uptime_s=3600),
            Connection(local_addr="192.168.1.100", local_port=443, remote_addr="140.82.121.3",
                       remote_port=443, state=ConnectionState.ESTABLISHED, protocol=Protocol.HTTPS,
                       pid=200, process="firefox", rx_bytes=45000000, tx_bytes=8000000, uptime_s=1800),
            Connection(local_addr="192.168.1.100", local_port=8080, remote_addr="192.168.1.50",
                       remote_port=51234, state=ConnectionState.ESTABLISHED, protocol=Protocol.HTTP,
                       pid=300, process="code-server", rx_bytes=12000000, tx_bytes=3000000, uptime_s=7200),
            Connection(local_addr="192.168.1.100", local_port=53, remote_addr="8.8.8.8",
                       remote_port=53, state=ConnectionState.ESTABLISHED, protocol=Protocol.DNS,
                       pid=101, process="systemd-resolved", rx_bytes=500000, tx_bytes=200000),
            Connection(local_addr="0.0.0.0", local_port=22, remote_addr="",
                       remote_port=0, state=ConnectionState.LISTEN, protocol=Protocol.TCP,
                       pid=1024, process="sshd"),
            Connection(local_addr="192.168.1.100", local_port=43210, remote_addr="10.0.0.5",
                       remote_port=443, state=ConnectionState.TIME_WAIT, protocol=Protocol.TCP,
                       pid=200, process="firefox", rx_bytes=800000, tx_bytes=100000),
        ]

        self.probes = [
            LatencyProbe(host="Google DNS", ip="8.8.8.8", avg_ms=12.5,
                          min_ms=10.2, max_ms=18.3, jitter_ms=2.1,
                          packet_loss_pct=0.0, hops=4, last_probe=now - 60),
            LatencyProbe(host="Cloudflare", ip="1.1.1.1", avg_ms=8.3,
                          min_ms=6.8, max_ms=12.1, jitter_ms=1.5,
                          packet_loss_pct=0.0, hops=3, last_probe=now - 60),
            LatencyProbe(host="GitHub", ip="140.82.121.3", avg_ms=35.2,
                          min_ms=28.5, max_ms=45.8, jitter_ms=5.2,
                          packet_loss_pct=0.0, hops=8, last_probe=now - 120),
            LatencyProbe(host="Local Gateway", ip="192.168.1.1", avg_ms=1.2,
                          min_ms=0.8, max_ms=2.5, jitter_ms=0.3,
                          packet_loss_pct=0.0, hops=1, last_probe=now - 30),
            LatencyProbe(host="AWS us-east-1", ip="52.94.236.248", avg_ms=65.0,
                          min_ms=55.2, max_ms=82.5, jitter_ms=8.5,
                          packet_loss_pct=0.5, hops=12, last_probe=now - 180),
        ]

        for i in range(30):
            self.bandwidth_history.append(BandwidthSample(
                timestamp=now - (30 - i) * 60,
                rx_bytes=random.randint(500000, 20000000),
                tx_bytes=random.randint(100000, 5000000),
                rx_rate_bps=random.uniform(1000000, 20000000),
                tx_rate_bps=random.uniform(500000, 5000000)))

        self.total_rx = sum(s.rx_bytes for s in self.bandwidth_history)
        self.total_tx = sum(s.tx_bytes for s in self.bandwidth_history)

    def get_connections_by_state(self, state: ConnectionState) -> List[Connection]:
        return [c for c in self.connections if c.state == state]

    def get_connections_by_process(self, process: str) -> List[Connection]:
        return [c for c in self.connections if c.process.lower() == process.lower()]

    def search_connections(self, query: str) -> List[Connection]:
        q = query.lower()
        return [c for c in self.connections if q in c.process.lower()
                or q in c.remote_addr or q in c.remote_display]

    def get_bandwidth_summary(self) -> Dict:
        if not self.bandwidth_history:
            return {}
        latest = self.bandwidth_history[-1]
        return {
            "rx_rate": latest.rx_display,
            "tx_rate": latest.tx_display,
            "rx_total": f"{self.total_rx / (1024 * 1024 * 1024):.2f} GB",
            "tx_total": f"{self.total_tx / (1024 * 1024 * 1024):.2f} GB",
            "connections": len(self.connections),
        }

    def get_stats(self) -> Dict:
        return {
            "interfaces": len(self.interfaces),
            "connections": len(self.connections),
            "probes": len(self.probes),
            "bandwidth_samples": len(self.bandwidth_history),
            "total_rx_gb": round(self.total_rx / (1024 ** 3), 2),
            "total_tx_gb": round(self.total_tx / (1024 ** 3), 2),
        }


class InterfaceType(Enum):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    LOOPBACK = "loopback"
    BRIDGE = "bridge"
    TUNNEL = "tunnel"


class InterfaceStatus:
    pass  # backward compat stub
