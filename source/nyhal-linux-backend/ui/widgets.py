#!/usr/bin/env python3
"""widgets — Nyrqis desktop widget system.

A widget system that demonstrates the full Nyrqis stack:

- Clock widget (analog/digital)
- CPU usage monitor
- Memory usage monitor
- Sticky notes widget
- Widget lifecycle (create, update, remove)
- Widget positioning on the desktop

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import math
import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Widget:
    """A single desktop widget."""
    id: str
    widget_type: str         # clock, cpu, memory, sticky
    x: int = 0
    y: int = 0
    width: int = 200
    height: int = 200
    visible: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    last_update: float = 0.0


class WidgetSystem:
    """Desktop widget system.

    Manages a collection of widgets and provides rendering.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._widgets: List[Widget] = []
        self._next_id = 1
        self._callbacks: List[Callable] = []

    # -- Widget CRUD --------------------------------------------------

    def add_widget(
        self,
        widget_type: str,
        x: int = 100,
        y: int = 100,
        **kwargs,
    ) -> Widget:
        """Add a new widget of the given type.

        Types: clock, cpu, memory, sticky
        """
        wid = f"widget-{self._next_id}"
        self._next_id += 1

        defaults = {
            "clock": {"width": 180, "height": 60},
            "cpu": {"width": 200, "height": 120},
            "memory": {"width": 200, "height": 120},
            "sticky": {"width": 200, "height": 200, "data": {"text": "Note"}},
        }
        d = defaults.get(widget_type, {})
        w = Widget(
            id=wid,
            widget_type=widget_type,
            x=x, y=y,
            width=kwargs.get("width", d.get("width", 200)),
            height=kwargs.get("height", d.get("height", 120)),
            data=kwargs.get("data", d.get("data", {})),
        )
        self._widgets.append(w)
        self._dispatch("added", w)
        return w

    def remove_widget(self, widget_id: str) -> bool:
        """Remove a widget by ID."""
        for i, w in enumerate(self._widgets):
            if w.id == widget_id:
                self._widgets.pop(i)
                self._dispatch("removed", w)
                return True
        return False

    def get_widget(self, widget_id: str) -> Optional[Widget]:
        """Find a widget by ID."""
        for w in self._widgets:
            if w.id == widget_id:
                return w
        return None

    @property
    def widgets(self) -> List[Widget]:
        return list(self._widgets)

    def by_type(self, widget_type: str) -> List[Widget]:
        """Get all widgets of a specific type."""
        return [w for w in self._widgets if w.widget_type == widget_type]

    # -- Widget updates -----------------------------------------------

    def update_all(self) -> None:
        """Update all widgets with fresh data."""
        now = time.time()
        for w in self._widgets:
            if not w.visible:
                continue
            if now - w.last_update < 1.0:
                continue
            w.last_update = now
            if w.widget_type == "clock":
                self._update_clock(w)
            elif w.widget_type == "cpu":
                self._update_cpu(w)
            elif w.widget_type == "memory":
                self._update_memory(w)

    def _update_clock(self, w: Widget) -> None:
        now = time.localtime()
        w.data["time"] = time.strftime("%H:%M:%S", now)
        w.data["date"] = time.strftime("%Y-%m-%d", now)
        w.data["seconds"] = now.tm_sec
        w.data["minutes"] = now.tm_min
        w.data["hours"] = now.tm_hour % 12

    def _update_cpu(self, w: Widget) -> None:
        """Read CPU usage from /proc/stat (Linux) or estimate."""
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            idle = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            if hasattr(self, "_prev_cpu"):
                d_total = total - self._prev_cpu[0]
                d_idle = idle - self._prev_cpu[1]
                usage = (d_total - d_idle) / d_total * 100 if d_total > 0 else 0
                w.data["usage"] = round(usage, 1)
            else:
                w.data["usage"] = 0.0
            self._prev_cpu = (total, idle)
        except (OSError, IndexError):
            w.data["usage"] = 0.0

    def _update_memory(self, w: Widget) -> None:
        """Read memory usage from /proc/meminfo (Linux)."""
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_avail = int(lines[2].split()[1])
            used = mem_total - mem_avail
            w.data["total_mb"] = round(mem_total / 1024)
            w.data["used_mb"] = round(used / 1024)
            w.data["usage"] = round(used / mem_total * 100, 1) if mem_total > 0 else 0
        except (OSError, IndexError):
            w.data["total_mb"] = 0
            w.data["used_mb"] = 0
            w.data["usage"] = 0.0

    # -- Sticky notes -------------------------------------------------

    def update_sticky(self, widget_id: str, text: str) -> bool:
        """Update the text of a sticky note widget."""
        w = self.get_widget(widget_id)
        if w is None or w.widget_type != "sticky":
            return False
        w.data["text"] = text
        self._dispatch("updated", w)
        return True

    # -- Rendering ----------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render all widgets to a transparent PIL Image."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        self.update_all()
        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except (OSError, IOError):
            font = font_large = font_small = ImageFont.load_default()

        for w in self._widgets:
            if not w.visible:
                continue
            renderer = getattr(self, f"_render_{w.widget_type}", None)
            if renderer:
                renderer(draw, w, font, font_large, font_small)

        return img

    def _render_clock(self, draw, w: Widget, font, font_large, font_small):
        """Render a clock widget."""
        x, y, hw, hh = w.x, w.y, w.width, w.height
        # Background
        draw.rounded_rectangle([x, y, x+hw, y+hh], radius=12,
                               fill=(40, 40, 40, 200), outline=(80, 80, 80))
        # Digital time
        time_str = w.data.get("time", "00:00:00")
        bbox = draw.textbbox((0, 0), time_str, font=font_large)
        tw = bbox[2] - bbox[0]
        draw.text((x + (hw - tw)//2, y + 10), time_str,
                  fill=(230, 230, 230), font=font_large)
        # Date
        date_str = w.data.get("date", "")
        if date_str:
            bbox = draw.textbbox((0, 0), date_str, font=font_small)
            dw = bbox[2] - bbox[0]
            draw.text((x + (hw - dw)//2, y + 42), date_str,
                      fill=(150, 150, 150), font=font_small)

    def _render_cpu(self, draw, w: Widget, font, font_large, font_small):
        """Render a CPU usage widget."""
        x, y, hw, hh = w.x, w.y, w.width, w.height
        usage = w.data.get("usage", 0)
        draw.rounded_rectangle([x, y, x+hw, y+hh], radius=12,
                               fill=(40, 40, 40, 200), outline=(80, 80, 80))
        # Label
        draw.text((x+12, y+10), "CPU", fill=(150, 150, 150), font=font)
        # Usage percentage
        pct = f"{usage:.1f}%"
        draw.text((x+12, y+28), pct, fill=(230, 230, 230), font=font_large)
        # Bar
        bar_x, bar_y = x+12, y+60
        bar_w, bar_h = hw-24, 16
        draw.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h],
                               radius=4, fill=(60, 60, 60))
        fill_w = int(bar_w * usage / 100)
        color = (100, 200, 100) if usage < 70 else (220, 180, 60) if usage < 90 else (220, 80, 80)
        if fill_w > 0:
            draw.rounded_rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h],
                                   radius=4, fill=color)

    def _render_memory(self, draw, w: Widget, font, font_large, font_small):
        """Render a memory usage widget."""
        x, y, hw, hh = w.x, w.y, w.width, w.height
        usage = w.data.get("usage", 0)
        used = w.data.get("used_mb", 0)
        total = w.data.get("total_mb", 0)
        draw.rounded_rectangle([x, y, x+hw, y+hh], radius=12,
                               fill=(40, 40, 40, 200), outline=(80, 80, 80))
        draw.text((x+12, y+10), "Memory", fill=(150, 150, 150), font=font)
        pct = f"{usage:.1f}%"
        draw.text((x+12, y+28), pct, fill=(230, 230, 230), font=font_large)
        detail = f"{used} MB / {total} MB"
        draw.text((x+12, y+58), detail, fill=(150, 150, 150), font=font_small)
        # Bar
        bar_x, bar_y = x+12, y+76
        bar_w, bar_h = hw-24, 16
        draw.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h],
                               radius=4, fill=(60, 60, 60))
        fill_w = int(bar_w * usage / 100)
        color = (100, 149, 237) if usage < 80 else (220, 180, 60) if usage < 95 else (220, 80, 80)
        if fill_w > 0:
            draw.rounded_rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h],
                                   radius=4, fill=color)

    def _render_sticky(self, draw, w: Widget, font, font_large, font_small):
        """Render a sticky note widget."""
        x, y, hw, hh = w.x, w.y, w.width, w.height
        color = w.data.get("color", (255, 230, 100))
        draw.rounded_rectangle([x, y, x+hw, y+hh], radius=8,
                               fill=color + (220,), outline=(200, 180, 80))
        text = w.data.get("text", "Note")
        # Word wrap text
        lines = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            line = ""
            for word in words:
                test = f"{line} {word}".strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] > hw - 24:
                    if line:
                        lines.append(line)
                    line = word
                else:
                    line = test
            if line:
                lines.append(line)
        for i, line in enumerate(lines[:10]):
            draw.text((x+12, y+12 + i*18), line,
                      fill=(40, 40, 40), font=font)

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, widget: Widget) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, widget)
            except Exception:
                pass


__all__ = ["WidgetSystem", "Widget"]
