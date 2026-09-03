"""Tests for DrumMachine, SystemMonitorPro, MarkdownEditor"""
import time
import unittest

from ui.drum_machine import (
    DrumMachine, DrumPattern, PadHit, KitPreset, DrumPad,
    PatternMode, TimeSignature
)
from ui.system_monitor_pro import (
    SystemMonitorPro, CpuCore, GpuInfo, MemoryInfo, DiskInfo,
    ProcessInfo, Alert, MonitorView, ProcessSort, ProcessState, AlertSeverity
)
from ui.markdown_editor import (
    MarkdownEditor, MarkdownDocument, MarkdownBlock, DocumentStats,
    BlockType, HeadingLevel, ExportType
)


class TestDrumMachine(unittest.TestCase):
    def setUp(self):
        self.dm = DrumMachine()

    def test_initial_state(self):
        self.assertGreater(len(self.dm._patterns), 0)
        self.assertFalse(self.dm._is_playing)

    def test_select_pattern(self):
        self.dm.select_pattern(1)
        self.assertEqual(self.dm._selected_pattern, 1)
        self.assertEqual(self.dm.selected_pattern.name, "Funky Groove")

    def test_select_invalid(self):
        self.dm.select_pattern(99)
        self.assertEqual(self.dm._selected_pattern, 0)

    def test_total_patterns(self):
        self.assertEqual(self.dm.total_patterns, 4)

    def test_total_hits(self):
        self.assertGreater(self.dm.total_hits, 0)

    def test_play_stop(self):
        self.dm.start_playback()
        self.assertTrue(self.dm._is_playing)
        self.dm.stop_playback()
        self.assertFalse(self.dm._is_playing)

    def test_add_hit(self):
        pat = self.dm.selected_pattern
        before = pat.hit_count
        self.dm.add_hit(DrumPad.CRASH, 0, 100)
        self.assertEqual(pat.hit_count, before + 1)

    def test_remove_hit(self):
        pat = self.dm.selected_pattern
        self.dm.remove_hit(DrumPad.KICK, 0)

    def test_kits(self):
        self.assertEqual(len(self.dm._kits), 4)

    def test_pad_hit_velocity(self):
        hit = PadHit(DrumPad.KICK, 100, 0)
        self.assertIn("█", hit.velocity_display)

    def test_pattern_hit_count(self):
        pat = self.dm.selected_pattern
        self.assertGreater(pat.hit_count, 0)

    def test_pattern_pads_used(self):
        pat = self.dm.selected_pattern
        self.assertGreater(pat.pads_used, 0)

    def test_pattern_get_hit(self):
        pat = self.dm.selected_pattern
        hit = pat.get_hit(0, DrumPad.KICK)
        self.assertIsNotNone(hit)

    def test_mute_toggle(self):
        self.dm.toggle_mute(DrumPad.KICK)
        self.assertTrue(self.dm._mute_states.get(DrumPad.KICK, False))

    def test_render(self):
        lines = self.dm.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DRUM" in l for l in lines))

    def test_render_pads(self):
        lines = self.dm.render_pads()
        self.assertGreater(len(lines), 3)

    def test_render_pattern_detail(self):
        self.dm.select_pattern(0)
        lines = self.dm.render_pattern_detail()
        self.assertGreater(len(lines), 3)


class TestSystemMonitorPro(unittest.TestCase):
    def setUp(self):
        self.sm = SystemMonitorPro()

    def test_initial_state(self):
        self.assertGreater(len(self.sm._cpu_cores), 0)
        self.assertIsNotNone(self.sm._gpu)
        self.assertIsNotNone(self.sm._memory)

    def test_select_process(self):
        self.sm.select_process(1)
        self.assertIsNotNone(self.sm.selected_process)

    def test_total_processes(self):
        self.assertEqual(self.sm.total_processes, 15)

    def test_running_processes(self):
        self.assertGreater(self.sm.running_processes, 0)

    def test_sort_processes(self):
        self.sm.sort_processes(ProcessSort.MEMORY)
        self.assertEqual(self.sm._process_sort, ProcessSort.MEMORY)

    def test_uptime(self):
        self.assertIn("d", self.sm.uptime_display)

    def test_load(self):
        self.assertIn(" ", self.sm.load_display)

    def test_cpu_cores(self):
        self.assertEqual(len(self.sm._cpu_cores), 16)

    def test_cpu_thermal(self):
        core = self.sm._cpu_cores[0]
        self.assertIn("Cool", core.thermal_status) if core.temperature_c < 40 else self.assertIn("Normal", core.thermal_status)

    def test_cpu_usage_bar(self):
        core = self.sm._cpu_cores[0]
        self.assertIn("█", core.usage_bar)

    def test_gpu_memory(self):
        self.assertIn("GB", self.sm._gpu.memory_display)

    def test_gpu_temp(self):
        self.assertIn(self.sm._gpu.temp_status, ["🟢", "🟡", "🟠", "🔴"])

    def test_gpu_memory_bar(self):
        self.assertIn("█", self.sm._gpu.memory_bar)

    def test_memory(self):
        m = self.sm._memory
        self.assertIn("█", m.usage_bar)
        self.assertGreater(m.available_gb, 0)

    def test_disk(self):
        self.assertGreater(len(self.sm._disks), 0)
        d = self.sm._disks[0]
        self.assertIn("█", d.usage_bar)

    def test_alerts(self):
        self.assertGreater(len(self.sm._alerts), 0)
        self.assertGreater(self.sm.unacked_alerts, 0)

    def test_ack_alert(self):
        self.sm.acknowledge_alert(0)
        self.assertTrue(self.sm._alerts[0].acknowledged)

    def test_process_state_icon(self):
        p = ProcessInfo(1, "test", "user", 0, 0, ProcessState.RUNNING, 1)
        self.assertEqual(p.state_icon, "🟢")

    def test_process_memory_display(self):
        p = ProcessInfo(1, "test", "user", 0, 1500, ProcessState.RUNNING, 1)
        self.assertIn("GB", p.memory_display)

    def test_render(self):
        lines = self.sm.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("MONITOR" in l for l in lines))


class TestMarkdownEditor(unittest.TestCase):
    def setUp(self):
        self.me = MarkdownEditor()

    def test_initial_state(self):
        self.assertGreater(len(self.me._documents), 0)
        self.assertEqual(self.me._selected_doc, 0)

    def test_select_doc(self):
        self.me.select_doc(1)
        self.assertEqual(self.me._selected_doc, 1)

    def test_select_invalid(self):
        self.me.select_doc(99)
        self.assertEqual(self.me._selected_doc, 0)

    def test_total_documents(self):
        self.assertEqual(self.me.total_documents, 3)

    def test_total_words(self):
        self.assertGreater(self.me.total_words, 0)

    def test_doc_word_count(self):
        doc = self.me.selected_doc
        self.assertGreater(doc.word_count, 0)

    def test_doc_preview(self):
        doc = self.me.selected_doc
        self.assertGreater(len(doc.preview), 0)

    def test_compute_stats(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.words, 0)
        self.assertGreater(stats.characters, 0)
        self.assertGreater(stats.paragraphs, 0)

    def test_stats_read_time(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.reading_time_mins, 0)

    def test_stats_speak_time(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.speaking_time_mins, 0)

    def test_stats_headings(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.headings, 0)

    def test_stats_code_blocks(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.code_blocks, 0)

    def test_stats_tables(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.tables, 0)

    def test_stats_lists(self):
        stats = self.me.compute_stats()
        self.assertGreater(stats.lists, 0)

    def test_block_types(self):
        doc = self.me.selected_doc
        types = set(b.block_type for b in doc.blocks)
        self.assertIn(BlockType.HEADING, types)
        self.assertIn(BlockType.PARAGRAPH, types)

    def test_heading_level(self):
        doc = self.me.selected_doc
        h1 = [b for b in doc.blocks if b.block_type == BlockType.HEADING][0]
        self.assertEqual(h1.heading_level, HeadingLevel.H1)

    def test_render(self):
        lines = self.me.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("EDITOR" in l for l in lines))

    def test_render_preview(self):
        lines = self.me.render_preview()
        self.assertGreater(len(lines), 3)

    def test_render_stats(self):
        lines = self.me.render_stats()
        self.assertGreater(len(lines), 10)

    def test_document_tags(self):
        doc = self.me.selected_doc
        self.assertGreater(len(doc.tags), 0)


if __name__ == "__main__":
    unittest.main()
