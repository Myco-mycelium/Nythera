from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ResourceLimit(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    IO = "io"
    NETWORK = "network"
    PROCESSES = "processes"


class LimitScope(Enum):
    PER_PROCESS = "per-process"
    PER_GROUP = "per-group"
    SYSTEM_WIDE = "system-wide"


class ProfileType(Enum):
    DESKTOP = "desktop"
    GAMING = "gaming"
    SERVER = "server"
    BATTERY_SAVER = "battery-saver"
    PERFORMANCE = "performance"
    CUSTOM = "custom"


class CGroupVersion(Enum):
    V1 = "cgroup-v1"
    V2 = "cgroup-v2"


@dataclass
class ResourceRule:
    name: str
    limit_type: ResourceLimit
    value: float
    unit: str
    scope: LimitScope = LimitScope.PER_PROCESS
    enabled: bool = True

    @property
    def value_display(self) -> str:
        if self.unit == "%":
            return f"{self.value:.0f}%"
        if self.unit == "MB":
            return f"{self.value:.0f} MB"
        if self.unit == "MB/s":
            return f"{self.value:.0f} MB/s"
        if self.unit == "ops":
            return f"{self.value:.0f} ops/s"
        return f"{self.value} {self.unit}"

    @property
    def bar(self) -> str:
        max_val = 100 if self.unit == "%" else 1024
        pct = min(self.value / max_val, 1.0)
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class AppProfile:
    name: str
    executable: str
    rules: list = field(default_factory=list)
    is_active: bool = False
    profile_type: ProfileType = ProfileType.CUSTOM
    priority: int = 0

    @property
    def rule_count(self) -> int:
        return len([r for r in self.rules if r.enabled])


@dataclass
class ProcessLimit:
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float
    io_read_mbps: float
    io_write_mbps: float
    limits: list = field(default_factory=list)
    is_throttled: bool = False
    oom_score: int = 0

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_display(self) -> str:
        if self.memory_mb >= 1024:
            return f"{self.memory_mb / 1024:.1f} GB"
        return f"{self.memory_mb:.0f} MB"


@dataclass
class SystemLimits:
    cgroup_version: CGroupVersion
    total_cpu_cores: int
    total_memory_gb: float
    oom_enabled: bool
    swap_limit: bool
    cpu_quota_enabled: bool
    io_weight_enabled: bool

    @property
    def cpu_total_bar(self) -> str:
        return f"{self.total_cpu_cores} cores  {self.total_memory_gb:.0f} GB RAM"


class ResourceLimiter:
    def __init__(self):
        self._profiles: list[AppProfile] = []
        self._selected_profile: int = 0
        self._process_limits: list[ProcessLimit] = []
        self._selected_process: int = 0
        self._system_limits: Optional[SystemLimits] = None
        self._global_cpu_limit: float = 100
        self._global_memory_limit_gb: float = 64
        self._global_io_limit_mbps: float = 1000
        self._view: str = "profiles"
        self._cgroup_version: CGroupVersion = CGroupVersion.V2
        self._create_samples()

    def _create_samples(self):
        self._system_limits = SystemLimits(CGroupVersion.V2, 16, 64.0, True, True, True, True)

        desktop_rules = [
            ResourceRule("CPU Limit", ResourceLimit.CPU, 80, "%", LimitScope.PER_PROCESS),
            ResourceRule("Memory Cap", ResourceLimit.MEMORY, 8192, "MB", LimitScope.PER_PROCESS),
            ResourceRule("IO Weight", ResourceLimit.IO, 100, "ops", LimitScope.PER_PROCESS),
            ResourceRule("Network Rate", ResourceLimit.NETWORK, 100, "MB/s", LimitScope.PER_PROCESS),
        ]
        self._profiles.append(AppProfile("Desktop Default", "nyrqis-compositor", desktop_rules, True, ProfileType.DESKTOP, 10))

        gaming_rules = [
            ResourceRule("CPU Limit", ResourceLimit.CPU, 100, "%", LimitScope.PER_PROCESS),
            ResourceRule("Memory Cap", ResourceLimit.MEMORY, 16384, "MB", LimitScope.PER_PROCESS),
            ResourceRule("IO Weight", ResourceLimit.IO, 200, "ops", LimitScope.PER_PROCESS),
            ResourceRule("Network Rate", ResourceLimit.NETWORK, 500, "MB/s", LimitScope.PER_PROCESS),
            ResourceRule("Max Processes", ResourceLimit.PROCESSES, 64, "ops", LimitScope.PER_PROCESS),
        ]
        self._profiles.append(AppProfile("Gaming Mode", "steam", gaming_rules, False, ProfileType.GAMING, 20))

        server_rules = [
            ResourceRule("CPU Limit", ResourceLimit.CPU, 50, "%", LimitScope.PER_GROUP),
            ResourceRule("Memory Cap", ResourceLimit.MEMORY, 4096, "MB", LimitScope.PER_GROUP),
            ResourceRule("IO Weight", ResourceLimit.IO, 50, "ops", LimitScope.PER_GROUP),
        ]
        self._profiles.append(AppProfile("Server Container", "docker", server_rules, True, ProfileType.SERVER, 5))

        battery_rules = [
            ResourceRule("CPU Limit", ResourceLimit.CPU, 30, "%", LimitScope.SYSTEM_WIDE),
            ResourceRule("Memory Cap", ResourceLimit.MEMORY, 4096, "MB", LimitScope.PER_PROCESS),
            ResourceRule("IO Weight", ResourceLimit.IO, 30, "ops", LimitScope.PER_PROCESS),
            ResourceRule("Network Rate", ResourceLimit.NETWORK, 10, "MB/s", LimitScope.PER_PROCESS),
        ]
        self._profiles.append(AppProfile("Battery Saver", "*", battery_rules, False, ProfileType.BATTERY_SAVER, 1))

        self._process_limits = [
            ProcessLimit(456, "nyrqis-compositor", 35.2, 1536, 12.4, 8.2, [desktop_rules[0], desktop_rules[1]], False, 100),
            ProcessLimit(789, "firefox", 28.5, 3276.8, 45.2, 23.1, [], False, 200),
            ProcessLimit(1011, "code", 22.1, 2867.2, 34.5, 18.9, [], False, 150),
            ProcessLimit(1234, "dockerd", 8.3, 256, 125.4, 89.2, [server_rules[0]], False, 300),
            ProcessLimit(1345, "postgres", 4.2, 512, 234.5, 156.8, [server_rules[0], server_rules[1]], False, 250),
            ProcessLimit(1567, "rustc", 65.0, 1024, 56.7, 34.5, [], True, 180),
            ProcessLimit(1678, "Xwayland", 12.8, 384, 8.9, 5.6, [], False, 120),
            ProcessLimit(1789, "pipewire", 2.1, 48, 45.2, 23.4, [], False, 100),
        ]

    @property
    def selected_profile(self) -> Optional[AppProfile]:
        if 0 <= self._selected_profile < len(self._profiles):
            return self._profiles[self._selected_profile]
        return None

    @property
    def selected_process(self) -> Optional[ProcessLimit]:
        if 0 <= self._selected_process < len(self._process_limits):
            return self._process_limits[self._selected_process]
        return None

    @property
    def total_profiles(self) -> int:
        return len(self._profiles)

    @property
    def active_profiles(self) -> int:
        return sum(1 for p in self._profiles if p.is_active)

    @property
    def throttled_processes(self) -> int:
        return sum(1 for p in self._process_limits if p.is_throttled)

    def select_profile(self, idx: int):
        if 0 <= idx < len(self._profiles):
            self._selected_profile = idx

    def select_process(self, idx: int):
        if 0 <= idx < len(self._process_limits):
            self._selected_process = idx

    def toggle_profile(self, idx: int):
        if 0 <= idx < len(self._profiles):
            self._profiles[idx].is_active = not self._profiles[idx].is_active

    def add_rule(self, profile_idx: int, rule: ResourceRule):
        if 0 <= profile_idx < len(self._profiles):
            self._profiles[profile_idx].rules.append(rule)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                   NYRQIS RESOURCE LIMITER                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        if self._system_limits:
            s = self._system_limits
            lines.append(f"  System: {s.cgroup_version.value}  {s.cpu_total_bar}  OOM: {'ON' if s.oom_enabled else 'OFF'}  Swap: {'ON' if s.swap_limit else 'OFF'}")
        lines.append(f"  Global: CPU {self._global_cpu_limit:.0f}%  Memory {self._global_memory_limit_gb:.0f}GB  IO {self._global_io_limit_mbps:.0f}MB/s")
        lines.append(f"  Profiles: {self.total_profiles} ({self.active_profiles} active)  Throttled: {self.throttled_processes}")
        lines.append("")
        lines.append("  ── Profiles ──")
        for i, p in enumerate(self._profiles):
            sel = "▶" if i == self._selected_profile else " "
            active = "🟢" if p.is_active else "⚪"
            type_icons = {"desktop": "🖥️", "gaming": "🎮", "server": "📦", "battery-saver": "🔋", "performance": "⚡", "custom": "🔧"}
            icon = type_icons.get(p.profile_type.value, "🔧")
            lines.append(f"  {sel}{active} {icon} {p.name}  {p.executable}  {p.rule_count} rules  priority: {p.priority}")
        lines.append("")
        lines.append("  ── Process Limits ──")
        for i, proc in enumerate(self._process_limits):
            sel = "▶" if i == self._selected_process else " "
            throttle = "🔴" if proc.is_throttled else "🟢"
            lines.append(f"  {sel}{throttle} {proc.name:<20s} PID:{proc.pid:<6d} CPU:{proc.cpu_bar} {proc.cpu_percent:5.1f}%  MEM:{proc.memory_display:>8s}  OOM:{proc.oom_score}")
        lines.append("")
        lines.append("  [A]ctivate  [D]eactivate  [R]ules  [L]imit  [P]rocess  [S]ystem  [E]xport")
        return lines

    def render_profile_detail(self) -> list:
        p = self.selected_profile
        if not p:
            return ["  No profile selected"]
        lines = []
        lines.append(f"  ── {p.name} ({p.profile_type.value}) ──")
        lines.append(f"  Executable: {p.executable}  Active: {'Yes' if p.is_active else 'No'}  Priority: {p.priority}")
        lines.append("")
        for r in p.rules:
            status = "🟢" if r.enabled else "⚪"
            lines.append(f"  {status} {r.limit_type.value:<10s} [{r.bar}] {r.value_display}  ({r.scope.value})")
        return lines

    def render_system(self) -> list:
        lines = []
        lines.append("  ── System Limits ──")
        lines.append("")
        if self._system_limits:
            s = self._system_limits
            lines.append(f"  CGroup Version:  {s.cgroup_version.value}")
            lines.append(f"  CPU Cores:       {s.total_cpu_cores}")
            lines.append(f"  Total Memory:    {s.total_memory_gb:.0f} GB")
            lines.append(f"  OOM Killer:      {'Enabled' if s.oom_enabled else 'Disabled'}")
            lines.append(f"  Swap Limit:      {'Enabled' if s.swap_limit else 'Disabled'}")
            lines.append(f"  CPU Quota:       {'Enabled' if s.cpu_quota_enabled else 'Disabled'}")
            lines.append(f"  IO Weight:       {'Enabled' if s.io_weight_enabled else 'Disabled'}")
        return lines
