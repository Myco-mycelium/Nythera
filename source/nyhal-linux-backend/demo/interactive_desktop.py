#!/usr/bin/env python3
"""Interactive desktop demo — complete Nyrqis desktop environment.

Renders a full desktop with all components:
- Desktop background with gradient
- Taskbar with start button, clock, system tray
- Terminal window with live output
- File manager window
- Settings panel window
- Notification shade (top-left pull-down)
- Quick settings (top-right pull-down)
- Right-click context menu
- Desktop icons

Each state renders a 1920x1080 PNG showing different interactions.

Usage:
    python3 demo/interactive_desktop.py --output /tmp/nyrqis-desktop --states all
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Tuple

_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from ui.terminal import TerminalEmulator, TerminalConfig, AnsiColor, DEFAULT_PALETTE
from ui.notifications import NotificationShade, QuickSettings, Notification, NotificationCategory
from ui.context_menu import ContextMenu, MenuItem, MenuItemType, desktop_context_menu
from ui.settings_panel import SettingsPanel, BUILTIN_THEMES


# ---------------------------------------------------------------------------
# PNG writer
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int, rgb_data: bytes) -> None:
    """Write an RGB image as PNG."""
    import struct
    import zlib
    
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    stride = width * 3
    for y in range(height):
        raw += b"\x00"
        raw += rgb_data[y * stride : (y + 1) * stride]
    
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * max(0, min(1, t)))

def _lerp_color(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t))


# ---------------------------------------------------------------------------
# Desktop renderer
# ---------------------------------------------------------------------------

class InteractiveDesktop:
    """Renders the complete Nyrqis desktop with all components."""
    
    W, H = 1920, 1080
    TASKBAR_H = 48
    
    # Colors
    BG_TOP = (24, 24, 32)
    BG_BOTTOM = (40, 40, 56)
    TASKBAR = (32, 32, 44)
    TASKBAR_BORDER = (60, 60, 80)
    WINDOW_BG = (35, 35, 48)
    TITLE_BG = (50, 50, 68)
    ACCENT = (80, 140, 255)
    TEXT_WHITE = (220, 220, 230)
    TEXT_DIM = (120, 120, 140)
    
    def __init__(self):
        # Terminal
        self._terminal = TerminalEmulator(TerminalConfig(
            cols=80, rows=20, font_size=1, padding=4,
            bg_color=(20, 20, 28), fg_color=AnsiColor.WHITE,
        ))
        self._setup_terminal()
        
        # Notification shade
        self._notifications = NotificationShade(self.W, self.H)
        self._notifications.add_notification(Notification(
            id="n1", title="System Update", body="Nyrqis v0.26.0 available",
            app_name="System", icon_color=(80, 140, 255),
        ))
        self._notifications.add_notification(Notification(
            id="n2", title="Build Complete", body="nyrqis-backend built successfully",
            app_name="CI", icon_color=(60, 200, 120),
        ))
        
        # Quick settings
        self._quick_settings = QuickSettings(self.W, self.H)
        
        # Context menu
        self._context_menu = desktop_context_menu()
        
        # Settings panel
        self._settings = SettingsPanel(380, 600)
    
    def _setup_terminal(self):
        t = self._terminal
        t.feed("\x1b[1;32mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mnyrqisctl status\r\n")
        t.feed("  \x1b[32m●\x1b[0m Backend:   running (PID 1234)\r\n")
        t.feed("  \x1b[32m●\x1b[0m Session:   active\r\n")
        t.feed("  \x1b[32m●\x1b[0m Output:    1920x1080@60Hz\r\n")
        t.feed("  \x1b[32m●\x1b[0m GPU:       Intel HD Graphics\r\n")
        t.feed("\r\n")
        t.feed("\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0mnyrqis-init --diagnose\r\n")
        t.feed("  \x1b[32m✓\x1b[0m DRM device    \x1b[32m✓\x1b[0m GBM available\r\n")
        t.feed("  \x1b[32m✓\x1b[0m EGL available \x1b[32m✓\x1b[0m Vulkan ready\r\n")
        t.feed("  \x1b[32m✓\x1b[0m Wayland       \x1b[32m✓\x1b[0m Shell loaded\r\n")
        t.feed("  \x1b[1;32mAll checks passed!\x1b[0m\r\n")
        t.feed("\r\n")
        t.feed("\x1b[1;37mnyrqis\x1b[0;36m@\x1b[0;37mdesktop\x1b[0;37m:\x1b[0;34m~\x1b[0;37m$ \x1b[0m█")
    
    def render_state(self, state: str) -> bytes:
        """Render a specific desktop state."""
        # Base desktop
        pixels = self._render_base_desktop()
        w, h = self.W, self.H
        
        # Window positions
        term_x, term_y = 120, 60
        term_w, term_h = 700, 520
        files_x, files_y = 500, 180
        files_w, files_h = 500, 440
        settings_x = w - 420
        settings_y = 60
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = self._get_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:60]:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        def draw_window(x: int, y: int, ww: int, wh: int, title: str, focused: bool = False) -> None:
            fill_rect(x + 4, y + 4, ww, wh, (10, 10, 15))
            fill_rect(x, y, ww, wh, self.ACCENT if focused else (60, 60, 80))
            fill_rect(x + 1, y + 1, ww - 2, wh - 2, self.WINDOW_BG)
            fill_rect(x + 1, y + 1, ww - 2, 32, self.TITLE_BG)
            draw_text(x + 12, y + 10, title, self.TEXT_WHITE)
            fill_rect(x + ww - 28, y + 10, 14, 14, (220, 60, 60))
            fill_rect(x + ww - 48, y + 10, 14, 14, (60, 180, 120))
            fill_rect(x + ww - 68, y + 10, 14, 14, (60, 140, 220))
        
        def composite_terminal(tx: int, ty: int, tw: int, th: int) -> None:
            term_pixels, twr, thr = self._terminal.render_pixels()
            palette = list(DEFAULT_PALETTE)
            for row in range(min(self._terminal.config.rows, thr)):
                cells = self._terminal.screen[row]
                for col in range(min(twr, tw)):
                    if col < len(cells) and cells[col].char != " ":
                        fg = cells[col].fg
                        if cells[col].bold:
                            fg = min(fg + 8, 15)
                        fg_rgb = palette[fg] if fg < len(palette) else (200, 200, 200)
                        px = tx + col * 6
                        py = ty + row * 8
                        set_pixel(px, py, fg_rgb)
                        # Draw glyph
                        from ui.terminal import _get_char_glyph
                        g = _get_char_glyph(cells[col].char)
                        for gy in range(7):
                            bits = g[gy]
                            for gx in range(5):
                                if bits & (1 << (4 - gx)):
                                    set_pixel(px + gx, py + gy, fg_rgb)
        
        # === Draw windows based on state ===
        
        if state in ("default", "terminal", "notifications", "context_menu"):
            draw_window(term_x, term_y, term_w, term_h, "Terminal — nyrqis@desktop", True)
            composite_terminal(term_x + 8, term_y + 40, term_w - 16, term_h - 48)
        
        if state in ("default", "file_manager"):
            draw_window(files_x, files_y, files_w, files_h, "Files — /home/user", state == "file_manager")
            # File list
            dirs = [("📁 Documents", 0), ("📁 Downloads", 1), ("📁 Music", 2),
                    ("📄 readme.md", 3), ("📄 config.toml", 4)]
            for i, (name, _) in enumerate(dirs):
                fy = files_y + 44 + i * 32
                fill_rect(files_x + 8, fy, files_w - 16, 28, (45, 45, 60))
                draw_text(files_x + 16, fy + 6, name, self.TEXT_WHITE)
        
        if state in ("default", "settings"):
            draw_window(settings_x, settings_y, 380, 600, "Settings", state == "settings")
            # Settings preview
            sy = settings_y + 44
            draw_text(settings_x + 16, sy, "Volume", self.TEXT_WHITE)
            fill_rect(settings_x + 16, sy + 16, 300, 8, (60, 60, 80))
            fill_rect(settings_x + 16, sy + 16, 225, 8, self.ACCENT)
            sy += 48
            draw_text(settings_x + 16, sy, "Brightness", self.TEXT_WHITE)
            fill_rect(settings_x + 16, sy + 16, 300, 8, (60, 60, 80))
            fill_rect(settings_x + 16, sy + 16, 300, 8, (255, 200, 60))
            sy += 48
            draw_text(settings_x + 16, sy, "Theme", self.TEXT_WHITE)
            draw_text(settings_x + 100, sy, "Eclipse", self.ACCENT)
        
        # === Overlay elements based on state ===
        
        if state == "notifications":
            # Render notification shade overlay
            notif_pixels, nw, nh = self._notifications.render()
            if notif_pixels:
                for ty in range(nh):
                    for tx in range(nw):
                        idx = ty * nw + tx
                        if idx < len(notif_pixels):
                            set_pixel(tx, ty, notif_pixels[idx])
        
        if state == "quick_settings":
            # Render quick settings overlay
            qs_pixels, qw, qh = self._quick_settings.render()
            if qs_pixels:
                for ty in range(qh):
                    for tx in range(qw):
                        idx = ty * qw + tx
                        if idx < len(qs_pixels):
                            set_pixel(self.W - qw + tx, ty, qs_pixels[idx])
        
        if state == "context_menu":
            # Render context menu
            cm = desktop_context_menu()
            cm.show(600, 300)
            cm_pixels, cw, ch = cm.render()
            if cm_pixels:
                for ty in range(ch):
                    for tx in range(cw):
                        idx = ty * cw + tx
                        if idx < len(cm_pixels):
                            set_pixel(600 + tx, 300 + ty, cm_pixels[idx])
        
        # Render to RGB
        rgb_data = bytearray(w * h * 3)
        i = 0
        for r, g, b in pixels:
            rgb_data[i] = r
            rgb_data[i+1] = g
            rgb_data[i+2] = b
            i += 3
        return bytes(rgb_data)
    
    def _render_base_desktop(self) -> List[Tuple[int, int, int]]:
        """Render the base desktop (background + taskbar + icons)."""
        w, h = self.W, self.H
        pixels = [self.BG_TOP] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = self._get_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        # Background gradient
        for y in range(h):
            t = y / h
            r = int(self.BG_TOP[0] * (1 - t) + self.BG_BOTTOM[0] * t)
            g = int(self.BG_TOP[1] * (1 - t) + self.BG_BOTTOM[1] * t)
            b = int(self.BG_TOP[2] * (1 - t) + self.BG_BOTTOM[2] * t)
            for x in range(w):
                pixels[y * w + x] = (r, g, b)
        
        # Taskbar
        fill_rect(0, h - self.TASKBAR_H, w, self.TASKBAR_H, self.TASKBAR)
        fill_rect(0, h - self.TASKBAR_H, w, 1, self.TASKBAR_BORDER)
        
        # Start button
        fill_rect(8, h - 40, 32, 32, self.ACCENT)
        draw_text(14, h - 34, "N", self.TEXT_WHITE)
        
        # Separator
        fill_rect(48, h - 40, 1, 32, self.TASKBAR_BORDER)
        
        # App indicators
        for i, label in enumerate(["T", "F", "S"]):
            bx = 64 + i * 44
            fill_rect(bx, h - 36, 36, 24, (60, 60, 80))
            draw_text(bx + 14, h - 30, label, self.TEXT_DIM)
        
        # Clock
        now = time.localtime()
        clock = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        fill_rect(w - 120, h - 40, 80, 32, (50, 50, 68))
        draw_text(w - 108, h - 34, clock, self.TEXT_WHITE)
        
        # Date
        date = f"{now.tm_mday:02d}/{now.tm_mon:02d}"
        fill_rect(w - 36, h - 40, 32, 32, (50, 50, 68))
        draw_text(w - 30, h - 34, date, self.TEXT_DIM)
        
        # Desktop icons
        icons = [("Terminal", self.ACCENT), ("Files", (255, 200, 60)),
                 ("Browser", (80, 200, 140)), ("Settings", self.TEXT_DIM)]
        for i, (name, color) in enumerate(icons):
            ix, iy = 30, 30 + i * 90
            fill_rect(ix, iy, 56, 56, color)
            # Icon letter
            draw_text(ix + 20, iy + 20, name[0], self.TEXT_WHITE)
            # Label
            draw_text(ix - 4, iy + 64, name, self.TEXT_WHITE)
        
        # Drag handles (visual indicators for pull-down)
        # Left handle (notifications)
        fill_rect(0, 0, 200, 6, self.TASKBAR_BORDER)
        draw_text(60, 10, "pull down", self.TEXT_DIM)
        
        # Right handle (quick settings)
        fill_rect(w - 200, 0, 200, 6, self.TASKBAR_BORDER)
        draw_text(w - 180, 10, "pull down", self.TEXT_DIM)
        
        return pixels
    
    def _get_font(self) -> dict:
        return {
            ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
            '/': [0x02, 0x02, 0x04, 0x08, 0x08, 0x10, 0x10],
            ':': [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x00],
            '0': [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
            '1': [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
            '2': [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
            '3': [0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E],
            '4': [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
            '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
            '6': [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
            '7': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
            '8': [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
            '9': [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
            'A': [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
            'B': [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
            'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
            'D': [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
            'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
            'F': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
            'G': [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
            'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
            'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
            'J': [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
            'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
            'L': [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
            'M': [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
            'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
            'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
            'P': [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
            'Q': [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
            'R': [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
            'S': [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
            'T': [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
            'U': [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
            'V': [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
            'W': [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
            'X': [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
            'Y': [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
            'Z': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
            'a': [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
            'b': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E],
            'c': [0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E],
            'd': [0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F],
            'e': [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
            'f': [0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08],
            'g': [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
            'h': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11],
            'i': [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
            'k': [0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12],
            'l': [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
            'm': [0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11],
            'n': [0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11],
            'o': [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
            'p': [0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10],
            'r': [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
            's': [0x00, 0x00, 0x0E, 0x10, 0x0E, 0x01, 0x1E],
            't': [0x10, 0x10, 0x1C, 0x10, 0x10, 0x10, 0x0E],
            'u': [0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D],
            'v': [0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04],
            'w': [0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A],
            'x': [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
            'y': [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
            'z': [0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F],
        }
    
    def render_all_states(self, output_dir: str) -> List[str]:
        """Render all desktop states."""
        os.makedirs(output_dir, exist_ok=True)
        frames = []
        
        states = [
            ("01_default", "Default desktop with windows"),
            ("02_terminal", "Terminal focused"),
            ("03_file_manager", "File manager focused"),
            ("04_settings", "Settings panel focused"),
            ("05_notifications", "Notification shade (pull from top-left)"),
            ("06_quick_settings", "Quick settings (pull from top-right)"),
            ("07_context_menu", "Right-click context menu"),
        ]
        
        for filename, description in states:
            state = filename.split("_", 1)[1]
            print(f"  Rendering {description}...")
            
            rgb = self.render_state(state)
            path = os.path.join(output_dir, f"{filename}.png")
            _write_png(path, self.W, self.H, rgb)
            frames.append(path)
        
        return frames


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Nyrqis Interactive Desktop Demo")
    parser.add_argument("--output", "-o", default="/tmp/nyrqis-desktop")
    parser.add_argument("--states", default="all",
                       help="Comma-separated states or 'all'")
    args = parser.parse_args()
    
    print("╔══════════════════════════════════════════╗")
    print("║    Nyrqis Interactive Desktop Demo       ║")
    print("╚══════════════════════════════════════════╝")
    print()
    
    desktop = InteractiveDesktop()
    frames = desktop.render_all_states(args.output)
    
    print()
    print("════════════════════════════════════════════")
    print(f"  Rendered {len(frames)} desktop states!")
    print(f"  Output: {args.output}")
    print()
    for f in frames:
        size = os.path.getsize(f) // 1024
        print(f"  {os.path.basename(f)} ({size} KB)")
    print()
    print("  UI Features:")
    print("    - Pull down from TOP-LEFT for Notifications")
    print("    - Pull down from TOP-RIGHT for Quick Settings")
    print("    - Right-click for Context Menus")
    print("    - Window management with title bars")
    print("════════════════════════════════════════════")


if __name__ == "__main__":
    main()
