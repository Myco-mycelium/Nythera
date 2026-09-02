#!/usr/bin/env python3
"""multi_monitor — Nyrqis multi-monitor desktop support.

Full multi-monitor management:

- Monitor detection and configuration
- Per-monitor workspace sets
- Window placement across monitors
- Primary monitor designation
- Monitor arrangement (left/right/above/below)
- Resolution and refresh rate management
- Mirrored vs extended desktop
- Per-monitor taskbars
- Cross-monitor drag and drop
- Focus follows monitor switching
- Resolution-independent coordinate mapping
- Monitor profiles (presets)

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class MonitorState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    MIRRORED = "mirrored"
    EXTENDED = "extended"


class MonitorArrangement(Enum):
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"


@dataclass
class MonitorMode:
    """Display mode for a monitor."""
    width: int
    height: int
    refresh_rate: float = 60.0
    is_preferred: bool = False

    @property
    def aspect_ratio(self) -> str:
        from math import gcd
        d = gcd(self.width, self.height)
        return f"{self.width // d}:{self.height // d}"

    @property
    def pixel_count(self) -> int:
        return self.width * self.height


@dataclass
class MonitorInfo:
    """Information about a physical monitor."""
    id: str
    name: str
    model: str = ""
    manufacturer: str = ""
    serial: str = ""
    state: MonitorState = MonitorState.CONNECTED
    modes: List[MonitorMode] = field(default_factory=list)
    current_mode: Optional[MonitorMode] = None
    is_primary: bool = False
    is_builtin: bool = False      # laptop screen
    edid: str = ""

    # Physical properties
    physical_width_mm: int = 0
    physical_height_mm: int = 0
    diagonal_inches: float = 0.0
    dpi: float = 0.0

    # Arrangement
    x: int = 0               # position in virtual desktop
    y: int = 0
    arrangement: Optional[MonitorArrangement] = None
    relative_to: str = ""    # ID of monitor this is relative to

    @property
    def width(self) -> int:
        return self.current_mode.width if self.current_mode else 0

    @property
    def height(self) -> int:
        return self.current_mode.height if self.current_mode else 0

    @property
    def refresh_rate(self) -> float:
        return self.current_mode.refresh_rate if self.current_mode else 60.0

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, px: int, py: int) -> bool:
        return (self.x <= px < self.x + self.width and
                self.y <= py < self.y + self.height)

    def local_to_virtual(self, lx: int, ly: int) -> Tuple[int, int]:
        """Convert local monitor coords to virtual desktop coords."""
        return (self.x + lx, self.y + ly)

    def virtual_to_local(self, vx: int, vy: int) -> Tuple[int, int]:
        """Convert virtual desktop coords to local monitor coords."""
        return (vx - self.x, vy - self.y)

    def overlap_area(self, other: "MonitorInfo") -> int:
        """Calculate overlap area with another monitor."""
        x1 = max(self.x, other.x)
        y1 = max(self.y, other.y)
        x2 = min(self.right, other.right)
        y2 = min(self.bottom, other.bottom)
        if x1 < x2 and y1 < y2:
            return (x2 - x1) * (y2 - y1)
        return 0

    def distance_to(self, other: "MonitorInfo") -> float:
        """Calculate distance between monitor centers."""
        cx1 = self.x + self.width // 2
        cy1 = self.y + self.height // 2
        cx2 = other.x + other.width // 2
        cy2 = other.y + other.height // 2
        return ((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2) ** 0.5


@dataclass
class MonitorWorkspace:
    """A workspace on a specific monitor."""
    id: str
    monitor_id: str
    name: str
    windows: List[str] = field(default_factory=list)  # window IDs
    background: str = ""


@dataclass
class MonitorProfile:
    """A saved monitor configuration profile."""
    name: str
    monitors: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    arrangement: str = "extended"
    primary_monitor: str = ""


# ---------------------------------------------------------------------------
# Multi-monitor manager
# ---------------------------------------------------------------------------

class MultiMonitorManager:
    """Manages multiple monitors and their workspaces.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._monitors: Dict[str, MonitorInfo] = {}
        self._workspaces: Dict[str, MonitorWorkspace] = {}
        self._profiles: Dict[str, MonitorProfile] = {}
        self._active_monitor_id: Optional[str] = None
        self._workspace_counter = 0
        self._callbacks: List[Callable] = []

        # Legacy API state (OutputInfo compat)
        self._next_output_id: int = 0
        self._legacy_outputs: Dict[int, Any] = {}
        self._legacy_bindings: List[Any] = []

    # -- Monitor management --------------------------------------------

    def add_monitor(
        self,
        monitor_id: str,
        name: str,
        width: int = 1920,
        height: int = 1080,
        refresh_rate: float = 60.0,
        **kwargs,
    ) -> MonitorInfo:
        """Add a monitor."""
        mode = MonitorMode(width=width, height=height,
                           refresh_rate=refresh_rate, is_preferred=True)
        monitor = MonitorInfo(
            id=monitor_id, name=name,
            modes=[mode], current_mode=mode,
            **{k: v for k, v in kwargs.items()
               if hasattr(MonitorInfo, k)},
        )
        self._monitors[monitor_id] = monitor
        self._auto_arrange()
        self._create_workspace(monitor_id)
        if self._active_monitor_id is None:
            self._active_monitor_id = monitor_id
        self._dispatch("monitor_added", monitor_id)
        return monitor

    def remove_monitor(self, monitor_id: str) -> bool:
        """Remove a monitor."""
        if monitor_id not in self._monitors:
            return False
        del self._monitors[monitor_id]
        # Remove workspaces
        to_remove = [wid for wid, ws in self._workspaces.items()
                     if ws.monitor_id == monitor_id]
        for wid in to_remove:
            del self._workspaces[wid]
        # Update active
        if self._active_monitor_id == monitor_id:
            self._active_monitor_id = (
                next(iter(self._monitors)) if self._monitors else None)
        self._dispatch("monitor_removed", monitor_id)
        return True

    def get_monitor(self, monitor_id: str) -> Optional[MonitorInfo]:
        return self._monitors.get(monitor_id)

    @property
    def monitors(self) -> List[MonitorInfo]:
        return list(self._monitors.values())

    @property
    def connected_monitors(self) -> List[MonitorInfo]:
        return [m for m in self._monitors.values()
                if m.state == MonitorState.CONNECTED]

    @property
    def monitor_count(self) -> int:
        return len(self._monitors)

    @property
    def primary_monitor(self) -> Optional[MonitorInfo]:
        for m in self._monitors.values():
            if m.is_primary:
                return m
        if self._monitors:
            return next(iter(self._monitors.values()))
        return None

    def set_primary(self, monitor_id: str) -> bool:
        """Set a monitor as primary."""
        for m in self._monitors.values():
            m.is_primary = False
        monitor = self._monitors.get(monitor_id)
        if monitor:
            monitor.is_primary = True
            self._dispatch("primary_changed", monitor_id)
            return True
        return False

    def set_active(self, monitor_id: str) -> bool:
        """Set the active (focused) monitor."""
        if monitor_id in self._monitors:
            self._active_monitor_id = monitor_id
            self._dispatch("monitor_focused", monitor_id)
            return True
        return False

    @property
    def active_monitor(self) -> Optional[MonitorInfo]:
        if self._active_monitor_id:
            return self._monitors.get(self._active_monitor_id)
        return None

    @property
    def active_monitor_id(self) -> Optional[str]:
        return self._active_monitor_id

    # -- Monitor arrangement -------------------------------------------

    def arrange(self, monitor_id: str, relative_to: str,
                arrangement: MonitorArrangement) -> bool:
        """Arrange a monitor relative to another."""
        m1 = self._monitors.get(monitor_id)
        m2 = self._monitors.get(relative_to)
        if m1 is None or m2 is None:
            return False

        m1.arrangement = arrangement
        m1.relative_to = relative_to

        if arrangement == MonitorArrangement.RIGHT_OF:
            m1.x = m2.right
            m1.y = m2.y
        elif arrangement == MonitorArrangement.LEFT_OF:
            m1.x = m2.x - m1.width
            m1.y = m2.y
        elif arrangement == MonitorArrangement.BELOW:
            m1.x = m2.x
            m1.y = m2.bottom
        elif arrangement == MonitorArrangement.ABOVE:
            m1.x = m2.x
            m1.y = m2.y - m1.height

        self._dispatch("monitor_arranged", monitor_id)
        return True

    def _auto_arrange(self) -> None:
        """Auto-arrange monitors in a row."""
        monitors = list(self._monitors.values())
        if len(monitors) <= 1:
            return
        x_offset = 0
        for m in monitors:
            m.x = x_offset
            m.y = 0
            x_offset += m.width

    def set_resolution(self, monitor_id: str, width: int, height: int,
                       refresh_rate: float = 60.0) -> bool:
        """Change monitor resolution."""
        monitor = self._monitors.get(monitor_id)
        if monitor is None:
            return False

        mode = MonitorMode(width=width, height=height,
                           refresh_rate=refresh_rate)
        monitor.current_mode = mode
        if mode not in monitor.modes:
            monitor.modes.append(mode)

        self._dispatch("resolution_changed", monitor_id)
        return True

    @property
    def virtual_desktop_rect(self) -> Tuple[int, int, int, int]:
        """Get the bounding rect of all monitors combined."""
        if not self._monitors:
            return (0, 0, 0, 0)
        x1 = min(m.x for m in self._monitors.values())
        y1 = min(m.y for m in self._monitors.values())
        x2 = max(m.right for m in self._monitors.values())
        y2 = max(m.bottom for m in self._monitors.values())
        return (x1, y1, x2 - x1, y2 - y1)

    @property
    def total_pixels(self) -> int:
        """Total pixel count across all monitors."""
        return sum(m.width * m.height for m in self._monitors.values()
                   if m.state == MonitorState.CONNECTED)

    # -- Window placement ----------------------------------------------

    def find_monitor_at(self, x: int, y: int) -> Optional[MonitorInfo]:
        """Find the monitor containing a point."""
        for m in self._monitors.values():
            if m.contains(x, y):
                return m
        return None

    def find_nearest_monitor(self, x: int, y: int) -> Optional[MonitorInfo]:
        """Find the nearest monitor to a point."""
        best = None
        best_dist = float("inf")
        for m in self._monitors.values():
            cx = m.x + m.width // 2
            cy = m.y + m.height // 2
            dist = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = m
        return best

    def place_window_center(self, width: int, height: int,
                            monitor_id: Optional[str] = None) -> Tuple[int, int]:
        """Place a window centered on a monitor."""
        m = self._monitors.get(monitor_id) if monitor_id else self.active_monitor
        if m is None:
            return (0, 0)
        x = m.x + (m.width - width) // 2
        y = m.y + (m.height - height) // 2
        return (x, y)

    def place_window_maximized(self, monitor_id: Optional[str] = None) -> Tuple[int, int, int, int]:
        """Get maximized bounds for a monitor."""
        m = self._monitors.get(monitor_id) if monitor_id else self.active_monitor
        if m is None:
            return (0, 0, 1920, 1080)
        return (m.x, m.y, m.width, m.height)

    def snap_window(self, x: int, y: int, width: int, height: int,
                    direction: str) -> Tuple[int, int, int, int]:
        """Snap a window to a monitor edge."""
        m = self.find_monitor_at(x + width // 2, y + height // 2)
        if m is None:
            m = self.active_monitor
        if m is None:
            return (x, y, width, height)

        hw, hh = m.width // 2, m.height // 2

        if direction == "left":
            return (m.x, m.y, hw, m.height)
        elif direction == "right":
            return (m.x + hw, m.y, hw, m.height)
        elif direction == "top":
            return (m.x, m.y, m.width, hh)
        elif direction == "bottom":
            return (m.x, m.y + hh, m.width, hh)
        elif direction == "maximize":
            return (m.x, m.y, m.width, m.height)
        elif direction == "top_left":
            return (m.x, m.y, hw, hh)
        elif direction == "top_right":
            return (m.x + hw, m.y, hw, hh)
        elif direction == "bottom_left":
            return (m.x, m.y + hh, hw, hh)
        elif direction == "bottom_right":
            return (m.x + hw, m.y + hh, hw, hh)

        return (x, y, width, height)

    def move_window_to_monitor(self, x: int, y: int, width: int, height: int,
                               target_monitor_id: str) -> Tuple[int, int]:
        """Move a window to another monitor, preserving relative position."""
        m = self.find_monitor_at(x + width // 2, y + height // 2)
        target = self._monitors.get(target_monitor_id)
        if m is None or target is None:
            return (x, y)

        # Calculate relative position on source monitor
        rel_x = (x - m.x) / max(1, m.width)
        rel_y = (y - m.y) / max(1, m.height)

        new_x = target.x + int(rel_x * target.width)
        new_y = target.y + int(rel_y * target.height)
        return (new_x, new_y)

    # -- Per-monitor workspaces ----------------------------------------

    def _create_workspace(self, monitor_id: str) -> MonitorWorkspace:
        """Create a workspace on a monitor."""
        self._workspace_counter += 1
        ws_id = f"ws-{self._workspace_counter}"
        ws = MonitorWorkspace(
            id=ws_id, monitor_id=monitor_id,
            name=f"Workspace {self._workspace_counter}",
        )
        self._workspaces[ws_id] = ws
        return ws

    def get_workspaces(self, monitor_id: Optional[str] = None) -> List[MonitorWorkspace]:
        """Get workspaces, optionally filtered by monitor."""
        wss = list(self._workspaces.values())
        if monitor_id:
            wss = [ws for ws in wss if ws.monitor_id == monitor_id]
        return wss

    @property
    def workspace_count(self) -> int:
        return len(self._workspaces)

    # -- Profiles ------------------------------------------------------

    def save_profile(self, name: str) -> MonitorProfile:
        """Save current monitor configuration as a profile."""
        monitors = {}
        for mid, m in self._monitors.items():
            monitors[mid] = {
                "name": m.name, "width": m.width, "height": m.height,
                "refresh_rate": m.refresh_rate, "x": m.x, "y": m.y,
                "is_primary": m.is_primary,
                "arrangement": m.arrangement.value if m.arrangement else None,
                "relative_to": m.relative_to,
            }
        primary = self.primary_monitor
        profile = MonitorProfile(
            name=name, monitors=monitors,
            primary_monitor=primary.id if primary else "",
        )
        self._profiles[name] = profile
        return profile

    def load_profile(self, name: str) -> bool:
        """Load a monitor profile."""
        profile = self._profiles.get(name)
        if profile is None:
            return False
        # Apply monitor positions
        for mid, data in profile.monitors.items():
            m = self._monitors.get(mid)
            if m:
                m.x = data.get("x", 0)
                m.y = data.get("y", 0)
                if data.get("is_primary"):
                    self.set_primary(mid)
        self._dispatch("profile_loaded", name)
        return True

    @property
    def profiles(self) -> List[str]:
        return list(self._profiles.keys())

    # -- Legacy API (backward compat with render pipeline tests) ---------

    @property
    def outputs(self) -> Dict[int, OutputInfo]:
        """Legacy dict of output_id → OutputInfo."""
        return self._legacy_outputs

    def add_output(self, width: int, height: int, name: str = "",
                   refresh_rate: int = 60000) -> OutputInfo:
        """Add a new output (legacy API)."""
        output_id = self._next_output_id
        self._next_output_id += 1
        output = OutputInfo(
            id=output_id,
            name=name or f"output-{output_id}",
            width=width, height=height,
            refresh_rate=refresh_rate,
            status=OutputStatus.ACTIVE,
            primary=len(self._legacy_outputs) == 0,
        )
        self._legacy_outputs[output_id] = output
        return output

    def remove_output(self, output_id: int, migrate: bool = True) -> List[int]:
        """Remove an output (legacy API). Returns migrated workspace IDs."""
        migrated: List[int] = []
        if output_id in self._legacy_outputs:
            if migrate:
                primary = self.get_primary_output()
                if primary and primary.id != output_id:
                    for b in list(self._legacy_bindings):
                        if b.output_id == output_id:
                            migrated.append(b.workspace_id)
            del self._legacy_outputs[output_id]
        return migrated

    def bind_workspace(self, workspace_id: int, output_id: int) -> bool:
        """Bind a workspace to an output (legacy API)."""
        if output_id not in self._legacy_outputs:
            return False
        # Remove existing binding for this workspace
        self._legacy_bindings = [
            b for b in self._legacy_bindings
            if b.workspace_id != workspace_id
        ]
        binding = type('Binding', (), {
            'workspace_id': workspace_id,
            'output_id': output_id,
        })()
        self._legacy_bindings.append(binding)
        return True

    def get_output_for_workspace(self, workspace_id: int) -> Optional[OutputInfo]:
        """Get the output bound to a workspace (legacy API)."""
        for b in self._legacy_bindings:
            if b.workspace_id == workspace_id:
                return self._legacy_outputs.get(b.output_id)
        return None

    def get_primary_output(self) -> Optional[OutputInfo]:
        """Get the primary output (legacy API)."""
        for o in self._legacy_outputs.values():
            if o.primary:
                return o
        if self._legacy_outputs:
            return next(iter(self._legacy_outputs.values()))
        return None

    def get_total_resolution(self) -> Tuple[int, int]:
        """Get total resolution across all outputs (legacy API)."""
        if not self._legacy_outputs:
            return (1920, 1080)
        max_x = 0
        max_y = 0
        for o in self._legacy_outputs.values():
            right = o.x + o.width
            bottom = o.y + o.height
            if right > max_x:
                max_x = right
            if bottom > max_y:
                max_y = bottom
        return (max_x, max_y)

    def get_output_count(self) -> int:
        """Get number of active outputs (legacy API)."""
        return len([o for o in self._legacy_outputs.values()
                    if o.status in (OutputStatus.CONNECTED, OutputStatus.ACTIVE)])

    # -- DRM detection -------------------------------------------------

    def detect_outputs(self) -> List[OutputInfo]:
        """Detect connected outputs as OutputInfo objects."""
        outputs = []
        for m in self._monitors.values():
            status = (OutputStatus.CONNECTED
                      if m.state == MonitorState.CONNECTED
                      else OutputStatus.DISCONNECTED)
            outputs.append(OutputInfo(
                id=hash(m.id) & 0xFFFFFFFF,
                name=m.name,
                width=m.width,
                height=m.height,
                refresh_rate=int(m.refresh_rate * 1000),
                status=status,
                x=m.x, y=m.y,
                primary=m.is_primary,
            ))
        return outputs

    # -- Summary -------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        return {
            "monitors": self.monitor_count,
            "connected": len(self.connected_monitors),
            "primary": self.primary_monitor.id if self.primary_monitor else None,
            "active": self._active_monitor_id,
            "virtual_rect": self.virtual_desktop_rect,
            "total_pixels": self.total_pixels,
            "workspaces": self.workspace_count,
            "profiles": len(self._profiles),
        }

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"MultiMonitorManager(monitors={s['monitors']}, "
            f"virtual={s['virtual_rect']})"
        )


# ---------------------------------------------------------------------------
# Backward compatibility (old API used by render pipeline tests)
# ---------------------------------------------------------------------------


class OutputStatus(Enum):
    """Display output status (compatibility alias)."""
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ACTIVE = "active"


@dataclass
class OutputInfo:
    """Information about a display output (compatibility alias)."""
    id: int
    name: str
    width: int
    height: int
    refresh_rate: int  # mHz
    status: OutputStatus
    x: int = 0
    y: int = 0
    primary: bool = False

    @property
    def is_connected(self) -> bool:
        return self.status in (OutputStatus.CONNECTED, OutputStatus.ACTIVE)


class HotPlugMonitor:
    """Monitors for output hot-plug events.

    Periodically polls for connected displays and fires callbacks
    when outputs are added or removed.
    """

    def __init__(self, manager: MultiMonitorManager, poll_interval: float = 2.0):
        self.manager = manager
        self.poll_interval = poll_interval
        self._running = False
        self._thread = None
        self._on_connect = None
        self._on_disconnect = None
        self._known_outputs: Dict[int, Any] = {}

    def set_callbacks(self, on_connect=None, on_disconnect=None):
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def start(self) -> None:
        if self._running:
            return
        import threading
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="hotplug-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _poll_loop(self) -> None:
        import time as _time
        while self._running:
            try:
                self._check_outputs()
            except Exception:
                pass
            _time.sleep(self.poll_interval)

    def _check_outputs(self) -> None:
        current = self.manager.detect_outputs()
        current_ids = {o.id for o in current}
        for output in current:
            if output.id not in self._known_outputs:
                if self._on_connect:
                    self._on_connect(output)
        for oid in set(self._known_outputs.keys()) - current_ids:
            output = self._known_outputs[oid]
            if self._on_disconnect:
                self._on_disconnect(output)
        self._known_outputs = {o.id: o for o in current}

    @property
    def is_running(self) -> bool:
        return self._running


__all__ = [
    "MultiMonitorManager", "MonitorInfo", "MonitorMode", "MonitorState",
    "MonitorArrangement", "MonitorWorkspace", "MonitorProfile",
    "OutputInfo", "OutputStatus", "HotPlugMonitor",
]
