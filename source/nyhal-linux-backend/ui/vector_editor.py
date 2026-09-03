"""Vector Graphics Editor — shapes, layers, SVG export for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import math
import json


class ToolType(Enum):
    SELECT = "Select"
    RECT = "Rectangle"
    ELLIPSE = "Ellipse"
    LINE = "Line"
    POLYGON = "Polygon"
    STAR = "Star"
    PATH = "Path"
    TEXT = "Text"
    PEN = "Pen"
    ERASER = "Eraser"
    HAND = "Hand"
    ZOOM = "Zoom"


class BlendMode(Enum):
    NORMAL = "Normal"
    MULTIPLY = "Multiply"
    SCREEN = "Screen"
    OVERLAY = "Overlay"
    DARKEN = "Darken"
    LIGHTEN = "Lighten"


class FillType(Enum):
    SOLID = "Solid"
    LINEAR_GRADIENT = "Linear Gradient"
    RADIAL_GRADIENT = "Radial Gradient"
    NONE = "None"


class StrokeCap(Enum):
    BUTT = "butt"
    ROUND = "round"
    SQUARE = "square"


class StrokeJoin(Enum):
    MITER = "miter"
    ROUND = "round"
    BEVEL = "bevel"


class AnchorType(Enum):
    CORNER = "Corner"
    SMOOTH = "Smooth"
    SYMMETRIC = "Symmetric"
    ASYMMETRIC = "Asymmetric"


@dataclass
class Point:
    x: float = 0.0
    y: float = 0.0

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    def distance_to(self, other: "Point") -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)


@dataclass
class Color:
    r: int = 0
    g: int = 0
    b: int = 0
    a: float = 1.0

    @property
    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def css(self) -> str:
        if self.a < 1.0:
            return f"rgba({self.r},{self.g},{self.b},{self.a:.2f})"
        return self.hex

    @property
    def color_bar(self) -> str:
        return f"[██████] {self.hex}"


@dataclass
class GradientStop:
    offset: float = 0.0  # 0.0 to 1.0
    color: Color = field(default_factory=Color)


@dataclass
class Fill:
    fill_type: FillType = FillType.SOLID
    color: Color = field(default_factory=Color)
    gradient_stops: List[GradientStop] = field(default_factory=list)
    opacity: float = 1.0

    @property
    def css(self) -> str:
        if self.fill_type == FillType.NONE:
            return "none"
        if self.fill_type == FillType.SOLID:
            return self.color.css
        return self.color.css  # simplified


@dataclass
class Stroke:
    color: Color = field(default_factory=lambda: Color(0, 0, 0))
    width: float = 1.0
    cap: StrokeCap = StrokeCap.BUTT
    join: StrokeJoin = StrokeJoin.MITER
    dash_array: List[float] = field(default_factory=list)
    opacity: float = 1.0

    @property
    def css(self) -> str:
        return f"{self.color.css} {self.width}px"


@dataclass
class Transform2D:
    translate_x: float = 0.0
    translate_y: float = 0.0
    rotate: float = 0.0  # degrees
    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_x: float = 0.0
    skew_y: float = 0.0

    @property
    def matrix_str(self) -> str:
        return f"translate({self.translate_x:.1f},{self.translate_y:.1f}) rotate({self.rotate:.1f}) scale({self.scale_x:.2f},{self.scale_y:.2f})"


@dataclass
class VectorShape:
    id: int
    name: str
    shape_type: str = "rect"  # rect, ellipse, line, polygon, star, path, text
    x: float = 0.0
    y: float = 0.0
    width: float = 100.0
    height: float = 100.0
    fill: Fill = field(default_factory=Fill)
    stroke: Stroke = field(default_factory=Stroke)
    transform: Transform2D = field(default_factory=Transform2D)
    opacity: float = 1.0
    visible: bool = True
    locked: bool = False
    blend_mode: BlendMode = BlendMode.NORMAL
    points: List[Point] = field(default_factory=list)
    text_content: str = ""
    font_size: int = 14
    corner_radius: float = 0.0
    rotation: float = 0.0
    layer_id: int = 0

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def type_icon(self) -> str:
        icons = {
            "rect": "▭", "ellipse": "⬭", "line": "╱", "polygon": "⬠",
            "star": "★", "path": "〰", "text": "T",
        }
        return icons.get(self.shape_type, "?")


@dataclass
class Layer:
    id: int
    name: str
    shapes: List[VectorShape] = field(default_factory=list)
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL

    @property
    def shape_count(self) -> int:
        return len(self.shapes)

    @property
    def visible_icon(self) -> str:
        return "👁" if self.visible else "👁‍🗨"


@dataclass
class Document:
    name: str = "Untitled"
    width: int = 1920
    height: int = 1080
    layers: List[Layer] = field(default_factory=list)
    background_color: Color = field(default_factory=lambda: Color(255, 255, 255))
    grid_enabled: bool = False
    grid_size: float = 10.0
    snap_to_grid: bool = False
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    @property
    def total_shapes(self) -> int:
        return sum(len(l.shapes) for l in self.layers)

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class ExportSettings:
    format: str = "svg"  # svg, png, pdf, eps
    scale: float = 1.0
    include_hidden: bool = False
    include_guides: bool = False
    color_mode: str = "sRGB"


class VectorEditor:
    def __init__(self):
        self._doc: Optional[Document] = None
        self._selected_tool: ToolType = ToolType.SELECT
        self._selected_shapes: List[int] = []
        self._selected_layer: int = 0
        self._active_layer: int = 0
        self._clipboard: List[VectorShape] = []
        self._undo_stack: List[str] = []
        self._redo_stack: List[str] = []
        self._shape_counter: int = 0
        self._history: List[str] = []
        self._view_mode: str = "canvas"
        self._show_grid: bool = False
        self._show_rulers: bool = True
        self._show_guides: bool = False
        self._snap_to_grid: bool = False
        self._zoom: float = 1.0
        self._create_samples()

    def _create_samples(self):
        self._doc = Document("Nyrqis Logo Design", 1920, 1080)

        # Background layer
        bg_layer = Layer(0, "Background")
        bg_shape = VectorShape(0, "Background Rect", "rect", 0, 0, 1920, 1080,
                               fill=Fill(FillType.SOLID, Color(30, 30, 30)))
        bg_layer.shapes.append(bg_shape)
        self._doc.layers.append(bg_layer)

        # Logo layer
        logo_layer = Layer(1, "Logo")
        shapes = [
            VectorShape(1, "Outer Circle", "ellipse", 760, 240, 400, 400,
                        fill=Fill(FillType.SOLID, Color(100, 149, 237)),
                        stroke=Stroke(Color(255, 255, 255), 3)),
            VectorShape(2, "Inner Circle", "ellipse", 810, 290, 300, 300,
                        fill=Fill(FillType.SOLID, Color(65, 105, 225)),
                        stroke=Stroke(Color(200, 200, 200), 2)),
            VectorShape(3, "Mycelium Star", "star", 885, 365, 150, 150,
                        fill=Fill(FillType.SOLID, Color(255, 255, 255)),
                        rotation=15),
            VectorShape(4, "Title Text", "text", 720, 680, 480, 60,
                        text_content="NYRQIS", font_size=48,
                        fill=Fill(FillType.SOLID, Color(230, 230, 230))),
            VectorShape(5, "Subtitle Text", "text", 760, 740, 400, 30,
                        text_content="Linux-Based Operating System", font_size=18,
                        fill=Fill(FillType.SOLID, Color(150, 150, 150))),
        ]
        logo_layer.shapes.extend(shapes)
        self._doc.layers.append(logo_layer)

        # UI Elements layer
        ui_layer = Layer(2, "UI Elements")
        ui_shapes = [
            VectorShape(6, "Panel", "rect", 100, 820, 300, 200, corner_radius=12,
                        fill=Fill(FillType.SOLID, Color(45, 45, 45)),
                        stroke=Stroke(Color(80, 80, 80), 1)),
            VectorShape(7, "Button", "rect", 120, 850, 120, 40, corner_radius=6,
                        fill=Fill(FillType.SOLID, Color(100, 149, 237))),
            VectorShape(8, "Button Text", "text", 140, 860, 80, 20,
                        text_content="Click Me", font_size=14,
                        fill=Fill(FillType.SOLID, Color(255, 255, 255))),
            VectorShape(9, "Input Field", "rect", 120, 910, 260, 36, corner_radius=4,
                        fill=Fill(FillType.SOLID, Color(35, 35, 35)),
                        stroke=Stroke(Color(80, 80, 80), 1)),
            VectorShape(10, "Progress Bar", "rect", 120, 960, 260, 8, corner_radius=4,
                        fill=Fill(FillType.SOLID, Color(45, 45, 45))),
            VectorShape(11, "Progress Fill", "rect", 120, 960, 156, 8, corner_radius=4,
                        fill=Fill(FillType.SOLID, Color(100, 149, 237))),
            VectorShape(12, "Divider", "line", 100, 1005, 300, 0,
                        stroke=Stroke(Color(60, 60, 60), 1)),
        ]
        ui_layer.shapes.extend(ui_shapes)
        self._doc.layers.append(ui_layer)

        # Guides layer
        guides_layer = Layer(3, "Guides")
        guides = [
            VectorShape(13, "Center H", "line", 0, 540, 1920, 0,
                        stroke=Stroke(Color(255, 0, 0), 1)),
            VectorShape(14, "Center V", "line", 960, 0, 0, 1080,
                        stroke=Stroke(Color(255, 0, 0), 1)),
        ]
        guides_layer.shapes.extend(guides)
        guides_layer.visible = False
        self._doc.layers.append(guides_layer)

        self._shape_counter = 15
        self._history.append("Created document")

    @property
    def document(self) -> Optional[Document]:
        return self._doc

    @property
    def selected_tool(self) -> ToolType:
        return self._selected_tool

    @property
    def total_shapes(self) -> int:
        return self._doc.total_shapes if self._doc else 0

    def select_tool(self, tool: ToolType):
        self._selected_tool = tool

    def select_shape(self, shape_id: int):
        self._selected_shapes = [shape_id]

    def add_shape(self, shape_type: str, x: float, y: float, w: float, h: float) -> int:
        if not self._doc:
            return -1
        self._shape_counter += 1
        shape = VectorShape(self._shape_counter, f"Shape {self._shape_counter}",
                            shape_type, x, y, w, h)
        if self._doc.layers:
            layer_idx = min(self._active_layer, len(self._doc.layers) - 1)
            shape.layer_id = self._doc.layers[layer_idx].id
            self._doc.layers[layer_idx].shapes.append(shape)
        self._history.append(f"Added {shape_type}")
        return self._shape_counter

    def delete_selected(self):
        if not self._doc or not self._selected_shapes:
            return
        for layer in self._doc.layers:
            layer.shapes = [s for s in layer.shapes if s.id not in self._selected_shapes]
        self._selected_shapes.clear()
        self._history.append("Deleted shapes")

    def duplicate_selected(self):
        if not self._doc or not self._selected_shapes:
            return
        for layer in self._doc.layers:
            for shape in layer.shapes:
                if shape.id in self._selected_shapes:
                    import copy
                    new_shape = copy.deepcopy(shape)
                    self._shape_counter += 1
                    new_shape.id = self._shape_counter
                    new_shape.name = f"{shape.name} Copy"
                    new_shape.x += 20
                    new_shape.y += 20
                    layer.shapes.append(new_shape)
        self._history.append("Duplicated shapes")

    def add_layer(self, name: str = "New Layer"):
        if self._doc:
            layer_id = len(self._doc.layers)
            self._doc.layers.append(Layer(layer_id, name))
            self._history.append(f"Added layer: {name}")

    def delete_layer(self, idx: int = -1):
        i = idx if idx >= 0 else self._active_layer
        if self._doc and 0 < i < len(self._doc.layers):
            name = self._doc.layers[i].name
            self._doc.layers.pop(i)
            self._active_layer = min(self._active_layer, len(self._doc.layers) - 1)
            self._history.append(f"Deleted layer: {name}")

    def move_shape_to_layer(self, shape_id: int, layer_idx: int):
        if not self._doc or layer_idx >= len(self._doc.layers):
            return
        for layer in self._doc.layers:
            for i, shape in enumerate(layer.shapes):
                if shape.id == shape_id:
                    moved = layer.shapes.pop(i)
                    moved.layer_id = self._doc.layers[layer_idx].id
                    self._doc.layers[layer_idx].shapes.append(moved)
                    return

    def export_svg(self) -> str:
        if not self._doc:
            return ""
        lines = [
            f'<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self._doc.width}" height="{self._doc.height}" viewBox="0 0 {self._doc.width} {self._doc.height}">',
            f'  <rect width="100%" height="100%" fill="{self._doc.background_color.hex}"/>',
        ]

        for layer in self._doc.layers:
            if not layer.visible:
                continue
            lines.append(f'  <g id="{layer.name}" opacity="{layer.opacity}">')
            for shape in layer.shapes:
                if not shape.visible:
                    continue
                fill_css = shape.fill.css
                stroke_css = shape.stroke.css
                opacity = f' opacity="{shape.opacity}"' if shape.opacity < 1 else ""
                transform = ""
                if shape.rotation != 0:
                    cx, cy = shape.center.x, shape.center.y
                    transform = f' transform="rotate({shape.rotation},{cx},{cy})"'

                if shape.shape_type == "rect":
                    rx = f' rx="{shape.corner_radius}"' if shape.corner_radius > 0 else ""
                    lines.append(f'    <rect x="{shape.x}" y="{shape.y}" width="{shape.width}" height="{shape.height}"{rx} fill="{fill_css}" stroke="{stroke_css}" stroke-width="{shape.stroke.width}"{opacity}{transform}/>')
                elif shape.shape_type == "ellipse":
                    cx, cy = shape.center.x, shape.center.y
                    lines.append(f'    <ellipse cx="{cx}" cy="{cy}" rx="{shape.width/2}" ry="{shape.height/2}" fill="{fill_css}" stroke="{stroke_css}" stroke-width="{shape.stroke.width}"{opacity}{transform}/>')
                elif shape.shape_type == "line":
                    x2 = shape.x + shape.width
                    y2 = shape.y + shape.height
                    lines.append(f'    <line x1="{shape.x}" y1="{shape.y}" x2="{x2}" y2="{y2}" stroke="{stroke_css}" stroke-width="{shape.stroke.width}"{opacity}{transform}/>')
                elif shape.shape_type == "text":
                    lines.append(f'    <text x="{shape.x}" y="{shape.y + shape.font_size}" font-size="{shape.font_size}" fill="{fill_css}"{opacity}{transform}>{shape.text_content}</text>')
                elif shape.shape_type == "star":
                    cx, cy = shape.center.x, shape.center.y
                    r1, r2 = shape.width / 2, shape.height / 4
                    points_str = ""
                    for i in range(10):
                        angle = math.radians(i * 36 - 90)
                        r = r1 if i % 2 == 0 else r2
                        px = cx + r * math.cos(angle)
                        py = cy + r * math.sin(angle)
                        points_str += f"{px:.1f},{py:.1f} "
                    lines.append(f'    <polygon points="{points_str.strip()}" fill="{fill_css}" stroke="{stroke_css}" stroke-width="{shape.stroke.width}"{opacity}{transform}/>')
            lines.append("  </g>")

        lines.append("</svg>")
        return "\n".join(lines)

    def export_json(self) -> str:
        if not self._doc:
            return "{}"
        data = {
            "name": self._doc.name,
            "width": self._doc.width,
            "height": self._doc.height,
            "layers": []
        }
        for layer in self._doc.layers:
            ld = {"name": layer.name, "visible": layer.visible, "shapes": []}
            for shape in layer.shapes:
                ld["shapes"].append({
                    "id": shape.id,
                    "name": shape.name,
                    "type": shape.shape_type,
                    "x": shape.x, "y": shape.y,
                    "width": shape.width, "height": shape.height,
                    "fill": shape.fill.color.hex,
                    "rotation": shape.rotation,
                    "text": shape.text_content,
                })
            data["layers"].append(ld)
        return json.dumps(data, indent=2)

    def handle_input(self, key: str):
        key = key.lower()
        if key == "v":
            self.select_tool(ToolType.SELECT)
        elif key == "r":
            self.select_tool(ToolType.RECT)
        elif key == "e":
            self.select_tool(ToolType.ELLIPSE)
        elif key == "l":
            self.select_tool(ToolType.LINE)
        elif key == "p":
            self.select_tool(ToolType.PEN)
        elif key == "t":
            self.select_tool(ToolType.TEXT)
        elif key == "g":
            self._show_grid = not self._show_grid
        elif key == "d":
            self.delete_selected()
        elif key == "x":
            self.duplicate_selected()
        elif key == "+":
            self.add_layer()
        elif key == "e":
            svg = self.export_svg()
            self._history.append(f"Exported SVG ({len(svg)} bytes)")

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS VECTOR GRAPHICS EDITOR                            ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        if not self._doc:
            lines.append("  No document open")
            return lines

        doc = self._doc
        lines.append(f"  📄 {doc.name}  {doc.resolution_str}  Zoom: {self._zoom:.0%}  Shapes: {doc.total_shapes}  Layers: {doc.layer_count}")
        lines.append(f"  Tool: {self._selected_tool.value}  Grid: {'ON' if self._show_grid else 'OFF'}  Snap: {'ON' if self._snap_to_grid else 'OFF'}  Rulers: {'ON' if self._show_rulers else 'OFF'}")
        lines.append("")

        # Canvas preview
        lines.append("  ┌─── CANVAS ──────────────────────────────────────────────────────┐")
        lines.append("  │  ▭ Panel          ★ Logo                                       │")
        lines.append("  │  ┌──────┐        (●)                                            │")
        lines.append("  │  │[Btn] │       NYRQIS                                          │")
        lines.append("  │  │______│       Linux-Based OS                                  │")
        lines.append("  └──────────────────────────────────────────────────────────────────┘")
        lines.append("")

        # Layers
        lines.append("  ── Layers ──")
        for i, layer in enumerate(doc.layers):
            sel = "▶" if i == self._active_layer else " "
            lock = "🔒" if layer.locked else ""
            lines.append(f"  {sel} {layer.visible_icon} {lock} {layer.name} ({layer.shape_count} shapes)  Opacity: {layer.opacity:.0%}")
        lines.append("")

        # Selected shapes
        if self._selected_shapes:
            lines.append("  ── Selected ──")
            for layer in doc.layers:
                for shape in layer.shapes:
                    if shape.id in self._selected_shapes:
                        lines.append(f"  {shape.type_icon} {shape.name}  ({shape.x:.0f},{shape.y:.0f}) {shape.width:.0f}x{shape.height:.0f}  Fill: {shape.fill.color.hex}  Rot: {shape.rotation:.0f}°")
            lines.append("")

        # Color palette
        lines.append("  ── Colors ──")
        lines.append(f"  Fill: {Color(100, 149, 237).color_bar}  Stroke: {Color(0, 0, 0).color_bar}")
        lines.append("")

        lines.append("  [V]Select [R]ect [E]llipse [L]ine [P]en [T]ext  [G]rid [D]el [X]Dup")
        lines.append("  [+]Layer [↑↓]Layer  [Ctrl+Z]Undo [Ctrl+S]Export SVG")
        return lines
