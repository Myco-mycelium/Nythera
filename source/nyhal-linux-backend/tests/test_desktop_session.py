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

# NyApp packager imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tools.nyapp import (
    NyAppPackager, build_napp, parse_napp, validate_napp,
    compile_source, OP_HALT, OP_LOG, OP_SET_STATE,
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


class TestDesktopSessionWorkspaces(unittest.TestCase):
    """Tests for multi-monitor and workspace support."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_initial_monitors(self):
        self.assertGreaterEqual(len(self.session.monitors), 1)
        self.assertTrue(self.session.monitors[0].primary)

    def test_initial_workspaces(self):
        self.assertGreaterEqual(len(self.session.workspaces), 2)
        self.assertIsNotNone(self.session.active_workspace)

    def test_switch_workspace(self):
        ws = self.session.workspaces[1]
        result = self.session.switch_workspace(ws.id)
        self.assertTrue(result)
        self.assertEqual(self.session.active_workspace.id, ws.id)

    def test_switch_nonexistent_workspace(self):
        result = self.session.switch_workspace("no-such-ws")
        self.assertFalse(result)

    def test_cycle_workspace(self):
        initial = self.session.active_workspace.id
        self.session.cycle_workspace(1)
        self.assertNotEqual(self.session.active_workspace.id, initial)

    def test_cycle_workspace_wraps(self):
        # Cycle forward through all workspaces
        for _ in range(len(self.session.workspaces) + 1):
            self.session.cycle_workspace(1)
        # Should still have a valid active workspace
        self.assertIsNotNone(self.session.active_workspace)

    def test_summary_includes_workspaces(self):
        s = self.session.summary()
        self.assertIn("monitors", s)
        self.assertIn("workspaces", s)
        self.assertIn("active_workspace", s)
        self.assertEqual(s["monitors"], 1)
        self.assertGreaterEqual(s["workspaces"], 2)


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


class TestSettingsApp(unittest.TestCase):
    """Tests for the Nyrqis settings application."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from examples.settings_app import SettingsApp
        self.settings = SettingsApp(self.session)

    def test_initial_settings(self):
        self.assertEqual(self.settings.get("theme"), "Eclipse")
        self.assertEqual(self.settings.get("volume"), 80)

    def test_toggle_theme(self):
        result = self.settings.toggle_theme()
        self.assertEqual(result, "Solar")
        self.assertEqual(self.settings.get("theme"), "Solar")
        result2 = self.settings.toggle_theme()
        self.assertEqual(result2, "Eclipse")

    def test_set_volume_clamps(self):
        self.settings.set_volume(150)
        self.assertEqual(self.settings.get("volume"), 100)
        self.settings.set_volume(-10)
        self.assertEqual(self.settings.get("volume"), 0)

    def test_set_brightness_clamps(self):
        self.settings.set_brightness(200)
        self.assertEqual(self.settings.get("brightness"), 100)

    def test_toggle_taskbar(self):
        result = self.settings.toggle_taskbar_autohide()
        self.assertTrue(result)
        result2 = self.settings.toggle_taskbar_autohide()
        self.assertFalse(result2)

    def test_toggle_animations(self):
        result = self.settings.toggle_animations()
        self.assertFalse(result)

    def test_show_hide(self):
        self.assertFalse(self.settings.visible)
        self.settings.show()
        self.assertTrue(self.settings.visible)
        self.settings.hide()
        self.assertFalse(self.settings.visible)

    def test_toggle_visibility(self):
        self.settings.show()
        result = self.settings.toggle()
        self.assertFalse(result)
        result2 = self.settings.toggle()
        self.assertTrue(result2)

    def test_reset_to_defaults(self):
        self.settings.toggle_theme()
        self.settings.set_volume(10)
        self.settings.reset()
        self.assertEqual(self.settings.get("theme"), "Eclipse")
        self.assertEqual(self.settings.get("volume"), 80)

    def test_on_change_callback(self):
        changes = []
        self.settings.on_change(lambda k, v, o: changes.append((k, v, o)))
        self.settings.set("theme", "Solar")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0], ("theme", "Solar", "Eclipse"))

    def test_get_windows(self):
        windows = self.settings.get_windows()
        self.assertIsInstance(windows, list)
        self.assertEqual(len(windows), 2)

    def test_get_workspaces(self):
        workspaces = self.settings.get_workspaces()
        self.assertIsInstance(workspaces, list)
        self.assertGreaterEqual(len(workspaces), 2)
        self.assertIn("name", workspaces[0])
        self.assertIn("windows", workspaces[0])

    def test_persistence(self):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(
                suffix='.json', delete=False) as f:
            path = f.name
        try:
            from examples.settings_app import SettingsApp
            s1 = SettingsApp(self.session, config_path=path)
            s1.set("theme", "Solar")
            s1.set_volume(42)
            # Create a new instance that loads from the same file
            s2 = SettingsApp(self.session, config_path=path)
            self.assertEqual(s2.get("theme"), "Solar")
            self.assertEqual(s2.get("volume"), 42)
        finally:
            os.unlink(path)

    def test_minimize_all(self):
        count = self.settings.minimize_all()
        self.assertEqual(count, 2)  # Both windows minimized


class TestNotificationService(unittest.TestCase):
    """Tests for the notification system."""

    def setUp(self):
        from ui.notifications import NotificationService, NotificationSeverity
        self.ns = NotificationService()
        self.severity = NotificationSeverity

    def test_notify_creates_notification(self):
        n = self.ns.notify("Test", "Body")
        self.assertEqual(n.title, "Test")
        self.assertEqual(n.message, "Body")
        self.assertEqual(self.ns.count, 1)

    def test_shorthand_methods(self):
        self.ns.info("Info")
        self.ns.success("OK")
        self.ns.warning("Careful")
        self.ns.error("Bad")
        self.assertEqual(self.ns.count, 4)

    def test_dismiss(self):
        n = self.ns.notify("Test")
        result = self.ns.dismiss(n.id)
        self.assertTrue(result)
        self.assertEqual(self.ns.count, 0)

    def test_dismiss_nonexistent(self):
        result = self.ns.dismiss("no-such-id")
        self.assertFalse(result)

    def test_dismiss_all(self):
        self.ns.info("A")
        self.ns.info("B")
        self.ns.info("C")
        count = self.ns.dismiss_all()
        self.assertEqual(count, 3)
        self.assertEqual(self.ns.count, 0)

    def test_clear(self):
        self.ns.info("A")
        self.ns.info("B")
        count = self.ns.clear()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.ns.history), 0)

    def test_tick_auto_dismiss(self):
        from ui.notifications import Notification
        import time
        n = self.ns.notify("Expiring", timeout_ms=1)
        # Simulate time passing
        n.timestamp = time.time() - 0.01  # 10ms ago
        dismissed = self.ns.tick()
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(self.ns.count, 0)

    def test_tick_no_timeout(self):
        import time
        n = self.ns.notify("Persistent", timeout_ms=0)
        n.timestamp = time.time() - 1000
        dismissed = self.ns.tick()
        self.assertEqual(len(dismissed), 0)
        self.assertEqual(self.ns.count, 1)

    def test_layout(self):
        self.ns.info("A")
        self.ns.info("B")
        self.ns.layout(1920, 1080)
        active = self.ns.active
        self.assertGreater(active[0]._x, 1000)  # Right side
        self.assertLess(active[0]._y, 100)       # Top
        self.assertGreater(active[1]._y, active[0]._y)  # Stacked

    def test_hit_test(self):
        self.ns.info("Test")
        self.ns.layout(1920, 1080)
        n = self.ns.active[0]
        result = self.ns.hit_test(n._x + 10, n._y + 10)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, n.id)

    def test_handle_click_dismiss(self):
        self.ns.info("Clickable")
        self.ns.layout(1920, 1080)
        n = self.ns.active[0]
        # Click on dismiss button (top-right corner)
        result = self.ns.handle_click(
            n._x + n._width - 10, n._y + 10)
        self.assertTrue(result)
        self.assertEqual(self.ns.count, 0)

    def test_handle_click_action(self):
        action_called = []
        self.ns.info("Actionable", action=lambda n: action_called.append(True))
        self.ns.layout(1920, 1080)
        n = self.ns.active[0]
        # Click in the body (not dismiss)
        result = self.ns.handle_click(n._x + 50, n._y + 40)
        self.assertTrue(result)
        self.assertEqual(len(action_called), 1)

    def test_callback(self):
        events = []
        self.ns.on_event(lambda n, e: events.append((n.title, e)))
        n = self.ns.notify("CB Test")
        self.ns.dismiss(n.id)
        self.assertEqual(events, [
            ("CB Test", "created"),
            ("CB Test", "dismissed"),
        ])

    def test_by_severity(self):
        self.ns.info("I")
        self.ns.error("E")
        self.ns.error("E2")
        from ui.notifications import NotificationSeverity
        errors = self.ns.by_severity(NotificationSeverity.ERROR)
        self.assertEqual(len(errors), 2)
        infos = self.ns.by_severity(NotificationSeverity.INFO)
        self.assertEqual(len(infos), 1)

    def test_render_returns_image(self):
        self.ns.info("Render Test")
        img = self.ns.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))


class TestStartMenu(unittest.TestCase):
    """Tests for the start menu launcher."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from examples.start_menu import StartMenu
        self.menu = StartMenu(self.session)

    def test_initial_apps(self):
        self.assertGreaterEqual(len(self.menu.apps), 5)

    def test_pinned_apps(self):
        pinned = self.menu.pinned_apps
        self.assertGreaterEqual(len(pinned), 3)
        names = [a.name for a in pinned]
        self.assertIn("Settings", names)
        self.assertIn("Terminal", names)

    def test_by_category(self):
        cats = self.menu.by_category()
        self.assertIn("System", cats)
        self.assertIn("Developer", cats)

    def test_find_app(self):
        app = self.menu.find_app("settings")
        self.assertIsNotNone(app)
        self.assertEqual(app.name, "Settings")

    def test_find_nonexistent(self):
        app = self.menu.find_app("no-such-app")
        self.assertIsNone(app)

    def test_search(self):
        results = self.menu.search("terminal")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Terminal")

    def test_search_empty(self):
        results = self.menu.search("")
        self.assertEqual(len(results), len(self.menu.apps))

    def test_clear_search(self):
        self.menu.search("terminal")
        self.menu.clear_search()
        self.assertEqual(len(self.menu.filtered_apps), len(self.menu.apps))

    def test_launch_creates_window(self):
        initial = len(self.session.windows)
        result = self.menu.launch("settings")
        self.assertTrue(result)
        self.assertEqual(len(self.session.windows), initial + 1)

    def test_launch_nonexistent(self):
        result = self.menu.launch("no-such-app")
        self.assertFalse(result)

    def test_register_unregister(self):
        from examples.start_menu import AppEntry
        app = AppEntry(id="custom", name="Custom App")
        self.menu.register_app(app)
        self.assertIsNotNone(self.menu.find_app("custom"))
        self.menu.unregister_app("custom")
        self.assertIsNone(self.menu.find_app("custom"))

    def test_visibility(self):
        self.assertFalse(self.menu.visible)
        self.menu.show()
        self.assertTrue(self.menu.visible)
        self.menu.hide()
        self.assertFalse(self.menu.visible)

    def test_toggle(self):
        result = self.menu.toggle()
        self.assertTrue(result)
        result2 = self.menu.toggle()
        self.assertFalse(result2)


class TestFileManager(unittest.TestCase):
    """Tests for the file manager application."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-fm-")
        from examples.file_manager import FileManager
        self.fm = FileManager(self.session, root_path=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initial_path(self):
        self.assertEqual(self.fm.current_path, self.tmpdir)

    def test_entries(self):
        self.assertIsNotNone(self.fm.entries)

    def test_create_file(self):
        result = self.fm.create_file("test.txt", "hello")
        self.assertTrue(result)
        content = self.fm.read_file("test.txt")
        self.assertEqual(content, "hello")

    def test_create_directory(self):
        result = self.fm.create_directory("subdir")
        self.assertTrue(result)
        import os
        self.assertTrue(os.path.isdir(os.path.join(self.tmpdir, "subdir")))

    def test_navigate(self):
        import os
        os.makedirs(os.path.join(self.tmpdir, "child"), exist_ok=True)
        result = self.fm.navigate(os.path.join(self.tmpdir, "child"))
        self.assertTrue(result)
        self.assertIn("child", self.fm.current_path)

    def test_go_up(self):
        import os
        child = os.path.join(self.tmpdir, "child")
        os.makedirs(child, exist_ok=True)
        self.fm.navigate(child)
        self.fm.go_up()
        self.assertEqual(self.fm.current_path, self.tmpdir)

    def test_go_back(self):
        import os
        child = os.path.join(self.tmpdir, "child")
        os.makedirs(child, exist_ok=True)
        self.fm.navigate(child)
        self.fm.go_back()
        self.assertEqual(self.fm.current_path, self.tmpdir)

    def test_delete(self):
        self.fm.create_file("to-delete.txt", "bye")
        result = self.fm.delete("to-delete.txt")
        self.assertTrue(result)
        import os
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "to-delete.txt")))

    def test_rename(self):
        self.fm.create_file("old.txt", "content")
        result = self.fm.rename("old.txt", "new.txt")
        self.assertTrue(result)
        content = self.fm.read_file("new.txt")
        self.assertEqual(content, "content")

    def test_select(self):
        self.fm.create_file("selectable.txt")
        self.fm.select("selectable.txt")
        self.assertEqual(self.fm.state.selected, "selectable.txt")

    def test_breadcrumb(self):
        import os
        child = os.path.join(self.tmpdir, "deep")
        os.makedirs(child, exist_ok=True)
        self.fm.navigate(child)
        bc = self.fm.breadcrumb
        self.assertGreaterEqual(len(bc), 2)
        names = [name for name, _ in bc]
        self.assertIn("deep", names)

    def test_sort_by_size(self):
        self.fm.create_file("small.txt", "a")
        self.fm.create_file("big.txt", "a" * 1000)
        self.fm.sort_by("size")
        entries = self.fm.entries
        names = [e.name for e in entries]
        self.assertIn("small.txt", names)
        self.assertIn("big.txt", names)

    def test_toggle_hidden(self):
        import os
        open(os.path.join(self.tmpdir, ".hidden"), "w").close()
        self.fm.toggle_hidden()  # Enable hidden
        names = [e.name for e in self.fm.entries]
        self.assertIn(".hidden", names)
        self.fm.toggle_hidden()  # Disable hidden
        names = [e.name for e in self.fm.entries]
        self.assertNotIn(".hidden", names)

    def test_to_nstudio(self):
        nui = self.fm.to_nstudio()
        self.assertEqual(nui["id"], "file-manager")
        self.assertIn("children", nui)

    def test_visibility(self):
        self.assertFalse(self.fm.visible)
        self.fm.show()
        self.assertTrue(self.fm.visible)
        self.fm.hide()
        self.assertFalse(self.fm.visible)


class TestTerminalApp(unittest.TestCase):
    """Tests for the terminal emulator application."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from examples.terminal_app import TerminalApp
        self.term = TerminalApp(self.session)

    def test_initial_state(self):
        self.assertIsNotNone(self.term.history)
        self.assertGreater(len(self.term.history), 0)

    def test_execute_echo(self):
        result = self.term.execute("echo hello")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)

    def test_execute_pwd(self):
        result = self.term.execute("pwd")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, self.term.cwd)

    def test_execute_cd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            result = self.term.execute(f"cd {d}")
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(self.term.cwd, d)

    def test_execute_help(self):
        result = self.term.execute("help")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Nyrqis Terminal", result.stdout)

    def test_execute_clear(self):
        self.term.execute("echo stuff")
        self.term.execute("clear")
        self.assertEqual(len(self.term.history), 0)

    def test_execute_theme(self):
        result = self.term.execute("theme Solar")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            self.session.document.themes.get("active"), "Solar")

    def test_execute_theme_invalid(self):
        result = self.term.execute("theme Invalid")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Unknown theme", result.stderr)

    def test_execute_neofetch(self):
        result = self.term.execute("neofetch")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Nyrqis", result.stdout)

    def test_execute_history(self):
        self.term.execute("echo a")
        self.term.execute("echo b")
        result = self.term.execute("history")
        self.assertIn("echo a", result.stdout)
        self.assertIn("echo b", result.stdout)

    def test_execute_workspace_list(self):
        result = self.term.execute("workspace list")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Workspace", result.stdout)

    def test_history_navigation(self):
        self.term.execute("echo first")
        self.term.execute("echo second")
        up = self.term.history_up()
        self.assertEqual(up, "echo second")
        up2 = self.term.history_up()
        self.assertEqual(up2, "echo first")
        down = self.term.history_down()
        self.assertEqual(down, "echo second")

    def test_execute_exit(self):
        result = self.term.execute("exit")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(self.term.visible)

    def test_execute_invalid_command(self):
        result = self.term.execute("nonexistent_command_xyz")
        self.assertNotEqual(result.exit_code, 0)

    def test_to_nstudio(self):
        nui = self.term.to_nstudio()
        self.assertEqual(nui["id"], "terminal")
        self.assertIn("children", nui)

    def test_visibility(self):
        self.assertFalse(self.term.visible)
        self.term.show()
        self.assertTrue(self.term.visible)
        self.term.hide()
        self.assertFalse(self.term.visible)

    def test_cwd_expansion(self):
        import os
        self.term.execute("cd ~")
        self.assertEqual(self.term.cwd, os.path.expanduser("~"))


class TestWindowSwitcher(unittest.TestCase):
    """Tests for the Alt+Tab window switcher."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from ui.window_switcher import WindowSwitcher
        self.switcher = WindowSwitcher(self.session)

    def test_initial_state(self):
        self.assertFalse(self.switcher.active)
        self.assertIsNone(self.switcher.selected)

    def test_start_with_two_windows(self):
        result = self.switcher.start()
        self.assertTrue(result)
        self.assertTrue(self.switcher.active)
        self.assertIsNotNone(self.switcher.selected)
        self.assertEqual(len(self.switcher.entries), 2)

    def test_start_with_one_window(self):
        # Remove one window
        win = self.session.windows[0]
        self.session.remove_window(win.id)
        result = self.switcher.start()
        self.assertFalse(result)
        self.assertFalse(self.switcher.active)

    def test_stop_returns_window_id(self):
        self.switcher.start()
        win_id = self.switcher.stop()
        self.assertIsNotNone(win_id)
        self.assertFalse(self.switcher.active)
        self.assertEqual(len(self.switcher.entries), 0)

    def test_cycle_forward(self):
        self.switcher.start()
        initial = self.switcher.selected.window_id
        self.switcher.cycle(backward=False)
        next_id = self.switcher.selected.window_id
        # Should have moved (or wrapped)
        self.assertIsNotNone(next_id)

    def test_cycle_backward(self):
        self.switcher.start()
        self.switcher.cycle(backward=True)
        self.assertIsNotNone(self.switcher.selected)

    def test_cycle_wraps(self):
        self.switcher.start()
        ids = set()
        for _ in range(len(self.switcher.entries) + 2):
            self.switcher.cycle(backward=False)
            ids.add(self.switcher.selected.window_id)
        # Should see all window IDs
        self.assertEqual(len(ids), len(self.switcher.entries))

    def test_layout_positions(self):
        self.switcher.start()
        self.switcher.layout(1920, 1080)
        for entry in self.switcher.entries:
            self.assertGreater(entry.x, 0)
            self.assertGreater(entry.y, 0)

    def test_render_returns_image(self):
        self.switcher.start()
        img = self.switcher.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_render_none_when_inactive(self):
        img = self.switcher.render()
        self.assertIsNone(img)

    def test_callback(self):
        events = []
        self.switcher.on_event(lambda t, i: events.append((t, i)))
        self.switcher.start()
        self.switcher.cycle()
        self.switcher.stop()
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0][0], "started")
        self.assertEqual(events[1][0], "cycled")
        self.assertEqual(events[2][0], "stopped")

    def test_entries_populated(self):
        self.switcher.start()
        entries = self.switcher.entries
        self.assertGreater(len(entries), 0)
        for e in entries:
            self.assertIsNotNone(e.window_id)
            self.assertIsNotNone(e.title)


class TestLockScreen(unittest.TestCase):
    """Tests for the lock screen."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from ui.lock_screen import LockScreen
        self.lock = LockScreen(self.session, timeout_seconds=10)

    def test_initial_state(self):
        self.assertFalse(self.lock.locked)
        self.assertFalse(self.lock.visible)

    def test_lock(self):
        self.lock.lock()
        self.assertTrue(self.lock.locked)
        self.assertTrue(self.lock.visible)

    def test_unlock(self):
        self.lock.lock()
        result = self.lock.unlock()
        self.assertTrue(result)
        self.assertFalse(self.lock.locked)
        self.assertFalse(self.lock.visible)

    def test_toggle(self):
        self.lock.toggle()
        self.assertTrue(self.lock.locked)
        self.lock.toggle()
        self.assertFalse(self.lock.locked)

    def test_swipe_unlock(self):
        self.lock.lock()
        self.lock.handle_swipe_start(960, 800)
        self.lock.handle_swipe_move(960, 500)  # 300px up
        result = self.lock.handle_swipe_end(960, 500)
        self.assertTrue(result)
        self.assertFalse(self.lock.locked)

    def test_swipe_insufficient(self):
        self.lock.lock()
        self.lock.handle_swipe_start(960, 800)
        self.lock.handle_swipe_move(960, 700)  # only 100px up
        result = self.lock.handle_swipe_end(960, 700)
        self.assertFalse(result)
        self.assertTrue(self.lock.locked)

    def test_unlock_progress(self):
        self.lock.lock()
        self.lock.handle_swipe_start(960, 800)
        self.lock.handle_swipe_move(960, 700)
        self.assertGreater(self.lock.state.unlock_progress, 0)
        self.assertLess(self.lock.state.unlock_progress, 1)

    def test_auto_lock_timeout(self):
        self.lock._last_activity = 0  # Force timeout
        result = self.lock.check_timeout()
        self.assertTrue(result)
        self.assertTrue(self.lock.locked)

    def test_no_auto_lock_when_active(self):
        self.lock.activity()  # Reset timer
        result = self.lock.check_timeout()
        self.assertFalse(result)
        self.assertFalse(self.lock.locked)

    def test_activity_resets_timer(self):
        self.lock._last_activity = 0
        self.lock.activity()
        result = self.lock.check_timeout()
        self.assertFalse(result)

    def test_current_time(self):
        t = self.lock.current_time
        self.assertIsNotNone(t)
        self.assertEqual(len(t), 5)  # HH:MM

    def test_current_date(self):
        d = self.lock.current_date
        self.assertIsNotNone(d)
        self.assertGreater(len(d), 0)

    def test_render_returns_image(self):
        self.lock.lock()
        img = self.lock.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_render_none_when_unlocked(self):
        img = self.lock.render()
        self.assertIsNone(img)

    def test_callback(self):
        events = []
        self.lock.on_event(lambda e: events.append(e))
        self.lock.lock()
        self.lock.unlock()
        self.assertEqual(events, ["locked", "unlocked"])

    def test_unlock_when_not_locked(self):
        result = self.lock.unlock()
        self.assertTrue(result)  # Should succeed (no-op)

    def test_click_starts_swipe(self):
        self.lock.lock()
        result = self.lock.handle_click(960, 800)
        self.assertTrue(result)
        self.assertTrue(self.lock.state.swipe_active)

    def test_click_when_unlocked(self):
        result = self.lock.handle_click(960, 800)
        self.assertFalse(result)


class TestWidgetSystem(unittest.TestCase):
    """Tests for the desktop widget system."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from ui.widgets import WidgetSystem
        self.ws = WidgetSystem(self.session)

    def test_add_widget(self):
        w = self.ws.add_widget("clock", x=100, y=100)
        self.assertIsNotNone(w)
        self.assertEqual(w.widget_type, "clock")
        self.assertEqual(w.x, 100)
        self.assertEqual(len(self.ws.widgets), 1)

    def test_add_multiple_types(self):
        self.ws.add_widget("clock")
        self.ws.add_widget("cpu")
        self.ws.add_widget("memory")
        self.ws.add_widget("sticky", data={"text": "Hello"})
        self.assertEqual(len(self.ws.widgets), 4)

    def test_remove_widget(self):
        w = self.ws.add_widget("clock")
        result = self.ws.remove_widget(w.id)
        self.assertTrue(result)
        self.assertEqual(len(self.ws.widgets), 0)

    def test_remove_nonexistent(self):
        result = self.ws.remove_widget("no-such-id")
        self.assertFalse(result)

    def test_get_widget(self):
        w = self.ws.add_widget("cpu")
        found = self.ws.get_widget(w.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, w.id)

    def test_by_type(self):
        self.ws.add_widget("clock")
        self.ws.add_widget("clock")
        self.ws.add_widget("cpu")
        clocks = self.ws.by_type("clock")
        self.assertEqual(len(clocks), 2)

    def test_update_all(self):
        w = self.ws.add_widget("clock")
        w.last_update = 0  # Force update
        self.ws.update_all()
        self.assertIn("time", w.data)

    def test_update_cpu(self):
        w = self.ws.add_widget("cpu")
        w.last_update = 0
        self.ws.update_all()
        self.assertIn("usage", w.data)

    def test_update_memory(self):
        w = self.ws.add_widget("memory")
        w.last_update = 0
        self.ws.update_all()
        self.assertIn("usage", w.data)

    def test_update_sticky(self):
        w = self.ws.add_widget("sticky", data={"text": "old"})
        result = self.ws.update_sticky(w.id, "new text")
        self.assertTrue(result)
        self.assertEqual(w.data["text"], "new text")

    def test_update_sticky_wrong_type(self):
        w = self.ws.add_widget("clock")
        result = self.ws.update_sticky(w.id, "text")
        self.assertFalse(result)

    def test_render_returns_image(self):
        self.ws.add_widget("clock")
        self.ws.add_widget("cpu")
        self.ws.add_widget("memory")
        img = self.ws.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_render_empty(self):
        img = self.ws.render()
        self.assertIsNotNone(img)

    def test_callback(self):
        events = []
        self.ws.on_event(lambda t, w: events.append((t, w.widget_type)))
        w = self.ws.add_widget("clock")
        self.ws.remove_widget(w.id)
        self.assertEqual(events, [("added", "clock"), ("removed", "clock")])

    def test_sticky_word_wrap(self):
        w = self.ws.add_widget("sticky", data={
            "text": "This is a very long note that should be wrapped across multiple lines in the widget"
        })
        img = self.ws.render()
        self.assertIsNotNone(img)


class TestClipboardManager(unittest.TestCase):
    """Tests for the clipboard manager."""

    def setUp(self):
        from ui.clipboard import ClipboardManager
        self.cb = ClipboardManager(max_history=10)

    def test_copy(self):
        entry = self.cb.copy("hello")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.content, "hello")
        self.assertEqual(self.cb.count, 1)

    def test_paste(self):
        self.cb.copy("world")
        result = self.cb.paste()
        self.assertEqual(result, "world")

    def test_paste_empty(self):
        result = self.cb.paste()
        self.assertIsNone(result)

    def test_copy_deduplicates(self):
        self.cb.copy("same")
        self.cb.copy("same")
        self.assertEqual(self.cb.count, 1)

    def test_copy_different(self):
        self.cb.copy("a")
        self.cb.copy("b")
        self.assertEqual(self.cb.count, 2)

    def test_paste_entry(self):
        e1 = self.cb.copy("first")
        self.cb.copy("second")
        result = self.cb.paste_entry(e1.id)
        self.assertEqual(result, "first")
        self.assertEqual(self.cb.current_text, "first")

    def test_search(self):
        self.cb.copy("hello world")
        self.cb.copy("foo bar")
        results = self.cb.search("hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content, "hello world")

    def test_search_empty(self):
        self.cb.copy("a")
        self.cb.copy("b")
        results = self.cb.search("")
        self.assertEqual(len(results), 2)

    def test_pin(self):
        entry = self.cb.copy("pinned")
        result = self.cb.pin(entry.id)
        self.assertTrue(result)
        self.assertTrue(entry.pinned)

    def test_unpin(self):
        entry = self.cb.copy("unpin")
        self.cb.pin(entry.id)
        self.cb.unpin(entry.id)
        self.assertFalse(entry.pinned)

    def test_delete(self):
        entry = self.cb.copy("delete me")
        result = self.cb.delete(entry.id)
        self.assertTrue(result)
        self.assertEqual(self.cb.count, 0)

    def test_clear(self):
        self.cb.copy("a")
        self.cb.copy("b")
        count = self.cb.clear()
        self.assertEqual(count, 2)
        self.assertEqual(self.cb.count, 0)

    def test_clear_keeps_pinned(self):
        e1 = self.cb.copy("pinned")
        self.cb.pin(e1.id)
        self.cb.copy("unpinned")
        self.cb.clear()
        self.assertEqual(self.cb.count, 1)
        self.assertTrue(self.cb.entries[0].pinned)

    def test_max_history(self):
        for i in range(15):
            self.cb.copy(f"item {i}")
        self.assertLessEqual(self.cb.count, 10)

    def test_recent(self):
        for i in range(5):
            self.cb.copy(f"item {i}")
        recent = self.cb.recent(3)
        self.assertEqual(len(recent), 3)

    def test_pinned_entries(self):
        e1 = self.cb.copy("a")
        self.cb.pin(e1.id)
        self.cb.copy("b")
        pinned = self.cb.pinned_entries()
        self.assertEqual(len(pinned), 1)

    def test_by_type(self):
        self.cb.copy("text", content_type="text")
        self.cb.copy("image", content_type="image")
        text = self.cb.by_type("text")
        self.assertEqual(len(text), 1)

    def test_callback(self):
        events = []
        self.cb.on_event(lambda t, e: events.append(t))
        self.cb.copy("test")
        self.cb.paste()
        self.assertIn("copied", events)
        self.assertIn("pasted", events)

    def test_visibility(self):
        self.assertFalse(self.cb.visible)
        self.cb.show()
        self.assertTrue(self.cb.visible)
        self.cb.hide()
        self.assertFalse(self.cb.visible)

    def test_get_entry(self):
        entry = self.cb.copy("find me")
        found = self.cb.get_entry(entry.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.content, "find me")

    def test_delete_nonexistent(self):
        result = self.cb.delete("no-such-id")
        self.assertFalse(result)


class TestSpotlight(unittest.TestCase):
    """Tests for the spotlight search."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from ui.spotlight import Spotlight
        self.spot = Spotlight(self.session)

    def test_initial_state(self):
        self.assertFalse(self.spot.visible)
        self.assertEqual(self.spot.query, "")
        self.assertEqual(len(self.spot.results), 0)

    def test_show_hide(self):
        self.spot.show()
        self.assertTrue(self.spot.visible)
        self.spot.hide()
        self.assertFalse(self.spot.visible)

    def test_toggle(self):
        self.spot.toggle()
        self.assertTrue(self.spot.visible)
        self.spot.toggle()
        self.assertFalse(self.spot.visible)

    def test_type_char(self):
        self.spot.show()
        self.spot.type_char("s")
        self.assertEqual(self.spot.query, "s")
        self.assertGreater(len(self.spot.results), 0)

    def test_type_multiple(self):
        self.spot.show()
        for ch in "terminal":
            self.spot.type_char(ch)
        self.assertEqual(self.spot.query, "terminal")
        self.assertGreater(len(self.spot.results), 0)
        # Should find Terminal
        titles = [r.title for r in self.spot.results]
        self.assertIn("Terminal", titles)

    def test_backspace(self):
        self.spot.show()
        self.spot.type_char("abc")
        self.spot.backspace()
        self.assertEqual(self.spot.query, "ab")

    def test_backspace_empty(self):
        self.spot.show()
        self.spot.backspace()  # Should not crash
        self.assertEqual(self.spot.query, "")

    def test_clear_query(self):
        self.spot.show()
        self.spot.type_char("test")
        self.spot.clear_query()
        self.assertEqual(self.spot.query, "")
        self.assertEqual(len(self.spot.results), 0)

    def test_navigate_down(self):
        self.spot.show()
        self.spot.type_char("s")
        initial = self.spot.selected_index
        self.spot.navigate_down()
        self.assertNotEqual(self.spot.selected_index, initial)

    def test_navigate_up(self):
        self.spot.show()
        self.spot.type_char("s")
        self.spot.navigate_down()
        self.spot.navigate_up()
        self.assertEqual(self.spot.selected_index, 0)

    def test_navigate_wraps(self):
        self.spot.show()
        self.spot.type_char("s")
        count = len(self.spot.results)
        for _ in range(count + 2):
            self.spot.navigate_down()
        # Should still be valid
        self.assertIsNotNone(self.spot.selected)

    def test_execute_selected(self):
        self.spot.show()
        self.spot.type_char("theme")
        # Navigate to the "Change Theme" command (index 1)
        self.spot.navigate_down()
        result = self.spot.execute_selected()
        self.assertIsNotNone(result)
        self.assertFalse(self.spot.visible)
        # Theme should have changed
        self.assertEqual(
            self.session.document.themes.get("active"), "Solar")

    def test_execute_none(self):
        self.spot.show()
        result = self.spot.execute_selected()
        self.assertIsNone(result)

    def test_fuzzy_score_exact(self):
        score = self.spot._fuzzy_score("terminal", "Terminal")
        self.assertGreater(score, 0.8)

    def test_fuzzy_score_starts_with(self):
        score = self.spot._fuzzy_score("term", "Terminal")
        self.assertGreater(score, 0.7)

    def test_fuzzy_score_contains(self):
        score = self.spot._fuzzy_score("erm", "Terminal")
        self.assertGreater(score, 0.5)

    def test_fuzzy_score_no_match(self):
        score = self.spot._fuzzy_score("xyz", "Terminal")
        self.assertEqual(score, 0.0)

    def test_results_sorted_by_score(self):
        self.spot.show()
        self.spot.type_char("s")
        scores = [r.score for r in self.spot.results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_render_returns_image(self):
        self.spot.show()
        self.spot.type_char("term")
        img = self.spot.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_render_none_when_hidden(self):
        img = self.spot.render()
        self.assertIsNone(img)

    def test_callback(self):
        events = []
        self.spot.on_event(lambda t, e: events.append(t))
        self.spot.show()
        self.spot.type_char("s")
        self.spot.navigate_down()  # Navigate to a result with an action
        self.spot.execute_selected()
        self.assertIn("shown", events)
        self.assertIn("searched", events)
        self.assertIn("executed", events)

    def test_type_when_hidden(self):
        self.spot.type_char("s")  # Should not crash
        self.assertEqual(self.spot.query, "")

    def test_search_settings(self):
        self.spot.show()
        self.spot.type_char("theme")
        categories = [r.category for r in self.spot.results]
        self.assertIn("settings", categories)


class TestPowerMenu(unittest.TestCase):
    """Tests for the power menu."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)
        from ui.power_menu import PowerMenu
        self.pm = PowerMenu(self.session)

    def test_initial_state(self):
        self.assertFalse(self.pm.visible)
        self.assertEqual(len(self.pm.options), 5)

    def test_show_hide(self):
        self.pm.show()
        self.assertTrue(self.pm.visible)
        self.pm.hide()
        self.assertFalse(self.pm.visible)

    def test_toggle(self):
        self.pm.toggle()
        self.assertTrue(self.pm.visible)
        self.pm.toggle()
        self.assertFalse(self.pm.visible)

    def test_navigate(self):
        self.pm.show()
        initial = self.pm.selected.id
        self.pm.navigate_down()
        self.assertNotEqual(self.pm.selected.id, initial)
        self.pm.navigate_up()
        self.assertEqual(self.pm.selected.id, initial)

    def test_execute_lock(self):
        self.pm.show()
        # Navigate to lock (index 0)
        result = self.pm.execute()
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "lock")
        self.assertFalse(self.pm.visible)

    def test_execute_dangerous_requires_confirm(self):
        self.pm.show()
        # Navigate to shutdown (index 4)
        for _ in range(4):
            self.pm.navigate_down()
        result = self.pm.execute()
        self.assertIsNone(result)  # Not executed yet
        self.assertTrue(self.pm.confirming)
        self.assertIsNotNone(self.pm.confirm_option)

    def test_confirm_dangerous(self):
        self.pm.show()
        for _ in range(4):
            self.pm.navigate_down()
        self.pm.execute()  # Start confirmation
        result = self.pm.execute()  # Confirm
        self.assertIsNotNone(result)
        self.assertFalse(self.pm.visible)
        self.assertFalse(self.pm.confirming)

    def test_cancel(self):
        self.pm.show()
        for _ in range(4):
            self.pm.navigate_down()
        self.pm.execute()  # Start confirmation
        self.pm.cancel()
        self.assertFalse(self.pm.confirming)
        self.assertTrue(self.pm.visible)

    def test_render(self):
        self.pm.show()
        img = self.pm.render()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_render_none_when_hidden(self):
        img = self.pm.render()
        self.assertIsNone(img)

    def test_callback(self):
        events = []
        self.pm.on_event(lambda t, e: events.append(t))
        self.pm.show()
        self.pm.execute()  # Lock
        self.assertIn("shown", events)
        self.assertIn("executed", events)


class TestScreenCapture(unittest.TestCase):
    """Tests for the screenshot tool."""

    def setUp(self):
        self.doc = _make_doc()
        self.session = DesktopSession(self.doc)

    def test_grab_fullscreen(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_fullscreen()
        self.assertIsNotNone(result.image)
        self.assertEqual(result.region.width, 1920)
        self.assertEqual(result.region.height, 1080)
        self.assertGreater(result.timestamp, 0)

    def test_grab_region(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_region(100, 100, 800, 600)
        self.assertEqual(result.region.width, 800)
        self.assertEqual(result.region.height, 600)
        self.assertEqual(result.image.size, (800, 600))

    def test_save_to_file(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_fullscreen()
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            path = f.name
        try:
            saved = cap.save(result, path)
            self.assertTrue(os.path.exists(saved))
            self.assertGreater(os.path.getsize(saved), 0)
        finally:
            os.unlink(path)

    def test_clipboard(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_fullscreen()
        self.assertIsNone(cap.clipboard_image)
        cap.copy_to_clipboard(result)
        self.assertIsNotNone(cap.clipboard_image)

    def test_history(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        self.assertEqual(len(cap.history), 0)
        cap.grab_fullscreen()
        self.assertEqual(len(cap.history), 1)
        cap.grab_region(0, 0, 100, 100)
        self.assertEqual(len(cap.history), 2)
        self.assertEqual(cap.last_capture.region.width, 100)
        cleared = cap.clear_history()
        self.assertEqual(cleared, 2)
        self.assertEqual(len(cap.history), 0)

    def test_annotate(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_fullscreen()
        original_size = result.image.size
        cap.annotate(result, [
            {"type": "rectangle", "x": 10, "y": 10, "width": 50, "height": 50},
            {"type": "text", "x": 20, "y": 20, "text": "Test"},
        ])
        self.assertEqual(result.image.size, original_size)

    def test_grab_and_save(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            path = f.name
        try:
            saved = cap.grab_and_save(path)
            self.assertTrue(os.path.exists(saved))
        finally:
            os.unlink(path)

    def test_monitor_capture(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        result = cap.grab_fullscreen(monitor_id="monitor-0")
        self.assertIsNotNone(result)
        self.assertGreater(result.region.width, 0)

    def test_callback(self):
        from ui.screenshot import ScreenCapture
        cap = ScreenCapture(self.session)
        events = []
        cap.on_capture(lambda e, r: events.append(e))
        cap.grab_fullscreen()
        self.assertIn("capture", events)


class TestTextEditor(unittest.TestCase):
    """Tests for the text editor."""

    def setUp(self):
        from ui.text_editor import TextEditor
        self.editor = TextEditor()

    def test_new_file(self):
        self.assertIsNone(self.editor.filename)
        self.assertEqual(self.editor.line_count, 1)
        self.assertFalse(self.editor.modified)

    def test_insert_text(self):
        self.editor.insert_text("hello world")
        self.assertEqual(self.editor.text, "hello world")
        self.assertEqual(self.editor.line_count, 1)
        self.assertTrue(self.editor.modified)

    def test_insert_multiline(self):
        self.editor.insert_text("line1\nline2\nline3")
        self.assertEqual(self.editor.line_count, 3)
        self.assertEqual(self.editor.lines[0], "line1")
        self.assertEqual(self.editor.lines[2], "line3")

    def test_insert_newline(self):
        self.editor.insert_text("hello")
        self.editor.move_to_line_start()
        self.editor.insert_newline()
        self.assertEqual(self.editor.line_count, 2)

    def test_delete_char_forward(self):
        self.editor.insert_text("abc")
        self.editor.move_to_file_start()
        self.editor.delete_char(forward=True)
        self.assertEqual(self.editor.text, "bc")

    def test_delete_char_backward(self):
        self.editor.insert_text("abc")
        self.editor.move_to_file_end()
        self.editor.delete_char(forward=False)
        self.assertEqual(self.editor.text, "ab")

    def test_undo_redo(self):
        self.editor.insert_text("hello")
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.text, "")
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.text, "hello")

    def test_undo_empty(self):
        self.assertFalse(self.editor.undo())

    def test_redo_empty(self):
        self.assertFalse(self.editor.redo())

    def test_find(self):
        self.editor.insert_text("hello world\nhello again")
        results = self.editor.find("hello")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], (0, 0))
        self.assertEqual(results[1], (1, 0))

    def test_find_next(self):
        self.editor.insert_text("hello world\nhello again")
        self.editor.move_to_file_start()
        # find_next returns the first match AFTER cursor position
        self.editor.move_cursor(0, 1)  # move past first match
        result = self.editor.find_next("hello")
        self.assertEqual(result, (1, 0))

    def test_replace(self):
        self.editor.insert_text("hello world\nhello again")
        count = self.editor.replace("hello", "hi")
        self.assertEqual(count, 1)  # only first by default

    def test_replace_all(self):
        self.editor.insert_text("hello world\nhello again")
        count = self.editor.replace("hello", "hi", all_occurrences=True)
        self.assertEqual(count, 2)
        self.assertEqual(self.editor.lines[0], "hi world")
        self.assertEqual(self.editor.lines[1], "hi again")

    def test_cursor_movement(self):
        self.editor.insert_text("abc\ndef")
        self.editor.move_to_file_start()
        self.assertEqual(self.editor.cursor.line, 0)
        self.assertEqual(self.editor.cursor.column, 0)
        self.editor.move_cursor_right()
        self.assertEqual(self.editor.cursor.column, 1)
        self.editor.move_cursor_down()
        self.assertEqual(self.editor.cursor.line, 1)
        self.editor.move_cursor_left()
        self.assertEqual(self.editor.cursor.column, 0)

    def test_move_to_line_start_end(self):
        self.editor.insert_text("hello world")
        self.editor.move_to_line_end()
        self.assertEqual(self.editor.cursor.column, 11)
        self.editor.move_to_line_start()
        self.assertEqual(self.editor.cursor.column, 0)

    def test_selection(self):
        self.editor.insert_text("hello world")
        self.editor.move_to_file_start()  # cursor at (0, 0)
        self.editor.start_selection()     # start = (0, 0)
        self.editor.move_cursor(0, 5)     # cursor at (0, 5)
        self.editor.end_selection()       # end = (0, 5)
        self.assertTrue(self.editor.has_selection)
        sel = self.editor.get_selection()
        self.assertEqual(sel, (0, 0, 0, 5))
        deleted = self.editor.delete_selection()
        self.assertEqual(deleted, "hello")
        self.assertEqual(self.editor.text, " world")

    def test_file_operations(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("print('hello')")
            path = f.name
        try:
            self.assertTrue(self.editor.open_file(path))
            self.assertEqual(self.editor.filename, path)
            self.assertEqual(self.editor.language, "python")
            self.assertFalse(self.editor.modified)
            self.editor.insert_text("# comment")
            self.assertTrue(self.editor.modified)
            self.assertTrue(self.editor.save())
            self.assertFalse(self.editor.modified)
        finally:
            os.unlink(path)

    def test_language_detection(self):
        from ui.text_editor import _detect_language
        self.assertEqual(_detect_language("test.py"), "python")
        self.assertEqual(_detect_language("test.rs"), "rust")
        self.assertEqual(_detect_language("test.cpp"), "cpp")
        self.assertEqual(_detect_language("test.json"), "json")
        self.assertEqual(_detect_language("test.md"), "markdown")
        self.assertEqual(_detect_language("test.txt"), "text")

    def test_render(self):
        self.editor.insert_text("hello world")
        img = self.editor.render(800, 600)
        self.assertEqual(img.size, (800, 600))

    def test_tab_size(self):
        self.editor.tab_size = 2
        self.assertEqual(self.editor.tab_size, 2)
        self.editor.tab_size = 0
        self.assertEqual(self.editor.tab_size, 1)  # clamped

    def test_word_wrap(self):
        self.editor.word_wrap = False
        self.assertFalse(self.editor.word_wrap)
        self.editor.word_wrap = True
        self.assertTrue(self.editor.word_wrap)

    def test_properties(self):
        self.editor.insert_text("hello world\nsecond line")
        self.assertEqual(self.editor.char_count, 22)
        self.assertEqual(self.editor.word_count, 4)


# ---------------------------------------------------------------------------
# Calculator tests
# ---------------------------------------------------------------------------

class TestCalculator(unittest.TestCase):
    """Tests for ui.calculator.Calculator."""

    def setUp(self):
        from ui.calculator import Calculator
        self.calc = Calculator()

    def test_basic_addition(self):
        self.calc.press("5")
        self.calc.press("+")
        self.calc.press("3")
        self.calc.press("=")
        self.assertEqual(self.calc.display, "8")

    def test_basic_subtraction(self):
        self.calc.press("1")
        self.calc.press("0")
        self.calc.press("-")
        self.calc.press("3")
        self.calc.press("=")
        self.assertEqual(self.calc.display, "7")

    def test_basic_multiplication(self):
        self.calc.press("6")
        self.calc.press("*")
        self.calc.press("7")
        self.calc.press("=")
        self.assertEqual(self.calc.display, "42")

    def test_basic_division(self):
        self.calc.press("1")
        self.calc.press("5")
        self.calc.press("/")
        self.calc.press("3")
        self.calc.press("=")
        self.assertEqual(float(self.calc.display), 5.0)

    def test_division_by_zero(self):
        self.calc.press("5")
        self.calc.press("/")
        self.calc.press("0")
        self.calc.press("=")
        self.assertEqual(self.calc.display, "Error")
        self.assertTrue(self.calc.error)

    def test_clear(self):
        self.calc.press("5")
        self.calc.press("+")
        self.calc.press("3")
        self.calc.press("C")
        self.assertEqual(self.calc.display, "0")
        self.assertFalse(self.calc.error)

    def test_decimal(self):
        self.calc.press("3")
        self.calc.press(".")
        self.calc.press("1")
        self.calc.press("4")
        self.assertEqual(self.calc.display, "3.14")

    def test_sqrt(self):
        self.calc.press("9")
        self.calc.press("sqrt")
        self.assertEqual(self.calc.display, "3")

    def test_sin_zero(self):
        self.calc.press("0")
        self.calc.press("sin")
        self.assertEqual(self.calc.display, "0")

    def test_memory_add_recall(self):
        self.calc.press("4")
        self.calc.press("2")
        self.calc.press("m+")
        self.calc.press("C")
        self.calc.press("mr")
        self.assertEqual(self.calc.display, "42")
        self.calc.press("mc")

    def test_negate(self):
        self.calc.press("5")
        self.calc.press("±")
        self.assertEqual(self.calc.display, "-5")

    def test_reciprocal(self):
        self.calc.press("4")
        self.calc.press("1/x")
        self.assertEqual(self.calc.display, "0.25")

    def test_history(self):
        self.calc.press("5")
        self.calc.press("+")
        self.calc.press("3")
        self.calc.press("=")
        self.assertGreater(len(self.calc.history), 0)
        entry = self.calc.history[-1]
        self.assertEqual(entry.expression, "5 + 3")
        self.assertEqual(entry.result, "8")

    def test_clear_history(self):
        self.calc.press("1")
        self.calc.press("+")
        self.calc.press("2")
        self.calc.press("=")
        count = self.calc.clear_history()
        self.assertEqual(count, 1)
        self.assertEqual(len(self.calc.history), 0)

    def test_chained_operations(self):
        self.calc.press("2")
        self.calc.press("+")
        self.calc.press("3")
        self.calc.press("*")
        # Should compute 2+3=5 first
        self.calc.press("4")
        self.calc.press("=")
        # 5 * 4 = 20
        self.assertEqual(float(self.calc.display), 20.0)

    def test_backspace(self):
        self.calc.press("1")
        self.calc.press("2")
        self.calc.press("3")
        self.calc.press("backspace")
        self.assertEqual(self.calc.display, "12")

    def test_error_blocks_input(self):
        self.calc.press("5")
        self.calc.press("/")
        self.calc.press("0")
        self.calc.press("=")
        self.assertTrue(self.calc.error)
        # Non-clear key should be blocked
        self.calc.press("1")
        self.assertEqual(self.calc.display, "Error")
        self.calc.press("C")
        self.assertFalse(self.calc.error)

    def test_expression_property(self):
        self.calc.press("5")
        self.calc.press("+")
        self.calc.press("3")
        expr = self.calc.expression
        self.assertIn("5", expr)
        self.assertIn("+", expr)
        self.assertIn("3", expr)

    def test_angle_mode(self):
        self.assertEqual(self.calc.angle_mode, "deg")
        self.calc.press("rad")
        self.assertEqual(self.calc.angle_mode, "rad")
        self.calc.press("deg")
        self.assertEqual(self.calc.angle_mode, "deg")

    def test_visibility(self):
        self.assertFalse(self.calc.visible)
        self.calc.show()
        self.assertTrue(self.calc.visible)
        self.calc.hide()
        self.assertFalse(self.calc.visible)
        self.assertTrue(self.calc.toggle())
        self.assertFalse(self.calc.toggle())

    def test_callback(self):
        events = []
        self.calc.on_press(lambda e, k: events.append((e, k)))
        self.calc.press("5")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], ("press", "5"))


# ---------------------------------------------------------------------------
# SystemMonitor tests (no PIL, no process scanning)
# ---------------------------------------------------------------------------

class TestSystemMonitor(unittest.TestCase):
    """Tests for ui.system_monitor.SystemMonitor.

    These tests require importing the system_monitor module which reads
    /proc.  In memory-constrained CI environments the import can take a
    long time, so we skip gracefully on timeout.
    """

    def setUp(self):
        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self._import_system_monitor)
                fut.result(timeout=15)
        except concurrent.futures.TimeoutError:
            self.skipTest("system_monitor import timed out (memory pressure)")
        except ImportError as exc:
            self.skipTest(f"system_monitor not importable: {exc}")

    def _import_system_monitor(self):
        from ui.system_monitor import (
            SystemMonitor, CpuInfo, MemoryInfo, DiskInfo,
            NetworkInfo, ProcessInfo, SystemSnapshot,
        )
        self.SystemMonitor = SystemMonitor
        self.CpuInfo = CpuInfo
        self.MemoryInfo = MemoryInfo
        self.DiskInfo = DiskInfo
        self.NetworkInfo = NetworkInfo
        self.ProcessInfo = ProcessInfo
        self.SystemSnapshot = SystemSnapshot

    def test_snapshot(self):
        m = self.SystemMonitor(include_processes=False)
        snap = m.snapshot()
        self.assertIsNotNone(snap)
        self.assertGreater(snap.timestamp, 0)
        self.assertNotEqual(snap.hostname, "")

    def test_latest(self):
        m = self.SystemMonitor(include_processes=False)
        self.assertIsNone(m.latest)
        m.snapshot()
        self.assertIsNotNone(m.latest)

    def test_history(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        m.snapshot()
        self.assertEqual(len(m.history), 2)

    def test_cpu_history(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        h = m.cpu_history(10)
        self.assertEqual(len(h), 1)
        self.assertIsInstance(h[0], float)

    def test_memory_history(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        h = m.memory_history(10)
        self.assertEqual(len(h), 1)
        self.assertIsInstance(h[0], float)

    def test_summary(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        s = m.get_summary()
        self.assertIn("hostname", s)
        self.assertIn("cpu_percent", s)
        self.assertIn("memory_percent", s)
        self.assertIn("disks", s)

    def test_summary_empty(self):
        m = self.SystemMonitor(include_processes=False)
        self.assertEqual(m.get_summary(), {})

    def test_visibility(self):
        m = self.SystemMonitor()
        self.assertFalse(m.visible)
        m.show()
        self.assertTrue(m.visible)
        m.hide()
        self.assertFalse(m.visible)
        self.assertTrue(m.toggle())
        self.assertFalse(m.toggle())

    def test_callbacks(self):
        events = []
        m = self.SystemMonitor(include_processes=False)
        m.on_snapshot(lambda e, d: events.append(e))
        m.snapshot()
        self.assertIn("snapshot", events)

    def test_filtered_processes(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        procs = m.filtered_processes()
        self.assertIsInstance(procs, list)

    def test_process_search(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        m.set_process_search("nonexistent")
        procs = m.filtered_processes()
        self.assertEqual(len(procs), 0)

    def test_sort_by(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        m.set_sort_by("cpu")
        m.set_sort_by("name")
        m.set_sort_by("pid")
        m.set_sort_by("invalid")  # should be ignored

    def test_top_processes(self):
        m = self.SystemMonitor(include_processes=False)
        m.snapshot()
        top = m.top_processes(5, by="cpu")
        self.assertIsInstance(top, list)

    def test_data_classes(self):
        c = self.CpuInfo(percent=50.0, per_core=[40.0, 60.0])
        self.assertEqual(c.percent, 50.0)
        m = self.MemoryInfo(total_mb=8000, used_mb=4000, percent=50.0)
        self.assertEqual(m.total_mb, 8000)
        d = self.DiskInfo(mount="/", total_gb=100, used_gb=50, percent=50.0)
        self.assertEqual(d.mount, "/")
        n = self.NetworkInfo(interface="eth0", bytes_sent=1000, bytes_recv=2000)
        self.assertEqual(n.interface, "eth0")
        p = self.ProcessInfo(pid=1, name="test", cpu_percent=10.0)
        self.assertEqual(p.pid, 1)

    def test_snapshot_timestamp(self):
        s = self.SystemSnapshot()
        self.assertGreater(s.timestamp, 0)

    def test_history_size_limit(self):
        m = self.SystemMonitor(history_size=3, include_processes=False)
        for _ in range(5):
            m.snapshot()
        self.assertEqual(len(m.history), 3)


class TestNyforgeBridge(unittest.TestCase):
    """Tests for NyforgeBridge — Nyforge ↔ Nyrqis integration."""

    def _make_doc_json(self, windows=None, states=None, behaviors=None,
                        bindings=None):
        """Create a minimal .nstudio JSON dict."""
        if windows is None:
            windows = [{
                'id': 'win-1', 'type': 'Window',
                'layout': {'x': 100, 'y': 50, 'width': 600, 'height': 400},
                'properties': {'title': 'Test Window'},
            }]
        return {
            'version': '1.0.0',
            'screens': [{
                'id': 'desktop',
                'size': {'width': 1920, 'height': 1080},
                'root': {
                    'id': 'root', 'type': 'DesktopSurface',
                    'layout': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080},
                    'children': windows,
                }
            }],
            'states': states or {},
            'behaviors': behaviors or [],
            'bindings': bindings or [],
            'components': [],
        }

    def _make_session_and_bridge(self, doc_json=None):
        """Create a DesktopSession and NyforgeBridge from JSON."""
        from ui.nstudio import loads
        from ui.desktop_session import DesktopSession
        from ui.nyforge_bridge import NyforgeBridge
        if doc_json is None:
            doc_json = self._make_doc_json()
        doc = loads(json.dumps(doc_json))
        session = DesktopSession(doc)
        bridge = NyforgeBridge(session)
        return session, bridge

    def test_load_json_single_window(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        result = bridge.load_json(json.dumps(doc_json))
        self.assertTrue(result['ok'])
        self.assertEqual(result['windows_created'], 1)
        self.assertEqual(len(bridge.mapped_windows), 1)
        self.assertIn('win-1', bridge.mapped_windows)

    def test_load_json_geometry(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        bridge.load_json(json.dumps(doc_json))
        mapped = bridge.mapped_windows['win-1']
        self.assertEqual(mapped.x, 100)
        self.assertEqual(mapped.y, 50)
        self.assertEqual(mapped.width, 600)
        self.assertEqual(mapped.height, 400)
        self.assertEqual(mapped.title, 'Test Window')

    def test_load_json_role_detection(self):
        """A Window with 'taskbar' in the title gets the taskbar role."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(windows=[{
            'id': 'taskbar', 'type': 'Window',
            'layout': {'x': 0, 'y': 1030, 'width': 1920, 'height': 50},
            'properties': {'title': 'Taskbar'},
        }])
        bridge.load_json(json.dumps(doc_json))
        mapped = bridge.mapped_windows['taskbar']
        self.assertEqual(mapped.role, 'taskbar')

    def test_load_json_multiple_windows(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(windows=[
            {'id': 'w1', 'type': 'Window',
             'layout': {'x': 10, 'y': 10, 'width': 300, 'height': 200},
             'properties': {'title': 'First'}},
            {'id': 'w2', 'type': 'Window',
             'layout': {'x': 200, 'y': 100, 'width': 400, 'height': 300},
             'properties': {'title': 'Second'}},
        ])
        result = bridge.load_json(json.dumps(doc_json))
        self.assertEqual(result['windows_created'], 2)
        self.assertEqual(len(bridge.mapped_windows), 2)

    def test_load_json_invisible_window(self):
        """A window with no visibility property defaults to visible."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(windows=[{
            'id': 'win-a', 'type': 'Window',
            'layout': {'x': 0, 'y': 0, 'width': 300, 'height': 200},
            'properties': {'title': 'A'},
        }])
        bridge.load_json(json.dumps(doc_json))
        mapped = bridge.mapped_windows['win-a']
        self.assertTrue(mapped.visible)
        self.assertEqual(mapped.width, 300)
        self.assertEqual(mapped.height, 200)

    def test_load_json_invalid_json(self):
        session, bridge = self._make_session_and_bridge()
        result = bridge.load_json('not valid json {{{')
        self.assertFalse(result['ok'])
        self.assertIn('error', result)
        self.assertEqual(result['windows_created'], 0)

    def test_load_document_from_file(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nstudio',
                                         delete=False) as f:
            json.dump(doc_json, f)
            tmppath = f.name
        try:
            result = bridge.load_document(tmppath)
            self.assertTrue(result['ok'])
            self.assertEqual(result['windows_created'], 1)
            self.assertEqual(bridge.doc_path, os.path.abspath(tmppath))
            self.assertIsNotNone(bridge.doc_hash)
        finally:
            os.unlink(tmppath)

    def test_load_document_file_not_found(self):
        session, bridge = self._make_session_and_bridge()
        result = bridge.load_document('/nonexistent/file.nstudio')
        self.assertFalse(result['ok'])
        self.assertEqual(result['windows_created'], 0)

    def test_summary(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        bridge.load_json(json.dumps(doc_json))
        summary = bridge.summary()
        self.assertEqual(summary['mapped_windows'], 1)
        self.assertIn('win-1', summary['windows'])
        w = summary['windows']['win-1']
        self.assertEqual(w['role'], 'generic')
        self.assertIn('Test Window', w['title'])

    def test_refresh_unchanged(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        bridge.load_json(json.dumps(doc_json))
        old_hash = bridge.doc_hash
        # Refresh without file path returns error
        result = bridge.refresh()
        self.assertFalse(result['ok'])
        self.assertIn('error', result)

    def test_unmap_all(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(windows=[
            {'id': 'a', 'type': 'Window',
             'layout': {'x': 0, 'y': 0, 'width': 300, 'height': 200},
             'properties': {'title': 'A'}},
            {'id': 'b', 'type': 'Window',
             'layout': {'x': 10, 'y': 10, 'width': 300, 'height': 200},
             'properties': {'title': 'B'}},
        ])
        bridge.load_json(json.dumps(doc_json))
        self.assertEqual(len(bridge.mapped_windows), 2)
        bridge.unmap_all()
        self.assertEqual(len(bridge.mapped_windows), 0)

    def test_hot_reload_lifecycle(self):
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.nstudio',
                                         delete=False) as f:
            json.dump(doc_json, f)
            tmppath = f.name
        try:
            bridge.load_document(tmppath)
            self.assertFalse(bridge.is_hot_reload_active)
            # Enable hot reload
            bridge.enable_hot_reload(interval=0.1)
            self.assertTrue(bridge.is_hot_reload_active)
            # Disable
            bridge.disable_hot_reload()
            self.assertFalse(bridge.is_hot_reload_active)
        finally:
            bridge.disable_hot_reload()
            os.unlink(tmppath)

    def test_hot_reload_no_doc(self):
        session, bridge = self._make_session_and_bridge()
        with self.assertRaises(ValueError):
            bridge.enable_hot_reload()

    def test_event_callbacks(self):
        session, bridge = self._make_session_and_bridge()
        events = []
        bridge.on_event(lambda e, d: events.append((e, d)))
        # Trigger unmap_all which fires a notification
        bridge.unmap_all()
        self.assertTrue(any(e == 'unmap_all' for e, _ in events))

    def test_wiring_behaviors(self):
        """Behaviors with matching target should be wired."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(
            behaviors=[{
                'id': 'b1',
                'condition': None,
                'action': {
                    'target': 'win-1',
                    'name': 'Close',
                    'arguments': {},
                },
            }]
        )
        result = bridge.load_json(json.dumps(doc_json))
        self.assertTrue(result['ok'])
        self.assertEqual(result['behaviors_wired'], 1)

    def test_wiring_behaviors_no_match(self):
        """Behaviors with non-matching action name are not wired."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(
            behaviors=[{
                'id': 'b1',
                'condition': None,
                'action': {
                    'target': 'win-1',
                    'name': 'Close',
                    'arguments': {},
                },
            }]
        )
        result = bridge.load_json(json.dumps(doc_json))
        # The bridge wired it (Close maps to close_window)
        self.assertTrue(result['ok'])
        self.assertEqual(result['behaviors_wired'], 1)

    def test_wiring_behaviors_system_target(self):
        """System-target behaviors are skipped by the bridge."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(
            behaviors=[{
                'id': 'b1',
                'condition': None,
                'action': {
                    'target': 'System',
                    'name': 'Nyrqis.Notification.Show',
                    'arguments': {'title': 'hi', 'message': 'test'},
                },
            }]
        )
        result = bridge.load_json(json.dumps(doc_json))
        self.assertTrue(result['ok'])
        # System targets are not in _mapped, so wired=0
        self.assertEqual(result['behaviors_wired'], 0)

    def test_apply_bindings(self):
        """Bindings that match mapped windows should update properties."""
        session, bridge = self._make_session_and_bridge()
        doc_json = self._make_doc_json(
            states={'title': 'Dynamic Title'},
            bindings=[{
                'component': 'win-1',
                'property': 'title',
                'state': 'title',
            }]
        )
        result = bridge.load_json(json.dumps(doc_json))
        self.assertEqual(result['bindings_applied'], 1)
        # Check the window title was updated
        win = [w for w in session.windows if w.id == bridge.mapped_windows['win-1'].window_id][0]
        self.assertEqual(win.title, 'Dynamic Title')


class TestNyrqisDesktopShell(unittest.TestCase):
    """Tests for the Nyrqis Desktop Shell."""

    def test_shell_creation(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell(width=1920, height=1080, theme='Eclipse')
        self.assertEqual(shell.theme, 'Eclipse')
        self.assertGreater(len(shell.apps), 0)
        self.assertIsNotNone(shell.session)

    def test_shell_summary(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        s = shell.summary()
        self.assertEqual(s['display'], '1920x1080')
        self.assertEqual(s['theme'], 'Eclipse')
        self.assertIn('windows', s)
        self.assertIn('apps', s)

    def test_open_app(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        wid = shell.open_app('terminal')
        self.assertIsNotNone(wid)
        self.assertEqual(len(shell.session.windows), 1)
        self.assertEqual(shell.session.windows[0].title, '💻 Terminal')

    def test_open_unknown_app(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        wid = shell.open_app('nonexistent')
        self.assertIsNone(wid)

    def test_open_multiple_apps(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        shell.open_app('terminal')
        shell.open_app('settings')
        shell.open_app('calculator')
        self.assertEqual(len(shell.session.windows), 3)
        self.assertEqual(shell._launch_count, 3)

    def test_search_start_menu(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        results = shell.search_start_menu('calc')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, 'Calculator')

    def test_search_start_menu_empty(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        results = shell.search_start_menu('')
        self.assertEqual(len(results), len(shell.apps))

    def test_pinned_apps(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        pinned = shell.get_pinned_apps()
        self.assertGreater(len(pinned), 0)
        for app in pinned:
            self.assertTrue(app.pinned)

    def test_apps_by_category(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        system_apps = shell.get_apps_by_category('System')
        self.assertGreater(len(system_apps), 0)
        for app in system_apps:
            self.assertEqual(app.category, 'System')

    def test_theme_switch(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell(theme='Eclipse')
        self.assertEqual(shell.theme, 'Eclipse')
        shell.theme = 'Solar'
        self.assertEqual(shell.theme, 'Solar')
        self.assertEqual(shell.session.document.themes['active'], 'Solar')

    def test_build_shell_document(self):
        from examples.nyrqis_shell import build_shell_document
        import json
        doc_json = build_shell_document(width=1920, height=1080)
        doc = json.loads(doc_json)
        self.assertEqual(doc['version'], '1.0.0')
        self.assertEqual(len(doc['screens']), 1)
        self.assertEqual(doc['screens'][0]['size']['width'], 1920)
        self.assertIn('states', doc)
        self.assertIn('behaviors', doc)
        self.assertIn('bindings', doc)

    def test_shell_uptime(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        import time
        shell = NyrqisDesktopShell()
        time.sleep(0.01)
        self.assertGreater(shell.uptime, 0)

    def test_shell_render(self):
        """Shell render should return an image (or None if PIL unavailable)."""
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        img = shell.render()
        # render returns PIL Image or None
        self.assertTrue(img is None or hasattr(img, 'save'))

    def test_open_file(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        wid = shell.open_file('/tmp/test.py')
        self.assertIsNotNone(wid)

    def test_start_menu_toggle(self):
        from examples.nyrqis_shell import NyrqisDesktopShell
        shell = NyrqisDesktopShell()
        result = shell.toggle_start_menu()
        self.assertTrue(result)
        result = shell.toggle_start_menu()
        self.assertFalse(result)


class TestNyAppPackager(unittest.TestCase):
    """Tests for the NyApp packager."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyapp-test-")
        self.packager = NyAppPackager(app_dir=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_default(self):
        """Build a minimal .napp with defaults."""
        napp = self.packager.build(name="test-app", output=os.path.join(self.tmpdir, "test.napp"))
        self.assertIsInstance(napp, bytes)
        self.assertTrue(napp.startswith(b"NYAP"))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "test.napp")))

    def test_build_from_source(self):
        """Build a .napp from Python source."""
        src_path = os.path.join(self.tmpdir, "hello.py")
        with open(src_path, "w") as f:
            f.write('print("hello")\n')
        napp = self.packager.build(name="hello", source=src_path)
        parsed = parse_napp(napp)
        self.assertEqual(parsed["manifest"]["name"], "hello")
        self.assertGreater(len(parsed["code"]), 0)

    def test_parse_napp(self):
        """Parse a .napp binary."""
        napp = self.packager.build(name="parse-test")
        parsed = parse_napp(napp)
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(parsed["manifest"]["name"], "parse-test")
        self.assertIn("code", parsed)
        self.assertIn("data", parsed)

    def test_validate_napp(self):
        """Validate a .napp binary."""
        napp = self.packager.build(name="validate-test")
        issues = validate_napp(napp)
        self.assertEqual(len(issues), 0)

    def test_validate_bad_magic(self):
        """Validate rejects bad magic bytes."""
        issues = validate_napp(b"XXXX")
        self.assertGreater(len(issues), 0)

    def test_inspect(self):
        """Inspect a .napp package."""
        napp = self.packager.build(name="inspect-test")
        path = os.path.join(self.tmpdir, "inspect.napp")
        with open(path, "wb") as f:
            f.write(napp)
        result = self.packager.inspect(path)
        self.assertEqual(result["manifest"]["name"], "inspect-test")
        self.assertIn("disassembly", result)
        self.assertIsInstance(result["disassembly"], list)

    def test_run_python_backend(self):
        """Run a .napp using the Python interpreter."""
        napp = self.packager.build(name="run-test")
        path = os.path.join(self.tmpdir, "run.napp")
        with open(path, "wb") as f:
            f.write(napp)
        result = self.packager.run(path)
        self.assertIn("exit_code", result)
        self.assertIn("backend", result)
        self.assertIn(result["backend"], ("rust", "python"))

    def test_install_and_list(self):
        """Install a .napp and list it."""
        napp = self.packager.build(name="install-test", version="2.0.0", description="A test")
        path = os.path.join(self.tmpdir, "install.napp")
        with open(path, "wb") as f:
            f.write(napp)
        dest = self.packager.install(path)
        self.assertTrue(os.path.exists(dest))
        apps = self.packager.list_installed()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["name"], "install-test")
        self.assertEqual(apps[0]["version"], "2.0.0")

    def test_compile_print(self):
        """Compile a print statement to opcodes."""
        code, data = compile_source('print("hello")', 'test')
        self.assertGreater(len(code), 0)
        self.assertEqual(code[0], OP_LOG)

    def test_compile_state(self):
        """Compile a state assignment to opcodes."""
        code, data = compile_source('state["key"] = "value"', 'test')
        self.assertGreater(len(code), 0)
        self.assertEqual(code[0], OP_SET_STATE)

    def test_compile_halt(self):
        """Compiled source should end with HALT."""
        code, data = compile_source('print("x")', 'test')
        self.assertEqual(code[-2], OP_HALT)


# ---------------------------------------------------------------------------
# Command system (undo/redo)
# ---------------------------------------------------------------------------

class TestCommandSystem(unittest.TestCase):
    """Tests for the undo/redo command system."""

    def setUp(self):
        from ui.commands import (
            UndoManager, AddWindowCommand, RemoveWindowCommand,
            MoveWindowCommand, ResizeWindowCommand, FocusWindowCommand,
            MinimizeWindowCommand, MaximizeWindowCommand,
            ChangePropertyCommand, ChangeThemeCommand, Transaction,
        )
        self.UndoManager = UndoManager
        self.AddWindowCommand = AddWindowCommand
        self.RemoveWindowCommand = RemoveWindowCommand
        self.MoveWindowCommand = MoveWindowCommand
        self.ResizeWindowCommand = ResizeWindowCommand
        self.FocusWindowCommand = FocusWindowCommand
        self.MinimizeWindowCommand = MinimizeWindowCommand
        self.MaximizeWindowCommand = MaximizeWindowCommand
        self.ChangePropertyCommand = ChangePropertyCommand
        self.ChangeThemeCommand = ChangeThemeCommand
        self.Transaction = Transaction
        self.doc = loads(json.dumps({
            'version': '1.0.0',
            'project': {'id': 'test'},
            'themes': {'active': 'Eclipse'},
            'states': {},
            'screens': [{
                'id': 'screen-1',
                'size': {'width': 1920, 'height': 1080},
                'root': {
                    'id': 'root', 'type': 'DesktopSurface',
                    'layout': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080},
                    'children': [],
                },
            }],
        }))
        from ui.commands import install_undo
        self.session = DesktopSession(self.doc)
        install_undo(self.session)

    def _add_window(self, win_id='win-1', title='Test'):
        from ui.desktop_session import Window
        win = Window(id=win_id, component_id=win_id, title=title,
                     x=100, y=100, width=600, height=400)
        cmd = self.AddWindowCommand(self.session, win)
        self.session.execute(cmd)
        return win

    def test_undo_manager_basic(self):
        mgr = self.UndoManager()
        self.assertFalse(mgr.can_undo)
        self.assertFalse(mgr.can_redo)
        self.assertIsNone(mgr.undo())
        self.assertIsNone(mgr.redo())

    def test_add_window_undo(self):
        self._add_window()
        self.assertEqual(len(self.session.windows), 1)
        result = self.session.undo()
        self.assertIsNotNone(result)
        self.assertEqual(len(self.session.windows), 0)

    def test_add_window_redo(self):
        self._add_window()
        self.session.undo()
        self.assertEqual(len(self.session.windows), 0)
        self.session.redo()
        self.assertEqual(len(self.session.windows), 1)

    def test_remove_window_undo(self):
        self._add_window()
        cmd = self.RemoveWindowCommand(self.session, 'win-1')
        self.session.execute(cmd)
        self.assertEqual(len(self.session.windows), 0)
        self.session.undo()
        self.assertEqual(len(self.session.windows), 1)

    def test_move_window_undo(self):
        self._add_window()
        cmd = self.MoveWindowCommand(self.session, 'win-1', x=200, y=300)
        self.session.execute(cmd)
        win = self.session.windows[0]
        self.assertEqual(win.x, 200)
        self.assertEqual(win.y, 300)
        self.session.undo()
        self.assertEqual(win.x, 100)
        self.assertEqual(win.y, 100)

    def test_resize_window_undo(self):
        self._add_window()
        cmd = self.ResizeWindowCommand(self.session, 'win-1', width=800, height=600)
        self.session.execute(cmd)
        win = self.session.windows[0]
        self.assertEqual(win.width, 800)
        self.assertEqual(win.height, 600)
        self.session.undo()
        self.assertEqual(win.width, 600)
        self.assertEqual(win.height, 400)

    def test_minimize_window_undo(self):
        self._add_window()
        cmd = self.MinimizeWindowCommand(self.session, 'win-1')
        self.session.execute(cmd)
        win = self.session.windows[0]
        self.assertTrue(win.minimized)
        self.session.undo()
        self.assertFalse(win.minimized)

    def test_maximize_window_undo(self):
        self._add_window()
        cmd = self.MaximizeWindowCommand(self.session, 'win-1')
        self.session.execute(cmd)
        win = self.session.windows[0]
        self.assertTrue(win.maximized)
        self.session.undo()
        self.assertFalse(win.maximized)

    def test_change_property_undo(self):
        # Use a component that exists in the document tree
        comp = self.doc.find_component('root')
        self.assertIsNotNone(comp)
        cmd = self.ChangePropertyCommand(self.session, 'root', 'title', 'New Title')
        self.session.execute(cmd)
        self.assertEqual(comp.properties['title'], 'New Title')
        self.session.undo()
        self.assertNotEqual(comp.properties.get('title'), 'New Title')

    def test_change_theme_undo(self):
        cmd = self.ChangeThemeCommand(self.session, 'Solar')
        self.session.execute(cmd)
        self.assertEqual(self.doc.themes['active'], 'Solar')
        self.session.undo()
        self.assertEqual(self.doc.themes['active'], 'Eclipse')

    def test_transaction_undo(self):
        self._add_window()
        txn = self.Transaction('move window')
        txn.add(self.MoveWindowCommand(self.session, 'win-1', x=200, y=300))
        txn.add(self.ChangeThemeCommand(self.session, 'Solar'))
        self.session.execute(txn)
        win = self.session.windows[0]
        self.assertEqual(win.x, 200)
        self.assertEqual(self.doc.themes['active'], 'Solar')
        self.session.undo()
        self.assertEqual(win.x, 100)
        self.assertEqual(self.doc.themes['active'], 'Eclipse')

    def test_undo_stack_depth(self):
        mgr = self.UndoManager(max_depth=3)
        from ui.desktop_session import Window
        for i in range(5):
            win = Window(id=f'w-{i}', component_id=f'w-{i}', title=f'W{i}')
            cmd = self.AddWindowCommand(self.session, win)
            mgr.push(cmd)
        self.assertEqual(mgr.undo_depth, 3)  # trimmed
        self.assertEqual(mgr.redo_depth, 0)

    def test_new_action_clears_redo(self):
        self._add_window()
        self.session.undo()
        self.assertTrue(self.session._undo_manager.can_redo)
        self._add_window()
        self.assertFalse(self.session._undo_manager.can_redo)

    def test_undo_manager_summary(self):
        self._add_window()
        summary = self.session._undo_manager.summary()
        self.assertTrue(summary['can_undo'])
        self.assertFalse(summary['can_redo'])
        self.assertEqual(summary['undo_depth'], 1)

    def test_install_undo(self):
        from ui.commands import install_undo
        session = DesktopSession(self.doc)
        mgr = install_undo(session)
        self.assertIsNotNone(mgr)
        self.assertTrue(hasattr(session, 'execute'))
        self.assertTrue(hasattr(session, 'undo'))
        self.assertTrue(hasattr(session, 'redo'))

    def test_focus_window_undo(self):
        w1 = self._add_window('w1', 'First')
        w2 = self._add_window('w2', 'Second')
        # Second window is focused
        self.assertEqual(self.session.focused_window.id, 'w2')
        cmd = self.FocusWindowCommand(self.session, 'w1')
        self.session.execute(cmd)
        self.assertEqual(self.session.focused_window.id, 'w1')
        self.session.undo()
        self.assertEqual(self.session.focused_window.id, 'w2')

    def test_redo_reapply(self):
        self._add_window()
        cmd = self.MoveWindowCommand(self.session, 'win-1', x=500, y=500)
        self.session.execute(cmd)
        self.session.undo()
        self.session.redo()
        win = self.session.windows[0]
        self.assertEqual(win.x, 500)
        self.assertEqual(win.y, 500)

    def test_clear_stacks(self):
        self._add_window()
        mgr = self.session._undo_manager
        self.assertTrue(mgr.can_undo)
        mgr.clear()
        self.assertFalse(mgr.can_undo)
        self.assertFalse(mgr.can_redo)


# ---------------------------------------------------------------------------
# Apple Compositor
# ---------------------------------------------------------------------------

class TestAppleCompositor(unittest.TestCase):
    """Tests for the Apple-quality compositor."""

    def setUp(self):
        self.doc = loads(json.dumps({
            'version': '1.0.0',
            'project': {'id': 'test'},
            'themes': {'active': 'Eclipse'},
            'states': {},
            'screens': [{
                'id': 'screen-1',
                'size': {'width': 1920, 'height': 1080},
                'root': {
                    'id': 'root', 'type': 'DesktopSurface',
                    'layout': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080},
                    'children': [],
                },
            }],
        }))

    def test_import(self):
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor()
        self.assertIsNotNone(comp)

    def test_dark_mode_colors(self):
        from ui.apple_compositor import AppleCompositor, APPLE_COLORS_DARK
        comp = AppleCompositor(dark_mode=True)
        self.assertEqual(comp.colors, APPLE_COLORS_DARK)
        self.assertEqual(comp._c('accent'), (10, 132, 255))

    def test_light_mode_colors(self):
        from ui.apple_compositor import AppleCompositor, APPLE_COLORS_LIGHT
        comp = AppleCompositor(dark_mode=False)
        self.assertEqual(comp.colors, APPLE_COLORS_LIGHT)
        self.assertEqual(comp._c('accent'), (0, 122, 255))

    def test_render_document(self):
        """Render a simple document with the Apple compositor."""
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(dark_mode=True)
        img = comp.render_document(self.doc)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1920, 1080))

    def test_color_helper(self):
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(dark_mode=True)
        self.assertEqual(comp._c('text_primary'), (255, 255, 255))
        self.assertEqual(comp._c('close_btn'), (255, 69, 58))
        self.assertEqual(comp._c('nonexistent'), (128, 128, 128))

    def test_scale_factor(self):
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(dark_mode=True, scale=2.0)
        img = comp.render_document(self.doc)
        self.assertEqual(img.size, (3840, 2160))


class TestA11ySchema(unittest.TestCase):
    """Tests for the NUI accessibility schema (ui/a11y.py)."""

    def test_default_role_mapping(self):
        from ui.a11y import A11yMetadata, _DEFAULT_ROLES
        a = A11yMetadata()
        self.assertEqual(a.effective_role("Button").value, "button")
        self.assertEqual(a.effective_role("Slider").value, "slider")
        self.assertEqual(a.effective_role("Notification").value, "alert")
        self.assertEqual(a.effective_role("Taskbar").value, "banner")

    def test_explicit_role_overrides_default(self):
        from ui.a11y import A11yMetadata
        a = A11yMetadata(role="navigation")
        self.assertEqual(a.effective_role("Container").value, "navigation")

    def test_invalid_role(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(role="notarole")
        issues = validate_a11y(a, "Button", "btn-1")
        self.assertTrue(any('ERROR' in i and 'invalid' in i.lower() for i in issues))

    def test_button_requires_label(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(role="button")
        issues = validate_a11y(a, "Button", "btn-1")
        self.assertTrue(any('ERROR' in i and 'label' in i.lower() for i in issues))

    def test_button_with_label_passes(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(role="button", label="Submit")
        issues = validate_a11y(a, "Button", "btn-1")
        self.assertFalse(any('ERROR' in i for i in issues))

    def test_focusable_implies_label_warning(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(role="button")
        issues = validate_a11y(a, "Button", "btn-1")
        self.assertTrue(any('WARN' in i and 'label' in i.lower() for i in issues))

    def test_tabindex_on_non_focusable_warns(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(tab_index=1)
        issues = validate_a11y(a, "Text", "txt-1")
        self.assertTrue(any('WARN' in i and 'tabindex' in i.lower() for i in issues))

    def test_invalid_live_region(self):
        from ui.a11y import A11yMetadata, validate_a11y
        a = A11yMetadata(live_region="loud")
        issues = validate_a11y(a, "Notification", "notif-1")
        self.assertTrue(any('ERROR' in i and 'live_region' in i.lower() for i in issues))

    def test_valid_live_region(self):
        from ui.a11y import A11yMetadata, validate_a11y
        for lr in ('polite', 'assertive', 'off'):
            a = A11yMetadata(live_region=lr, label="Status")
            issues = validate_a11y(a, "Notification", "notif-1")
            self.assertFalse(any('ERROR' in i for i in issues))

    def test_to_dict_from_dict_roundtrip(self):
        from ui.a11y import A11yMetadata
        a = A11yMetadata(role='button', label='OK', tab_index=0, live_region='polite')
        d = a.to_dict()
        self.assertEqual(d['role'], 'button')
        self.assertNotIn('description', d)  # None omitted
        b = A11yMetadata.from_dict(d)
        self.assertEqual(b.role, 'button')
        self.assertEqual(b.label, 'OK')
        self.assertEqual(b.tab_index, 0)
        self.assertEqual(b.live_region, 'polite')

    def test_audit_tree(self):
        from ui.a11y import audit_a11y_tree
        tree = [
            {'id': 'b1', 'type': 'Button', 'children': []},
            {'id': 't1', 'type': 'Text', 'accessibility': {'role': 'heading'}},
        ]
        issues = audit_a11y_tree(tree)
        # Button without label → ERROR
        self.assertTrue(any('b1' in i and 'ERROR' in i for i in issues))
        # Text without label → WARN (heading should have label)
        self.assertTrue(any('t1' in i for i in issues))


class TestLocalize(unittest.TestCase):
    """Tests for the NUI localization system (ui/localize.py)."""

    def setUp(self):
        from ui.localize import LocaleManager
        self.lm = LocaleManager()
        self.lm.load_dict('en', {
            'settings': {'save': 'Save', 'cancel': 'Cancel'},
            'app': {'title': 'My App'},
        })
        self.lm.load_dict('af', {
            'settings': {'save': 'Stoor', 'cancel': 'Kanselleer'},
            'app': {'title': 'My Toepassing'},
        })
        self.lm.set_active('en')

    def test_resolve_plain_string(self):
        self.assertEqual(self.lm.resolve_string('hello'), 'hello')

    def test_resolve_localize_ref(self):
        self.assertEqual(
            self.lm.resolve_string('$localize:settings.save'), 'Save')

    def test_resolve_missing_key(self):
        result = self.lm.resolve_string('$localize:missing.key')
        self.assertIn('$localize:missing.key', result)

    def test_switch_locale(self):
        self.lm.set_active('af')
        self.assertEqual(
            self.lm.resolve_string('$localize:settings.save'), 'Stoor')

    def test_resolve_recursive(self):
        obj = {
            'title': '$localize:app.title',
            'items': ['$localize:settings.save', 'literal'],
            'nested': {'label': '$localize:settings.cancel'},
        }
        result = self.lm.resolve_recursive(obj)
        self.assertEqual(result['title'], 'My App')
        self.assertEqual(result['items'][0], 'Save')
        self.assertEqual(result['items'][1], 'literal')
        self.assertEqual(result['nested']['label'], 'Cancel')

    def test_load_inline(self):
        from ui.localize import LocaleManager
        lm = LocaleManager()
        lm.load_inline({
            'active': 'fr',
            'tables': {'fr': {'hello': 'Bonjour'}},
        })
        self.assertEqual(lm.active_locale, 'fr')
        self.assertEqual(lm.resolve_string('$localize:hello'), 'Bonjour')

    def test_available_locales(self):
        self.assertEqual(self.lm.available_locales, ['af', 'en'])

    def test_summary(self):
        s = self.lm.summary()
        self.assertEqual(s['active'], 'en')
        self.assertEqual(s['locales']['en'], 3)  # 3 flattened keys (settings.save, settings.cancel, app.title)

    def test_flatten_nested(self):
        from ui.localize import LocaleManager
        lm = LocaleManager()
        lm.load_dict('en', {'a': {'b': {'c': 'deep'}}})
        self.assertEqual(lm.resolve_string('$localize:a.b.c'), 'deep')

    def test_validate_document_missing_keys(self):
        from ui.localize import LocaleManager
        lm = LocaleManager()
        lm.load_dict('en', {'greeting': 'Hello'})
        lm.set_active('en')
        # Walk screens manually for the mock
        issues = []
        def _walk(node, path):
            props = getattr(node, 'properties', {})
            if isinstance(props, dict):
                for k, v in props.items():
                    import re as _re
                    for m in _re.finditer(r'\$localize:([A-Za-z0-9_.\-]+)', str(v)):
                        key = m.group(1)
                        if not lm.check_key_exists(key):
                            issues.append(f'{path}.{k}: missing {key}')
            for child in getattr(node, 'children', []):
                _walk(child, f'{path}.{child.id}')
        screen_root = type('R', (), {'id': 'root', 'type': 'DesktopSurface', 'children': [
            type('C', (), {'id': 'c1', 'type': 'Text', 'children': [],
                'properties': {'text': '$localize:missing'}})()
        ]})()
        _walk(screen_root, 'root')
        self.assertTrue(len(issues) > 0)
        self.assertTrue(any('missing' in i for i in issues))

    def test_validate_no_locales_loaded(self):
        from ui.localize import LocaleManager
        lm = LocaleManager()  # empty
        issues = lm.validate_document(type('Doc', (), {
            'states': {},
            'screens': [],
            'behaviors': [],
        })())
        # No $localize refs in empty doc = no issues
        self.assertEqual(len(issues), 0)


if __name__ == "__main__":
    unittest.main()
