#!/usr/bin/env python3
"""Window manager for the Nyrqis desktop.

Features:
- Drag-to-move windows by title bar
- Drag-to-resize from edges and corners
- Snap-to-edge (left/right/top/bottom halves, quarters)
- Workspace switching (multiple virtual desktops)
- Minimize/maximize/restore with smooth transitions
- Z-order management (raise, lower, focus)
- Window grouping and task switching
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Window states
# ---------------------------------------------------------------------------

class WindowState(Enum):
    """Window display states."""
    NORMAL = "normal"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"
    SNAP_LEFT = "snap_left"
    SNAP_RIGHT = "snap_right"
    SNAP_TOP = "snap_top"
    SNAP_BOTTOM = "snap_bottom"
    FULLSCREEN = "fullscreen"


class SnapZone(Enum):
    """Snap zones for window placement."""
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"
    MAXIMIZE = "maximize"


# ---------------------------------------------------------------------------
# Managed window
# ---------------------------------------------------------------------------

@dataclass
class ManagedWindow:
    """A window managed by the window manager."""
    id: str
    title: str = ""
    x: int = 100
    y: int = 100
    width: int = 800
    height: int = 600
    min_width: int = 300
    min_height: int = 200
    state: WindowState = WindowState.NORMAL
    workspace: int = 0
    focused: bool = False
    visible: bool = True
    draggable: bool = True
    resizable: bool = True
    
    # Saved state for restore
    _saved_x: int = 100
    _saved_y: int = 100
    _saved_w: int = 800
    _saved_h: int = 600
    
    # Animation
    _anim_x: int = 100
    _anim_y: int = 100
    _anim_w: int = 800
    _anim_h: int = 600
    _anim_progress: float = 1.0  # 1.0 = done
    
    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)
    
    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)
    
    def save_state(self) -> None:
        """Save current position for restore."""
        if self.state == WindowState.NORMAL:
            self._saved_x = self.x
            self._saved_y = self.y
            self._saved_w = self.width
            self._saved_h = self.height
    
    def restore_state(self) -> None:
        """Restore saved position."""
        self.x = self._saved_x
        self.y = self._saved_y
        self.width = self._saved_w
        self.height = self._saved_h


# ---------------------------------------------------------------------------
# Drag state
# ---------------------------------------------------------------------------

class DragMode(Enum):
    """What is being dragged."""
    NONE = "none"
    MOVE = "move"
    RESIZE_LEFT = "resize_left"
    RESIZE_RIGHT = "resize_right"
    RESIZE_TOP = "resize_top"
    RESIZE_BOTTOM = "resize_bottom"
    RESIZE_TOP_LEFT = "resize_top_left"
    RESIZE_TOP_RIGHT = "resize_top_right"
    RESIZE_BOTTOM_LEFT = "resize_bottom_left"
    RESIZE_BOTTOM_RIGHT = "resize_bottom_right"


# ---------------------------------------------------------------------------
# Window manager
# ---------------------------------------------------------------------------

class WindowManager:
    """Full-featured window manager.
    
    Parameters
    ----------
    screen_width : int
        Screen width in pixels.
    screen_height : int
        Screen height in pixels.
    taskbar_height : int
        Height reserved for the taskbar.
    """
    
    # Snap zone thresholds
    SNAP_THRESHOLD = 20  # pixels from edge to trigger snap
    EDGE_ZONE = 8  # pixels from edge for resize cursor
    
    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
        taskbar_height: int = 48,
    ):
        self._sw = screen_width
        self._sh = screen_height
        self._th = taskbar_height
        
        self._windows: List[ManagedWindow] = []
        self._workspaces: int = 4
        self._active_workspace: int = 0
        self._focused_id: Optional[str] = None
        
        # Drag state
        self._drag_mode: DragMode = DragMode.NONE
        self._drag_window: Optional[ManagedWindow] = None
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0
        self._drag_orig_x: int = 0
        self._drag_orig_y: int = 0
        self._drag_orig_w: int = 0
        self._drag_orig_h: int = 0
        
        # Snap preview
        self._snap_preview: Optional[Tuple[int, int, int, int]] = None
        self._snap_zone: SnapZone = SnapZone.NONE
        
        # Callbacks
        self._on_window_added: List[Callable] = []
        self._on_window_removed: List[Callable] = []
        self._on_workspace_changed: List[Callable] = []
    
    # -- Window management -------------------------------------------------
    
    def add_window(self, win: ManagedWindow) -> None:
        """Add a window to the manager."""
        win.workspace = self._active_workspace
        self._windows.append(win)
        self._focus_window(win.id)
        for cb in self._on_window_added:
            cb(win)
    
    def remove_window(self, window_id: str) -> bool:
        """Remove a window by ID."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                removed = self._windows.pop(i)
                if self._focused_id == window_id:
                    self._focused_id = None
                    self._auto_focus()
                for cb in self._on_window_removed:
                    cb(removed)
                return True
        return False
    
    def find_window(self, window_id: str) -> Optional[ManagedWindow]:
        for w in self._windows:
            if w.id == window_id:
                return w
        return None
    
    @property
    def windows(self) -> List[ManagedWindow]:
        return list(self._windows)
    
    @property
    def workspace_windows(self) -> List[ManagedWindow]:
        """Windows on the current workspace."""
        return [w for w in self._windows if w.workspace == self._active_workspace]
    
    @property
    def visible_windows(self) -> List[ManagedWindow]:
        """Visible windows on the current workspace."""
        return [w for w in self.workspace_windows if w.visible and w.state != WindowState.MINIMIZED]
    
    # -- Focus management --------------------------------------------------
    
    def _focus_window(self, window_id: str) -> None:
        """Focus a window and raise it."""
        for w in self._windows:
            w.focused = (w.id == window_id)
        self._focused_id = window_id
    
    def _auto_focus(self) -> None:
        """Focus the topmost visible window."""
        visible = self.visible_windows
        if visible:
            self._focus_window(visible[-1].id)
    
    def focus_next(self) -> Optional[ManagedWindow]:
        """Focus the next window in the workspace."""
        visible = self.visible_windows
        if not visible:
            return None
        if self._focused_id:
            for i, w in enumerate(visible):
                if w.id == self._focused_id:
                    next_idx = (i + 1) % len(visible)
                    self._focus_window(visible[next_idx].id)
                    return visible[next_idx]
        self._focus_window(visible[0].id)
        return visible[0]
    
    def focus_prev(self) -> Optional[ManagedWindow]:
        """Focus the previous window."""
        visible = self.visible_windows
        if not visible:
            return None
        if self._focused_id:
            for i, w in enumerate(visible):
                if w.id == self._focused_id:
                    prev_idx = (i - 1) % len(visible)
                    self._focus_window(visible[prev_idx].id)
                    return visible[prev_idx]
        self._focus_window(visible[-1].id)
        return visible[-1]
    
    @property
    def focused_window(self) -> Optional[ManagedWindow]:
        if self._focused_id:
            return self.find_window(self._focused_id)
        return None
    
    # -- Window state transitions ------------------------------------------
    
    def minimize(self, window_id: str) -> bool:
        win = self.find_window(window_id)
        if win and win.state != WindowState.MINIMIZED:
            win.save_state()
            win.state = WindowState.MINIMIZED
            win.visible = False
            self._auto_focus()
            return True
        return False
    
    def maximize(self, window_id: str) -> bool:
        win = self.find_window(window_id)
        if win:
            if win.state == WindowState.MAXIMIZED:
                return self.restore(window_id)
            win.save_state()
            win.state = WindowState.MAXIMIZED
            win.x = 0
            win.y = 0
            win.width = self._sw
            win.height = self._sh - self._th
            self._focus_window(window_id)
            return True
        return False
    
    def restore(self, window_id: str) -> bool:
        win = self.find_window(window_id)
        if win and win.state != WindowState.NORMAL:
            win.restore_state()
            win.state = WindowState.NORMAL
            win.visible = True
            self._focus_window(window_id)
            return True
        return False
    
    def close(self, window_id: str) -> bool:
        return self.remove_window(window_id)
    
    def raise_window(self, window_id: str) -> bool:
        """Raise a window to the top of the z-order."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                win = self._windows.pop(i)
                self._windows.append(win)
                self._focus_window(window_id)
                return True
        return False
    
    def lower_window(self, window_id: str) -> bool:
        """Lower a window to the bottom of the z-order."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                win = self._windows.pop(i)
                self._windows.insert(0, win)
                self._auto_focus()
                return True
        return False
    
    # -- Snap zones --------------------------------------------------------
    
    def _detect_snap_zone(self, x: int, y: int) -> SnapZone:
        """Detect snap zone based on mouse position."""
        if y <= self.SNAP_THRESHOLD:
            if x <= self.SNAP_THRESHOLD:
                return SnapZone.TOP_LEFT
            elif x >= self._sw - self.SNAP_THRESHOLD:
                return SnapZone.TOP_RIGHT
            elif x <= self._sw // 4:
                return SnapZone.LEFT
            elif x >= self._sw * 3 // 4:
                return SnapZone.RIGHT
            else:
                return SnapZone.MAXIMIZE
        elif x <= self.SNAP_THRESHOLD:
            return SnapZone.LEFT
        elif x >= self._sw - self.SNAP_THRESHOLD:
            return SnapZone.RIGHT
        elif y >= self._sh - self._th - self.SNAP_THRESHOLD:
            return SnapZone.BOTTOM
        return SnapZone.NONE
    
    def _get_snap_rect(self, zone: SnapZone) -> Tuple[int, int, int, int]:
        """Get the rectangle for a snap zone."""
        half_w = self._sw // 2
        half_h = (self._sh - self._th) // 2
        quarter_w = self._sw // 4
        quarter_h = (self._sh - self._th) // 4
        
        rects = {
            SnapZone.LEFT: (0, 0, half_w, self._sh - self._th),
            SnapZone.RIGHT: (half_w, 0, half_w, self._sh - self._th),
            SnapZone.TOP: (0, 0, self._sw, half_h),
            SnapZone.BOTTOM: (0, half_h, self._sw, half_h),
            SnapZone.TOP_LEFT: (0, 0, half_w, half_h),
            SnapZone.TOP_RIGHT: (half_w, 0, half_w, half_h),
            SnapZone.BOTTOM_LEFT: (0, half_h, half_w, half_h),
            SnapZone.BOTTOM_RIGHT: (half_w, half_h, half_w, half_h),
            SnapZone.CENTER: (quarter_w, quarter_h, half_w, half_h),
            SnapZone.MAXIMIZE: (0, 0, self._sw, self._sh - self._th),
        }
        return rects.get(zone, (0, 0, 800, 600))
    
    # -- Drag operations ---------------------------------------------------
    
    def start_move(self, window_id: str, mouse_x: int, mouse_y: int) -> bool:
        """Start moving a window."""
        win = self.find_window(window_id)
        if win and win.draggable:
            self._drag_mode = DragMode.MOVE
            self._drag_window = win
            self._drag_start_x = mouse_x
            self._drag_start_y = mouse_y
            self._drag_orig_x = win.x
            self._drag_orig_y = win.y
            # Restore from snap/maximize on drag
            if win.state in (WindowState.SNAP_LEFT, WindowState.SNAP_RIGHT,
                              WindowState.MAXIMIZED):
                win.restore_state()
                win.state = WindowState.NORMAL
            self.raise_window(window_id)
            return True
        return False
    
    def start_resize(self, window_id: str, mouse_x: int, mouse_y: int,
                    edge: str = "bottom_right") -> bool:
        """Start resizing a window from an edge."""
        win = self.find_window(window_id)
        if win and win.resizable:
            mode_map = {
                "left": DragMode.RESIZE_LEFT,
                "right": DragMode.RESIZE_RIGHT,
                "top": DragMode.RESIZE_TOP,
                "bottom": DragMode.RESIZE_BOTTOM,
                "top_left": DragMode.RESIZE_TOP_LEFT,
                "top_right": DragMode.RESIZE_TOP_RIGHT,
                "bottom_left": DragMode.RESIZE_BOTTOM_LEFT,
                "bottom_right": DragMode.RESIZE_BOTTOM_RIGHT,
            }
            self._drag_mode = mode_map.get(edge, DragMode.RESIZE_BOTTOM_RIGHT)
            self._drag_window = win
            self._drag_start_x = mouse_x
            self._drag_start_y = mouse_y
            self._drag_orig_x = win.x
            self._drag_orig_y = win.y
            self._drag_orig_w = win.width
            self._drag_orig_h = win.height
            return True
        return False
    
    def update_drag(self, mouse_x: int, mouse_y: int) -> Optional[SnapZone]:
        """Update drag operation with current mouse position."""
        if not self._drag_window:
            return SnapZone.NONE
        
        dx = mouse_x - self._drag_start_x
        dy = mouse_y - self._drag_start_y
        win = self._drag_window
        
        if self._drag_mode == DragMode.MOVE:
            win.x = self._drag_orig_x + dx
            win.y = self._drag_orig_y + dy
            
            # Detect snap zone
            zone = self._detect_snap_zone(mouse_x, mouse_y)
            if zone != SnapZone.NONE:
                self._snap_preview = self._get_snap_rect(zone)
                self._snap_zone = zone
            else:
                self._snap_preview = None
                self._snap_zone = SnapZone.NONE
            
            return zone
        
        elif self._drag_mode == DragMode.RESIZE_RIGHT:
            win.width = max(win.min_width, self._drag_orig_w + dx)
        elif self._drag_mode == DragMode.RESIZE_BOTTOM:
            win.height = max(win.min_height, self._drag_orig_h + dy)
        elif self._drag_mode == DragMode.RESIZE_LEFT:
            new_w = max(win.min_width, self._drag_orig_w - dx)
            win.x = self._drag_orig_x + (self._drag_orig_w - new_w)
            win.width = new_w
        elif self._drag_mode == DragMode.RESIZE_TOP:
            new_h = max(win.min_height, self._drag_orig_h - dy)
            win.y = self._drag_orig_y + (self._drag_orig_h - new_h)
            win.height = new_h
        
        return SnapZone.NONE
    
    def end_drag(self) -> Optional[ManagedWindow]:
        """End the current drag operation."""
        if not self._drag_window:
            return None
        
        win = self._drag_window
        
        if self._drag_mode == DragMode.MOVE and self._snap_zone != SnapZone.NONE:
            # Apply snap
            sx, sy, sw, sh = self._get_snap_rect(self._snap_zone)
            win.save_state()
            win.x, win.y, win.width, win.height = sx, sy, sw, sh
            if self._snap_zone == SnapZone.MAXIMIZE:
                win.state = WindowState.MAXIMIZED
            elif self._snap_zone == SnapZone.LEFT:
                win.state = WindowState.SNAP_LEFT
            elif self._snap_zone == SnapZone.RIGHT:
                win.state = WindowState.SNAP_RIGHT
        
        result = win
        self._drag_mode = DragMode.NONE
        self._drag_window = None
        self._snap_preview = None
        self._snap_zone = SnapZone.NONE
        return result
    
    @property
    def snap_preview(self) -> Optional[Tuple[int, int, int, int]]:
        return self._snap_preview
    
    @property
    def drag_mode(self) -> DragMode:
        return self._drag_mode
    
    # -- Workspace management ----------------------------------------------
    
    def switch_workspace(self, workspace: int) -> bool:
        """Switch to a workspace."""
        if 0 <= workspace < self._workspaces and workspace != self._active_workspace:
            # Hide current workspace windows
            for w in self._windows:
                if w.workspace == self._active_workspace:
                    w.visible = False
            
            self._active_workspace = workspace
            
            # Show new workspace windows
            for w in self._windows:
                if w.workspace == self._active_workspace:
                    w.visible = True
            
            self._auto_focus()
            for cb in self._on_workspace_changed:
                cb(workspace)
            return True
        return False
    
    def move_to_workspace(self, window_id: str, workspace: int) -> bool:
        """Move a window to a different workspace."""
        win = self.find_window(window_id)
        if win and 0 <= workspace < self._workspaces:
            win.workspace = workspace
            win.visible = (workspace == self._active_workspace)
            if win.workspace != self._active_workspace:
                self._auto_focus()
            return True
        return False
    
    @property
    def active_workspace(self) -> int:
        return self._active_workspace
    
    @property
    def workspace_count(self) -> int:
        return self._workspaces
    
    # -- Resize edge detection ---------------------------------------------
    
    def detect_resize_edge(self, window_id: str, x: int, y: int) -> str:
        """Detect which edge/corner the mouse is on for a window."""
        win = self.find_window(window_id)
        if not win:
            return ""
        
        e = self.EDGE_ZONE
        on_left = x <= win.x + e
        on_right = x >= win.x + win.width - e
        on_top = y <= win.y + e
        on_bottom = y >= win.y + win.height - e
        
        if on_top and on_left:
            return "top_left"
        elif on_top and on_right:
            return "top_right"
        elif on_bottom and on_left:
            return "bottom_left"
        elif on_bottom and on_right:
            return "bottom_right"
        elif on_left:
            return "left"
        elif on_right:
            return "right"
        elif on_top:
            return "top"
        elif on_bottom:
            return "bottom"
        return ""
    
    def hit_test(self, x: int, y: int) -> Optional[str]:
        """Hit test: returns window_id or None."""
        # Check from top of z-order (last in list)
        for win in reversed(self._windows):
            if not win.visible or win.state == WindowState.MINIMIZED:
                continue
            if (win.x <= x <= win.x + win.width and
                win.y <= y <= win.y + win.height):
                return win.id
        return None
    
    def hit_test_titlebar(self, x: int, y: int) -> Optional[str]:
        """Check if click is in a window's title bar."""
        for win in reversed(self._windows):
            if not win.visible or win.state == WindowState.MINIMIZED:
                continue
            if (win.x <= x <= win.x + win.width and
                win.y <= y <= win.y + 32):  # 32px title bar
                return win.id
        return None
    
    # -- Keyboard handling -------------------------------------------------
    
    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Handle keyboard input."""
        mods = modifiers or {}
        
        if key == "Tab" and mods.get("alt"):
            self.focus_next()
            return "focus"
        elif key == "Tab" and mods.get("alt") and mods.get("shift"):
            self.focus_prev()
            return "focus"
        elif key == "Up" and mods.get("alt"):
            self.maximize(self._focused_id or "")
            return "maximize"
        elif key == "Down" and mods.get("alt"):
            self.minimize(self._focused_id or "")
            return "minimize"
        elif key == "w" and mods.get("ctrl"):
            self.close(self._focused_id or "")
            return "close"
        elif key in ("1", "2", "3", "4") and mods.get("ctrl"):
            ws = int(key) - 1
            self.switch_workspace(ws)
            return "workspace"
        elif key == "m" and mods.get("ctrl"):
            self.maximize(self._focused_id or "")
            return "maximize"
        elif key == "n" and mods.get("ctrl"):
            self.minimize(self._focused_id or "")
            return "minimize"
        
        return ""
    
    # -- Callbacks ---------------------------------------------------------
    
    def on_window_added(self, callback: Callable) -> None:
        self._on_window_added.append(callback)
    
    def on_window_removed(self, callback: Callable) -> None:
        self._on_window_removed.append(callback)
    
    def on_workspace_changed(self, callback: Callable) -> None:
        self._on_workspace_changed.append(callback)
    
    def __repr__(self) -> str:
        return (
            f"WindowManager(windows={len(self._windows)}, "
            f"workspace={self._active_workspace}, "
            f"focused={self._focused_id})"
        )
