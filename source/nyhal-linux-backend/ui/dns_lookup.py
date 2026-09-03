"""DNS Lookup Tool — dig/nslookup simulation, history, and record visualization.

Features:
- DNS record type lookup (A, AAAA, MX, CNAME, NS, TXT, SOA, SRV, PTR, CAA)
- dig/nslookup simulation output
- Query history with timestamps
- DNS propagation checker
- TTL visualization
- Reverse DNS lookup
- DNS benchmark (response time tracking)
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class DNSRecordType(Enum):
    A = "A"
    AAAA = "AAAA"
    MX = "MX"
    CNAME = "CNAME"
    NS = "NS"
    TXT = "TXT"
    SOA = "SOA"
    SRV = "SRV"
    PTR = "PTR"
    CAA = "CAA"

    @property
    def icon(self) -> str:
        icons = {
            DNSRecordType.A: "🌐", DNSRecordType.AAAA: "🌐", DNSRecordType.MX: "📧",
            DNSRecordType.CNAME: "🔗", DNSRecordType.NS: "🖥", DNSRecordType.TXT: "📝",
            DNSRecordType.SOA: "📋", DNSRecordType.SRV: "📡", DNSRecordType.PTR: "↩",
            DNSRecordType.CAA: "🔒",
        }
        return icons.get(self, "?")


@dataclass
class DNSRecord:
    record_type: str = "A"
    name: str = ""
    value: str = ""
    ttl: int = 3600
    priority: int = 0
    class_value: str = "IN"

    @property
    def ttl_str(self) -> str:
        if self.ttl < 60:
            return f"{self.ttl}s"
        if self.ttl < 3600:
            return f"{self.ttl // 60}m"
        return f"{self.ttl // 3600}h"

    @property
    def ttl_bar(self) -> str:
        filled = min(20, self.ttl // 180)
        return "█" * filled + "░" * (20 - filled)

    @property
    def priority_str(self) -> str:
        return str(self.priority) if self.priority else ""

    @property
    def type_icon(self) -> str:
        icons = {
            "A": "🌐", "AAAA": "🌐", "MX": "📧", "CNAME": "🔗",
            "NS": "🖥", "TXT": "📝", "SOA": "📋", "SRV": "📡",
            "PTR": "↩", "CAA": "🔒",
        }
        return icons.get(self.record_type, "?")


@dataclass
class DNSQuery:
    domain: str = ""
    record_type: str = "A"
    timestamp: float = 0.0
    response_time_ms: float = 0.0
    records_found: int = 0
    records: List[DNSRecord] = field(default_factory=list)
    nameserver: str = ""
    status: str = "NOERROR"

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def date_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.timestamp))

    @property
    def status_icon(self) -> str:
        icons = {"NOERROR": "✅", "NXDOMAIN": "❌", "SERVFAIL": "⚠️", "REFUSED": "🚫"}
        return icons.get(self.status, "?")


@dataclass
class DNSPropagationNode:
    region: str = ""
    server: str = ""
    ip: str = ""
    resolved: bool = True
    response_time_ms: float = 0.0
    last_checked: float = 0.0

    @property
    def status_icon(self) -> str:
        return "✅" if self.resolved else "❌"

    @property
    def time_str(self) -> str:
        ago = time.time() - self.last_checked
        if ago < 60:
            return "just now"
        if ago < 3600:
            return f"{ago / 60:.0f}m ago"
        return f"{ago / 3600:.1f}h ago"


@dataclass
class DNSBenchmarkResult:
    nameserver: str = ""
    ip: str = ""
    avg_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    queries: int = 0
    failures: int = 0

    @property
    def speed_bar(self) -> str:
        filled = min(20, int(self.avg_ms / 10))
        return "█" * filled + "░" * (20 - filled)

    @property
    def success_rate(self) -> float:
        if self.queries == 0:
            return 0.0
        return (self.queries - self.failures) / self.queries * 100


class DNSLookup:
    def __init__(self):
        self._queries: List[DNSQuery] = []
        self._propagation: List[DNSPropagationNode] = []
        self._benchmarks: List[DNSBenchmarkResult] = []
        self._selected_query: int = 0
        self._view_mode: str = "lookup"  # lookup, history, propagation, benchmark
        self._current_domain: str = "nyrqis.dev"
        self._current_type: str = "A"
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Queries
        self._queries = [
            DNSQuery("nyrqis.dev", "A", now - 100, 12.5, 1,
                     [DNSRecord("A", "nyrqis.dev", "192.168.1.100", 3600)],
                     "8.8.8.8"),
            DNSQuery("nyrqis.dev", "AAAA", now - 200, 15.2, 1,
                     [DNSRecord("AAAA", "nyrqis.dev", "2001:db8::1", 3600)],
                     "8.8.8.8"),
            DNSQuery("nyrqis.dev", "MX", now - 300, 18.3, 2,
                     [DNSRecord("MX", "nyrqis.dev", "mail.nyrqis.dev", 3600, 10),
                      DNSRecord("MX", "nyrqis.dev", "mail2.nyrqis.dev", 3600, 20)],
                     "8.8.8.8"),
            DNSQuery("nyrqis.dev", "NS", now - 400, 11.1, 2,
                     [DNSRecord("NS", "nyrqis.dev", "ns1.nyrqis.dev", 86400),
                      DNSRecord("NS", "nyrqis.dev", "ns2.nyrqis.dev", 86400)],
                     "8.8.8.8"),
            DNSQuery("nyrqis.dev", "TXT", now - 500, 14.7, 3,
                     [DNSRecord("TXT", "nyrqis.dev", "v=spf1 include:_spf.nyrqis.dev ~all", 3600),
                      DNSRecord("TXT", "nyrqis.dev", "google-site-verification=abc123", 3600)],
                     "8.8.8.8"),
            DNSQuery("github.com", "A", now - 600, 8.2, 3,
                     [DNSRecord("A", "github.com", "140.82.121.4", 60),
                      DNSRecord("A", "github.com", "140.82.114.4", 60)],
                     "8.8.8.8"),
            DNSQuery("google.com", "A", now - 700, 5.1, 4,
                     [DNSRecord("A", "google.com", "142.250.190.78", 300)],
                     "8.8.8.8"),
        ]

        # Propagation
        self._propagation = [
            DNSPropagationNode("North America", "8.8.8.8", "Google DNS", True, 12.5, now - 100),
            DNSPropagationNode("North America", "1.1.1.1", "Cloudflare DNS", True, 8.3, now - 150),
            DNSPropagationNode("Europe", "8.8.4.4", "Google DNS EU", True, 45.2, now - 200),
            DNSPropagationNode("Europe", "185.228.168.9", "CleanBrowsing", True, 52.1, now - 250),
            DNSPropagationNode("Asia", "8.8.8.8", "Google DNS Asia", True, 85.3, now - 300),
            DNSPropagationNode("Asia", "101.226.4.6", "360 DNS", True, 120.5, now - 350),
            DNSPropagationNode("South America", "8.8.8.8", "Google DNS SA", True, 150.2, now - 400),
            DNSPropagationNode("Africa", "8.8.8.8", "Google DNS AF", False, 0, now - 500),
        ]

        # Benchmarks
        self._benchmarks = [
            DNSBenchmarkResult("Google DNS", "8.8.8.8", 12.5, 8.0, 25.0, 100, 2),
            DNSBenchmarkResult("Cloudflare DNS", "1.1.1.1", 8.3, 5.0, 15.0, 100, 0),
            DNSBenchmarkResult("OpenDNS", "208.67.222.222", 18.7, 12.0, 35.0, 100, 5),
            DNSBenchmarkResult("Quad9", "9.9.9.9", 15.2, 10.0, 28.0, 100, 1),
            DNSBenchmarkResult("AdGuard", "94.140.14.14", 22.1, 15.0, 40.0, 100, 3),
            DNSBenchmarkResult("NextDNS", "45.90.28.0", 25.5, 18.0, 45.0, 100, 8),
        ]
        self._benchmarks.sort(key=lambda b: b.avg_ms)

    @property
    def selected_query(self) -> Optional[DNSQuery]:
        if 0 <= self._selected_query < len(self._queries):
            return self._queries[self._selected_query]
        return None

    def select_query(self, idx: int):
        if 0 <= idx < len(self._queries):
            self._selected_query = idx

    def set_view(self, mode: str):
        if mode in ("lookup", "history", "propagation", "benchmark"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DNS LOOKUP TOOL                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🔍 {self._current_domain}  📋 {self._current_type}  📜 {len(self._queries)} queries  🌍 {len(self._propagation)} nodes  ⚡ {len(self._benchmarks)} servers benchmarked")
        lines.append("")

        if self._view_mode == "lookup":
            query = self.selected_query
            if query:
                lines.append(f"  ── Query: {query.domain} (Type: {query.record_type}) ──")
                lines.append(f"  Status: {query.status_icon} {query.status}  Response: {query.response_time_ms:.1f}ms  Server: {query.nameserver}")
                lines.append("")
                # dig-style output
                lines.append("  ;; ANSWER SECTION:")
                for rec in query.records:
                    lines.append(f"  {rec.name:<24s} {rec.ttl_str:>5s}  {rec.class_value:<4s} {rec.record_type:<6s} {rec.value}")
                if query.record_type == "MX":
                    lines.append("")
                    lines.append("  ;; MX Records (sorted by priority):")
                    for rec in query.records:
                        lines.append(f"  {rec.priority:>3d} {rec.value}")
            else:
                lines.append("  Enter a domain and record type to query")
                lines.append("  Supported types: A, AAAA, MX, NS, TXT, SOA, SRV, PTR, CAA")

        elif self._view_mode == "history":
            lines.append("  ── Query History ──")
            for i, q in enumerate(self._queries[:12]):
                sel = "▶" if i == self._selected_query else " "
                status = q.status_icon
                lines.append(f"  {sel}{status} {q.time_str} {q.domain:<24s} {q.record_type:<6s} {q.response_time_ms:>6.1f}ms  {q.records_found} records")

        elif self._view_mode == "propagation":
            lines.append("  ── DNS Propagation ──")
            lines.append(f"  Domain: {self._current_domain}")
            lines.append("")
            for node in self._propagation:
                lines.append(f"  {node.status_icon} {node.region:<16s} {node.server:<20s} {node.ip}  {node.response_time_ms:.1f}ms  {node.time_str}")

        elif self._view_mode == "benchmark":
            lines.append("  ── DNS Benchmark Results ──")
            lines.append(f"  Querying: {self._current_domain}  Queries per server: 100")
            lines.append("")
            for i, bm in enumerate(self._benchmarks):
                lines.append(f"  {i+1}. {bm.nameserver:<20s} {bm.ip:<16s} [{bm.speed_bar}] {bm.avg_ms:.1f}ms avg ({bm.min_ms:.0f}-{bm.max_ms:.0f}ms)")
                lines.append(f"     Success: {bm.success_rate:.0f}%  Failures: {bm.failures}/{bm.queries}")

        lines.append("")
        lines.append("  [L]ookup [H]istory [P]ropagation [B]enchmark [↑↓]Nav [Q]uery [T]ype")
        return lines
