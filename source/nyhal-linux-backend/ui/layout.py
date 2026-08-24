#!/usr/bin/env python3
"""Responsive layout constraint system for Nyrqis NUI.

Resolves component layout properties (x, y, width, height) against
parent constraints, anchor rules, min/max bounds, and breakpoint
overrides.  This is the NUI layout engine that sits between the
document model and the compositor.

Design goals (from NUI-SCHEMA §6):
  - Components declare constraints, not absolute pixels
  - The layout engine resolves concrete pixel values at render time
  - One design adapts to desktop, laptop, tablet, mobile, console
  - Constraints compose: anchor + min/max + breakpoint
  - Layout is deterministic — same inputs → same pixels

Constraint model per component::

    layout:
      x: 420              # absolute or percentage ("50%")
      y: 120
      width: 300
      height: 80
      anchor-left: true
      anchor-right: false
      anchor-top: true
      anchor-bottom: false
      min-width: 100
      max-width: 800
      min-height: 40
      max-height: 600
      percentage-width: null
      percentage-height: null
      aspect-ratio: null    # e.g. "16:9" or 1.777
      growth: "none"        # "none", "horizontal", "vertical", "both"
      alignment: "left"     # "left", "center", "right" (horizontal)
      v-alignment: "top"    # "top", "center", "bottom" (vertical)
      breakpoint-overrides:
        mobile:
          width: 100%
          x: 0
          height: auto
        tablet:
          width: 80%
          x: 10%

References:
  - NUI-SCHEMA §6: layout constraints
  - NUI-SCHEMA §7: responsive breakpoints
  - ADR-0025 §8: layout resolution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Breakpoint presets (matching NUI-SCHEMA §7)
# ---------------------------------------------------------------------------

class Breakpoint(Enum):
    CONSOLE = 3840
    DESKTOP = 1920
    LAPTOP = 1366
    TABLET = 1280
    MOBILE = 390


# Sorted descending — first match wins
_BREAKPOINT_ORDER: List[Breakpoint] = [
    Breakpoint.CONSOLE,
    Breakpoint.DESKTOP,
    Breakpoint.LAPTOP,
    Breakpoint.TABLET,
    Breakpoint.MOBILE,
]


def resolve_breakpoint(screen_width: int) -> Breakpoint:
    """Determine which breakpoint the screen width falls into."""
    for bp in _BREAKPOINT_ORDER:
        if screen_width >= bp.value:
            return bp
    return Breakpoint.MOBILE


# ---------------------------------------------------------------------------
# Anchor model
# ---------------------------------------------------------------------------

class AnchorH(Enum):
    LEFT = auto()
    CENTER = auto()
    RIGHT = auto()
    STRETCH = auto()   # anchor both left and right


class AnchorV(Enum):
    TOP = auto()
    CENTER = auto()
    BOTTOM = auto()
    STRETCH = auto()   # anchor both top and bottom


# ---------------------------------------------------------------------------
# Constraint dataclass
# ---------------------------------------------------------------------------

@dataclass
class LayoutConstraints:
    """Resolved constraints for a single component."""

    # Absolute position (pixels)
    x: int = 0
    y: int = 0
    width: int = 100
    height: int = 30

    # Anchors
    anchor_left: bool = False
    anchor_right: bool = False
    anchor_top: bool = False
    anchor_bottom: bool = False

    # Min / max bounds
    min_width: int = 0
    max_width: int = 99999
    min_height: int = 0
    max_height: int = 99999

    # Percentage sizing (None = not set)
    percentage_width: Optional[float] = None   # 0.0–1.0
    percentage_height: Optional[float] = None

    # Aspect ratio (width / height), None = free
    aspect_ratio: Optional[float] = None

    # Growth direction
    growth: str = "none"   # "none", "horizontal", "vertical", "both"

    # Alignment (within parent, when not stretching)
    alignment: str = "left"     # "left", "center", "right"
    v_alignment: str = "top"    # "top", "center", "bottom"

    # Breakpoint overrides: breakpoint_name -> layout overrides
    breakpoint_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layout resolver
# ---------------------------------------------------------------------------

class LayoutResolver:
    """Resolves NUI layout constraints into concrete pixel values.

    Parameters
    ----------
    screen_width, screen_height : int
        The current screen dimensions.
    parent_x, parent_y : int
        Parent component's resolved position (in screen coords).
    parent_width, parent_height : int
        Parent component's resolved dimensions.
    """

    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
        parent_x: int = 0,
        parent_y: int = 0,
        parent_width: int = 1920,
        parent_height: int = 1080,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.parent_x = parent_x
        self.parent_y = parent_y
        self.parent_width = parent_width
        self.parent_height = parent_height
        self.breakpoint = resolve_breakpoint(screen_width)

    def resolve(
        self,
        layout: Dict[str, Any],
        props: Optional[Dict[str, Any]] = None,
    ) -> LayoutConstraints:
        """Resolve a raw layout dict into concrete LayoutConstraints.

        Steps:
          1. Extract base values from the layout dict
          2. Apply breakpoint overrides if present
          3. Resolve percentage values
          4. Apply anchor constraints
          5. Enforce min/max bounds
          6. Resolve aspect ratio
          7. Apply alignment
        """
        c = LayoutConstraints()

        # 1. Base values
        raw_x = layout.get("x", 0)
        raw_y = layout.get("y", 0)
        raw_w = layout.get("width", 100)
        raw_h = layout.get("height", 30)

        # Auto-detect percentage strings — store the ratio, defer resolution
        if isinstance(raw_x, str) and raw_x.strip().endswith("%"):
            c.x = 0  # resolved later via alignment
        else:
            c.x = self._to_int(raw_x)

        if isinstance(raw_y, str) and raw_y.strip().endswith("%"):
            c.y = 0
        else:
            c.y = self._to_int(raw_y)

        if isinstance(raw_w, str) and raw_w.strip().endswith("%"):
            c.percentage_width = float(raw_w.strip().rstrip("%")) / 100.0
        else:
            c.width = self._to_int(raw_w)

        if isinstance(raw_h, str) and raw_h.strip().endswith("%"):
            c.percentage_height = float(raw_h.strip().rstrip("%")) / 100.0
        else:
            c.height = self._to_int(raw_h)

        # Anchors
        c.anchor_left = bool(layout.get("anchor-left", False))
        c.anchor_right = bool(layout.get("anchor-right", False))
        c.anchor_top = bool(layout.get("anchor-top", False))
        c.anchor_bottom = bool(layout.get("anchor-bottom", False))

        # Min/max
        c.min_width = self._to_int(layout.get("min-width", 0))
        c.max_width = self._to_int(layout.get("max-width", 99999))
        c.min_height = self._to_int(layout.get("min-height", 0))
        c.max_height = self._to_int(layout.get("max-height", 99999))

        # Percentage sizing
        pw = layout.get("percentage-width")
        if pw is not None:
            c.percentage_width = self._to_float(pw)
        ph = layout.get("percentage-height")
        if ph is not None:
            c.percentage_height = self._to_float(ph)

        # Aspect ratio
        ar = layout.get("aspect-ratio")
        if ar is not None:
            c.aspect_ratio = self._parse_aspect_ratio(ar)

        # Growth
        c.growth = str(layout.get("growth", "none"))

        # Alignment
        c.alignment = str(layout.get("alignment", "left"))
        c.v_alignment = str(layout.get("v-alignment", "top"))

        # Breakpoint overrides
        c.breakpoint_overrides = dict(layout.get("breakpoint-overrides", {}))

        # 2. Apply breakpoint overrides
        self._apply_breakpoint_override(c)

        # 3. Resolve percentages
        self._resolve_percentages(c)

        # 4. Apply anchor constraints
        self._apply_anchors(c)

        # 5. Enforce min/max
        self._enforce_bounds(c)

        # 6. Resolve aspect ratio
        if c.aspect_ratio is not None:
            self._resolve_aspect_ratio(c)

        # 7. Apply alignment (when not anchored/stretched)
        self._apply_alignment(c)

        return c

    # -- Internal helpers ------------------------------------------------

    def _apply_breakpoint_override(self, c: LayoutConstraints) -> None:
        """Apply breakpoint-specific overrides."""
        bp_name = self.breakpoint.name.lower()
        overrides = c.breakpoint_overrides.get(bp_name, {})
        if not overrides:
            return

        if "x" in overrides:
            c.x = self._to_int(overrides["x"])
        if "y" in overrides:
            c.y = self._to_int(overrides["y"])
        if "width" in overrides:
            val = overrides["width"]
            if isinstance(val, str) and val.endswith("%"):
                c.percentage_width = float(val[:-1]) / 100.0
            else:
                c.width = self._to_int(val)
        if "height" in overrides:
            val = overrides["height"]
            if isinstance(val, str) and val.endswith("%"):
                c.percentage_height = float(val[:-1]) / 100.0
            else:
                c.height = self._to_int(val)
        if "anchor-left" in overrides:
            c.anchor_left = bool(overrides["anchor-left"])
        if "anchor-right" in overrides:
            c.anchor_right = bool(overrides["anchor-right"])
        if "anchor-top" in overrides:
            c.anchor_top = bool(overrides["anchor-top"])
        if "anchor-bottom" in overrides:
            c.anchor_bottom = bool(overrides["anchor-bottom"])
        if "min-width" in overrides:
            c.min_width = self._to_int(overrides["min-width"])
        if "max-width" in overrides:
            c.max_width = self._to_int(overrides["max-width"])
        if "min-height" in overrides:
            c.min_height = self._to_int(overrides["min-height"])
        if "max-height" in overrides:
            c.max_height = self._to_int(overrides["max-height"])

    def _resolve_percentages(self, c: LayoutConstraints) -> None:
        """Resolve percentage-based sizing against the parent."""
        if c.percentage_width is not None:
            c.width = max(1, int(self.parent_width * c.percentage_width))
        if c.percentage_height is not None:
            c.height = max(1, int(self.parent_height * c.percentage_height))

    def _apply_anchors(self, c: LayoutConstraints) -> None:
        """Apply anchor constraints — pins edges to the parent."""
        h_stretch = c.anchor_left and c.anchor_right
        v_stretch = c.anchor_top and c.anchor_bottom

        if h_stretch:
            # Stretch horizontally: fill parent width
            c.x = self.parent_x
            c.width = self.parent_width
        elif c.anchor_left and not c.anchor_right:
            # Left-anchored: keep x relative to parent left
            c.x = self.parent_x + c.x
        elif c.anchor_right and not c.anchor_left:
            # Right-anchored: x = parent_right - width
            c.x = self.parent_x + self.parent_width - c.x - c.width

        if v_stretch:
            # Stretch vertically: fill parent height
            c.y = self.parent_y
            c.height = self.parent_height
        elif c.anchor_top and not c.anchor_bottom:
            # Top-anchored: keep y relative to parent top
            c.y = self.parent_y + c.y
        elif c.anchor_bottom and not c.anchor_top:
            # Bottom-anchored: y = parent_bottom - height
            c.y = self.parent_y + self.parent_height - c.y - c.height

        if h_stretch:
            c.growth = "horizontal" if c.growth == "none" else c.growth
        if v_stretch:
            c.growth = "vertical" if c.growth == "none" else c.growth

    def _enforce_bounds(self, c: LayoutConstraints) -> None:
        """Enforce min/max constraints."""
        c.width = max(c.min_width, min(c.max_width, c.width))
        c.height = max(c.min_height, min(c.max_height, c.height))

    def _resolve_aspect_ratio(self, c: LayoutConstraints) -> None:
        """Constrain dimensions to maintain aspect ratio.

        If width was explicitly set, derive height.
        Otherwise, derive width from height.
        """
        ar = c.aspect_ratio
        # Use the dimension that was more recently set
        if c.percentage_width is not None:
            c.height = max(1, int(c.width / ar))
        elif c.percentage_height is not None:
            c.width = max(1, int(c.height * ar))
        else:
            # Default: derive height from width
            c.height = max(1, int(c.width / ar))

    def _apply_alignment(self, c: LayoutConstraints) -> None:
        """Apply alignment within the parent when not stretching."""
        h_stretch = c.anchor_left and c.anchor_right
        v_stretch = c.anchor_top and c.anchor_bottom

        if not h_stretch:
            if c.alignment == "center":
                c.x = self.parent_x + (self.parent_width - c.width) // 2
            elif c.alignment == "right":
                c.x = self.parent_x + self.parent_width - c.width
            else:  # left
                c.x = self.parent_x + c.x

        if not v_stretch:
            if c.v_alignment == "center":
                c.y = self.parent_y + (self.parent_height - c.height) // 2
            elif c.v_alignment == "bottom":
                c.y = self.parent_y + self.parent_height - c.height
            else:  # top
                c.y = self.parent_y + c.y

    # -- Parsing helpers -------------------------------------------------

    @staticmethod
    def _to_int(val: Any) -> int:
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            val = val.strip()
            if val.endswith("%"):
                return 0  # percentages handled elsewhere
            try:
                return int(val)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _to_float(val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            val = val.strip()
            if val.endswith("%"):
                return float(val[:-1]) / 100.0
            try:
                return float(val)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _parse_aspect_ratio(val: Any) -> Optional[float]:
        """Parse an aspect ratio from various formats.

        Accepts:
          - float/int (direct ratio)
          - "16:9" (colon-separated)
          - "WxH" (multiplication sign)
        """
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
        if isinstance(val, str):
            val = val.strip()
            if ":" in val:
                parts = val.split(":", 1)
                try:
                    w, h = float(parts[0]), float(parts[1])
                    return w / h if h > 0 else None
                except (ValueError, ZeroDivisionError):
                    return None
            if "x" in val.lower():
                parts = val.lower().split("x", 1)
                try:
                    w, h = float(parts[0]), float(parts[1])
                    return w / h if h > 0 else None
                except (ValueError, ZeroDivisionError):
                    return None
            try:
                return float(val)
            except ValueError:
                return None
        return None


# ---------------------------------------------------------------------------
# Batch resolver — walks a component tree
# ---------------------------------------------------------------------------

def resolve_tree(
    screen_root: Any,
    screen_width: int = 1920,
    screen_height: int = 1080,
) -> Dict[str, LayoutConstraints]:
    """Walk a component tree and resolve layout for every component.

    Returns a dict mapping component.id → LayoutConstraints.

    Parameters
    ----------
    screen_root : NstudioComponent
        The root component of a screen.
    screen_width, screen_height : int
        Screen dimensions for breakpoint detection.
    """
    result: Dict[str, LayoutConstraints] = {}

    def walk(comp: Any, parent_x: int, parent_y: int,
             parent_w: int, parent_h: int) -> None:
        layout = getattr(comp, "layout", {})
        props = getattr(comp, "properties", {})

        resolver = LayoutResolver(
            screen_width=screen_width,
            screen_height=screen_height,
            parent_x=parent_x,
            parent_y=parent_y,
            parent_width=parent_w,
            parent_height=parent_h,
        )
        resolved = resolver.resolve(layout, props)
        result[comp.id] = resolved

        # Recurse into children
        children = getattr(comp, "children", [])
        for child in children:
            walk(child, resolved.x, resolved.y,
                 resolved.width, resolved.height)

    walk(screen_root, 0, 0, screen_width, screen_height)
    return result


__all__ = [
    "Breakpoint",
    "AnchorH",
    "AnchorV",
    "LayoutConstraints",
    "LayoutResolver",
    "resolve_breakpoint",
    "resolve_tree",
]
