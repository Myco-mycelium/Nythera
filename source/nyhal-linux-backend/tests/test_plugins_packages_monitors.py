#!/usr/bin/env python3
"""Tests for plugin system, package manager, and multi-monitor."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# ===================================================================
# Plugin System Tests
# ===================================================================

class TestPluginManifest(unittest.TestCase):
    """Tests for PluginManifest."""

    def test_creation(self):
        from ui.plugin_system import PluginManifest
        m = PluginManifest(id="test-plugin", name="Test", version="1.0.0")
        self.assertEqual(m.id, "test-plugin")
        self.assertEqual(m.display_name, "Test v1.0.0")

    def test_to_dict(self):
        from ui.plugin_system import PluginManifest
        m = PluginManifest(id="t", name="T", version="1.0")
        d = m.to_dict()
        self.assertEqual(d["id"], "t")

    def test_from_dict(self):
        from ui.plugin_system import PluginManifest
        m = PluginManifest.from_dict({"id": "x", "name": "X", "version": "2.0"})
        self.assertEqual(m.id, "x")


class TestPluginRegistry(unittest.TestCase):
    """Tests for PluginRegistry."""

    def setUp(self):
        from ui.plugin_system import PluginRegistry, PluginManifest
        self.reg = PluginRegistry()
        self.manifest = PluginManifest(
            id="test-plugin", name="Test Plugin", version="1.0.0",
            permissions=["notifications"],
        )

    def test_install(self):
        inst = self.reg.install(self.manifest)
        self.assertIsNotNone(inst)
        self.assertEqual(inst.id, "test-plugin")
        self.assertEqual(self.reg.count(), 1)

    def test_install_duplicate(self):
        self.reg.install(self.manifest)
        inst2 = self.reg.install(self.manifest)
        self.assertEqual(self.reg.count(), 1)  # Not duplicated

    def test_uninstall(self):
        self.reg.install(self.manifest)
        result = self.reg.uninstall("test-plugin")
        self.assertTrue(result)
        self.assertEqual(self.reg.count(), 0)

    def test_uninstall_nonexistent(self):
        result = self.reg.uninstall("no-such")
        self.assertFalse(result)

    def test_enable(self):
        self.reg.install(self.manifest)
        result = self.reg.enable("test-plugin")
        self.assertTrue(result)
        self.assertEqual(len(self.reg.enabled), 1)

    def test_disable(self):
        self.reg.install(self.manifest)
        self.reg.enable("test-plugin")
        result = self.reg.disable("test-plugin")
        self.assertTrue(result)
        self.assertEqual(len(self.reg.disabled), 1)

    def test_enable_nonexistent(self):
        result = self.reg.enable("no-such")
        self.assertFalse(result)

    def test_search(self):
        self.reg.install(self.manifest)
        results = self.reg.search("test")
        self.assertEqual(len(results), 1)

    def test_by_tag(self):
        self.manifest.tags = ["ui", "tool"]
        self.reg.install(self.manifest)
        results = self.reg.by_tag("ui")
        self.assertEqual(len(results), 1)

    def test_settings(self):
        self.reg.install(self.manifest)
        self.reg.set_setting("test-plugin", "theme", "dark")
        val = self.reg.get_setting("test-plugin", "theme")
        self.assertEqual(val, "dark")

    def test_save_load_state(self):
        self.reg.install(self.manifest)
        self.reg.enable("test-plugin")
        json_str = self.reg.save_state()
        self.assertIsNotNone(json_str)

        from ui.plugin_system import PluginRegistry as PR
        reg2 = PR()
        count = reg2.load_state(json_str)
        self.assertEqual(count, 1)
        inst = reg2.get("test-plugin")
        self.assertIsNotNone(inst)

    def test_callback(self):
        events = []
        self.reg.on_event(lambda t, pid: events.append((t, pid)))
        self.reg.install(self.manifest)
        self.assertIn(("installed", "test-plugin"), events)


class TestHookDispatcher(unittest.TestCase):
    """Tests for HookDispatcher."""

    def setUp(self):
        from ui.plugin_system import PluginRegistry, HookDispatcher, PluginManifest
        self.reg = PluginRegistry()
        self.hooks = HookDispatcher(self.reg)
        m = PluginManifest(id="p1", name="P1", version="1.0")
        self.reg.install(m)
        self.reg.enable("p1")

    def test_register_hook(self):
        self.hooks.register_hook("p1", "on_click", lambda e: None)
        self.assertEqual(self.hooks.hook_count("on_click"), 1)

    def test_dispatch(self):
        results = []
        self.hooks.register_hook("p1", "on_click", lambda e: "clicked")
        results = self.hooks.dispatch("on_click")
        self.assertEqual(results, ["clicked"])

    def test_disabled_not_dispatched(self):
        self.reg.disable("p1")
        results = self.hooks.dispatch("on_click")
        self.assertEqual(results, [])

    def test_unregister(self):
        self.hooks.register_hook("p1", "on_click", lambda e: None)
        self.hooks.unregister_hook("p1", "on_click")
        self.assertEqual(self.hooks.hook_count("on_click"), 0)

    def test_unregister_all(self):
        self.hooks.register_hook("p1", "on_click", lambda e: None)
        self.hooks.register_hook("p1", "on_key", lambda e: None)
        self.hooks.unregister_all("p1")
        self.assertEqual(self.hooks.total_hooks(), 0)

    def test_total_hooks(self):
        self.hooks.register_hook("p1", "on_click", lambda e: None)
        self.hooks.register_hook("p1", "on_key", lambda e: None)
        self.assertEqual(self.hooks.total_hooks(), 2)


class TestMessageBus(unittest.TestCase):
    """Tests for MessageBus."""

    def setUp(self):
        from ui.plugin_system import MessageBus
        self.bus = MessageBus()

    def test_subscribe_send(self):
        received = []
        self.bus.subscribe("p1", "topic_a", lambda m: received.append(m))
        count = self.bus.send(
            __import__("ui.plugin_system", fromlist=["PluginMessage"]).PluginMessage(
                from_plugin="p2", to_plugin="", topic="topic_a", payload="hello"))
        self.assertEqual(count, 1)
        self.assertEqual(len(received), 1)

    def test_broadcast(self):
        received = []
        self.bus.subscribe_broadcast("p1", lambda m: received.append(m))
        self.bus.broadcast("p2", "test_topic", "data")
        self.assertEqual(len(received), 1)

    def test_no_self_delivery(self):
        received = []
        self.bus.subscribe("p1", "t", lambda m: received.append(m))
        self.bus.broadcast("p1", "t", "data")
        self.assertEqual(len(received), 0)

    def test_history(self):
        self.bus.broadcast("p1", "t", "data")
        self.assertEqual(len(self.bus.history), 1)

    def test_topic_count(self):
        self.bus.subscribe("p1", "t1", lambda m: None)
        self.bus.subscribe("p1", "t2", lambda m: None)
        self.assertEqual(self.bus.topic_count(), 2)


class TestPluginManager(unittest.TestCase):
    """Tests for PluginManager."""

    def setUp(self):
        from ui.plugin_system import PluginManager, PluginManifest
        self.pm = PluginManager()
        self.manifest = PluginManifest(
            id="demo", name="Demo Plugin", version="1.0.0",
            permissions=["notifications"],
        )

    def test_install(self):
        result = self.pm.install(self.manifest)
        self.assertTrue(result)
        self.assertEqual(self.pm.summary()["total_installed"], 1)

    def test_enable_disable(self):
        self.pm.install(self.manifest)
        self.pm.enable("demo")
        self.assertEqual(self.pm.summary()["enabled"], 1)
        self.pm.disable("demo")
        self.assertEqual(self.pm.summary()["enabled"], 0)

    def test_uninstall(self):
        self.pm.install(self.manifest)
        result = self.pm.uninstall("demo")
        self.assertTrue(result)
        self.assertEqual(self.pm.summary()["total_installed"], 0)

    def test_dispatch_click(self):
        results = self.pm.dispatch_click(100, 200)
        self.assertIsInstance(results, list)

    def test_dispatch_key(self):
        results = self.pm.dispatch_key("a")
        self.assertIsInstance(results, list)

    def test_send_message(self):
        self.pm.install(self.manifest)
        count = self.pm.send_message("p1", "p2", "topic", "hello")
        self.assertIsInstance(count, int)

    def test_broadcast(self):
        count = self.pm.broadcast("p1", "topic", "data")
        self.assertIsInstance(count, int)

    def test_repr(self):
        r = repr(self.pm)
        self.assertIn("PluginManager", r)


# ===================================================================
# Package Manager Tests
# ===================================================================

class TestPackageManager(unittest.TestCase):
    """Tests for PackageManager."""

    def setUp(self):
        from ui.package_manager import PackageManager
        self.pm = PackageManager()

    def test_initial_state(self):
        self.assertFalse(self.pm.visible)
        self.assertGreater(self.pm.package_count, 0)

    def test_show_hide(self):
        self.pm.show()
        self.assertTrue(self.pm.visible)
        self.pm.hide()
        self.assertFalse(self.pm.visible)

    def test_toggle(self):
        result = self.pm.toggle()
        self.assertTrue(result)
        result2 = self.pm.toggle()
        self.assertFalse(result2)

    def test_search(self):
        self.pm.search("terminal")
        self.assertGreater(self.pm.package_count, 0)

    def test_filter_category(self):
        from ui.package_manager import AppCategory
        self.pm.set_category(AppCategory.SYSTEM)
        for pkg in self.pm.packages:
            self.assertEqual(pkg.category, AppCategory.SYSTEM)

    def test_sort(self):
        self.pm.set_sort("rating")
        pkgs = self.pm.packages
        self.assertGreater(len(pkgs), 1)

    def test_install_package(self):
        # Find an uninstalled package
        available = [p for p in self.pm.packages if not p.is_installed]
        self.assertGreater(len(available), 0)
        pkg = available[0]
        result = self.pm.install_package(pkg.id)
        self.assertTrue(result)
        # Verify it's now installed
        updated = self.pm.get_package(pkg.id)
        self.assertTrue(updated.is_installed)

    def test_uninstall_package(self):
        installed = [p for p in self.pm.packages if p.is_installed]
        self.assertGreater(len(installed), 0)
        pkg = installed[0]
        result = self.pm.uninstall_package(pkg.id)
        self.assertTrue(result)
        updated = self.pm.get_package(pkg.id)
        self.assertFalse(updated.is_installed)

    def test_update_package(self):
        updates = [p for p in self.pm.packages if p.has_update]
        self.assertGreater(len(updates), 0)
        pkg = updates[0]
        result = self.pm.update_package(pkg.id)
        self.assertTrue(result)

    def test_select_package(self):
        pkgs = self.pm.packages
        self.pm.select_package(pkgs[0].id)
        self.assertEqual(self.pm.current_view, "detail")
        self.assertIsNotNone(self.pm.selected_package)

    def test_views(self):
        self.pm.set_view("installed")
        self.assertEqual(self.pm.current_view, "installed")
        for pkg in self.pm.packages:
            self.assertTrue(pkg.is_installed)

    def test_installed_count(self):
        self.assertGreater(self.pm.installed_count, 0)

    def test_update_count(self):
        self.assertGreaterEqual(self.pm.update_count, 0)

    def test_categories(self):
        cats = self.pm.categories
        self.assertGreater(len(cats), 0)

    def test_navigation(self):
        self.pm.navigate_down()
        self.assertEqual(self.pm.selected_index, 1)
        self.pm.navigate_up()
        self.assertEqual(self.pm.selected_index, 0)

    def test_activate_selected(self):
        pkg = self.pm.activate_selected()
        self.assertIsNotNone(pkg)
        self.assertEqual(self.pm.current_view, "detail")

    def test_render_when_hidden(self):
        self.assertIsNone(self.pm.render())

    def test_callback(self):
        events = []
        self.pm.on_event(lambda t, d: events.append(t))
        self.pm.show()
        self.assertIn("shown", events)

    def test_repr(self):
        r = repr(self.pm)
        self.assertIn("PackageManager", r)


class TestPackageInfo(unittest.TestCase):
    """Tests for PackageInfo."""

    def test_display_size(self):
        from ui.package_manager import PackageInfo, PackageState, AppCategory
        p = PackageInfo(id="t", name="T", version="1.0",
                        size_bytes=1024 * 1024 * 5,
                        state=PackageState.INSTALLED)
        self.assertEqual(p.display_size, "5.0 MB")

    def test_is_installed(self):
        from ui.package_manager import PackageInfo, PackageState, AppCategory
        p = PackageInfo(id="t", name="T", version="1.0",
                        state=PackageState.INSTALLED)
        self.assertTrue(p.is_installed)

    def test_stars(self):
        from ui.package_manager import PackageInfo, AppCategory
        p = PackageInfo(id="t", name="T", version="1.0", rating=4.5)
        stars = p.stars
        self.assertIn("★", stars)


# ===================================================================
# Multi-Monitor Tests
# ===================================================================

class TestMonitorInfo(unittest.TestCase):
    """Tests for MonitorInfo."""

    def test_creation(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        mode = MonitorMode(width=1920, height=1080)
        m = MonitorInfo(id="m1", name="Primary", modes=[mode], current_mode=mode)
        self.assertEqual(m.width, 1920)
        self.assertEqual(m.height, 1080)

    def test_rect(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        m = MonitorInfo(id="m1", name="M1", x=100, y=50,
                        current_mode=MonitorMode(1920, 1080))
        self.assertEqual(m.rect, (100, 50, 1920, 1080))

    def test_contains(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        m = MonitorInfo(id="m1", name="M1", x=0, y=0,
                        current_mode=MonitorMode(1920, 1080))
        self.assertTrue(m.contains(960, 540))
        self.assertFalse(m.contains(2000, 540))

    def test_coord_conversion(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        m = MonitorInfo(id="m1", name="M1", x=100, y=0,
                        current_mode=MonitorMode(1920, 1080))
        vx, vy = m.local_to_virtual(50, 50)
        self.assertEqual(vx, 150)
        self.assertEqual(vy, 50)
        lx, ly = m.virtual_to_local(150, 50)
        self.assertEqual(lx, 50)
        self.assertEqual(ly, 50)

    def test_overlap(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        m1 = MonitorInfo(id="m1", name="M1", x=0, y=0,
                         current_mode=MonitorMode(100, 100))
        m2 = MonitorInfo(id="m2", name="M2", x=50, y=50,
                         current_mode=MonitorMode(100, 100))
        area = m1.overlap_area(m2)
        self.assertEqual(area, 50 * 50)

    def test_distance(self):
        from ui.multi_monitor import MonitorInfo, MonitorMode
        m1 = MonitorInfo(id="m1", name="M1", x=0, y=0,
                         current_mode=MonitorMode(100, 100))
        m2 = MonitorInfo(id="m2", name="M2", x=100, y=0,
                         current_mode=MonitorMode(100, 100))
        dist = m1.distance_to(m2)
        self.assertGreater(dist, 0)


class TestMultiMonitorManager(unittest.TestCase):
    """Tests for MultiMonitorManager."""

    def setUp(self):
        from ui.multi_monitor import MultiMonitorManager
        self.mm = MultiMonitorManager()

    def test_add_monitor(self):
        m = self.mm.add_monitor("m1", "Primary", 1920, 1080)
        self.assertIsNotNone(m)
        self.assertEqual(self.mm.monitor_count, 1)

    def test_add_multiple(self):
        self.mm.add_monitor("m1", "Left", 1920, 1080)
        self.mm.add_monitor("m2", "Right", 2560, 1440)
        self.assertEqual(self.mm.monitor_count, 2)

    def test_remove_monitor(self):
        self.mm.add_monitor("m1", "M1")
        result = self.mm.remove_monitor("m1")
        self.assertTrue(result)
        self.assertEqual(self.mm.monitor_count, 0)

    def test_remove_nonexistent(self):
        result = self.mm.remove_monitor("no-such")
        self.assertFalse(result)

    def test_primary(self):
        self.mm.add_monitor("m1", "M1")
        self.mm.add_monitor("m2", "M2")
        self.mm.set_primary("m2")
        self.assertEqual(self.mm.primary_monitor.id, "m2")

    def test_active_monitor(self):
        self.mm.add_monitor("m1", "M1")
        self.mm.add_monitor("m2", "M2")
        self.mm.set_active("m2")
        self.assertEqual(self.mm.active_monitor_id, "m2")

    def test_auto_arrange(self):
        self.mm.add_monitor("m1", "Left", 1920, 1080)
        self.mm.add_monitor("m2", "Right", 2560, 1440)
        m1 = self.mm.get_monitor("m1")
        m2 = self.mm.get_monitor("m2")
        self.assertEqual(m1.x, 0)
        self.assertEqual(m2.x, 1920)

    def test_arrange_right_of(self):
        from ui.multi_monitor import MonitorArrangement
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        self.mm.add_monitor("m2", "M2", 2560, 1440)
        self.mm.arrange("m2", "m1", MonitorArrangement.RIGHT_OF)
        m2 = self.mm.get_monitor("m2")
        self.assertEqual(m2.x, 1920)

    def test_virtual_rect(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        self.mm.add_monitor("m2", "M2", 2560, 1440)
        x, y, w, h = self.mm.virtual_desktop_rect
        self.assertEqual(w, 1920 + 2560)
        self.assertEqual(h, 1440)  # max height of side-by-side

    def test_find_monitor_at(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        m = self.mm.find_monitor_at(100, 100)
        self.assertIsNotNone(m)
        self.assertEqual(m.id, "m1")

    def test_find_nearest(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        self.mm.add_monitor("m2", "M2", 1920, 1080)
        m = self.mm.get_monitor("m2")
        m.x = 2000  # far right
        nearest = self.mm.find_nearest_monitor(1900, 540)
        self.assertIsNotNone(nearest)

    def test_place_centered(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        x, y = self.mm.place_window_center(800, 600)
        self.assertEqual(x, (1920 - 800) // 2)
        self.assertEqual(y, (1080 - 600) // 2)

    def test_snap_window(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        x, y, w, h = self.mm.snap_window(100, 100, 800, 600, "left")
        self.assertEqual(x, 0)
        self.assertEqual(w, 960)
        self.assertEqual(h, 1080)

    def test_move_to_monitor(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        self.mm.add_monitor("m2", "M2", 1920, 1080)
        x, y = self.mm.move_window_to_monitor(100, 100, 800, 600, "m2")
        self.assertEqual(x, 1920 + 100)

    def test_set_resolution(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        result = self.mm.set_resolution("m1", 3840, 2160, 144.0)
        self.assertTrue(result)
        m = self.mm.get_monitor("m1")
        self.assertEqual(m.width, 3840)

    def test_workspaces(self):
        self.mm.add_monitor("m1", "M1")
        wss = self.mm.get_workspaces("m1")
        self.assertGreater(len(wss), 0)

    def test_workspace_count(self):
        self.mm.add_monitor("m1", "M1")
        self.mm.add_monitor("m2", "M2")
        self.assertGreaterEqual(self.mm.workspace_count, 2)

    def test_save_load_profile(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        profile = self.mm.save_profile("default")
        self.assertIsNotNone(profile)
        result = self.mm.load_profile("default")
        self.assertTrue(result)

    def test_total_pixels(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        self.assertEqual(self.mm.total_pixels, 1920 * 1080)

    def test_summary(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        s = self.mm.summary()
        self.assertEqual(s["monitors"], 1)
        self.assertIn("virtual_rect", s)

    def test_callback(self):
        events = []
        self.mm.on_event(lambda t, d: events.append(t))
        self.mm.add_monitor("m1", "M1")
        self.assertIn("monitor_added", events)

    def test_repr(self):
        self.mm.add_monitor("m1", "M1", 1920, 1080)
        r = repr(self.mm)
        self.assertIn("MultiMonitorManager", r)


class TestMonitorMode(unittest.TestCase):
    """Tests for MonitorMode."""

    def test_aspect_ratio(self):
        from ui.multi_monitor import MonitorMode
        mode = MonitorMode(1920, 1080)
        self.assertEqual(mode.aspect_ratio, "16:9")

    def test_pixel_count(self):
        from ui.multi_monitor import MonitorMode
        mode = MonitorMode(1920, 1080)
        self.assertEqual(mode.pixel_count, 1920 * 1080)


if __name__ == "__main__":
    unittest.main()
