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
from .commands import (
    UndoManager, Command,
    AddWindowCommand, RemoveWindowCommand, MoveWindowCommand,
    ResizeWindowCommand, FocusWindowCommand, MinimizeWindowCommand,
    MaximizeWindowCommand, ChangePropertyCommand, ChangeThemeCommand,
    install_undo,
)
from .animation import AnimationTimeline

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
    opacity: float = 1.0
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
# Monitor
# ---------------------------------------------------------------------------

@dataclass
class Monitor:
    """A display monitor in the desktop shell.

    Each monitor has its own coordinate space.  Windows are placed
    in a monitor's local coordinates; the session maps them to
    global screen coordinates when hit-testing.
    """
    id: str
    x: int = 0                # global x offset
    y: int = 0                # global y offset
    width: int = 1920
    height: int = 1080
    scale: float = 1.0
    primary: bool = True
    name: str = ""


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

@dataclass
class Workspace:
    """A virtual desktop workspace.

    Each workspace holds its own set of window IDs.  The active
    workspace determines which windows are visible.
    """
    id: str
    name: str = ""
    window_ids: List[str] = field(default_factory=list)
    monitor_id: str = "default"


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

        # Multi-monitor support
        self._monitors: List[Monitor] = []
        self._workspaces: List[Workspace] = []
        self._active_workspace_id: Optional[str] = None

        # Notification service
        from .notifications import NotificationService
        self._notifications = NotificationService()

        # Shell chrome components (Taskbar, StartMenu, etc.) — tracked
        # separately from user windows so window management only sees
        # application windows.
        self._shell_components: Dict[str, Any] = {}  # id -> nstudio node

        # Undo/redo manager — enabled by default so all mutations are
        # tracked automatically.  Call ``enable_undo()`` to reconfigure
        # the depth, or ``disable_undo()`` to turn it off.
        self._undo_manager: Optional[UndoManager] = UndoManager()

        # Animation timeline (NUI-SCHEMA §8.3)
        self._timeline = AnimationTimeline()

        # Wayland display (ADR-0026) — lazy-initialized.
        # When available, rendering goes to real hardware surfaces.
        # When unavailable, rendering falls back to PIL.
        self._wayland_display: Optional[Any] = None

        # Build initial window list from top-level Window components
        self._build_windows()
        self._build_monitors()
        self._build_workspaces()

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

    # -- Undo / redo ---------------------------------------------------

    def enable_undo(self, max_depth: int = 100) -> UndoManager:
        """Enable undo/redo for this session.

        Installs an UndoManager and monkey-patches execute/undo/redo
        onto the session.  All subsequent mutations through the
        command API will be tracked.

        Returns the UndoManager for direct access.
        """
        return install_undo(self, max_depth=max_depth)

    @property
    def undo_manager(self) -> Optional[UndoManager]:
        """The undo manager, or None if undo is not enabled."""
        return self._undo_manager

    def execute(self, cmd: Command) -> None:
        """Execute a command (only if undo is enabled)."""
        if self._undo_manager is None:
            raise RuntimeError("Undo not enabled — call session.enable_undo() first")
        self._undo_manager.push(cmd)

    def undo(self) -> Optional[Command]:
        """Undo the last command."""
        if self._undo_manager is None:
            return None
        return self._undo_manager.undo()

    def redo(self) -> Optional[Command]:
        """Redo the last undone command."""
        if self._undo_manager is None:
            return None
        return self._undo_manager.redo()

    # -- Animation timeline --------------------------------------------

    @property
    def timeline(self) -> AnimationTimeline:
        """The animation timeline managing all active animations."""
        return self._timeline

    def tick(self, elapsed_ms: float = 16.0) -> Dict[str, Any]:
        """Advance the session by one frame.

        Updates the animation timeline, applies animated property
        values to windows, and returns the current animation snapshot.

        Call this every frame in a render loop (typically ~16 ms
        for 60 fps).
        """
        self._timeline.tick(elapsed_ms)
        snapshot = self._timeline.snapshot()

        # Apply animated properties to windows
        for window in self._windows:
            for key, value in snapshot.items():
                prefix = f"{window.component_id}."
                if key.startswith(prefix):
                    prop = key[len(prefix):]
                    if prop == "opacity":
                        window.opacity = float(value) if value is not None else 1.0
                    elif prop == "x":
                        window.x = int(value) if value is not None else window.x
                    elif prop == "y":
                        window.y = int(value) if value is not None else window.y
                    elif prop == "width":
                        window.width = int(value) if value is not None else window.width
                    elif prop == "height":
                        window.height = int(value) if value is not None else window.height

        return snapshot

    def play_animation(self, animation_id: str, **kwargs: Any) -> None:
        """Play an animation from the document's animation list."""
        for anim in self._doc.animations:
            if anim.id == animation_id:
                self._timeline.play_from_nstudio(anim, state=self._doc.states)
                return
        self._log(f"Animation '{animation_id}' not found in document")

    # -- Window management ---------------------------------------------

    def add_window(self, window: Window, *, track_undo: bool = False) -> None:
        """Add a window to the top of the stack.

        If *track_undo* is True and undo is enabled, the add is
        routed through the command system so it can be undone.
        """
        if track_undo and self._undo_manager is not None:
            self._undo_manager.push(
                AddWindowCommand(self, window,
                                description=f"Add '{window.title or window.id}'"))
            self._notifications.info(
                "Window opened", f"'{window.title or window.id}' opened")
            return
        self._windows.append(window)
        self._focus_window(window.id)
        self._notifications.info(
            "Window opened", f"'{window.title or window.id}' opened")
        self._log(f"Window '{window.title or window.id}' added")

    def remove_window(self, window_id: str, *, track_undo: bool = False) -> bool:
        """Remove a window from the session."""
        if track_undo and self._undo_manager is not None:
            self._undo_manager.push(
                RemoveWindowCommand(self, window_id,
                                   description=f"Remove '{window_id}'"))
            return True
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

    def close_window(self, window_id: str, *, track_undo: bool = False) -> bool:
        """Close a window — fires WINDOW_CLOSE event, shows a toast,
        and removes it."""
        win = self._find_window(window_id)
        title = win.title if win else window_id
        self._notifications.info("Window closed", f"'{title}' was closed")
        event = InputEvent(type=EventType.WINDOW_CLOSE, window_id=window_id)
        self._dispatch_event(event)
        return self.remove_window(window_id, track_undo=track_undo)

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
        elif event.ctrl and event.super_key and event.key == "Left":
            self.cycle_workspace(-1)
            return inp
        elif event.ctrl and event.super_key and event.key == "Right":
            self.cycle_workspace(1)
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

        When a Wayland display is connected, this polls the display fd,
        dispatches Wayland events (configure, close, input), renders
        frames to the Wayland surface, and ticks the animation timeline.

        On the floor (no Wayland), it simply ticks.
        """
        self._running = True
        frame_time = 1.0 / fps
        frames = int(duration * fps)
        start = time.monotonic()

        for frame_idx in range(frames):
            if not self._running:
                break

            # --- Wayland event dispatch + render cycle ---
            if self.has_wayland:
                # Poll Wayland display for events (configure, close, input)
                self._wayland_display.poll_and_dispatch(timeout_s=frame_time * 0.5)

                # Check for output changes (hot-plug events)
                if self._wayland_display.check_output_changes():
                    self._sync_wayland_outputs()

                # Render the current state and submit to Wayland
                try:
                    img = self.live_render()
                    self._wayland_display.render_frame(img)
                except Exception as e:
                    self._log(f"Wayland render frame {frame_idx}: {e}")

            # --- Animation tick ---
            self.tick(frame_time * 1000)  # tick takes milliseconds

            # --- Timing ---
            elapsed = time.monotonic() - start
            target = (frame_idx + 1) * frame_time
            if elapsed < target:
                time.sleep(target - elapsed)

        self._running = False

    def stop(self) -> None:
        """Stop the event loop and clean up the Wayland display."""
        self._running = False
        if self._wayland_display is not None:
            self._wayland_display.close()
            self._wayland_display = None

    # -- Wayland display (ADR-0026) ----------------------------------

    def connect_wayland(self, display_name: Optional[str] = None) -> bool:
        """Connect to a Wayland display server.

        If a compositor is available, creates a WaylandDisplay and
        connects.  Returns True on success, False on failure (caller
        should fall back to PIL rendering).

        Parameters
        ----------
        display_name : str, optional
            Wayland display name (e.g. ``"wayland-0"``).  If None,
            uses the ``WAYLAND_DISPLAY`` environment variable.
        """
        try:
            from .wayland_display import WaylandDisplay
            self._wayland_display = WaylandDisplay(display_name)
            if self._wayland_display.open():
                self._log("Wayland display connected")
                self._setup_wayland_events()
                self._sync_wayland_outputs()
                return True
            else:
                self._wayland_display = None
                return False
        except Exception as e:
            self._log(f"Wayland connection failed: {e}")
            self._wayland_display = None
            return False

    @property
    def wayland_display(self) -> Optional[Any]:
        """The Wayland display, or None if not connected."""
        return self._wayland_display

    @property
    def has_wayland(self) -> bool:
        """Whether the session is connected to a Wayland display."""
        return self._wayland_display is not None and self._wayland_display.connected

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

    def render_to_wayland(self) -> bool:
        """Render the session to the Wayland display.

        Renders with live window positions and submits the pixel
        buffer to the Wayland surface.  Returns True on success,
        False if Wayland is unavailable or rendering failed.
        """
        if not self.has_wayland:
            return False

        try:
            img = self.live_render()
            return self._wayland_display.render_frame(img)
        except Exception as e:
            self._log(f"Wayland render failed: {e}")
            return False

    def _setup_wayland_events(self) -> None:
        """Wire Wayland compositor events to the DesktopSession.

        Registers callbacks on the WaylandDisplay so that compositor
        events (configure, close, keyboard, pointer) are translated
        into DesktopSession actions (resize, close, input).
        """
        if not self.has_wayland:
            return

        from .wayland_display import (
            WaylandConfigureEvent, WaylandCloseEvent,
            WaylandKeyEvent, WaylandPointerEvent,
        )

        def on_configure(event: WaylandConfigureEvent) -> None:
            """Handle compositor-driven resize."""
            if event.width <= 0 or event.height <= 0:
                return  # maximized or minimized — ignore size
            # Find the window for this surface and resize it
            for win in self._windows:
                # In the current design, the primary window maps to
                # the first Wayland surface
                if win.visible and not win.minimized:
                    win.width = event.width
                    win.height = event.height
                    self._log(
                        f"Compositor resize: '{win.title or win.id}' "
                        f"→ {event.width}x{event.height}")
                    break

        def on_close(event: WaylandCloseEvent) -> None:
            """Handle compositor close request."""
            # Close the focused window
            if self._focused_window_id:
                self.close_window(self._focused_window_id)
                self._log("Compositor close: window closed")

        def on_key(event: WaylandKeyEvent) -> None:
            """Handle compositor keyboard event."""
            # Translate Wayland key event to DesktopSession KeyEvent
            # Key state: 0 = pressed, 1 = released (Wayland convention)
            if event.state == 0:  # pressed
                key_event = KeyEvent(key=str(event.key))
                self.process_key_event(key_event)

        def on_pointer(event: WaylandPointerEvent) -> None:
            """Handle compositor pointer event."""
            # Translate Wayland pointer event to DesktopSession MouseEvent
            from .desktop_session import MouseButton
            button = MouseButton.LEFT if event.button == 0x110 else MouseButton.NONE
            mouse_event = MouseEvent(
                x=int(event.x),
                y=int(event.y),
                button=button,
            )
            self.process_mouse_event(mouse_event)

        self._wayland_display.on_configure(on_configure)
        self._wayland_display.on_close(on_close)
        self._wayland_display.on_key(on_key)
        self._wayland_display.on_pointer(on_pointer)
        self._log("Wayland event callbacks registered")

    def _sync_wayland_outputs(self) -> None:
        """Synchronize Wayland outputs with DesktopSession monitors.

        Queries the Wayland display for active outputs and maps them
        to Monitor objects.  If no outputs are reported, falls back
        to the document's screen definitions.
        """
        if not self.has_wayland:
            return

        outputs = self._wayland_display.outputs
        if not outputs:
            self._log("No Wayland outputs reported — using document screens")
            return

        # Clear existing monitors and rebuild from Wayland outputs
        self._monitors.clear()
        for i, out in enumerate(outputs):
            m = Monitor(
                id=f"monitor-{out['id']}",
                x=out['x'],
                y=out['y'],
                width=out['width'],
                height=out['height'],
                scale=float(out['scale']),
                primary=out['primary'],
                name=f"Output {out['id']}",
            )
            self._monitors.append(m)
            self._log(
                f"Wayland output {out['id']}: "
                f"{out['width']}x{out['height']}@{out['scale']}x "
                f"at ({out['x']}, {out['y']})"
                f"{' [primary]' if out['primary'] else ''}")

        # Rebuild workspaces for the new monitor layout
        self._workspaces.clear()
        self._build_workspaces()

        self._log(f"Synced {len(self._monitors)} Wayland output(s) to monitors")

    # -- Notifications ------------------------------------------------

    @property
    def notifications(self):
        """The notification service for this session."""
        return self._notifications

    def summary(self) -> Dict[str, Any]:
        """Session summary for diagnostics."""
        aws = self.active_workspace
        result = {
            "windows": len(self._windows),
            "focused": self._focused_window_id,
            "visible": sum(1 for w in self._windows if w.visible and not w.minimized),
            "minimized": sum(1 for w in self._windows if w.minimized),
            "monitors": len(self._monitors),
            "workspaces": len(self._workspaces),
            "active_workspace": aws.name if aws else None,
            "active_notifications": self._notifications.count,
            "events_processed": len(self._event_log),
            "wayland": self.has_wayland,
            "wayland_outputs": len(self._wayland_display.outputs) if self.has_wayland else 0,
            **self._runtime.summary(),
        }
        return result

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

        Walks each screen's root children — only ``Window`` type
        components become session windows.  Shell chrome (Taskbar,
        StartMenu, etc.) is tracked separately in _shell_components.
        """
        _SHELL_TYPES = {
            "Taskbar", "StartMenu", "NotificationCenter",
            "QuickSettings", "CommandPalette", "LockScreen",
            "WorkspaceSwitcher",
        }
        for screen in self._doc.screens:
            root = screen.root

            for child in root.children:
                if child.type == "Window":
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
                elif child.type in _SHELL_TYPES:
                    self._shell_components[child.id] = child

        # Focus the topmost window
        if self._windows:
            self._focus_window(self._windows[-1].id)

    def _build_monitors(self) -> None:
        """Build the initial monitor list from the document's screens.

        Each NUI screen becomes a Monitor.  The first screen is primary.
        """
        for i, screen in enumerate(self._doc.screens):
            m = Monitor(
                id=f"monitor-{i}",
                width=screen.size.get("width", 1920),
                height=screen.size.get("height", 1080),
                primary=(i == 0),
                name=screen.id,
            )
            self._monitors.append(m)
        if not self._monitors:
            self._monitors.append(Monitor(id="monitor-0"))

    def _build_workspaces(self) -> None:
        """Create the initial workspaces (one per monitor, plus one
        additional workspace per monitor for overflow)."""
        ws_id = 0
        for monitor in self._monitors:
            for ws_num in range(1, 3):  # 2 workspaces per monitor
                ws = Workspace(
                    id=f"ws-{ws_id}",
                    name=f"Workspace {ws_num}",
                    monitor_id=monitor.id,
                )
                self._workspaces.append(ws)
                ws_id += 1
        # Assign all initial windows to workspace 0 on the primary monitor
        if self._workspaces and self._windows:
            self._workspaces[0].window_ids = [w.id for w in self._windows]
            self._active_workspace_id = self._workspaces[0].id

    # -- Workspace management -------------------------------------------

    def switch_workspace(self, workspace_id: str) -> bool:
        """Switch to a different workspace.

        Hides windows not belonging to the target workspace and shows
        windows that do.  Returns True on success.
        """
        target = None
        for ws in self._workspaces:
            if ws.id == workspace_id:
                target = ws
                break
        if target is None:
            return False
        old_ws = self.active_workspace
        self._active_workspace_id = workspace_id
        # Hide old workspace windows, show new
        for w in self._windows:
            if w.id in (target.window_ids if target else []):
                w.visible = True
            elif old_ws and w.id in old_ws.window_ids:
                w.visible = False
        self._log(f"Workspace → '{target.name}'")
        # Focus topmost visible window
        self._auto_focus_topmost()
        return True

    def cycle_workspace(self, direction: int = 1) -> None:
        """Cycle to the next (+1) or previous (-1) workspace."""
        if not self._workspaces:
            return
        idx = 0
        for i, ws in enumerate(self._workspaces):
            if ws.id == self._active_workspace_id:
                idx = i
                break
        new_idx = (idx + direction) % len(self._workspaces)
        self.switch_workspace(self._workspaces[new_idx].id)

    @property
    def active_workspace(self) -> Optional[Workspace]:
        if self._active_workspace_id is None:
            return None
        for ws in self._workspaces:
            if ws.id == self._active_workspace_id:
                return ws
        return None

    @property
    def monitors(self) -> List[Monitor]:
        return list(self._monitors)

    @property
    def workspaces(self) -> List[Workspace]:
        return list(self._workspaces)

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
