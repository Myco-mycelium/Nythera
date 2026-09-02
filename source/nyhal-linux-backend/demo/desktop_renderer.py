"""desktop_renderer — Renders a complete Nyrqis desktop environment.

Draws a full desktop with:
- Desktop background with gradient
- Taskbar with start button, clock, system tray
- Window manager with title bars and decorations
- Start menu with app icons
- Desktop icons
- Multiple application windows

References:
    - ADR-0026: Wayland display-server integration
    - shell/defaults/desktop.nstudio
"""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# Color constants (R, G, B, A)
COLOR_BG_TOP = (24, 24, 32, 255)
COLOR_BG_BOTTOM = (40, 40, 56, 255)
COLOR_TASKBAR = (32, 32, 44, 240)
COLOR_TASKBAR_BORDER = (60, 60, 80, 255)
COLOR_WINDOW_BG = (35, 35, 48, 250)
COLOR_WINDOW_TITLE = (50, 50, 68, 255)
COLOR_WINDOW_TITLE_TEXT = (200, 200, 220, 255)
COLOR_WINDOW_BORDER = (70, 70, 95, 255)
COLOR_MENU_BG = (38, 38, 52, 245)
COLOR_MENU_HOVER = (60, 60, 85, 255)
COLOR_TEXT_WHITE = (230, 230, 240, 255)
COLOR_TEXT_DIM = (140, 140, 160, 255)
COLOR_ACCENT = (80, 140, 255, 255)
COLOR_CLOCK_BG = (50, 50, 68, 200)
COLOR_ICON_FOLDER = (255, 200, 60, 255)
COLOR_ICON_TERMINAL = (60, 200, 120, 255)
COLOR_ICON_BROWSER = (80, 140, 255, 255)
COLOR_ICON_SETTINGS = (180, 180, 200, 255)
COLOR_CLOSE_BTN = (220, 60, 60, 255)
COLOR_MINIMIZE_BTN = (60, 180, 120, 255)
COLOR_MAXIMIZE_BTN = (60, 140, 220, 255)


@dataclass
class Window:
    """A desktop window."""
    title: str
    x: int
    y: int
    width: int
    height: int
    content_color: Tuple[int, int, int, int] = COLOR_WINDOW_BG
    focused: bool = False
    minimized: bool = False


@dataclass
class DesktopIcon:
    """A desktop icon."""
    label: str
    x: int
    y: int
    icon_color: Tuple[int, int, int, int]
    icon_type: str = "generic"


class DesktopRenderer:
    """Renders a complete Nyrqis desktop.
    
    Usage:
        renderer = DesktopRenderer(1920, 1080)
        renderer.render()
        pixels = renderer.get_pixels()
    """
    
    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
        self._pixels = bytearray(width * height * 4)  # ARGB8888
        
        # Desktop state
        self._windows: List[Window] = []
        self._icons: List[DesktopIcon] = []
        self._taskbar_height = 48
        self._menu_open = False
        self._menu_items = [
            ("Terminal", COLOR_ICON_TERMINAL),
            ("Files", COLOR_ICON_FOLDER),
            ("Browser", COLOR_ICON_BROWSER),
            ("Settings", COLOR_ICON_SETTINGS),
            ("About", COLOR_ACCENT),
        ]
    
    def render(self):
        """Render the complete desktop."""
        # Clear with background gradient
        self._draw_gradient()
        
        # Draw desktop icons
        self._setup_icons()
        self._draw_icons()
        
        # Draw windows
        self._setup_windows()
        for window in self._windows:
            if not window.minimized:
                self._draw_window(window)
        
        # Draw taskbar
        self._draw_taskbar()
        
        # Draw start menu if open
        if self._menu_open:
            self._draw_start_menu()
    
    def _draw_gradient(self):
        """Draw the desktop background gradient."""
        for y in range(self.height):
            t = y / self.height
            r = int(COLOR_BG_TOP[0] * (1 - t) + COLOR_BG_BOTTOM[0] * t)
            g = int(COLOR_BG_TOP[1] * (1 - t) + COLOR_BG_BOTTOM[1] * t)
            b = int(COLOR_BG_TOP[2] * (1 - t) + COLOR_BG_BOTTOM[2] * t)
            
            for x in range(self.width):
                self._set_pixel(x, y, (r, g, b, 255))
    
    def _setup_icons(self):
        """Set up desktop icons."""
        if self._icons:
            return
        
        margin = 30
        spacing = 100
        start_y = 30
        
        self._icons = [
            DesktopIcon("Terminal", margin, start_y, COLOR_ICON_TERMINAL, "terminal"),
            DesktopIcon("Files", margin, start_y + spacing, COLOR_ICON_FOLDER, "folder"),
            DesktopIcon("Browser", margin, start_y + spacing * 2, COLOR_ICON_BROWSER, "browser"),
            DesktopIcon("Settings", margin, start_y + spacing * 3, COLOR_ICON_SETTINGS, "settings"),
        ]
    
    def _draw_icons(self):
        """Draw desktop icons."""
        for icon in self._icons:
            # Icon background (rounded rectangle approximation)
            self._draw_rect(icon.x, icon.y, 56, 56, icon.icon_color)
            
            # Icon symbol (simplified)
            cx, cy = icon.x + 28, icon.y + 28
            if icon.icon_type == "terminal":
                self._draw_rect(cx - 12, cy - 8, 24, 16, (0, 0, 0, 180))
                self._draw_rect(cx - 10, cy - 6, 8, 2, COLOR_ICON_TERMINAL)
            elif icon.icon_type == "folder":
                self._draw_rect(cx - 12, cy - 6, 24, 14, (0, 0, 0, 120))
                self._draw_rect(cx - 12, cy - 8, 10, 4, icon.icon_color)
            elif icon.icon_type == "browser":
                self._draw_rect(cx - 10, cy - 10, 20, 20, (0, 0, 0, 120))
                self._draw_rect(cx - 8, cy - 8, 16, 16, COLOR_ACCENT)
            elif icon.icon_type == "settings":
                self._draw_circle(cx, cy, 10, (0, 0, 0, 120))
                self._draw_circle(cx, cy, 6, icon.icon_color)
            
            # Label
            self._draw_text(icon.x - 2, icon.y + 64, icon.label, COLOR_TEXT_WHITE)
    
    def _setup_windows(self):
        """Set up application windows."""
        if self._windows:
            return
        
        self._windows = [
            Window(
                title="Terminal — nyrqis@desktop",
                x=200, y=80, width=720, height=480,
                content_color=(20, 20, 28, 250),
                focused=True,
            ),
            Window(
                title="Files — /home/user",
                x=400, y=160, width=640, height=420,
                content_color=(30, 30, 42, 250),
                focused=False,
            ),
            Window(
                title="Browser — Nyrqis Web",
                x=600, y=120, width=800, height=560,
                content_color=(25, 25, 35, 250),
                focused=False,
            ),
        ]
    
    def _draw_window(self, window: Window):
        """Draw a window with title bar and decorations."""
        x, y = window.x, window.y
        w, h = window.width, window.height
        
        # Window shadow
        self._draw_rect(x + 4, y + 4, w, h, (0, 0, 0, 60))
        
        # Window border
        border = 1
        if window.focused:
            self._draw_rect(x, y, w, h, COLOR_ACCENT)
        else:
            self._draw_rect(x, y, w, h, COLOR_WINDOW_BORDER)
        
        # Window background
        self._draw_rect(x + border, y + border, w - border * 2, h - border * 2, window.content_color)
        
        # Title bar
        title_h = 32
        self._draw_rect(x + border, y + border, w - border * 2, title_h, COLOR_WINDOW_TITLE)
        
        # Title text
        self._draw_text(x + 12, y + border + 8, window.title, COLOR_WINDOW_TITLE_TEXT)
        
        # Window control buttons
        btn_y = y + border + 8
        btn_size = 14
        
        # Close button (red)
        self._draw_circle(x + w - 24, btn_y + 7, btn_size // 2, COLOR_CLOSE_BTN)
        self._draw_rect(x + w - 28, btn_y + 6, 8, 2, (255, 255, 255, 200))
        
        # Minimize button (green)
        self._draw_circle(x + w - 44, btn_y + 7, btn_size // 2, COLOR_MINIMIZE_BTN)
        self._draw_rect(x + w - 48, btn_y + 6, 8, 2, (255, 255, 255, 200))
        
        # Maximize button (blue)
        self._draw_circle(x + w - 64, btn_y + 7, btn_size // 2, COLOR_MAXIMIZE_BTN)
        self._draw_rect(x + w - 68, btn_y + 5, 6, 6, (255, 255, 255, 0))
        self._draw_rect(x + w - 68, btn_y + 5, 6, 6, COLOR_MAXIMIZE_BTN)
        self._draw_rect(x + w - 67, btn_y + 6, 4, 4, (255, 255, 255, 200))
        
        # Content area placeholder
        content_y = y + border + title_h
        content_h = h - border * 2 - title_h
        
        # Draw some content based on window type
        if "Terminal" in window.title:
            self._draw_terminal_content(x + 12, content_y + 8, w - 24, content_h - 16)
        elif "Files" in window.title:
            self._draw_files_content(x + 12, content_y + 8, w - 24, content_h - 16)
        elif "Browser" in window.title:
            self._draw_browser_content(x + 12, content_y + 8, w - 24, content_h - 16)
    
    def _draw_terminal_content(self, x: int, y: int, w: int, h: int):
        """Draw terminal content."""
        lines = [
            "$ nyrqisctl status",
            "  Backend: running (PID 1234)",
            "  Session: active",
            "  Output: 1920x1080@60Hz",
            "",
            "$ nyrqis-init --headless",
            "Phase 1: Daemon started (200ms)",
            "Phase 2: Shell design loaded",
            "Phase 3: Session rendered",
            "",
            "$ nyrqis-ctl app list",
            "  Terminal    v1.0.0",
            "  Files       v1.2.0",
            "  Browser     v2.1.0",
            "",
            "$ █",
        ]
        
        for i, line in enumerate(lines):
            if y + i * 18 > y + h:
                break
            color = COLOR_ICON_TERMINAL if line.startswith("$") else COLOR_TEXT_DIM
            self._draw_text(x, y + i * 18, line, color)
    
    def _draw_files_content(self, x: int, y: int, w: int, h: int):
        """Draw file manager content."""
        items = [
            ("📁 Documents", COLOR_ICON_FOLDER),
            ("📁 Downloads", COLOR_ICON_FOLDER),
            ("📁 Music", COLOR_ICON_FOLDER),
            ("📁 Pictures", COLOR_ICON_FOLDER),
            ("📁 Videos", COLOR_ICON_FOLDER),
            ("📄 readme.md", COLOR_TEXT_DIM),
            ("📄 nyrqis.conf", COLOR_ACCENT),
        ]
        
        for i, (name, color) in enumerate(items):
            if y + i * 28 > y + h:
                break
            self._draw_rect(x, y + i * 28, w, 24, (45, 45, 60, 100))
            self._draw_text(x + 8, y + i * 28 + 4, name, color)
    
    def _draw_browser_content(self, x: int, y: int, w: int, h: int):
        """Draw browser content."""
        # URL bar
        self._draw_rect(x, y, w, 32, (50, 50, 65, 200))
        self._draw_text(x + 8, y + 8, "🔒 https://nyrqis.dev", COLOR_TEXT_DIM)
        
        # Page content
        self._draw_text(x, y + 50, "Welcome to Nyrqis", COLOR_TEXT_WHITE)
        self._draw_text(x, y + 74, "A modern operating system built with", COLOR_TEXT_DIM)
        self._draw_text(x, y + 92, "Rust, Python, and Wayland", COLOR_TEXT_DIM)
        
        # Button
        self._draw_rect(x, y + 130, 160, 40, COLOR_ACCENT)
        self._draw_text(x + 20, y + 138, "Get Started", COLOR_TEXT_WHITE)
    
    def _draw_taskbar(self):
        """Draw the taskbar."""
        y = self.height - self._taskbar_height
        
        # Taskbar background
        self._draw_rect(0, y, self.width, self._taskbar_height, COLOR_TASKBAR)
        
        # Top border
        self._draw_rect(0, y, self.width, 1, COLOR_TASKBAR_BORDER)
        
        # Start button
        self._draw_rect(8, y + 8, 32, 32, COLOR_ACCENT)
        self._draw_text(14, y + 14, "N", COLOR_TEXT_WHITE)
        
        # Taskbar separator
        self._draw_rect(52, y + 8, 1, 32, COLOR_TASKBAR_BORDER)
        
        # Running app indicators
        for i, window in enumerate(self._windows):
            if not window.minimized:
                btn_x = 64 + i * 44
                self._draw_rect(btn_x, y + 12, 36, 24, 
                              COLOR_ACCENT if window.focused else (60, 60, 80, 200))
                # App icon (simplified)
                icon_color = COLOR_ICON_TERMINAL if "Terminal" in window.title else \
                            COLOR_ICON_FOLDER if "Files" in window.title else \
                            COLOR_ICON_BROWSER
                self._draw_rect(btn_x + 12, y + 16, 12, 12, icon_color)
        
        # System tray (right side)
        tray_x = self.width - 180
        
        # Clock
        now = time.localtime()
        clock_text = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        self._draw_rect(tray_x, y + 8, 60, 32, COLOR_CLOCK_BG)
        self._draw_text(tray_x + 8, y + 14, clock_text, COLOR_TEXT_WHITE)
        
        # Date
        date_text = f"{now.tm_mday:02d}/{now.tm_mon:02d}"
        self._draw_rect(tray_x + 68, y + 8, 50, 32, COLOR_CLOCK_BG)
        self._draw_text(tray_x + 74, y + 14, date_text, COLOR_TEXT_DIM)
        
        # Network icon
        self._draw_rect(tray_x + 126, y + 12, 16, 16, COLOR_ICON_BROWSER)
        
        # Volume icon
        self._draw_rect(tray_x + 148, y + 12, 16, 16, COLOR_ICON_SETTINGS)
    
    def _draw_start_menu(self):
        """Draw the start menu."""
        menu_x = 8
        menu_y = self.height - self._taskbar_height - 320
        menu_w = 280
        menu_h = 312
        
        # Menu background
        self._draw_rect(menu_x, menu_y, menu_w, menu_h, COLOR_MENU_BG)
        
        # Menu border
        self._draw_rect(menu_x, menu_y, menu_w, 1, COLOR_TASKBAR_BORDER)
        self._draw_rect(menu_x, menu_y + menu_h - 1, menu_w, 1, COLOR_TASKBAR_BORDER)
        self._draw_rect(menu_x, menu_y, 1, menu_h, COLOR_TASKBAR_BORDER)
        self._draw_rect(menu_x + menu_w - 1, menu_y, 1, menu_h, COLOR_TASKBAR_BORDER)
        
        # Menu title
        self._draw_text(menu_x + 16, menu_y + 12, "Nyrqis", COLOR_ACCENT)
        
        # Search bar
        self._draw_rect(menu_x + 12, menu_y + 44, menu_w - 24, 28, (50, 50, 65, 200))
        self._draw_text(menu_x + 20, menu_y + 50, "🔍 Search...", COLOR_TEXT_DIM)
        
        # Menu items
        for i, (name, color) in enumerate(self._menu_items):
            item_y = menu_y + 84 + i * 44
            
            # Hover effect (first item)
            if i == 0:
                self._draw_rect(menu_x + 4, item_y, menu_w - 8, 36, COLOR_MENU_HOVER)
            
            # Icon
            self._draw_rect(menu_x + 16, item_y + 8, 20, 20, color)
            
            # Label
            self._draw_text(menu_x + 44, item_y + 10, name, COLOR_TEXT_WHITE)
        
        # Power button
        self._draw_rect(menu_x + 12, menu_y + menu_h - 44, menu_w - 24, 32, (60, 60, 80, 200))
        self._draw_text(menu_x + 20, menu_y + menu_h - 38, "⏻ Power", COLOR_TEXT_DIM)
    
    def _set_pixel(self, x: int, y: int, color: Tuple[int, int, int, int]):
        """Set a pixel color (ARGB8888)."""
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 4
            self._pixels[offset] = color[2]      # B
            self._pixels[offset + 1] = color[1]  # G
            self._pixels[offset + 2] = color[0]  # R
            self._pixels[offset + 3] = color[3]  # A
    
    def _draw_rect(self, x: int, y: int, w: int, h: int, color: Tuple[int, int, int, int]):
        """Draw a filled rectangle."""
        for dy in range(h):
            for dx in range(w):
                self._set_pixel(x + dx, y + dy, color)
    
    def _draw_circle(self, cx: int, cy: int, r: int, color: Tuple[int, int, int, int]):
        """Draw a filled circle."""
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2:
                    self._set_pixel(x, y, color)
    
    def _draw_text(self, x: int, y: int, text: str, color: Tuple[int, int, int, int]):
        """Draw text using a simple bitmap font (5x7 pixel characters)."""
        # Minimal 5x7 font for ASCII printable characters
        FONT = {
            ' ': [0x00, 0x00, 0x00, 0x00, 0x00],
            '!': [0x04, 0x04, 0x04, 0x04, 0x04],
            '#': [0x14, 0x3E, 0x14, 0x3E, 0x14],
            '$': [0x08, 0x3C, 0x0A, 0x1E, 0x08],
            '%': [0x06, 0x08, 0x10, 0x20, 0x30],
            '&': [0x14, 0x08, 0x14, 0x08, 0x14],
            '(': [0x02, 0x04, 0x04, 0x04, 0x02],
            ')': [0x08, 0x04, 0x04, 0x04, 0x08],
            '*': [0x00, 0x14, 0x08, 0x14, 0x00],
            '+': [0x00, 0x08, 0x1C, 0x08, 0x00],
            ',': [0x00, 0x00, 0x00, 0x02, 0x04],
            '-': [0x00, 0x00, 0x1C, 0x00, 0x00],
            '.': [0x00, 0x00, 0x00, 0x00, 0x04],
            '/': [0x02, 0x02, 0x08, 0x20, 0x20],
            '0': [0x1C, 0x22, 0x22, 0x22, 0x1C],
            '1': [0x08, 0x0C, 0x08, 0x08, 0x1C],
            '2': [0x1C, 0x22, 0x0C, 0x10, 0x3E],
            '3': [0x3E, 0x04, 0x0C, 0x22, 0x1C],
            '4': [0x04, 0x0C, 0x14, 0x3E, 0x04],
            '5': [0x3E, 0x20, 0x3C, 0x02, 0x3C],
            '6': [0x1C, 0x20, 0x3C, 0x22, 0x1C],
            '7': [0x3E, 0x02, 0x04, 0x08, 0x10],
            '8': [0x1C, 0x22, 0x1C, 0x22, 0x1C],
            '9': [0x1C, 0x22, 0x1E, 0x02, 0x1C],
            ':': [0x00, 0x04, 0x00, 0x04, 0x00],
            '=': [0x00, 0x1C, 0x00, 0x1C, 0x00],
            '>': [0x10, 0x08, 0x04, 0x08, 0x10],
            '@': [0x1C, 0x22, 0x2A, 0x2A, 0x1C],
            'A': [0x08, 0x14, 0x22, 0x3E, 0x22],
            'B': [0x3C, 0x22, 0x3C, 0x22, 0x3C],
            'C': [0x1C, 0x22, 0x20, 0x22, 0x1C],
            'D': [0x3C, 0x22, 0x22, 0x22, 0x3C],
            'E': [0x3E, 0x20, 0x3C, 0x20, 0x3E],
            'F': [0x3E, 0x20, 0x3C, 0x20, 0x20],
            'G': [0x1C, 0x22, 0x2A, 0x22, 0x1E],
            'H': [0x22, 0x22, 0x3E, 0x22, 0x22],
            'I': [0x1C, 0x08, 0x08, 0x08, 0x1C],
            'J': [0x02, 0x02, 0x02, 0x22, 0x1C],
            'K': [0x22, 0x24, 0x38, 0x24, 0x22],
            'L': [0x20, 0x20, 0x20, 0x20, 0x3E],
            'M': [0x22, 0x36, 0x2A, 0x22, 0x22],
            'N': [0x22, 0x32, 0x2A, 0x26, 0x22],
            'O': [0x1C, 0x22, 0x22, 0x22, 0x1C],
            'P': [0x3C, 0x22, 0x3C, 0x20, 0x20],
            'Q': [0x1C, 0x22, 0x22, 0x26, 0x1A],
            'R': [0x3C, 0x22, 0x3C, 0x24, 0x22],
            'S': [0x1C, 0x20, 0x1C, 0x02, 0x3C],
            'T': [0x3E, 0x08, 0x08, 0x08, 0x08],
            'U': [0x22, 0x22, 0x22, 0x22, 0x1C],
            'V': [0x22, 0x22, 0x22, 0x14, 0x08],
            'W': [0x22, 0x22, 0x2A, 0x36, 0x22],
            'X': [0x22, 0x14, 0x08, 0x14, 0x22],
            'Y': [0x22, 0x14, 0x08, 0x08, 0x08],
            'Z': [0x3E, 0x04, 0x08, 0x10, 0x3E],
            '[': [0x1C, 0x10, 0x10, 0x10, 0x1C],
            '\\': [0x20, 0x20, 0x08, 0x02, 0x02],
            ']': [0x1C, 0x04, 0x04, 0x04, 0x1C],
            '_': [0x00, 0x00, 0x00, 0x00, 0x3E],
            'a': [0x00, 0x00, 0x1C, 0x02, 0x1E],
            'b': [0x20, 0x20, 0x3C, 0x22, 0x3C],
            'c': [0x00, 0x00, 0x1C, 0x20, 0x1C],
            'd': [0x02, 0x02, 0x1E, 0x22, 0x1E],
            'e': [0x00, 0x00, 0x1C, 0x3E, 0x20],
            'f': [0x0C, 0x12, 0x1C, 0x10, 0x10],
            'g': [0x00, 0x00, 0x1E, 0x22, 0x1E],
            'h': [0x20, 0x20, 0x3C, 0x22, 0x22],
            'i': [0x04, 0x00, 0x0C, 0x04, 0x0E],
            'j': [0x02, 0x00, 0x06, 0x02, 0x22],
            'k': [0x20, 0x24, 0x28, 0x30, 0x28],
            'l': [0x0C, 0x04, 0x04, 0x04, 0x0E],
            'm': [0x00, 0x00, 0x34, 0x2A, 0x22],
            'n': [0x00, 0x00, 0x2C, 0x32, 0x22],
            'o': [0x00, 0x00, 0x1C, 0x22, 0x1C],
            'p': [0x00, 0x00, 0x3C, 0x22, 0x3C],
            'q': [0x00, 0x00, 0x1E, 0x22, 0x1E],
            'r': [0x00, 0x00, 0x2C, 0x30, 0x20],
            's': [0x00, 0x00, 0x1E, 0x10, 0x3C],
            't': [0x10, 0x10, 0x3C, 0x10, 0x10],
            'u': [0x00, 0x00, 0x22, 0x22, 0x1E],
            'v': [0x00, 0x00, 0x22, 0x14, 0x08],
            'w': [0x00, 0x00, 0x22, 0x2A, 0x14],
            'x': [0x00, 0x00, 0x14, 0x08, 0x14],
            'y': [0x00, 0x00, 0x1E, 0x22, 0x1E],
            'z': [0x00, 0x00, 0x3E, 0x08, 0x3E],
            '{': [0x06, 0x04, 0x18, 0x04, 0x06],
            '|': [0x04, 0x04, 0x04, 0x04, 0x04],
            '}': [0x18, 0x08, 0x06, 0x08, 0x18],
            '~': [0x00, 0x04, 0x02, 0x04, 0x00],
            '█': [0x1F, 0x1F, 0x1F, 0x1F, 0x1F],
        }
        
        cursor_x = x
        for ch in text:
            glyph = FONT.get(ch, FONT.get(' '))
            if glyph:
                for row in range(5):
                    bits = glyph[row]
                    for col in range(8):
                        if bits & (1 << (7 - col)):
                            self._set_pixel(cursor_x + col, y + row, color)
                cursor_x += 6
            else:
                cursor_x += 6
    
    def get_pixels(self) -> bytes:
        """Get the rendered pixels as bytes."""
        return bytes(self._pixels)
    
    def toggle_menu(self):
        """Toggle the start menu."""
        self._menu_open = not self._menu_open
