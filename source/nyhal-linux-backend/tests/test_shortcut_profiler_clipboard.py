"""
Tests for Shortcut Editor, Live Profiler, and Clipboard Pro.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.shortcut_editor import (
    ShortcutEditor, ShortcutBinding, ShortcutProfile,
    ShortcutScope, ModifierKey
)
from ui.live_profiler import (
    LiveProfiler, ProcessInfo, SystemLoad, ProcessState
)
from ui.clipboard_pro import (
    ClipboardPro, ClipboardItem, SnippetTemplate,
    ClipCategory
)


class TestShortcutEditor(unittest.TestCase):

    def setUp(self):
        self.se = ShortcutEditor()

    def test_initial_state(self):
        self.assertEqual(self.se.view_mode, "shortcuts")
        self.assertGreater(len(self.se._bindings), 0)

    def test_conflict_detection(self):
        # No conflicts by default in sample data
        self.assertEqual(self.se.total_conflicts, 0)

    def test_toggle_binding(self):
        self.assertTrue(self.se.toggle_binding(0))
        self.assertFalse(self.se._bindings[0].enabled)

    def test_reset_to_default(self):
        self.se._bindings[0].is_custom = True
        self.assertTrue(self.se.reset_to_default(0))
        self.assertFalse(self.se._bindings[0].is_custom)

    def test_save_profile(self):
        profile = self.se.save_profile("Test Profile")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "Test Profile")

    def test_navigation(self):
        self.se.select_down()
        self.assertEqual(self.se.selected_index, 1)
        self.se.select_up()
        self.assertEqual(self.se.selected_index, 0)

    def test_render_shortcuts(self):
        lines = self.se.render_shortcuts()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_conflicts(self):
        self.se.set_view("conflicts")
        lines = self.se.render_conflicts()
        self.assertIsInstance(lines, list)

    def test_render_profiles(self):
        self.se.set_view("profiles")
        lines = self.se.render_profiles()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.se.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestShortcutBinding(unittest.TestCase):

    def test_display_key(self):
        b = ShortcutBinding("test", "Test", modifiers=[ModifierKey.CTRL, ModifierKey.SHIFT], key="a")
        self.assertEqual(b.display_key, "Ctrl+Shift+a")

    def test_display(self):
        b = ShortcutBinding("test", "Test", scope=ShortcutScope.SYSTEM,
                            modifiers=[ModifierKey.CTRL], key="t")
        self.assertIn("Test", b.display)
        self.assertIn("Ctrl+t", b.display)


class TestLiveProfiler(unittest.TestCase):

    def setUp(self):
        self.lp = LiveProfiler()

    def test_initial_state(self):
        self.assertEqual(self.lp.view_mode, "overview")
        self.assertGreater(len(self.lp._processes), 0)

    def test_load_stats(self):
        load = self.lp.load
        self.assertGreater(load.load_1, 0)
        self.assertGreater(load.total_processes, 0)

    def test_sorted_processes(self):
        procs = self.lp.get_sorted_processes()
        self.assertEqual(len(procs), len(self.lp._processes))
        # Should be sorted by CPU descending
        self.assertGreaterEqual(procs[0].cpu_pct, procs[-1].cpu_pct)

    def test_tree(self):
        tree = self.lp.get_tree()
        self.assertGreater(len(tree), 0)
        # Root processes should have depth 0
        self.assertEqual(tree[0][0], 0)

    def test_navigation(self):
        self.lp.set_view("processes")
        self.lp.select_down()
        self.assertEqual(self.lp.selected_index, 1)
        self.lp.select_up()
        self.assertEqual(self.lp.selected_index, 0)

    def test_render_overview(self):
        lines = self.lp.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_processes(self):
        self.lp.set_view("processes")
        lines = self.lp.render_processes()
        self.assertIsInstance(lines, list)

    def test_render_tree(self):
        self.lp.set_view("tree")
        lines = self.lp.render_tree()
        self.assertIsInstance(lines, list)

    def test_render_io(self):
        self.lp.set_view("io")
        lines = self.lp.render_io()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.lp.handle_key("p")
        self.assertEqual(result, "processes")


class TestProcessInfo(unittest.TestCase):

    def test_display(self):
        p = ProcessInfo(1234, "test-proc", ProcessState.RUNNING, cpu_pct=50.0, mem_mb=512)
        self.assertIn("test-proc", p.display)
        self.assertIn("50.0%", p.display)

    def test_sparkline(self):
        p = ProcessInfo(1, "test")
        p.cpu_history = [10, 20, 30, 40, 50]
        spark = p.cpu_sparkline
        self.assertIsInstance(spark, str)


class TestSystemLoad(unittest.TestCase):

    def test_uptime_str(self):
        load = SystemLoad(uptime_seconds=90000)
        self.assertIn("d", load.uptime_str)

    def test_load_bar(self):
        load = SystemLoad(load_1=8.0)
        bar = load.load_bar
        self.assertIn("█", bar)


class TestClipboardPro(unittest.TestCase):

    def setUp(self):
        self.cp = ClipboardPro()

    def test_initial_state(self):
        self.assertEqual(self.cp.view_mode, "history")
        self.assertGreater(self.cp.total_items, 0)

    def test_copy_item(self):
        item = self.cp.copy_item("test content")
        self.assertIsNotNone(item)
        self.assertEqual(item.content, "test content")

    def test_pin_item(self):
        self.assertTrue(self.cp.pin_item(0))

    def test_favorite_item(self):
        self.assertTrue(self.cp.favorite_item(0))

    def test_delete_item(self):
        initial = self.cp.total_items
        self.assertTrue(self.cp.delete_item(0))
        self.assertEqual(self.cp.total_items, initial - 1)

    def test_use_snippet(self):
        result = self.cp.use_snippet(0)
        self.assertIsNotNone(result)
        self.assertIn("git", result)

    def test_add_snippet(self):
        snippet = self.cp.add_snippet("Test Snippet", "content")
        self.assertIsNotNone(snippet)
        self.assertEqual(snippet.name, "Test Snippet")

    def test_navigation(self):
        self.cp.select_down()
        self.assertEqual(self.cp.selected_index, 1)
        self.cp.select_up()
        self.assertEqual(self.cp.selected_index, 0)

    def test_render_history(self):
        lines = self.cp.render_history()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_snippets(self):
        self.cp.set_view("snippets")
        lines = self.cp.render_snippets()
        self.assertIsInstance(lines, list)

    def test_render_favorites(self):
        self.cp.set_view("favorites")
        lines = self.cp.render_favorites()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.cp.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestClipboardItem(unittest.TestCase):

    def test_display(self):
        item = ClipboardItem("Hello World", ClipCategory.TEXT)
        self.assertIn("Hello World", item.display)

    def test_size_str(self):
        item = ClipboardItem("x" * 2000)
        self.assertIn("KB", item.size_str)


class TestSnippetTemplate(unittest.TestCase):

    def test_display(self):
        s = SnippetTemplate("Test", "content", shortcut="Ctrl+T")
        self.assertIn("Test", s.display)
        self.assertIn("Ctrl+T", s.display)

    def test_render(self):
        s = SnippetTemplate("Test", "Hello ${name}!")
        result = s.render({"name": "World"})
        self.assertEqual(result, "Hello World!")


if __name__ == "__main__":
    unittest.main()
