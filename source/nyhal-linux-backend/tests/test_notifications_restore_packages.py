import unittest
import time


class TestNotificationCenter(unittest.TestCase):
    def setUp(self):
        from ui.notification_center import NotificationCenter, NotificationPriority, NotificationCategory
        self.nc = NotificationCenter()
        self.NP = NotificationPriority
        self.NC = NotificationCategory

    def test_initial_state(self):
        self.assertGreater(len(self.nc.notifications), 0)
        self.assertGreater(len(self.nc.dnd_schedules), 0)
        self.assertGreater(len(self.nc.groups), 0)

    def test_add_notification(self):
        from ui.notification_center import Notification
        notif = Notification(id=99, title="Test", body="Test body", app_name="test")
        self.nc.add_notification(notif)
        self.assertEqual(self.nc.notifications[0].id, 99)

    def test_dismiss(self):
        result = self.nc.dismiss(1)
        self.assertTrue(result)

    def test_mark_read(self):
        result = self.nc.mark_read(1)
        self.assertTrue(result)

    def test_mark_all_read(self):
        count = self.nc.mark_all_read()
        self.assertGreater(count, 0)

    def test_pin(self):
        result = self.nc.pin(2)
        self.assertTrue(result)

    def test_clear_all(self):
        self.nc.pin(1)
        cleared = self.nc.clear_all()
        self.assertGreater(cleared, 0)
        self.assertGreater(len(self.nc.notifications), 0)

    def test_search(self):
        results = self.nc.search("update")
        self.assertGreater(len(results), 0)

    def test_filter_by_category(self):
        results = self.nc.filter_by_category(self.NC.SECURITY)
        self.assertGreater(len(results), 0)

    def test_filter_by_priority(self):
        results = self.nc.filter_by_priority(self.NP.URGENT)
        self.assertGreater(len(results), 0)

    def test_get_unread(self):
        unread = self.nc.get_unread()
        self.assertIsInstance(unread, list)

    def test_get_stats(self):
        stats = self.nc.get_stats()
        self.assertGreater(stats.total, 0)
        self.assertGreaterEqual(stats.unread, 0)

    def test_notification_priority_icon(self):
        from ui.notification_center import Notification
        n = Notification(priority=self.NP.URGENT)
        self.assertEqual(n.priority_icon, "🔴")

    def test_notification_time_ago(self):
        from ui.notification_center import Notification
        n = Notification(timestamp=time.time() - 120)
        self.assertIn("m", n.time_ago)

    def test_dnd_schedule_display(self):
        from ui.notification_center import DNDschedule
        dnd = DNDschedule(name="Test", start_hour=22, end_hour=7)
        self.assertIn("22:00", dnd.time_display)

    def test_dnd_days_display(self):
        from ui.notification_center import DNDschedule
        dnd = DNDschedule(name="Test", days=["Mon", "Tue", "Wed", "Thu", "Fri"])
        self.assertEqual(dnd.days_display, "Weekdays")


class TestSystemRestore(unittest.TestCase):
    def setUp(self):
        from ui.system_restore import SystemRestore, SnapshotType, SnapshotStatus
        self.sr = SystemRestore()
        self.ST = SnapshotType
        self.SS = SnapshotStatus

    def test_initial_state(self):
        self.assertGreater(len(self.sr.snapshots), 0)
        self.assertGreater(len(self.sr.backup_schedules), 0)
        self.assertGreater(len(self.sr.restore_points), 0)

    def test_create_snapshot(self):
        snap = self.sr.create_snapshot("Test Snapshot", "Test description",
                                        self.ST.FULL, size_gb=1.0)
        self.assertEqual(snap.name, "Test Snapshot")
        self.assertIn(snap, self.sr.snapshots)

    def test_delete_snapshot(self):
        snap_id = self.sr.snapshots[0].id
        result = self.sr.delete_snapshot(snap_id)
        self.assertTrue(result)

    def test_rollback(self):
        result = self.sr.rollback(self.sr.snapshots[0].id)
        self.assertTrue(result)
        self.assertEqual(self.sr.snapshots[0].status, self.SS.RESTORED)

    def test_get_snapshot(self):
        snap = self.sr.get_snapshot(self.sr.snapshots[0].id)
        self.assertIsNotNone(snap)

    def test_get_rollback_snapshots(self):
        snapshots = self.sr.get_rollback_snapshots()
        self.assertGreater(len(snapshots), 0)

    def test_get_bootable_snapshots(self):
        snapshots = self.sr.get_bootable_snapshots()
        self.assertGreater(len(snapshots), 0)

    def test_search(self):
        results = self.sr.search("kernel")
        self.assertIsInstance(results, list)

    def test_get_stats(self):
        stats = self.sr.get_stats()
        self.assertIn("total_snapshots", stats)
        self.assertIn("total_size_gb", stats)

    def test_snapshot_size_display(self):
        from ui.system_restore import Snapshot
        s = Snapshot(size_gb=0.5)
        self.assertIn("MB", s.size_display)
        s.size_gb = 2.5
        self.assertIn("GB", s.size_display)

    def test_snapshot_status_icon(self):
        from ui.system_restore import Snapshot
        s = Snapshot(status=self.SS.COMPLETED)
        self.assertEqual(s.status_icon, "✅")

    def test_backup_schedule_status(self):
        from ui.system_restore import BackupScheduleEntry, BackupSchedule
        b = BackupScheduleEntry(name="test", enabled=True, schedule=BackupSchedule.DAILY)
        self.assertEqual(b.status_icon, "🟢")
        b.enabled = False
        self.assertEqual(b.status_icon, "⚪")


class TestPackageManager(unittest.TestCase):
    def setUp(self):
        from ui.package_manager import PackageManager, PackageStatus, PackageCategory
        self.pm = PackageManager()
        self.PS = PackageStatus
        self.PC = PackageCategory

    def test_initial_state(self):
        self.assertGreater(len(self.pm.packages), 0)
        self.assertGreater(len(self.pm.repositories), 0)

    def test_search(self):
        results = self.pm.search("nyrqis")
        self.assertGreater(len(results), 0)

    def test_get_installed(self):
        installed = self.pm.get_installed()
        self.assertGreater(len(installed), 0)
        for p in installed:
            self.assertEqual(p.status, self.PS.INSTALLED)

    def test_get_updatable(self):
        updatable = self.pm.get_updatable()
        self.assertGreater(len(updatable), 0)

    def test_select_package(self):
        pkg = self.pm.select_package("nyrqis-kernel")
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.name, "nyrqis-kernel")

    def test_install_package(self):
        pkg = next(p for p in self.pm.packages if p.status == self.PS.AVAILABLE)
        op = self.pm.install_package(pkg.name)
        self.assertIsNotNone(op)
        self.assertEqual(pkg.status, self.PS.INSTALLED)

    def test_remove_package(self):
        pkg = next(p for p in self.pm.packages if p.status == self.PS.INSTALLED)
        op = self.pm.remove_package(pkg.name)
        self.assertIsNotNone(op)
        self.assertEqual(pkg.status, self.PS.AVAILABLE)

    def test_update_package(self):
        pkg = next(p for p in self.pm.packages if p.status == self.PS.UPDATABLE)
        op = self.pm.update_package(pkg.name)
        self.assertIsNotNone(op)
        self.assertEqual(pkg.version, pkg.latest_version)

    def test_upgrade_all(self):
        count = self.pm.upgrade_all()
        self.assertGreater(count, 0)
        self.assertEqual(len(self.pm.get_updatable()), 0)

    def test_get_dependencies(self):
        deps = self.pm.get_dependencies("nyrqis-shell")
        self.assertGreater(len(deps), 0)

    def test_get_category_count(self):
        counts = self.pm.get_category_count()
        self.assertIn("system", counts)

    def test_get_stats(self):
        stats = self.pm.get_stats()
        self.assertIn("total_packages", stats)
        self.assertIn("installed", stats)

    def test_package_size_display(self):
        from ui.package_manager import Package
        p = Package(name="test", size_bytes=500)
        self.assertEqual(p.size_display, "500 B")
        p.size_bytes = 2048
        self.assertEqual(p.size_display, "2.0 KB")
        p.size_bytes = 2 * 1024 * 1024
        self.assertEqual(p.size_display, "2.0 MB")

    def test_package_status_icon(self):
        from ui.package_manager import Package
        p = Package(name="test", status=self.PS.INSTALLED)
        self.assertEqual(p.status_icon, "✅")

    def test_operation_progress_bar(self):
        from ui.package_manager import PackageOperation
        op = PackageOperation(package_name="test", progress=50.0)
        bar = op.progress_bar
        self.assertEqual(len(bar), 20)


if __name__ == "__main__":
    unittest.main()
