"""Firewall Manager — rule editor, traffic logging, threat dashboard for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class RuleAction(Enum):
    ALLOW = "Allow"
    DENY = "Deny"
    DROP = "Drop"
    REJECT = "Reject"
    LOG = "Log"
    RATE_LIMIT = "Rate Limit"


class Protocol(Enum):
    TCP = "TCP"
    UDP = "UDP"
    ICMP = "ICMP"
    ANY = "Any"
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    SSH = "SSH"
    DNS = "DNS"
    FTP = "FTP"
    SMTP = "SMTP"


class Direction(Enum):
    INBOUND = "Inbound"
    OUTBOUND = "Outbound"
    BOTH = "Both"


class ThreatCategory(Enum):
    NONE = "None"
    PORT_SCAN = "Port Scan"
    BRUTE_FORCE = "Brute Force"
    DDoS = "DDoS"
    MALWARE = "Malware"
    DATA_EXFIL = "Data Exfiltration"
    C2_BEACON = "C2 Beacon"
    KNOWN_IP = "Known Malicious IP"


class ChainType(Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    FORWARD = "FORWARD"
    PREROUTING = "Prerouting"
    POSTROUTING = "Postrouting"


class ProfileType(Enum):
    HOME = "Home"
    OFFICE = "Office"
    PUBLIC = "Public"
    CUSTOM = "Custom"
    LOCKDOWN = "Lockdown"


@dataclass
class FirewallRule:
    id: int
    name: str
    action: RuleAction = RuleAction.ALLOW
    protocol: Protocol = Protocol.ANY
    direction: Direction = Direction.INBOUND
    chain: ChainType = ChainType.INPUT
    source_ip: str = ""
    dest_ip: str = ""
    source_port: int = 0
    dest_port: int = 0
    interface: str = ""
    enabled: bool = True
    priority: int = 100
    hit_count: int = 0
    last_hit: float = 0.0
    created: float = 0.0
    expires: float = 0.0
    rate_limit: int = 0  # packets per second
    comment: str = ""

    @property
    def action_icon(self) -> str:
        icons = {
            RuleAction.ALLOW: "🟢",
            RuleAction.DENY: "🟡",
            RuleAction.DROP: "🔴",
            RuleAction.REJECT: "🟠",
            RuleAction.LOG: "📝",
            RuleAction.RATE_LIMIT: "⏱",
        }
        return icons.get(self.action, "?")

    @property
    def direction_icon(self) -> str:
        icons = {
            Direction.INBOUND: "⬇",
            Direction.OUTBOUND: "⬆",
            Direction.BOTH: "↕",
        }
        return icons.get(self.direction, "?")

    @property
    def port_str(self) -> str:
        if self.source_port and self.dest_port:
            return f"{self.source_port}:{self.dest_port}"
        elif self.dest_port:
            return f":{self.dest_port}"
        elif self.source_port:
            return f"{self.source_port}:"
        return "*"

    @property
    def hit_bar(self) -> str:
        filled = min(int(self.hit_count / 100), 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class TrafficLog:
    timestamp: float = 0.0
    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    protocol: Protocol = Protocol.TCP
    action: RuleAction = RuleAction.ALLOW
    rule_id: int = -1
    bytes_transferred: int = 0
    threat: ThreatCategory = ThreatCategory.NONE
    country: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def threat_icon(self) -> str:
        icons = {
            ThreatCategory.NONE: "",
            ThreatCategory.PORT_SCAN: "🔍",
            ThreatCategory.BRUTE_FORCE: "🔨",
            ThreatCategory.DDoS: "🌊",
            ThreatCategory.MALWARE: "🦠",
            ThreatCategory.DATA_EXFIL: "📤",
            ThreatCategory.C2_BEACON: "📡",
            ThreatCategory.KNOWN_IP: "⚠️",
        }
        return icons.get(self.threat, "")

    @property
    def bytes_str(self) -> str:
        if self.bytes_transferred < 1024:
            return f"{self.bytes_transferred} B"
        elif self.bytes_transferred < 1024 * 1024:
            return f"{self.bytes_transferred / 1024:.1f} KB"
        else:
            return f"{self.bytes_transferred / (1024 * 1024):.1f} MB"


@dataclass
class ThreatEvent:
    timestamp: float = 0.0
    category: ThreatCategory = ThreatCategory.NONE
    source_ip: str = ""
    description: str = ""
    severity: int = 1  # 1-5
    blocked: bool = False
    rule_triggered: int = -1

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def severity_bar(self) -> str:
        return "█" * self.severity + "░" * (5 - self.severity)

    @property
    def severity_color(self) -> str:
        colors = {1: "Low", 2: "Low", 3: "Medium", 4: "High", 5: "Critical"}
        return colors.get(self.severity, "Unknown")


@dataclass
class FirewallProfile:
    name: str
    profile_type: ProfileType = ProfileType.HOME
    default_action_in: RuleAction = RuleAction.ALLOW
    default_action_out: RuleAction = RuleAction.ALLOW
    log_blocked: bool = True
    stealth_mode: bool = False
    icmp_echo: bool = True
    port_scan_protection: bool = True
    rate_limiting: bool = False
    active: bool = False

    @property
    def status_icon(self) -> str:
        return "🟢" if self.active else "⚪"


class FirewallManager:
    def __init__(self):
        self._rules: List[FirewallRule] = []
        self._selected_rule: int = 0
        self._logs: List[TrafficLog] = []
        self._threats: List[ThreatEvent] = []
        self._profiles: List[FirewallProfile] = []
        self._active_profile: int = 0
        self._enabled: bool = True
        self._logging_enabled: bool = True
        self._block_all_inbound: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._rules = [
            FirewallRule(1, "Allow SSH", RuleAction.ALLOW, Protocol.SSH, Direction.INBOUND,
                         dest_port=22, interface="eth0", priority=10, hit_count=342,
                         last_hit=now - 300, created=now - 86400 * 30, comment="Admin access"),
            FirewallRule(2, "Allow HTTP", RuleAction.ALLOW, Protocol.HTTP, Direction.INBOUND,
                         dest_port=80, hit_count=12450, last_hit=now - 10,
                         created=now - 86400 * 30),
            FirewallRule(3, "Allow HTTPS", RuleAction.ALLOW, Protocol.HTTPS, Direction.INBOUND,
                         dest_port=443, hit_count=28900, last_hit=now - 5,
                         created=now - 86400 * 30),
            FirewallRule(4, "Allow DNS", RuleAction.ALLOW, Protocol.DNS, Direction.BOTH,
                         dest_port=53, hit_count=8920, last_hit=now - 2,
                         created=now - 86400 * 30),
            FirewallRule(5, "Block Telnet", RuleAction.DROP, Protocol.TCP, Direction.INBOUND,
                         dest_port=23, hit_count=89, last_hit=now - 3600,
                         created=now - 86400 * 60, comment="Legacy protocol"),
            FirewallRule(6, "Block NetBIOS", RuleAction.DROP, Protocol.UDP, Direction.INBOUND,
                         dest_port=137, hit_count=23, last_hit=now - 7200,
                         created=now - 86400 * 30),
            FirewallRule(7, "Rate Limit SSH", RuleAction.RATE_LIMIT, Protocol.SSH, Direction.INBOUND,
                         dest_port=22, rate_limit=5, hit_count=12, last_hit=now - 600,
                         created=now - 86400 * 7, comment="Brute force protection"),
            FirewallRule(8, "Allow Outbound HTTPS", RuleAction.ALLOW, Protocol.HTTPS, Direction.OUTBOUND,
                         dest_port=443, hit_count=45000, last_hit=now - 1,
                         created=now - 86400 * 30),
            FirewallRule(9, "Log ICMP", RuleAction.LOG, Protocol.ICMP, Direction.INBOUND,
                         hit_count=156, last_hit=now - 1800,
                         created=now - 86400 * 14),
            FirewallRule(10, "Deny TOR", RuleAction.DENY, Protocol.TCP, Direction.OUTBOUND,
                         dest_port=9001, hit_count=5, last_hit=now - 86400,
                         created=now - 86400 * 7, comment="Block TOR exit nodes"),
        ]

        # Sample traffic logs
        for i in range(30):
            self._logs.append(TrafficLog(
                now - i * random.randint(5, 60),
                f"192.168.1.{random.randint(10, 200)}",
                f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}",
                random.randint(1024, 65535), random.choice([80, 443, 22, 53]),
                random.choice(list(Protocol)),
                random.choice([RuleAction.ALLOW, RuleAction.DENY, RuleAction.DROP]),
                random.randint(1, 10),
                random.randint(64, 65000),
                random.choices(list(ThreatCategory), weights=[70, 8, 5, 3, 4, 3, 2, 5])[0],
                random.choice(["US", "CN", "RU", "DE", "BR", "IN", "JP", ""]),
            ))

        # Sample threats
        threat_data = [
            (ThreatCategory.PORT_SCAN, "185.220.101.34", "SYN scan on ports 21-1024", 3, True),
            (ThreatCategory.BRUTE_FORCE, "45.33.32.156", "SSH brute force attempt (50 failures)", 4, True),
            (ThreatCategory.DDoS, "103.224.182.250", "SYN flood detected (10k pkt/s)", 5, True),
            (ThreatCategory.MALWARE, "91.215.85.142", "Known C2 server IP contacted", 5, True),
            (ThreatCategory.DATA_EXFIL, "198.51.100.23", "Unusual outbound data volume (500MB)", 3, False),
            (ThreatCategory.C2_BEACON, "104.244.72.115", "Regular beacon interval detected", 4, True),
            (ThreatCategory.KNOWN_IP, "23.129.64.100", "Tor exit node IP", 2, True),
        ]
        for cat, ip, desc, sev, blocked in threat_data:
            self._threats.append(ThreatEvent(
                now - random.randint(0, 3600), cat, ip, desc, sev, blocked,
                random.randint(1, 10)))

        self._profiles = [
            FirewallProfile("Home Network", ProfileType.HOME, active=True,
                            default_action_in=RuleAction.ALLOW, log_blocked=True,
                            port_scan_protection=True),
            FirewallProfile("Office", ProfileType.OFFICE,
                            default_action_in=RuleAction.DENY, log_blocked=True,
                            stealth_mode=True, rate_limiting=True),
            FirewallProfile("Public WiFi", ProfileType.PUBLIC,
                            default_action_in=RuleAction.DROP, default_action_out=RuleAction.DENY,
                            stealth_mode=True, icmp_echo=False, rate_limiting=True),
            FirewallProfile("Lockdown", ProfileType.LOCKDOWN,
                            default_action_in=RuleAction.DROP, default_action_out=RuleAction.DROP,
                            log_blocked=True, stealth_mode=True),
        ]

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def selected_rule(self) -> Optional[FirewallRule]:
        if 0 <= self._selected_rule < len(self._rules):
            return self._rules[self._selected_rule]
        return None

    @property
    def total_rules(self) -> int:
        return len(self._rules)

    @property
    def active_rules(self) -> int:
        return sum(1 for r in self._rules if r.enabled)

    @property
    def blocked_count(self) -> int:
        return sum(1 for r in self._rules if r.action in (RuleAction.DENY, RuleAction.DROP))

    @property
    def total_threats(self) -> int:
        return len(self._threats)

    @property
    def blocked_threats(self) -> int:
        return sum(1 for t in self._threats if t.blocked)

    def select_rule(self, idx: int):
        if 0 <= idx < len(self._rules):
            self._selected_rule = idx

    def add_rule(self, name: str, action: RuleAction, port: int = 0):
        rule_id = max(r.id for r in self._rules) + 1 if self._rules else 1
        rule = FirewallRule(rule_id, name, action, dest_port=port, created=time.time())
        self._rules.append(rule)
        self._history.append(f"Added rule: {name}")

    def delete_rule(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_rule
        if 0 <= i < len(self._rules):
            name = self._rules[i].name
            self._rules.pop(i)
            self._selected_rule = min(self._selected_rule, len(self._rules) - 1)
            self._history.append(f"Deleted rule: {name}")

    def toggle_rule(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_rule
        if 0 <= i < len(self._rules):
            self._rules[i].enabled = not self._rules[i].enabled
            state = "enabled" if self._rules[i].enabled else "disabled"
            self._history.append(f"{self._rules[i].name} {state}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "e":
            self._enabled = not self._enabled
        elif key == "l":
            self._logging_enabled = not self._logging_enabled
        elif key == "b":
            self._block_all_inbound = not self._block_all_inbound
        elif key == "d":
            self.delete_rule()
        elif key == "n":
            self.add_rule("New Rule", RuleAction.ALLOW)

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS FIREWALL MANAGER                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Status
        status = "🟢 ENABLED" if self._enabled else "🔴 DISABLED"
        profile = self._profiles[self._active_profile] if self._profiles else None
        prof_name = profile.name if profile else "None"
        lines.append(f"  {status}  Profile: {prof_name}  Rules: {self.active_rules}/{self.total_rules}  Logging: {'ON' if self._logging_enabled else 'OFF'}")
        lines.append(f"  Blocked: {self.blocked_count} rules  Threats: {self.total_threats} ({self.blocked_threats} blocked)  Block All Inbound: {'ON' if self._block_all_inbound else 'OFF'}")
        lines.append("")

        # Rules
        lines.append("  ── Rules ──")
        for i, rule in enumerate(self._rules):
            sel = "▶" if i == self._selected_rule else " "
            en = "🟢" if rule.enabled else "⚪"
            lines.append(f"  {sel} {en} {rule.action_icon} {rule.direction_icon} {rule.name:<20s} {rule.protocol.value:<6s} {rule.port_str:<10s} Hits: {rule.hit_count:>6d} {rule.comment}")
        lines.append("")

        # Selected rule detail
        rule = self.selected_rule
        if rule:
            lines.append(f"  ── Rule: {rule.name} ──")
            lines.append(f"  Action: {rule.action.value} {rule.action_icon}  Protocol: {rule.protocol.value}  Direction: {rule.direction.value}")
            lines.append(f"  Chain: {rule.chain.value}  Source: {rule.source_ip or '*'}  Dest: {rule.dest_ip or '*'}")
            lines.append(f"  Source Port: {rule.source_port or '*'}  Dest Port: {rule.dest_port or '*'}  Interface: {rule.interface or 'Any'}")
            lines.append(f"  Priority: {rule.priority}  Rate Limit: {rule.rate_limit or 'None'} pps")
            lines.append(f"  Hits: [{rule.hit_bar}] {rule.hit_count}  Last Hit: {time.strftime('%H:%M:%S', time.localtime(rule.last_hit)) if rule.last_hit else 'Never'}")
            lines.append("")

        # Recent threats
        if self._threats:
            lines.append("  ── Recent Threats ──")
            for t in self._threats[:5]:
                blocked = "✅ Blocked" if t.blocked else "⚠️ Passed"
                lines.append(f"  {t.threat_icon if hasattr(t, 'threat_icon') else ''} {t.time_str} {t.category.value:<16s} {t.source_ip:<16s} [{t.severity_bar}] {t.severity_color}  {blocked}")
                lines.append(f"      {t.description}")
            lines.append("")

        # Recent logs
        if self._logs:
            lines.append("  ── Recent Logs ──")
            for log in self._logs[:5]:
                action = "🟢" if log.action == RuleAction.ALLOW else "🔴"
                lines.append(f"  {log.time_str} {action} {log.src_ip}:{log.src_port} → {log.dst_ip}:{log.dst_port} {log.protocol.value} {log.bytes_str} {log.threat_icon} {log.country}")
            lines.append("")

        lines.append("  [E]nable/Disable [L]ogging [B]lock All [N]ew Rule [D]Delete [↑↓]Select")
        return lines
