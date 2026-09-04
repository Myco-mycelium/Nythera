"""
Nyrqis OS - Screenshot Tool
Region selection, delay timer, and annotation overlay.
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class CaptureMode(Enum):
    FULL_SCREEN = "full_screen"
    WINDOW = "window"
    REGION = "region"
    FREEFORM = "freeform"
    SCROLLING = "scrolling"
    DELAYED = "delayed"


class AnnotationTool(Enum):
    ARROW = "arrow"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    FREEHAND = "freehand"
    TEXT = "text"
    BLUR = "blur"
    HIGHLIGHT = "highlight"
    CROP = "crop"
    NUMBER = "number"


class OutputFormat(Enum):
    PNG = "png"
    JPG = "jpg"
    WEBP = "webp"
    BMP = "bmp"
    SVG = "svg"


class SaveAction(Enum):
    SAVE_TO_FILE = "save_to_file"
    COPY_TO_CLIPBOARD = "copy_to_clipboard"
    OPEN_EDITOR = "open_editor"
    UPLOAD = "upload"
    SHARE = "share"


@dataclass
class Region:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def display(self) -> str:
        return f"{self.x},{y} {self.width}×{self.height}"


@dataclass
class Annotation:
    tool: AnnotationTool = AnnotationTool.ARROW
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    color: str = "#ff0000"
    stroke_width: int = 3
    text: str = ""
    font_size: int = 16
    opacity: float = 1.0
    filled: bool = False

    @property
    def tool_icon(self) -> str:
        icons = {
            AnnotationTool.ARROW: "➡️",
            AnnotationTool.RECTANGLE: "⬜",
            AnnotationTool.ELLIPSE: "⭕",
            AnnotationTool.LINE: "📏",
            AnnotationTool.FREEHAND: "✏️",
            AnnotationTool.TEXT: "📝",
            AnnotationTool.BLUR: "🌫️",
            AnnotationTool.HIGHLIGHT: "🖍️",
            AnnotationTool.CROP: "✂️",
            AnnotationTool.NUMBER: "#️⃣",
        }
        return icons.get(self.tool, "?")


@dataclass
class Screenshot:
    name: str = ""
    timestamp: float = 0.0
    mode: CaptureMode = CaptureMode.FULL_SCREEN
    region: Region = field(default_factory=Region)
    width: int = 1920
    height: int = 1080
    annotations: List[Annotation] = field(default_factory=list)
    output_format: OutputFormat = OutputFormat.PNG
    file_path: str = ""
    size_bytes: int = 0
    checksum: str = ""
    monitor: int = 1
    delay_seconds: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.name:
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.timestamp))
            self.name = f"screenshot_{ts}"

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def mode_icon(self) -> str:
        icons = {
            CaptureMode.FULL_SCREEN: "🖥️",
            CaptureMode.WINDOW: "🪟",
            CaptureMode.REGION: "📐",
            CaptureMode.FREEFORM: "✏️",
            CaptureMode.SCROLLING: "📜",
            CaptureMode.DELAYED: "⏱️",
        }
        return icons.get(self.mode, "?")


@dataclass
class CaptureTimer:
    seconds: int = 3
    running: bool = False
    start_time: float = 0.0

    @property
    def remaining(self) -> float:
        if not self.running:
            return float(self.seconds)
        elapsed = time.time() - self.start_time
        return max(0, self.seconds - elapsed)

    @property
    def progress_bar(self) -> str:
        if not self.running:
            return "░" * 20
        pct = (self.seconds - self.remaining) / self.seconds if self.seconds else 0
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class Hotkey:
    name: str
    keys: str
    action: str
    enabled: bool = True


class ScreenshotTool:
    def __init__(self):
        self.screenshots: List[Screenshot] = []
        self.current_screenshot: Optional[Screenshot] = None
        self.current_annotations: List[Annotation] = []
        self.active_tool: AnnotationTool = AnnotationTool.ARROW
        self.timer = CaptureTimer()
        self.default_format: OutputFormat = OutputFormat.PNG
        self.auto_save: bool = True
        self.save_path: str = "~/Pictures/Screenshots"
        self.copy_after_capture: bool = True
        self.hotkeys: List[Hotkey] = []
        self.monitors: List[Dict] = []
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.screenshots = [
            Screenshot(name="screenshot_20260904_091500", mode=CaptureMode.FULL_SCREEN,
                        width=2560, height=1440, output_format=OutputFormat.PNG,
                        size_bytes=2450000, monitor=1,
                        timestamp=now - 7200,
                        checksum="a1b2c3d4" * 4),
            Screenshot(name="region_capture_20260904_103022", mode=CaptureMode.REGION,
                        region=Region(x=100, y=200, width=800, height=600),
                        width=800, height=600, output_format=OutputFormat.PNG,
                        size_bytes=450000, monitor=1,
                        timestamp=now - 3600),
            Screenshot(name="window_firefox_20260904_112245", mode=CaptureMode.WINDOW,
                        width=1920, height=1080, output_format=OutputFormat.JPG,
                        size_bytes=890000, monitor=1,
                        timestamp=now - 1800),
            Screenshot(name="terminal_annotated_20260904_120030", mode=CaptureMode.REGION,
                        region=Region(x=0, y=0, width=1200, height=800),
                        width=1200, height=800, output_format=OutputFormat.PNG,
                        size_bytes=1200000, monitor=2, timestamp=now - 900,
                        annotations=[
                            Annotation(tool=AnnotationTool.ARROW, x1=100, y1=100,
                                       x2=300, y2=200, color="#ff0000"),
                            Annotation(tool=AnnotationTool.TEXT, x1=320, y1=200,
                                       text="Important code here", color="#ffff00"),
                        ]),
            Screenshot(name="full_desktop_20260904_131500", mode=CaptureMode.FULL_SCREEN,
                        width=3840, height=2160, output_format=OutputFormat.WEBP,
                        size_bytes=3100000, monitor=1,
                        timestamp=now - 300),
        ]

        self.hotkeys = [
            Hotkey(name="Full Screen", keys="Print", action="capture_full_screen"),
            Hotkey(name="Active Window", keys="Alt+Print", action="capture_window"),
            Hotkey(name="Region Select", keys="Shift+Print", action="capture_region"),
            Hotkey(name="Delayed 3s", keys="Ctrl+Print", action="capture_delayed_3"),
            Hotkey(name="Delayed 5s", keys="Ctrl+Shift+Print", action="capture_delayed_5"),
            Hotkey(name="Copy to Clipboard", keys="Ctrl+C", action="copy_screenshot"),
            Hotkey(name="Undo Annotation", keys="Ctrl+Z", action="undo_annotation"),
            Hotkey(name="Redo Annotation", keys="Ctrl+Shift+Z", action="redo_annotation"),
        ]

        self.monitors = [
            {"name": "Primary", "resolution": "2560×1440", "refresh_rate": 144,
             "hdr": True, "color_space": "sRGB"},
            {"name": "Secondary", "resolution": "1920×1080", "refresh_rate": 60,
             "hdr": False, "color_space": "sRGB"},
        ]

    def capture_full_screen(self, monitor: int = 1) -> Screenshot:
        mon = next((m for m in self.monitors if m.get("name") == f"Monitor {monitor}"), self.monitors[0])
        res = mon["resolution"].split("×")
        ss = Screenshot(mode=CaptureMode.FULL_SCREEN, width=int(res[0]),
                         height=int(res[1]), monitor=monitor,
                         output_format=self.default_format,
                         size_bytes=random.randint(1000000, 5000000))
        self.screenshots.append(ss)
        self.current_screenshot = ss
        return ss

    def capture_window(self) -> Screenshot:
        ss = Screenshot(mode=CaptureMode.WINDOW, width=1920, height=1080,
                         output_format=self.default_format,
                         size_bytes=random.randint(500000, 3000000))
        self.screenshots.append(ss)
        self.current_screenshot = ss
        return ss

    def capture_region(self, x: int, y: int, w: int, h: int) -> Screenshot:
        ss = Screenshot(mode=CaptureMode.REGION,
                         region=Region(x=x, y=y, width=w, height=h),
                         width=w, height=h,
                         output_format=self.default_format,
                         size_bytes=random.randint(100000, 1000000))
        self.screenshots.append(ss)
        self.current_screenshot = ss
        return ss

    def start_delay(self, seconds: int = 3) -> bool:
        self.timer.seconds = seconds
        self.timer.running = True
        self.timer.start_time = time.time()
        return True

    def add_annotation(self, tool: AnnotationTool, **kwargs) -> Annotation:
        ann = Annotation(tool=tool, **kwargs)
        self.current_annotations.append(ann)
        if self.current_screenshot:
            self.current_screenshot.annotations.append(ann)
        return ann

    def undo_annotation(self) -> bool:
        if self.current_annotations:
            removed = self.current_annotations.pop()
            if self.current_screenshot and removed in self.current_screenshot.annotations:
                self.current_screenshot.annotations.remove(removed)
            return True
        return False

    def set_tool(self, tool: AnnotationTool) -> None:
        self.active_tool = tool

    def delete_screenshot(self, name: str) -> bool:
        for i, ss in enumerate(self.screenshots):
            if ss.name == name:
                del self.screenshots[i]
                return True
        return False

    def get_recent(self, limit: int = 5) -> List[Screenshot]:
        return sorted(self.screenshots, key=lambda s: s.timestamp, reverse=True)[:limit]

    def search(self, query: str) -> List[Screenshot]:
        q = query.lower()
        return [s for s in self.screenshots if q in s.name.lower()]

    def get_stats(self) -> Dict:
        total_size = sum(s.size_bytes for s in self.screenshots)
        return {
            "total_screenshots": len(self.screenshots),
            "total_size_bytes": total_size,
            "total_annotations": sum(len(s.annotations) for s in self.screenshots),
            "formats": len(set(s.output_format for s in self.screenshots)),
            "hotkeys": len(self.hotkeys),
        }


import random
