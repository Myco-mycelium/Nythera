"""Tests for AdventureEngine, SystemReport, VirtualGuitar"""
import time
import unittest

from ui.adventure_engine import (
    AdventureEngine, Room, Item, NPC, PlayerState, GameLog,
    ItemType, NPCDisposition, GameEvent, Direction
)
from ui.system_report import (
    SystemReport, HardwareInfo, BenchmarkData, ReportTemplate,
    ReportFormat, ReportSection
)
from ui.virtual_guitar import (
    VirtualGuitar, Chord, StrummingPattern, GuitarEffectInstance, Recording,
    Tuning, GuitarType, GuitarEffect
)


class TestAdventureEngine(unittest.TestCase):
    def setUp(self):
        self.ae = AdventureEngine()
        self.ae.start_game()

    def test_initial_state(self):
        self.assertTrue(self.ae._is_running)
        self.assertEqual(self.ae._player.current_room, "entrance")
        self.assertEqual(self.ae._player.health, 100)

    def test_move(self):
        result = self.ae.process_command("go north")
        self.assertIn("Library", result)

    def test_move_invalid(self):
        result = self.ae.process_command("go south")
        self.assertIn("can't go", result)

    def test_look(self):
        result = self.ae.process_command("look")
        self.assertIn("grand hall", result)

    def test_look_at_item(self):
        result = self.ae.process_command("examine torch")
        self.assertIn("Torch", result)

    def test_take_item(self):
        result = self.ae.process_command("take torch")
        self.assertIn("take", result.lower())
        self.assertEqual(len(self.ae._player.inventory), 1)

    def test_take_nonexistent(self):
        result = self.ae.process_command("take dragon")
        self.assertIn("don't see", result)

    def test_use_item(self):
        self.ae.process_command("take torch")
        result = self.ae.process_command("use torch")
        self.assertIn("torch", result.lower())

    def test_talk_npc(self):
        self.ae.process_command("go north")
        result = self.ae.process_command("talk librarian")
        self.assertIn("Librarian Ghost", result)

    def test_attack_npc(self):
        self.ae.process_command("go east")
        result = self.ae.process_command("attack armorer")
        self.assertIn("attack", result.lower())

    def test_inventory(self):
        result = self.ae.process_command("inventory")
        self.assertIn("empty", result)

    def test_inventory_with_items(self):
        self.ae.process_command("take torch")
        result = self.ae.process_command("inventory")
        self.assertIn("Torch", result)

    def test_status(self):
        result = self.ae.process_command("status")
        self.assertIn("Health", result)

    def test_help(self):
        result = self.ae.process_command("help")
        self.assertIn("Commands", result)

    def test_quit(self):
        result = self.ae.process_command("quit")
        self.assertFalse(self.ae._is_running)

    def test_unknown_command(self):
        result = self.ae.process_command("dance")
        self.assertIn("don't understand", result)

    def test_turn_count(self):
        self.ae.process_command("look")
        self.ae.process_command("go north")
        self.assertEqual(self.ae.turn_count, 2)

    def test_game_time(self):
        self.assertGreater(len(self.ae.game_time), 0)

    def test_room_items(self):
        room = self.ae._rooms["entrance"]
        self.assertGreater(len(room.items), 0)

    def test_room_npcs(self):
        room = self.ae._rooms["library"]
        self.assertGreater(len(room.npcs), 0)

    def test_player_attack_power(self):
        self.assertGreater(self.ae._player.attack_power, 0)

    def test_render(self):
        lines = self.ae.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("ADVENTURE" in l for l in lines))


class TestSystemReport(unittest.TestCase):
    def setUp(self):
        self.sr = SystemReport()

    def test_initial_state(self):
        self.assertGreater(len(self.sr._hardware), 0)
        self.assertGreater(len(self.sr._templates), 0)

    def test_select_template(self):
        self.sr.select_template(1)
        self.assertEqual(self.sr._selected_template, 1)

    def test_select_invalid(self):
        self.sr.select_template(99)
        self.assertEqual(self.sr._selected_template, 0)

    def test_total_sections(self):
        self.assertEqual(self.sr.total_sections, len(ReportSection))

    def test_generate_html(self):
        report = self.sr.generate_report(0)
        self.assertIn("<!DOCTYPE html>", report)

    def test_generate_markdown(self):
        report = self.sr.generate_report(1)
        self.assertIn("#", report)

    def test_generate_json(self):
        report = self.sr.generate_report(5)
        self.assertIn("hostname", report)

    def test_generate_csv(self):
        report = self.sr.generate_report(3)
        self.assertIn("Category", report)

    def test_hardware_info(self):
        h = self.sr._hardware[0]
        self.assertIn("AMD", h.summary)

    def test_benchmark_data(self):
        b = self.sr._benchmarks[0]
        self.assertIn("pts", b.unit)

    def test_render(self):
        lines = self.sr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("REPORT" in l for l in lines))

    def test_render_preview(self):
        lines = self.sr.render_preview()
        self.assertGreater(len(lines), 3)

    def test_render_sections(self):
        lines = self.sr.render_sections()
        self.assertGreater(len(lines), 5)

    def test_generated_reports(self):
        self.sr.generate_report()
        self.assertGreater(len(self.sr._generated_reports), 0)


class TestVirtualGuitar(unittest.TestCase):
    def setUp(self):
        self.vg = VirtualGuitar()

    def test_initial_state(self):
        self.assertGreater(len(self.vg._chords), 0)
        self.assertEqual(self.vg._tuning, Tuning.STANDARD)

    def test_select_chord(self):
        self.vg.select_chord(1)
        self.assertEqual(self.vg._selected_chord, 1)
        self.assertIsNotNone(self.vg.selected_chord)

    def test_select_invalid(self):
        self.vg.select_chord(99)
        self.assertEqual(self.vg._selected_chord, 0)

    def test_total_chords(self):
        self.assertGreater(self.vg.total_chords, 0)

    def test_select_pattern(self):
        self.vg.select_pattern(1)
        self.assertEqual(self.vg._selected_pattern, 1)

    def test_strum(self):
        self.vg.select_chord(0)
        self.vg.strum(self.vg._patterns[0])
        self.assertIsNotNone(self.vg._active_chord)

    def test_chord_diagram(self):
        c = self.vg._chords[0]
        self.assertIn("x", c.diagram)

    def test_chord_difficulty(self):
        c = self.vg._chords[0]
        self.assertIn("★", c.difficulty_stars)

    def test_pattern_beats(self):
        p = self.vg._patterns[0]
        self.assertGreater(len(p.beats), 0)

    def test_effect_mix_bar(self):
        eff = self.vg._effects[0]
        self.assertIn("█", eff.mix_bar)

    def test_recording(self):
        self.assertGreater(len(self.vg._recordings), 0)

    def test_recording_duration(self):
        r = self.vg._recordings[0]
        self.assertGreater(len(r.display_duration), 0)

    def test_render(self):
        lines = self.vg.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("GUITAR" in l for l in lines))

    def test_render_chord_detail(self):
        self.vg.select_chord(0)
        lines = self.vg.render_chord_detail()
        self.assertGreater(len(lines), 3)

    def test_render_tuning(self):
        lines = self.vg.render_tuning()
        self.assertGreater(len(lines), 5)

    def test_tuning_types(self):
        self.assertGreater(len(Tuning), 5)


if __name__ == "__main__":
    unittest.main()
