import unittest
import time


class TestVMManager(unittest.TestCase):
    def setUp(self):
        from ui.vm_manager import VMManager, VMState, VMOSType
        self.vm = VMManager()
        self.VS = VMState
        self.VOS = VMOSType

    def test_initial_state(self):
        self.assertGreater(len(self.vm.vms), 0)
        self.assertGreater(len(self.vm.templates), 0)
        self.assertGreater(len(self.vm.snapshots), 0)

    def test_start_vm(self):
        stopped = next(v for v in self.vm.vms if v.state == self.VS.STOPPED)
        result = self.vm.start_vm(stopped.name)
        self.assertTrue(result)
        self.assertEqual(stopped.state, self.VS.RUNNING)

    def test_stop_vm(self):
        running = next(v for v in self.vm.vms if v.state == self.VS.RUNNING)
        result = self.vm.stop_vm(running.name)
        self.assertTrue(result)
        self.assertEqual(running.state, self.VS.STOPPED)

    def test_pause_resume(self):
        running = next(v for v in self.vm.vms if v.state == self.VS.RUNNING)
        self.vm.pause_vm(running.name)
        self.assertEqual(running.state, self.VS.PAUSED)
        self.vm.resume_vm(running.name)
        self.assertEqual(running.state, self.VS.RUNNING)

    def test_create_vm(self):
        from ui.vm_manager import VMTemplate
        template = self.vm.templates[0]
        new_vm = self.vm.create_vm(template=template, name="test-vm")
        self.assertEqual(new_vm.name, "test-vm")
        self.assertIn(new_vm, self.vm.vms)

    def test_delete_vm(self):
        result = self.vm.delete_vm("freebsd-jail")
        self.assertTrue(result)

    def test_create_snapshot(self):
        snap = self.vm.create_snapshot("nyrqis-dev", "test-snap", "Test snapshot")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.vm_name, "nyrqis-dev")

    def test_get_running_vms(self):
        running = self.vm.get_running_vms()
        self.assertGreater(len(running), 0)
        for v in running:
            self.assertEqual(v.state, self.VS.RUNNING)

    def test_search(self):
        results = self.vm.search("nyrqis")
        self.assertGreater(len(results), 0)

    def test_get_stats(self):
        stats = self.vm.get_stats()
        self.assertIn("total_vms", stats)
        self.assertIn("running", stats)

    def test_vm_state_icon(self):
        from ui.vm_manager import VirtualMachine
        v = VirtualMachine(name="test", state=self.VS.RUNNING)
        self.assertEqual(v.state_icon, "🟢")

    def test_vm_os_icon(self):
        from ui.vm_manager import VirtualMachine
        v = VirtualMachine(name="test", os_type=self.VOS.WINDOWS)
        self.assertEqual(v.os_icon, "🪟")


class TestTimeZoneManager(unittest.TestCase):
    def setUp(self):
        from ui.timezone_manager import TimeZoneManager
        self.tzm = TimeZoneManager()

    def test_initial_state(self):
        self.assertGreater(len(self.tzm.timezones), 0)
        self.assertGreater(len(self.tzm.world_clocks), 0)
        self.assertGreater(len(self.tzm.meetings), 0)

    def test_add_world_clock(self):
        result = self.tzm.add_world_clock("Mumbai", label="India Office")
        self.assertTrue(result)

    def test_add_world_clock_not_found(self):
        result = self.tzm.add_world_clock("Nonexistent")
        self.assertFalse(result)

    def test_remove_world_clock(self):
        result = self.tzm.remove_world_clock("Berlin")
        self.assertTrue(result)

    def test_add_meeting(self):
        from ui.timezone_manager import MeetingSlot, TimeZone
        meeting = MeetingSlot(name="Test Meeting", local_hour=10, duration_minutes=30,
                              timezone=TimeZone(name="UTC"))
        self.tzm.add_meeting(meeting)
        self.assertIn(meeting, self.tzm.meetings)

    def test_remove_meeting(self):
        result = self.tzm.remove_meeting("Daily Standup")
        self.assertTrue(result)

    def test_get_meetings_today(self):
        meetings = self.tzm.get_meetings_today("Mon")
        self.assertGreater(len(meetings), 0)

    def test_convert_time(self):
        from ui.timezone_manager import TimeZone
        ny = self.tzm.timezones[0]
        tokyo = self.tzm.timezones[4]
        h, m = self.tzm.convert_time(9, 0, ny, tokyo)
        self.assertEqual(m, 0)
        self.assertIn(h, [22, 23])

    def test_get_time_difference(self):
        diff = self.tzm.get_time_difference(self.tzm.timezones[0], self.tzm.timezones[4])
        self.assertIn("h", diff)

    def test_search(self):
        results = self.tzm.search("tokyo")
        self.assertGreater(len(results), 0)

    def test_get_stats(self):
        stats = self.tzm.get_stats()
        self.assertIn("timezones", stats)
        self.assertIn("meetings", stats)

    def test_meeting_time_display(self):
        from ui.timezone_manager import MeetingSlot, TimeZone
        m = MeetingSlot(name="test", local_hour=9, local_minute=30,
                        timezone=TimeZone(name="UTC"))
        self.assertEqual(m.time_display, "09:30")

    def test_meeting_duration_display(self):
        from ui.timezone_manager import MeetingSlot, TimeZone
        m = MeetingSlot(name="test", duration_minutes=90,
                        timezone=TimeZone(name="UTC"))
        self.assertEqual(m.duration_display, "1h30m")

    def test_timezone_current_offset(self):
        from ui.timezone_manager import TimeZone
        tz = TimeZone(name="test", utc_offset=5.5)
        self.assertIn("05:30", tz.current_offset)


class TestSensorMonitor(unittest.TestCase):
    def setUp(self):
        from ui.sensor_monitor import SensorMonitor, SensorType
        self.sm = SensorMonitor()
        self.ST = SensorType

    def test_initial_state(self):
        self.assertGreater(len(self.sm.sensors), 0)
        self.assertGreater(len(self.sm.fan_profiles), 0)
        self.assertIsNotNone(self.sm.active_profile)

    def test_get_temperatures(self):
        temps = self.sm.get_temperatures()
        self.assertGreater(len(temps), 0)
        for t in temps:
            self.assertEqual(t.sensor_type, self.ST.TEMPERATURE)

    def test_get_fans(self):
        fans = self.sm.get_fans()
        self.assertGreater(len(fans), 0)

    def test_get_power(self):
        power = self.sm.get_power()
        self.assertGreater(len(power), 0)

    def test_get_max_temperature(self):
        max_temp = self.sm.get_max_temperature()
        self.assertIsNotNone(max_temp)
        temps = self.sm.get_temperatures()
        self.assertEqual(max_temp.value, max(t.value for t in temps))

    def test_set_fan_profile(self):
        result = self.sm.set_fan_profile("Performance")
        self.assertTrue(result)
        self.assertEqual(self.sm.active_profile.name, "Performance")

    def test_set_fan_profile_not_found(self):
        result = self.sm.set_fan_profile("Nonexistent")
        self.assertFalse(result)

    def test_acknowledge_alert(self):
        if self.sm.alerts:
            result = self.sm.acknowledge_alert(0)
            self.assertTrue(result)
            self.assertTrue(self.sm.alerts[0].acknowledged)

    def test_check_thresholds(self):
        alerts = self.sm.check_thresholds()
        self.assertIsInstance(alerts, list)

    def test_search(self):
        results = self.sm.search("cpu")
        self.assertGreater(len(results), 0)

    def test_get_stats(self):
        stats = self.sm.get_stats()
        self.assertIn("total_sensors", stats)
        self.assertIn("max_temp", stats)

    def test_sensor_status_icon(self):
        from ui.sensor_monitor import SensorReading
        s = SensorReading(name="test", sensor_type=self.ST.TEMPERATURE,
                          value=40.0, high_threshold=80.0, critical_threshold=95.0)
        self.assertEqual(s.status_icon, "🟢")
        s.value = 85.0
        self.assertEqual(s.status_icon, "🟠")

    def test_sensor_bar(self):
        from ui.sensor_monitor import SensorReading
        s = SensorReading(name="test", value=50.0, max_value=100.0)
        bar = s.bar
        self.assertEqual(len(bar), 20)

    def test_fan_profile_description(self):
        from ui.sensor_monitor import FanProfile
        fp = FanProfile(name="Silent")
        self.assertIn("Quiet", fp.description)


if __name__ == "__main__":
    unittest.main()
