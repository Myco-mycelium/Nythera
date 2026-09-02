#!/usr/bin/env python3
"""Tiling layout manager for the Nyrqis desktop.

Provides window tiling with multiple layout modes:
- Horizontal (side by side)
- Vertical (stacked)
- Grid (auto-arranged)
- Master-stack (one main + sidebar)
- Monocle (fullscreen single)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Layout modes
# ---------------------------------------------------------------------------

class LayoutMode(Enum):
    """Tiling layout modes."""
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    GRID = "grid"
    MASTER_STACK = "master_stack"
    MONOCLE = "monocle"


# ---------------------------------------------------------------------------
# Window record for tiling
# ---------------------------------------------------------------------------

@dataclass
class TileWindow:
    """A window tracked by the tiling manager."""
    id: str
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    visible: bool = True
    minimized: bool = False
    floating: bool = False
    workspace: int = 0
    
    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


# ---------------------------------------------------------------------------
# Layout algorithms
# ---------------------------------------------------------------------------

def _layout_horizontal(
    windows: List[TileWindow],
    area_x: int, area_y: int, area_w: int, area_h: int,
    gap: int = 4,
    **kwargs: Any,
) -> None:
    """Arrange windows side by side horizontally."""
    visible = [w for w in windows if w.visible and not w.minimized]
    if not visible:
        return
    
    n = len(visible)
    col_w = (area_w - gap * (n - 1)) // n if n > 0 else area_w
    
    for i, win in enumerate(visible):
        win.x = area_x + i * (col_w + gap)
        win.y = area_y
        win.width = col_w
        win.height = area_h


def _layout_vertical(
    windows: List[TileWindow],
    area_x: int, area_y: int, area_w: int, area_h: int,
    gap: int = 4,
    **kwargs: Any,
) -> None:
    """Arrange windows stacked vertically."""
    visible = [w for w in windows if w.visible and not w.minimized]
    if not visible:
        return
    
    n = len(visible)
    row_h = (area_h - gap * (n - 1)) // n if n > 0 else area_h
    
    for i, win in enumerate(visible):
        win.x = area_x
        win.y = area_y + i * (row_h + gap)
        win.width = area_w
        win.height = row_h


def _layout_grid(
    windows: List[TileWindow],
    area_x: int, area_y: int, area_w: int, area_h: int,
    gap: int = 4,
    **kwargs: Any,
) -> None:
    """Arrange windows in a grid."""
    visible = [w for w in windows if w.visible and not w.minimized]
    if not visible:
        return
    
    n = len(visible)
    cols = max(1, int(n ** 0.5 + 0.5))
    rows = (n + cols - 1) // cols
    
    cell_w = (area_w - gap * (cols - 1)) // cols
    cell_h = (area_h - gap * (rows - 1)) // rows
    
    for i, win in enumerate(visible):
        row = i // cols
        col = i % cols
        win.x = area_x + col * (cell_w + gap)
        win.y = area_y + row * (cell_h + gap)
        win.width = cell_w
        win.height = cell_h


def _layout_master_stack(
    windows: List[TileWindow],
    area_x: int, area_y: int, area_w: int, area_h: int,
    gap: int = 4,
    master_ratio: float = 0.6,
) -> None:
    """Master-stack: one large window on the left, others stacked on the right."""
    visible = [w for w in windows if w.visible and not w.minimized]
    if not visible:
        return
    
    if len(visible) == 1:
        visible[0].x = area_x
        visible[0].y = area_y
        visible[0].width = area_w
        visible[0].height = area_h
        return
    
    # Master window
    master_w = int(area_w * master_ratio) - gap
    visible[0].x = area_x
    visible[0].y = area_y
    visible[0].width = master_w
    visible[0].height = area_h
    
    # Stack windows
    stack = visible[1:]
    stack_x = area_x + master_w + gap
    stack_w = area_w - master_w - gap
    stack_h = (area_h - gap * (len(stack) - 1)) // len(stack) if stack else area_h
    
    for i, win in enumerate(stack):
        win.x = stack_x
        win.y = area_y + i * (stack_h + gap)
        win.width = stack_w
        win.height = stack_h


def _layout_monocle(
    windows: List[TileWindow],
    area_x: int, area_y: int, area_w: int, area_h: int,
    gap: int = 4,
    **kwargs: Any,
) -> None:
    """Fullscreen single window — only the focused window is shown."""
    visible = [w for w in windows if w.visible and not w.minimized]
    if not visible:
        return
    
    # Last window in list gets focus
    for win in visible[:-1]:
        win.visible = False
    
    focused = visible[-1]
    focused.visible = True
    focused.x = area_x
    focused.y = area_y
    focused.width = area_w
    focused.height = area_h


# Layout function registry
LAYOUT_FUNCTIONS = {
    LayoutMode.HORIZONTAL: _layout_horizontal,
    LayoutMode.VERTICAL: _layout_vertical,
    LayoutMode.GRID: _layout_grid,
    LayoutMode.MASTER_STACK: _layout_master_stack,
    LayoutMode.MONOCLE: _layout_monocle,
}


# ---------------------------------------------------------------------------
# Tiling manager
# ---------------------------------------------------------------------------

class TilingManager:
    """Multi-window tiling layout manager.
    
    Parameters
    ----------
    area_x : int
        Left edge of the tiling area.
    area_y : int
        Top edge of the tiling area.
    area_width : int
        Width of the tiling area.
    area_height : int
        Height of the tiling area.
    gap : int
        Gap between windows in pixels.
    """
    
    def __init__(
        self,
        area_x: int = 0,
        area_y: int = 0,
        area_width: int = 1920,
        area_height: int = 1032,  # 1080 - 48px taskbar
        gap: int = 4,
    ) -> None:
        self._area_x = area_x
        self._area_y = area_y
        self._area_width = area_width
        self._area_height = area_height
        self._gap = gap
        
        self._windows: List[TileWindow] = []
        self._layout_mode: LayoutMode = LayoutMode.MASTER_STACK
        self._master_ratio: float = 0.6
        self._focused_index: int = 0
        self._workspaces: Dict[int, List[TileWindow]] = {}
    
    # -- Window management -------------------------------------------------
    
    def add_window(self, win: TileWindow) -> None:
        """Add a window to the tiling manager."""
        self._windows.append(win)
        self.layout()
    
    def remove_window(self, window_id: str) -> bool:
        """Remove a window by ID."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                self._windows.pop(i)
                if self._focused_index >= len(self._windows):
                    self._focused_index = max(0, len(self._windows) - 1)
                self.layout()
                return True
        return False
    
    def find_window(self, window_id: str) -> Optional[TileWindow]:
        """Find a window by ID."""
        for w in self._windows:
            if w.id == window_id:
                return w
        return None
    
    def focus_window(self, window_id: str) -> bool:
        """Focus a window by ID."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                self._focused_index = i
                return True
        return False
    
    def focus_next(self) -> Optional[TileWindow]:
        """Focus the next window."""
        if not self._windows:
            return None
        self._focused_index = (self._focused_index + 1) % len(self._windows)
        return self._windows[self._focused_index]
    
    def focus_prev(self) -> Optional[TileWindow]:
        """Focus the previous window."""
        if not self._windows:
            return None
        self._focused_index = (self._focused_index - 1) % len(self._windows)
        return self._windows[self._focused_index]
    
    @property
    def focused_window(self) -> Optional[TileWindow]:
        """Get the currently focused window."""
        if 0 <= self._focused_index < len(self._windows):
            return self._windows[self._focused_index]
        return None
    
    # -- Layout ------------------------------------------------------------
    
    def layout(self) -> None:
        """Apply the current layout to all windows."""
        func = LAYOUT_FUNCTIONS.get(self._layout_mode, _layout_master_stack)
        func(
            self._windows,
            self._area_x, self._area_y,
            self._area_width, self._area_height,
            gap=self._gap,
            master_ratio=self._master_ratio,
        )
    
    def set_layout(self, mode: LayoutMode) -> None:
        """Change the layout mode."""
        if mode != self._layout_mode:
            self._layout_mode = mode
            # Restore visibility for monocle
            if mode != LayoutMode.MONOCLE:
                for w in self._windows:
                    w.visible = True
            self.layout()
    
    def cycle_layout(self) -> LayoutMode:
        """Cycle to the next layout mode."""
        modes = list(LayoutMode)
        idx = modes.index(self._layout_mode)
        self._layout_mode = modes[(idx + 1) % len(modes)]
        if self._layout_mode != LayoutMode.MONOCLE:
            for w in self._windows:
                w.visible = True
        self.layout()
        return self._layout_mode
    
    # -- Area management ---------------------------------------------------
    
    def set_area(self, x: int, y: int, width: int, height: int) -> None:
        """Set the tiling area dimensions."""
        self._area_x = x
        self._area_y = y
        self._area_width = width
        self._area_height = height
        self.layout()
    
    def set_gap(self, gap: int) -> None:
        """Set the gap between windows."""
        self._gap = max(0, gap)
        self.layout()
    
    def set_master_ratio(self, ratio: float) -> None:
        """Set the master area ratio (0.3 to 0.8)."""
        self._master_ratio = max(0.3, min(0.8, ratio))
        self.layout()
    
    # -- Keyboard input ----------------------------------------------------
    
    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Handle keyboard input.
        
        Returns action name or "" if unhandled.
        """
        mods = modifiers or {}
        
        if key == "l" and mods.get("ctrl") and mods.get("shift"):
            self.cycle_layout()
            return "layout"
        elif key == "j" and mods.get("ctrl"):
            self.focus_next()
            return "focus"
        elif key == "k" and mods.get("ctrl"):
            self.focus_prev()
            return "focus"
        elif key == "space" and mods.get("ctrl"):
            # Swap focused with first (master)
            if self._windows and self._focused_index > 0:
                self._windows[0], self._windows[self._focused_index] = (
                    self._windows[self._focused_index], self._windows[0]
                )
                self._focused_index = 0
                self.layout()
                return "swap"
        elif key == "Return" and mods.get("ctrl"):
            # Toggle monocle for focused window
            focused = self.focused_window
            if focused:
                if self._layout_mode == LayoutMode.MONOCLE:
                    self.set_layout(LayoutMode.MASTER_STACK)
                else:
                    self.set_layout(LayoutMode.MONOCLE)
                return "layout"
        elif key == "h":
            self.set_master_ratio(self._master_ratio - 0.05)
            return "resize"
        elif key == "l" and not mods.get("ctrl"):
            self.set_master_ratio(self._master_ratio + 0.05)
            return "resize"
        
        return ""
    
    # -- Properties --------------------------------------------------------
    
    @property
    def layout_mode(self) -> LayoutMode:
        return self._layout_mode
    
    @property
    def windows(self) -> List[TileWindow]:
        return list(self._windows)
    
    @property
    def window_count(self) -> int:
        return len(self._windows)
    
    @property
    def visible_count(self) -> int:
        return sum(1 for w in self._windows if w.visible and not w.minimized)
    
    @property
    def focused_index(self) -> int:
        return self._focused_index
    
    @property
    def master_ratio(self) -> float:
        return self._master_ratio
    
    @property
    def gap(self) -> int:
        return self._gap
    
    @property
    def area(self) -> Tuple[int, int, int, int]:
        return (self._area_x, self._area_y, self._area_width, self._area_height)
    
    def __repr__(self) -> str:
        return (
            f"TilingManager(layout={self._layout_mode.value}, "
            f"windows={len(self._windows)}, "
            f"focused={self._focused_index})"
        )
