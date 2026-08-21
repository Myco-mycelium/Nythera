#!/usr/bin/env python3
"""DesktopSession — interactive desktop shell for Nyrqis.

Turns a static .nstudio document into a live, interactive desktop with:

- Window stack management (z-order, focus, raise/lower)
- Mouse input (click, drag-to-move, drag-to-resize, hover)
- Keyboard input (dispatched to focused window)
- Hit-testing through the component tree
- Event routing to NUI behaviors

This is the Nyrqis counterpart of a Wayland compositor: it owns the
input devices and presents a window hierarchy to applications. On the
floor (development/testing) it runs synchronously; on a real OS it
would be driven by epoll on the input device file descriptors.

Architecture:
  .nstudio document
        │
        ▼
  DesktopSession
    ├── WindowManager (z-order, focus, stacking)
    ├── InputRouter   (mouse → hit-test → behavior, keyboard → focused)
    └── Compositor    (PIL render, already exists)

The session owns the **authoritative window list**; the compositor
reads it for rendering. State mutations (theme changes, bindings)
flow through the same NyrqisRuntime that shell.py uses.

References:
  - NFS-001 §7: behaviors (WHEN/IF/DO)
  - NFS-001 §8: bindings (component property ← state)
  - ADR-0025 §9: runtime consumption decision
  - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from .nstudio import NstudioComponent, NstudioDocument, NstudioValidationError
from .runtime import NyrqisRuntime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input events
# ---------------------------------------------------------------------------

class MouseButton(Enum):
    NONE = 0
    LEFT = auto()
    RIGHT = auto()
    MIDDLE = auto()


@dataclass(frozen=True)
class MouseEvent:
    """A mouse input event."""
    x: int
    y: int
    button: MouseButton = MouseButton.NONE
    dx: int = 0
    dy: int = 0
    double_click: bool = False


@dataclass(frozen=True)
class KeyEvent:
    """A keyboard input event."""
    key: str              # logical key name, e.g. "a", "Enter", "Escape"
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    super_key: bool = False  # "super" is Python-reserved, use super_key


class EventType(Enum):
    """High-level event types for the event loop callback."""
    MOUSE_DOWN = auto()
    MOUSE_UP = auto()
    MOUSE_MOVE = auto()
    MOUSE_DOUBLE_CLICK = auto()
    KEY_DOWN = auto()
    KEY_UP = auto()
    FOCUS_CHANGE = auto()
    WINDOW_CLOSE = auto()


@dataclass
class InputEvent:
    """A unified input event dispatched through the event loop."""
    type: EventType
    mouse: Optional[MouseEvent] = None
    key: Optional[KeyEvent] = None
    component_id: Optional[str] = None   # hit-test target
    window_id: Optional[str] = None      # owning window


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

@dataclass
class Window:
    """A window in the desktop shell.

    Each window maps to a top-level NstudioComponent (typically a
    ``Window`` type) inside the .nstudio document.  The session
    maintains a list of windows ordered by z-index (index 0 = bottom,
    index -1 = topmost).
    """
    id: str
    component_id: str          # the NUI component that is this window's root
    title: str = ""
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    min_width: int = 200
    min_height: int = 100
    visible: bool = True
    minimized: bool = False
    maximized: bool = False
    focused: bool = False
    # Saved geometry for un-maximize
    _saved_x: int = 0
    _saved_y: int = 0
    _saved_w: int = 800
    _saved_h: int = 600


# ---------------------------------------------------------------------------
# Hit-test result
# ---------------------------------------------------------------------------

@dataclass
class HitResult:
    """Result of a hit-test at a screen coordinate."""
    component: Optional[NstudioComponent] = None
    window: Optional[Window] = None
    local_x: int = 0          # x relative to the component
    local_y: int = 0          # y relative to the component
    hit: bool = False


# ---------------------------------------------------------------------------
# DesktopSession
# ---------------------------------------------------------------------------

class DesktopSession:
    """Interactive desktop session.

    Loads a .nstudio document, creates the runtime, and provides an
    event loop that routes mouse/keyboard input to the correct
    component and fires NUI behaviors.

    Parameters
    ----------
    document : NstudioDocument
        The loaded shell design.
    log : callable, optional
        Runtime log callback.
    """

    # Title-bar height (pixels) for drag-to-move hit area
    TITLEBAR_HEIGHT = 32

    def __init__(
        self,
        document: NstudioDocument,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._doc = document
        self._runtime = NyrqisRuntime(document, log=log)
        self._log = log or (lambda msg: None)
        self._windows: List[Window] = []
        self._focused_window_id: Optional[str] = None
        self._dragging: Optional[str] = None       # window id being dragged
        self._drag_offset: Tuple[int, int] = (0, 0)
        self._resizing: Optional[str] = None        # window id being resized
        self._resize_edge: str = ""                 # "right", "bottom", etc.
        self._resize_start: Tuple[int, int] = (0, 0)
        self._resize_orig: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._event_log: List[InputEvent] = []
        self._callbacks: Dict[EventType, List[Callable]] = {}
        self._running = False

        # Build initial window list from top-level Window components
        self._build_windows()

    # -- Factory -------------------------------------------------------

    @classmethod
    def from_file(cls, path: str, log=None) -> "DesktopSession":
        """Load from a .nstudio file."""
        from .nstudio import load
        doc = load(path)
        return cls(doc, log=log)

    @classmethod
    def from_json(cls, text: str, log=None) -> "DesktopSession":
        """Load from a JSON string."""
        from .nstudio import loads
        doc = loads(text)
        return cls(doc, log=log)

    # -- Properties ----------------------------------------------------

    @property
    def runtime(self) -> NyrqisRuntime:
        return self._runtime

    @property
    def document(self) -> NstudioDocument:
        return self._doc

    @property
    def windows(self) -> List[Window]:
        return list(self._windows)

    @property
    def focused_window(self) -> Optional[Window]:
        if self._focused_window_id is None:
            return None
        for w in self._windows:
            if w.id == self._focused_window_id:
                return w
        return None

    @property
    def event_log(self) -> List[InputEvent]:
        return list(self._event_log)

    # -- Window management ---------------------------------------------

    def add_window(self, window: Window) -> None:
        """Add a window to the top of the stack."""
        self._windows.append(window)
        self._focus_window(window.id)
        self._log(f"Window '{window.title or window.id}' added")

    def remove_window(self, window_id: str) -> bool:
        """Remove a window from the session."""
        for i, w in enumerate(self._windows):
            if w.id == window_id:
                self._windows.pop(i)
                if self._focused_window_id == window_id:
                    self._focused_window_id = None
                    # Focus the topmost remaining window
                    if self._windows:
                        self._focus_window(self._windows[-1].id)
                self._log(f"Window '{window_id}' removed")
                return True
        return False

    def focus_window(self, window_id: str) -> bool:
        """Bring a window to focus and raise it to the top."""
        return self._focus_window(window_id)

    def minimize_window(self, window_id: str) -> bool:
        for w in self._windows:
            if w.id == window_id:
                w.minimized = True
                w.focused = False
                if self._focused_window_id == window_id:
                    self._focused_window_id = None
                    self._auto_focus_topmost()
                self._log(f"Window '{window_id}' minimized")
                return True
        return False

    def maximize_window(self, window_id: str) -> bool:
        for w in self._windows:
            if w.id == window_id:
                if w.maximized:
                    # Restore
                    w.x, w.y = w._saved_x, w._saved_y
                    w.width, w.height = w._saved_w, w._saved_h
                    w.maximized = False
                else:
                    # Save and maximize
                    w._saved_x, w._saved_y = w.x, w.y
                    w._saved_w, w._saved_h = w.width, w.height
                    w.x, w.y = 0, 0
                    # Use the screen size from the document
                    screen = self._doc.screens[0] if self._doc.screens else None
                    if screen:
                        w.width = screen.size.get("width", 1920)
                        w.height = screen.size.get("height", 1080)
                    w.maximized = True
                self._log(f"Window '{window_id}' {'maximized' if w.maximized else 'restored'}")
                return True
        return False

    def close_window(self, window_id: str) -> bool:
        """Close a window — fires WINDOW_CLOSE event and removes it."""
        event = InputEvent(type=EventType.WINDOW_CLOSE, window_id=window_id)
        self._dispatch_event(event)
        return self.remove_window(window_id)

    # -- Hit-testing ---------------------------------------------------

    def hit_test(self, x: int, y: int) -> HitResult:
        """Find the component at screen coordinates (x, y).

        Iterates windows from topmost to bottommost, testing each
        window's component tree. Returns the deepest (most specific)
        component that contains the point.
        """
        # Iterate windows from top (last) to bottom (first)
        for window in reversed(self._windows):
            if not window.visible or window.minimized:
                continue
            if not (window.x <= x < window.x + window.width
                    and window.y <= y < window.y + window.height):
                continue

            # Hit-test inside this window's component tree
            comp = self._doc.find_component(window.component_id)
            if comp is None:
                continue

            result = self._hit_component(
                comp, x - window.x, y - window.y, window)
            if result.hit:
                return result

            # The window itself was hit (even if no child was)
            return HitResult(
                component=comp, window=window,
                local_x=x - window.x, local_y=y - window.y, hit=True)

        # Fallback: hit-test against the screen root (DesktopSurface)
        # so clicks on the desktop background, taskbar, etc. register.
        for screen in self._doc.screens:
            root = screen.root
            sw = screen.size.get("width", 1920)
            sh = screen.size.get("height", 1080)
            if 0 <= x < sw and 0 <= y < sh:
                result = self._hit_component(root, x, y, None)
                if result.hit:
                    return result
                return HitResult(
                    component=root, window=None,
                    local_x=x, local_y=y, hit=True)

        return HitResult(hit=False)

    def _hit_component(
        self,
        comp: NstudioComponent,
        local_x: int,
        local_y: int,
        window: Window,
    ) -> HitResult:
        """Recursive hit-test inside a component tree."""
        layout = comp.layout
        cx = layout.get("x", 0)
        cy = layout.get("y", 0)
        cw = layout.get("width", 0)
        ch = layout.get("height", 0)

        # Test children first (deepest wins) — render order = child order,
        # last child is on top.
        for child in reversed(comp.children):
            result = self._hit_component(child, local_x, local_y, window)
            if result.hit:
                return result

        # Test this component
        if (cx <= local_x < cx + cw and cy <= local_y < cy + ch):
            return HitResult(
                component=comp, window=window,
                local_x=local_x - cx, local_y=local_y - cy, hit=True)

        return HitResult(hit=False)

    # -- Event dispatch ------------------------------------------------

    def on_event(self, event_type: EventType, callback: Callable) -> None:
        """Register a callback for an event type."""
        self._callbacks.setdefault(event_type, []).append(callback)

    def process_mouse_event(self, event: MouseEvent) -> Optional[InputEvent]:
        """Process a mouse event: hit-test, route, and fire behaviors.

        Returns the resolved InputEvent (or None if nothing was hit).
        """
        hit = self.hit_test(event.x, event.y)
        event_type = {
            MouseButton.LEFT: EventType.MOUSE_DOWN,
            MouseButton.RIGHT: EventType.MOUSE_DOWN,
            MouseButton.MIDDLE: EventType.MOUSE_DOWN,
        }.get(event.button, EventType.MOUSE_MOVE)

        if event.button == MouseButton.NONE:
            event_type = EventType.MOUSE_MOVE

        inp = InputEvent(
            type=event_type,
            mouse=event,
            component_id=hit.component.id if hit.component else None,
            window_id=hit.window.id if hit.window else None,
        )

        # Focus the hit window
        if hit.window and hit.window.id != self._focused_window_id:
            self._focus_window(hit.window.id)

        # Drag logic
        if event_type == EventType.MOUSE_DOWN and hit.window:
            self._begin_drag_or_resize(hit, event)

        if event.button == MouseButton.NONE and self._dragging:
            # Mouse-move while dragging
            self._continue_drag(event)
            inp.type = EventType.MOUSE_MOVE
        elif event.button == MouseButton.NONE and self._resizing:
            self._continue_resize(event)
            inp.type = EventType.MOUSE_MOVE

        self._event_log.append(inp)
        self._dispatch_event(inp)

        # Fire NUI behavior if a component was hit
        if hit.component and event.button != MouseButton.NONE:
            self._fire_component_event(hit.component, event)

        return inp

    def process_key_event(self, event: KeyEvent) -> Optional[InputEvent]:
        """Process a keyboard event: dispatch to focused window."""
        event_type = EventType.KEY_DOWN
        inp = InputEvent(
            type=event_type,
            key=event,
            window_id=self._focused_window_id,
        )

        # Global shortcuts
        if event.ctrl and event.key == "w":
            if self._focused_window_id:
                self.close_window(self._focused_window_id)
                return inp
        elif event.ctrl and event.key == "n":
            if self._focused_window_id:
                self.minimize_window(self._focused_window_id)
                return inp
        elif event.ctrl and event.key == "m":
            if self._focused_window_id:
                self.maximize_window(self._focused_window_id)
                return inp

        self._event_log.append(inp)
        self._dispatch_event(inp)

        # Fire behavior on focused window's root component
        if self._focused_window_id:
            win = self.focused_window
            if win:
                comp = self._doc.find_component(win.component_id)
                if comp:
                    self._fire_key_event(comp, event)

        return inp

    def process_mouse_up(self, event: MouseEvent) -> None:
        """End a drag or resize operation."""
        if self._dragging:
            self._end_drag()
        if self._resizing:
            self._end_resize()
        # Release focus from resize/drag mode

    # -- Event loop (synchronous, for testing/development) -------------

    def run_event_loop(self, duration: float = 1.0, fps: int = 60) -> None:
        """Run the event loop for ``duration`` seconds at ``fps``.

        On a real OS this would read from /dev/input/eventX and
        /dev/fb0 (or DRM/KMS). On the floor it simply ticks.
        """
        self._running = True
        frame_time = 1.0 / fps
        frames = int(duration * fps)
        start = time.monotonic()

        for _ in range(frames):
            if not self._running:
                break
            # On a real OS: poll input devices, dispatch events,
            # render to framebuffer.
            # On the floor: just tick (nothing to do — events come in
            # via process_* methods).
            elapsed = time.monotonic() - start
            target = (len(self._event_log) + 1) * frame_time
            if elapsed < target:
                time.sleep(target - elapsed)

        self._running = False

    def stop(self) -> None:
        """Stop the event loop."""
        self._running = False

    # -- Render (delegates to compositor) ------------------------------

    def render(self) -> Any:
        """Render the current session state to a PIL Image.

        Uses the existing PIL Compositor but applies the session's
        window positions on top of the .nstudio layout.
        """
        from .compositor import Compositor

        # Pick theme from document
        theme = self._doc.themes.get("active", "Eclipse")
        compositor = Compositor(theme_name=theme)

        # Find the primary screen
        if not self._doc.screens:
            raise RuntimeError("no screens in document")
        screen = self._doc.screens[0]

        return compositor.render_screen(self._doc, screen_id=screen.id)

    def live_render(self) -> Any:
        """Render the session with live window positions applied.

        This overrides each Window component's layout coordinates
        with the session's current window positions (from drag,
        maximize, etc.) before rendering.  Minimized windows are
        skipped; focused windows get a subtle highlight.

        Returns a PIL Image.
        """
        from .compositor import Compositor
        from .nstudio import NstudioComponent
        import copy

        # Deep-copy the document so we don't mutate the original
        doc = copy.deepcopy(self._doc)

        # Override window layouts with session positions
        for win in self._windows:
            comp = doc.find_component(win.component_id)
            if comp is None:
                continue
            if win.minimized:
                # Move off-screen for minimized windows
                comp.layout["x"] = -9999
                comp.layout["y"] = -9999
            else:
                comp.layout["x"] = win.x
                comp.layout["y"] = win.y
                comp.layout["width"] = win.width
                comp.layout["height"] = win.height

        # Pick theme from (possibly mutated) document
        theme = doc.themes.get("active", "Eclipse")
        compositor = Compositor(theme_name=theme)

        if not doc.screens:
            raise RuntimeError("no screens in document")
        screen = doc.screens[0]

        return compositor.render_screen(doc, screen_id=screen.id)

    def render_to_file(self, path: str) -> str:
        """Render the live session to a PNG file."""
        img = self.live_render()
        img.save(path)
        return path

    def summary(self) -> Dict[str, Any]:
        """Session summary for diagnostics."""
        return {
            "windows": len(self._windows),
            "focused": self._focused_window_id,
            "visible": sum(1 for w in self._windows if w.visible and not w.minimized),
            "minimized": sum(1 for w in self._windows if w.minimized),
            "events_processed": len(self._event_log),
            **self._runtime.summary(),
        }

    # -- Internal: tree helpers -----------------------------------------

    def _find_parent(self, component_id: str) -> Optional[NstudioComponent]:
        """Find the parent of a component in the document tree."""
        def walk(c: NstudioComponent) -> Optional[NstudioComponent]:
            for child in c.children:
                if child.id == component_id:
                    return c
                found = walk(child)
                if found is not None:
                    return found
            return None
        for screen in self._doc.screens:
            found = walk(screen.root)
            if found is not None:
                return found
        return None

    # -- Internal: window building -------------------------------------

    def _build_windows(self) -> None:
        """Build the initial window list from top-level Window components.

        Walks each screen's root children — any component with type
        ``Window`` or ``DesktopSurface`` becomes a session window.
        """
        for screen in self._doc.screens:
            root = screen.root
            sw = screen.size.get("width", 1920)
            sh = screen.size.get("height", 1080)

            # Walk children of the root — DesktopSurface is the root
            # container, not a window itself.
            for child in root.children:
                if child.type in ("Window", "StartMenu",
                                  "NotificationCenter",
                                  "QuickSettings", "CommandPalette",
                                  "LockScreen"):
                    w = Window(
                        id=f"win-{child.id}",
                        component_id=child.id,
                        title=child.properties.get("title", child.id),
                        x=child.layout.get("x", 0),
                        y=child.layout.get("y", 0),
                        width=child.layout.get("width", 800),
                        height=child.layout.get("height", 600),
                    )
                    self._windows.append(w)

        # Focus the topmost window
        if self._windows:
            self._focus_window(self._windows[-1].id)

    # -- Internal: focus management ------------------------------------

    def _focus_window(self, window_id: str) -> bool:
        """Bring a window to the top and give it focus."""
        for w in self._windows:
            if w.id == window_id:
                if w.minimized:
                    w.minimized = False
                # Remove from current position and put on top
                self._windows.remove(w)
                self._windows.append(w)
                # Update focus
                for other in self._windows:
                    other.focused = False
                w.focused = True
                self._focused_window_id = window_id
                self._log(f"Focus → '{w.title or w.id}'")
                self._dispatch_event(InputEvent(
                    type=EventType.FOCUS_CHANGE, window_id=window_id))
                return True
        return False

    def _auto_focus_topmost(self) -> None:
        """Focus the topmost visible, non-minimized window."""
        for w in reversed(self._windows):
            if w.visible and not w.minimized:
                self._focus_window(w.id)
                return

    # -- Internal: drag and resize -------------------------------------

    def _hit_resize_edge(self, local_x: int, local_y: int, win: Window) -> Optional[str]:
        """Detect if a point is on a resize edge. Returns edge name
        like 'right', 'left', 'top', 'bottom', 'right-bottom', etc.
        or None if not on any edge."""
        margin = 8
        w, h = win.width, win.height
        on_left = local_x < margin
        on_right = local_x >= w - margin
        on_top = local_y < margin
        on_bottom = local_y >= h - margin
        parts = []
        if on_left:
            parts.append("left")
        if on_right:
            parts.append("right")
        if on_top:
            parts.append("top")
        if on_bottom:
            parts.append("bottom")
        if not parts:
            return None
        return "-".join(parts)

    def _begin_drag_or_resize(self, hit: HitResult, event: MouseEvent) -> None:
        """Start a drag (title bar) or resize (edge)."""
        if hit.window is None:
            return
        win = hit.window

        # Check if we're in the title bar area
        if hit.local_y < self.TITLEBAR_HEIGHT and not win.maximized:
            self._dragging = win.id
            self._drag_offset = (hit.local_x, hit.local_y)
            self._log(f"Drag start: '{win.title or win.id}'")
            return

        # Check if we're on a resize edge (all 4 edges + corners)
        if not win.maximized:
            edge = self._hit_resize_edge(hit.local_x, hit.local_y, win)
            if edge:
                self._resizing = win.id
                self._resize_edge = edge
                self._resize_start = (event.x, event.y)
                self._resize_orig = (win.x, win.y, win.width, win.height)
                self._log(f"Resize start: '{win.title or win.id}' ({edge})")

    def _continue_drag(self, event: MouseEvent) -> None:
        """Update window position during drag."""
        if not self._dragging:
            return
        win = self._find_window(self._dragging)
        if win is None:
            return
        win.x = max(0, event.x - self._drag_offset[0])
        win.y = max(0, event.y - self._drag_offset[1])

    def _end_drag(self) -> None:
        """Finish a drag operation."""
        if self._dragging:
            win = self._find_window(self._dragging)
            if win:
                self._log(f"Drag end: '{win.title or win.id}' at ({win.x}, {win.y})")
        self._dragging = None
        self._drag_offset = (0, 0)

    def _continue_resize(self, event: MouseEvent) -> None:
        """Update window size during resize using delta from start."""
        if not self._resizing:
            return
        win = self._find_window(self._resizing)
        if win is None or not hasattr(self, '_resize_orig'):
            return
        ox, oy, ow, oh = self._resize_orig
        dx = event.x - self._resize_start[0]
        dy = event.y - self._resize_start[1]
        edge = self._resize_edge
        # Right edge
        if "right" in edge:
            win.width = max(win.min_width, ow + dx)
        # Bottom edge
        if "bottom" in edge:
            win.height = max(win.min_height, oh + dy)
        # Left edge (move x, shrink width)
        if "left" in edge:
            new_w = max(win.min_width, ow - dx)
            win.x = ox + (ow - new_w)
            win.width = new_w
        # Top edge (move y, shrink height)
        if "top" in edge and "title" not in edge:
            new_h = max(win.min_height, oh - dy)
            win.y = oy + (oh - new_h)
            win.height = new_h

    def _end_resize(self) -> None:
        """Finish a resize operation."""
        if self._resizing:
            win = self._find_window(self._resizing)
            if win:
                self._log(
                    f"Resize end: '{win.title or win.id}' "
                    f"{win.width}x{win.height}")
        self._resizing = None
        self._resize_edge = ""

    def _find_window(self, window_id: Optional[str]) -> Optional[Window]:
        if window_id is None:
            return None
        for w in self._windows:
            if w.id == window_id:
                return w
        return None

    # -- Internal: behavior firing -------------------------------------

    def _fire_component_event(
        self, comp: NstudioComponent, event: MouseEvent
    ) -> None:
        """Fire the appropriate NUI behavior for a mouse click.

        Walks up from the clicked component to ancestors, checking
        each for the matching event (e.g. 'clicked').
        """
        button_event = "clicked"
        if event.button == MouseButton.RIGHT:
            button_event = "rightClicked"
        elif getattr(event, 'double_click', False):
            button_event = "doubleClicked"

        # Walk from the component up through ancestors
        target = comp
        while target is not None:
            if button_event in target.events:
                behavior_id = target.events[button_event]
                if behavior_id:
                    try:
                        actions = self._runtime.fire_event(
                            target.id, button_event)
                        if actions:
                            self._log(
                                f"Behavior fired: {target.id}"
                                f".{button_event} → {len(actions)}"
                                f" action(s)")
                    except NstudioValidationError as e:
                        self._log(f"Behavior error: {e}")
                break
            # Walk up to parent — find_component is tree-wide,
            # so find which screen contains this component
            target = self._find_parent(target.id)

    def _fire_key_event(
        self, root_comp: NstudioComponent, event: KeyEvent
    ) -> None:
        """Fire key-related behaviors on the focused window's tree."""
        # Walk the component tree looking for components with key events
        def walk(comp: NstudioComponent) -> None:
            if "KeyDown" in comp.events:
                behavior_id = comp.events["KeyDown"]
                if behavior_id:
                    try:
                        self._runtime.fire_event(comp.id, "KeyDown")
                    except NstudioValidationError:
                        pass
            for child in comp.children:
                walk(child)

        walk(root_comp)

    # -- Internal: event dispatch --------------------------------------

    def _dispatch_event(self, event: InputEvent) -> None:
        """Dispatch an event to registered callbacks."""
        for callback in self._callbacks.get(event.type, []):
            try:
                callback(event)
            except Exception as e:
                self._log(f"Event callback error: {e}")


__all__ = [
    "DesktopSession",
    "Window",
    "MouseEvent",
    "KeyEvent",
    "MouseButton",
    "EventType",
    "InputEvent",
    "HitResult",
]
