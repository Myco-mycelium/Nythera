import unittest
import time


class TestNetworkMonitor(unittest.TestCase):
    def setUp(self):
        from ui.network_monitor import NetworkMonitor, ConnectionState, Protocol
        self.nm = NetworkMonitor()
        self.CS = ConnectionState
        self.Proto = Protocol

    def test_initial_state(self):
        self.assertGreater(len(self.nm.interfaces), 0)
        self.assertGreater(len(self.nm.connections), 0)
        self.assertGreater(len(self.nm.probes), 0)
        self.assertGreater(len(self.nm.bandwidth_history), 0)

    def test_get_connections_by_state(self):
        conns = self.nm.get_connections_by_state(self.CS.ESTABLISHED)
        self.assertGreater(len(conns), 0)
        for c in conns:
            self.assertEqual(c.state, self.CS.ESTABLISHED)

    def test_get_connections_by_process(self):
        conns = self.nm.get_connections_by_process("firefox")
        self.assertGreater(len(conns), 0)

    def test_search_connections(self):
        results = self.nm.search_connections("ssh")
        self.assertGreater(len(results), 0)

    def test_get_bandwidth_summary(self):
        summary = self.nm.get_bandwidth_summary()
        self.assertIn("rx_rate", summary)
        self.assertIn("tx_rate", summary)

    def test_get_stats(self):
        stats = self.nm.get_stats()
        self.assertIn("interfaces", stats)
        self.assertIn("connections", stats)

    def test_connection_state_icon(self):
        from ui.network_monitor import Connection
        c = Connection(state=self.CS.ESTABLISHED)
        self.assertEqual(c.state_icon, "🟢")

    def test_connection_remote_display(self):
        from ui.network_monitor import Connection
        c = Connection(remote_addr="1.1.1.1", remote_port=443)
        self.assertEqual(c.remote_display, "1.1.1.1:443")

    def test_probe_latency_status(self):
        from ui.network_monitor import LatencyProbe
        p = LatencyProbe(avg_ms=10)
        self.assertIn("🟢", p.latency_status)
        p.avg_ms = 60
        self.assertIn("🟠", p.latency_status)

    def test_probe_latency_bar(self):
        from ui.network_monitor import LatencyProbe
        p = LatencyProbe(avg_ms=50)
        bar = p.latency_bar
        self.assertEqual(len(bar), 20)

    def test_interface_status_icon(self):
        from ui.network_monitor import NetworkInterface
        i = NetworkInterface(is_up=True)
        self.assertEqual(i.status_icon, "🟢")


class TestBackupScheduler(unittest.TestCase):
    def setUp(self):
        from ui.backup_scheduler import BackupScheduler, BackupStatus, BackupType
        self.bs = BackupScheduler()
        self.BS = BackupStatus
        self.BT = BackupType

    def test_initial_state(self):
        self.assertGreater(len(self.bs.jobs), 0)
        self.assertGreater(len(self.bs.versions), 0)
        self.assertGreater(len(self.bs.cloud_syncs), 0)

    def test_create_job(self):
        job = self.bs.create_job("Test Job", backup_type=self.BT.FULL,
                                  source_paths=["/test"], destination="/backup/test")
        self.assertEqual(job.name, "Test Job")
        self.assertIn(job, self.bs.jobs)

    def test_delete_job(self):
        result = self.bs.delete_job("Cloud Sync")
        self.assertTrue(result)

    def test_toggle_job(self):
        result = self.bs.toggle_job("System Config")
        self.assertTrue(result)
        job = next(j for j in self.bs.jobs if j.name == "System Config")
        self.assertFalse(job.enabled)

    def test_run_job(self):
        job = self.bs.run_job("System Config")
        self.assertIsNotNone(job)
        self.assertEqual(job.status, self.BS.COMPLETED)

    def test_get_running_jobs(self):
        running = self.bs.get_running_jobs()
        self.assertIsInstance(running, list)

    def test_get_enabled_jobs(self):
        enabled = self.bs.get_enabled_jobs()
        self.assertGreater(len(enabled), 0)

    def test_search(self):
        results = self.bs.search("database")
        self.assertGreater(len(results), 0)

    def test_get_stats(self):
        stats = self.bs.get_stats()
        self.assertIn("jobs", stats)
        self.assertIn("total_size_gb", stats)

    def test_job_progress_bar(self):
        from ui.backup_scheduler import BackupJob
        j = BackupJob(name="test", progress=50.0)
        bar = j.progress_bar
        self.assertEqual(len(bar), 20)

    def test_job_status_icon(self):
        from ui.backup_scheduler import BackupJob
        j = BackupJob(name="test", status=self.BS.COMPLETED)
        self.assertEqual(j.status_icon, "✅")

    def test_job_size_display(self):
        from ui.backup_scheduler import BackupJob
        j = BackupJob(name="test", size_gb=0.5)
        self.assertIn("MB", j.size_display)
        j.size_gb = 5.0
        self.assertIn("GB", j.size_display)

    def test_version_size_display(self):
        from ui.backup_scheduler import BackupVersion
        v = BackupVersion(size_gb=0.8)
        self.assertIn("MB", v.size_display)


class TestGPUMonitor(unittest.TestCase):
    def setUp(self):
        from ui.gpu_monitor import GPUMonitor, GPUVendor
        self.gm = GPUMonitor()
        self.GV = GPUVendor

    def test_initial_state(self):
        self.assertEqual(self.gm.vendor, self.GV.NVIDIA)
        self.assertIsNotNone(self.gm.temperature)
        self.assertIsNotNone(self.gm.memory)
        self.assertIsNotNone(self.gm.power)
        self.assertGreater(len(self.gm.processes), 0)

    def test_get_gpu_utilization(self):
        util = self.gm.get_gpu_utilization()
        self.assertGreater(util, 0)
        self.assertLessEqual(util, 100)

    def test_get_memory_utilization(self):
        util = self.gm.get_memory_utilization()
        self.assertGreater(util, 0)
        self.assertLessEqual(util, 100)

    def test_get_temperature_history(self):
        history = self.gm.get_temperature_history()
        self.assertGreater(len(history), 0)

    def test_get_power_history(self):
        history = self.gm.get_power_history()
        self.assertGreater(len(history), 0)

    def test_get_top_processes(self):
        top = self.gm.get_top_processes(3)
        self.assertEqual(len(top), 3)
        self.assertGreaterEqual(top[0].gpu_memory_mb, top[1].gpu_memory_mb)

    def test_get_compute_processes(self):
        compute = self.gm.get_compute_processes()
        self.assertGreater(len(compute), 0)

    def test_get_stats(self):
        stats = self.gm.get_stats()
        self.assertIn("model", stats)
        self.assertIn("temperature", stats)

    def test_temperature_status(self):
        self.gm.temperature.current = 40
        self.assertIn("🟢", self.gm.temperature.status)
        self.gm.temperature.current = 75
        self.assertIn("🟠", self.gm.temperature.status)

    def test_temperature_bar(self):
        bar = self.gm.temperature.bar
        self.assertEqual(len(bar), 20)

    def test_memory_usage_bar(self):
        bar = self.gm.memory.usage_bar
        self.assertEqual(len(bar), 20)

    def test_memory_display(self):
        display = self.gm.memory.display
        self.assertIn("8.2", display)
        self.assertIn("24", display)

    def test_power_bar(self):
        bar = self.gm.power.bar
        self.assertEqual(len(bar), 20)

    def test_power_display(self):
        display = self.gm.power.display
        self.assertIn("285", display)
        self.assertIn("450", display)

    def test_process_util_bar(self):
        from ui.gpu_monitor import GPUProcess
        p = GPUProcess(gpu_utilization=50.0)
        bar = p.util_bar
        self.assertEqual(len(bar), 20)


if __name__ == "__main__":
    unittest.main()
