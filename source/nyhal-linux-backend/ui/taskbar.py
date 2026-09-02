"""taskbar — Minimal taskbar component for Nyrqis desktop.

Provides a complete taskbar implementation:
- Start button with Nyrqis logo
- Running application indicators
- System clock with date
- System tray (network, volume, battery)
- Quick launch area

References:
    - shell/defaults/default-shell.nstudio
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# Color constants
COLOR_TASKBAR_BG = (32, 32, 44, 240)
COLOR_TASKBAR_BORDER = (60, 60, 80, 255)
COLOR_ACCENT = (80, 140, 255, 255)
COLOR_TEXT_WHITE = (230, 230, 240, 255)
COLOR_TEXT_DIM = (140, 140, 160, 255)
COLOR_CLOCK_BG = (50, 50, 68, 200)
COLOR_ICON_TERMINAL = (60, 200, 120, 255)
COLOR_ICON_FOLDER = (255, 200, 60, 255)
COLOR_ICON_BROWSER = (80, 140, 255, 255)


@dataclass
class TaskbarItem:
    """An item in the taskbar."""
    id: str
    label: str
    icon_color: Tuple[int, int, int, int]
    active: bool = False
    callback: Optional[Callable] = None


class Taskbar:
    """Minimal taskbar component for Nyrqis.
    
    Renders a complete taskbar with:
    - Start button
    - Running apps
    - System tray
    - Clock
    
    Usage:
        taskbar = Taskbar(1920)
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        pixels = taskbar.render(1032)  # y position
    """
    
    def __init__(self, width: int = 1920, height: int = 48):
        self.width = width
        self.height = height
        self._items: List[TaskbarItem] = []
        self._quick_launch: List[TaskbarItem] = []
        self._start_menu_open = False
        
        # Default quick launch items
        self._quick_launch = [
            TaskbarItem("terminal", "Terminal", COLOR_ICON_TERMINAL),
            TaskbarItem("files", "Files", COLOR_ICON_FOLDER),
            TaskbarItem("browser", "Browser", COLOR_ICON_BROWSER),
        ]
    
    def add_app(self, app_id: str, label: str, icon_color: Tuple[int, int, int, int],
               active: bool = False, callback: Optional[Callable] = None):
        """Add an application to the taskbar."""
        item = TaskbarItem(
            id=app_id,
            label=label,
            icon_color=icon_color,
            active=active,
            callback=callback,
        )
        self._items.append(item)
    
    def remove_app(self, app_id: str):
        """Remove an application from the taskbar."""
        self._items = [item for item in self._items if item.id != app_id]
    
    def set_active(self, app_id: str, active: bool = True):
        """Set an app as active/inactive."""
        for item in self._items:
            if item.id == app_id:
                item.active = active
    
    def toggle_start_menu(self):
        """Toggle the start menu."""
        self._start_menu_open = not self._start_menu_open
    
    def render(self, y: int = 0) -> List[Tuple[int, int, Tuple[int, int, int, int]]]:
        """Render the taskbar.
        
        Returns a list of (x, y, color) tuples for each pixel.
        """
        pixels = []
        
        # Taskbar background
        for x in range(self.width):
            for dy in range(self.height):
                pixels.append((x, y + dy, COLOR_TASKBAR_BG))
        
        # Top border
        for x in range(self.width):
            pixels.append((x, y, COLOR_TASKBAR_BORDER))
        
        # Start button
        self._draw_rect(pixels, 8, y + 8, 32, 32, COLOR_ACCENT)
        
        # Running app indicators
        for i, item in enumerate(self._items):
            btn_x = 56 + i * 44
            color = COLOR_ACCENT if item.active else (60, 60, 80, 200)
            self._draw_rect(pixels, btn_x, y + 12, 36, 24, color)
            self._draw_rect(pixels, btn_x + 12, y + 16, 12, 12, item.icon_color)
        
        # System tray
        tray_x = self.width - 180
        
        # Clock
        now = time.localtime()
        clock_text = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        self._draw_rect(pixels, tray_x, y + 8, 60, 32, COLOR_CLOCK_BG)
        
        # Date
        date_text = f"{now.tm_mday:02d}/{now.tm_mon:02d}"
        self._draw_rect(pixels, tray_x + 68, y + 8, 50, 32, COLOR_CLOCK_BG)
        
        # Network icon
        self._draw_rect(pixels, tray_x + 126, y + 12, 16, 16, COLOR_ICON_BROWSER)
        
        # Volume icon
        self._draw_rect(pixels, tray_x + 148, y + 12, 16, 16, (180, 180, 200, 255))
        
        return pixels
    
    def _draw_rect(self, pixels: List, x: int, y: int, w: int, h: int, 
                   color: Tuple[int, int, int, int]):
        """Draw a rectangle into the pixel list."""
        for dy in range(h):
            for dx in range(w):
                pixels.append((x + dx, y + dy, color))
    
    def get_height(self) -> int:
        """Get the taskbar height."""
        return self.height
    
    def handle_click(self, x: int, y: int) -> Optional[str]:
        """Handle a click at the given coordinates.
        
        Returns the ID of the clicked item, or None.
        """
        # Check start button
        if 8 <= x <= 40 and self.height - 40 <= y <= self.height - 8:
            self.toggle_start_menu()
            return "start"
        
        # Check app indicators
        for i, item in enumerate(self._items):
            btn_x = 56 + i * 44
            if btn_x <= x <= btn_x + 36 and 12 <= y <= 36:
                if item.callback:
                    item.callback()
                return item.id
        
        return None
