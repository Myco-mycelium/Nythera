"""
Tests for Hardware Diagnostics, Dock Customizer, and System Installer.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.hardware_diagnostics import (
    HardwareDiagnostics, ComponentInfo, StressTest, FailurePrediction,
    TestHistoryEntry, ComponentType, StressStatus, HealthStatus, StressType,
)
from ui.dock_customizer import (
    DockCustomizer, DockApp, DockWidget, DockConfig, MonitorDock,
    DockPosition, DockTheme, ClickAction, IndicatorStyle, AutoHideMode, WidgetType,
)
from ui.system_installer import (
    SystemInstaller, DiskInfo, Partition, PartitionLayout, UserInfo,
    PackageSelection, BootloaderConfig, InstallLogEntry,
    InstallerStep, FilesystemType, PartitionTable, BootloaderType,
    InstallStatus, PackageGroup,
)


# ─── Hardware Diagnostics Tests ───────────────────────────────────────────


class TestComponentInfo(unittest.TestCase):
    def test_create(self):
        c = ComponentInfo(ComponentType.CPU, "Ryzen 9 7950X", health=HealthStatus.EXCELLENT)
        self.assertEqual(c.name, "Ryzen 9 7950X")

    def test_health_icon(self):
        c = ComponentInfo(health=HealthStatus.WARNING)
        self.assertEqual(c.health_icon, "🟠")

    def test_temp_status(self):
        c = ComponentInfo(temperature_c=75)
        self.assertIn("Hot", c.temp_status)

    def test_temp_bar(self):
        c = ComponentInfo(temperature_c=65)
        bar = c.temp_bar
        self.assertIn("█", bar)

    def test_usage_bar(self):
        c = ComponentInfo(usage_percent=50)
        bar = c.usage_bar
        self.assertIn("█", bar)

    def test_error_summary_clean(self):
        c = ComponentInfo(errors=0, warnings=0)
        self.assertIn("Clean", c.error_summary)


class TestStressTest(unittest.TestCase):
    def test_create(self):
        st = StressTest(StressType.CPU_MULTI, StressStatus.IDLE, 60.0)
        self.assertEqual(st.stress_type, StressType.CPU_MULTI)

    def test_status_icon(self):
        st = StressTest(status=StressStatus.RUNNING)
        self.assertEqual(st.status_icon, "🔄")

    def test_progress_bar(self):
        st = StressTest(duration_s=60, elapsed_s=30)
        bar = st.progress_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_grade(self):
        st = StressTest(score=92, max_score=100)
        self.assertEqual(st.grade, "A")

    def test_grade_a_plus(self):
        st = StressTest(score=96, max_score=100)
        self.assertEqual(st.grade, "A+")


class TestFailurePrediction(unittest.TestCase):
    def test_create(self):
        fp = FailurePrediction("Samsung SSD", ComponentType.STORAGE, HealthStatus.GOOD)
        self.assertEqual(fp.component, "Samsung SSD")

    def test_risk_icon(self):
        fp = FailurePrediction(risk_level=HealthStatus.WARNING)
        self.assertEqual(fp.risk_icon, "🟠")

    def test_eta_str(self):
        fp = FailurePrediction(estimated_days=180)
        self.assertIn("months", fp.eta_str)


class TestHardwareDiagnostics(unittest.TestCase):
    def setUp(self):
        self.mgr = HardwareDiagnostics()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.components), 0)
        self.assertGreater(len(self.mgr.stress_tests), 0)

    def test_selected_component(self):
        c = self.mgr.selected_component
        self.assertIsNotNone(c)

    def test_select_component(self):
        self.mgr.select_component(2)
        self.assertEqual(self.mgr._selected_component, 2)

    def test_start_test(self):
        result = self.mgr.start_test(6)  # idle test
        self.assertTrue(result)
        self.assertEqual(self.mgr.stress_tests[6].status, StressStatus.RUNNING)

    def test_complete_test(self):
        self.mgr.start_test(6)
        result = self.mgr.complete_test(6, 88.5, 0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.stress_tests[6].status, StressStatus.COMPLETED)

    def test_stop_test(self):
        self.mgr.start_test(6)
        result = self.mgr.stop_test(6)
        self.assertTrue(result)
        self.assertEqual(self.mgr.stress_tests[6].status, StressStatus.STOPPED)

    def test_get_completed_tests(self):
        completed = self.mgr.get_completed_tests()
        self.assertGreater(len(completed), 0)

    def test_get_warning_predictions(self):
        warnings = self.mgr.get_warning_predictions()
        self.assertGreater(len(warnings), 0)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_component, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_component, 0)

    def test_search_components(self):
        results = self.mgr.search_components("nvidia")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_components", stats)
        self.assertIn("completed_tests", stats)


# ─── Dock Customizer Tests ────────────────────────────────────────────────


class TestDockApp(unittest.TestCase):
    def test_create(self):
        a = DockApp("Firefox", "/usr/bin/firefox", "🦊", pinned=True, running=True)
        self.assertEqual(a.name, "Firefox")

    def test_display_name_custom(self):
        a = DockApp("Firefox", custom_label="Web Browser")
        self.assertEqual(a.display_name, "Web Browser")

    def test_badge(self):
        a = DockApp(notification_count=5)
        self.assertEqual(a.badge, "(5)")

    def test_running_indicator(self):
        a = DockApp(running=True)
        self.assertEqual(a.running_indicator, "●")


class TestDockWidget(unittest.TestCase):
    def test_create(self):
        w = DockWidget(WidgetType.CLOCK, True, 32)
        self.assertEqual(w.widget_type, WidgetType.CLOCK)

    def test_icon(self):
        w = DockWidget(WidgetType.BATTERY)
        self.assertEqual(w.icon, "🔋")


class TestDockConfig(unittest.TestCase):
    def test_create(self):
        cfg = DockConfig(position=DockPosition.BOTTOM, dock_size=48)
        self.assertEqual(cfg.position, DockPosition.BOTTOM)

    def test_position_label(self):
        cfg = DockConfig(position=DockPosition.LEFT)
        self.assertIn("Left", cfg.position_label)

    def test_opacity_bar(self):
        cfg = DockConfig(opacity=0.75)
        bar = cfg.opacity_bar
        self.assertIn("█", bar)


class TestDockCustomizer(unittest.TestCase):
    def setUp(self):
        self.mgr = DockCustomizer()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.apps), 0)
        self.assertGreater(len(self.mgr.widgets), 0)

    def test_selected_app(self):
        a = self.mgr.selected_app
        self.assertIsNotNone(a)

    def test_select_app(self):
        self.mgr.select_app(2)
        self.assertEqual(self.mgr._selected_app, 2)

    def test_pin_app(self):
        self.mgr.pin_app(9)  # OBS
        self.assertTrue(self.mgr.apps[9].pinned)

    def test_unpin_app(self):
        self.mgr.unpin_app(0)  # Firefox
        self.assertFalse(self.mgr.apps[0].pinned)

    def test_toggle_favorite(self):
        result = self.mgr.toggle_favorite(0)
        self.assertTrue(result)
        self.assertTrue(self.mgr.apps[0].favorite)

    def test_move_app(self):
        name = self.mgr.apps[0].name
        result = self.mgr.move_app(0, 3)
        self.assertTrue(result)
        self.assertEqual(self.mgr.apps[3].name, name)

    def test_remove_app(self):
        count = len(self.mgr.apps)
        result = self.mgr.remove_app(4)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.apps), count - 1)

    def test_add_app(self):
        count = len(self.mgr.apps)
        app = self.mgr.add_app("TestApp", "/usr/bin/test", "📦")
        self.assertEqual(len(self.mgr.apps), count + 1)

    def test_set_position(self):
        self.mgr.set_position(DockPosition.LEFT)
        self.assertEqual(self.mgr.config.position, DockPosition.LEFT)

    def test_set_dock_size(self):
        self.mgr.set_dock_size(60)
        self.assertEqual(self.mgr.config.dock_size, 60)

    def test_set_opacity(self):
        self.mgr.set_opacity(0.5)
        self.assertEqual(self.mgr.config.opacity, 0.5)

    def test_toggle_widget(self):
        result = self.mgr.toggle_widget(0)
        self.assertTrue(result)
        self.assertFalse(self.mgr.widgets[0].enabled)

    def test_add_widget(self):
        count = len(self.mgr.widgets)
        w = self.mgr.add_widget(WidgetType.WEATHER)
        self.assertEqual(len(self.mgr.widgets), count + 1)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_app, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_app, 0)

    def test_search_apps(self):
        results = self.mgr.search_apps("firefox")
        self.assertGreater(len(results), 0)

    def test_get_pinned(self):
        pinned = self.mgr.get_pinned_apps()
        self.assertGreater(len(pinned), 0)

    def test_get_running(self):
        running = self.mgr.get_running_apps()
        self.assertGreater(len(running), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_apps", stats)
        self.assertIn("pinned", stats)


# ─── System Installer Tests ───────────────────────────────────────────────


class TestDiskInfo(unittest.TestCase):
    def test_create(self):
        d = DiskInfo("/dev/nvme0n1", "Samsung 990 Pro", size_bytes=2 * 1024 ** 4)
        self.assertEqual(d.device, "/dev/nvme0n1")

    def test_size_str(self):
        d = DiskInfo(size_bytes=2 * 1024 ** 4)
        self.assertIn("TB", d.size_str)

    def test_type_label(self):
        d = DiskInfo(interface="NVMe", rotational=False)
        self.assertEqual(d.type_label, "NVMe")


class TestPartition(unittest.TestCase):
    def test_create(self):
        p = Partition("/dev/nvme0n1", 1, "/", FilesystemType.BTRFS, 50 * 1024 ** 3)
        self.assertEqual(p.mount_point, "/")

    def test_size_str(self):
        p = Partition(size_bytes=50 * 1024 ** 3)
        self.assertIn("GB", p.size_str)


class TestUserInfo(unittest.TestCase):
    def test_create(self):
        u = UserInfo("admin", "P@ssw0rd!", "Admin User")
        self.assertEqual(u.username, "admin")

    def test_password_strength(self):
        u = UserInfo(password="P@ssw0rd!123")
        self.assertIn("Strong", u.password_strength)

    def test_strength_bar(self):
        u = UserInfo(password="P@ssw0rd!")
        bar = u.strength_bar
        self.assertIn("█", bar)


class TestPackageSelection(unittest.TestCase):
    def test_create(self):
        ps = PackageSelection(PackageGroup.BASE, True, "Core", 180, 450)
        self.assertEqual(ps.group, PackageGroup.BASE)

    def test_check_icon(self):
        ps = PackageSelection(selected=True)
        self.assertEqual(ps.check_icon, "☑️")


class TestSystemInstaller(unittest.TestCase):
    def setUp(self):
        self.installer = SystemInstaller()

    def test_initial_state(self):
        self.assertGreater(len(self.installer.disks), 0)
        self.assertGreater(len(self.installer.layouts), 0)
        self.assertGreater(len(self.installer.packages), 0)

    def test_selected_disk(self):
        d = self.installer.selected_disk
        self.assertIsNotNone(d)

    def test_select_disk(self):
        self.installer.select_disk(2)
        self.assertEqual(self.installer._selected_disk, 2)

    def test_select_layout(self):
        self.installer.select_layout(1)
        self.assertEqual(self.installer._selected_layout, 1)

    def test_go_next(self):
        initial = self.installer.current_step
        result = self.installer.go_next()
        self.assertTrue(result)
        self.assertNotEqual(self.installer.current_step, initial)

    def test_go_back(self):
        self.installer.go_next()
        result = self.installer.go_back()
        self.assertTrue(result)
        self.assertEqual(self.installer.current_step, InstallerStep.WELCOME)

    def test_set_user(self):
        self.installer.set_user("admin", "P@ssw0rd!", "Admin", "my-pc")
        self.assertEqual(self.installer.user.username, "admin")
        self.assertEqual(self.installer.user.hostname, "my-pc")

    def test_toggle_package(self):
        result = self.installer.toggle_package(4)  # Office
        self.assertTrue(result)
        self.assertTrue(self.installer.packages[4].selected)

    def test_select_all_packages(self):
        self.installer.select_all_packages()
        self.assertTrue(all(p.selected for p in self.installer.packages))

    def test_select_base_packages(self):
        self.installer.select_base_packages()
        selected = [p.group for p in self.installer.packages if p.selected]
        self.assertIn(PackageGroup.BASE, selected)
        self.assertIn(PackageGroup.DESKTOP, selected)

    def test_total_packages(self):
        total = self.installer.total_packages
        self.assertGreater(total, 0)

    def test_set_bootloader(self):
        self.installer.set_bootloader(BootloaderType.REFIND)
        self.assertEqual(self.installer.bootloader_config.bootloader, BootloaderType.REFIND)

    def test_start_install(self):
        self.installer.start_install()
        self.assertEqual(self.installer.install_status, InstallStatus.PREPARING)

    def test_update_progress(self):
        self.installer.start_install()
        self.installer.update_progress(50)
        self.assertEqual(self.installer.install_status, InstallStatus.EXTRACTING)

    def test_update_progress_complete(self):
        self.installer.start_install()
        self.installer.update_progress(100)
        self.assertEqual(self.installer.install_status, InstallStatus.COMPLETE)

    def test_add_log(self):
        count = len(self.installer.logs)
        self.installer.add_log("Test", "Test message")
        self.assertEqual(len(self.installer.logs), count + 1)

    def test_get_summary(self):
        summary = self.installer.get_summary()
        self.assertIn("disk", summary)
        self.assertIn("user", summary)
        self.assertIn("bootloader", summary)

    def test_step_progress(self):
        self.installer.go_next()  # Move to step 2
        bar = self.installer.step_progress_bar
        self.assertIn("█", bar)

    def test_stats(self):
        stats = self.installer.get_stats()
        self.assertIn("disks", stats)
        self.assertIn("packages_selected", stats)


class TestInstallLogEntry(unittest.TestCase):
    def test_create(self):
        log = InstallLogEntry(time.time(), "Test", "message")
        self.assertEqual(log.step, "Test")

    def test_icon(self):
        log = InstallLogEntry(level="error")
        self.assertEqual(log.icon, "❌")


if __name__ == "__main__":
    unittest.main()
