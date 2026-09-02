#!/usr/bin/env python3
"""drag_drop — Nyrqis drag-and-drop system.

A full drag-and-drop framework for the Nyrqis desktop:

- Drag data between windows, from desktop, between apps
- Drop targets with visual indicators (highlight zones)
- Multi-item drag support (drag multiple selected items)
- Drag preview rendering (ghost thumbnail under cursor)
- Desktop drop zones (create shortcuts, move files)
- Clipboard integration (drag text to clipboard)
- Keyboard modifiers (Ctrl=copy, Shift=move, Alt=link)
- Snap-back animation when drop is rejected
- Cross-window transfer via serialized data

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class DropAction(Enum):
    """Supported drop actions."""
    COPY = "copy"
    MOVE = "move"
    LINK = "link"
    ASK = "ask"


class DragState(Enum):
    """Current drag operation state."""
    IDLE = "idle"
    PREPARING = "preparing"
    DRAGGING = "dragging"
    DROPPING = "dropping"
    CANCELLED = "cancelled"


class DropEffect(Enum):
    """Visual effect for drop target."""
    NONE = "none"
    COPY = "copy"       # blue highlight
    MOVE = "move"       # green highlight
    LINK = "link"       # orange highlight
    REJECT = "reject"   # red highlight


@dataclass
class DragData:
    """Data payload being dragged."""
    id: str
    mime_type: str          # e.g. "text/plain", "file/path", "application/nyrqis-window"
    content: Any            # The actual data (str, list of paths, dict, etc.)
    label: str = ""         # Display label for preview
    icon: str = ""          # Optional icon character
    source_window_id: str = ""
    source_app: str = ""
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        if not self.id:
            self.id = str(uuid.uuid4())[:8]


@dataclass
class DropZone:
    """A region that accepts drops."""
    id: str
    rect: Tuple[int, int, int, int]  # (x, y, width, height)
    accepted_types: Set[str] = field(default_factory=lambda: {"*/*"})
    action: DropAction = DropAction.COPY
    label: str = ""
    enabled: bool = True
    visible: bool = True
    effect: DropEffect = DropEffect.NONE
    window_id: str = ""

    @property
    def x(self) -> int:
        return self.rect[0]

    @property
    def y(self) -> int:
        return self.rect[1]

    @property
    def width(self) -> int:
        return self.rect[2]

    @property
    def height(self) -> int:
        return self.rect[3]

    def contains(self, px: int, py: int) -> bool:
        """Check if a point is inside this zone."""
        return (self.x <= px < self.x + self.width and
                self.y <= py < self.y + self.height)

    def accepts(self, mime_type: str) -> bool:
        """Check if this zone accepts the given MIME type."""
        if not self.enabled:
            return False
        if "*/*" in self.accepted_types:
            return True
        return mime_type in self.accepted_types


@dataclass
class DragPreview:
    """Visual preview of what's being dragged."""
    label: str
    icon: str = ""
    item_count: int = 1
    offset_x: int = 12   # offset from cursor
    offset_y: int = 12


@dataclass
class DragEvent:
    """Event emitted during drag operations."""
    type: str          # started, moved, entered_zone, left_zone, dropped, cancelled, rejected
    drag_data: Optional[DragData] = None
    drop_zone: Optional[DropZone] = None
    x: int = 0
    y: int = 0
    action: Optional[DropAction] = None


# ---------------------------------------------------------------------------
# Drag-and-drop manager
# ---------------------------------------------------------------------------

class DragDropManager:
    """Manages drag-and-drop operations across the desktop.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._state: DragState = DragState.IDLE
        self._current_drag: Optional[DragData] = None
        self._items: List[DragData] = []  # Multi-item drag
        self._drop_zones: List[DropZone] = []
        self._cursor_x: int = 0
        self._cursor_y: int = 0
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0
        self._drag_start_time: float = 0.0
        self._active_zone: Optional[DropZone] = None
        self._modifiers: Dict[str, bool] = {"ctrl": False, "shift": False, "alt": False}
        self._callbacks: List[Callable] = []
        self._snap_back: bool = False
        self._snap_origin: Tuple[int, int] = (0, 0)
        self._history: List[Dict] = []
        self._zone_counter = 0

    # -- Drop zone management -------------------------------------------

    def register_zone(
        self,
        rect: Tuple[int, int, int, int],
        accepted_types: Optional[Set[str]] = None,
        action: DropAction = DropAction.COPY,
        label: str = "",
        window_id: str = "",
        **kwargs,
    ) -> DropZone:
        """Register a drop target zone."""
        zone = DropZone(
            id=f"zone-{self._zone_counter}",
            rect=rect,
            accepted_types=accepted_types or {"*/*"},
            action=action,
            label=label,
            window_id=window_id,
        )
        for k, v in kwargs.items():
            if hasattr(zone, k):
                setattr(zone, k, v)
        self._drop_zones.append(zone)
        self._zone_counter += 1
        return zone

    def unregister_zone(self, zone_id: str) -> bool:
        """Remove a drop zone."""
        for i, z in enumerate(self._drop_zones):
            if z.id == zone_id:
                self._drop_zones.pop(i)
                return True
        return False

    def get_zone(self, zone_id: str) -> Optional[DropZone]:
        """Find a zone by ID."""
        for z in self._drop_zones:
            if z.id == zone_id:
                return z
        return None

    def clear_zones(self) -> int:
        """Remove all drop zones. Returns count removed."""
        count = len(self._drop_zones)
        self._drop_zones.clear()
        return count

    def set_zone_enabled(self, zone_id: str, enabled: bool) -> bool:
        """Enable or disable a drop zone."""
        zone = self.get_zone(zone_id)
        if zone:
            zone.enabled = enabled
            return True
        return False

    @property
    def zones(self) -> List[DropZone]:
        return list(self._drop_zones)

    def zones_for_type(self, mime_type: str) -> List[DropZone]:
        """Get all zones that accept a given MIME type."""
        return [z for z in self._drop_zones if z.accepts(mime_type) and z.visible]

    # -- Drag operations -----------------------------------------------

    def start_drag(
        self,
        data: DragData,
        x: int,
        y: int,
    ) -> bool:
        """Start a drag operation.

        Returns True if drag started successfully.
        """
        if self._state != DragState.IDLE:
            return False

        self._current_drag = data
        self._items = [data]
        self._cursor_x = x
        self._cursor_y = y
        self._drag_start_x = x
        self._drag_start_y = y
        self._drag_start_time = time.time()
        self._state = DragState.PREPARING
        self._active_zone = None
        self._snap_back = False

        self._dispatch(DragEvent(
            type="started",
            drag_data=data,
            x=x, y=y,
        ))

        # Transition to dragging after a small movement threshold
        self._state = DragState.DRAGGING
        return True

    def add_item(self, data: DragData) -> None:
        """Add an additional item to the current drag."""
        if self._state == DragState.DRAGGING:
            self._items.append(data)

    def move_drag(self, x: int, y: int) -> None:
        """Update cursor position during drag."""
        if self._state != DragState.DRAGGING:
            return

        self._cursor_x = x
        self._cursor_y = y

        # Hit test against drop zones
        hit_zone = self._hit_test(x, y)

        if hit_zone != self._active_zone:
            # Left old zone
            if self._active_zone:
                self._active_zone.effect = DropEffect.NONE
                self._dispatch(DragEvent(
                    type="left_zone",
                    drag_data=self._current_drag,
                    drop_zone=self._active_zone,
                    x=x, y=y,
                ))

            # Entered new zone
            self._active_zone = hit_zone
            if hit_zone:
                effect = self._compute_effect(hit_zone)
                hit_zone.effect = effect
                self._dispatch(DragEvent(
                    type="entered_zone",
                    drag_data=self._current_drag,
                    drop_zone=hit_zone,
                    x=x, y=y,
                    action=hit_zone.action,
                ))

        self._dispatch(DragEvent(
            type="moved",
            drag_data=self._current_drag,
            x=x, y=y,
        ))

    def set_modifier(self, modifier: str, pressed: bool) -> None:
        """Update keyboard modifier state (ctrl, shift, alt)."""
        self._modifiers[modifier.lower()] = pressed

        # Update effect on active zone if modifier changed
        if self._active_zone and self._state == DragState.DRAGGING:
            effect = self._compute_effect(self._active_zone)
            self._active_zone.effect = effect

    def drop(self, x: int, y: int) -> bool:
        """Complete the drag operation at the given position.

        Returns True if the drop was accepted.
        """
        if self._state != DragState.DRAGGING:
            return False

        self._state = DragState.DROPPING
        zone = self._hit_test(x, y)

        if zone is None or zone.effect == DropEffect.REJECT:
            self._reject(x, y)
            return False

        # Determine final action based on modifiers
        action = self._resolve_action(zone)

        self._dispatch(DragEvent(
            type="dropped",
            drag_data=self._current_drag,
            drop_zone=zone,
            x=x, y=y,
            action=action,
        ))

        # Record in history
        self._history.append({
            "items": [d.id for d in self._items],
            "zone": zone.id,
            "action": action.value if action else "none",
            "time": time.time(),
        })

        # Reset state
        self._finish()
        return True

    def cancel(self) -> None:
        """Cancel the current drag operation."""
        if self._state not in (DragState.DRAGGING, DragState.PREPARING):
            return

        self._snap_back = True
        self._state = DragState.CANCELLED

        self._dispatch(DragEvent(
            type="cancelled",
            drag_data=self._current_drag,
            x=self._cursor_x,
            y=self._cursor_y,
        ))

        self._finish()

    # -- Hit testing ---------------------------------------------------

    def _hit_test(self, x: int, y: int) -> Optional[DropZone]:
        """Find the topmost drop zone at the given position."""
        if self._current_drag is None:
            return None

        mime = self._current_drag.mime_type
        for zone in reversed(self._drop_zones):
            if (zone.visible and zone.enabled and
                    zone.contains(x, y) and zone.accepts(mime)):
                return zone
        return None

    def _compute_effect(self, zone: DropZone) -> DropEffect:
        """Compute the visual effect based on current modifiers."""
        if not self._current_drag:
            return DropEffect.NONE

        if not zone.accepts(self._current_drag.mime_type):
            return DropEffect.REJECT

        if self._modifiers.get("shift"):
            return DropEffect.MOVE
        elif self._modifiers.get("alt"):
            return DropEffect.LINK
        elif self._modifiers.get("ctrl"):
            return DropEffect.COPY
        else:
            # Default effect from zone action
            mapping = {
                DropAction.COPY: DropEffect.COPY,
                DropAction.MOVE: DropEffect.MOVE,
                DropAction.LINK: DropEffect.LINK,
                DropAction.ASK: DropEffect.COPY,
            }
            return mapping.get(zone.action, DropEffect.COPY)

    def _resolve_action(self, zone: DropZone) -> DropAction:
        """Resolve the final drop action."""
        if self._modifiers.get("shift"):
            return DropAction.MOVE
        elif self._modifiers.get("alt"):
            return DropAction.LINK
        elif self._modifiers.get("ctrl"):
            return DropAction.COPY
        return zone.action

    def _reject(self, x: int, y: int) -> None:
        """Handle a rejected drop (snap back)."""
        self._snap_back = True
        self._snap_origin = (self._drag_start_x, self._drag_start_y)

        self._dispatch(DragEvent(
            type="rejected",
            drag_data=self._current_drag,
            x=x, y=y,
        ))

        self._finish()

    def _finish(self) -> None:
        """Clean up after a drag operation."""
        if self._active_zone:
            self._active_zone.effect = DropEffect.NONE
            self._active_zone = None

        self._state = DragState.IDLE
        self._current_drag = None
        self._items = []
        self._snap_back = False

    # -- Properties ----------------------------------------------------

    @property
    def state(self) -> DragState:
        return self._state

    @property
    def is_dragging(self) -> bool:
        return self._state == DragState.DRAGGING

    @property
    def cursor_x(self) -> int:
        return self._cursor_x

    @property
    def cursor_y(self) -> int:
        return self._cursor_y

    @property
    def active_zone(self) -> Optional[DropZone]:
        return self._active_zone

    @property
    def items(self) -> List[DragData]:
        return list(self._items)

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def preview(self) -> Optional[DragPreview]:
        """Get the drag preview for the current operation."""
        if not self._current_drag or self._state == DragState.IDLE:
            return None
        return DragPreview(
            label=self._current_drag.label or "Drag",
            icon=self._current_drag.icon,
            item_count=self._items.__len__(),
        )

    @property
    def drag_duration(self) -> float:
        """How long the current drag has been active (seconds)."""
        if self._state == DragState.IDLE:
            return 0.0
        return time.time() - self._drag_start_time

    @property
    def snap_back(self) -> bool:
        return self._snap_back

    @property
    def snap_origin(self) -> Tuple[int, int]:
        return self._snap_origin

    @property
    def history(self) -> List[Dict]:
        return list(self._history)

    # -- Rendering -----------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the drag overlay (zones, preview, effects)."""
        if self._state == DragState.IDLE:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except (OSError, IOError):
            font = font_bold = ImageFont.load_default()

        # Draw drop zone indicators
        for zone in self._drop_zones:
            if not zone.visible:
                continue
            self._render_zone(draw, zone, font)

        # Draw drag preview
        if self._current_drag:
            self._render_preview(draw, font, font_bold)

        return img

    def _render_zone(self, draw, zone: DropZone, font) -> None:
        """Render a drop zone highlight."""
        x, y, w, h = zone.rect

        effect_colors = {
            DropEffect.NONE: (100, 100, 100, 40),
            DropEffect.COPY: (60, 120, 220, 80),
            DropEffect.MOVE: (60, 200, 100, 80),
            DropEffect.LINK: (220, 160, 40, 80),
            DropEffect.REJECT: (220, 60, 60, 80),
        }

        fill = effect_colors.get(zone.effect, (100, 100, 100, 40))
        outline_color = {
            DropEffect.NONE: (100, 100, 100, 60),
            DropEffect.COPY: (60, 120, 220, 150),
            DropEffect.MOVE: (60, 200, 100, 150),
            DropEffect.LINK: (220, 160, 40, 150),
            DropEffect.REJECT: (220, 60, 60, 150),
        }.get(zone.effect, (100, 100, 100, 60))

        draw.rounded_rectangle(
            [x, y, x + w, y + h],
            radius=8,
            fill=fill,
            outline=outline_color,
            width=2,
        )

        # Label
        if zone.label and zone.effect != DropEffect.NONE:
            bbox = draw.textbbox((x, y), zone.label, font=font)
            tw = bbox[2] - bbox[0]
            tx = x + (w - tw) // 2
            ty = y + (h - 20) // 2
            draw.text((tx, ty), zone.label, fill=(255, 255, 255, 200), font=font)

    def _render_preview(self, draw, font, font_bold) -> None:
        """Render the drag preview under the cursor."""
        preview = self.preview
        if not preview:
            return

        x = self._cursor_x + preview.offset_x
        y = self._cursor_y + preview.offset_y

        # Preview card
        card_w = max(120, len(preview.label) * 10 + 60)
        card_h = 40

        # Shadow
        draw.rounded_rectangle(
            [x + 2, y + 2, x + card_w + 2, y + card_h + 2],
            radius=8, fill=(0, 0, 0, 80))

        # Card
        draw.rounded_rectangle(
            [x, y, x + card_w, y + card_h],
            radius=8, fill=(50, 50, 50, 220), outline=(120, 120, 120))

        # Icon
        if preview.icon:
            draw.text((x + 10, y + 8), preview.icon,
                      fill=(200, 200, 200), font=font_bold)

        # Label
        label_x = x + (36 if preview.icon else 12)
        draw.text((label_x, y + 10), preview.label,
                  fill=(230, 230, 230), font=font)

        # Count badge (multi-item)
        if preview.item_count > 1:
            badge = str(preview.item_count)
            bx = x + card_w - 28
            by = y + 4
            draw.ellipse([bx, by, bx + 22, by + 22],
                         fill=(60, 120, 220))
            draw.text((bx + 6, by + 3), badge,
                      fill=(255, 255, 255), font=font)

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event: DragEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"DragDropManager(state={self._state.value}, "
            f"zones={len(self._drop_zones)}, "
            f"items={len(self._items)})"
        )


__all__ = [
    "DragDropManager", "DragData", "DropZone", "DropAction",
    "DragState", "DropEffect", "DragPreview", "DragEvent",
]
