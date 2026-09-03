"""
Tests for Hardware Monitor, Virtual Assistant, and Presentation Tool.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.hardware_monitor import (
    HardwareMonitor, CPUCore, GPUInfo, FanInfo, TemperatureSensor, RAMInfo,
    ThermalStatus, FanMode
)
from ui.virtual_assistant import (
    VirtualAssistant, Message, Reminder, TimerItem, QuickAction,
    AssistantIntent, MessageRole
)
from ui.presentation_tool import (
    PresentationTool, Presentation, Slide, SlideElement,
    SlideLayout, TransitionType
)


# ─── Hardware Monitor Tests ──────────────────────────────────────────────


class TestHardwareMonitor(unittest.TestCase):

    def setUp(self):
        self.hm = HardwareMonitor()

    def test_initial_state(self):
        self.assertEqual(self.hm.view_mode, "overview")
        self.assertGreater(len(self.hm.cpu_cores), 0)
        self.assertGreater(len(self.hm.fans), 0)
        self.assertGreater(len(self.hm.sensors), 0)

    def test_cpu_stats(self):
        self.assertGreater(self.hm.cpu_avg_usage, 0)
        self.assertGreater(self.hm.cpu_avg_temp, 0)
        self.assertGreater(self.hm.cpu_avg_freq, 0)

    def test_gpu_stats(self):
        gpu = self.hm.gpu
        self.assertGreater(gpu.vram_total_mb, 0)
        self.assertGreater(gpu.usage_pct, 0)

    def test_ram_stats(self):
        ram = self.hm.ram
        self.assertGreater(ram.total_mb, 0)
        self.assertGreater(ram.usage_pct, 0)

    def test_total_power(self):
        self.assertGreater(self.hm.total_power, 0)

    def test_fan_control(self):
        self.assertTrue(self.hm.set_fan_mode(0, FanMode.PERFORMANCE))
        self.assertEqual(self.hm.fans[0].mode, FanMode.PERFORMANCE)

    def test_fan_speed(self):
        self.assertTrue(self.hm.set_fan_speed(0, 80))
        self.assertEqual(self.hm.fans[0].speed_pct, 80)

    def test_navigation(self):
        self.hm.set_view("fans")
        self.hm.select_down()
        self.assertEqual(self.hm.selected_index, 1)
        self.hm.select_up()
        self.assertEqual(self.hm.selected_index, 0)

    def test_render_overview(self):
        lines = self.hm.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_cpu(self):
        self.hm.set_view("cpu")
        lines = self.hm.render_cpu()
        self.assertIsInstance(lines, list)

    def test_render_gpu(self):
        self.hm.set_view("gpu")
        lines = self.hm.render_gpu()
        self.assertIsInstance(lines, list)

    def test_render_memory(self):
        self.hm.set_view("memory")
        lines = self.hm.render_memory()
        self.assertIsInstance(lines, list)

    def test_render_fans(self):
        self.hm.set_view("fans")
        lines = self.hm.render_fans()
        self.assertIsInstance(lines, list)

    def test_render_temps(self):
        self.hm.set_view("temps")
        lines = self.hm.render_temps()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.hm.handle_key("c")
        self.assertEqual(result, "cpu")


class TestCPUCore(unittest.TestCase):

    def test_usage_bar(self):
        core = CPUCore(0, usage_pct=50.0)
        bar = core.usage_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_temp_status(self):
        core = CPUCore(0, temperature_c=35)
        self.assertEqual(core.temp_status, ThermalStatus.COOL)

    def test_sparkline(self):
        core = CPUCore(0)
        core.usage_history = [10, 20, 30, 40, 50]
        spark = core.sparkline
        self.assertIsInstance(spark, str)


class TestGPUInfo(unittest.TestCase):

    def test_vram_bar(self):
        gpu = GPUInfo(vram_total_mb=12000, vram_used_mb=6000)
        bar = gpu.vram_bar
        self.assertIn("█", bar)

    def test_power_pct(self):
        gpu = GPUInfo(power_watts=100, power_limit_watts=200)
        self.assertAlmostEqual(gpu.power_pct, 50.0)


class TestFanInfo(unittest.TestCase):

    def test_display(self):
        fan = FanInfo("CPU", 1500, 3000, 50, FanMode.AUTO)
        self.assertIn("CPU", fan.display)
        self.assertIn("1500", fan.display)

    def test_status(self):
        fan = FanInfo("CPU", 0, 3000, 10)
        self.assertEqual(fan.status, "silent")


# ─── Virtual Assistant Tests ─────────────────────────────────────────────


class TestVirtualAssistant(unittest.TestCase):

    def setUp(self):
        self.va = VirtualAssistant()

    def test_initial_state(self):
        self.assertEqual(self.va.view_mode, "chat")
        self.assertGreater(len(self.va.messages), 0)

    def test_process_time(self):
        msg = self.va.process_input("what time is it?")
        self.assertEqual(msg.intent, AssistantIntent.TIME)

    def test_process_weather(self):
        msg = self.va.process_input("what's the weather?")
        self.assertEqual(msg.intent, AssistantIntent.WEATHER)

    def test_process_shutdown(self):
        msg = self.va.process_input("shutdown")
        self.assertEqual(msg.intent, AssistantIntent.SYSTEM)

    def test_process_help(self):
        msg = self.va.process_input("help")
        self.assertEqual(msg.intent, AssistantIntent.HELP)

    def test_process_calculator(self):
        msg = self.va.process_input("calculate 42 * 7")
        self.assertEqual(msg.intent, AssistantIntent.CALCULATE)

    def test_process_reminder(self):
        msg = self.va.process_input("remind me to check email in 30 minutes")
        self.assertEqual(msg.intent, AssistantIntent.REMINDER)
        self.assertGreater(len(self.va.reminders), 0)

    def test_process_timer(self):
        msg = self.va.process_input("set timer for 5 minutes")
        self.assertEqual(msg.intent, AssistantIntent.TIMER)
        self.assertGreater(len(self.va.timers), 0)

    def test_tick_timers(self):
        self.va.process_input("set timer for 1 second")
        self.va.timers[0].remaining_seconds = 1
        self.va.timers[0].running = True
        expired = self.va.tick_timers()
        self.assertGreater(len(self.va.timers), 0)

    def test_delete_reminder(self):
        self.va.process_input("remind me to test in 5 minutes")
        self.assertTrue(self.va.delete_reminder(0))

    def test_quick_actions(self):
        self.assertGreater(len(self.va.quick_actions), 0)

    def test_navigation(self):
        self.va.set_view("reminders")
        self.va.process_input("remind me to test in 5 minutes")
        self.va.select_down()
        self.assertGreaterEqual(self.va.selected_index, 0)
        self.va.select_up()
        self.assertEqual(self.va.selected_index, 0)

    def test_render_chat(self):
        lines = self.va.render_chat()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_reminders(self):
        self.va.set_view("reminders")
        lines = self.va.render_reminders()
        self.assertIsInstance(lines, list)

    def test_render_actions(self):
        self.va.set_view("actions")
        lines = self.va.render_actions()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.va.handle_key("a")
        self.assertEqual(result, "actions")


class TestMessage(unittest.TestCase):

    def test_display(self):
        msg = Message(MessageRole.USER, "Hello")
        self.assertIn("Hello", msg.display)
        self.assertIn("👤", msg.display)


class TestReminder(unittest.TestCase):

    def test_time_until(self):
        r = Reminder("Test", remind_at=time.time() + 300)
        self.assertIn("m", r.time_until)

    def test_display(self):
        r = Reminder("Test Task")
        self.assertIn("Test Task", r.display)


class TestTimerItem(unittest.TestCase):

    def test_display_time(self):
        t = TimerItem("Test", total_seconds=125, remaining_seconds=125)
        self.assertEqual(t.display_time, "02:05")

    def test_progress_bar(self):
        t = TimerItem("Test", total_seconds=100, remaining_seconds=50)
        bar = t.progress_bar
        self.assertIn("█", bar)


# ─── Presentation Tool Tests ─────────────────────────────────────────────


class TestPresentationTool(unittest.TestCase):

    def setUp(self):
        self.pt = PresentationTool()

    def test_initial_state(self):
        self.assertEqual(self.pt.view_mode, "library")
        self.assertGreater(len(self.pt.presentations), 0)

    def test_select_presentation(self):
        self.assertTrue(self.pt.select_presentation(0))
        self.assertIsNotNone(self.pt.current_presentation)

    def test_add_slide(self):
        self.pt.select_presentation(0)
        initial = self.pt.current_presentation.slide_count
        slide = self.pt.add_slide(SlideLayout.BLANK)
        self.assertIsNotNone(slide)
        self.assertEqual(self.pt.current_presentation.slide_count, initial + 1)

    def test_delete_slide(self):
        self.pt.select_presentation(0)
        initial = self.pt.current_presentation.slide_count
        self.assertTrue(self.pt.delete_slide(initial - 1))
        self.assertEqual(self.pt.current_presentation.slide_count, initial - 1)

    def test_duplicate_slide(self):
        self.pt.select_presentation(0)
        initial = self.pt.current_presentation.slide_count
        slide = self.pt.duplicate_slide(0)
        self.assertIsNotNone(slide)
        self.assertEqual(self.pt.current_presentation.slide_count, initial + 1)

    def test_set_transition(self):
        self.pt.select_presentation(0)
        self.assertTrue(self.pt.set_transition(0, TransitionType.ZOOM))

    def test_presenter_mode(self):
        self.pt.select_presentation(0)
        self.assertTrue(self.pt.start_presentation())
        self.assertTrue(self.pt.presenter_active)
        self.assertTrue(self.pt.next_slide())
        self.assertTrue(self.pt.prev_slide())
        self.pt.stop_presentation()
        self.assertFalse(self.pt.presenter_active)

    def test_navigation(self):
        self.pt.select_presentation(0)
        self.pt.set_view("editor")
        self.pt.select_down()
        self.assertEqual(self.pt.selected_index, 1)
        self.pt.select_up()
        self.assertEqual(self.pt.selected_index, 0)

    def test_render_library(self):
        lines = self.pt.render_library()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_editor(self):
        self.pt.select_presentation(0)
        self.pt.set_view("editor")
        lines = self.pt.render_editor()
        self.assertIsInstance(lines, list)

    def test_render_presenter(self):
        self.pt.select_presentation(0)
        self.pt.start_presentation()
        lines = self.pt.render_presenter()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.pt.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestSlide(unittest.TestCase):

    def test_display_title(self):
        slide = Slide(SlideLayout.TITLE, "My Title", slide_number=1)
        self.assertIn("My Title", slide.display_title)

    def test_layout_icon(self):
        slide = Slide(SlideLayout.TITLE)
        self.assertEqual(slide.layout_icon, "📰")


class TestPresentation(unittest.TestCase):

    def test_slide_count(self):
        p = Presentation("Test")
        p.slides = [Slide(), Slide(), Slide()]
        self.assertEqual(p.slide_count, 3)

    def test_visible_slides(self):
        p = Presentation("Test")
        s1 = Slide()
        s2 = Slide(hidden=True)
        p.slides = [s1, s2]
        self.assertEqual(p.visible_slides, 1)


if __name__ == "__main__":
    unittest.main()
