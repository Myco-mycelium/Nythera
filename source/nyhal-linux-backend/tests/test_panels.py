#!/usr/bin/env python3
"""Tests for ui.notifications and ui.context_menu."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.notifications import (
    NotificationShade,
    QuickSettings,
    Notification,
    NotificationSeverity,
    PanelState,
    QuickToggle,
)

from ui.context_menu import (
    ContextMenu,
    MenuItem,
    MenuItemType,
    desktop_context_menu,
    window_context_menu,
    file_context_menu,
    taskbar_context_menu,
)


# ---------------------------------------------------------------------------
# Notification Tests
# ---------------------------------------------------------------------------

class TestNotification(unittest.TestCase):
    """Tests for Notification dataclass."""

    def test_creation(self):
        n = Notification(id="n1", title="Test")
        self.assertEqual(n.id, "n1")
        self.assertEqual(n.title, "Test")
        self.assertFalse(n.dismissed)

    def test_notification_fields(self):
        n = Notification(id="n1", title="Hello", message="World")
        self.assertEqual(n.title, "Hello")
        self.assertEqual(n.message, "World")


class TestNotificationShade(unittest.TestCase):
    """Tests for NotificationShade."""

    def setUp(self):
        self.shade = NotificationShade()

    def test_creation(self):
        self.assertIsNotNone(self.shade)
        self.assertEqual(self.shade.notification_count, 0)

    def test_add_notification(self):
        n = Notification(id="n1", title="Hello")
        self.shade.add_notification(n)
        self.assertEqual(self.shade.notification_count, 1)

    def test_remove_notification(self):
        self.shade.add_notification(Notification(id="n1", title="Test"))
        result = self.shade.remove_notification("n1")
        self.assertTrue(result)
        self.assertEqual(self.shade.notification_count, 0)

    def test_dismiss_notification(self):
        self.shade.add_notification(Notification(id="n1", title="Test"))
        result = self.shade.dismiss_notification("n1")
        self.assertTrue(result)
        self.assertEqual(self.shade.notification_count, 0)

    def test_clear_all(self):
        self.shade.add_notification(Notification(id="n1", title="A"))
        self.shade.add_notification(Notification(id="n2", title="B"))
        count = self.shade.clear_all()
        self.assertEqual(count, 2)
        self.assertEqual(self.shade.notification_count, 0)

    def test_panel_state(self):
        self.assertFalse(self.shade.is_open)
        self.shade.open()
        self.assertTrue(self.shade.is_open)
        self.shade.close()
        self.assertFalse(self.shade.is_open)

    def test_toggle(self):
        self.shade.toggle()
        self.assertTrue(self.shade.is_open)
        self.shade.toggle()
        self.assertFalse(self.shade.is_open)

    def test_handle_tap_outside(self):
        self.shade.open()
        result = self.shade.handle_tap(500, 500)
        self.assertEqual(result, "close")

    def test_handle_tap_clear_all(self):
        self.shade.add_notification(Notification(id="n1", title="Test"))
        self.shade.open()
        # Tap on clear all button area
        h = self.shade._panel_height
        result = self.shade.handle_tap(100, h - 30)
        self.assertEqual(result, "clear_all")
        self.assertEqual(self.shade.notification_count, 0)

    def test_render_empty(self):
        self.shade.open()
        pixels, w, h = self.shade.render()
        self.assertGreater(h, 0)

    def test_render_with_notifications(self):
        from ui.notifications import NotificationSeverity
        self.shade.add_notification(Notification(id="n1", title="Hello World", severity=NotificationSeverity.INFO))
        self.shade.add_notification(Notification(id="n2", title="Test Alert", severity=NotificationSeverity.WARNING))
        self.shade.open()
        pixels, w, h = self.shade.render()
        self.assertGreater(h, 0)

    def test_notification_order(self):
        self.shade.add_notification(Notification(id="n1", title="First"))
        self.shade.add_notification(Notification(id="n2", title="Second"))
        # New notifications should be first
        self.assertEqual(self.shade.notifications[0].id, "n2")


# ---------------------------------------------------------------------------
# Quick Settings Tests
# ---------------------------------------------------------------------------

class TestQuickToggle(unittest.TestCase):
    """Tests for QuickToggle dataclass."""

    def test_creation(self):
        t = QuickToggle(label="WiFi", icon_letter="W")
        self.assertEqual(t.label, "WiFi")
        self.assertFalse(t.enabled)

    def test_enabled(self):
        t = QuickToggle(label="BT", icon_letter="B", enabled=True)
        self.assertTrue(t.enabled)


class TestQuickSettings(unittest.TestCase):
    """Tests for QuickSettings."""

    def setUp(self):
        self.qs = QuickSettings()

    def test_creation(self):
        self.assertIsNotNone(self.qs)
        self.assertEqual(len(self.qs.toggles), 6)

    def test_toggle_setting(self):
        result = self.qs.toggle_setting(0)
        self.assertFalse(result)  # Was True, now False

    def test_toggle_out_of_bounds(self):
        result = self.qs.toggle_setting(99)
        self.assertFalse(result)

    def test_brightness(self):
        self.qs.brightness = 50
        self.assertEqual(self.qs.brightness, 50)

    def test_brightness_clamp(self):
        self.qs.brightness = 150
        self.assertEqual(self.qs.brightness, 100)

    def test_volume(self):
        self.qs.volume = 30
        self.assertEqual(self.qs.volume, 30)

    def test_panel_state(self):
        self.assertFalse(self.qs.is_open)
        self.qs.open()
        self.assertTrue(self.qs.is_open)
        self.qs.close()
        self.assertFalse(self.qs.is_open)

    def test_toggle_panel(self):
        self.qs.toggle_panel()
        self.assertTrue(self.qs.is_open)
        self.qs.toggle_panel()
        self.assertFalse(self.qs.is_open)

    def test_handle_tap_outside(self):
        self.qs.open()
        result = self.qs.handle_tap(100, 100)
        self.assertEqual(result, "close")

    def test_render(self):
        self.qs.open()
        pixels, w, h = self.qs.render()
        self.assertGreater(h, 0)


# ---------------------------------------------------------------------------
# Context Menu Tests
# ---------------------------------------------------------------------------

class TestMenuItem(unittest.TestCase):
    """Tests for MenuItem dataclass."""

    def test_creation(self):
        item = MenuItem(label="Test")
        self.assertEqual(item.label, "Test")
        self.assertTrue(item.is_selectable)

    def test_separator(self):
        item = MenuItem("", MenuItemType.SEPARATOR)
        self.assertFalse(item.is_selectable)

    def test_header(self):
        item = MenuItem("Header", MenuItemType.HEADER)
        self.assertFalse(item.is_selectable)

    def test_checkbox(self):
        item = MenuItem("Check", MenuItemType.CHECKBOX)
        self.assertTrue(item.is_selectable)
        self.assertFalse(item.checked)


class TestContextMenu(unittest.TestCase):
    """Tests for ContextMenu."""

    def setUp(self):
        self.menu = ContextMenu(items=[
            MenuItem("Cut", MenuItemType.ACTION, shortcut="Ctrl+X"),
            MenuItem("Copy", MenuItemType.ACTION, shortcut="Ctrl+C"),
            MenuItem("", MenuItemType.SEPARATOR),
            MenuItem("Paste", MenuItemType.ACTION, shortcut="Ctrl+V", enabled=False),
            MenuItem("Select All", MenuItemType.ACTION, shortcut="Ctrl+A"),
        ])

    def test_creation(self):
        self.assertIsNotNone(self.menu)
        self.assertEqual(len(self.menu.items), 5)

    def test_show_hide(self):
        self.menu.show(100, 200)
        self.assertTrue(self.menu.is_visible)
        self.assertEqual(self.menu.position, (100, 200))
        self.menu.hide()
        self.assertFalse(self.menu.is_visible)

    def test_navigate_up(self):
        self.menu.show(0, 0)
        self.menu.move_down()
        self.menu.move_up()
        self.assertEqual(self.menu.selected_index, 0)

    def test_navigate_down(self):
        self.menu.show(0, 0)
        self.menu.move_down()
        self.assertEqual(self.menu.selected_index, 1)

    def test_navigate_skips_separator(self):
        self.menu.show(0, 0)
        self.menu.move_down()  # -> Cut (1)
        self.menu.move_down()  # -> Copy (2)
        self.menu.move_down()  # -> Select All (4, skips separator at 3)
        self.assertEqual(self.menu.selected_index, 4)

    def test_activate_action(self):
        self.menu.show(0, 0)
        called = [False]
        def on_cut():
            called[0] = True
        self.menu.items[0].action = on_cut
        result = self.menu.activate_selected()
        self.assertEqual(result, "action:Cut")
        self.assertTrue(called[0])

    def test_activate_checkbox(self):
        menu = ContextMenu(items=[
            MenuItem("Option", MenuItemType.CHECKBOX),
        ])
        menu.show(0, 0)
        result = menu.activate_selected()
        self.assertEqual(result, "check:Option")
        self.assertTrue(menu.items[0].checked)

    def test_handle_key_escape(self):
        self.menu.show(0, 0)
        result = self.menu.handle_key("Escape")
        self.assertEqual(result, "close")
        self.assertFalse(self.menu.is_visible)

    def test_handle_key_up_down(self):
        self.menu.show(0, 0)
        self.menu.handle_key("Down")
        self.menu.handle_key("Down")
        result = self.menu.handle_key("Up")
        self.assertEqual(result, "navigate")

    def test_handle_click_outside(self):
        self.menu.show(100, 100)
        result = self.menu.handle_click(500, 500)
        self.assertEqual(result, "close")

    def test_size(self):
        w, h = self.menu.size
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_render(self):
        self.menu.show(0, 0)
        pixels, w, h = self.menu.render()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_add_item(self):
        self.menu.add_item(MenuItem("New"))
        self.assertEqual(len(self.menu.items), 6)

    def test_add_separator(self):
        self.menu.add_separator()
        self.assertEqual(len(self.menu.items), 6)

    def test_add_header(self):
        self.menu.add_header("Actions")
        self.assertEqual(len(self.menu.items), 6)


class TestPrebuiltMenus(unittest.TestCase):
    """Tests for pre-built context menus."""

    def test_desktop_menu(self):
        menu = desktop_context_menu()
        self.assertGreater(len(menu.items), 0)

    def test_window_menu(self):
        menu = window_context_menu()
        self.assertGreater(len(menu.items), 0)

    def test_file_menu(self):
        menu = file_context_menu()
        self.assertGreater(len(menu.items), 0)

    def test_directory_menu(self):
        menu = file_context_menu(is_dir=True)
        self.assertGreater(len(menu.items), 0)

    def test_taskbar_menu(self):
        menu = taskbar_context_menu()
        self.assertGreater(len(menu.items), 0)

    def test_all_menus_render(self):
        for menu in [desktop_context_menu(), window_context_menu(),
                     file_context_menu(), taskbar_context_menu()]:
            menu.show(100, 100)
            pixels, w, h = menu.render()
            self.assertGreater(w, 0)


if __name__ == "__main__":
    unittest.main()
