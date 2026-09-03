"""Network Speed Test — Ping, traceroute, and bandwidth measurement.

Features:
- Ping with latency, jitter, and packet loss tracking
- Traceroute with hop-by-hop visualization
- Download/upload bandwidth measurement
- Server selection with geographic regions
- Historical results with graph
- Network quality score
- Connection quality indicators
"""

from __future__ import annotations

import time
import random
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class TestState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def icon(self) -> str:
        icons = {
            TestState.IDLE: "⏹", TestState.RUNNING: "🔄",
            TestState.COMPLETED: "✅", TestState.FAILED: "❌",
        }
        return icons.get(self, "?")


@dataclass
class PingResult:
    host: str = ""
    ip: str = ""
    packets_sent: int = 0
    packets_received: int = 0
    min_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    jitter_ms: float = 0.0
    timestamps: List[float] = field(default_factory=list)

    @property
    def packet_loss_pct(self) -> float:
        if self.packets_sent == 0:
            return 0.0
        return (self.packets_sent - self.packets_received) / self.packets_sent * 100

    @property
    def packet_loss_str(self) -> str:
        return f"{self.packet_loss_pct:.1f}%"

    @property
    def loss_bar(self) -> str:
        pct = self.packet_loss_pct
        filled = min(20, int(pct / 5))
        return "█" * (20 - filled) + "░" * filled

    @property
    def latency_bar(self) -> str:
        filled = min(20, int(self.avg_ms / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def quality(self) -> str:
        if self.avg_ms < 20 and self.packet_loss_pct < 1:
            return "Excellent 🟢"
        if self.avg_ms < 50 and self.packet_loss_pct < 3:
            return "Good 🟡"
        if self.avg_ms < 100 and self.packet_loss_pct < 5:
            return "Fair 🟠"
        return "Poor 🔴"

    @property
    def latency_history(self) -> str:
        if not self.timestamps:
            return ""
        blocks = "▁▂▃▄▅▆▇█"
        result = ""
        max_val = max(self.timestamps) if self.timestamps else 1
        for i in range(0, min(len(self.timestamps), 30)):
            val = self.timestamps[i]
            idx = min(7, int(val / max(max_val, 0.1) * 7))
            result += blocks[idx]
        return result


@dataclass
class TraceHop:
    hop: int = 0
    hostname: str = ""
    ip: str = ""
    region: str = ""
    latency_ms: float = 0.0
    timed_out: bool = False

    @property
    def latency_str(self) -> str:
        if self.timed_out:
            return "* * *"
        return f"{self.latency_ms:.1f}ms"

    @property
    def latency_bar(self) -> str:
        filled = min(20, int(self.latency_ms / 10))
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        if self.timed_out:
            return "⏰"
        return "🟢"


@dataclass
class Server:
    name: str = ""
    location: str = ""
    region: str = ""
    ip: str = ""
    distance_km: int = 0
    ping_ms: float = 0.0
    selected: bool = False

    @property
    def flag(self) -> str:
        flags = {
            "US": "🇺🇸", "EU": "🇪🇺", "JP": "🇯🇵", "AU": "🇦🇺",
            "BR": "🇧🇷", "IN": "🇮🇳", "SG": "🇸🇬", "DE": "🇩🇪",
        }
        return flags.get(self.region, "🌐")


@dataclass
class BandwidthResult:
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    ping_ms: float = 0.0
    server: str = ""
    timestamp: float = 0.0
    duration_s: float = 0.0

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def download_bar(self) -> str:
        filled = min(20, int(self.download_mbps / 50))
        return "█" * filled + "░" * (20 - filled)

    @property
    def upload_bar(self) -> str:
        filled = min(20, int(self.upload_mbps / 50))
        return "█" * filled + "░" * (20 - filled)

    @property
    def quality_score(self) -> float:
        score = 0
        score += min(30, self.download_mbps / 10)
        score += min(30, self.upload_mbps / 10)
        score += max(0, 40 - self.ping_ms / 5)
        return min(100, score)

    @property
    def quality_bar(self) -> str:
        filled = min(20, int(self.quality_score / 5))
        return "█" * filled + "░" * (20 - filled)


class NetSpeedTest:
    def __init__(self):
        self._servers: List[Server] = []
        self._ping_results: List[PingResult] = []
        self._traceroute: List[TraceHop] = []
        self._bandwidth_history: List[BandwidthResult] = []
        self._current_bandwidth: Optional[BandwidthResult] = None
        self._test_state: TestState = TestState.IDLE
        self._selected_server: int = 0
        self._view_mode: str = "speed"  # speed, ping, traceroute, servers, history
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Servers
        self._servers = [
            Server("Nyrqis HQ", "San Francisco", "US", "192.168.1.1", 0, 1.2),
            Server("AWS West", "Los Angeles", "US", "52.94.236.248", 600, 8.5),
            Server("AWS East", "N. Virginia", "US", "54.239.28.85", 4000, 45.2),
            Server("Cloudflare", "London", "EU", "172.64.32.1", 8600, 125.3),
            Server("AWS Tokyo", "Tokyo", "JP", "13.112.0.1", 8300, 118.7),
            Server("AWS Sydney", "Sydney", "AU", "13.55.0.1", 12000, 165.2),
            Server("AWS São Paulo", "São Paulo", "BR", "54.239.2.85", 10500, 195.8),
            Server("AWS Singapore", "Singapore", "SG", "13.228.0.1", 13600, 172.1),
        ]
        self._servers[0].selected = True

        # Ping result
        self._ping_results = [
            PingResult("nyrqis.dev", "192.168.1.100", 100, 100, 1.1, 3.2, 8.5, 1.5,
                       timestamps=[random.uniform(1, 8) for _ in range(60)]),
            PingResult("8.8.8.8", "8.8.8.8", 100, 100, 8.2, 12.5, 25.0, 3.2,
                       timestamps=[random.uniform(8, 25) for _ in range(60)]),
        ]

        # Traceroute
        self._traceroute = [
            TraceHop(1, "gateway.local", "192.168.1.1", "Local", 1.2),
            TraceHop(2, "isp-router.isp.net", "10.0.0.1", "Local", 5.8),
            TraceHop(3, "core-router.isp.net", "172.16.0.1", "Regional", 12.3),
            TraceHop(4, "ix-peer.amazon.com", "206.82.112.1", "Regional", 15.8),
            TraceHop(5, "ec2-52-94-236-248.compute.amazonaws.com", "52.94.236.248", "Remote", 18.2),
            TraceHop(6, "", "", "", 0, True),
            TraceHop(7, "nyrqis-app.internal", "10.0.1.50", "Remote", 22.1),
        ]

        # Bandwidth history
        self._bandwidth_history = [
            BandwidthResult(245.8, 89.2, 3.2, "Nyrqis HQ", now - 86400 * 7, 30),
            BandwidthResult(238.5, 85.1, 3.5, "Nyrqis HQ", now - 86400 * 3, 30),
            BandwidthResult(242.1, 87.8, 3.1, "Nyrqis HQ", now - 86400, 30),
            BandwidthResult(251.3, 91.5, 2.9, "Nyrqis HQ", now - 3600, 30),
        ]

        self._current_bandwidth = BandwidthResult(
            248.5, 90.3, 3.0, "Nyrqis HQ", now, 30,
        )

    @property
    def quality_score(self) -> float:
        if self._current_bandwidth:
            return self._current_bandwidth.quality_score
        return 0.0

    @property
    def avg_download(self) -> float:
        if not self._bandwidth_history:
            return 0.0
        return sum(b.download_mbps for b in self._bandwidth_history) / len(self._bandwidth_history)

    @property
    def avg_upload(self) -> float:
        if not self._bandwidth_history:
            return 0.0
        return sum(b.upload_mbps for b in self._bandwidth_history) / len(self._bandwidth_history)

    def select_server(self, idx: int):
        if 0 <= idx < len(self._servers):
            self._selected_server = idx

    def set_view(self, mode: str):
        if mode in ("speed", "ping", "traceroute", "servers", "history"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS NETWORK SPEED TEST                              ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🌐 Server: {self._servers[self._selected_server].name}  ⚡ State: {self._test_state.icon} {self._test_state.value}  📊 Score: {self.quality_score:.0f}/100  📜 {len(self._bandwidth_history)} tests")
        lines.append("")

        if self._view_mode == "speed":
            bw = self._current_bandwidth
            if bw:
                lines.append(f"  ── Speed Results ──")
                lines.append(f"  ↓ Download: [{bw.download_bar}] {bw.download_mbps:.1f} Mbps")
                lines.append(f"  ↑ Upload:   [{bw.upload_bar}] {bw.upload_mbps:.1f} Mbps")
                lines.append(f"  ⏱ Ping:     {bw.ping_ms:.1f}ms")
                lines.append(f"  📊 Quality: [{bw.quality_bar}] {bw.quality_score:.0f}/100")
                lines.append("")
                lines.append(f"  Server: {bw.server}  Duration: {bw.duration_s}s  {bw.time_str}")
            lines.append("")
            # Quick stats
            lines.append(f"  ── Historical Average ──")
            lines.append(f"  ↓ Avg Download: {self.avg_download:.1f} Mbps  ↑ Avg Upload: {self.avg_upload:.1f} Mbps")

        elif self._view_mode == "ping":
            lines.append("  ── Ping Results ──")
            for pr in self._ping_results:
                lines.append(f"  🌐 {pr.host} ({pr.ip})")
                lines.append(f"     Packets: {pr.packets_sent} sent, {pr.packets_received} received, {pr.packet_loss_str} loss")
                lines.append(f"     Latency: [{pr.latency_bar}] min:{pr.min_ms:.1f} avg:{pr.avg_ms:.1f} max:{pr.max_ms:.1f}ms  Jitter: {pr.jitter_ms:.1f}ms")
                lines.append(f"     History: {pr.latency_history}")
                lines.append(f"     Quality: {pr.quality}")

        elif self._view_mode == "traceroute":
            lines.append("  ── Traceroute ──")
            lines.append("  Hop  Status  Latency       Hostname                              IP")
            lines.append("  ───  ──────  ──────────    ────────────────────────────────────  ──────────────")
            for hop in self._traceroute:
                status = hop.status_icon
                lines.append(f"  {hop.hop:>3d}  {status}     {hop.latency_str:<12s}  {hop.hostname[:36]:<36s}  {hop.ip}")

        elif self._view_mode == "servers":
            lines.append("  ── Server List ──")
            for i, s in enumerate(self._servers):
                sel = "▶" if i == self._selected_server else " "
                active = "🟢" if s.selected else "⚪"
                lines.append(f"  {sel}{active} {s.flag} {s.name:<20s} {s.location:<16s} {s.region}  {s.distance_km:>6d}km  {s.ping_ms:.1f}ms")

        elif self._view_mode == "history":
            lines.append("  ── Bandwidth History ──")
            for bw in self._bandwidth_history[:8]:
                lines.append(f"  📅 {bw.time_str}  ↓{bw.download_mbps:>6.1f}Mbps  ↑{bw.upload_mbps:>6.1f}Mbps  ⏱{bw.ping_ms:.1f}ms  [{bw.quality_bar}] {bw.quality_score:.0f}/100")

        lines.append("")
        lines.append("  [S]peed [P]ing [T]raceroute [E]servers [H]istory [↑↓]Nav [R]un test")
        return lines
