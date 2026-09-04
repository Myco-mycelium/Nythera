"""
Tests for NFC Manager and Power Profile Manager.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.nfc_manager import (
    NFCManager, NFCTag, NDEFRecord, AutomationTrigger, TagOperation,
    ReaderConfig, NFCTagType, NDEFRecordType, TagState, TriggerAction,
    ReaderBackend,
)
from ui.power_profiles import (
    PowerProfileManager, BatteryInfo, PowerLimit, ThermalSensor,
    FanProfile, PowerEvent, WakeSchedule, PowerProfileConfig,
    PowerProfile, CPUGovernor, ThermalPolicy, ChargingState, CHARGING_ICONS,
)


# ─── NFC Manager Tests ────────────────────────────────────────────────────


class TestNDEFRecord(unittest.TestCase):
    def test_create(self):
        r = NDEFRecord(NDEFRecordType.TEXT, payload="Hello")
        self.assertEqual(r.payload, "Hello")

    def test_display_payload(self):
        r = NDEFRecord(NDEFRecordType.URL, url="https://nyrqis.dev")
        self.assertEqual(r.display_payload, "WiFi Config" if r.record_type == NDEFRecordType.WIFI else r.payload)

    def test_display_payload_wifi(self):
        r = NDEFRecord(NDEFRecordType.WIFI, payload="config")
        self.assertEqual(r.display_payload, "WiFi Config")


class TestNFCTag(unittest.TestCase):
    def test_create(self):
        tag = NFCTag(uid="04A1B2C3D4E5F6", tag_type=NFCTagType.NTAG216)
        self.assertEqual(tag.uid, "04A1B2C3D4E5F6")

    def test_uid_display(self):
        tag = NFCTag(uid="04A1B2C3D4E5F6")
        self.assertIn(":", tag.uid_display)

    def test_state_icon(self):
        tag = NFCTag(state=TagState.PRESENT)
        self.assertEqual(tag.state_icon, "🟢")

    def test_capacity_bar(self):
        tag = NFCTag(max_data_bytes=144, used_data_bytes=48)
        bar = tag.capacity_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_capacity_str(self):
        tag = NFCTag(max_data_bytes=144, used_data_bytes=48)
        self.assertEqual(tag.capacity_str, "48/144 bytes")

    def test_display_name(self):
        tag = NFCTag(uid="04A1B2", Nickname="Desk Tag")
        self.assertEqual(tag.display_name, "Desk Tag")

    def test_display_name_default(self):
        tag = NFCTag(uid="04A1B2")
        self.assertEqual(tag.display_name, tag.uid_display)


class TestAutomationTrigger(unittest.TestCase):
    def test_create(self):
        t = AutomationTrigger("Open URL", "04A1B2", TriggerAction.OPEN_URL, {"url": "https://test.com"})
        self.assertEqual(t.name, "Open URL")

    def test_action_icon(self):
        t = AutomationTrigger(action=TriggerAction.OPEN_URL)
        self.assertEqual(t.action_icon, "🌐")

    def test_param_display(self):
        t = AutomationTrigger(parameters={"url": "https://test.com"})
        self.assertIn("url=", t.param_display)


class TestTagOperation(unittest.TestCase):
    def test_create(self):
        op = TagOperation(time.time(), "read", "04A1B2", "NTAG213", True, "ok", 15)
        self.assertEqual(op.operation, "read")

    def test_icon(self):
        op = TagOperation(operation="read")
        self.assertEqual(op.icon, "📖")

    def test_status_icon(self):
        op = TagOperation(success=True)
        self.assertEqual(op.status_icon, "✅")


class TestNFCManager(unittest.TestCase):
    def setUp(self):
        self.mgr = NFCManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.tags), 0)
        self.assertGreater(len(self.mgr.triggers), 0)

    def test_selected_tag(self):
        tag = self.mgr.selected_tag
        self.assertIsNotNone(tag)

    def test_select_tag(self):
        self.mgr.select_tag(2)
        self.assertEqual(self.mgr._selected_tag, 2)

    def test_read_tag(self):
        tag = self.mgr.read_tag(0)
        self.assertIsNotNone(tag)
        self.assertEqual(tag.state, TagState.PRESENT)

    def test_write_tag(self):
        record = NDEFRecord(NDEFRecordType.TEXT, payload="Test write")
        result = self.mgr.write_tag(0, record)
        self.assertTrue(result)
        self.assertGreater(len(self.mgr.tags[0].ndef_records), 2)

    def test_write_locked_tag(self):
        record = NDEFRecord(NDEFRecordType.TEXT, payload="Test")
        result = self.mgr.write_tag(3, record)  # locked tag
        self.assertFalse(result)

    def test_erase_tag(self):
        result = self.mgr.erase_tag(0)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.tags[0].ndef_records), 0)

    def test_lock_tag(self):
        result = self.mgr.lock_tag(0)
        self.assertTrue(result)
        self.assertTrue(self.mgr.tags[0].is_locked)

    def test_simulate_tag(self):
        count = len(self.mgr.tags)
        tag = self.mgr.simulate_tag()
        self.assertEqual(len(self.mgr.tags), count + 1)
        self.assertEqual(tag.state, TagState.PRESENT)

    def test_fire_trigger(self):
        result = self.mgr.fire_trigger(0)
        self.assertTrue(result)
        self.assertGreater(self.mgr.triggers[0].trigger_count, 45)

    def test_toggle_trigger(self):
        result = self.mgr.toggle_trigger(4)  # disabled trigger
        self.assertTrue(result)
        self.assertTrue(self.mgr.triggers[4].enabled)

    def test_add_trigger(self):
        count = len(self.mgr.triggers)
        trigger = self.mgr.add_trigger("Test", "04A1B2", TriggerAction.RUN_COMMAND, {"cmd": "ls"})
        self.assertEqual(len(self.mgr.triggers), count + 1)

    def test_remove_trigger(self):
        count = len(self.mgr.triggers)
        result = self.mgr.remove_trigger(4)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.triggers), count - 1)

    def test_toggle_reader(self):
        self.assertTrue(self.mgr.reader_connected)
        self.mgr.toggle_reader()
        self.assertFalse(self.mgr.reader_connected)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_tag, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_tag, 0)

    def test_search_tags(self):
        results = self.mgr.search_tags("desk")
        self.assertGreater(len(results), 0)

    def test_search_triggers(self):
        results = self.mgr.search_triggers("wifi")
        self.assertGreater(len(results), 0)

    def test_get_present_tags(self):
        present = self.mgr.get_present_tags()
        self.assertGreater(len(present), 0)

    def test_get_triggers_for_tag(self):
        triggers = self.mgr.get_triggers_for_tag("04A1B2C3D4E5F6")
        self.assertGreater(len(triggers), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_tags", stats)
        self.assertIn("total_reads", stats)


# ─── Power Profile Manager Tests ──────────────────────────────────────────


class TestBatteryInfo(unittest.TestCase):
    def test_create(self):
        b = BatteryInfo(charge_percent=73.0, health_percent=93.0)
        self.assertEqual(b.charge_percent, 73.0)

    def test_charge_bar(self):
        b = BatteryInfo(charge_percent=50)
        bar = b.charge_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_health_status(self):
        b = BatteryInfo(health_percent=95)
        self.assertIn("Excellent", b.health_status)

    def test_temp_status(self):
        b = BatteryInfo(temperature_c=60)
        self.assertIn("Hot", b.temp_status)

    def test_charge_display(self):
        b = BatteryInfo(charge_percent=73, charging_state=ChargingState.CHARGING)
        self.assertIn("73%", b.charge_display)


class TestPowerLimit(unittest.TestCase):
    def test_create(self):
        pl = PowerLimit("CPU", 125.0, 10.0, 170.0, 170.0)
        self.assertEqual(pl.current_watts, 125.0)

    def test_bar(self):
        pl = PowerLimit("CPU", 125.0, 10.0, 170.0)
        bar = pl.bar
        self.assertIn("█", bar)


class TestThermalSensor(unittest.TestCase):
    def test_create(self):
        ts = ThermalSensor("CPU", 62.0)
        self.assertEqual(ts.current_c, 62.0)

    def test_status(self):
        ts = ThermalSensor("CPU", 55.0, warning_c=80.0)
        self.assertIn("Normal", ts.status)

    def test_temp_bar(self):
        ts = ThermalSensor("CPU", 55.0)
        bar = ts.temp_bar
        self.assertIn("█", bar)


class TestFanProfile(unittest.TestCase):
    def test_create(self):
        fp = FanProfile("Silent", [(40, 25), (55, 35)])
        self.assertEqual(fp.name, "Silent")

    def test_curve_str(self):
        fp = FanProfile("Balanced", [(40, 30), (55, 45), (70, 65)])
        self.assertIn("40°C", fp.curve_str)


class TestPowerProfileConfig(unittest.TestCase):
    def test_create(self):
        p = PowerProfileConfig("Test", CPUGovernor.PERFORMANCE, cpu_max_percent=100)
        self.assertEqual(p.name, "Test")

    def test_cpu_bar(self):
        p = PowerProfileConfig(cpu_max_percent=50)
        bar = p.cpu_bar
        self.assertIn("█", bar)


class TestPowerProfileManager(unittest.TestCase):
    def setUp(self):
        self.mgr = PowerProfileManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.profiles), 0)
        self.assertGreater(len(self.mgr.power_limits), 0)
        self.assertGreater(len(self.mgr.thermal_sensors), 0)

    def test_activate_profile(self):
        result = self.mgr.activate_profile(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.active_profile, PowerProfile.PERFORMANCE)

    def test_set_cpu_max(self):
        result = self.mgr.set_cpu_max(0, 80)
        self.assertTrue(result)
        self.assertEqual(self.mgr.profiles[0].cpu_max_percent, 80)

    def test_set_gpu_max(self):
        result = self.mgr.set_gpu_max(0, 90)
        self.assertTrue(result)
        self.assertEqual(self.mgr.profiles[0].gpu_max_percent, 90)

    def test_toggle_turbo(self):
        initial = self.mgr.profiles[0].turbe_boost
        result = self.mgr.toggle_turbo(0)
        self.assertTrue(result)
        self.assertNotEqual(self.mgr.profiles[0].turbe_boost, initial)

    def test_toggle_pcie_aspm(self):
        result = self.mgr.toggle_pcie_aspm(0)
        self.assertTrue(result)

    def test_set_governor(self):
        result = self.mgr.set_governor(0, CPUGovernor.SCHEDUTIL)
        self.assertTrue(result)
        self.assertEqual(self.mgr.profiles[0].governor, CPUGovernor.SCHEDUTIL)

    def test_set_fan_profile(self):
        result = self.mgr.set_fan_profile("Silent")
        self.assertTrue(result)
        self.assertEqual(self.mgr.active_fan_profile, "Silent")

    def test_set_thermal_policy(self):
        result = self.mgr.set_thermal_policy(ThermalPolicy.PASSIVE)
        self.assertTrue(result)
        self.assertEqual(self.mgr.thermal_policy, ThermalPolicy.PASSIVE)

    def test_set_power_limit(self):
        result = self.mgr.set_power_limit(0, 150.0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.power_limits[0].current_watts, 150.0)

    def test_reset_power_limit(self):
        self.mgr.set_power_limit(0, 100.0)
        result = self.mgr.reset_power_limit(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.power_limits[0].current_watts, 170.0)

    def test_get_total_power(self):
        total = self.mgr.get_total_power()
        self.assertGreater(total, 0)

    def test_get_power_usage_bar(self):
        bar = self.mgr.get_power_usage_bar()
        self.assertIn("█", bar)

    def test_get_thermal_warnings(self):
        warnings = self.mgr.get_thermal_warnings()
        self.assertIsInstance(warnings, list)

    def test_toggle_wake_schedule(self):
        result = self.mgr.toggle_wake_schedule(0)
        self.assertTrue(result)
        self.assertFalse(self.mgr.wake_schedules[0].enabled)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_profile, 2)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_profile, 1)

    def test_search_profiles(self):
        results = self.mgr.search_profiles("perf")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("active_profile", stats)
        self.assertIn("battery_percent", stats)
        self.assertIn("total_power_w", stats)


class TestBatteryChargingState(unittest.TestCase):
    def test_charging_icons(self):
        self.assertEqual(CHARGING_ICONS[ChargingState.CHARGING], "🔌")
        self.assertEqual(CHARGING_ICONS[ChargingState.DISCHARGING], "🔋")
        self.assertEqual(CHARGING_ICONS[ChargingState.FULL], "✅")


class TestWakeSchedule(unittest.TestCase):
    def test_create(self):
        ws = WakeSchedule("Workday", True, 7, 0, [0, 1, 2, 3, 4])
        self.assertEqual(ws.time_str, "07:00")

    def test_days_str(self):
        ws = WakeSchedule("Workday", days=[0, 1, 2, 3, 4])
        self.assertIn("Mon", ws.days_str)


if __name__ == "__main__":
    unittest.main()
