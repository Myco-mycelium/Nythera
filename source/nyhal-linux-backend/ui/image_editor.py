from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math
import hashlib


class EditTool(Enum):
    SELECT = "select"
    CROP = "crop"
    RESIZE = "resize"
    BRUSH = "brush"
    ERASER = "eraser"
    TEXT = "text"
    EYEDROPPER = "eyedropper"
    MOVE = "move"
    MAGIC_WAND = "magic-wand"
    GRADIENT = "gradient"


class FilterType(Enum):
    NONE = "none"
    BLUR = "blur"
    SHARPEN = "sharpen"
    BRIGHTNESS = "brightness"
    CONTRAST = "contrast"
    SATURATION = "saturation"
    HUE_ROTATE = "hue-rotate"
    SEPIA = "sepia"
    GRAYSCALE = "grayscale"
    INVERT = "invert"
    VIGNETTE = "vignette"
    NOISE = "noise"
    EMBOSS = "emboss"
    EDGE_DETECT = "edge-detect"
    PIXELATE = "pixelate"


class ExportFormat(Enum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    BMP = "bmp"
    TIFF = "tiff"
    GIF = "gif"
    SVG = "svg"
    PDF = "pdf"


class BlendMode(Enum):
    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    ADD = "add"
    SUBTRACT = "subtract"


class ResizeMethod(Enum):
    NEAREST = "nearest"
    BILINEAR = "bilinear"
    BICUBIC = "bicubic"
    LANCZOS = "lanczos"


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def display(self) -> str:
        return f"{self.width}×{self.height}"


@dataclass
class EditHistory:
    tool: EditTool
    timestamp: float
    description: str
    layer_name: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class FilterPreset:
    name: str
    filter_type: FilterType
    intensity: float
    params: dict = field(default_factory=dict)


@dataclass
class Layer:
    name: str
    visible: bool = True
    opacity: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL
    locked: bool = False
    x_offset: int = 0
    y_offset: int = 0
    layer_id: str = ""

    def __post_init__(self):
        if not self.layer_id:
            self.layer_id = hashlib.md5(self.name.encode()).hexdigest()[:6]


@dataclass
class ImageProject:
    name: str
    width: int
    height: int
    layers: list = field(default_factory=list)
    modified: bool = False
    created_at: float = 0.0
    file_format: ExportFormat = ExportFormat.PNG

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def dimensions(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000


class ImageEditor:
    def __init__(self):
        self._projects: list[ImageProject] = []
        self._selected_project: int = 0
        self._current_tool: EditTool = EditTool.SELECT
        self._selected_layer: int = 0
        self._brush_size: int = 5
        self._brush_color: str = "#FFFFFF"
        self._undo_stack: list[EditHistory] = []
        self._redo_stack: list[EditHistory] = []
        self._zoom: float = 1.0
        self._fit_to_screen: bool = True
        self._grid_enabled: bool = False
        self._snap_to_grid: bool = False
        self._grid_size: int = 16
        self._crop_rect: Optional[Rect] = None
        self._resize_method: ResizeMethod = ResizeMethod.BICUBIC
        self._current_filter: FilterType = FilterType.NONE
        self._filter_intensity: float = 1.0
        self._selected_filter_preset: int = 0
        self._view: str = "canvas"
        self._export_format: ExportFormat = ExportFormat.PNG
        self._export_quality: int = 90
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        p1 = ImageProject("Nyrqis Wallpaper", 3840, 2160, created_at=now - 86400)
        p1.layers = [
            Layer("Background", opacity=1.0, blend_mode=BlendMode.NORMAL),
            Layer("Nebula", opacity=0.6, blend_mode=BlendMode.SCREEN),
            Layer("Stars", opacity=0.9, blend_mode=BlendMode.ADD),
            Layer("Text Overlay", opacity=1.0, blend_mode=BlendMode.NORMAL),
        ]
        self._projects.append(p1)

        p2 = ImageProject("Icon Set - 64x64", 64, 64, created_at=now - 7200)
        p2.layers = [
            Layer("Base", opacity=1.0),
            Layer("Highlight", opacity=0.4, blend_mode=BlendMode.SCREEN),
            Layer("Shadow", opacity=0.5, blend_mode=BlendMode.MULTIPLY),
        ]
        self._projects.append(p2)

        p3 = ImageProject("Screenshot Annotation", 2560, 1440, created_at=now - 3600)
        p3.layers = [
            Layer("Screenshot", opacity=1.0),
            Layer("Annotations", opacity=1.0),
            Layer("Crop Guides", opacity=0.5),
        ]
        self._projects.append(p3)

        self._filter_presets = [
            FilterPreset("Warm Tone", FilterType.BRIGHTNESS, 1.1, {"warm": True}),
            FilterPreset("Cool Tone", FilterType.HUE_ROTATE, 0.7, {"cool": True}),
            FilterPreset("Vintage", FilterType.SEPIA, 0.6, {"grain": 0.3}),
            FilterPreset("B&W Classic", FilterType.GRAYSCALE, 1.0, {}),
            FilterPreset("Dramatic", FilterType.CONTRAST, 1.4, {"clarity": 0.8}),
            FilterPreset("Dreamy", FilterType.BLUR, 0.3, {"glow": True}),
            FilterPreset("Sharp Pro", FilterType.SHARPEN, 1.2, {"detail": 0.9}),
            FilterPreset("Film Noir", FilterType.VIGNETTE, 0.8, {"contrast": 1.3}),
        ]

        self._undo_stack = [
            EditHistory(EditTool.BRUSH, now - 600, "Drew nebula overlay", "Nebula"),
            EditHistory(EditTool.BRUSH, now - 500, "Added star points", "Stars"),
            EditHistory(EditTool.TEXT, now - 400, "Added title text", "Text Overlay"),
            EditHistory(EditTool.CROP, now - 300, "Cropped to 16:9"),
            EditHistory(EditTool.RESIZE, now - 200, "Resized to 3840x2160"),
        ]

    @property
    def selected_project(self) -> Optional[ImageProject]:
        if 0 <= self._selected_project < len(self._projects):
            return self._projects[self._selected_project]
        return None

    @property
    def selected_layer(self) -> Optional[Layer]:
        proj = self.selected_project
        if proj and 0 <= self._selected_layer < len(proj.layers):
            return proj.layers[self._selected_layer]
        return None

    @property
    def total_projects(self) -> int:
        return len(self._projects)

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def history_count(self) -> int:
        return len(self._undo_stack)

    def select_project(self, idx: int):
        if 0 <= idx < len(self._projects):
            self._selected_project = idx
            self._selected_layer = 0

    def select_layer(self, idx: int):
        proj = self.selected_project
        if proj and 0 <= idx < len(proj.layers):
            self._selected_layer = idx

    def add_layer(self, name: str = "New Layer"):
        proj = self.selected_project
        if proj:
            proj.layers.append(Layer(name))
            self._selected_layer = len(proj.layers) - 1

    def remove_layer(self, idx: int) -> bool:
        proj = self.selected_project
        if proj and 0 < idx < len(proj.layers):
            proj.layers.pop(idx)
            if self._selected_layer >= len(proj.layers):
                self._selected_layer = max(0, len(proj.layers) - 1)
            return True
        return False

    def toggle_layer_visibility(self, idx: int) -> bool:
        proj = self.selected_project
        if proj and 0 <= idx < len(proj.layers):
            proj.layers[idx].visible = not proj.layers[idx].visible
            return proj.layers[idx].visible
        return False

    def set_tool(self, tool: EditTool):
        self._current_tool = tool

    def set_brush_size(self, size: int):
        self._brush_size = max(1, min(100, size))

    def set_brush_color(self, color: str):
        self._brush_color = color

    def set_zoom(self, zoom: float):
        self._zoom = max(0.1, min(32.0, zoom))

    def undo(self) -> bool:
        if self._undo_stack:
            action = self._undo_stack.pop()
            self._redo_stack.append(action)
            return True
        return False

    def redo(self) -> bool:
        if self._redo_stack:
            action = self._redo_stack.pop()
            self._undo_stack.append(action)
            return True
        return False

    def crop(self, x: int, y: int, w: int, h: int):
        proj = self.selected_project
        if proj:
            self._crop_rect = Rect(x, y, w, h)
            proj.width = w
            proj.height = h
            self._undo_stack.append(EditHistory(EditTool.CROP, time.time(), f"Cropped to {w}×{h}"))
            proj.modified = True

    def resize(self, width: int, height: int):
        proj = self.selected_project
        if proj:
            proj.width = width
            proj.height = height
            self._undo_stack.append(EditHistory(EditTool.RESIZE, time.time(), f"Resized to {width}×{height}"))
            proj.modified = True

    def apply_filter(self, filter_type: FilterType, intensity: float = 1.0):
        proj = self.selected_project
        if proj:
            self._undo_stack.append(EditHistory(EditTool.SELECT, time.time(), f"Applied {filter_type.value} filter"))
            proj.modified = True

    def export(self, fmt: ExportFormat, quality: int = 90) -> dict:
        proj = self.selected_project
        if not proj:
            return {}
        proj.file_format = fmt
        return {
            "format": fmt.value,
            "width": proj.width,
            "height": proj.height,
            "quality": quality,
            "layers": len(proj.layers),
        }

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                       NYRQIS IMAGE EDITOR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        proj = self.selected_project
        if proj:
            lines.append(f"  Project: {proj.name}  Dimensions: {proj.dimensions}  Zoom: {self._zoom:.0%}")
            lines.append(f"  Tool: {self._current_tool.value}  Brush: {self._brush_size}px  Color: {self._brush_color}")
            lines.append(f"  Grid: {'ON' if self._grid_enabled else 'OFF'}  Snap: {'ON' if self._snap_to_grid else 'OFF'}")
        lines.append("")
        lines.append("  ── Projects ─────────────────────────────────────────────")
        for i, p in enumerate(self._projects):
            sel = "▶" if i == self._selected_project else " "
            mod = " *" if p.modified else ""
            lines.append(f"  {sel} {p.name}{mod}  {p.dimensions} ({p.megapixels:.1f} MP)  {p.layers.__len__()} layers")
        lines.append("")
        lines.append("  ── Layers ───────────────────────────────────────────────")
        if proj:
            for i, l in enumerate(proj.layers):
                sel = "▶" if i == self._selected_layer else " "
                vis = "👁" if l.visible else "  "
                lock = "🔒" if l.locked else "  "
                lines.append(f"  {sel} {vis}{lock} {l.name}  {l.opacity:.0%}  {l.blend_mode.value}")
        lines.append("")
        lines.append("  ── History ──────────────────────────────────────────────")
        for h in self._undo_stack[-5:]:
            lines.append(f"  {h.tool.value}: {h.description}")
        lines.append("")
        lines.append("  [T]ool  [L]ayer  [Z]oom  [G]rid  [E]xport  [Ctrl+Z]undo  [Ctrl+Y]redo")
        return lines

    def render_canvas(self) -> list:
        proj = self.selected_project
        if not proj:
            return ["  No project open"]
        lines = []
        lines.append(f"  ── {proj.name} ({proj.dimensions}) ── Tool: {self._current_tool.value} ──")
        lines.append("")
        # Simple ASCII preview
        h_chars = [" ", "░", "▒", "▓", "█"]
        for row in range(min(12, proj.height // 180)):
            line = "  "
            for col in range(min(40, proj.width // 96)):
                idx = (row * 7 + col * 3) % 5
                line += h_chars[idx]
            lines.append(line)
        lines.append("")
        return lines

    def render_filters(self) -> list:
        lines = []
        lines.append("  ── Filters ──")
        lines.append("")
        for f in FilterType:
            if f == FilterType.NONE:
                continue
            lines.append(f"  • {f.value}")
        lines.append("")
        lines.append("  ── Presets ──")
        for i, p in enumerate(self._filter_presets):
            sel = "▶" if i == self._selected_filter_preset else " "
            lines.append(f"  {sel} {p.name}  ({p.filter_type.value} × {p.intensity:.1f})")
        return lines

    def render_export(self) -> list:
        lines = []
        lines.append("  ── Export Settings ──")
        lines.append("")
        for f in ExportFormat:
            sel = "▶" if f == self._export_format else " "
            lines.append(f"  {sel} {f.value.upper()}")
        lines.append("")
        lines.append(f"  Quality: {self._export_quality}%")
        proj = self.selected_project
        if proj:
            lines.append(f"  Output: {proj.name}.{self._export_format.value}")
            lines.append(f"  Dimensions: {proj.dimensions}")
            lines.append(f"  Layers: {len(proj.layers)}")
        return lines
