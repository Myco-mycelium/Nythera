from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class TestPhase(Enum):
    IDLE = "idle"
    PING = "ping"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    COMPLETE = "complete"


class ServerRegion(Enum):
    NORTH_AMERICA = "North America"
    EUROPE = "Europe"
    ASIA_PACIFIC = "Asia Pacific"
    SOUTH_AMERICA = "South America"
    MIDDLE_EAST = "Middle East"
    AFRICA = "Africa"


@dataclass
class SpeedServer:
    name: str
    city: str
    country: str
    region: ServerRegion
    host: str
    distance_km: int
    ping_ms: float = 0
    jitter_ms: float = 0
    download_mbps: float = 0
    upload_mbps: float = 0

    @property
    def ping_bar(self) -> str:
        if self.ping_ms == 0:
            return "░" * 20
        filled = min(int(self.ping_ms / 5), 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def ping_status(self) -> str:
        if self.ping_ms == 0:
            return "N/A"
        if self.ping_ms < 20:
            return "Excellent"
        if self.ping_ms < 50:
            return "Good"
        if self.ping_ms < 100:
            return "Fair"
        return "Poor"

    @property
    def distance_display(self) -> str:
        if self.distance_km >= 1000:
            return f"{self.distance_km / 1000:.0f}k km"
        return f"{self.distance_km} km"


@dataclass
class TestResult:
    timestamp: float
    phase: TestPhase
    value: float
    unit: str
    server: str = ""

    @property
    def value_display(self) -> str:
        if self.value >= 1000:
            return f"{self.value / 1000:.2f} G{self.unit}"
        return f"{self.value:.1f} {self.unit}"


@dataclass
class PingResult:
    host: str
    packets_sent: int
    packets_received: int
    min_ms: float
    max_ms: float
    avg_ms: float
    jitter_ms: float
    timestamp: float

    @property
    def packet_loss(self) -> float:
        if self.packets_sent == 0:
            return 100
        return (1 - self.packets_received / self.packets_sent) * 100

    @property
    def loss_display(self) -> str:
        loss = self.packet_loss
        if loss == 0:
            return "0%"
        if loss < 5:
            return f"{loss:.1f}%"
        return f"{loss:.1f}% ⚠️"

    @property
    def latency_bar(self) -> str:
        filled = min(int(self.avg_ms / 5), 20)
        return "█" * filled + "░" * (20 - filled)


class SpeedTest:
    def __init__(self):
        self._servers: list[SpeedServer] = []
        self._selected_server: int = 0
        self._phase: TestPhase = TestPhase.IDLE
        self._progress: float = 0.0
        self._results: list[TestResult] = []
        self._ping_results: list[PingResult] = []
        self._selected_result: int = 0
        self._is_running: bool = False
        self._test_history: list = []
        self._auto_server: bool = True
        self._test_size_mb: int = 100
        self._view: str = "test"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._servers = [
            SpeedServer("Nyrqis East", "New York", "US", ServerRegion.NORTH_AMERICA, "nyrqis-east.nyrqis.dev", 0, 12.3, 2.1, 945.2, 423.8),
            SpeedServer("Nyrqis West", "San Francisco", "US", ServerRegion.NORTH_AMERICA, "nyrqis-west.nyrqis.dev", 4100, 48.5, 8.3, 876.4, 412.1),
            SpeedServer("Nyrqis EU", "London", "UK", ServerRegion.EUROPE, "nyrqis-eu.nyrqis.dev", 5570, 82.1, 12.5, 712.3, 389.2),
            SpeedServer("Nyrqis APAC", "Tokyo", "JP", ServerRegion.ASIA_PACIFIC, "nyrqis-apac.nyrqis.dev", 10840, 145.2, 22.1, 623.8, 312.5),
            SpeedServer("Nyrqis SA", "São Paulo", "BR", ServerRegion.SOUTH_AMERICA, "nyrqis-sa.nyrqis.dev", 7690, 128.4, 18.7, 534.2, 267.8),
            SpeedServer("Nyrqis ME", "Dubai", "AE", ServerRegion.MIDDLE_EAST, "nyrqis-me.nyrqis.dev", 11200, 165.3, 28.4, 489.1, 234.5),
        ]
        self._results = [
            TestResult(now - 3600, TestPhase.DOWNLOAD, 945.2, "Mbps", "Nyrqis East"),
            TestResult(now - 3600, TestPhase.UPLOAD, 423.8, "Mbps", "Nyrqis East"),
            TestResult(now - 86400, TestPhase.DOWNLOAD, 876.4, "Mbps", "Nyrqis West"),
            TestResult(now - 86400, TestPhase.UPLOAD, 412.1, "Mbps", "Nyrqis West"),
        ]
        self._ping_results = [
            PingResult("8.8.8.8", 100, 100, 8.2, 15.4, 11.3, 2.1, now - 3600),
            PingResult("1.1.1.1", 100, 99, 7.8, 12.1, 9.5, 1.8, now - 3600),
            PingResult("nyrqis-east.nyrqis.dev", 100, 100, 10.1, 18.2, 12.3, 2.5, now - 3600),
        ]
        self._test_history = [
            {"time": now - 3600, "download": 945.2, "upload": 423.8, "ping": 12.3},
            {"time": now - 7200, "download": 912.4, "upload": 418.5, "ping": 13.1},
            {"time": now - 10800, "download": 898.7, "upload": 415.2, "ping": 12.8},
            {"time": now - 86400, "download": 876.4, "upload": 412.1, "ping": 14.2},
            {"time": now - 172800, "download": 856.3, "upload": 408.9, "ping": 13.5},
        ]

    @property
    def selected_server(self) -> Optional[SpeedServer]:
        if 0 <= self._selected_server < len(self._servers):
            return self._servers[self._selected_server]
        return None

    @property
    def best_server(self) -> Optional[SpeedServer]:
        if not self._servers:
            return None
        return min(self._servers, key=lambda s: s.ping_ms if s.ping_ms > 0 else float('inf'))

    @property
    def total_tests(self) -> int:
        return len(self._test_history)

    def select_server(self, idx: int):
        if 0 <= idx < len(self._servers):
            self._selected_server = idx

    def start_test(self):
        self._is_running = True
        self._phase = TestPhase.PING
        self._progress = 0

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS SPEED TEST                                      ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        phase_icons = {"idle": "⏹", "ping": "📡", "download": "⬇️", "upload": "⬆️", "complete": "✅"}
        lines.append(f"  Status: {phase_icons[self._phase.value]} {self._phase.value.upper()}  Auto: {'ON' if self._auto_server else 'OFF'}  Size: {self._test_size_mb}MB")
        if self._is_running:
            bar_len = int(self._progress * 40)
            bar = "█" * bar_len + "░" * (40 - bar_len)
            lines.append(f"  Progress: [{bar}] {self._progress:.0%}")
        lines.append("")
        if self._servers:
            s = self._servers[self._selected_server]
            lines.append(f"  ── Best Server ──")
            best = self.best_server
            if best:
                lines.append(f"  🏆 {best.name} ({best.city}, {best.country})  Ping: {best.ping_ms:.1f}ms  DL: {best.download_mbps:.1f} Mbps  UL: {best.upload_mbps:.1f} Mbps")
        lines.append("")
        lines.append(f"  ── Servers ({len(self._servers)}) ──")
        for i, srv in enumerate(self._servers):
            sel = "▶" if i == self._selected_server else " "
            lines.append(f"  {sel} {srv.name:<18s} {srv.city:<15s} {srv.ping_ms:>6.1f}ms  ↓{srv.download_mbps:>7.1f}  ↑{srv.upload_mbps:>7.1f}  {srv.distance_display}")
        lines.append("")
        lines.append(f"  ── Recent Results ──")
        for r in self._results[-4:]:
            icon = "⬇️" if r.phase == TestPhase.DOWNLOAD else "⬆️"
            age = int((time.time() - r.timestamp) / 3600)
            lines.append(f"  {icon} {r.value_display:>15s}  {r.server}  {age}h ago")
        lines.append("")
        lines.append("  [T]est  [S]erver  [P]ing  [H]istory  [E]xport  [A]uto-select")
        return lines

    def render_ping(self) -> list:
        lines = []
        lines.append("  ── Ping Results ──")
        lines.append("")
        for p in self._ping_results:
            lines.append(f"  {p.host}")
            lines.append(f"    Latency: [{p.latency_bar}] {p.avg_ms:.1f}ms (min: {p.min_ms:.1f} / max: {p.max_ms:.1f})")
            lines.append(f"    Jitter: {p.jitter_ms:.1f}ms  Loss: {p.loss_display}  Packets: {p.packets_received}/{p.packets_sent}")
        return lines

    def render_history(self) -> list:
        lines = []
        lines.append("  ── Test History ──")
        lines.append("")
        lines.append(f"  {'Time':<12s} {'Download':>10s} {'Upload':>10s} {'Ping':>8s}")
        lines.append(f"  {'─'*12} {'─'*10} {'─'*10} {'─'*8}")
        for h in self._test_history:
            age = int((time.time() - h["time"]) / 3600)
            lines.append(f"  {age:>4d}h ago    {h['download']:>8.1f} M  {h['upload']:>8.1f} M  {h['ping']:>6.1f}ms")
        lines.append("")
        if self._test_history:
            avg_dl = sum(h["download"] for h in self._test_history) / len(self._test_history)
            avg_ul = sum(h["upload"] for h in self._test_history) / len(self._test_history)
            avg_ping = sum(h["ping"] for h in self._test_history) / len(self._test_history)
            lines.append(f"  Average:  ↓{avg_dl:.1f} Mbps  ↑{avg_ul:.1f} Mbps  {avg_ping:.1f}ms")
        return lines
