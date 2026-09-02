#!/usr/bin/env python3
"""Tests for ui.file_manager — file manager component."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.file_manager import (
    FileManager,
    FileEntry,
    FileType,
    SortMode,
    EXTENSION_MAP,
    FILE_TYPE_COLORS,
)


class TestFileType(unittest.TestCase):
    """Tests for FileType enum."""

    def test_file_types_exist(self):
        self.assertEqual(FileType.FILE.value, 0)
        self.assertEqual(FileType.DIRECTORY.value, 1)
        self.assertEqual(FileType.CODE.value, 9)

    def test_extension_map(self):
        self.assertEqual(EXTENSION_MAP[".py"], FileType.CODE)
        self.assertEqual(EXTENSION_MAP[".rs"], FileType.CODE)
        self.assertEqual(EXTENSION_MAP[".png"], FileType.IMAGE)
        self.assertEqual(EXTENSION_MAP[".mp3"], FileType.AUDIO)
        self.assertEqual(EXTENSION_MAP[".zip"], FileType.ARCHIVE)

    def test_file_type_colors(self):
        self.assertIn(FileType.FILE, FILE_TYPE_COLORS)
        self.assertIn(FileType.DIRECTORY, FILE_TYPE_COLORS)
        self.assertEqual(len(FILE_TYPE_COLORS[FileType.FILE]), 3)


class TestFileEntry(unittest.TestCase):
    """Tests for FileEntry dataclass."""

    def test_directory_entry(self):
        entry = FileEntry(name="test", path="/tmp/test", is_dir=True)
        self.assertEqual(entry.file_type, FileType.DIRECTORY)
        self.assertTrue(entry.is_dir)

    def test_python_file(self):
        entry = FileEntry(name="main.py", path="/tmp/main.py", size=1024)
        self.assertEqual(entry.file_type, FileType.CODE)
        self.assertFalse(entry.is_dir)
        self.assertEqual(entry.extension, ".py")

    def test_image_file(self):
        entry = FileEntry(name="photo.png", path="/tmp/photo.png", size=50000)
        self.assertEqual(entry.file_type, FileType.IMAGE)
        self.assertEqual(entry.extension, ".png")

    def test_display_size_bytes(self):
        entry = FileEntry(name="file.txt", path="/file.txt", size=500)
        self.assertEqual(entry.display_size, "500 B")

    def test_display_size_kb(self):
        entry = FileEntry(name="file.txt", path="/file.txt", size=2048)
        self.assertEqual(entry.display_size, "2.0 KB")

    def test_display_size_mb(self):
        entry = FileEntry(name="file.txt", path="/file.txt", size=5 * 1024 * 1024)
        self.assertEqual(entry.display_size, "5.0 MB")

    def test_display_size_empty_for_dir(self):
        entry = FileEntry(name="dir", path="/dir", is_dir=True)
        self.assertEqual(entry.display_size, "")

    def test_display_date(self):
        now = time.time()
        entry = FileEntry(name="file.txt", path="/file.txt", modified=now)
        date_str = entry.display_date
        self.assertTrue(len(date_str) > 0)

    def test_display_date_empty_for_zero(self):
        entry = FileEntry(name="file.txt", path="/file.txt", modified=0)
        self.assertEqual(entry.display_date, "")


class TestFileManager(unittest.TestCase):
    """Tests for the FileManager class."""

    def setUp(self):
        # Use a temp directory to avoid slow /tmp scans
        self._tmpdir = tempfile.mkdtemp()
        self.fm = FileManager(self._tmpdir)
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_creation(self):
        self.assertIsNotNone(self.fm)
        self.assertEqual(self.fm.current_path, self._tmpdir)

    def test_entries_loaded(self):
        self.assertGreater(self.fm.entry_count, 0)

    def test_parent_entry(self):
        """Should have .. entry for non-root directories."""
        entries = self.fm.entries
        if entries:
            self.assertEqual(entries[0].name, "..")

    def test_breadcrumbs(self):
        crumbs = self.fm.breadcrumbs
        self.assertGreater(len(crumbs), 0)
        # First breadcrumb is always root
        self.assertEqual(crumbs[0], ("/", "/"))
        # Last breadcrumb is current directory
        self.assertEqual(crumbs[-1][0], os.path.basename(self._tmpdir))

    def test_select(self):
        self.fm.select(0)
        self.assertEqual(self.fm.selected_index, 0)

    def test_select_bounds(self):
        """Selecting out of bounds should not crash."""
        self.fm.select(-1)
        self.fm.select(99999)

    def test_get_selected(self):
        self.fm.select(0)
        selected = self.fm.get_selected()
        self.assertIsNotNone(selected)

    def test_sort_by_name(self):
        self.fm.sort_mode = SortMode.NAME
        self.assertEqual(self.fm.sort_mode, SortMode.NAME)

    def test_sort_by_size(self):
        self.fm.sort_mode = SortMode.SIZE
        self.assertEqual(self.fm.sort_mode, SortMode.SIZE)

    def test_sort_reverse(self):
        self.fm.sort_reverse = True
        self.assertTrue(self.fm.sort_reverse)

    def test_show_hidden(self):
        initial_count = self.fm.entry_count
        self.fm.show_hidden = True
        # Should have more entries when showing hidden
        self.assertGreaterEqual(self.fm.entry_count, initial_count)

    def test_go_up(self):
        original = self.fm.current_path
        result = self.fm.go_up()
        if original != "/":
            self.assertTrue(result)
            self.assertNotEqual(self.fm.current_path, original)

    def test_navigate_to_valid(self):
        result = self.fm.navigate_to("/tmp")
        self.assertTrue(result)
        self.assertEqual(self.fm.current_path, "/tmp")

    def test_navigate_to_invalid(self):
        result = self.fm.navigate_to("/nonexistent/path/12345")
        self.assertFalse(result)

    def test_activate_directory(self):
        """Activating a directory should navigate to it."""
        # Create a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            self.fm.navigate_to(tmpdir)
            # Find the first directory entry (after ..)
            for i, entry in enumerate(self.fm.entries):
                if entry.is_dir and entry.name != "..":
                    self.fm.select(i)
                    result = self.fm.activate_selected()
                    self.assertTrue(result)
                    break

    def test_activate_file_returns_false(self):
        """Activating a file should return False."""
        with tempfile.NamedTemporaryFile() as f:
            self.fm.navigate_to(os.path.dirname(f.name))
            for i, entry in enumerate(self.fm.entries):
                if not entry.is_dir:
                    self.fm.select(i)
                    result = self.fm.activate_selected()
                    self.assertFalse(result)
                    break

    def test_scroll(self):
        self.fm.scroll(5)
        self.fm.scroll(-3)

    def test_scroll_to_selected(self):
        self.fm.select(0)
        self.fm.scroll_to_selected()

    def test_key_up(self):
        # Create files so we have multiple entries
        for i in range(5):
            open(os.path.join(self._tmpdir, f"file{i}.txt"), "w").close()
        self.fm._load_directory()
        self.fm.select(3)
        result = self.fm.handle_key("Up")
        self.assertEqual(result, "select")
        self.assertEqual(self.fm.selected_index, 2)

    def test_key_down(self):
        # Create files so we have multiple entries
        for i in range(5):
            open(os.path.join(self._tmpdir, f"file{i}.txt"), "w").close()
        self.fm._load_directory()
        self.fm.select(0)
        result = self.fm.handle_key("Down")
        self.assertEqual(result, "select")
        self.assertEqual(self.fm.selected_index, 1)

    def test_key_home(self):
        self.fm.select(5)
        result = self.fm.handle_key("Home")
        self.assertEqual(result, "select")
        self.assertEqual(self.fm.selected_index, 0)

    def test_key_enter(self):
        """Enter on a directory should navigate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.fm.navigate_to(tmpdir)
            for i, entry in enumerate(self.fm.entries):
                if entry.is_dir and entry.name != "..":
                    self.fm.select(i)
                    result = self.fm.handle_key("Enter")
                    self.assertEqual(result, "navigate")
                    break

    def test_key_backspace(self):
        original = self.fm.current_path
        if original != "/":
            result = self.fm.handle_key("Backspace")
            self.assertEqual(result, "navigate")

    def test_key_refresh(self):
        result = self.fm.handle_key("r")
        self.assertEqual(result, "refresh")

    def test_key_toggle_hidden(self):
        result = self.fm.handle_key(".")
        self.assertEqual(result, "toggle_hidden")
        self.assertTrue(self.fm.show_hidden)

    def test_key_sort_name(self):
        result = self.fm.handle_key("n")
        self.assertEqual(result, "sort")
        self.assertEqual(self.fm.sort_mode, SortMode.NAME)

    def test_key_sort_size(self):
        result = self.fm.handle_key("S")
        self.assertEqual(result, "sort")
        self.assertEqual(self.fm.sort_mode, SortMode.SIZE)
        self.assertTrue(self.fm.sort_reverse)

    def test_key_unhandled(self):
        result = self.fm.handle_key("F1")
        self.assertEqual(result, "")


class TestFileManagerRendering(unittest.TestCase):
    """Tests for file manager rendering."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.fm = FileManager(self._tmpdir)
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_render(self):
        pixels, width, height = self.fm.render(width=800, height=600)
        self.assertEqual(width, 800)
        self.assertEqual(height, 600)
        self.assertEqual(len(pixels), width * height)

    def test_render_rgb(self):
        data, width, height = self.fm.render_to_rgb()
        self.assertEqual(len(data), width * height * 3)

    def test_render_with_selection(self):
        self.fm.select(0)
        pixels, width, height = self.fm.render(width=400, height=300)
        # Should have non-background pixels
        bg = (30, 30, 42)
        non_bg = sum(1 for p in pixels if p != bg)
        self.assertGreater(non_bg, 0)


class TestFileManagerIntegration(unittest.TestCase):
    """Integration tests using real temp directories."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create test structure
        os.makedirs(os.path.join(self.tmpdir, "subdir1"))
        os.makedirs(os.path.join(self.tmpdir, "subdir2"))
        
        for name in ["file1.txt", "file2.py", "file3.png"]:
            with open(os.path.join(self.tmpdir, name), "w") as f:
                f.write("test content")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lists_created_files(self):
        fm = FileManager(self.tmpdir)
        names = [e.name for e in fm.entries if e.name != ".."]
        self.assertIn("subdir1", names)
        self.assertIn("subdir2", names)
        self.assertIn("file1.txt", names)
        self.assertIn("file2.py", names)
        self.assertIn("file3.png", names)

    def test_file_types_detected(self):
        fm = FileManager(self.tmpdir)
        entries = {e.name: e for e in fm.entries}
        
        self.assertEqual(entries["file1.txt"].file_type, FileType.DOCUMENT)
        self.assertEqual(entries["file2.py"].file_type, FileType.CODE)
        self.assertEqual(entries["file3.png"].file_type, FileType.IMAGE)
        self.assertEqual(entries["subdir1"].file_type, FileType.DIRECTORY)

    def test_navigate_into_subdir(self):
        fm = FileManager(self.tmpdir)
        subdir_path = os.path.join(self.tmpdir, "subdir1")
        result = fm.navigate_to(subdir_path)
        self.assertTrue(result)
        self.assertEqual(fm.current_path, subdir_path)

    def test_sort_by_name(self):
        fm = FileManager(self.tmpdir)
        fm.sort_mode = SortMode.NAME
        names = [e.name for e in fm.entries if e.name != ".."]
        # Sort puts dirs first, then files alphabetically
        dirs = sorted([n for n in names if os.path.isdir(os.path.join(self.tmpdir, n))])
        files = sorted([n for n in names if not os.path.isdir(os.path.join(self.tmpdir, n))])
        self.assertEqual(names, dirs + files)

    def test_sort_by_size(self):
        fm = FileManager(self.tmpdir)
        fm.sort_mode = SortMode.SIZE
        sizes = [e.size for e in fm.entries if e.name != ".." and not e.is_dir]
        # Files should be in order (all same size in this test)
        self.assertEqual(sizes, sorted(sizes))

    def test_render_full_cycle(self):
        fm = FileManager(self.tmpdir)
        fm.select(1)
        pixels, width, height = fm.render(width=640, height=480)
        self.assertEqual(len(pixels), 640 * 480)
        self.assertEqual(width, 640)
        self.assertEqual(height, 480)
        
        # Verify RGB rendering works (uses render's stored dimensions)
        fm._view_width = 640
        fm._view_height = 480
        data, w, h = fm.render_to_rgb()
        self.assertEqual(len(data), w * h * 3)


if __name__ == "__main__":
    unittest.main()
