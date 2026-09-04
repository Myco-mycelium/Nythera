"""
Nyrqis OS - Terminal Emulator
Tabs, splits, profiles, and command history.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class SplitDirection(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class ShellType(Enum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    POWERSHELL = "powershell"
    NUSH = "nush"
    SH = "sh"


@dataclass
class TerminalProfile:
    name: str
    shell: ShellType = ShellType.ZSH
    font_family: str = "JetBrains Mono"
    font_size: int = 14
    foreground: str = "#c0c5ce"
    background: str = "#1a1a2e"
    cursor_color: str = "#e94560"
    selection_color: str = "#233554"
    opacity: float = 0.95
    scrollback_lines: int = 10000
    bell: bool = False
    copy_on_select: bool = True
    audible_bell: bool = False
    cursor_blink: bool = True

    @property
    def color_scheme(self) -> str:
        return f"fg={self.foreground} bg={self.background}"


@dataclass
class TerminalTab:
    id: int = 0
    title: str = ""
    profile: Optional[TerminalProfile] = None
    cwd: str = "~"
    history: List[str] = field(default_factory=list)
    output_lines: List[str] = field(default_factory=list)
    is_active: bool = False
    pid: int = 0
    zoom_level: float = 1.0

    @property
    def display_title(self) -> str:
        return self.title if self.title else self.cwd.split("/")[-1] or "~"

    @property
    def history_count(self) -> int:
        return len(self.history)


@dataclass
class SplitPane:
    id: int = 0
    tab_id: int = 0
    direction: SplitDirection = SplitDirection.HORIZONTAL
    ratio: float = 0.5
    children: List[int] = field(default_factory=list)
    terminal_tab: Optional[TerminalTab] = None
    is_leaf: bool = True


@dataclass
class CommandHistory:
    command: str = ""
    timestamp: float = 0.0
    cwd: str = ""
    exit_code: int = 0
    duration_s: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


class TerminalEmulator:
    def __init__(self):
        self.tabs: List[TerminalTab] = []
        self.profiles: List[TerminalProfile] = []
        self.current_tab: Optional[TerminalTab] = None
        self.global_history: List[CommandHistory] = []
        self.tab_counter: int = 0
        self.active_profile: Optional[TerminalProfile] = None
        self._create_sample_data()

    def _create_sample_data(self):
        self.profiles = [
            TerminalProfile(name="Nyrqis Default", shell=ShellType.ZSH,
                            font_family="JetBrains Mono", font_size=14,
                            foreground="#c0c5ce", background="#1a1a2e",
                            cursor_color="#e94560"),
            TerminalProfile(name="Light", shell=ShellType.ZSH,
                            font_family="Inter", font_size=14,
                            foreground="#212121", background="#fafafa",
                            cursor_color="#1565c0"),
            TerminalProfile(name="Matrix", shell=ShellType.ZSH,
                            font_family="Fira Code", font_size=13,
                            foreground="#00ff41", background="#0d0208",
                            cursor_color="#00ff41"),
            TerminalProfile(name="Dracula", shell=ShellType.ZSH,
                            font_family="Fira Code", font_size=14,
                            foreground="#f8f8f2", background="#282a36",
                            cursor_color="#bd93f9"),
            TerminalProfile(name="PowerShell", shell=ShellType.POWERSHELL,
                            font_family="Cascadia Code", font_size=14,
                            foreground="#cccccc", background="#012456",
                            cursor_color="#ffffff"),
        ]
        self.active_profile = self.profiles[0]

        sample_commands = [
            ("cd /opt/Nyrqis", 0), ("cargo build --release", 0),
            ("git status", 0), ("git log --oneline -5", 0),
            ("ls -la", 0), ("grep -r 'compositor' src/", 0),
            ("cargo test -- --test-threads=4", 0),
            ("docker ps", 0), ("kubectl get pods", 0),
            ("ssh root@nyrqis-server", 0),
            ("python3 -m http.server 8080", 0),
            ("make clean && make", 1),
        ]

        tab1 = TerminalTab(id=1, title="dev", profile=self.profiles[0],
                            cwd="~/Projects/Nyrqis",
                            history=[cmd for cmd, _ in sample_commands[:6]],
                            output_lines=[
                                "$ cargo build --release",
                                "   Compiling nyrqis-compositor v0.9.5",
                                "   Compiling nyrqis-shell v0.8.2",
                                "   Finished release [optimized] target(s)",
                                "$ _",
                            ],
                            is_active=True, pid=1234)

        tab2 = TerminalTab(id=2, title="server", profile=self.profiles[0],
                            cwd="~",
                            history=["ssh root@10.0.0.5", "htop", "docker ps"],
                            output_lines=[
                                "Welcome to Ubuntu 24.04 LTS",
                                "$ docker ps",
                                "CONTAINER ID  IMAGE  STATUS",
                                "a1b2c3d4  nyrqis-app  Up 2 hours",
                                "$ _",
                            ],
                            is_active=False, pid=1235)

        tab3 = TerminalTab(id=3, title="logs", profile=self.profiles[3],
                            cwd="/var/log",
                            history=["tail -f syslog", "grep error syslog"],
                            output_lines=[
                                "$ tail -f syslog",
                                "Sep 04 10:15:22 nyrqis systemd[1]: Started...",
                                "Sep 04 10:15:23 nyrqis nyrqis-compositor[2]:...",
                                "$ _",
                            ],
                            is_active=False, pid=1236)

        self.tabs = [tab1, tab2, tab3]
        self.current_tab = tab1
        self.tab_counter = 3

        for cmd, _ in sample_commands:
            self.global_history.append(CommandHistory(
                command=cmd, cwd="~/Projects/Nyrqis",
                exit_code=random.choice([0, 0, 0, 1]),
                duration_s=random.uniform(0.1, 15.0)))

    def new_tab(self, profile_name: str = "") -> TerminalTab:
        self.tab_counter += 1
        profile = self.active_profile
        if profile_name:
            profile = next((p for p in self.profiles if p.name == profile_name), self.active_profile)
        tab = TerminalTab(id=self.tab_counter, profile=profile,
                           cwd=self.current_tab.cwd if self.current_tab else "~",
                           pid=10000 + self.tab_counter)
        self.tabs.append(tab)
        return tab

    def close_tab(self, tab_id: int) -> bool:
        if len(self.tabs) <= 1:
            return False
        for i, t in enumerate(self.tabs):
            if t.id == tab_id:
                del self.tabs[i]
                if self.current_tab and self.current_tab.id == tab_id:
                    self.current_tab = self.tabs[min(i, len(self.tabs) - 1)]
                return True
        return False

    def switch_tab(self, tab_id: int) -> bool:
        tab = next((t for t in self.tabs if t.id == tab_id), None)
        if tab:
            for t in self.tabs:
                t.is_active = False
            tab.is_active = True
            self.current_tab = tab
            return True
        return False

    def execute_command(self, command: str) -> str:
        if not self.current_tab:
            return ""
        self.current_tab.history.append(command)
        self.global_history.append(CommandHistory(
            command=command, cwd=self.current_tab.cwd))
        output = f"$ {command}\n"
        if command.startswith("cd "):
            self.current_tab.cwd = command[3:].strip()
            return output
        if command == "ls":
            output += "Documents  Downloads  Music  Pictures  Videos\n"
        elif command == "pwd":
            output += self.current_tab.cwd + "\n"
        elif command == "whoami":
            output += "zeus\n"
        elif command == "date":
            output += time.strftime("%Y-%m-%d %H:%M:%S") + "\n"
        elif command == "uname -a":
            output += "Nyrqis 1.0.0-rc1 #1 SMP x86_64 GNU/Linux\n"
        elif command.startswith("echo "):
            output += command[5:] + "\n"
        else:
            output += ""
        output += "$ "
        self.current_tab.output_lines.append(output)
        return output

    def resize_split(self, tab_id: int, ratio: float) -> bool:
        self.current_tab = next((t for t in self.tabs if t.id == tab_id), None)
        if self.current_tab:
            self.current_tab.zoom_level = max(0.5, min(3.0, ratio))
            return True
        return False

    def zoom_in(self) -> float:
        if self.current_tab:
            self.current_tab.zoom_level = min(3.0, self.current_tab.zoom_level + 0.1)
            return self.current_tab.zoom_level
        return 1.0

    def zoom_out(self) -> float:
        if self.current_tab:
            self.current_tab.zoom_level = max(0.5, self.current_tab.zoom_level - 0.1)
            return self.current_tab.zoom_level
        return 1.0

    def search_history(self, query: str) -> List[CommandHistory]:
        q = query.lower()
        return [h for h in self.global_history if q in h.command.lower()]

    def set_profile(self, tab_id: int, profile_name: str) -> bool:
        tab = next((t for t in self.tabs if t.id == tab_id), None)
        profile = next((p for p in self.profiles if p.name == profile_name), None)
        if tab and profile:
            tab.profile = profile
            return True
        return False

    def clear_tab(self, tab_id: int) -> bool:
        tab = next((t for t in self.tabs if t.id == tab_id), None)
        if tab:
            tab.output_lines = ["$ "]
            return True
        return False

    def get_stats(self) -> Dict:
        return {
            "tabs": len(self.tabs),
            "profiles": len(self.profiles),
            "history_entries": len(self.global_history),
            "active_tab": self.current_tab.display_title if self.current_tab else "None",
        }
