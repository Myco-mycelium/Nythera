"""Tests for Nyrqis SDK CLI and hot reload."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nyrqis_sdk.cli import cmd_new, cmd_build, cmd_test
from nyrqis_sdk.hotreload import HotReloader, DirectoryReloader


class TestCLIPackageCommands(unittest.TestCase):
    """Test CLI package manager commands."""

    def test_cmd_pkg_importable(self):
        """Package command is importable."""
        from nyrqis_sdk.cli import cmd_pkg
        self.assertTrue(callable(cmd_pkg))

    def test_pkg_stats(self):
        """Package stats command works."""
        import argparse
        from nyrqis_sdk.cli import cmd_pkg
        
        args = argparse.Namespace(pkg_command="stats")
        result = cmd_pkg(args)
        self.assertEqual(result, 0)


class TestHotReloader(unittest.TestCase):
    """Test hot reload functionality."""

    def setUp(self):
        """Create temp directory for test files."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-hotreload-")
        self.test_file = Path(self.tmpdir) / "test.nstudio"
        self.test_file.write_text('{"version": "1.0.0"}')

    def tearDown(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reloader_creation(self):
        """HotReloader can be created."""
        reloader = HotReloader(self.test_file)
        self.assertEqual(reloader.file_path, self.test_file.resolve())
        self.assertFalse(reloader.is_running)

    def test_reloader_start_stop(self):
        """HotReloader can be started and stopped."""
        reloader = HotReloader(self.test_file)
        reloader.start()
        self.assertTrue(reloader.is_running)
        reloader.stop()
        self.assertFalse(reloader.is_running)

    def test_reloader_detects_change(self):
        """HotReloader detects file changes."""
        reloader = HotReloader(self.test_file, interval=0.1)
        
        change_detected = Event()
        
        def on_change(path):
            change_detected.set()
        
        reloader.on_change(on_change)
        reloader.start()
        
        # Modify the file
        time.sleep(0.2)
        self.test_file.write_text('{"version": "1.0.1"}')
        
        # Wait for change detection
        change_detected.wait(timeout=2.0)
        self.assertTrue(change_detected.is_set())
        
        reloader.stop()

    def test_reloader_check_now(self):
        """HotReloader check_now detects changes."""
        reloader = HotReloader(self.test_file)
        
        # No change initially
        self.assertFalse(reloader.check_now())
        
        # Modify the file
        self.test_file.write_text('{"version": "1.0.1"}')
        
        # Should detect change
        self.assertTrue(reloader.check_now())
        
        # Second check should not detect change (already seen)
        self.assertFalse(reloader.check_now())

    def test_reloader_no_callback_error(self):
        """HotReloader callbacks don't crash on errors."""
        reloader = HotReloader(self.test_file)
        
        def bad_callback(path):
            raise ValueError("Test error")
        
        reloader.on_change(bad_callback)
        
        # Should not raise
        self.test_file.write_text('{"version": "1.0.1"}')
        reloader.check_now()


class TestDirectoryReloader(unittest.TestCase):
    """Test directory hot reload."""

    def setUp(self):
        """Create temp directory with test files."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-dirreload-")
        self.test_file = Path(self.tmpdir) / "test.nstudio"
        self.test_file.write_text('{"version": "1.0.0"}')

    def tearDown(self):
        """Clean up test files."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_directory_reloader_creation(self):
        """DirectoryReloader can be created."""
        reloader = DirectoryReloader(self.tmpdir)
        self.assertEqual(reloader.directory, Path(self.tmpdir).resolve())
        self.assertFalse(reloader.is_running)

    def test_directory_reloader_start_stop(self):
        """DirectoryReloader can be started and stopped."""
        reloader = DirectoryReloader(self.tmpdir)
        reloader.start()
        self.assertTrue(reloader.is_running)
        reloader.stop()
        self.assertFalse(reloader.is_running)

    def test_directory_reloader_detects_new_file(self):
        """DirectoryReloader detects new files."""
        reloader = DirectoryReloader(self.tmpdir, interval=0.1)
        
        change_detected = Event()
        changed_files = []
        
        def on_change(path):
            changed_files.append(path)
            change_detected.set()
        
        reloader.on_change(on_change)
        reloader.start()
        
        # Create new file
        time.sleep(0.2)
        new_file = Path(self.tmpdir) / "new.nstudio"
        new_file.write_text('{"version": "2.0.0"}')
        
        # Wait for detection
        change_detected.wait(timeout=2.0)
        self.assertTrue(change_detected.is_set())
        self.assertIn(new_file.resolve(), [f.resolve() for f in changed_files])
        
        reloader.stop()


if __name__ == "__main__":
    unittest.main()
