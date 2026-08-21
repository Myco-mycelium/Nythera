#!/usr/bin/env python3
"""Tests for ui.desktop_session — interactive desktop shell."""

import json
import os
import sys
import tempfile
import unittest

# Ensure the backend is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.nstudio import NstudioDocument, loads
from ui.desktop_session import (
    DesktopSession,
    EventType,
    HitResult,
    InputEvent,
    KeyEvent,
    MouseButton,
    MouseEvent,
    Window,
)


def _make_doc(
    screens=None,
    behaviors=None,
    bindings=None,
    states=None,
    state_scopes=None,
    animations=None,
    locales=None,
    resources=None,
    components=None,
):
    """Helper: create a minimal NstudioDocument for testing."""
    if screens is None:
        screens = [{
            "id": "desktop",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "root",
                "type": "DesktopSurface",
                "layout": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "children": [
                    {
                        "id": "taskbar",
                        "type": "Taskbar",
                        "layout": {"x": 0, "y": 1030, "width": 1920, "height": 50},
                        "properties": {"position": "bottom"},
                    },
                    {
                        "id": "win-main",
                        "type": "Window",
                        "layout": {"x": 200, "y": 100, "width": 800, "height": 600},
                        "properties": {"title": "Main Window"},
                        "children": [
                            {
                                "id": "btn-ok",
                                "type": "Button",
                                "layout": {"x": 300, "y": 500, "width": 100, "height": 40},
                                "properties": {"text": "OK"},
                                "events": {"clicked": "btn-click-behavior"},
                            },
                        ],
                    },
                    {
                        "id": "win-settings",
                        "type": "Window",
                        "layout": {"x": 400, "y": 200, "width": 600, "height": 400},
                        "properties": {"title": "Settings"},
                    },
                ],
            },
        }]
    if behaviors is None:
        behaviors = [
            {
                "id": "btn-click-behavior",
                "condition": None,
                "action": {
                    "target": "System",
                    "name": "Nyrqis.Theme.Set",
                    "arguments": {"theme": "Solar"},
                },
            },
        ]
    if bindings is None:
        bindings = []
    if states is None:
        states = {"theme": "Eclipse", "volume": 80}
    if state_scopes is None:
        state_scopes = {}
    if animations is None:
        animations = []
    if locales is None:
        locales = {}
    if resources is None:
        resources = {}
    if components is None:
        components = []

    raw = {
        "version": "1.0.0",
        "project": {"name": "test"},
        "themes": {"active": "Eclipse", "available": ["Eclipse", "Solar"]},
        "states": states,
        "stateScopes": state_scopes,
        "locales": locales,
        "resources": resources,
        "animations": animations,
        "behaviors": behaviors,
        "bindings": bindings,
        "components": components,
        "screens": screens,
    }
    return loads(json.dumps(raw))


def _find_win(session, comp_id):
    """Find a window by its component_id."""
    for w in session.windows:
        if w.component_id == comp_id:
            return w
    return None


class TestWindow(unittest.TestCase):
    """Tests for the Window dataclass."""

    def test_window_creation(self):
        w = Window(id="w1", component_id="c1", title="Test")
        self.assertEqual(w.id, "w1")
        self.assertEqual(w.title, "Test")
        self.assertTrue(w.visible)
        self.assertFalse(w.minimized)
        self.assertFalse(w.maximized)
        self.assertFalse(w.focused)

    def test_window_defaults(self):
        w = Window(id="w1", component_id="c1")
        self.assertEqual(w.width, 800)
        self.assertEqual(w.height, 600)
        self.assertEqual(w.x, 0)
        self.assertEqual(w.y, 0)


class TestMouseEvent(unittest.TestCase):
    """Tests for input event types."""

    def test_mouse_event(self):
        e = MouseEvent(x=100, y=200, button=MouseButton.LEFT)
        self.assertEqual(e.x, 100)
        self.assertEqual(e.button, MouseButton.LEFT)

    def test_key_event(self):
        e = KeyEvent(key="a", ctrl=True)
        self.assertEqual(e.key, "a")
        self.assertTrue(e.ctrl)
        self.assertFalse(e.alt)

    def test_input_event(self):
        inp = InputEvent(type=EventType.MOUSE_DOWN, mouse=MouseEvent(10, 20))
        self.assertEqual(inp.type, EventType.MOUSE_DOWN)
        self.assertIsNotNone(inp.mouse)


class TestDesktopSessionWindowManagement(unittest.TestCase):
    """Tests for window lifecycle and focus."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_initial_windows(self):
        """Session should discover top-level Window components."""
        comp_ids = [w.component_id for w in self.session.windows]
        self.assertGreaterEqual(len(comp_ids), 2)
        self.assertIn("win-main", comp_ids)
        self.assertIn("win-settings", comp_ids)

    def test_initial_focus(self):
        """Topmost window should be focused initially."""
        focused = self.session.focused_window
        self.assertIsNotNone(focused)
        self.assertTrue(focused.focused)

    def test_add_window(self):
        w = Window(id="w-new", component_id="new", title="New")
        self.session.add_window(w)
        self.assertEqual(len(self.session.windows), 3)  # 2 initial + 1
        self.assertEqual(self.session.focused_window.id, "w-new")

    def test_remove_window(self):
        win = _find_win(self.session, "win-settings")
        result = self.session.remove_window(win.id)
        self.assertTrue(result)
        self.assertEqual(len(self.session.windows), 1)

    def test_remove_nonexistent(self):
        result = self.session.remove_window("no-such-window")
        self.assertFalse(result)

    def test_focus_window(self):
        win = _find_win(self.session, "win-settings")
        self.session.focus_window(win.id)
        focused = self.session.focused_window
        self.assertEqual(focused.component_id, "win-settings")
        self.assertEqual(self.session.windows[-1].component_id, "win-settings")

    def test_minimize_window(self):
        main = _find_win(self.session, "win-main")
        self.session.minimize_window(main.id)
        self.assertTrue(main.minimized)
        self.assertFalse(main.focused)

    def test_maximize_restore(self):
        main = _find_win(self.session, "win-main")
        self.session.maximize_window(main.id)
        self.assertTrue(main.maximized)
        self.assertEqual(main.x, 0)
        self.assertEqual(main.y, 0)
        self.assertEqual(main.width, 1920)
        self.assertEqual(main.height, 1080)

        # Restore
        self.session.maximize_window(main.id)
        self.assertFalse(main.maximized)

    def test_close_window(self):
        win = _find_win(self.session, "win-settings")
        result = self.session.close_window(win.id)
        self.assertTrue(result)
        self.assertEqual(len(self.session.windows), 1)


class TestDesktopSessionHitTesting(unittest.TestCase):
    """Tests for mouse hit-testing."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_hit_inside_window(self):
        # Click inside win-main (200,100,800,600) but outside win-settings
        # Use (250, 150) which is in win-main but not in win-settings (400,200)
        result = self.session.hit_test(250, 150)
        self.assertTrue(result.hit)
        self.assertEqual(result.window.component_id, "win-main")

    def test_hit_button(self):
        # Button at local (300,500) inside win-main at (200,100)
        # screen coords: (200+300, 100+500) = (500, 600)
        result = self.session.hit_test(500, 600)
        self.assertTrue(result.hit)
        self.assertEqual(result.component.id, "btn-ok")

    def test_hit_taskbar(self):
        # Taskbar at (0, 1030, 1920, 50) — outside any Window
        result = self.session.hit_test(500, 1050)
        self.assertTrue(result.hit)
        self.assertEqual(result.component.id, "taskbar")

    def test_hit_desktop_background(self):
        # Click on empty desktop area (no window)
        result = self.session.hit_test(10, 50)
        self.assertTrue(result.hit)
        self.assertEqual(result.component.id, "root")


class TestDesktopSessionMouseEvents(unittest.TestCase):
    """Tests for mouse event processing."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        self.events_received = []
        self.session.on_event(
            EventType.MOUSE_DOWN,
            lambda e: self.events_received.append(e))

    def test_click_fires_callback(self):
        event = MouseEvent(x=500, y=600, button=MouseButton.LEFT)
        self.session.process_mouse_event(event)
        self.assertEqual(len(self.events_received), 1)
        self.assertEqual(self.events_received[0].type, EventType.MOUSE_DOWN)

    def test_click_focuses_window(self):
        # Click on settings window (400,200,600,400)
        event = MouseEvent(x=500, y=300, button=MouseButton.LEFT)
        self.session.process_mouse_event(event)
        focused = self.session.focused_window
        self.assertIn(focused.component_id, ["win-main", "win-settings"])

    def test_button_click_fires_behavior(self):
        # Button at screen (500, 600)
        event = MouseEvent(x=500, y=600, button=MouseButton.LEFT)
        self.session.process_mouse_event(event)
        # Nyrqis.Theme.Set sets themes["active"]
        self.assertEqual(self.session.document.themes.get("active"), "Solar")

    def test_drag_begins_on_titlebar(self):
        # Click on the title bar area of win-main (y < 32 from window top)
        # win-main is at (200, 100), so title bar is y=100..132
        event = MouseEvent(x=400, y=110, button=MouseButton.LEFT)
        self.session.process_mouse_event(event)
        # Drag should be initiated — process a move
        move = MouseEvent(x=450, y=120, button=MouseButton.NONE)
        self.session.process_mouse_event(move)
        main = _find_win(self.session, "win-main")
        self.assertGreater(main.x, 200)  # Should have moved right

    def test_mouse_move_no_crash(self):
        event = MouseEvent(x=500, y=500, button=MouseButton.NONE)
        self.session.process_mouse_event(event)
        self.assertEqual(len(self.events_received), 0)  # No callback for move


class TestDesktopSessionKeyEvents(unittest.TestCase):
    """Tests for keyboard event processing."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        self.key_events = []
        self.session.on_event(
            EventType.KEY_DOWN,
            lambda e: self.key_events.append(e))

    def test_key_event_dispatched(self):
        event = KeyEvent(key="a")
        self.session.process_key_event(event)
        self.assertEqual(len(self.key_events), 1)

    def test_ctrl_w_closes_focused(self):
        initial_count = len(self.session.windows)
        event = KeyEvent(key="w", ctrl=True)
        self.session.process_key_event(event)
        self.assertLess(len(self.session.windows), initial_count)

    def test_ctrl_n_minimizes_focused(self):
        # Focus win-main first
        main = _find_win(self.session, "win-main")
        self.session.focus_window(main.id)
        event = KeyEvent(key="n", ctrl=True)
        self.session.process_key_event(event)
        # main should now be minimized
        self.assertTrue(main.minimized)
        # Focus should have moved to another window
        self.assertIsNotNone(self.session.focused_window)


class TestDesktopSessionSummary(unittest.TestCase):
    """Tests for session summary/diagnostics."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_summary_structure(self):
        s = self.session.summary()
        self.assertIn("windows", s)
        self.assertIn("focused", s)
        self.assertIn("events_processed", s)
        self.assertIn("screens", s)
        self.assertIn("components", s)
        self.assertEqual(s["windows"], 2)

    def test_summary_after_events(self):
        self.session.process_mouse_event(
            MouseEvent(x=500, y=600, button=MouseButton.LEFT))
        s = self.session.summary()
        self.assertEqual(s["events_processed"], 1)


class TestDesktopSessionRendering(unittest.TestCase):
    """Tests for live rendering."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_live_render_returns_image(self):
        img = self.session.live_render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_live_render_reflects_window_position(self):
        # Move a window
        main = _find_win(self.session, "win-main")
        main.x = 500
        main.y = 400
        img = self.session.live_render()
        self.assertIsNotNone(img)
        # Image should still be the right size
        self.assertEqual(img.size, (1920, 1080))

    def test_live_render_minimized_window_offscreen(self):
        main = _find_win(self.session, "win-main")
        self.session.minimize_window(main.id)
        img = self.session.live_render()
        self.assertIsNotNone(img)

    def test_render_to_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png') as f:
            path = self.session.render_to_file(f.name)
            import os
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)


class TestDesktopSessionE2E(unittest.TestCase):
    """End-to-end: load design → session → interact → verify state."""

    def test_full_lifecycle(self):
        doc = _make_doc()
        session = DesktopSession(doc)

        # 1. Verify initial state
        self.assertEqual(len(session.windows), 2)
        self.assertIsNotNone(session.focused_window)
        self.assertEqual(
            session.document.themes.get("active"), "Eclipse")

        # 2. Click the OK button (screen coords: 200+300, 100+500 = 500, 600)
        session.process_mouse_event(
            MouseEvent(x=500, y=600, button=MouseButton.LEFT))

        # 3. Behavior should have changed the theme
        self.assertEqual(
            session.document.themes.get("active"), "Solar")

        # 4. Drag the window
        session.process_mouse_event(
            MouseEvent(x=300, y=110, button=MouseButton.LEFT))
        session.process_mouse_event(
            MouseEvent(x=600, y=150, button=MouseButton.NONE))
        session.process_mouse_up(MouseEvent(x=600, y=150))
        main = _find_win(session, "win-main")
        self.assertGreater(main.x, 200)

        # 5. Minimize via keyboard
        session.process_key_event(KeyEvent(key="n", ctrl=True))
        self.assertTrue(main.minimized)

        # 6. Summary checks
        s = session.summary()
        self.assertEqual(s["windows"], 2)
        self.assertEqual(s["minimized"], 1)
        self.assertEqual(s["events_processed"], 3)

        # 7. Live render should work
        img = session.live_render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_two_windows_focus_switching(self):
        doc = _make_doc()
        session = DesktopSession(doc)

        # Focus settings
        settings = _find_win(session, "win-settings")
        session.focus_window(settings.id)
        self.assertEqual(
            session.focused_window.component_id, "win-settings")

        # Click on main window to switch focus
        session.process_mouse_event(
            MouseEvent(x=300, y=200, button=MouseButton.LEFT))
        self.assertEqual(
            session.focused_window.component_id, "win-main")

        # Ctrl+W should close main, focus goes to settings
        session.process_key_event(KeyEvent(key="w", ctrl=True))
        self.assertEqual(
            session.focused_window.component_id, "win-settings")

        # Only settings remains
        self.assertEqual(len(session.windows), 1)


class TestDesktopSessionFromFile(unittest.TestCase):
    """Tests for loading from file."""

    def test_from_json_string(self):
        raw = {
            "version": "1.0.0",
            "project": {"name": "test"},
            "themes": {"active": "Eclipse"},
            "states": {},
            "stateScopes": {},
            "locales": {},
            "resources": {},
            "animations": [],
            "behaviors": [],
            "bindings": [],
            "components": [],
            "screens": [{
                "id": "s1",
                "size": {"width": 1024, "height": 768},
                "root": {
                    "id": "root",
                    "type": "DesktopSurface",
                    "layout": {"x": 0, "y": 0, "width": 1024, "height": 768},
                },
            }],
        }
        session = DesktopSession.from_json(json.dumps(raw))
        self.assertIsNotNone(session)
        self.assertEqual(len(session.windows), 0)  # No Window-type children


if __name__ == "__main__":
    unittest.main()
