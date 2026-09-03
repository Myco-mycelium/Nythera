"""Whiteboard App — Freehand drawing, sticky notes, and collaboration cursors.

Features:
- Drawing tools: pen, line, rectangle, circle, arrow, eraser
- Sticky notes with color coding and resize
- Text placement with font size
- Shape library (basic shapes, arrows, callouts)
- Collaboration cursors with user avatars
- Layer management (drawing, notes, shapes, text)
- Undo/redo history
- Export to PNG/JSON
- Grid and snap-to-grid
"""

from __future__ import annotations

import time
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class DrawTool(Enum):
    PEN = "pen"
    LINE = "line"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ARROW = "arrow"
    ERASER = "eraser"
    TEXT = "text"
    SELECT = "select"
    PAN = "pan"

    @property
    def icon(self) -> str:
        icons = {
            DrawTool.PEN: "✏️", DrawTool.LINE: "📏", DrawTool.RECTANGLE: "▭",
            DrawTool.CIRCLE: "◯", DrawTool.ARROW: "→", DrawTool.ERASER: "🧹",
            DrawTool.TEXT: "T", DrawTool.SELECT: "🔘", DrawTool.PAN: "✋",
        }
        return icons.get(self, "?")


class StickyColor(Enum):
    YELLOW = "#FEF3C7"
    PINK = "#FCE7F3"
    BLUE = "#DBEAFE"
    GREEN = "#D1FAE5"
    PURPLE = "#EDE9FE"
    ORANGE = "#FED7AA"


@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0

    def distance_to(self, other: 'Point') -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass
class DrawStroke:
    id: int = 0
    tool: DrawTool = DrawTool.PEN
    points: List[Point] = field(default_factory=list)
    color: str = "#000000"
    width: int = 2
    filled: bool = False
    opacity: float = 1.0
    layer: str = "drawing"
    author: str = ""

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        if not self.points:
            return (0, 0, 0, 0)
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def point_count(self) -> int:
        return len(self.points)

    @property
    def length(self) -> float:
        total = 0.0
        for i in range(1, len(self.points)):
            total += self.points[i - 1].distance_to(self.points[i])
        return total

    @property
    def length_str(self) -> str:
        l = self.length
        if l < 1:
            return f"{l * 100:.0f}px"
        return f"{l:.1f}u"


@dataclass
class StickyNote:
    id: int = 0
    position: Point = field(default_factory=Point)
    width: float = 200
    height: float = 150
    text: str = ""
    color: StickyColor = StickyColor.YELLOW
    author: str = ""
    created_at: float = 0.0
    font_size: int = 14
    rotation: float = 0.0
    layer: str = "notes"
    locked: bool = False

    @property
    def color_hex(self) -> str:
        return self.color.value

    @property
    def text_preview(self) -> str:
        return self.text[:30] + "..." if len(self.text) > 30 else self.text

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class TextObject:
    id: int = 0
    position: Point = field(default_factory=Point)
    text: str = ""
    font_size: int = 16
    font_family: str = "sans-serif"
    color: str = "#000000"
    bold: bool = False
    layer: str = "text"


@dataclass
class CollabCursor:
    user_id: str = ""
    name: str = ""
    position: Point = field(default_factory=Point)
    color: str = "#FF0000"
    tool: DrawTool = DrawTool.PEN
    last_seen: float = 0.0
    active: bool = True

    @property
    def stale(self) -> bool:
        return time.time() - self.last_seen > 30

    @property
    def status_icon(self) -> str:
        if self.stale:
            return "⚪"
        return "🟢"


@dataclass
class Layer:
    name: str = ""
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0

    @property
    def icon(self) -> str:
        if not self.visible:
            return "👁‍🗨"
        if self.locked:
            return "🔒"
        return "👁"


@dataclass
class WhiteboardAction:
    action_type: str = ""  # draw, add_note, add_text, erase, move
    element_id: int = 0
    timestamp: float = 0.0
    author: str = ""


class WhiteboardApp:
    def __init__(self):
        self._strokes: List[DrawStroke] = []
        self._stickies: List[StickyNote] = []
        self._texts: List[TextObject] = []
        self._cursors: List[CollabCursor] = []
        self._layers: List[Layer] = []
        self._undo_stack: List[WhiteboardAction] = []
        self._redo_stack: List[WhiteboardAction] = []
        self._current_tool: DrawTool = DrawTool.PEN
        self._current_color: str = "#000000"
        self._stroke_width: int = 2
        self._zoom: float = 100.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._grid_visible: bool = True
        self._grid_size: int = 20
        self._snap_to_grid: bool = False
        self._selected_element: int = -1
        self._view_mode: str = "canvas"  # canvas, layers, history, collaborators
        self._canvas_width: int = 4000
        self._canvas_height: int = 3000
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Layers
        self._layers = [
            Layer("Background", True, False, 1.0),
            Layer("Drawing", True, False, 1.0),
            Layer("Shapes", True, False, 1.0),
            Layer("Sticky Notes", True, False, 1.0),
            Layer("Text", True, False, 1.0),
            Layer("Guides", False, False, 0.3),
        ]

        # Sample strokes — architecture diagram
        self._strokes = [
            DrawStroke(1, DrawTool.RECTANGLE,
                       [Point(100, 100), Point(400, 100), Point(400, 250), Point(100, 250)],
                       "#4A90D9", 3, True, 0.8, "Shapes", "Buffy"),
            DrawStroke(2, DrawTool.RECTANGLE,
                       [Point(450, 100), Point(750, 100), Point(750, 250), Point(450, 250)],
                       "#2ECC71", 3, True, 0.8, "Shapes", "Nyx"),
            DrawStroke(3, DrawTool.RECTANGLE,
                       [Point(200, 300), Point(600, 300), Point(600, 450), Point(200, 450)],
                       "#E74C3C", 3, True, 0.8, "Shapes", "Buffy"),
            DrawStroke(4, DrawTool.ARROW,
                       [Point(250, 250), Point(250, 300)],
                       "#333333", 2, False, 1.0, "Drawing", "Buffy"),
            DrawStroke(5, DrawTool.ARROW,
                       [Point(600, 250), Point(550, 300)],
                       "#333333", 2, False, 1.0, "Drawing", "Buffy"),
            DrawStroke(6, DrawTool.PEN,
                       [Point(100, 500), Point(150, 480), Point(200, 500), Point(250, 490), Point(300, 510)],
                       "#9B59B6", 2, False, 1.0, "Drawing", "Nyx"),
            DrawStroke(7, DrawTool.CIRCLE,
                       [Point(800, 200), Point(900, 300)],
                       "#1ABC9C", 2, True, 0.6, "Shapes", "Grace"),
        ]

        # Sticky notes
        self._stickies = [
            StickyNote(1, Point(110, 110), 280, 130,
                       "🖥 Compositor\nHandles all GPU rendering\nvia Vulkan/EGL/GBM pipeline",
                       StickyColor.YELLOW, "Buffy", now - 86400),
            StickyNote(2, Point(460, 110), 280, 130,
                       "🔌 HAL Layer\nHardware abstraction for\nGPU, DRM, display outputs",
                       StickyColor.BLUE, "Nyx", now - 86400),
            StickyNote(3, Point(210, 310), 380, 130,
                       "🐚 Shell UI\n172 Python modules\n4193 tests passing",
                       StickyColor.GREEN, "Grace", now - 3600),
            StickyNote(4, Point(100, 550), 280, 80,
                       "⚡ Performance Target\n<1ms frame latency at 144Hz",
                       StickyColor.PINK, "Buffy", now - 1800),
            StickyNote(5, Point(810, 110), 250, 80,
                       "🐕 Sample Circle\nJust a test shape",
                       StickyColor.PURPLE, "Grace", now - 7200),
        ]

        # Text objects
        self._texts = [
            TextObject(1, Point(180, 80), "Nyrqis OS Architecture", 24, "sans-serif", "#000000", True, "Text"),
            TextObject(2, Point(500, 80), "System Components", 18, "sans-serif", "#666666", False, "Text"),
            TextObject(3, Point(300, 460), "Data Flow", 14, "sans-serif", "#999999", False, "Text"),
        ]

        # Collaboration cursors
        self._cursors = [
            CollabCursor("u1", "Buffy", Point(350, 200), "#E74C3C", DrawTool.PEN, now - 2, True),
            CollabCursor("u2", "Nyx", Point(550, 180), "#3498DB", DrawTool.SELECT, now - 5, True),
            CollabCursor("u3", "Grace", Point(200, 400), "#2ECC71", DrawTool.PEN, now - 60, True),
        ]

        # Undo history
        self._undo_stack = [
            WhiteboardAction("draw", 6, now - 1800, "Nyx"),
            WhiteboardAction("add_note", 4, now - 3600, "Buffy"),
            WhiteboardAction("draw", 5, now - 7200, "Buffy"),
            WhiteboardAction("draw", 7, now - 10800, "Grace"),
            WhiteboardAction("add_note", 3, now - 14400, "Grace"),
            WhiteboardAction("add_note", 2, now - 18000, "Nyx"),
            WhiteboardAction("draw", 4, now - 21600, "Buffy"),
        ]

    @property
    def total_elements(self) -> int:
        return len(self._strokes) + len(self._stickies) + len(self._texts)

    @property
    def online_users(self) -> int:
        return sum(1 for c in self._cursors if not c.stale)

    def set_tool(self, tool: DrawTool):
        self._current_tool = tool

    def set_view(self, mode: str):
        if mode in ("canvas", "layers", "history", "collaborators"):
            self._view_mode = mode

    def zoom_in(self):
        self._zoom = min(400, self._zoom + 25)

    def zoom_out(self):
        self._zoom = max(25, self._zoom - 25)

    def toggle_grid(self):
        self._grid_visible = not self._grid_visible

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS WHITEBOARD                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        tool = self._current_tool.icon
        lines.append(f"  {tool} {self._current_tool.value}  🔍 {self._zoom:.0f}%  📐 {self._canvas_width}×{self._canvas_height}  🧱 {self.total_elements} elements  👥 {self.online_users} online  📏 Grid:{'ON' if self._grid_visible else 'OFF'}")
        lines.append("")

        if self._view_mode == "canvas":
            # Canvas mini-view
            lines.append("  ── Canvas ──")
            # Draw a simple representation
            for y in range(0, 8):
                row = "  "
                for x in range(0, 70):
                    char = "·" if self._grid_visible else " "
                    # Place markers for elements
                    for sticky in self._stickies[:3]:
                        sx = int(sticky.position.x / self._canvas_width * 70)
                        sy = int(sticky.position.y / self._canvas_height * 8)
                        if x == sx and y == sy:
                            char = "📝"
                    for text in self._texts[:2]:
                        tx = int(text.position.x / self._canvas_width * 70)
                        ty = int(text.position.y / self._canvas_height * 8)
                        if x == tx and y == ty:
                            char = "T"
                    for cursor in self._cursors[:2]:
                        cx = int(cursor.position.x / self._canvas_width * 70)
                        cy = int(cursor.position.y / self._canvas_height * 8)
                        if x == cx and y == cy:
                            char = "📍"
                    row += char
                lines.append(row)

            lines.append("")
            # Tool palette
            tools = " ".join(f"{t.icon}{t.value[0].upper()}" for t in DrawTool)
            lines.append(f"  Tools: {tools}")
            lines.append(f"  Color: {self._current_color}  Width: {self._stroke_width}px")

        elif self._view_mode == "layers":
            lines.append("  ── Layers ──")
            for layer in self._layers:
                lines.append(f"  {layer.icon} {layer.name}  Opacity: {layer.opacity:.0%}")

        elif self._view_mode == "history":
            lines.append("  ── History ──")
            for action in self._undo_stack:
                t = time.strftime("%H:%M", time.localtime(action.timestamp))
                lines.append(f"  ↩ {action.action_type} by {action.author} at {t}")

        elif self._view_mode == "collaborators":
            lines.append("  ── Collaborators ──")
            for cursor in self._cursors:
                status = cursor.status_icon
                tool = cursor.tool.icon
                lines.append(f"  {status} {cursor.name} — {tool} at ({cursor.position.x:.0f}, {cursor.position.y:.0f})")

        lines.append("")
        lines.append("  [T]ool [L]ayers [H]istory [C]ollaborators [G]rid [Z]oom [+N]ote [↑↓]Nav")
        return lines
