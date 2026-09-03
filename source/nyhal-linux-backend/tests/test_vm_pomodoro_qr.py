"""Tests for VM manager, pomodoro timer, and QR tool."""
import unittest
import time

from ui.vm_manager import (
    VMManager, VirtualMachine, VMStorage, VMNetwork, VMSnapshot,
    VMTemplate, VMOS, VMState, NetworkMode,
)
from ui.pomodoro import (
    PomodoroTimer, PomodoroSession, PomodoroConfig, DailyStats,
    PomodoroState, SessionTag,
)
from ui.qr_tool import (
    QRTool, QRCode, QRStyle, QRScanResult, BatchItem,
    QRMode, ErrorCorrection,
)


# ─── VM Manager Tests ────────────────────────────────────────────────

class TestVMStorage(unittest.TestCase):
    def test_usage_pct(self):
        s = VMStorage(disk_gb=100, used_gb=50)
        self.assertAlmostEqual(s.usage_pct, 50.0)

    def test_usage_bar(self):
        s = VMStorage(disk_gb=100, used_gb=50)
        bar = s.usage_bar
        self.assertEqual(len(bar), 20)


class TestVirtualMachine(unittest.TestCase):
    def test_cpu_bar(self):
        vm = VirtualMachine(cpu_usage=50)
        bar = vm.cpu_bar
        self.assertEqual(len(bar), 20)

    def test_uptime_str(self):
        vm = VirtualMachine(uptime_s=7200)
        self.assertIn("h", vm.uptime_str)

    def test_uptime_down(self):
        vm = VirtualMachine(uptime_s=0)
        self.assertEqual(vm.uptime_str, "down")


class TestVMManager(unittest.TestCase):
    def setUp(self):
        self.mgr = VMManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr._vms), 0)
        self.assertGreater(len(self.mgr._templates), 0)

    def test_render_vms(self):
        lines = self.mgr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("VM MANAGER" in l for l in lines))

    def test_render_console(self):
        self.mgr.set_view("console")
        lines = self.mgr.render()
        self.assertTrue(any("Console" in l for l in lines))

    def test_render_templates(self):
        self.mgr.set_view("templates")
        lines = self.mgr.render()
        self.assertTrue(any("Templates" in l for l in lines))

    def test_render_network(self):
        self.mgr.set_view("network")
        lines = self.mgr.render()
        self.assertTrue(any("Network" in l for l in lines))

    def test_render_stats(self):
        self.mgr.set_view("stats")
        lines = self.mgr.render()
        self.assertTrue(any("Resource" in l for l in lines))

    def test_running_count(self):
        self.assertGreater(self.mgr.running_count, 0)

    def test_start_stop(self):
        self.mgr._selected_vm = 3  # arch-lab (stopped)
        self.mgr.start_vm()
        self.assertEqual(self.mgr._vms[3].state, VMState.RUNNING)
        self.mgr.stop_vm()
        self.assertEqual(self.mgr._vms[3].state, VMState.STOPPED)


# ─── Pomodoro Timer Tests ────────────────────────────────────────────

class TestPomodoroConfig(unittest.TestCase):
    def test_work_seconds(self):
        c = PomodoroConfig(work_minutes=25)
        self.assertEqual(c.work_seconds, 1500)

    def test_short_break_seconds(self):
        c = PomodoroConfig(short_break_minutes=5)
        self.assertEqual(c.short_break_seconds, 300)


class TestPomodoroSession(unittest.TestCase):
    def test_completion_pct(self):
        s = PomodoroSession(duration_s=1500, planned_s=1500)
        self.assertAlmostEqual(s.completion_pct, 100.0)

    def test_duration_str(self):
        s = PomodoroSession(duration_s=1500)
        self.assertEqual(s.duration_str, "25:00")


class TestDailyStats(unittest.TestCase):
    def test_work_hours_str(self):
        d = DailyStats(total_work_minutes=150)
        self.assertEqual(d.work_hours_str, "2h 30m")

    def test_focus_bar(self):
        d = DailyStats(focus_score=75)
        bar = d.focus_bar
        self.assertEqual(len(bar), 20)


class TestPomodoroTimer(unittest.TestCase):
    def setUp(self):
        self.timer = PomodoroTimer()

    def test_initial_state(self):
        self.assertGreater(len(self.timer._sessions), 0)
        self.assertGreater(len(self.timer._daily_stats), 0)

    def test_render_timer(self):
        lines = self.timer.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("POMODORO" in l for l in lines))

    def test_render_history(self):
        self.timer.set_view("history")
        lines = self.timer.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_stats(self):
        self.timer.set_view("stats")
        lines = self.timer.render()
        self.assertTrue(any("Stats" in l for l in lines))

    def test_render_settings(self):
        self.timer.set_view("settings")
        lines = self.timer.render()
        self.assertTrue(any("Settings" in l for l in lines))

    def test_total_sessions(self):
        self.assertGreater(self.timer.total_sessions, 0)

    def test_total_focus_hours(self):
        self.assertGreater(self.timer.total_focus_hours, 0)

    def test_remaining_str(self):
        self.assertEqual(self.timer.remaining_str, "00:00")


# ─── QR Tool Tests ───────────────────────────────────────────────────

class TestQRCode(unittest.TestCase):
    def test_preview_short(self):
        c = QRCode(content="short text")
        self.assertEqual(c.preview, "short text")

    def test_preview_long(self):
        c = QRCode(content="x" * 50)
        self.assertTrue(len(c.preview) <= 43)

    def test_ec_label(self):
        c = QRCode(error_correction=ErrorCorrection.H)
        self.assertIn("High", c.ec_label)


class TestQRScanResult(unittest.TestCase):
    def test_confidence_pct(self):
        s = QRScanResult(confidence=0.95)
        self.assertEqual(s.confidence_pct, "95%")


class TestBatchItem(unittest.TestCase):
    def test_status_generated(self):
        b = BatchItem(generated=True)
        self.assertEqual(b.status_icon, "✅")

    def test_status_error(self):
        b = BatchItem(error="fail")
        self.assertEqual(b.status_icon, "❌")


class TestQRTool(unittest.TestCase):
    def setUp(self):
        self.tool = QRTool()

    def test_initial_state(self):
        self.assertGreater(len(self.tool._codes), 0)
        self.assertGreater(len(self.tool._scans), 0)
        self.assertGreater(len(self.tool._batch_items), 0)

    def test_render_generate(self):
        lines = self.tool.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("QR CODE" in l for l in lines))

    def test_render_history(self):
        self.tool.set_view("history")
        lines = self.tool.render()
        self.assertTrue(any("Generated" in l or "History" in l or "codes" in l for l in lines))

    def test_render_batch(self):
        self.tool.set_view("batch")
        lines = self.tool.render()
        self.assertTrue(any("Batch" in l for l in lines))

    def test_render_scans(self):
        self.tool.set_view("scans")
        lines = self.tool.render()
        self.assertTrue(any("Scan" in l for l in lines))

    def test_render_templates(self):
        self.tool.set_view("templates")
        lines = self.tool.render()
        self.assertTrue(any("Templates" in l for l in lines))

    def test_total_scans(self):
        self.assertGreater(self.tool.total_scans, 0)

    def test_favorite_count(self):
        self.assertGreater(self.tool.favorite_count, 0)


if __name__ == "__main__":
    unittest.main()
