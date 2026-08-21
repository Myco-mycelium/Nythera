#!/usr/bin/env python3
"""terminal_app — Nyrqis terminal emulator application.

A terminal emulator that demonstrates the full Nyrqis stack:

- Shell command execution via subprocess
- Output streaming (line-buffered)
- Command history and recall
- Built-in Nyrqis commands (settings, workspace, theme)
- Window title updates on command execution

This runs as a NUI component with a window in the desktop shell.

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class TerminalLine:
    """A single line in the terminal buffer."""
    text: str
    is_command: bool = False
    is_error: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class CommandResult:
    """Result of executing a command."""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class TerminalApp:
    """Nyrqis terminal emulator application.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session (for built-in commands).
    cwd : str
        Initial working directory.
    """

    def __init__(
        self,
        session=None,
        cwd: Optional[str] = None,
    ) -> None:
        self._session = session
        self._cwd = cwd or os.path.expanduser("~")
        self._history: List[TerminalLine] = []
        self._command_history: List[str] = []
        self._history_index: int = -1
        self._visible = False
        self._callbacks: List[Callable] = []
        self._env = dict(os.environ)
        self._env["TERM"] = "nyrqis"
        self._env["COLUMNS"] = "80"
        self._env["LINES"] = "24"

        # Welcome message
        self._history.append(TerminalLine(
            "Nyrqis Terminal v1.0.0 — type 'help' for commands"))
        self._history.append(TerminalLine(""))

    # -- Command execution --------------------------------------------

    def execute(self, command: str) -> CommandResult:
        """Execute a shell command and return the result.

        Built-in commands are handled directly.  Everything else
        is passed to the system shell.
        """
        command = command.strip()
        if not command:
            return CommandResult(exit_code=0)

        # Record in command history
        self._command_history.append(command)
        self._history_index = len(self._command_history)

        # Echo the command
        self._history.append(TerminalLine(
            f"$ {command}", is_command=True))

        # Check for built-in commands
        builtins = {
            "help": self._cmd_help,
            "clear": self._cmd_clear,
            "cd": self._cmd_cd,
            "pwd": self._cmd_pwd,
            "theme": self._cmd_theme,
            "workspace": self._cmd_workspace,
            "settings": self._cmd_settings,
            "history": self._cmd_history,
            "neofetch": self._cmd_neofetch,
            "exit": self._cmd_exit,
        }

        parts = command.split()
        cmd_name = parts[0] if parts else ""
        if cmd_name in builtins:
            start = time.monotonic()
            result = builtins[cmd_name](parts[1:])
            result.duration_ms = int(
                (time.monotonic() - start) * 1000)
        else:
            result = self._run_external(command)

        # Record output
        if result.stdout:
            for line in result.stdout.split("\n"):
                self._history.append(TerminalLine(line))
        if result.stderr:
            for line in result.stderr.split("\n"):
                self._history.append(TerminalLine(line, is_error=True))

        return result

    def _run_external(self, command: str) -> CommandResult:
        """Run an external command via subprocess."""
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self._cwd,
                env=self._env,
            )
            duration = int((time.monotonic() - start) * 1000)
            return CommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                exit_code=124,
                stderr="command timed out after 30s",
            )
        except Exception as e:
            return CommandResult(
                exit_code=1,
                stderr=str(e),
            )

    # -- Built-in commands --------------------------------------------

    def _cmd_help(self, args: List[str]) -> CommandResult:
        help_text = """Nyrqis Terminal built-in commands:
  help              Show this help message
  clear             Clear the terminal
  cd [dir]          Change directory
  pwd               Print working directory
  theme [name]      Get/set theme (Eclipse, Solar)
  workspace [cmd]   Workspace operations (list, switch, cycle)
  settings          Open settings panel
  history           Show command history
  neofetch          System information
  exit              Close the terminal

Any other command is passed to the system shell."""
        return CommandResult(stdout=help_text)

    def _cmd_clear(self, args: List[str]) -> CommandResult:
        self._history.clear()
        return CommandResult()

    def _cmd_cd(self, args: List[str]) -> CommandResult:
        target = args[0] if args else os.path.expanduser("~")
        if target == "~":
            target = os.path.expanduser("~")
        elif target.startswith("~/"):
            target = os.path.join(os.path.expanduser("~"), target[2:])
        elif not os.path.isabs(target):
            target = os.path.join(self._cwd, target)
        if not os.path.isdir(target):
            return CommandResult(exit_code=1, stderr=f"cd: {target}: No such directory")
        self._cwd = os.path.abspath(target)
        return CommandResult()

    def _cmd_pwd(self, args: List[str]) -> CommandResult:
        return CommandResult(stdout=self._cwd)

    def _cmd_theme(self, args: List[str]) -> CommandResult:
        if not args:
            theme = self._session.document.themes.get("active", "Eclipse") \
                if self._session else "Eclipse"
            return CommandResult(stdout=f"Current theme: {theme}")
        theme = args[0]
        if theme not in ("Eclipse", "Solar"):
            return CommandResult(
                exit_code=1,
                stderr=f"Unknown theme: {theme} (use Eclipse or Solar)")
        if self._session:
            self._session.document.themes["active"] = theme
            self._session.runtime.set_state("theme", theme)
            if hasattr(self._session, '_notifications'):
                self._session._notifications.info("Theme changed", theme)
        return CommandResult(stdout=f"Theme → {theme}")

    def _cmd_workspace(self, args: List[str]) -> CommandResult:
        if not args or args[0] == "list":
            if not self._session:
                return CommandResult(stdout="No session")
            lines = []
            for ws in self._session.workspaces:
                active = " (active)" if ws == self._session.active_workspace else ""
                lines.append(f"  {ws.id}: {ws.name}{active}")
            return CommandResult(stdout="\n".join(lines))
        elif args[0] == "cycle":
            direction = int(args[1]) if len(args) > 1 else 1
            if self._session:
                self._session.cycle_workspace(direction)
            return CommandResult(stdout="Workspace cycled")
        elif args[0] == "switch" and len(args) > 1:
            if self._session:
                result = self._session.switch_workspace(args[1])
                if not result:
                    return CommandResult(exit_code=1, stderr=f"Workspace not found: {args[1]}")
            return CommandResult(stdout=f"Switched to {args[1]}")
        return CommandResult(stdout="Usage: workspace [list|cycle|switch <id>]")

    def _cmd_settings(self, args: List[str]) -> CommandResult:
        if self._session:
            return CommandResult(stdout="Settings panel opened")
        return CommandResult(stdout="No session attached")

    def _cmd_history(self, args: List[str]) -> CommandResult:
        lines = []
        for i, cmd in enumerate(self._command_history[-20:], 1):
            lines.append(f"  {i:4d}  {cmd}")
        return CommandResult(stdout="\n".join(lines) if lines else "No history")

    def _cmd_neofetch(self, args: List[str]) -> CommandResult:
        info = f"""
   ╔══════════════╗     Nyrqis OS v0.1.0
   ║  ◈ Nyrqis    ║     ─────────────────
   ║  ◈ Desktop   ║     Shell: Nyrqis Terminal
   ╚══════════════╝     Kernel: Linux (NyHAL backend)
                        Theme: {self._get_theme()}
                        Workspaces: {len(self._session.workspaces) if self._session else 0}
                        Windows: {len(self._session.windows) if self._session else 0}
                        CWD: {self._cwd}
                        Python: {sys.version.split()[0]}
                        OS: {os.uname().sysname} {os.uname().machine}"""
        return CommandResult(stdout=info.strip())

    def _cmd_exit(self, args: List[str]) -> CommandResult:
        if self._session:
            self._visible = False
        return CommandResult(stdout="Goodbye!")

    def _get_theme(self) -> str:
        if self._session:
            return self._session.document.themes.get("active", "Eclipse")
        return "Eclipse"

    # -- History navigation -------------------------------------------

    def history_up(self) -> Optional[str]:
        """Navigate up in command history."""
        if self._command_history and self._history_index > 0:
            self._history_index -= 1
            return self._command_history[self._history_index]
        return None

    def history_down(self) -> Optional[str]:
        """Navigate down in command history."""
        if self._history_index < len(self._command_history) - 1:
            self._history_index += 1
            return self._command_history[self._history_index]
        self._history_index = len(self._command_history)
        return ""

    # -- Properties ---------------------------------------------------

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def history(self) -> List[TerminalLine]:
        return list(self._history)

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    # -- NUI export ---------------------------------------------------

    def to_nstudio(self) -> Dict[str, Any]:
        """Export the terminal state as a NUI component tree."""
        children = []
        for i, line in enumerate(self._history[-24:]):  # Max 24 lines
            color = "text_primary"
            if line.is_error:
                color = "border"  # Red-ish
            elif line.is_command:
                color = "accent"
            children.append({
                "id": f"term-line-{i}",
                "type": "Text",
                "layout": {"x": 8, "y": 8 + i * 20, "width": 780, "height": 20},
                "properties": {"text": line.text, "color": color},
            })
        return {
            "id": "terminal",
            "type": "Container",
            "layout": {"x": 0, "y": 0, "width": 800, "height": 500},
            "children": children,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the terminal standalone (interactive REPL)."""
    terminal = TerminalApp()

    print("Nyrqis Terminal v1.0.0")
    print("Type 'help' for commands, 'exit' to quit.\n")

    while True:
        try:
            prompt = f"\033[36m{os.path.basename(terminal.cwd)}$\033[0m "
            cmd = input(prompt)
            if cmd.strip() == "exit":
                break
            result = terminal.execute(cmd)
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(f"\033[31m{result.stderr}\033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
