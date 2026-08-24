#!/usr/bin/env python3
"""Command system for Nyrqis DesktopSession — undo/redo with transactions.

Every user-facing mutation goes through a Command object.  The session
maintains an undo stack and a redo stack; undo pops from undo and pushes
to redo, and vice versa.  Drag/resize operations use transactions:
mouse-down begins a transaction, mouse-move applies many in-memory
updates, and mouse-up commits a single MoveCommand or ResizeCommand.

Design principles:
  - Commands are immutable snapshots (old → new state).
  - Commands execute immediately on creation (push to undo stack).
  - Undo reverses the command; redo re-applies it.
  - Transactions batch many small mutations into one undo step.
  - The command stack has a configurable max depth (default 100).

References:
  - GoF Command pattern
  - macOS NSUndoManager
  - VS Code editor undo/redo

Usage:
    session = DesktopSession(doc)
    session.execute(AddWindowCommand(session, "win-1", title="My App"))
    session.execute(MoveWindowCommand(session, "win-1", x=100, y=200))
    session.undo()  # undoes the move
    session.redo()  # re-applies the move
"""

from __future__ import annotations

import copy
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Base command
# ---------------------------------------------------------------------------

class Command(ABC):
    """Abstract base for all undoable commands.

    Each concrete command captures enough state to both *apply* and
    *reverse* the operation.  ``execute()`` applies the forward
    direction; ``undo()`` reverses it; ``redo()`` re-applies.
    """

    def __init__(self, description: str = "") -> None:
        self._description = description
        self._timestamp = time.monotonic()

    @property
    def description(self) -> str:
        return self._description

    @property
    def timestamp(self) -> float:
        return self._timestamp

    @abstractmethod
    def execute(self) -> None:
        """Apply the command (forward)."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command."""

    def redo(self) -> None:
        """Re-apply the command (default: execute again)."""
        self.execute()


# ---------------------------------------------------------------------------
# Transaction (batch of commands)
# ---------------------------------------------------------------------------

class Transaction(Command):
    """A batch of commands treated as a single undo step.

    Used for drag/resize operations where many small mutations happen
    but should undo as a single atomic operation.
    """

    def __init__(self, description: str = "transaction",
                 commands: Optional[List[Command]] = None) -> None:
        super().__init__(description)
        self._commands: List[Command] = commands or []
        self._executed = False

    def add(self, cmd: Command) -> None:
        """Add a command to this transaction."""
        self._commands.append(cmd)

    def execute(self) -> None:
        """Execute all commands in order."""
        for cmd in self._commands:
            cmd.execute()
        self._executed = True

    def undo(self) -> None:
        """Undo all commands in reverse order."""
        for cmd in reversed(self._commands):
            cmd.undo()

    def redo(self) -> None:
        """Re-execute all commands in order."""
        for cmd in self._commands:
            cmd.redo()

    @property
    def commands(self) -> List[Command]:
        return list(self._commands)


# ---------------------------------------------------------------------------
# Concrete commands
# ---------------------------------------------------------------------------

class AddWindowCommand(Command):
    """Add a window to the desktop session."""

    def __init__(self, session: Any, window: Any,
                 description: str = "Add window") -> None:
        super().__init__(description)
        self._session = session
        self._window = window

    def execute(self) -> None:
        self._session._windows.append(self._window)
        self._session._focus_window(self._window.id)

    def undo(self) -> None:
        self._session._windows.remove(self._window)
        if self._session._focused_window_id == self._window.id:
            self._session._focused_window_id = None
            self._session._auto_focus_topmost()

    def redo(self) -> None:
        self._session._windows.append(self._window)
        self._session._focus_window(self._window.id)


class RemoveWindowCommand(Command):
    """Remove a window from the desktop session."""

    def __init__(self, session: Any, window_id: str,
                 description: str = "Remove window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._removed_window = None
        self._was_focused = False
        self._window_index = 0

    def execute(self) -> None:
        for i, w in enumerate(self._session._windows):
            if w.id == self._window_id:
                self._removed_window = w
                self._window_index = i
                self._was_focused = (self._session._focused_window_id == self._window_id)
                self._session._windows.pop(i)
                if self._was_focused:
                    self._session._focused_window_id = None
                    self._session._auto_focus_topmost()
                break

    def undo(self) -> None:
        if self._removed_window is not None:
            self._session._windows.insert(self._window_index, self._removed_window)
            if self._was_focused:
                self._session._focus_window(self._removed_window.id)

    def redo(self) -> None:
        if self._removed_window is not None:
            self._session._windows.remove(self._removed_window)
            if self._was_focused:
                self._session._focused_window_id = None
                self._session._auto_focus_topmost()


class MoveWindowCommand(Command):
    """Move a window to a new position."""

    def __init__(self, session: Any, window_id: str,
                 x: int, y: int,
                 description: str = "Move window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._new_x = x
        self._new_y = y
        self._old_x = 0
        self._old_y = 0

    def execute(self) -> None:
        win = self._find_window()
        if win:
            self._old_x, self._old_y = win.x, win.y
            win.x = self._new_x
            win.y = self._new_y

    def undo(self) -> None:
        win = self._find_window()
        if win:
            win.x = self._old_x
            win.y = self._old_y

    def _find_window(self):
        for w in self._session._windows:
            if w.id == self._window_id:
                return w
        return None


class ResizeWindowCommand(Command):
    """Resize a window."""

    def __init__(self, session: Any, window_id: str,
                 width: int, height: int,
                 description: str = "Resize window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._new_w = width
        self._new_h = height
        self._old_w = 0
        self._old_h = 0

    def execute(self) -> None:
        win = self._find_window()
        if win:
            self._old_w, self._old_h = win.width, win.height
            win.width = self._new_w
            win.height = self._new_h

    def undo(self) -> None:
        win = self._find_window()
        if win:
            win.width = self._old_w
            win.height = self._old_h

    def _find_window(self):
        for w in self._session._windows:
            if w.id == self._window_id:
                return w
        return None


class FocusWindowCommand(Command):
    """Bring a window to focus."""

    def __init__(self, session: Any, window_id: str,
                 description: str = "Focus window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._old_focused_id = None

    def execute(self) -> None:
        self._old_focused_id = self._session._focused_window_id
        self._session._focus_window(self._window_id)

    def undo(self) -> None:
        if self._old_focused_id:
            self._session._focus_window(self._old_focused_id)

    def redo(self) -> None:
        self._session._focus_window(self._window_id)


class MinimizeWindowCommand(Command):
    """Minimize a window."""

    def __init__(self, session: Any, window_id: str,
                 description: str = "Minimize window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._was_minimized = False

    def execute(self) -> None:
        win = self._find_window()
        if win:
            self._was_minimized = win.minimized
            self._session.minimize_window(self._window_id)

    def undo(self) -> None:
        win = self._find_window()
        if win:
            win.minimized = self._was_minimized
            if not self._was_minimized and self._session._focused_window_id is None:
                self._session._auto_focus_topmost()

    def _find_window(self):
        for w in self._session._windows:
            if w.id == self._window_id:
                return w
        return None


class MaximizeWindowCommand(Command):
    """Maximize or restore a window."""

    def __init__(self, session: Any, window_id: str,
                 description: str = "Maximize window") -> None:
        super().__init__(description)
        self._session = session
        self._window_id = window_id
        self._was_maximized = False

    def execute(self) -> None:
        win = self._find_window()
        if win:
            self._was_maximized = win.maximized
            self._session.maximize_window(self._window_id)

    def undo(self) -> None:
        win = self._find_window()
        if win:
            self._session.maximize_window(self._window_id)

    def _find_window(self):
        for w in self._session._windows:
            if w.id == self._window_id:
                return w
        return None


class ChangePropertyCommand(Command):
    """Change a property on a component."""

    def __init__(self, session: Any, component_id: str,
                 property_name: str, new_value: Any,
                 description: str = "Change property") -> None:
        super().__init__(description)
        self._session = session
        self._component_id = component_id
        self._property_name = property_name
        self._new_value = new_value
        self._old_value = None

    def execute(self) -> None:
        comp = self._session._doc.find_component(self._component_id)
        if comp:
            self._old_value = comp.properties.get(self._property_name)
            comp.properties[self._property_name] = self._new_value

    def undo(self) -> None:
        comp = self._session._doc.find_component(self._component_id)
        if comp and self._old_value is not None:
            comp.properties[self._property_name] = self._old_value
        elif comp:
            comp.properties.pop(self._property_name, None)

    def redo(self) -> None:
        comp = self._session._doc.find_component(self._component_id)
        if comp:
            comp.properties[self._property_name] = self._new_value


class ChangeThemeCommand(Command):
    """Switch the active theme."""

    def __init__(self, session: Any, theme_name: str,
                 description: str = "Change theme") -> None:
        super().__init__(description)
        self._session = session
        self._theme_name = theme_name
        self._old_theme = None

    def execute(self) -> None:
        self._old_theme = self._session._doc.themes.get("active", "Eclipse")
        self._session._doc.themes["active"] = self._theme_name

    def undo(self) -> None:
        if self._old_theme is not None:
            self._session._doc.themes["active"] = self._old_theme

    def redo(self) -> None:
        self._session._doc.themes["active"] = self._theme_name


# ---------------------------------------------------------------------------
# Undo / Redo Manager
# ---------------------------------------------------------------------------

class UndoManager:
    """Maintains undo and redo stacks with configurable depth.

    Parameters
    ----------
    max_depth : int
        Maximum number of commands kept in the undo stack.
        Older commands are discarded.
    """

    def __init__(self, max_depth: int = 100) -> None:
        self._undo_stack: List[Command] = []
        self._redo_stack: List[Command] = []
        self._max_depth = max_depth
        self._listeners: List = []

    # -- Public API --

    def push(self, command: Command) -> None:
        """Execute a command and push it onto the undo stack."""
        command.execute()
        self._undo_stack.append(command)
        # Clear redo stack (new action invalidates redo history)
        self._redo_stack.clear()
        # Trim undo stack
        while len(self._undo_stack) > self._max_depth:
            self._undo_stack.pop(0)
        self._notify("push", command)

    def undo(self) -> Optional[Command]:
        """Undo the most recent command. Returns the undone command."""
        if not self._undo_stack:
            return None
        cmd = self._undo_stack.pop()
        cmd.undo()
        self._redo_stack.append(cmd)
        self._notify("undo", cmd)
        return cmd

    def redo(self) -> Optional[Command]:
        """Redo the most recently undone command. Returns the redone command."""
        if not self._redo_stack:
            return None
        cmd = self._redo_stack.pop()
        cmd.redo()
        self._undo_stack.append(cmd)
        self._notify("redo", cmd)
        return cmd

    def clear(self) -> None:
        """Clear both stacks."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._notify("clear", None)

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_description(self) -> Optional[str]:
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    @property
    def redo_description(self) -> Optional[str]:
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None

    @property
    def undo_depth(self) -> int:
        return len(self._undo_stack)

    @property
    def redo_depth(self) -> int:
        return len(self._redo_stack)

    # -- Listeners --

    def on_change(self, callback) -> None:
        """Register a listener for stack changes (for UI updates)."""
        self._listeners.append(callback)

    def _notify(self, action: str, command: Optional[Command]) -> None:
        for cb in self._listeners:
            try:
                cb(action, command)
            except Exception:
                pass

    # -- Summary --

    def summary(self) -> Dict[str, Any]:
        return {
            "undo_depth": self.undo_depth,
            "redo_depth": self.redo_depth,
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "last_undo": self.undo_description,
            "last_redo": self.redo_description,
        }


# ---------------------------------------------------------------------------
# Session mixin helper (applies UndoManager to DesktopSession)
# ---------------------------------------------------------------------------

def install_undo(session: Any, max_depth: int = 100) -> UndoManager:
    """Attach an UndoManager to a DesktopSession.

    Sets ``session._undo_manager`` so the built-in ``undo_manager``,
    ``execute()``, ``undo()``, and ``redo()`` methods work.  If the
    session class does not already provide those, it also monkey-patches
    them as a fallback.
    """
    manager = UndoManager(max_depth=max_depth)
    session._undo_manager = manager

    # If the session class already provides undo support (e.g.
    # DesktopSession), we're done.
    if hasattr(type(session), 'undo_manager') and isinstance(
        getattr(type(session), 'undo_manager'), property):
        return manager

    # Fallback: monkey-patch for plain objects.
    def execute(cmd: Command) -> None:
        manager.push(cmd)

    def undo() -> Optional[Command]:
        return manager.undo()

    def redo() -> Optional[Command]:
        return manager.redo()

    session.execute = execute  # type: ignore[attr-defined]
    session.undo = undo  # type: ignore[attr-defined]
    session.redo = redo  # type: ignore[attr-defined]
    session.undo_manager = property(lambda self: self._undo_manager)  # type: ignore

    return manager


__all__ = [
    "Command",
    "Transaction",
    "UndoManager",
    "AddWindowCommand",
    "RemoveWindowCommand",
    "MoveWindowCommand",
    "ResizeWindowCommand",
    "FocusWindowCommand",
    "MinimizeWindowCommand",
    "MaximizeWindowCommand",
    "ChangePropertyCommand",
    "ChangeThemeCommand",
    "install_undo",
]
