#!/usr/bin/env python3
"""App launcher for the Nyrqis desktop.

Features:
- Fuzzy search filtering
- Keyboard navigation (arrows, Enter, Escape)
- Favorites section
- Recent apps section
- Alphabetical grid layout
- Pixel rendering for display
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# App entry
# ---------------------------------------------------------------------------

@dataclass
class AppEntry:
    """An application entry in the launcher."""
    id: str
    name: str
    description: str = ""
    category: str = "Other"
    icon_letter: str = ""
    icon_color: Tuple[int, int, int] = (80, 140, 255)
    favorite: bool = False
    recent: bool = False
    last_used: float = 0.0
    use_count: int = 0
    executable: str = ""
    
    @property
    def display_name(self) -> str:
        return self.name[:20]
    
    @property
    def search_text(self) -> str:
        """Text used for fuzzy search."""
        return f"{self.name} {self.description} {self.category}".lower()


# ---------------------------------------------------------------------------
# Default apps
# ---------------------------------------------------------------------------

DEFAULT_APPS = [
    AppEntry("terminal", "Terminal", "Command line interface", "System", "T", (60, 200, 120), executable="/usr/bin/nyrqis-terminal"),
    AppEntry("files", "Files", "File manager", "System", "F", (255, 200, 60), executable="/usr/bin/nyrqis-files"),
    AppEntry("browser", "Browser", "Web browser", "Internet", "B", (80, 140, 255), executable="/usr/bin/nyrqis-browser"),
    AppEntry("settings", "Settings", "System preferences", "System", "S", (180, 180, 200), executable="/usr/bin/nyrqis-settings"),
    AppEntry("editor", "Editor", "Text editor", "Development", "E", (100, 200, 160), executable="/usr/bin/nyrqis-editor"),
    AppEntry("calculator", "Calculator", "Calculator app", "Utilities", "C", (200, 140, 255)),
    AppEntry("music", "Music", "Music player", "Media", "M", (255, 100, 100)),
    AppEntry("photos", "Photos", "Image viewer", "Media", "P", (255, 180, 60)),
    AppEntry("notes", "Notes", "Note taking", "Productivity", "N", (255, 220, 80)),
    AppEntry("calendar", "Calendar", "Calendar app", "Productivity", "L", (60, 180, 255)),
    AppEntry("system-monitor", "System Monitor", "Resource usage", "System", "M", (200, 100, 100)),
    AppEntry("disk-utility", "Disk Utility", "Disk management", "System", "D", (140, 140, 160)),
]


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

class Launcher:
    """App launcher with search and keyboard navigation.
    
    Parameters
    ----------
    width : int
        Launcher width in pixels.
    height : int
        Launcher height in pixels.
    """
    
    # Layout (Apple HIG: 8pt grid)
    PADDING = 24
    SEARCH_HEIGHT = 48
    SECTION_GAP = 24
    APP_ICON_SIZE = 56
    APP_GRID_COLS = 4
    APP_GRID_GAP = 16
    APP_ROW_HEIGHT = 96
    
    # Colors (Apple HIG)
    BG_COLOR = (28, 28, 35)
    SURFACE_COLOR = (40, 40, 52)
    SEARCH_BG = (50, 50, 65)
    TEXT_PRIMARY = (240, 240, 245)
    TEXT_SECONDARY = (140, 140, 160)
    ACCENT = (80, 140, 255)
    HOVER_COLOR = (55, 55, 72)
    DIVIDER = (55, 55, 68)
    FAVORITE_COLOR = (255, 180, 60)
    
    def __init__(self, width: int = 500, height: int = 700):
        self._width = width
        self._height = height
        
        self._apps: List[AppEntry] = list(DEFAULT_APPS)
        self._search_query: str = ""
        self._selected_index: int = 0
        self._visible: bool = False
        self._scroll_offset: int = 0
        self._on_launch: Optional[Callable] = None
        
        # Pre-filtered results
        self._filtered: List[AppEntry] = []
        self._update_filtered()
    
    # -- App management ----------------------------------------------------
    
    def add_app(self, app: AppEntry) -> None:
        """Add an app to the launcher."""
        self._apps.append(app)
        self._update_filtered()
    
    def remove_app(self, app_id: str) -> bool:
        for i, app in enumerate(self._apps):
            if app.id == app_id:
                self._apps.pop(i)
                self._update_filtered()
                return True
        return False
    
    def find_app(self, app_id: str) -> Optional[AppEntry]:
        for app in self._apps:
            if app.id == app_id:
                return app
        return None
    
    def toggle_favorite(self, app_id: str) -> bool:
        app = self.find_app(app_id)
        if app:
            app.favorite = not app.favorite
            self._update_filtered()
            return app.favorite
        return False
    
    def record_launch(self, app_id: str) -> None:
        """Record that an app was launched."""
        app = self.find_app(app_id)
        if app:
            app.last_used = time.time()
            app.use_count += 1
            app.recent = True
            self._update_filtered()
    
    @property
    def apps(self) -> List[AppEntry]:
        return list(self._apps)
    
    @property
    def favorites(self) -> List[AppEntry]:
        return [a for a in self._apps if a.favorite]
    
    @property
    def recent(self) -> List[AppEntry]:
        return sorted(
            [a for a in self._apps if a.recent],
            key=lambda a: a.last_used,
            reverse=True
        )[:6]
    
    # -- Search ------------------------------------------------------------
    
    def set_search(self, query: str) -> None:
        """Set the search query."""
        self._search_query = query
        self._selected_index = 0
        self._scroll_offset = 0
        self._update_filtered()
    
    def _update_filtered(self) -> None:
        """Update the filtered app list based on search query."""
        if not self._search_query:
            # Show favorites first, then all
            favs = [a for a in self._apps if a.favorite]
            others = [a for a in self._apps if not a.favorite]
            self._filtered = favs + sorted(others, key=lambda a: a.name.lower())
        else:
            query = self._search_query.lower()
            # Fuzzy match: check if all query chars appear in search text in order
            scored = []
            for app in self._apps:
                score = self._fuzzy_score(query, app.search_text)
                if score > 0:
                    scored.append((score, app))
            scored.sort(key=lambda x: (-x[0], x[1].name.lower()))
            self._filtered = [app for _, app in scored]
    
    def _fuzzy_score(self, query: str, text: str) -> int:
        """Simple fuzzy matching score."""
        if not query:
            return 1
        
        qi = 0
        score = 0
        last_match = -1
        
        for i, ch in enumerate(text):
            if qi < len(query) and ch == query[qi]:
                score += 10
                if last_match == i - 1:
                    score += 5  # Consecutive bonus
                if ch == query[qi] and i < len(text) and text[i:i+len(query)] == query:
                    score += 20  # Substring bonus
                last_match = i
                qi += 1
        
        if qi == len(query):
            return score
        return 0
    
    @property
    def search_query(self) -> str:
        return self._search_query
    
    @property
    def filtered_apps(self) -> List[AppEntry]:
        return list(self._filtered)
    
    # -- Navigation --------------------------------------------------------
    
    def show(self) -> None:
        self._visible = True
        self._selected_index = 0
        self._search_query = ""
        self._update_filtered()
    
    def hide(self) -> None:
        self._visible = False
    
    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()
    
    @property
    def is_visible(self) -> bool:
        return self._visible
    
    def move_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)
        self._scroll_to_selected()
    
    def move_down(self) -> None:
        self._selected_index = min(len(self._filtered) - 1, self._selected_index + 1)
        self._scroll_to_selected()
    
    def move_left(self) -> None:
        self._selected_index = max(0, self._selected_index - self.APP_GRID_COLS)
        self._scroll_to_selected()
    
    def move_right(self) -> None:
        self._selected_index = min(
            len(self._filtered) - 1,
            self._selected_index + self.APP_GRID_COLS
        )
        self._scroll_to_selected()
    
    def _scroll_to_selected(self) -> None:
        visible_rows = (self._height - 150) // self.APP_ROW_HEIGHT
        row = self._selected_index // self.APP_GRID_COLS
        if row < self._scroll_offset:
            self._scroll_offset = row
        elif row >= self._scroll_offset + visible_rows:
            self._scroll_offset = row - visible_rows + 1
    
    def select(self) -> Optional[AppEntry]:
        """Select the current app and return it."""
        if 0 <= self._selected_index < len(self._filtered):
            app = self._filtered[self._selected_index]
            self.record_launch(app.id)
            if self._on_launch:
                self._on_launch(app)
            return app
        return None
    
    def handle_key(self, key: str) -> str:
        """Handle keyboard input."""
        if not self._visible:
            return ""
        
        if key == "Up":
            self.move_up()
            return "navigate"
        elif key == "Down":
            self.move_down()
            return "navigate"
        elif key == "Left":
            self.move_left()
            return "navigate"
        elif key == "Right":
            self.move_right()
            return "navigate"
        elif key in ("Enter", "Return"):
            app = self.select()
            return f"launch:{app.id}" if app else ""
        elif key == "Escape":
            self.hide()
            return "close"
        elif key == "BackSpace":
            self._search_query = self._search_query[:-1]
            self._selected_index = 0
            self._update_filtered()
            return "search"
        elif len(key) == 1 and key.isprintable():
            self._search_query += key
            self._selected_index = 0
            self._update_filtered()
            return "search"
        
        return ""
    
    @property
    def on_launch(self) -> Optional[Callable]:
        return self._on_launch
    
    @on_launch.setter
    def on_launch(self, callback: Optional[Callable]) -> None:
        self._on_launch = callback
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the launcher to a pixel buffer."""
        w = self._width
        h = self._height
        
        pixels = [self.BG_COLOR] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_launcher_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:30]:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        # === Search bar ===
        fill_rect(self.PADDING, self.PADDING, w - self.PADDING * 2, self.SEARCH_HEIGHT, self.SEARCH_BG)
        draw_text(self.PADDING + 12, self.PADDING + 14, "Search...", self.TEXT_SECONDARY)
        
        if self._search_query:
            draw_text(self.PADDING + 12, self.PADDING + 14, self._search_query, self.TEXT_PRIMARY)
            # Cursor
            cursor_x = self.PADDING + 12 + len(self._search_query) * 6
            fill_rect(cursor_x, self.PADDING + 12, 2, 18, self.ACCENT)
        
        cy = self.PADDING + self.SEARCH_HEIGHT + self.SECTION_GAP
        
        # === Favorites section ===
        favs = self.favorites
        if favs and not self._search_query:
            draw_text(self.PADDING, cy, "Favorites", self.TEXT_SECONDARY)
            cy += 24
            
            for i, app in enumerate(favs[:self.APP_GRID_COLS]):
                ax = self.PADDING + i * (self.APP_ICON_SIZE + self.APP_GRID_GAP)
                
                # Selection highlight
                global_idx = i
                if global_idx == self._selected_index:
                    fill_rect(ax - 4, cy - 4, self.APP_ICON_SIZE + 8, self.APP_ICON_SIZE + 8, self.HOVER_COLOR)
                
                # Icon
                fill_rect(ax, cy, self.APP_ICON_SIZE, self.APP_ICON_SIZE, app.icon_color)
                draw_text(ax + 20, cy + 20, app.icon_letter, self.TEXT_PRIMARY)
                
                # Star indicator
                fill_rect(ax + self.APP_ICON_SIZE - 8, cy - 2, 8, 8, self.FAVORITE_COLOR)
                
                # Name
                name = app.display_name[:10]
                nw = len(name) * 6
                draw_text(ax + (self.APP_ICON_SIZE - nw) // 2, cy + self.APP_ICON_SIZE + 4, name, self.TEXT_PRIMARY)
            
            cy += self.APP_ICON_SIZE + 32
        
        # === Divider ===
        fill_rect(self.PADDING, cy, w - self.PADDING * 2, 1, self.DIVIDER)
        cy += 16
        
        # === All apps grid ===
        if not self._search_query:
            draw_text(self.PADDING, cy, "All Apps", self.TEXT_SECONDARY)
            cy += 24
        
        # Filter apps for grid (exclude favorites from main grid if showing favorites)
        grid_apps = self._filtered
        if not self._search_query:
            grid_apps = [a for a in self._filtered if not a.favorite]
        
        visible_start = self._scroll_offset * self.APP_GRID_COLS
        visible_end = visible_start + self.APP_GRID_COLS * 8  # ~8 rows visible
        
        for i, app in enumerate(grid_apps[visible_start:visible_end]):
            actual_idx = visible_start + i
            col = i % self.APP_GRID_COLS
            row = i // self.APP_GRID_COLS
            
            ax = self.PADDING + col * (self.APP_ICON_SIZE + self.APP_GRID_GAP)
            ay = cy + row * self.APP_ROW_HEIGHT
            
            if ay + self.APP_ICON_SIZE > h - 20:
                break
            
            # Selection highlight
            if actual_idx == self._selected_index:
                fill_rect(ax - 4, ay - 4, self.APP_ICON_SIZE + 8, self.APP_ICON_SIZE + 8, self.HOVER_COLOR)
            
            # Icon
            fill_rect(ax, ay, self.APP_ICON_SIZE, self.APP_ICON_SIZE, app.icon_color)
            
            # Icon letter (centered)
            draw_text(ax + 20, ay + 20, app.icon_letter, self.TEXT_PRIMARY)
            
            # App name
            name = app.display_name[:12]
            nw = len(name) * 6
            draw_text(ax + (self.APP_ICON_SIZE - nw) // 2, ay + self.APP_ICON_SIZE + 6, name, self.TEXT_PRIMARY)
            
            # Favorite star
            if app.favorite:
                fill_rect(ax + self.APP_ICON_SIZE - 10, ay - 2, 10, 10, self.FAVORITE_COLOR)
        
        # === Scroll indicator ===
        total_rows = (len(grid_apps) + self.APP_GRID_COLS - 1) // self.APP_GRID_COLS
        visible_rows = (h - cy - 20) // self.APP_ROW_HEIGHT
        if total_rows > visible_rows:
            bar_h = max(20, visible_rows * self.APP_ROW_HEIGHT * visible_rows // max(1, total_rows))
            bar_y = cy + int((self._scroll_offset / max(1, total_rows - visible_rows)) * (self.APP_ROW_HEIGHT * visible_rows - bar_h))
            fill_rect(w - 8, bar_y, 4, bar_h, self.TEXT_SECONDARY)
        
        return pixels, w, h
    
    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes."""
        pixels, width, height = self.render()
        buf = bytearray(width * height * 3)
        i = 0
        for r, g, b in pixels:
            buf[i] = r
            buf[i+1] = g
            buf[i+2] = b
            i += 3
        return bytes(buf), width, height
    
    def __repr__(self) -> str:
        return (
            f"Launcher(apps={len(self._apps)}, "
            f"query='{self._search_query}', "
            f"selected={self._selected_index})"
        )


# ---------------------------------------------------------------------------
# Shared font
# ---------------------------------------------------------------------------

def _get_launcher_font() -> Dict[str, List[int]]:
    """5x7 bitmap font."""
    return {
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        'A': [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'B': [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        'D': [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
        'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        'F': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        'G': [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L': [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        'M': [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
        'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
        'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'P': [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
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
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
        '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        '_': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F],
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    }
