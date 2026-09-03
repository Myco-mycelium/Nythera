"""
Tests for Repo Manager, System Journal, and VFS Manager.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.repo_manager import (
    RepoManager, Package, Repository, InstallTransaction,
    PackageStatus, RepoStatus, SignatureStatus
)
from ui.system_journal import (
    SystemJournal, JournalEntry as LogEntry, LogLevel
)
from ui.vfs_manager import (
    VFSManager, MountPoint, FileEntry, Bookmark,
    FilesystemType, MountStatus, NetworkFS
)


# ─── Repo Manager Tests ──────────────────────────────────────────────────


class TestRepoManager(unittest.TestCase):

    def setUp(self):
        self.mgr = RepoManager()

    def test_initial_state(self):
        self.assertEqual(self.mgr.view_mode, "packages")
        self.assertGreater(len(self.mgr.packages), 0)
        self.assertGreater(len(self.mgr.repositories), 0)

    def test_install_package(self):
        self.assertTrue(self.mgr.install_package("docker"))
        pkg = next(p for p in self.mgr.packages if p.name == "docker")
        self.assertEqual(pkg.status, PackageStatus.INSTALLED)

    def test_remove_package(self):
        self.assertTrue(self.mgr.remove_package("vim"))
        pkg = next(p for p in self.mgr.packages if p.name == "vim")
        self.assertEqual(pkg.status, PackageStatus.AVAILABLE)

    def test_update_package(self):
        self.assertTrue(self.mgr.update_package("firefox"))
        pkg = next(p for p in self.mgr.packages if p.name == "firefox")
        self.assertEqual(pkg.status, PackageStatus.INSTALLED)

    def test_update_all(self):
        count = self.mgr.update_all()
        self.assertGreater(count, 0)

    def test_search(self):
        results = self.mgr.search("python")
        self.assertGreater(len(results), 0)

    def test_sort_cycle(self):
        initial = self.mgr._sort_by
        self.mgr.cycle_sort()
        self.assertNotEqual(self.mgr._sort_by, initial)

    def test_queue(self):
        self.assertTrue(self.mgr.add_to_queue("docker"))
        self.assertIn("docker", self.mgr.install_queue)
        self.assertTrue(self.mgr.remove_from_queue("docker"))
        self.assertNotIn("docker", self.mgr.install_queue)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr.selected_index, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr.selected_index, 0)

    def test_render_packages(self):
        lines = self.mgr.render_packages()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_repos(self):
        self.mgr.set_view("repos")
        lines = self.mgr.render_repos()
        self.assertIsInstance(lines, list)

    def test_render_installed(self):
        self.mgr.set_view("installed")
        lines = self.mgr.render_installed()
        self.assertIsInstance(lines, list)

    def test_render_updates(self):
        self.mgr.set_view("updates")
        lines = self.mgr.render_updates()
        self.assertIsInstance(lines, list)

    def test_render_queue(self):
        self.mgr.set_view("queue")
        lines = self.mgr.render_queue()
        self.assertIsInstance(lines, list)

    def test_categories(self):
        cats = self.mgr.categories
        self.assertIsInstance(cats, list)
        self.assertGreater(len(cats), 0)

    def test_stats(self):
        self.assertGreater(self.mgr.installed_count, 0)
        self.assertGreater(self.mgr.updatable_count, 0)
        self.assertGreater(self.mgr.total_installed_size, 0)

    def test_handle_key(self):
        result = self.mgr.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestPackage(unittest.TestCase):

    def test_display_name(self):
        pkg = Package("test", "1.0", status=PackageStatus.INSTALLED)
        self.assertIn("✅", pkg.display_name)
        self.assertIn("test", pkg.display_name)

    def test_downloads_str(self):
        pkg = Package("test", "1.0", downloads=1500000)
        self.assertIn("M", pkg.downloads_str)

    def test_popularity_bar(self):
        pkg = Package("test", "1.0", popularity=50.0)
        bar = pkg.popularity_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)


class TestInstallTransaction(unittest.TestCase):

    def test_progress_bar(self):
        tx = InstallTransaction(packages=["test"], progress=0.5)
        bar = tx.progress_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)


# ─── System Journal Tests ────────────────────────────────────────────────


class TestSystemJournal(unittest.TestCase):

    def setUp(self):
        self.journal = SystemJournal()

    def test_initial_state(self):
        self.assertEqual(self.journal.view_mode, "logs")
        self.assertGreater(self.journal.total_count, 0)

    def test_add_entry(self):
        entry = self.journal.add_entry(LogLevel.INFO, "test", "test message")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.service, "test")

    def test_toggle_bookmark(self):
        result = self.journal.toggle_bookmark(0)
        self.assertIsNotNone(result)

    def test_filter_level(self):
        self.journal.set_level_filter(LogLevel.ERROR)
        entries = self.journal._get_filtered_entries()
        for e in entries:
            self.assertIn(e.level, [LogLevel.ERROR, LogLevel.CRITICAL])

    def test_search(self):
        self.journal.set_search("kernel")
        entries = self.journal._get_filtered_entries()
        self.assertIsInstance(entries, list)

    def test_toggle_regex(self):
        result = self.journal.toggle_regex()
        self.assertTrue(result)

    def test_toggle_tail(self):
        result = self.journal.toggle_tail()
        self.assertTrue(result)

    def test_stats(self):
        stats = self.journal.get_stats(24)
        self.assertIsInstance(stats, LogStats)
        self.assertGreater(stats.total, 0)

    def test_services(self):
        services = self.journal.get_services()
        self.assertIsInstance(services, dict)
        self.assertGreater(len(services), 0)

    def test_bookmarks(self):
        bookmarks = self.journal.get_bookmarked_entries()
        self.assertIsInstance(bookmarks, list)
        self.assertGreater(len(bookmarks), 0)

    def test_export(self):
        text = self.journal.export_logs()
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)

    def test_navigation(self):
        self.journal.select_down()
        self.assertEqual(self.journal.selected_index, 1)
        self.journal.select_up()
        self.assertEqual(self.journal.selected_index, 0)

    def test_render_logs(self):
        lines = self.journal.render_logs()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_stats(self):
        self.journal.set_view("stats")
        lines = self.journal.render_stats()
        self.assertIsInstance(lines, list)

    def test_render_services(self):
        self.journal.set_view("services")
        lines = self.journal.render_services()
        self.assertIsInstance(lines, list)

    def test_render_bookmarks(self):
        self.journal.set_view("bookmarks")
        lines = self.journal.render_bookmarks()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.journal.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestLogEntry(unittest.TestCase):

    def test_time_str(self):
        entry = LogEntry(time.time(), LogLevel.INFO, "test", "message")
        self.assertIn(":", entry.time_str)

    def test_level_icon(self):
        entry = LogEntry(time.time(), LogLevel.ERROR, "test", "message")
        self.assertEqual(entry.level_icon, "❌")

    def test_display(self):
        entry = LogEntry(time.time(), LogLevel.INFO, "test", "hello world")
        self.assertIn("hello world", entry.display)


class TestLogStats(unittest.TestCase):

    def test_error_rate(self):
        stats = LogStats(total=100, error=5, critical=1)
        self.assertAlmostEqual(stats.error_rate, 6.0)


# ─── VFS Manager Tests ───────────────────────────────────────────────────


class TestVFSManager(unittest.TestCase):

    def setUp(self):
        self.vfs = VFSManager()

    def test_initial_state(self):
        self.assertEqual(self.vfs.view_mode, "browser")
        self.assertEqual(self.vfs.current_path, "/")

    def test_get_files(self):
        files = self.vfs.get_files("/")
        self.assertGreater(len(files), 0)

    def test_navigate(self):
        self.assertTrue(self.vfs.navigate_to("/home/user"))
        self.assertEqual(self.vfs.current_path, "/home/user")

    def test_go_back(self):
        self.vfs.navigate_to("/home/user")
        self.assertTrue(self.vfs.go_back())
        self.assertEqual(self.vfs.current_path, "/")

    def test_go_up(self):
        self.vfs.navigate_to("/home/user")
        self.assertTrue(self.vfs.go_up())
        self.assertEqual(self.vfs.current_path, "/home")

    def test_go_home(self):
        self.vfs.navigate_to("/data")
        self.assertTrue(self.vfs.go_home())
        self.assertEqual(self.vfs.current_path, "/home/user")

    def test_enter_directory(self):
        self.vfs.navigate_to("/")
        # Select "home" directory (index 3)
        self.vfs._selected_index = 3
        result = self.vfs.enter_selected()
        self.assertTrue(result)
        self.assertEqual(self.vfs.current_path, "/home")

    def test_mount_network(self):
        mp = self.vfs.mount_network("nas.local", "/share", "/mnt/test")
        self.assertIsNotNone(mp)
        self.assertEqual(mp.status, MountStatus.MOUNTED)

    def test_unmount(self):
        mp = self.vfs.mount_points[0]
        self.assertTrue(self.vfs.unmount(mp.mount_id))
        self.assertEqual(mp.status, MountStatus.UNMOUNTED)

    def test_remount(self):
        mp = self.vfs.mount_points[0]
        self.vfs.unmount(mp.mount_id)
        self.assertTrue(self.vfs.remount(mp.mount_id))
        self.assertEqual(mp.status, MountStatus.MOUNTED)

    def test_bookmarks(self):
        bm = self.vfs.add_bookmark("Test", "/tmp")
        self.assertIsNotNone(bm)
        self.assertTrue(self.vfs.remove_bookmark(len(self.vfs.bookmarks) - 1))

    def test_breadcrumbs(self):
        self.vfs.navigate_to("/home/user/Documents")
        crumbs = self.vfs.path_breadcrumbs
        self.assertEqual(len(crumbs), 4)  # /, /home, /home/user, /home/user/Documents

    def test_navigation(self):
        self.vfs.select_down()
        self.assertEqual(self.vfs.selected_index, 1)
        self.vfs.select_up()
        self.assertEqual(self.vfs.selected_index, 0)

    def test_mount_for_path(self):
        mp = self.vfs.get_mount_for_path("/")
        self.assertIsNotNone(mp)

    def test_render_browser(self):
        lines = self.vfs.render_browser()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_mounts(self):
        self.vfs.set_view("mounts")
        lines = self.vfs.render_mounts()
        self.assertIsInstance(lines, list)

    def test_render_network(self):
        self.vfs.set_view("network")
        lines = self.vfs.render_network()
        self.assertIsInstance(lines, list)

    def test_render_bookmarks(self):
        self.vfs.set_view("bookmarks")
        lines = self.vfs.render_bookmarks()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.vfs.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")

    def test_network_count(self):
        count = self.vfs.network_count
        self.assertGreater(count, 0)

    def test_mounted_count(self):
        count = self.vfs.mounted_count
        self.assertGreater(count, 0)


class TestMountPoint(unittest.TestCase):

    def test_display(self):
        mp = MountPoint("/dev/sda1", "/boot", FilesystemType.EXT4, MountStatus.MOUNTED)
        self.assertIn("/boot", mp.display)

    def test_usage_bar(self):
        mp = MountPoint("/dev/sda1", "/", FilesystemType.EXT4, MountStatus.MOUNTED,
                        total_kb=1000000, used_kb=500000)
        bar = mp.usage_bar
        self.assertIn("█", bar)

    def test_is_network(self):
        mp = MountPoint("server:/share", "/mnt/nfs", FilesystemType.NFS)
        self.assertTrue(mp.is_network)


class TestFileEntry(unittest.TestCase):

    def test_display_size(self):
        entry = FileEntry("test.py", "/test.py", False, 1048576)
        self.assertIn("MB", entry.display_size)

    def test_icon(self):
        entry = FileEntry("test.py", "/test.py", False)
        self.assertEqual(entry.icon, "🐍")

    def test_dir_icon(self):
        entry = FileEntry("dir", "/dir", True)
        self.assertEqual(entry.icon, "📁")


if __name__ == "__main__":
    unittest.main()
