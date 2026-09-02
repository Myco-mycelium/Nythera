#!/usr/bin/env python3
"""Context menu component — Apple HIG clean aesthetics with keyboard navigation.

Features:
- Clean typography with proper hierarchy
- Separator lines between sections
- Keyboard navigation (arrow keys, Enter, Escape)
- Nested submenus
- Icons and shortcuts display
- Checkboxes and radio items
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Menu item types
# ---------------------------------------------------------------------------

class MenuItemType(Enum):
    """Menu item types."""
    ACTION = "action"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SEPARATOR = "separator"
    SUBMENU = "submenu"
    HEADER = "header"


@dataclass
class MenuItem:
    """A single context menu item."""
    label: str
    item_type: MenuItemType = MenuItemType.ACTION
    action: Optional[Callable] = None
    shortcut: str = ""
    enabled: bool = True
    checked: bool = False
    icon: str = ""  # Single character icon
    submenu: Optional[List["MenuItem"]] = None
    
    @property
    def is_selectable(self) -> bool:
        return self.item_type not in (MenuItemType.SEPARATOR, MenuItemType.HEADER)


# ---------------------------------------------------------------------------
# Context menu
# ---------------------------------------------------------------------------

class ContextMenu:
    """Right-click context menu with Apple HIG aesthetics.
    
    Parameters
    ----------
    x : int
        Menu position X.
    y : int
        Menu position Y.
    items : list, optional
        Menu items.
    """
    
    # Apple HIG dimensions
    ITEM_HEIGHT = 32
    PADDING_X = 16
    PADDING_Y = 8
    ICON_WIDTH = 20
    SHORTCUT_WIDTH = 80
    SEPARATOR_HEIGHT = 8
    CORNER_RADIUS = 8
    MIN_WIDTH = 200
    
    # Colors (Apple HIG)
    BG_COLOR = (42, 42, 50)
    HOVER_COLOR = (60, 60, 75)
    TEXT_COLOR = (240, 240, 245)
    TEXT_DIM = (140, 140, 160)
    TEXT_DISABLED = (90, 90, 105)
    SEPARATOR_COLOR = (55, 55, 68)
    ACCENT_COLOR = (80, 140, 255)
    CHECK_COLOR = (80, 200, 140)
    
    def __init__(
        self,
        x: int = 0,
        y: int = 0,
        items: Optional[List[MenuItem]] = None,
    ):
        self._x = x
        self._y = y
        self._items: List[MenuItem] = items or []
        self._selected_index: int = 0
        self._visible: bool = False
        self._submenu_open: bool = False
        self._submenu_item: Optional[MenuItem] = None
        self._submenu_x: int = 0
        self._submenu_y: int = 0
        self._on_action: Optional[Callable] = None
    
    # -- Menu management ---------------------------------------------------
    
    def show(self, x: int, y: int) -> None:
        """Show the context menu at the given position."""
        self._x = x
        self._y = y
        self._visible = True
        self._selected_index = 0
        self._scroll_to_selected()
    
    def hide(self) -> None:
        """Hide the context menu."""
        self._visible = False
        self._submenu_open = False
        self._submenu_item = None
    
    def set_items(self, items: List[MenuItem]) -> None:
        """Replace all menu items."""
        self._items = items
        self._selected_index = 0
    
    def add_item(self, item: MenuItem) -> None:
        """Add a menu item."""
        self._items.append(item)
    
    def add_separator(self) -> None:
        """Add a separator line."""
        self._items.append(MenuItem("", MenuItemType.SEPARATOR))
    
    def add_header(self, text: str) -> None:
        """Add a header item."""
        self._items.append(MenuItem(text, MenuItemType.HEADER))
    
    @property
    def is_visible(self) -> bool:
        return self._visible
    
    @property
    def items(self) -> List[MenuItem]:
        return list(self._items)
    
    @property
    def selected_index(self) -> int:
        return self._selected_index
    
    @property
    def position(self) -> Tuple[int, int]:
        return (self._x, self._y)
    
    @property
    def size(self) -> Tuple[int, int]:
        """Calculate menu dimensions."""
        w = self.MIN_WIDTH
        h = self.PADDING_Y * 2
        
        for item in self._items:
            if item.item_type == MenuItemType.SEPARATOR:
                h += self.SEPARATOR_HEIGHT
            else:
                h += self.ITEM_HEIGHT
        
        # Calculate width from content
        for item in self._items:
            content_w = self.PADDING_X * 2 + self.ICON_WIDTH
            content_w += len(item.label) * 10
            if item.shortcut:
                content_w += self.SHORTCUT_WIDTH
            if item.item_type == MenuItemType.SUBMENU:
                content_w += 20
            w = max(w, content_w)
        
        return (w, h)
    
    # -- Navigation --------------------------------------------------------
    
    def _scroll_to_selected(self) -> None:
        """Ensure selected item is visible."""
        selectable = [i for i, item in enumerate(self._items) if item.is_selectable]
        if selectable and self._selected_index not in selectable:
            idx = selectable.index(self._selected_index) if self._selected_index in selectable else 0
            self._selected_index = selectable[min(idx, len(selectable) - 1)]
    
    def move_up(self) -> None:
        """Move selection up."""
        selectable = [i for i, item in enumerate(self._items) if item.is_selectable]
        if not selectable:
            return
        idx = selectable.index(self._selected_index) if self._selected_index in selectable else 0
        idx = (idx - 1) % len(selectable)
        self._selected_index = selectable[idx]
    
    def move_down(self) -> None:
        """Move selection down."""
        selectable = [i for i, item in enumerate(self._items) if item.is_selectable]
        if not selectable:
            return
        idx = selectable.index(self._selected_index) if self._selected_index in selectable else 0
        idx = (idx + 1) % len(selectable)
        self._selected_index = selectable[idx]
    
    def activate_selected(self) -> Optional[str]:
        """Activate the selected item. Returns action name or None."""
        if 0 <= self._selected_index < len(self._items):
            item = self._items[self._selected_index]
            if not item.is_selectable:
                return None
            
            if item.item_type == MenuItemType.CHECKBOX:
                item.checked = not item.checked
                if item.action:
                    item.action()
                return f"check:{item.label}"
            
            elif item.item_type == MenuItemType.RADIO:
                item.checked = True
                if item.action:
                    item.action()
                return f"radio:{item.label}"
            
            elif item.item_type == MenuItemType.ACTION:
                if item.action:
                    item.action()
                self.hide()
                return f"action:{item.label}"
            
            elif item.item_type == MenuItemType.SUBMENU:
                self._submenu_open = not self._submenu_open
                self._submenu_item = item
                return f"submenu:{item.label}"
        
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
        elif key in ("Enter", "Return"):
            return self.activate_selected() or ""
        elif key == "Escape":
            self.hide()
            return "close"
        elif key == "Left":
            self._submenu_open = False
            return "submenu_close"
        elif key == "Right":
            if 0 <= self._selected_index < len(self._items):
                item = self._items[self._selected_index]
                if item.item_type == MenuItemType.SUBMENU:
                    self._submenu_open = True
                    self._submenu_item = item
                    return "submenu_open"
        
        return ""
    
    def handle_click(self, x: int, y: int) -> str:
        """Handle mouse click. Returns action or ""."""
        if not self._visible:
            return ""
        
        mx, my = self._x, self._y
        w, h = self.size
        
        # Check if click is outside menu
        if not (mx <= x <= mx + w and my <= y <= my + h):
            self.hide()
            return "close"
        
        # Find clicked item
        cy = my + self.PADDING_Y
        for i, item in enumerate(self._items):
            if item.item_type == MenuItemType.SEPARATOR:
                cy += self.SEPARATOR_HEIGHT
                continue
            
            if cy <= y <= cy + self.ITEM_HEIGHT:
                self._selected_index = i
                return self.activate_selected() or ""
            
            cy += self.ITEM_HEIGHT
        
        return ""
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the context menu to a pixel buffer."""
        w, h = self.size
        if w <= 0 or h <= 0:
            return [], 0, 0
        
        pixels = [self.BG_COLOR] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_menu_font()
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
        
        # Draw items
        cy = self.PADDING_Y
        for i, item in enumerate(self._items):
            if item.item_type == MenuItemType.SEPARATOR:
                fill_rect(self.PADDING_X, cy + self.SEPARATOR_HEIGHT // 2,
                         w - self.PADDING_X * 2, 1, self.SEPARATOR_COLOR)
                cy += self.SEPARATOR_HEIGHT
                continue
            
            # Hover highlight
            if i == self._selected_index:
                fill_rect(4, cy, w - 8, self.ITEM_HEIGHT, self.HOVER_COLOR)
            
            # Text color
            if not item.enabled:
                text_color = self.TEXT_DISABLED
            elif i == self._selected_index:
                text_color = self.ACCENT_COLOR
            else:
                text_color = self.TEXT_COLOR
            
            # Icon
            item_x = self.PADDING_X
            if item.icon:
                draw_text(item_x, cy + 8, item.icon, text_color)
                item_x += self.ICON_WIDTH
            
            # Label
            draw_text(item_x, cy + 8, item.label, text_color)
            
            # Shortcut
            if item.shortcut:
                draw_text(w - self.SHORTCUT_WIDTH, cy + 8, item.shortcut, self.TEXT_DIM)
            
            # Checkbox
            if item.item_type == MenuItemType.CHECKBOX:
                check_color = self.CHECK_COLOR if item.checked else self.TEXT_DIM
                draw_text(w - 20, cy + 8, "x" if item.checked else " ", check_color)
            
            # Submenu arrow
            if item.item_type == MenuItemType.SUBMENU:
                draw_text(w - 20, cy + 8, ">", self.TEXT_DIM)
            
            cy += self.ITEM_HEIGHT
        
        return pixels, w, h
    
    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes."""
        pixels, width, height = self.render()
        if not pixels:
            return b"", 0, 0
        buf = bytearray(width * height * 3)
        i = 0
        for r, g, b in pixels:
            buf[i] = r
            buf[i+1] = g
            buf[i+2] = b
            i += 3
        return bytes(buf), width, height
    
    def __repr__(self) -> str:
        return f"ContextMenu(x={self._x}, y={self._y}, items={len(self._items)}, visible={self._visible})"


# ---------------------------------------------------------------------------
# Pre-built context menus
# ---------------------------------------------------------------------------

def desktop_context_menu() -> ContextMenu:
    """Create a desktop right-click context menu."""
    return ContextMenu(items=[
        MenuItem("New Folder", MenuItemType.ACTION, icon="F"),
        MenuItem("New File", MenuItemType.ACTION, icon="+"),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Paste", MenuItemType.ACTION, shortcut="Ctrl+V", enabled=False),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Sort By", MenuItemType.SUBMENU, icon="S", submenu=[
            MenuItem("Name", MenuItemType.RADIO, checked=True),
            MenuItem("Size", MenuItemType.RADIO),
            MenuItem("Date", MenuItemType.RADIO),
            MenuItem("Type", MenuItemType.RADIO),
        ]),
        MenuItem("View", MenuItemType.SUBMENU, icon="V", submenu=[
            MenuItem("Icons", MenuItemType.RADIO, checked=True),
            MenuItem("List", MenuItemType.RADIO),
            MenuItem("Columns", MenuItemType.RADIO),
        ]),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Display Settings", MenuItemType.ACTION, icon="D"),
        MenuItem("Change Wallpaper", MenuItemType.ACTION, icon="W"),
    ])


def window_context_menu() -> ContextMenu:
    """Create a window title bar right-click context menu."""
    return ContextMenu(items=[
        MenuItem("Minimize", MenuItemType.ACTION, shortcut="Ctrl+M"),
        MenuItem("Maximize", MenuItemType.ACTION, shortcut="Ctrl+F"),
        MenuItem("Move to Workspace", MenuItemType.SUBMENU),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Always on Top", MenuItemType.CHECKBOX),
        MenuItem("Full Screen", MenuItemType.CHECKBOX),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Close", MenuItemType.ACTION, shortcut="Ctrl+W"),
    ])


def file_context_menu(is_dir: bool = False) -> ContextMenu:
    """Create a file/folder right-click context menu."""
    items = [
        MenuItem("Open", MenuItemType.ACTION, icon="O"),
        MenuItem("Open With...", MenuItemType.SUBMENU),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Cut", MenuItemType.ACTION, shortcut="Ctrl+X"),
        MenuItem("Copy", MenuItemType.ACTION, shortcut="Ctrl+C"),
        MenuItem("Paste", MenuItemType.ACTION, shortcut="Ctrl+V", enabled=False),
        MenuItem("", MenuItemType.SEPARATOR),
    ]
    
    if is_dir:
        items.append(MenuItem("New Folder Inside", MenuItemType.ACTION, icon="F"))
    else:
        items.append(MenuItem("Rename", MenuItemType.ACTION, shortcut="F2"))
    
    items.extend([
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Get Info", MenuItemType.ACTION, icon="I"),
        MenuItem("Delete", MenuItemType.ACTION, shortcut="Del"),
    ])
    
    return ContextMenu(items=items)


def taskbar_context_menu() -> ContextMenu:
    """Create a taskbar right-click context menu."""
    return ContextMenu(items=[
        MenuItem("Task Manager", MenuItemType.ACTION),
        MenuItem("Settings", MenuItemType.ACTION),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Lock Screen", MenuItemType.ACTION, shortcut="Ctrl+L"),
        MenuItem("Log Out", MenuItemType.ACTION),
        MenuItem("", MenuItemType.SEPARATOR),
        MenuItem("Shut Down", MenuItemType.ACTION),
    ])


# ---------------------------------------------------------------------------
# Shared font
# ---------------------------------------------------------------------------

def _get_menu_font() -> Dict[str, List[int]]:
    """Shared 5x7 bitmap font for menu rendering."""
    return {
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
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
        '>': [0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08],
        '+': [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
        '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        'x': [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
    }
