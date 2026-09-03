"""
Tests for Network Analyzer, Disk Health, and Job Scheduler.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.network_analyzer import (
    NetworkAnalyzer, NetworkInterface, Connection, CapturedPacket,
    PingResult, Protocol, ConnectionState, InterfaceStatus
)
from ui.disk_health import (
    DiskHealthMonitor, DiskHealth, SMARTAttribute, TemperatureReading,
    BenchmarkResult, DiskAlert, HealthStatus, AlertSeverity, DiskType
)
from ui.job_scheduler import (
    JobScheduler, Job, JobRun, CronExpression, ResourceLimits,
    JobStatus, RunStatus, NotificationType
)


# ─── Network Analyzer Tests ──────────────────────────────────────────────


class TestNetworkAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = NetworkAnalyzer()

    def test_initial_state(self):
        self.assertEqual(self.analyzer.view_mode, "overview")
        self.assertGreater(len(self.analyzer.interfaces), 0)
        self.assertGreater(len(self.analyzer.connections), 0)

    def test_capture_start_stop(self):
        self.analyzer.start_capture()
        self.assertTrue(self.analyzer.capture_active)
        self.analyzer.stop_capture()
        self.assertFalse(self.analyzer.capture_active)

    def test_add_packet(self):
        pkt = self.analyzer.add_packet("1.1.1.1", "8.8.8.8", Protocol.DNS, 64, "query")
        self.assertIsNotNone(pkt)
        self.assertIn(pkt, self.analyzer.packets)

    def test_clear_capture(self):
        self.analyzer.start_capture()
        self.analyzer.add_packet("1.1.1.1", "8.8.8.8", Protocol.DNS, 64)
        count = self.analyzer.clear_capture()
        self.assertGreater(count, 0)

    def test_ping(self):
        result = self.analyzer.ping("8.8.8.8")
        self.assertIsNotNone(result)
        self.assertGreater(result.latency_ms, 0)

    def test_protocol_stats(self):
        stats = self.analyzer.get_protocol_stats()
        self.assertIsInstance(stats, dict)
        self.assertGreater(len(stats), 0)

    def test_top_talkers(self):
        talkers = self.analyzer.get_top_talkers(3)
        self.assertIsInstance(talkers, list)
        self.assertLessEqual(len(talkers), 3)

    def test_navigation(self):
        self.analyzer.set_view("connections")
        self.analyzer.select_down()
        self.assertEqual(self.analyzer.selected_index, 1)
        self.analyzer.select_up()
        self.assertEqual(self.analyzer.selected_index, 0)

    def test_render_overview(self):
        lines = self.analyzer.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_interfaces(self):
        self.analyzer.set_view("interfaces")
        lines = self.analyzer.render_interfaces()
        self.assertIsInstance(lines, list)

    def test_render_connections(self):
        self.analyzer.set_view("connections")
        lines = self.analyzer.render_connections()
        self.assertIsInstance(lines, list)

    def test_render_capture(self):
        self.analyzer.set_view("capture")
        lines = self.analyzer.render_capture()
        self.assertIsInstance(lines, list)

    def test_render_ping(self):
        self.analyzer.set_view("ping")
        lines = self.analyzer.render_ping()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.analyzer.handle_key("i")
        self.assertEqual(result, "interfaces")

    def test_total_rates(self):
        self.assertGreater(self.analyzer.total_rx_rate, 0)
        self.assertGreater(self.analyzer.total_tx_rate, 0)


class TestNetworkInterface(unittest.TestCase):

    def test_display(self):
        iface = NetworkInterface("eth0", "00:00:00:00:00:00", "192.168.1.1", status=InterfaceStatus.UP)
        self.assertIn("eth0", iface.display)

    def test_sparkline(self):
        iface = NetworkInterface("eth0")
        iface.rx_history = [100, 200, 300, 400, 500]
        spark = iface.sparkline_rx(5)
        self.assertEqual(len(spark), 5)

    def test_format_bytes(self):
        self.assertIn("GB", NetworkInterface._format_bytes(2_000_000_000))
        self.assertIn("MB", NetworkInterface._format_bytes(5_000_000))


class TestConnection(unittest.TestCase):

    def test_display(self):
        conn = Connection("10.0.0.1", 80, "10.0.0.2", 12345, Protocol.HTTP)
        self.assertIn("10.0.0.1", conn.display)

    def test_duration_str(self):
        conn = Connection("10.0.0.1", 80, "10.0.0.2", 12345, Protocol.TCP, ConnectionState.ESTABLISHED, "", 0, 0, 0, 3661)
        self.assertIn("h", conn.duration_str)


# ─── Disk Health Tests ───────────────────────────────────────────────────


class TestDiskHealthMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = DiskHealthMonitor()

    def test_initial_state(self):
        self.assertEqual(self.monitor.view_mode, "overview")
        self.assertGreater(len(self.monitor.disks), 0)

    def test_disk_health(self):
        disk = self.monitor.disks[0]
        self.assertGreater(disk.health_score, 0)
        self.assertEqual(disk.health_status, HealthStatus.EXCELLENT)

    def test_smart_attributes(self):
        disk = self.monitor.disks[0]
        self.assertGreater(len(disk.attributes), 0)

    def test_benchmark(self):
        disk = self.monitor.disks[0]
        self.assertIsNotNone(disk.benchmark)
        self.assertGreater(disk.benchmark.read_speed_mbps, 0)

    def test_temperature_history(self):
        disk = self.monitor.disks[0]
        self.assertGreater(len(disk.temperature_history), 0)

    def test_alerts(self):
        total = self.monitor.total_alerts
        self.assertGreater(total, 0)

    def test_navigation(self):
        self.monitor.select_disk_down()
        self.assertEqual(self.monitor.selected_disk, 1)
        self.monitor.select_disk_up()
        self.assertEqual(self.monitor.selected_disk, 0)

    def test_render_overview(self):
        lines = self.monitor.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_smart(self):
        self.monitor.set_view("smart")
        lines = self.monitor.render_smart()
        self.assertIsInstance(lines, list)

    def test_render_benchmark(self):
        self.monitor.set_view("benchmark")
        lines = self.monitor.render_benchmark()
        self.assertIsInstance(lines, list)

    def test_render_temperature(self):
        self.monitor.set_view("temperature")
        lines = self.monitor.render_temperature()
        self.assertIsInstance(lines, list)

    def test_render_alerts(self):
        self.monitor.set_view("alerts")
        lines = self.monitor.render_alerts()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.monitor.handle_key("Enter")
        self.assertEqual(result, "smart")


class TestDiskHealth(unittest.TestCase):

    def test_health_bar(self):
        disk = DiskHealth("/dev/sda", "Test", "SN", DiskType.NVME, 1000, health_score=80)
        bar = disk.health_bar
        self.assertIn("█", bar)

    def test_wear_bar(self):
        disk = DiskHealth("/dev/sda", "Test", "SN", DiskType.NVME, 1000, wear_level_pct=50)
        bar = disk.wear_bar
        self.assertIn("█", bar)

    def test_lifespan(self):
        disk = DiskHealth("/dev/sda", "Test", "SN", DiskType.NVME, 1000, wear_level_pct=20)
        self.assertAlmostEqual(disk.lifespan_pct, 80.0)


class TestSMARTAttribute(unittest.TestCase):

    def test_status(self):
        attr = SMARTAttribute(5, "Test", 200, 200, 0, 0)
        self.assertEqual(attr.status, "OK")

    def test_bar(self):
        attr = SMARTAttribute(5, "Test", 128, 128, 0, 0)
        bar = attr.bar
        self.assertIn("█", bar)


class TestBenchmarkResult(unittest.TestCase):

    def test_read_speed_str(self):
        bm = BenchmarkResult("Test", read_speed_mbps=1500.0)
        self.assertIn("GB/s", bm.read_speed_str)

    def test_write_speed_str(self):
        bm = BenchmarkResult("Test", write_speed_mbps=500.0)
        self.assertIn("MB/s", bm.write_speed_str)


# ─── Job Scheduler Tests ─────────────────────────────────────────────────


class TestJobScheduler(unittest.TestCase):

    def setUp(self):
        self.scheduler = JobScheduler()

    def test_initial_state(self):
        self.assertEqual(self.scheduler.view_mode, "jobs")
        self.assertGreater(len(self.scheduler.jobs), 0)
        self.assertGreater(len(self.scheduler.templates), 0)

    def test_create_job(self):
        cron = CronExpression("0", "12", "*", "*", "*")
        job = self.scheduler.create_job("Test Job", "echo hello", cron)
        self.assertIsNotNone(job)
        self.assertEqual(job.name, "Test Job")
        self.assertEqual(len(self.scheduler.jobs), 9)

    def test_delete_job(self):
        initial = len(self.scheduler.jobs)
        self.assertTrue(self.scheduler.delete_job(initial - 1))
        self.assertEqual(len(self.scheduler.jobs), initial - 1)

    def test_toggle_job(self):
        status = self.scheduler.toggle_job(0)
        self.assertEqual(status, JobStatus.PAUSED)
        status = self.scheduler.toggle_job(0)
        self.assertEqual(status, JobStatus.ACTIVE)

    def test_run_job_now(self):
        run = self.scheduler.run_job_now(0)
        self.assertIsNotNone(run)
        self.assertEqual(run.status, RunStatus.SUCCESS)

    def test_create_from_template(self):
        initial = len(self.scheduler.jobs)
        job = self.scheduler.create_from_template(0)
        self.assertIsNotNone(job)
        self.assertEqual(len(self.scheduler.jobs), initial + 1)

    def test_navigation(self):
        self.scheduler.select_down()
        self.assertEqual(self.scheduler.selected_index, 1)
        self.scheduler.select_up()
        self.assertEqual(self.scheduler.selected_index, 0)

    def test_stats(self):
        self.assertGreater(self.scheduler.active_count, 0)
        self.assertGreater(self.scheduler.total_runs, 0)
        self.assertGreater(self.scheduler.success_rate, 0)

    def test_render_jobs(self):
        lines = self.scheduler.render_jobs()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_runs(self):
        self.scheduler.set_view("runs")
        lines = self.scheduler.render_runs()
        self.assertIsInstance(lines, list)

    def test_render_templates(self):
        self.scheduler.set_view("templates")
        lines = self.scheduler.render_templates()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.scheduler.handle_key("Enter")
        self.assertEqual(result, "run_now")


class TestCronExpression(unittest.TestCase):

    def test_display(self):
        cron = CronExpression("0", "2", "*", "*", "*")
        self.assertEqual(cron.display, "0 2 * * *")

    def test_human_readable(self):
        cron = CronExpression("30", "8", "*", "*", "1")
        self.assertIn("Mon", cron.human_readable)

    def test_frequency(self):
        cron = CronExpression("*", "*", "*", "*", "*")
        self.assertEqual(cron.frequency, "Every minute")


class TestResourceLimits(unittest.TestCase):

    def test_display(self):
        limits = ResourceLimits(50, 2048, "low", 3600, 15)
        display = limits.display
        self.assertIn("CPU", display)
        self.assertIn("RAM", display)


class TestJobRun(unittest.TestCase):

    def test_duration_str(self):
        run = JobRun("r1", "j1", "Test", started_at=time.time() - 30, completed_at=time.time())
        self.assertIn("s", run.duration_str)

    def test_display(self):
        run = JobRun("r1", "j1", "Test Job", started_at=time.time())
        self.assertIn("Test Job", run.display)


class TestJob(unittest.TestCase):

    def test_display(self):
        job = Job("Test", "echo hello")
        self.assertIn("Test", job.display)

    def test_success_rate(self):
        job = Job("Test", "echo hello", run_count=10, success_count=8)
        self.assertAlmostEqual(job.success_rate, 80.0)

    def test_last_run_str(self):
        job = Job("Test", "echo hello", last_run=time.time() - 300)
        self.assertIn("m ago", job.last_run_str)


if __name__ == "__main__":
    unittest.main()
