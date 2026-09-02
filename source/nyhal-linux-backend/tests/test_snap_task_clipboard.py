#!/usr/bin/env python3
"""Tests for ui.snap_overlay, ui.task_switcher, and ui.clipboard."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.snap_overlay import SnapOverlay, SnapZone, GhostRect
from ui.task_switcher import TaskSwitcher, SwitcherEntry
from ui.clipboard import ClipboardManager, ClipboardEntry, ClipboardType


# ---------------------------------------------------------------------------
# Snap Overlay Tests
# ---------------------------------------------------------------------------

class TestGhostRect(unittest.TestCase):
    def test_creation(self):
        g = GhostRect(SnapZone.LEFT, 0, 0, 960, 1032)
        self.assertEqual(g.zone, SnapZone.LEFT)
        self.assertEqual(g.rect, (0, 0, 960, 1032))

    def test_center(self):
        g = GhostRect(SnapZone.CENTER, 100, 100, 400, 300)
        self.assertEqual(g.center, (300, 250))


class TestSnapOverlay(unittest.TestCase):
    def setUp(self):
        self.overlay = SnapOverlay(1920, 1080, 48)

    def test_creation(self):
        self.assertIsNotNone(self.overlay)
        self.assertFalse(self.overlay.is_visible)

    def test_show_zone(self):
        self.overlay.show(SnapZone.LEFT)
        self.assertTrue(self.overlay.is_visible)
        self.assertEqual(self.overlay.active_zone, SnapZone.LEFT)
        self.assertIsNotNone(self.overlay.ghost)

    def test_hide(self):
        self.overlay.show(SnapZone.RIGHT)
        self.overlay.hide()
        self.assertFalse(self.overlay.is_visible)
        self.assertIsNone(self.overlay.ghost)

    def test_update_fade_in(self):
        self.overlay.show(SnapZone.LEFT)
        animating = self.overlay.update()
        self.assertTrue(animating or self.overlay.alpha > 0)

    def test_zone_rects(self):
        self.assertIn(SnapZone.LEFT, self.overlay._zone_rects)
        self.assertIn(SnapZone.RIGHT, self.overlay._zone_rects)
        self.assertIn(SnapZone.MAXIMIZE, self.overlay._zone_rects)

    def test_render_invisible(self):
        pixels, w, h = self.overlay.render()
        self.assertEqual(pixels, [])
        self.assertEqual(w, 0)

    def test_render_visible(self):
        self.overlay.show(SnapZone.LEFT)
        self.overlay._alpha = 1.0
        pixels, w, h = self.overlay.render()
        self.assertEqual(w, 1920)
        self.assertEqual(h, 1080)
        self.assertEqual(len(pixels), w * h)

    def test_render_rgb(self):
        self.overlay.show(SnapZone.RIGHT)
        self.overlay._alpha = 1.0
        data, w, h = self.overlay.render_to_rgb()
        self.assertEqual(len(data), w * h * 3)

    def test_get_zone_at(self):
        # Zones overlap, so get_zone_at returns the first match
        zone = self.overlay.get_zone_at(100, 100)
        self.assertIn(zone, [SnapZone.LEFT, SnapZone.TOP, SnapZone.TOP_LEFT, SnapZone.MAXIMIZE])

    def test_maximize_color(self):
        self.overlay.show(SnapZone.MAXIMIZE)
        self.assertEqual(self.overlay.ghost.color, self.overlay.MAXIMIZE_COLOR)


# ---------------------------------------------------------------------------
# Task Switcher Tests
# ---------------------------------------------------------------------------

class TestSwitcherEntry(unittest.TestCase):
    def test_creation(self):
        e = SwitcherEntry(window_id="w1", title="Terminal")
        self.assertEqual(e.window_id, "w1")
        self.assertEqual(e.title, "Terminal")


class TestTaskSwitcher(unittest.TestCase):
    def setUp(self):
        self.ts = TaskSwitcher(1920, 1080)
        self.entries = [
            SwitcherEntry("w1", "Terminal", "Terminal"),
            SwitcherEntry("w2", "Files", "Files"),
            SwitcherEntry("w3", "Browser", "Browser"),
        ]

    def test_creation(self):
        self.assertIsNotNone(self.ts)
        self.assertFalse(self.ts.is_visible)

    def test_show(self):
        self.ts.show(self.entries)
        self.assertTrue(self.ts.is_visible)
        self.assertEqual(len(self.ts.entries), 3)

    def test_hide(self):
        self.ts.show(self.entries)
        self.ts.hide()
        self.assertFalse(self.ts.is_visible)

    def test_next(self):
        self.ts.show(self.entries)
        entry = self.ts.next()
        self.assertEqual(entry.window_id, "w2")
        entry = self.ts.next()
        self.assertEqual(entry.window_id, "w3")

    def test_prev(self):
        self.ts.show(self.entries)
        self.ts.next()
        entry = self.ts.prev()
        self.assertEqual(entry.window_id, "w1")

    def test_next_wraps(self):
        self.ts.show(self.entries)
        self.ts.next()
        self.ts.next()
        entry = self.ts.next()
        self.assertEqual(entry.window_id, "w1")

    def test_select(self):
        self.ts.show(self.entries)
        entry = self.ts.select()
        self.assertIsNotNone(entry)
        self.assertFalse(self.ts.is_visible)

    def test_handle_key_tab(self):
        self.ts.show(self.entries)
        result = self.ts.handle_key("Tab")
        self.assertEqual(result, "next")

    def test_handle_key_shift_tab(self):
        self.ts.show(self.entries)
        result = self.ts.handle_key("Tab", {"shift": True})
        self.assertEqual(result, "prev")

    def test_handle_key_enter(self):
        self.ts.show(self.entries)
        result = self.ts.handle_key("Enter")
        self.assertTrue(result.startswith("select:"))

    def test_handle_key_escape(self):
        self.ts.show(self.entries)
        result = self.ts.handle_key("Escape")
        self.assertEqual(result, "cancel")

    def test_render_empty(self):
        pixels, w, h = self.ts.render()
        self.assertEqual(pixels, [])

    def test_render_with_entries(self):
        self.ts.show(self.entries)
        self.ts._alpha = 1.0
        pixels, w, h = self.ts.render()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_render_rgb(self):
        self.ts.show(self.entries)
        self.ts._alpha = 1.0
        data, w, h = self.ts.render_to_rgb()
        self.assertEqual(len(data), w * h * 3)

    def test_update_animation(self):
        self.ts.show(self.entries)
        self.ts._alpha = 0.5
        animating = self.ts.update()
        self.assertTrue(animating or self.ts._alpha >= 0.5)

    def test_selected_entry(self):
        self.ts.show(self.entries)
        entry = self.ts.selected_entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.window_id, "w1")


# ---------------------------------------------------------------------------
# Clipboard Tests
# ---------------------------------------------------------------------------

class TestClipboardEntry(unittest.TestCase):
    def test_creation(self):
        e = ClipboardEntry(id="c1", content="Hello World")
        self.assertEqual(e.id, "c1")
        self.assertEqual(e.content, "Hello World")
        self.assertEqual(e.size, 11)

    def test_time_ago(self):
        e = ClipboardEntry(id="c1", content="Test", timestamp=time.time() - 120)
        self.assertEqual(e.time_ago, "2m ago")

    def test_preview(self):
        e = ClipboardEntry(id="c1", content="A" * 100)
        self.assertEqual(len(e.preview), 63)  # 60 + "..."

    def test_label_auto(self):
        e = ClipboardEntry(id="c1", content="First line\nSecond line")
        self.assertEqual(e.label, "First line")

    def test_display_size(self):
        e = ClipboardEntry(id="c1", content="A" * 500)
        self.assertEqual(e.display_size, "500 B")
        e2 = ClipboardEntry(id="c2", content="A" * 2048)
        self.assertEqual(e2.display_size, "2.0 KB")


class TestClipboardManager(unittest.TestCase):
    def setUp(self):
        self.cb = ClipboardManager(max_history=50)

    def test_creation(self):
        self.assertIsNotNone(self.cb)
        self.assertEqual(self.cb.entry_count, 0)

    def test_copy(self):
        entry = self.cb.copy("Hello")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.content, "Hello")
        self.assertEqual(self.cb.entry_count, 1)

    def test_copy_duplicate(self):
        self.cb.copy("Hello")
        self.cb.copy("Hello")
        self.assertEqual(self.cb.entry_count, 1)

    def test_paste(self):
        entry = self.cb.copy("Hello")
        content = self.cb.paste(entry.id)
        self.assertEqual(content, "Hello")

    def test_paste_last(self):
        self.cb.copy("First")
        self.cb.copy("Second")
        content = self.cb.paste_last()
        self.assertEqual(content, "Second")

    def test_remove(self):
        entry = self.cb.copy("Hello")
        self.assertTrue(self.cb.remove(entry.id))
        self.assertEqual(self.cb.entry_count, 0)

    def test_clear(self):
        self.cb.copy("A")
        self.cb.copy("B")
        count = self.cb.clear()
        self.assertEqual(self.cb.entry_count, 0)

    def test_pin(self):
        entry = self.cb.copy("Important")
        result = self.cb.pin(entry.id)
        self.assertTrue(result)
        self.assertTrue(self.cb.find_entry(entry.id).pinned)

    def test_pin_prevents_clear(self):
        entry = self.cb.copy("Important")
        self.cb.pin(entry.id)
        self.cb.copy("Temp")
        self.cb.clear()
        self.assertEqual(self.cb.entry_count, 1)
        self.assertEqual(self.cb.entries[0].content, "Important")

    def test_max_history(self):
        cb = ClipboardManager(max_history=5)
        for i in range(10):
            cb.copy(f"Item {i}")
        self.assertEqual(cb.entry_count, 5)

    def test_search(self):
        self.cb.copy("Hello World")
        self.cb.copy("Goodbye World")
        self.cb.set_search("hello")
        self.assertEqual(len(self.cb.filtered_entries), 1)

    def test_search_empty(self):
        self.cb.copy("A")
        self.cb.copy("B")
        self.cb.set_search("")
        self.assertEqual(len(self.cb.filtered_entries), 2)

    def test_navigation(self):
        self.cb.copy("A")
        self.cb.copy("B")
        self.cb.copy("C")
        self.cb.show()
        self.cb.move_down()
        self.cb.move_down()
        self.assertEqual(self.cb.selected_index, 2)
        self.cb.move_up()
        self.assertEqual(self.cb.selected_index, 1)

    def test_select(self):
        self.cb.copy("Hello")
        self.cb.show()
        entry = self.cb.select()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.content, "Hello")

    def test_show_hide(self):
        self.cb.show()
        self.assertTrue(self.cb.is_visible)
        self.cb.hide()
        self.assertFalse(self.cb.is_visible)

    def test_toggle(self):
        self.cb.toggle()
        self.assertTrue(self.cb.is_visible)
        self.cb.toggle()
        self.assertFalse(self.cb.is_visible)

    def test_handle_key(self):
        self.cb.copy("Test")
        self.cb.show()
        self.cb.handle_key("Down")
        self.cb.handle_key("Up")
        self.cb.handle_key("Escape")
        self.assertFalse(self.cb.is_visible)

    def test_handle_key_search(self):
        self.cb.show()
        self.cb.handle_key("a")
        self.cb.handle_key("b")
        self.assertEqual(self.cb.search_query, "ab")
        self.cb.handle_key("BackSpace")
        self.assertEqual(self.cb.search_query, "a")

    def test_handle_key_pin(self):
        entry = self.cb.copy("Test")
        self.cb.show()
        self.cb.handle_key("p")
        self.assertTrue(self.cb.find_entry(entry.id).pinned)

    def test_get_recent(self):
        self.cb.copy("A")
        self.cb.copy("B")
        recent = self.cb.get_recent(1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].content, "B")

    def test_pinned_count(self):
        self.cb.copy("A")
        entry = self.cb.copy("B")
        self.cb.pin(entry.id)
        self.assertEqual(self.cb.pinned_count, 1)

    def test_use_count(self):
        entry = self.cb.copy("Hello")
        self.cb.paste(entry.id)
        self.cb.paste(entry.id)
        self.assertEqual(entry.use_count, 2)

    def test_callbacks(self):
        copied = []
        self.cb.on_copy(lambda e: copied.append(e))
        self.cb.copy("Test")
        self.assertEqual(len(copied), 1)


if __name__ == "__main__":
    unittest.main()
