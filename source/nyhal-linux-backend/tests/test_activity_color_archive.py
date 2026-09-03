"""Tests for activity history, color picker, and archive manager."""

import os
import tempfile
import time
import unittest
import zipfile

from ui.activity_history import (
    ActivityHistory, ActivityEntry, ActivityType, SortOrder,
)
from ui.color_picker import (
    ColorPicker, Color, PALETTES,
)
from ui.archive_manager import (
    ArchiveManager, ArchiveEntry, ArchiveOperation,
    ArchiveFormat, CompressionLevel, ArchiveState,
)


# ---------------------------------------------------------------------------
# ActivityHistory tests
# ---------------------------------------------------------------------------

class TestActivityHistory(unittest.TestCase):
    """Tests for ActivityHistory."""

    def setUp(self):
        self.ah = ActivityHistory(max_entries=100)

    def test_initialization(self):
        self.assertEqual(len(self.ah.entries), 0)

    def test_record_file(self):
        entry = self.ah.record_file("/home/user/doc.txt", "Editor")
        self.assertEqual(entry.activity_type, ActivityType.FILE)
        self.assertEqual(entry.name, "doc.txt")
        self.assertEqual(entry.app_name, "Editor")
        self.assertEqual(len(self.ah.entries), 1)

    def test_record_file_dedup(self):
        """Same file recorded within 5 seconds should bump count."""
        self.ah.record_file("/tmp/test.txt")
        entry2 = self.ah.record_file("/tmp/test.txt")
        self.assertEqual(entry2.access_count, 2)
        self.assertEqual(len(self.ah.entries), 1)

    def test_record_app(self):
        entry = self.ah.record_app("Terminal", (60, 200, 120, 255))
        self.assertEqual(entry.activity_type, ActivityType.APP)
        self.assertEqual(entry.name, "Terminal")

    def test_record_app_dedup(self):
        self.ah.record_app("Firefox")
        entry2 = self.ah.record_app("Firefox")
        self.assertEqual(entry2.access_count, 2)

    def test_record_folder(self):
        entry = self.ah.record_folder("/home/user/Documents")
        self.assertEqual(entry.activity_type, ActivityType.FOLDER)
        self.assertEqual(entry.name, "Documents")

    def test_record_url(self):
        entry = self.ah.record_url("https://example.com")
        self.assertEqual(entry.activity_type, ActivityType.URL)

    def test_record_command(self):
        entry = self.ah.record_command("ls -la")
        self.assertEqual(entry.activity_type, ActivityType.COMMAND)

    def test_recent_files(self):
        self.ah.record_file("/a.txt")
        self.ah.record_app("Firefox")
        self.ah.record_file("/b.txt")
        files = self.ah.recent_files
        self.assertEqual(len(files), 2)

    def test_recent_apps(self):
        self.ah.record_file("/a.txt")
        self.ah.record_app("Firefox")
        apps = self.ah.recent_apps
        self.assertEqual(len(apps), 1)

    def test_frequent_files(self):
        self.ah.record_file("/popular.txt")
        self.ah.record_file("/popular.txt")
        self.ah.record_file("/popular.txt")
        self.ah.record_file("/rare.txt")
        freq = self.ah.frequent_files
        self.assertEqual(freq[0].name, "popular.txt")
        self.assertEqual(freq[0].access_count, 3)

    def test_pin(self):
        entry = self.ah.record_file("/test.txt")
        self.assertTrue(self.ah.pin(entry.id))
        self.assertTrue(entry.pinned)
        self.assertFalse(self.ah.pin(entry.id))  # toggle off

    def test_remove(self):
        entry = self.ah.record_file("/test.txt")
        self.assertTrue(self.ah.remove(entry.id))
        self.assertEqual(len(self.ah.entries), 0)

    def test_clear(self):
        self.ah.record_file("/a.txt")
        self.ah.record_file("/b.txt")
        count = self.ah.clear()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.ah.entries), 0)

    def test_clear_preserves_pinned(self):
        e1 = self.ah.record_file("/a.txt")
        self.ah.record_file("/b.txt")
        self.ah.pin(e1.id)
        self.ah.clear()
        self.assertEqual(len(self.ah.entries), 1)
        self.assertTrue(self.ah.entries[0].pinned)

    def test_clear_by_type(self):
        self.ah.record_file("/a.txt")
        self.ah.record_app("Firefox")
        count = self.ah.clear_by_type(ActivityType.FILE)
        self.assertEqual(count, 1)
        self.assertEqual(len(self.ah.entries), 1)

    def test_search(self):
        self.ah.record_file("/hello.txt")
        self.ah.record_file("/world.txt")
        self.ah.set_search("hello")
        self.assertEqual(len(self.ah.filtered_entries), 1)

    def test_filter_type(self):
        self.ah.record_file("/a.txt")
        self.ah.record_app("Firefox")
        self.ah.set_filter(ActivityType.FILE)
        self.assertEqual(len(self.ah.filtered_entries), 1)

    def test_sort_frequent(self):
        self.ah.record_file("/a.txt")
        self.ah.record_file("/b.txt")
        self.ah.record_file("/b.txt")
        self.ah.set_sort(SortOrder.FREQUENT)
        entries = self.ah.filtered_entries
        self.assertEqual(entries[0].name, "b.txt")

    def test_sort_alphabetical(self):
        self.ah.record_file("/banana.txt")
        self.ah.record_file("/apple.txt")
        self.ah.set_sort(SortOrder.ALPHABETICAL)
        entries = self.ah.filtered_entries
        self.assertEqual(entries[0].name, "apple.txt")

    def test_navigation(self):
        self.ah.record_file("/a.txt")
        self.ah.record_file("/b.txt")
        self.ah.move_down()
        self.assertEqual(self.ah.selected_index, 1)
        self.ah.move_up()
        self.assertEqual(self.ah.selected_index, 0)

    def test_select(self):
        self.ah.record_file("/test.txt")
        entry = self.ah.select()
        self.assertIsNotNone(entry)

    def test_handle_key_down(self):
        self.ah.record_file("/a.txt")
        result = self.ah.handle_key("Down")
        self.assertEqual(result, "navigate")

    def test_handle_key_enter(self):
        self.ah.record_file("/a.txt")
        result = self.ah.handle_key("Enter")
        self.assertTrue(result.startswith("open:"))

    def test_handle_key_escape(self):
        result = self.ah.handle_key("Escape")
        self.assertEqual(result, "close")

    def test_handle_key_search(self):
        result = self.ah.handle_key("a")
        self.assertEqual(result, "search")
        self.assertEqual(self.ah._search_query, "a")

    def test_stats(self):
        self.ah.record_file("/a.txt")
        self.ah.record_app("Firefox")
        stats = self.ah.get_stats()
        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["apps"], 1)

    def test_render(self):
        self.ah.record_file("/test.txt")
        rgb, w, h = self.ah.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_max_entries(self):
        ah = ActivityHistory(max_entries=5)
        for i in range(10):
            ah.record_file(f"/file{i}.txt")
        self.assertTrue(len(ah.entries) <= 5)

    def test_entry_time_ago(self):
        entry = ActivityEntry(
            id="test", activity_type=ActivityType.FILE,
            name="test", timestamp=time.time() - 3600,
        )
        self.assertIn("h ago", entry.time_ago)


# ---------------------------------------------------------------------------
# ColorPicker tests
# ---------------------------------------------------------------------------

class TestColorPicker(unittest.TestCase):
    """Tests for ColorPicker."""

    def setUp(self):
        self.cp = ColorPicker()

    def test_initialization(self):
        self.assertEqual(self.cp.color.r, 80)
        self.assertEqual(self.cp.color.g, 140)
        self.assertEqual(self.cp.color.b, 255)

    def test_set_color(self):
        self.cp.set_color(255, 0, 0)
        self.assertEqual(self.cp.color.r, 255)
        self.assertEqual(self.cp.color.g, 0)
        self.assertEqual(self.cp.color.b, 0)

    def test_set_color_clamped(self):
        self.cp.set_color(300, -10, 128)
        self.assertEqual(self.cp.color.r, 255)
        self.assertEqual(self.cp.color.g, 0)

    def test_set_from_hex(self):
        self.assertTrue(self.cp.set_from_hex("#ff8800"))
        self.assertEqual(self.cp.color.r, 255)
        self.assertEqual(self.cp.color.g, 136)
        self.assertEqual(self.cp.color.b, 0)

    def test_set_from_hex_no_hash(self):
        self.assertTrue(self.cp.set_from_hex("00ff00"))
        self.assertEqual(self.cp.color.g, 255)

    def test_set_from_hex_invalid(self):
        self.assertFalse(self.cp.set_from_hex("xyz"))

    def test_set_from_hsv(self):
        self.cp.set_from_hsv(0, 1.0, 1.0)  # Red
        self.assertEqual(self.cp.color.r, 255)

    def test_color_hex(self):
        c = Color(255, 128, 0)
        self.assertEqual(c.hex, "#ff8000")

    def test_color_rgb_str(self):
        c = Color(10, 20, 30)
        self.assertEqual(c.rgb_str, "rgb(10, 20, 30)")

    def test_color_hsv(self):
        c = Color(255, 0, 0)
        h, s, v = c.hsv
        self.assertAlmostEqual(h, 0.0)
        self.assertAlmostEqual(s, 1.0)
        self.assertAlmostEqual(v, 1.0)

    def test_color_luminance(self):
        white = Color(255, 255, 255)
        black = Color(0, 0, 0)
        self.assertGreater(white.luminance, black.luminance)

    def test_color_is_light(self):
        self.assertTrue(Color(255, 255, 255).is_light)
        self.assertFalse(Color(0, 0, 0).is_light)

    def test_color_contrast_text(self):
        self.assertEqual(Color(255, 255, 255).contrast_text, (0, 0, 0))
        self.assertEqual(Color(0, 0, 0).contrast_text, (255, 255, 255))

    def test_palettes_exist(self):
        self.assertIn("Material", PALETTES)
        self.assertIn("Nord", PALETTES)
        self.assertTrue(len(PALETTES["Material"]) > 10)

    def test_set_palette(self):
        self.assertTrue(self.cp.set_palette("Nord"))
        self.assertEqual(self.cp.selected_palette, "Nord")

    def test_set_palette_invalid(self):
        self.assertFalse(self.cp.set_palette("Nonexistent"))

    def test_palette_colors(self):
        self.cp.set_palette("Material")
        colors = self.cp.palette_colors
        self.assertTrue(len(colors) > 10)

    def test_select_palette_color(self):
        self.assertTrue(self.cp.select_palette_color(0))
        self.assertFalse(self.cp.select_palette_color(999))

    def test_add_palette(self):
        self.cp.add_palette("Custom", [(255, 0, 0), (0, 255, 0)])
        self.assertTrue(self.cp.set_palette("Custom"))
        self.assertEqual(len(self.cp.palette_colors), 2)

    def test_favorites(self):
        self.cp.set_color(255, 0, 0)
        self.cp.add_favorite()
        self.assertEqual(len(self.cp.favorites), 1)
        self.cp.add_favorite()  # duplicate
        self.assertEqual(len(self.cp.favorites), 1)

    def test_remove_favorite(self):
        self.cp.add_favorite()
        self.assertTrue(self.cp.remove_favorite(0))
        self.assertEqual(len(self.cp.favorites), 0)

    def test_recent(self):
        self.cp.set_color(255, 0, 0)
        self.cp.set_from_hex("#00ff00")
        self.assertTrue(len(self.cp.recent) >= 1)

    def test_copy_text(self):
        self.cp.set_color(255, 128, 0)
        self.assertEqual(self.cp.get_copy_text("hex"), "#ff8000")
        self.assertIn("rgb", self.cp.get_copy_text("rgb"))

    def test_invert(self):
        self.cp.set_color(255, 0, 0)
        self.cp.invert()
        self.assertEqual(self.cp.color.r, 0)
        self.assertEqual(self.cp.color.g, 255)
        self.assertEqual(self.cp.color.b, 255)

    def test_lighten(self):
        self.cp.set_color(100, 100, 100)
        self.cp.lighten(50)
        self.assertEqual(self.cp.color.r, 150)

    def test_darken(self):
        self.cp.set_color(200, 200, 200)
        self.cp.darken(50)
        self.assertEqual(self.cp.color.r, 150)

    def test_complementary(self):
        self.cp.set_color(255, 0, 0)
        comp = self.cp.complementary()
        self.assertIsNotNone(comp)

    def test_analogous(self):
        self.cp.set_color(255, 0, 0)
        colors = self.cp.analogous()
        self.assertEqual(len(colors), 3)

    def test_triadic(self):
        self.cp.set_color(255, 0, 0)
        colors = self.cp.triadic()
        self.assertEqual(len(colors), 3)

    def test_visibility(self):
        self.assertFalse(self.cp.visible)
        self.cp.show()
        self.assertTrue(self.cp.visible)
        self.cp.hide()
        self.assertFalse(self.cp.visible)
        self.cp.toggle()
        self.assertTrue(self.cp.visible)

    def test_render(self):
        rgb, w, h = self.cp.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.cp.to_dict()
        self.assertIn("color", d)
        self.assertIn("palette", d)

    def test_color_from_hex_rgba(self):
        c = Color.from_hex("#ff8800aa")
        self.assertEqual(c.a, 170)

    def test_color_tuple(self):
        c = Color(10, 20, 30, 40)
        self.assertEqual(c.tuple, (10, 20, 30, 40))


# ---------------------------------------------------------------------------
# ArchiveManager tests
# ---------------------------------------------------------------------------

class TestArchiveManager(unittest.TestCase):
    """Tests for ArchiveManager."""

    def setUp(self):
        self.am = ArchiveManager()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_zip(self, name: str, files: dict) -> str:
        """Create a test ZIP archive."""
        path = os.path.join(self.tmpdir, name)
        with zipfile.ZipFile(path, "w") as zf:
            for fname, content in files.items():
                zf.writestr(fname, content)
        return path

    def test_initialization(self):
        self.assertEqual(self.am._state, ArchiveState.IDLE)

    def test_open_archive(self):
        path = self._make_zip("test.zip", {"a.txt": "hello", "b.txt": "world"})
        result = self.am.open_archive(path)
        self.assertTrue(result)
        self.assertEqual(self.am._state, ArchiveState.BROWSING)
        self.assertEqual(len(self.am.entries), 2)

    def test_open_archive_invalid(self):
        result = self.am.open_archive("/nonexistent.zip")
        self.assertFalse(result)

    def test_open_not_zipfile(self):
        path = os.path.join(self.tmpdir, "notazip.zip")
        with open(path, "w") as f:
            f.write("not a zip")
        result = self.am.open_archive(path)
        self.assertFalse(result)

    def test_entries_info(self):
        path = self._make_zip("test.zip", {
            "a.txt": "hello",
            "dir/": "",
            "b.txt": "world",
        })
        self.am.open_archive(path)
        self.assertEqual(self.am.file_count, 2)
        self.assertGreater(self.am.total_size, 0)

    def test_extract(self):
        path = self._make_zip("test.zip", {"a.txt": "hello"})
        dest = os.path.join(self.tmpdir, "extracted")
        os.makedirs(dest)
        op = self.am.extract(path, dest)
        self.assertIsNotNone(op)
        # After extraction, file should exist
        self.assertTrue(os.path.exists(os.path.join(dest, "a.txt")))

    def test_extract_nonexistent(self):
        result = self.am.extract("/nonexistent.zip", "/tmp/out")
        self.assertIsNone(result)

    def test_create_archive(self):
        src = os.path.join(self.tmpdir, "source.txt")
        with open(src, "w") as f:
            f.write("content")
        archive_path = os.path.join(self.tmpdir, "created.zip")
        op = self.am.create(archive_path, [src])
        self.assertIsNotNone(op)
        self.assertEqual(op.state, ArchiveState.COMPLETED)
        self.assertTrue(os.path.exists(archive_path))

    def test_create_with_compression(self):
        src = os.path.join(self.tmpdir, "big.txt")
        with open(src, "w") as f:
            f.write("x" * 10000)
        archive_path = os.path.join(self.tmpdir, "best.zip")
        op = self.am.create(archive_path, [src], CompressionLevel.BEST)
        self.assertIsNotNone(op)

    def test_select(self):
        path = self._make_zip("test.zip", {"a.txt": "hello", "b.txt": "world"})
        self.am.open_archive(path)
        entry = self.am.select(0)
        self.assertIsNotNone(entry)

    def test_navigation(self):
        path = self._make_zip("test.zip", {"a.txt": "hello", "b.txt": "world"})
        self.am.open_archive(path)
        self.am.move_down()
        self.assertEqual(self.am._selected_index, 1)
        self.am.move_up()
        self.assertEqual(self.am._selected_index, 0)

    def test_handle_key_down(self):
        path = self._make_zip("test.zip", {"a.txt": "hello"})
        self.am.open_archive(path)
        result = self.am.handle_key("Down")
        self.assertEqual(result, "navigate")

    def test_handle_key_enter(self):
        path = self._make_zip("test.zip", {"a.txt": "hello"})
        self.am.open_archive(path)
        result = self.am.handle_key("Enter")
        self.assertTrue(result.startswith("select:"))

    def test_handle_key_escape(self):
        result = self.am.handle_key("Escape")
        self.assertEqual(result, "close")

    def test_close(self):
        path = self._make_zip("test.zip", {"a.txt": "hello"})
        self.am.open_archive(path)
        self.am.close()
        self.assertEqual(self.am._state, ArchiveState.IDLE)
        self.assertEqual(len(self.am.entries), 0)

    def test_stats(self):
        path = self._make_zip("test.zip", {"a.txt": "hello"})
        self.am.open_archive(path)
        stats = self.am.get_stats()
        self.assertEqual(stats["entries"], 1)

    def test_render(self):
        rgb, w, h = self.am.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.am.to_dict()
        self.assertIn("state", d)

    def test_compression_levels(self):
        self.assertEqual(CompressionLevel.STORED.value, 0)
        self.assertEqual(CompressionLevel.BEST.value, 9)

    def test_archive_entry_display_size(self):
        e = ArchiveEntry(name="test.txt", path="test.txt", size=2048)
        self.assertIn("KB", e.display_size)

    def test_archive_entry_dir(self):
        e = ArchiveEntry(name="dir/", path="dir/", is_dir=True)
        self.assertEqual(e.display_size, "—")

    def test_archive_entry_ratio(self):
        e = ArchiveEntry(name="test.txt", path="test.txt",
                        size=1000, compressed_size=500, ratio=0.5)
        self.assertEqual(e.display_ratio, "50%")

    def test_history(self):
        src = os.path.join(self.tmpdir, "h.txt")
        with open(src, "w") as f:
            f.write("x")
        archive_path = os.path.join(self.tmpdir, "h.zip")
        self.am.create(archive_path, [src])
        self.assertTrue(len(self.am.history) >= 1)


if __name__ == "__main__":
    unittest.main()
