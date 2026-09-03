"""
Tests for Config Editor, Notification Center, and Workspace Manager.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.config_editor import (
    ConfigEditor, ConfigFile, ConfigEntry, ConfigProfile, ConfigDiff,
    ConfigCategory, ConfigStatus
)
from ui.notification_center import (
    NotificationCenter, Notification, AppNotificationSettings,
    NotificationGroup, NotificationAction,
    NotificationPriority, NotificationStatus
)
from ui.workspace_manager import (
    WorkspaceManager, Workspace, WorkspaceWindow, TilingPreset, Monitor,
    TilingMode, WindowState, MonitorRole
)


# ─── Config Editor Tests ─────────────────────────────────────────────────


class TestConfigEditor(unittest.TestCase):

    def setUp(self):
        self.editor = ConfigEditor()

    def test_initial_state(self):
        self.assertEqual(self.editor.view_mode, "files")
        self.assertGreater(len(self.editor.files), 0)
        self.assertGreater(len(self.editor.profiles), 0)

    def test_set_value(self):
        self.assertTrue(self.editor.set_value(0, 0, "new-hostname"))
        entry = self.editor.files[0].entries[0]
        self.assertEqual(entry.value, "new-hostname")
        self.assertEqual(entry.status, ConfigStatus.MODIFIED)

    def test_set_invalid_option(self):
        result = self.editor.set_value(1, 0, "invalid_dhcp")
        self.assertFalse(result)

    def test_undo(self):
        original = self.editor.files[0].entries[0].value
        self.editor.set_value(0, 0, "changed")
        self.assertTrue(self.editor.undo())
        self.assertEqual(self.editor.files[0].entries[0].value, original)

    def test_redo(self):
        self.editor.set_value(0, 0, "changed")
        self.editor.undo()
        self.assertTrue(self.editor.redo())
        self.assertEqual(self.editor.files[0].entries[0].value, "changed")

    def test_save_file(self):
        self.editor.set_value(0, 0, "changed")
        self.assertTrue(self.editor.save_file(0))
        self.assertFalse(self.editor.files[0].modified)

    def test_reset_entry(self):
        original = self.editor.files[0].entries[0].default
        self.editor.set_value(0, 0, "changed")
        self.assertTrue(self.editor.reset_entry(0, 0))
        self.assertEqual(self.editor.files[0].entries[0].value, original)

    def test_save_profile(self):
        profile = self.editor.save_profile("Test Profile", "Testing")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "Test Profile")

    def test_load_profile(self):
        self.assertTrue(self.editor.load_profile(1))
        self.assertEqual(self.editor.active_profile, "Office")

    def test_delete_profile(self):
        initial = len(self.editor.profiles)
        self.assertTrue(self.editor.delete_profile(1))
        self.assertEqual(len(self.editor.profiles), initial - 1)

    def test_generate_diff(self):
        text = self.editor.generate_diff_text(0)
        self.assertIsInstance(text, str)

    def test_navigation(self):
        self.editor.select_file_down()
        self.assertEqual(self.editor.selected_file, 1)
        self.editor.select_file_up()
        self.assertEqual(self.editor.selected_file, 0)

    def test_render_files(self):
        lines = self.editor.render_files()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_editor(self):
        self.editor.set_view("editor")
        lines = self.editor.render_editor()
        self.assertIsInstance(lines, list)

    def test_render_profiles(self):
        self.editor.set_view("profiles")
        lines = self.editor.render_profiles()
        self.assertIsInstance(lines, list)

    def test_render_diff(self):
        self.editor.set_view("diff")
        lines = self.editor.render_diff()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.editor.handle_key("Enter")
        self.assertEqual(result, "editor")


class TestConfigEntry(unittest.TestCase):

    def test_display(self):
        entry = ConfigEntry("key", "value")
        self.assertIn("key", entry.display)
        self.assertIn("value", entry.display)

    def test_is_numeric(self):
        entry = ConfigEntry("num", "42")
        self.assertTrue(entry.is_numeric)

    def test_has_options(self):
        entry = ConfigEntry("opt", "a", options=["a", "b", "c"])
        self.assertTrue(entry.has_options)


# ─── Notification Center Tests ───────────────────────────────────────────


class TestNotificationCenter(unittest.TestCase):

    def setUp(self):
        self.nc = NotificationCenter()

    def test_initial_state(self):
        self.assertEqual(self.nc.view_mode, "notifications")
        self.assertGreater(len(self.nc.notifications), 0)
        self.assertGreater(self.nc.unread_count, 0)

    def test_add_notification(self):
        notif = self.nc.add_notification("Test", "Test body", "Test App")
        self.assertIsNotNone(notif)
        self.assertIn(notif, self.nc.notifications)

    def test_dismiss(self):
        initial = self.nc.unread_count
        self.assertTrue(self.nc.dismiss(0))

    def test_mark_read(self):
        initial = self.nc.unread_count
        self.assertTrue(self.nc.mark_read(0))
        self.assertLess(self.nc.unread_count, initial + 1)

    def test_pin(self):
        self.assertTrue(self.nc.pin(0))

    def test_snooze(self):
        self.assertTrue(self.nc.snooze(0, 30))

    def test_dismiss_all(self):
        count = self.nc.dismiss_all()
        self.assertGreaterEqual(count, 0)

    def test_toggle_dnd(self):
        result = self.nc.toggle_dnd()
        self.assertTrue(result)
        self.assertTrue(self.nc.dnd_enabled)
        result = self.nc.toggle_dnd()
        self.assertFalse(result)

    def test_get_groups(self):
        groups = self.nc.get_groups()
        self.assertIsInstance(groups, list)
        self.assertGreater(len(groups), 0)

    def test_navigation(self):
        self.nc.select_down()
        self.assertEqual(self.nc.selected_index, 1)
        self.nc.select_up()
        self.assertEqual(self.nc.selected_index, 0)

    def test_render_notifications(self):
        lines = self.nc.render_notifications()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_history(self):
        self.nc.set_view("history")
        lines = self.nc.render_history()
        self.assertIsInstance(lines, list)

    def test_render_apps(self):
        self.nc.set_view("apps")
        lines = self.nc.render_apps()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.nc.handle_key("x")
        self.assertEqual(result, "dnd_on")


class TestNotification(unittest.TestCase):

    def test_display(self):
        n = Notification("Test Title", "body", "App")
        self.assertIn("Test Title", n.display)

    def test_preview(self):
        n = Notification("Title", "Short body", "MyApp")
        self.assertIn("MyApp", n.preview)

    def test_is_active(self):
        n = Notification("Title", status=NotificationStatus.NEW)
        self.assertTrue(n.is_active)


class TestAppNotificationSettings(unittest.TestCase):

    def test_display(self):
        s = AppNotificationSettings("Test", enabled=True, count=5)
        self.assertIn("Test", s.display)
        self.assertIn("[5]", s.display)


# ─── Workspace Manager Tests ─────────────────────────────────────────────


class TestWorkspaceManager(unittest.TestCase):

    def setUp(self):
        self.wm = WorkspaceManager()

    def test_initial_state(self):
        self.assertEqual(self.wm.view_mode, "workspaces")
        self.assertGreater(len(self.wm.workspaces), 0)
        self.assertGreater(len(self.wm.presets), 0)
        self.assertGreater(len(self.wm.monitors), 0)

    def test_create_workspace(self):
        ws = self.wm.create_workspace("New WS")
        self.assertIsNotNone(ws)
        self.assertEqual(ws.name, "New WS")
        self.assertEqual(len(self.wm.workspaces), 7)

    def test_delete_workspace(self):
        initial = len(self.wm.workspaces)
        self.assertTrue(self.wm.delete_workspace(initial - 1))
        self.assertEqual(len(self.wm.workspaces), initial - 1)

    def test_rename_workspace(self):
        self.assertTrue(self.wm.rename_workspace(0, "Renamed"))
        self.assertEqual(self.wm.workspaces[0].name, "Renamed")

    def test_set_tiling(self):
        self.assertTrue(self.wm.set_tiling(0, TilingMode.BSP))
        self.assertEqual(self.wm.workspaces[0].tiling_mode, TilingMode.BSP)

    def test_switch_workspace(self):
        self.assertTrue(self.wm.switch_workspace(2))
        self.assertEqual(self.wm.active_workspace, 2)

    def test_apply_preset(self):
        self.assertTrue(self.wm.apply_preset(2))
        self.assertEqual(self.wm.workspaces[0].tiling_mode, TilingMode.COLUMNS)

    def test_add_window(self):
        win = self.wm.add_window(0, "Test Window", "TestApp")
        self.assertIsNotNone(win)
        self.assertEqual(len(self.wm.workspaces[0].windows), 3)

    def test_remove_window(self):
        initial = self.wm.workspaces[0].window_count
        self.assertTrue(self.wm.remove_window(0, 0))
        self.assertEqual(self.wm.workspaces[0].window_count, initial - 1)

    def test_focus_window(self):
        self.assertTrue(self.wm.focus_window(0, 1))
        self.assertTrue(self.wm.workspaces[0].windows[1].focused)

    def test_move_window(self):
        initial_ws0 = self.wm.workspaces[0].window_count
        initial_ws2 = self.wm.workspaces[2].window_count
        self.assertTrue(self.wm.move_window_to_workspace(0, 0, 2))
        self.assertEqual(self.wm.workspaces[0].window_count, initial_ws0 - 1)
        self.assertEqual(self.wm.workspaces[2].window_count, initial_ws2 + 1)

    def test_navigation(self):
        self.wm.select_ws_down()
        self.assertEqual(self.wm.selected_ws, 1)
        self.wm.select_ws_up()
        self.assertEqual(self.wm.selected_ws, 0)

    def test_total_windows(self):
        total = self.wm.total_windows
        self.assertGreater(total, 0)

    def test_render_workspaces(self):
        lines = self.wm.render_workspaces()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_windows(self):
        self.wm.set_view("windows")
        lines = self.wm.render_windows()
        self.assertIsInstance(lines, list)

    def test_render_presets(self):
        self.wm.set_view("presets")
        lines = self.wm.render_presets()
        self.assertIsInstance(lines, list)

    def test_render_monitors(self):
        self.wm.set_view("monitors")
        lines = self.wm.render_monitors()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.wm.handle_key("Enter")
        self.assertEqual(result, "switch")


class TestWorkspace(unittest.TestCase):

    def test_display(self):
        ws = Workspace("Test", 1, TilingMode.BSP)
        self.assertIn("Test", ws.display)
        self.assertIn("⊡", ws.display)

    def test_focused_title(self):
        ws = Workspace("Test", 1)
        ws.windows = [WorkspaceWindow("Focused Win", focused=True)]
        self.assertEqual(ws.focused_title, "Focused Win")


class TestWorkspaceWindow(unittest.TestCase):

    def test_display(self):
        win = WorkspaceWindow("Test Win", focused=True)
        self.assertIn("Test Win", win.display)
        self.assertIn("◆", win.display)


class TestTilingPreset(unittest.TestCase):

    def test_display(self):
        preset = TilingPreset("Test", TilingMode.BSP)
        self.assertIn("Test", preset.display)
        self.assertIn("bsp", preset.display)


class TestMonitor(unittest.TestCase):

    def test_display(self):
        mon = Monitor("LG", MonitorRole.PRIMARY, 2560, 1440, 144)
        self.assertIn("LG", mon.display)
        self.assertIn("2560×1440", mon.display)


if __name__ == "__main__":
    unittest.main()
