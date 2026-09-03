"""
Tests for VM Manager, Disk Partitioner, and Boot Manager.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.vm_manager import (
    VMManager, VirtualMachine, VirtualDisk, Snapshot, VMTemplate,
    VMStatus, VMOSType, NetworkMode, DiskFormat
)
from ui.disk_partitioner import (
    DiskPartitioner, Disk, Partition, RAIDArray, LogicalVolume,
    FilesystemType, PartitionType, TableType, DiskInterface
)
from ui.boot_manager import (
    BootManager, KernelEntry, BootEntry, GRUBConfig, BootPartition,
    BootMode, KernelStatus, InitramfsType
)


# ─── VM Manager Tests ────────────────────────────────────────────────────


class TestVMManager(unittest.TestCase):

    def setUp(self):
        self.mgr = VMManager()

    def test_initial_state(self):
        self.assertEqual(self.mgr.view_mode, "list")
        self.assertGreater(len(self.mgr.vms), 0)
        self.assertGreater(len(self.mgr.templates), 0)

    def test_start_vm(self):
        # Stop first, then start
        self.mgr.stop_vm(0)
        self.assertTrue(self.mgr.start_vm(0))
        self.assertEqual(self.mgr.vms[0].status, VMStatus.RUNNING)

    def test_stop_vm(self):
        self.assertTrue(self.mgr.stop_vm(0))
        self.assertEqual(self.mgr.vms[0].status, VMStatus.STOPPED)

    def test_pause_vm(self):
        self.assertTrue(self.mgr.pause_vm(0))
        self.assertEqual(self.mgr.vms[0].status, VMStatus.PAUSED)

    def test_resume_vm(self):
        self.mgr.pause_vm(0)
        self.assertTrue(self.mgr.resume_vm(0))
        self.assertEqual(self.mgr.vms[0].status, VMStatus.RUNNING)

    def test_delete_vm(self):
        self.mgr.stop_vm(4)  # Alpine is stopped
        initial = len(self.mgr.vms)
        self.assertTrue(self.mgr.delete_vm(4))
        self.assertEqual(len(self.mgr.vms), initial - 1)

    def test_create_vm(self):
        template = self.mgr.templates[0]  # Ubuntu
        vm = self.mgr.create_vm("Test VM", template)
        self.assertIsNotNone(vm)
        self.assertEqual(vm.name, "Test VM")
        self.assertEqual(len(self.mgr.vms), 6)

    def test_snapshot(self):
        snap = self.mgr.create_snapshot(0, "Test Snap", "Description")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.name, "Test Snap")

    def test_delete_snapshot(self):
        snap = self.mgr.create_snapshot(1, "Temp")
        self.assertTrue(self.mgr.delete_snapshot(1, 0))

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr.selected_index, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr.selected_index, 0)

    def test_render_list(self):
        lines = self.mgr.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_details(self):
        self.mgr.set_view("details")
        lines = self.mgr.render_details()
        self.assertIsInstance(lines, list)

    def test_render_storage(self):
        self.mgr.set_view("storage")
        lines = self.mgr.render_storage()
        self.assertIsInstance(lines, list)

    def test_render_console(self):
        self.mgr._init_console()
        self.mgr.set_view("console")
        lines = self.mgr.render_console()
        self.assertIsInstance(lines, list)

    def test_console_input(self):
        self.mgr._init_console()
        self.mgr.send_console_input("uname")
        self.assertGreater(len(self.mgr.console_lines), 5)

    def test_handle_key(self):
        result = self.mgr.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")

    def test_running_count(self):
        count = self.mgr.running_count
        self.assertGreater(count, 0)

    def test_total_vm_disk(self):
        total = self.mgr.total_vm_disk
        self.assertGreater(total, 0)


class TestVirtualMachine(unittest.TestCase):

    def test_display_name(self):
        vm = VirtualMachine("Test", VMOSType.LINUX, VMStatus.RUNNING)
        self.assertIn("Test", vm.display_name)
        self.assertIn("▶️", vm.display_name)

    def test_memory_str(self):
        vm = VirtualMachine("Test", VMOSType.LINUX, memory_mb=4096)
        self.assertIn("GB", vm.memory_str)

    def test_uptime_str(self):
        vm = VirtualMachine("Test", VMOSType.LINUX, uptime_seconds=3661)
        self.assertIn("1h", vm.uptime_str)

    def test_is_running(self):
        vm = VirtualMachine("Test", VMOSType.LINUX, VMStatus.RUNNING)
        self.assertTrue(vm.is_running)

    def test_can_start(self):
        vm = VirtualMachine("Test", VMOSType.LINUX, VMStatus.STOPPED)
        self.assertTrue(vm.can_start)


class TestVirtualDisk(unittest.TestCase):

    def test_usage_pct(self):
        disk = VirtualDisk("test.qcow2", 100, used_gb=25.0)
        self.assertAlmostEqual(disk.usage_pct, 25.0)

    def test_free_gb(self):
        disk = VirtualDisk("test.qcow2", 100, used_gb=40.0)
        self.assertAlmostEqual(disk.free_gb, 60.0)

    def test_display_size(self):
        disk = VirtualDisk("test.qcow2", 500)
        self.assertEqual(disk.display_size, "500 GB")


class TestSnapshot(unittest.TestCase):

    def test_time_ago(self):
        snap = Snapshot("Test", created=time.time() - 300)
        self.assertIn("m ago", snap.time_ago)


# ─── Disk Partitioner Tests ──────────────────────────────────────────────


class TestDiskPartitioner(unittest.TestCase):

    def setUp(self):
        self.dp = DiskPartitioner()

    def test_initial_state(self):
        self.assertEqual(self.dp.view_mode, "disks")
        self.assertGreater(len(self.dp.disks), 0)

    def test_create_partition(self):
        # Use disk 0 (System Disk, 1TB) which has free space after partitions
        # Disk 0 partitions sum to ~309760+32768 = ~342528, leaving room
        part = self.dp.create_partition(0, "New Part", 1024, FilesystemType.EXT4)
        self.assertIsNotNone(part)
        self.assertEqual(part.name, "New Part")

    def test_delete_partition(self):
        initial = len(self.dp.disks[2].partitions)
        self.assertTrue(self.dp.delete_partition(2, 0))
        self.assertEqual(len(self.dp.disks[2].partitions), initial - 1)

    def test_format_partition(self):
        self.assertTrue(self.dp.format_partition(0, 0, FilesystemType.XFS))
        self.assertEqual(self.dp.disks[0].partitions[0].filesystem, FilesystemType.XFS)

    def test_mount(self):
        self.assertTrue(self.dp.mount(0, 0, "/mnt/test"))
        self.assertEqual(self.dp.disks[0].partitions[0].mount_point, "/mnt/test")

    def test_unmount(self):
        self.dp.mount(0, 0, "/mnt/test")
        self.assertTrue(self.dp.unmount(0, 0))
        self.assertEqual(self.dp.disks[0].partitions[0].mount_point, "")

    def test_fsck(self):
        result = self.dp.fsck(0, 0)
        self.assertEqual(result, "OK")

    def test_resize(self):
        self.assertTrue(self.dp.resize_partition(0, 1, 4096))

    def test_navigation(self):
        self.dp.select_disk_down()
        self.assertEqual(self.dp.selected_disk, 1)
        self.dp.select_disk_up()
        self.assertEqual(self.dp.selected_disk, 0)

    def test_partition_navigation(self):
        self.dp.select_partition_down()
        self.assertEqual(self.dp.selected_partition, 1)

    def test_render_disks(self):
        lines = self.dp.render_disks()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_partitions(self):
        self.dp.set_view("partitions")
        lines = self.dp.render_partitions()
        self.assertIsInstance(lines, list)

    def test_render_smart(self):
        self.dp.set_view("smart")
        lines = self.dp.render_smart()
        self.assertIsInstance(lines, list)

    def test_render_raid(self):
        self.dp.set_view("raid")
        lines = self.dp.render_raid()
        self.assertIsInstance(lines, list)

    def test_render_lvm(self):
        self.dp.set_view("lvm")
        lines = self.dp.render_lvm()
        self.assertIsInstance(lines, list)

    def test_operation_log(self):
        self.dp.create_partition(0, "Logged", 512)
        self.assertGreater(len(self.dp.operation_log), 0)


class TestDisk(unittest.TestCase):

    def test_display_size(self):
        disk = Disk("Test", "/dev/sda", DiskInterface.SATA, 1024000)
        self.assertIn("GB", disk.display_size)

    def test_health_str(self):
        disk = Disk("Test", "/dev/sda", DiskInterface.SATA, 1000, health_pct=95)
        self.assertIn("95%", disk.health_str)

    def test_free_str(self):
        disk = Disk("Test", "/dev/sda", DiskInterface.SATA, 2048000, partitions=[
            Partition("p1", "/dev/sda1", 0, 512000, used_mb=250000)
        ])
        self.assertIn("GB", disk.free_str)


class TestPartition(unittest.TestCase):

    def test_display_size(self):
        part = Partition("Test", "/dev/sda1", 0, 204800)
        self.assertIn("GB", part.display_size)

    def test_usage_pct(self):
        part = Partition("Test", "/dev/sda1", 0, 1000, used_mb=500)
        self.assertAlmostEqual(part.usage_pct, 50.0)


class TestRAIDArray(unittest.TestCase):

    def test_display(self):
        arr = RAIDArray("test", 1, ["/dev/sda", "/dev/sdb"])
        self.assertIn("RAID 1", arr.display)

    def test_display_jbod(self):
        arr = RAIDArray("test", 0, ["/dev/sda", "/dev/sdb"])
        self.assertIn("JBOD", arr.display)

    def test_effective_size(self):
        arr = RAIDArray("test", 1, ["/dev/sda", "/dev/sdb"], total_mb=1000)
        self.assertEqual(arr.effective_size, 1000)  # RAID 1 = 1 disk


# ─── Boot Manager Tests ──────────────────────────────────────────────────


class TestBootManager(unittest.TestCase):

    def setUp(self):
        self.bm = BootManager()

    def test_initial_state(self):
        self.assertEqual(self.bm.view_mode, "overview")
        self.assertGreater(len(self.bm.kernels), 0)
        self.assertGreater(len(self.bm.boot_entries), 0)

    def test_set_default_kernel(self):
        self.assertTrue(self.bm.set_default_kernel(1))
        self.assertTrue(self.bm.kernels[1].is_default)
        self.assertFalse(self.bm.kernels[0].is_default)

    def test_remove_kernel(self):
        initial = len(self.bm.kernels)
        self.assertTrue(self.bm.remove_kernel(1))
        self.assertEqual(len(self.bm.kernels), initial - 1)

    def test_remove_active_kernel(self):
        self.assertFalse(self.bm.remove_kernel(0))  # Active kernel can't be removed

    def test_set_default_entry(self):
        self.assertTrue(self.bm.set_default_entry(2))
        self.assertTrue(self.bm.boot_entries[2].is_default)

    def test_toggle_hidden(self):
        result = self.bm.toggle_hidden(2)
        self.assertTrue(result)

    def test_move_entry(self):
        initial = self.bm.boot_entries[0].name
        self.assertTrue(self.bm.move_entry(0, 1))
        self.assertNotEqual(self.bm.boot_entries[0].name, initial)

    def test_set_timeout(self):
        self.bm.set_timeout(10)
        self.assertEqual(self.bm.config.timeout_seconds, 10)

    def test_toggle_quiet_boot(self):
        initial = self.bm.config.quiet_boot
        result = self.bm.toggle_quiet_boot()
        self.assertNotEqual(result, initial)

    def test_cycle_theme(self):
        initial = self.bm.config.theme
        self.bm.cycle_theme()
        self.assertNotEqual(self.bm.config.theme, initial)

    def test_generate_config(self):
        config = self.bm.generate_config()
        self.assertIsInstance(config, str)
        self.assertIn("GRUB_TIMEOUT", config)

    def test_navigation(self):
        self.bm.set_view("kernels")
        self.bm.select_down()
        self.assertEqual(self.bm.selected_index, 1)
        self.bm.select_up()
        self.assertEqual(self.bm.selected_index, 0)

    def test_render_overview(self):
        lines = self.bm.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_kernels(self):
        self.bm.set_view("kernels")
        lines = self.bm.render_kernels()
        self.assertIsInstance(lines, list)

    def test_render_entries(self):
        self.bm.set_view("entries")
        lines = self.bm.render_entries()
        self.assertIsInstance(lines, list)

    def test_render_config(self):
        self.bm.set_view("config")
        lines = self.bm.render_config()
        self.assertIsInstance(lines, list)

    def test_render_partitions(self):
        self.bm.set_view("partitions")
        lines = self.bm.render_partitions()
        self.assertIsInstance(lines, list)

    def test_active_kernel(self):
        kernel = self.bm.active_kernel
        self.assertIsNotNone(kernel)
        self.assertEqual(kernel.status, KernelStatus.ACTIVE)

    def test_handle_key_overview(self):
        result = self.bm.handle_key("k")
        self.assertEqual(result, "kernels")

    def test_handle_key_kernels(self):
        self.bm.set_view("kernels")
        result = self.bm.handle_key("Escape")
        self.assertEqual(result, "back")


class TestKernelEntry(unittest.TestCase):

    def test_display(self):
        k = KernelEntry("6.11.0", KernelStatus.ACTIVE, is_default=True)
        self.assertIn("6.11.0", k.display)
        self.assertIn("★", k.display)

    def test_full_line(self):
        k = KernelEntry("6.11.0", KernelStatus.INSTALLED)
        self.assertIn("6.11.0", k.full_line)
        self.assertIn("Installed", k.full_line)


class TestBootEntry(unittest.TestCase):

    def test_display(self):
        e = BootEntry("Nyrqis OS", "🍄", "Linux", is_default=True)
        self.assertIn("Nyrqis OS", e.display)
        self.assertIn("★", e.display)


class TestGRUBConfig(unittest.TestCase):

    def test_defaults(self):
        cfg = GRUBConfig()
        self.assertEqual(cfg.timeout_seconds, 5)
        self.assertTrue(cfg.secure_boot)
        self.assertTrue(cfg.splash)


if __name__ == "__main__":
    unittest.main()
