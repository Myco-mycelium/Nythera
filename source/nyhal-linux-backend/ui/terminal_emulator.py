from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import random


class SplitDirection(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class TerminalProfile(Enum):
    DEFAULT = "default"
    MONOKAI = "monokai"
    SOLARIZED = "solarized"
    DRACULA = "dracula"
    NORD = "nord"
    GRUVBOX = "gruvbox"
    ONE_DARK = "one-dark"
    CYBERPUNK = "cyberpunk"


@dataclass
class TerminalTab:
    title: str
    cwd: str
    history: list = field(default_factory=list)
    output_buffer: list = field(default_factory=list)
    env_vars: dict = field(default_factory=dict)
    created_at: float = 0.0
    is_modified: bool = False
    split_group: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def display_title(self) -> str:
        modified = " *" if self.is_modified else ""
        return f"{self.title}{modified}"

    @property
    def age_display(self) -> str:
        age = int(time.time() - self.created_at)
        if age < 60:
            return f"{age}s"
        elif age < 3600:
            return f"{age // 60}m"
        return f"{age // 3600}h"


@dataclass
class HistoryEntry:
    command: str
    timestamp: float
    exit_code: int = 0
    duration_ms: int = 0
    directory: str = ""
    output_lines: int = 0


@dataclass
class TerminalTheme:
    name: str
    background: str
    foreground: str
    cursor: str
    selection: str
    black: str = "#282a36"
    red: str = "#ff5555"
    green: str = "#50fa7b"
    yellow: str = "#f1fa8c"
    blue: str = "#6272a4"
    magenta: str = "#ff79c6"
    cyan: str = "#8be9fd"
    white: str = "#f8f8f2"
    bright_black: str = "#6272a4"
    bright_red: str = "#ff6e6e"
    bright_green: str = "#69ff94"
    bright_yellow: str = "#ffffa5"
    bright_blue: str = "#d6acff"
    bright_magenta: str = "#ff92df"
    bright_cyan: str = "#a4ffff"
    bright_white: str = "#ffffff"


# Predefined themes
THEMES = {
    TerminalProfile.DRACULA: TerminalTheme("Dracula", "#282a36", "#f8f8f2", "#ff79c6", "#44475a"),
    TerminalProfile.MONOKAI: TerminalTheme("Monokai", "#272822", "#f8f8f2", "#75715e", "#49483e",
                                           red="#f92672", green="#a6e22e", yellow="#e6db74", blue="#66d9ef", magenta="#ae81ff", cyan="#66d9ef"),
    TerminalProfile.SOLARIZED: TerminalTheme("Solarized", "#002b36", "#839496", "#93a1a1", "#073642",
                                             black="#073642", red="#dc322f", green="#859900", yellow="#b58900", blue="#268bd2", magenta="#d33682", cyan="#2aa198"),
    TerminalProfile.NORD: TerminalTheme("Nord", "#2e3440", "#d8dee9", "#d8dee9", "#434c5e",
                                        black="#3b4252", red="#bf616a", green="#a3be8c", yellow="#ebcb8b", blue="#81a1c1", magenta="#b48ead", cyan="#88c0d0"),
    TerminalProfile.GRUVBOX: TerminalTheme("Gruvbox", "#282828", "#ebdbb2", "#ebdbb2", "#504945",
                                           black="#282828", red="#cc241d", green="#98971a", yellow="#d79921", blue="#458588", magenta="#b16286", cyan="#689d6a"),
    TerminalProfile.ONE_DARK: TerminalTheme("One Dark", "#282c34", "#abb2bf", "#528bff", "#3e4451",
                                            red="#e06c75", green="#98c379", yellow="#e5c07b", blue="#61afef", magenta="#c678dd", cyan="#56b6c2"),
    TerminalProfile.CYBERPUNK: TerminalTheme("Cyberpunk", "#0a0a0f", "#f0f0f0", "#00ff41", "#1a1a2e",
                                             black="#0a0a0f", red="#ff0040", green="#00ff41", yellow="#ffff00", blue="#00d4ff", magenta="#ff00ff", cyan="#00ffff"),
}


class TerminalEmulator:
    def __init__(self):
        self._tabs: list[TerminalTab] = []
        self._selected_tab: int = 0
        self._history: list[HistoryEntry] = []
        self._current_theme: TerminalProfile = TerminalProfile.DRACULA
        self._font_size: int = 14
        self._font_family: str = "JetBrains Mono"
        self._cursor_style: str = "block"
        self._cursor_blink: bool = True
        self._scrollback_lines: int = 10000
        self._bell_sound: bool = False
        self._copy_on_select: bool = True
        self._hyperlinks: bool = True
        self._split_mode: Optional[SplitDirection] = None
        self._autocomplete: bool = True
        self._bold_is_bright: bool = True
        self._tab_width: int = 4
        self._padding: int = 12
        self._opacity: float = 0.95
        self._view: str = "tabs"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        # Create sample tabs
        self._tabs = [
            TerminalTab("zsh", "/home/user", created_at=now - 7200),
            TerminalTab("ssh:server", "/root", created_at=now - 3600),
            TerminalTab("htop", "/tmp", created_at=now - 1800),
        ]
        self._tabs[0].output_buffer = [
            "Last login: Mon Sep 1 10:30:00 2026",
            "Welcome to Nyrqis Terminal v2.1",
            "Type 'help' for available commands.",
            "",
            "user@nyrqis:~$ neofetch",
            "        .--.        user@nyrqis",
            "       |o_o |       OS: Nyrqis OS 1.1",
            "       |:_/ |       Kernel: 6.12.0-nyrqis",
            "      //   \\ \\      Shell: zsh 5.9",
            "     (|     | )     Terminal: Nyrqis Terminal",
            "    /'\\_   _/`\\     CPU: Ryzen 9 7950X",
            "    \\___)=(___/     Memory: 12.4 GiB / 64 GiB",
            "",
            "user@nyrqis:~$ █",
        ]

        # Sample command history
        cmd_history = [
            ("ls -la", 0, 5, 2),
            ("cd /etc/nginx", 0, 12, 1),
            ("git status", 0, 8, 15),
            ("cargo build --release", 0, 245, 8500),
            ("docker compose up -d", 0, 4, 320),
            ("ssh root@server.nyrqis.dev", 127, 1, 2100),
            ("htop", 0, 0, 0),
            ("make -j$(nproc)", 0, 156, 12400),
            ("python3 -m pytest tests/ -v", 0, 3191, 39500),
            ("git push origin main", 0, 1, 2500),
            ("pkill firefox", 0, 12, 50),
            ("cat /proc/cpuinfo | head -20", 0, 20, 5),
            ("df -h", 0, 7, 3),
            ("top -bn1 | head -15", 0, 15, 120),
            ("curl -s ifconfig.me", 0, 1, 800),
        ]
        for cmd, exit_code, lines, dur in cmd_history:
            self._history.append(HistoryEntry(
                cmd, now - random.randint(1, 86400), exit_code, dur, "/home/user", lines
            ))

    @property
    def selected_tab(self) -> Optional[TerminalTab]:
        if 0 <= self._selected_tab < len(self._tabs):
            return self._tabs[self._selected_tab]
        return None

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    @property
    def theme(self) -> TerminalTheme:
        return THEMES.get(self._current_theme, THEMES[TerminalProfile.DRACULA])

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def failed_commands(self) -> int:
        return sum(1 for h in self._history if h.exit_code != 0)

    def select_tab(self, idx: int):
        if 0 <= idx < len(self._tabs):
            self._selected_tab = idx

    def new_tab(self, title: str = "zsh", cwd: str = "/home/user") -> TerminalTab:
        tab = TerminalTab(title, cwd)
        self._tabs.append(tab)
        self._selected_tab = len(self._tabs) - 1
        return tab

    def close_tab(self, idx: int) -> bool:
        if 0 < idx < len(self._tabs):
            self._tabs.pop(idx)
            if self._selected_tab >= len(self._tabs):
                self._selected_tab = max(0, len(self._tabs) - 1)
            return True
        return False

    def split_tab(self, direction: SplitDirection):
        if self.selected_tab:
            self._split_mode = direction

    def execute_command(self, cmd: str) -> str:
        tab = self.selected_tab
        if not tab:
            return ""
        tab.output_buffer.append(f"{tab.cwd.split('/')[-1]}$ {cmd}")
        tab.history.append(cmd)

        # Simulate common commands
        if cmd.startswith("ls"):
            tab.output_buffer.extend(["Desktop  Documents  Downloads  Music  Pictures  Videos  .config  .local"])
        elif cmd.startswith("pwd"):
            tab.output_buffer.append(tab.cwd)
        elif cmd.startswith("date"):
            tab.output_buffer.append(time.strftime("%a %b %d %H:%M:%S %Y"))
        elif cmd.startswith("whoami"):
            tab.output_buffer.append("user")
        elif cmd.startswith("uname"):
            tab.output_buffer.append("Nyrqis 6.12.0-nyrqis x86_64 GNU/Linux")
        elif cmd.startswith("clear"):
            tab.output_buffer.clear()
        elif cmd == "help":
            tab.output_buffer.extend([
                "Available commands: ls, pwd, date, whoami, uname, clear, help, exit",
                "Type 'man <cmd>' for manual pages.",
            ])
        elif cmd == "exit":
            tab.output_buffer.append("logout")
            self.close_tab(self._selected_tab)
        else:
            tab.output_buffer.append(f"command not found: {cmd.split()[0]}")

        self._history.append(HistoryEntry(cmd, time.time(), 0, random.randint(1, 500), tab.cwd, len(tab.output_buffer)))
        tab.output_buffer.append("")
        return tab.output_buffer[-2] if len(tab.output_buffer) >= 2 else ""

    def search_history(self, query: str) -> list:
        return [h for h in self._history if query in h.command]

    def set_theme(self, profile: TerminalProfile):
        self._current_theme = profile

    def render(self, width: int = 80, height: int = 24) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                      NYRQIS TERMINAL EMULATOR                               ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Theme: {self.theme.name}  Font: {self._font_family} {self._font_size}pt  Tabs: {self.tab_count}")
        lines.append(f"  Scrollback: {self._scrollback_lines}  Autocomplete: {'ON' if self._autocomplete else 'OFF'}  Opacity: {self._opacity:.0%}")
        lines.append("")
        lines.append("  ── Tabs ──────────────────────────────────────────────────")
        for i, t in enumerate(self._tabs):
            sel = "▶" if i == self._selected_tab else " "
            lines.append(f"  {sel} {t.display_title}  [{t.cwd}]  ({t.age_display})")
        lines.append("")
        lines.append("  ── History ──────────────────────────────────────────────")
        for h in self._history[:10]:
            status = "✓" if h.exit_code == 0 else f"✗ {h.exit_code}"
            dur = f"{h.duration_ms}ms" if h.duration_ms else ""
            lines.append(f"  {status} {h.command}  {dur}")
        lines.append("")
        lines.append("  [N]ew tab  [C]lose  [S]plit  [T]heme  [/]search  ↑↓:history")
        return lines

    def render_terminal(self) -> list:
        tab = self.selected_tab
        if not tab:
            return ["  No tab open"]
        lines = []
        lines.append(f"  ── {tab.display_title} ── {tab.cwd} ──")
        lines.append("")
        for line in tab.output_buffer[-20:]:
            lines.append(f"  {line}")
        if not tab.output_buffer:
            lines.append(f"  user@nyrqis:{tab.cwd.split('/')[-1]}$ █")
        lines.append("")
        return lines

    def render_theme_preview(self) -> list:
        t = self.theme
        lines = []
        lines.append(f"  ── Theme: {t.name} ──")
        lines.append(f"  Background: {t.background}")
        lines.append(f"  Foreground: {t.foreground}")
        lines.append(f"  Cursor: {t.cursor}")
        lines.append(f"  Selection: {t.selection}")
        lines.append("")
        lines.append("  ANSI Colors:")
        lines.append(f"    {t.black} {t.red} {t.green} {t.yellow}")
        lines.append(f"    {t.blue} {t.magenta} {t.cyan} {t.white}")
        lines.append("")
        for profile in THEMES:
            sel = "▶" if profile == self._current_theme else " "
            lines.append(f"  {sel} {profile.value}")
        return lines
