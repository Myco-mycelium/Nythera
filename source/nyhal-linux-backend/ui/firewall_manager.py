"""
Nyrqis OS - Firewall Manager
Rule editor, traffic monitoring, and zone management.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class RuleAction(Enum):
    ACCEPT = "accept"
    DROP = "drop"
    REJECT = "reject"
    LOG = "log"
    MASQUERADE = "masquerade"
    REDIRECT = "redirect"


class Protocol(Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    ANY = "any"
    SCTP = "sctp"


class Direction(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class Zone(Enum):
    PUBLIC = "public"
    HOME = "home"
    WORK = "work"
    TRUSTED = "trusted"
    BLOCKED = "blocked"
    DMZ = "dmz"
    DROP = "drop"


class RulePriority(Enum):
    CRITICAL = 0
    HIGH = 100
    NORMAL = 500
    LOW = 1000
    DEFAULT = 65535


@dataclass
class FirewallRule:
    name: str
    action: RuleAction = RuleAction.ACCEPT
    direction: Direction = Direction.INBOUND
    protocol: Protocol = Protocol.ANY
    source_ip: str = ""
    dest_ip: str = ""
    source_port: int = 0
    dest_port: int = 0
    port_range: str = ""
    zone: Zone = Zone.PUBLIC
    priority: RulePriority = RulePriority.NORMAL
    enabled: bool = True
    logged: bool = False
    description: str = ""
    hit_count: int = 0
    last_hit: float = 0.0
    created_at: float = 0.0
    interfaces: List[str] = field(default_factory=list)

    @property
    def action_icon(self) -> str:
        icons = {
            RuleAction.ACCEPT: "✅",
            RuleAction.DROP: "🚫",
            RuleAction.REJECT: "❌",
            RuleAction.LOG: "📝",
            RuleAction.MASQUERADE: "🎭",
            RuleAction.REDIRECT: "↪️",
        }
        return icons.get(self.action, "?")

    @property
    def direction_icon(self) -> str:
        icons = {
            Direction.INBOUND: "⬇️",
            Direction.OUTBOUND: "⬆️",
            Direction.BOTH: "↕️",
        }
        return icons.get(self.direction, "?")

    @property
    def port_display(self) -> str:
        if self.port_range:
            return self.port_range
        if self.dest_port:
            return str(self.dest_port)
        return "*"

    @property
    def source_display(self) -> str:
        return self.source_ip if self.source_ip else "any"

    @property
    def dest_display(self) -> str:
        return self.dest_ip if self.dest_ip else "any"


@dataclass
class TrafficEntry:
    timestamp: float
    source_ip: str
    dest_ip: str
    source_port: int
    dest_port: int
    protocol: Protocol
    action: RuleAction
    bytes_transferred: int = 0
    rule_name: str = ""
    interface: str = ""

    @property
    def action_icon(self) -> str:
        icons = {
            RuleAction.ACCEPT: "✅",
            RuleAction.DROP: "🚫",
            RuleAction.REJECT: "❌",
            RuleAction.LOG: "📝",
        }
        return icons.get(self.action, "?")


@dataclass
class TrafficStats:
    protocol: Protocol = Protocol.TCP
    total_bytes: int = 0
    total_packets: int = 0
    accepted: int = 0
    dropped: int = 0
    rejected: int = 0
    avg_packet_size: float = 0.0

    @property
    def drop_rate(self) -> float:
        total = self.accepted + self.dropped + self.rejected
        if total == 0:
            return 0.0
        return (self.dropped / total) * 100

    @property
    def bytes_display(self) -> str:
        if self.total_bytes < 1024:
            return f"{self.total_bytes} B"
        elif self.total_bytes < 1024 * 1024:
            return f"{self.total_bytes / 1024:.1f} KB"
        elif self.total_bytes < 1024 * 1024 * 1024:
            return f"{self.total_bytes / (1024 * 1024):.1f} MB"
        return f"{self.total_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class FirewallZone:
    name: Zone
    description: str = ""
    interfaces: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    ports: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    target: RuleAction = RuleAction.ACCEPT
    rule_count: int = 0
    enabled: bool = True

    @property
    def target_icon(self) -> str:
        icons = {
            RuleAction.ACCEPT: "✅",
            RuleAction.DROP: "🚫",
            RuleAction.REJECT: "❌",
            RuleAction.MASQUERADE: "🎭",
        }
        return icons.get(self.target, "?")


class FirewallManager:
    def __init__(self):
        self.rules: List[FirewallRule] = []
        self.zones: List[FirewallZone] = []
        self.traffic_log: List[TrafficEntry] = []
        self.traffic_stats: Dict[str, TrafficStats] = {}
        self.active_zone: Zone = Zone.PUBLIC
        self.enabled: bool = True
        self.default_policy: RuleAction = RuleAction.DROP
        self.logging_enabled: bool = True
        self.block_count: int = 0
        self.alert_threshold: int = 1000
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.rules = [
            FirewallRule(name="Allow SSH", action=RuleAction.ACCEPT,
                         direction=Direction.INBOUND, protocol=Protocol.TCP,
                         dest_port=22, zone=Zone.HOME, priority=RulePriority.HIGH,
                         description="Allow SSH from trusted networks",
                         hit_count=452, last_hit=now - 60,
                         interfaces=["eth0"], created_at=now - 86400 * 30),
            FirewallRule(name="Allow HTTP", action=RuleAction.ACCEPT,
                         direction=Direction.INBOUND, protocol=Protocol.TCP,
                         dest_port=80, zone=Zone.PUBLIC, priority=RulePriority.NORMAL,
                         description="Allow HTTP web traffic",
                         hit_count=15230, last_hit=now - 5,
                         interfaces=["eth0", "wlan0"], created_at=now - 86400 * 30),
            FirewallRule(name="Allow HTTPS", action=RuleAction.ACCEPT,
                         direction=Direction.INBOUND, protocol=Protocol.TCP,
                         dest_port=443, zone=Zone.PUBLIC, priority=RulePriority.NORMAL,
                         description="Allow HTTPS web traffic",
                         hit_count=42150, last_hit=now - 2,
                         interfaces=["eth0", "wlan0"], created_at=now - 86400 * 30),
            FirewallRule(name="Allow DNS", action=RuleAction.ACCEPT,
                         direction=Direction.OUTBOUND, protocol=Protocol.UDP,
                         dest_port=53, zone=Zone.PUBLIC, priority=RulePriority.HIGH,
                         description="Allow outgoing DNS queries",
                         hit_count=8920, last_hit=now - 10,
                         interfaces=["eth0", "wlan0"], created_at=now - 86400 * 30),
            FirewallRule(name="Allow DHCP", action=RuleAction.ACCEPT,
                         direction=Direction.BOTH, protocol=Protocol.UDP,
                         source_port=68, dest_port=67, zone=Zone.HOME,
                         priority=RulePriority.HIGH,
                         description="Allow DHCP client",
                         hit_count=2400, last_hit=now - 300,
                         interfaces=["eth0"], created_at=now - 86400 * 30),
            FirewallRule(name="Block Port Scan", action=RuleAction.DROP,
                         direction=Direction.INBOUND, protocol=Protocol.TCP,
                         port_range="1-1024", zone=Zone.PUBLIC,
                         priority=RulePriority.CRITICAL,
                         description="Drop port scan attempts",
                         hit_count=892, last_hit=now - 120,
                         logged=True, created_at=now - 86400 * 15),
            FirewallRule(name="Block Telnet", action=RuleAction.DROP,
                         direction=Direction.INBOUND, protocol=Protocol.TCP,
                         dest_port=23, zone=Zone.PUBLIC, priority=RulePriority.HIGH,
                         description="Block insecure Telnet",
                         hit_count=234, last_hit=now - 600,
                         logged=True, created_at=now - 86400 * 30),
            FirewallRule(name="Allow Nyrqis Compositor", action=RuleAction.ACCEPT,
                         direction=Direction.BOTH, protocol=Protocol.ANY,
                         source_ip="127.0.0.1", dest_ip="127.0.0.1",
                         zone=Zone.TRUSTED, priority=RulePriority.CRITICAL,
                         description="Allow local compositor IPC",
                         hit_count=125000, last_hit=now - 1,
                         interfaces=["lo"], created_at=now - 86400 * 30),
            FirewallRule(name="Log ICMP", action=RuleAction.LOG,
                         direction=Direction.INBOUND, protocol=Protocol.ICMP,
                         zone=Zone.PUBLIC, priority=RulePriority.LOW,
                         description="Log all ICMP traffic",
                         hit_count=567, last_hit=now - 300,
                         created_at=now - 86400 * 7),
            FirewallRule(name="Masquerade Outbound", action=RuleAction.MASQUERADE,
                         direction=Direction.OUTBOUND, protocol=Protocol.ANY,
                         zone=Zone.PUBLIC, priority=RulePriority.DEFAULT,
                         description="NAT outbound traffic",
                         hit_count=245000, last_hit=now - 1,
                         interfaces=["eth0"], created_at=now - 86400 * 30),
        ]

        self.zones = [
            FirewallZone(name=Zone.PUBLIC, description="Public untrusted network",
                         interfaces=["eth0"], services=["http", "https"],
                         target=RuleAction.DROP, rule_count=6),
            FirewallZone(name=Zone.HOME, description="Trusted home network",
                         interfaces=["wlan0"], services=["ssh", "samba", "mdns"],
                         target=RuleAction.ACCEPT, rule_count=3),
            FirewallZone(name=Zone.WORK, description="Office network",
                         interfaces=[], services=["ssh", "http"],
                         target=RuleAction.ACCEPT, rule_count=2),
            FirewallZone(name=Zone.TRUSTED, description="Fully trusted zone",
                         interfaces=["lo"], services=[],
                         target=RuleAction.ACCEPT, rule_count=1),
            FirewallZone(name=Zone.BLOCKED, description="Blocked hosts",
                         interfaces=[], target=RuleAction.DROP, rule_count=0),
            FirewallZone(name=Zone.DMZ, description="Demilitarized zone",
                         interfaces=[], services=["http", "https"],
                         target=RuleAction.ACCEPT, rule_count=0),
        ]

        for proto in Protocol:
            total = random.randint(10000, 500000)
            self.traffic_stats[proto.value] = TrafficStats(
                protocol=proto, total_bytes=random.randint(1024 * 1024, 1024 * 1024 * 500),
                total_packets=total,
                accepted=int(total * random.uniform(0.85, 0.95)),
                dropped=int(total * random.uniform(0.02, 0.10)),
                rejected=int(total * random.uniform(0.01, 0.05)),
                avg_packet_size=random.uniform(200, 1200),
            )

        sample_ips = ["10.0.0.5", "192.168.1.50", "8.8.8.8", "1.1.1.1", "142.250.80.46"]
        for i in range(30):
            self.traffic_log.append(TrafficEntry(
                timestamp=now - random.uniform(0, 3600),
                source_ip=random.choice(sample_ips),
                dest_ip="192.168.1.100",
                source_port=random.randint(1024, 65535),
                dest_port=random.choice([22, 80, 443, 53, 8080]),
                protocol=random.choice([Protocol.TCP, Protocol.UDP]),
                action=random.choice([RuleAction.ACCEPT, RuleAction.ACCEPT, RuleAction.DROP]),
                bytes_transferred=random.randint(40, 1500),
                rule_name=random.choice(["Allow SSH", "Allow HTTPS", "Block Port Scan"]),
                interface=random.choice(["eth0", "wlan0"]),
            ))

    def add_rule(self, rule: FirewallRule) -> None:
        self.rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        for i, r in enumerate(self.rules):
            if r.name == name:
                del self.rules[i]
                return True
        return False

    def toggle_rule(self, name: str) -> bool:
        rule = next((r for r in self.rules if r.name == name), None)
        if rule:
            rule.enabled = not rule.enabled
            return True
        return False

    def get_rules_for_zone(self, zone: Zone) -> List[FirewallRule]:
        return [r for r in self.rules if r.zone == zone]

    def get_enabled_rules(self) -> List[FirewallRule]:
        return [r for r in self.rules if r.enabled]

    def search_rules(self, query: str) -> List[FirewallRule]:
        q = query.lower()
        return [r for r in self.rules if q in r.name.lower() or q in r.description.lower()]

    def get_zone(self, zone: Zone) -> Optional[FirewallZone]:
        return next((z for z in self.zones if z.name == zone), None)

    def get_traffic_summary(self) -> Dict:
        total_bytes = sum(s.total_bytes for s in self.traffic_stats.values())
        total_packets = sum(s.total_packets for s in self.traffic_stats.values())
        total_dropped = sum(s.dropped for s in self.traffic_stats.values())
        return {
            "total_bytes": total_bytes,
            "total_packets": total_packets,
            "total_dropped": total_dropped,
            "rules": len(self.rules),
            "enabled_rules": len(self.get_enabled_rules()),
            "zones": len([z for z in self.zones if z.enabled]),
            "block_count": self.block_count,
        }

    def get_recent_traffic(self, limit: int = 20) -> List[TrafficEntry]:
        return sorted(self.traffic_log, key=lambda t: t.timestamp, reverse=True)[:limit]
