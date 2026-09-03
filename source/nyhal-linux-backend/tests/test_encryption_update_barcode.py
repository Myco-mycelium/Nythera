"""
Tests for Encryption Manager, Update Manager, and Barcode Scanner.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.encryption_manager import (
    EncryptionManager, EncryptedVolume, KeySlot, EncryptionBackup,
    EncryptionType, VolumeStatus, KeySlotType
)
from ui.update_manager import (
    UpdateManager, PackageUpdate, UpdateHistory, ChangelogEntry,
    UpdateType, UpdateStatus
)
from ui.barcode_scanner import (
    BarcodeScanner, ScanResult, BatchJob, BarcodeTemplate,
    BarcodeFormat, ContentType, ScanMode
)


class TestEncryptionManager(unittest.TestCase):

    def setUp(self):
        self.em = EncryptionManager()

    def test_initial_state(self):
        self.assertEqual(self.em.view_mode, "volumes")
        self.assertGreater(len(self.em.volumes), 0)

    def test_unlock_volume(self):
        idx = next(i for i, v in enumerate(self.em.volumes) if v.status == VolumeStatus.LOCKED)
        self.assertTrue(self.em.unlock_volume(idx))
        self.assertEqual(self.em.volumes[idx].status, VolumeStatus.UNLOCKED)

    def test_lock_volume(self):
        idx = next(i for i, v in enumerate(self.em.volumes) if v.status == VolumeStatus.MOUNTED)
        self.assertTrue(self.em.lock_volume(idx))
        self.assertEqual(self.em.volumes[idx].status, VolumeStatus.LOCKED)

    def test_mount_volume(self):
        idx = next(i for i, v in enumerate(self.em.volumes) if v.status == VolumeStatus.UNLOCKED)
        self.assertTrue(self.em.mount_volume(idx))

    def test_add_key_slot(self):
        slot = self.em.add_key_slot(0, KeySlotType.KEYFILE)
        self.assertIsNotNone(slot)
        self.assertEqual(slot.slot_type, KeySlotType.KEYFILE)

    def test_remove_key_slot(self):
        vol = self.em.volumes[0]
        initial = len(vol.key_slots)
        slot_id = vol.key_slots[-1].slot_id
        self.assertTrue(self.em.remove_key_slot(0, slot_id))
        self.assertEqual(len(vol.key_slots), initial - 1)

    def test_navigation(self):
        self.em.select_down()
        self.assertEqual(self.em.selected_index, 1)
        self.em.select_up()
        self.assertEqual(self.em.selected_index, 0)

    def test_render_volumes(self):
        lines = self.em.render_volumes()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_details(self):
        self.em.set_view("details")
        lines = self.em.render_details()
        self.assertIsInstance(lines, list)

    def test_render_backups(self):
        self.em.set_view("backups")
        lines = self.em.render_backups()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.em.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestEncryptedVolume(unittest.TestCase):

    def test_display(self):
        vol = EncryptedVolume("Test", "/dev/sda2", encryption=EncryptionType.LUKS2, status=VolumeStatus.MOUNTED)
        self.assertIn("Test", vol.display)
        self.assertIn("LUKS2", vol.display)

    def test_size_str(self):
        vol = EncryptedVolume("Test", "/dev/sda2", total_bytes=500_000_000_000)
        self.assertIn("GB", vol.size_str)


class TestUpdateManager(unittest.TestCase):

    def setUp(self):
        self.um = UpdateManager()

    def test_initial_state(self):
        self.assertEqual(self.um.view_mode, "updates")
        self.assertGreater(len(self.um._updates), 0)

    def test_install_update(self):
        idx = next(i for i, u in enumerate(self.um._updates) if u.status == UpdateStatus.AVAILABLE)
        self.assertTrue(self.um.install_update(idx))
        self.assertEqual(self.um._updates[idx].status, UpdateStatus.INSTALLED)

    def test_rollback(self):
        self.assertTrue(self.um.rollback_update(0))
        self.assertTrue(self.um._history[0].rolled_back)

    def test_install_all(self):
        count = self.um.install_all()
        self.assertGreater(count, 0)

    def test_counts(self):
        self.assertGreater(self.um.available_count, 0)

    def test_navigation(self):
        self.um.select_down()
        self.assertEqual(self.um.selected_index, 1)
        self.um.select_up()
        self.assertEqual(self.um.selected_index, 0)

    def test_render_updates(self):
        lines = self.um.render_updates()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_history(self):
        self.um.set_view("history")
        lines = self.um.render_history()
        self.assertIsInstance(lines, list)

    def test_render_detail(self):
        self.um.set_view("detail")
        lines = self.um.render_detail()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.um.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestPackageUpdate(unittest.TestCase):

    def test_display(self):
        upd = PackageUpdate("test", "1.0", "2.0")
        self.assertIn("1.0", upd.display)
        self.assertIn("2.0", upd.display)

    def test_size_str(self):
        upd = PackageUpdate("test", "1.0", "2.0", size_kb=5000)
        self.assertIn("MB", upd.size_str)

    def test_progress_bar(self):
        upd = PackageUpdate("test", "1.0", "2.0", download_progress=50)
        bar = upd.progress_bar
        self.assertIn("█", bar)


class TestBarcodeScanner(unittest.TestCase):

    def setUp(self):
        self.bs = BarcodeScanner()

    def test_initial_state(self):
        self.assertEqual(self.bs.view_mode, "scan")
        self.assertGreater(len(self.bs.results), 0)

    def test_simulate_scan(self):
        result = self.bs.simulate_scan("https://example.com")
        self.assertIsNotNone(result)
        self.assertEqual(result.content_type, ContentType.URL)

    def test_simulate_wifi_scan(self):
        result = self.bs.simulate_scan("WIFI:T:WPA;S:Test;P:pass123;;")
        self.assertEqual(result.content_type, ContentType.WIFI)

    def test_delete_result(self):
        initial = len(self.bs.results)
        self.assertTrue(self.bs.delete_result(0))
        self.assertEqual(len(self.bs.results), initial - 1)

    def test_clear_history(self):
        count = self.bs.clear_history()
        self.assertGreater(count, 0)
        self.assertEqual(len(self.bs.results), 0)

    def test_batches(self):
        self.assertGreater(len(self.bs._batches), 0)

    def test_navigation(self):
        self.bs.set_view("history")
        self.bs.select_down()
        self.assertEqual(self.bs.selected_index, 1)
        self.bs.select_up()
        self.assertEqual(self.bs.selected_index, 0)

    def test_render_scan(self):
        lines = self.bs.render_scan()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_history(self):
        self.bs.set_view("history")
        lines = self.bs.render_history()
        self.assertIsInstance(lines, list)

    def test_render_batch(self):
        self.bs.set_view("batch")
        lines = self.bs.render_batch()
        self.assertIsInstance(lines, list)

    def test_render_generate(self):
        self.bs.set_view("generate")
        lines = self.bs.render_generate()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.bs.handle_key(" ")
        self.assertEqual(result, "camera_on")


class TestScanResult(unittest.TestCase):

    def test_display(self):
        r = ScanResult("test content", BarcodeFormat.QR_CODE)
        self.assertIn("test content", r.display)

    def test_preview(self):
        r = ScanResult("a" * 100, BarcodeFormat.QR_CODE)
        self.assertIn("...", r.preview)


class TestBatchJob(unittest.TestCase):

    def test_display(self):
        b = BatchJob("Test", [], "completed")
        self.assertIn("Test", b.display)
        self.assertIn("✅", b.display)


if __name__ == "__main__":
    unittest.main()
