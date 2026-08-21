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


if __name__ == "__main__":
    unittest.main()
