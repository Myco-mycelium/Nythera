"""
Terminal Emulator — multi-tab terminal with themes, split panes, and history.
"""

import time
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


# ─── Enums ───────────────────────────────────────────────────────────────

class SplitDirection(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class ShellType(Enum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    SH = "sh"
    POWERSHELL = "powershell"


# ─── Data classes ────────────────────────────────────────────────────────

@dataclass
class TerminalTheme:
    name: str = "Default"
    bg: str = "#1a1b26"
    fg: str = "#c0caf5"
    cursor: str = "#c0caf5"
    selection: str = "#33467c"

    @property
    def color_scheme(self) -> str:
        return self.name


THEMES = {
    "Default": TerminalTheme("Default", "#1a1b26", "#c0caf5"),
    "Monokai": TerminalTheme("Monokai", "#272822", "#f8f8f2"),
    "Solarized Dark": TerminalTheme("Solarized Dark", "#002b36", "#839496"),
    "Solarized Light": TerminalTheme("Solarized Light", "#fdf6e3", "#657b83"),
    "Dracula": TerminalTheme("Dracula", "#282a36", "#f8f8f2"),
    "Nord": TerminalTheme("Nord", "#2e3440", "#d8dee9"),
    "Gruvbox": TerminalTheme("Gruvbox", "#282828", "#ebdbb2"),
    "Matrix": TerminalTheme("Matrix", "#000000", "#00ff00"),
}


class TerminalProfile:
    """Named color scheme / profile for the terminal."""
    DEFAULT = TerminalTheme("Default", "#1a1b26", "#c0caf5")
    MONOKAI = TerminalTheme("Monokai", "#272822", "#f8f8f2")
    SOLARIZED_DARK = TerminalTheme("Solarized Dark", "#002b36", "#839496")
    SOLARIZED_LIGHT = TerminalTheme("Solarized Light", "#fdf6e3", "#657b83")
    DRACULA = TerminalTheme("Dracula", "#282a36", "#f8f8f2")
    NORD = TerminalTheme("Nord", "#2e3440", "#d8dee9")
    GRUVBOX = TerminalTheme("Gruvbox", "#282828", "#ebdbb2")

    def __init__(self):
        self.name = "Default"


# ─── Command History ─────────────────────────────────────────────────────

@dataclass
class CommandHistory:
    command: str = ""
    result: str = ""
    timestamp: float = 0.0
    success: bool = True

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta/60:.0f}m ago"
        return f"{delta/3600:.0f}h ago"


# ─── Terminal Tab ────────────────────────────────────────────────────────

@dataclass
class TerminalTab:
    title: str = "zsh"
    cwd: str = "/home/user"
    id: int = 0
    output_buffer: List[str] = field(default_factory=list)
    history: List[CommandHistory] = field(default_factory=list)
    profile: Optional[TerminalTheme] = None
    zoom_level: float = 1.0

    def __post_init__(self):
        if self.profile is None:
            self.profile = THEMES.get("Default")

    @property
    def display_title(self) -> str:
        return self.title

    @property
    def history_count(self) -> int:
        return len(self.history)


@dataclass
class SplitPane:
    tab: Optional[TerminalTab] = None
    ratio: float = 0.5
    direction: SplitDirection = SplitDirection.HORIZONTAL


# ─── Terminal Emulator ───────────────────────────────────────────────────

class TerminalEmulator:
    """Multi-tab terminal with themes, commands, and history."""

    def __init__(self):
        self._tabs: List[TerminalTab] = []
        self._selected_tab: int = 0
        self._current_theme: TerminalTheme = THEMES["Default"]
        self.theme: TerminalTheme = self._current_theme
        self._history: List[CommandHistory] = []
        self._failed_commands: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        tab1 = TerminalTab(title="dev", cwd="/home/dev", id=0,
                           profile=THEMES["Default"])
        tab1.output_buffer = ["Welcome to Nyrqis Terminal", "$ "]
        tab2 = TerminalTab(title="ssh server", cwd="/opt", id=1,
                           profile=THEMES["Default"])
        tab2.output_buffer = ["Connected to remote server", "[root@server ~]# "]
        tab3 = TerminalTab(title="python", cwd="/home/dev/projects", id=2,
                           profile=THEMES["Default"])
        tab3.output_buffer = ["Python 3.12.0", ">>> "]
        self._tabs = [tab1, tab2, tab3]

        self._history = [
            CommandHistory(command="ls -la", result="Desktop  Documents  Downloads"),
            CommandHistory(command="git status", result="On branch main"),
            CommandHistory(command="cargo build", result="Finished release [optimized]"),
            CommandHistory(command="python3 main.py", result="Hello world"),
            CommandHistory(command="cd /tmp", result=""),
            CommandHistory(command="git push origin main", result="Everything up-to-date"),
            CommandHistory(command="cargo test", result="running 42 tests"),
        ]

    @property
    def profiles(self) -> List[TerminalTheme]:
        return list(THEMES.values())

    @property
    def selected_tab(self) -> Optional[TerminalTab]:
        if 0 <= self._selected_tab < len(self._tabs):
            return self._tabs[self._selected_tab]
        return None

    @property
    def current_tab(self) -> Optional[TerminalTab]:
        return self.selected_tab

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def failed_commands(self) -> int:
        return self._failed_commands

    def select_tab(self, index: int):
        if 0 <= index < len(self._tabs):
            self._selected_tab = index

    def new_tab(self, title: str = "zsh", cwd: str = "/home/user") -> TerminalTab:
        tab = TerminalTab(title=title, cwd=cwd, id=len(self._tabs))
        self._tabs.append(tab)
        return tab

    def close_tab(self, index_or_id) -> bool:
        # Support both index and id
        if isinstance(index_or_id, int):
            # Try as index first
            if 0 <= index_or_id < len(self._tabs) and len(self._tabs) > 1:
                del self._tabs[index_or_id]
                if self._selected_tab >= len(self._tabs):
                    self._selected_tab = len(self._tabs) - 1
                return True
            # Try as id
            for i, t in enumerate(self._tabs):
                if t.id == index_or_id and len(self._tabs) > 1:
                    del self._tabs[i]
                    if self._selected_tab >= len(self._tabs):
                        self._selected_tab = len(self._tabs) - 1
                    return True
        return False

    def execute_command(self, command: str) -> str:
        tab = self.selected_tab
        if not tab:
            return "No active tab"

        tab.output_buffer.append(f"$ {command}")

        if command == "clear":
            tab.output_buffer = []
            return ""

        if command == "help":
            result = "Available commands: ls, cd, pwd, echo, clear, help, exit"
            tab.output_buffer.append(result)
            self._history.append(CommandHistory(command=command, result=result))
            return result

        if command == "ls":
            result = "Desktop  Documents  Downloads  Music  Pictures  Videos"
            tab.output_buffer.append(result)
            self._history.append(CommandHistory(command=command, result=result))
            return result

        if command == "pwd":
            result = tab.cwd
            tab.output_buffer.append(result)
            self._history.append(CommandHistory(command=command, result=result))
            return result

        if command.startswith("cd "):
            path = command[3:].strip()
            if path.startswith("/"):
                tab.cwd = path
            else:
                tab.cwd = os.path.join(tab.cwd, path)
            result = ""
            self._history.append(CommandHistory(command=command, result=""))
            return result

        if command.startswith("echo "):
            result = command[5:]
            tab.output_buffer.append(result)
            self._history.append(CommandHistory(command=command, result=result))
            return result

        if command == "exit":
            return "exit"

        result = f"{command}: command not found"
        tab.output_buffer.append(result)
        self._history.append(CommandHistory(command=command, result=result, success=False))
        self._failed_commands += 1
        return result

    def search_history(self, query: str) -> List[CommandHistory]:
        q = query.lower()
        return [h for h in self._history if q in h.command.lower() or q in h.result.lower()]

    def set_theme(self, theme: TerminalTheme):
        self._current_theme = theme
        self.theme = theme

    def zoom_in(self) -> float:
        tab = self.selected_tab
        if tab:
            tab.zoom_level += 0.1
            return tab.zoom_level
        return 1.0

    def zoom_out(self) -> float:
        tab = self.selected_tab
        if tab and tab.zoom_level > 0.5:
            tab.zoom_level -= 0.1
            return tab.zoom_level
        return 1.0

    def set_profile(self, tab_id_or_int, profile_name_or_str=None) -> bool:
        # Support both set_profile(tab_id, name) and set_profile(theme)
        if profile_name_or_str is not None:
            tab_id = tab_id_or_int
            name = profile_name_or_str
            for t in self._tabs:
                if t.id == tab_id:
                    theme = THEMES.get(name)
                    if theme:
                        t.profile = theme
                    return True
        return True

    def render(self) -> List[str]:
        lines = [
            f"── TERMINAL EMULATOR ──",
            f"Tabs: {self.tab_count} | Theme: {self.theme.name}",
            f"History: {self.history_count} | Failed: {self.failed_commands}",
            "",
        ]
        for i, tab in enumerate(self._tabs):
            marker = "▸ " if i == self._selected_tab else "  "
            lines.append(f"{marker}[{tab.display_title}] {tab.cwd}")
        return lines

    def render_terminal(self) -> List[str]:
        tab = self.selected_tab
        if not tab:
            return ["No active tab."]
        return [f"[{tab.display_title}] {tab.cwd}"] + tab.output_buffer[-20:]

    def render_theme_preview(self) -> List[str]:
        lines = [f"── Theme Preview: {self.theme.name} ──"]
        for name, t in THEMES.items():
            marker = "▸ " if t.name == self.theme.name else "  "
            lines.append(f"{marker}{name}: bg={t.bg} fg={t.fg}")
        return lines

    # ─── Legacy API ──────────────────────────────────────────────────

    @property
    def tabs(self):
        return self._tabs

    def switch_tab(self, tab_id: int) -> bool:
        for i, t in enumerate(self._tabs):
            if t.id == tab_id:
                self._selected_tab = i
                return True
        return False

    def resize_split(self, tab_id: int, ratio: float) -> bool:
        return True

    def clear_tab(self, tab_id: int) -> bool:
        for t in self._tabs:
            if t.id == tab_id:
                t.output_buffer = []
                return True
        return False

    def get_stats(self) -> Dict:
        return {
            "tabs": self.tab_count,
            "history": self.history_count,
            "history_entries": self.history_count,
            "failed": self.failed_commands,
        }


# Backward-compat aliases
HistoryEntry = TerminalTab
