"""Tests for PianoRoll, SystemDashboard, AdventureBuilder"""
import time
import unittest

from ui.piano_roll import (
    PianoRoll, MidiNote, MidiTrack, MidiClip, TimeSignature,
    NoteName, Quantize, ToolMode
)
from ui.system_dashboard import (
    SystemDashboard, MetricSnapshot, DashboardWidgetConfig, ProcessInfo,
    DashboardWidget, ExportFormat
)
from ui.adventure_builder import (
    AdventureBuilder, GameProject, RoomDef, ItemDef, NPCDef, EventDef,
    BuilderMode, RoomType, ItemType, NPCRole
)


class TestPianoRoll(unittest.TestCase):
    def setUp(self):
        self.pr = PianoRoll()

    def test_initial_state(self):
        self.assertGreater(len(self.pr._tracks), 0)
        self.assertEqual(self.pr._selected_track, 0)

    def test_select_track(self):
        self.pr.select_track(1)
        self.assertEqual(self.pr._selected_track, 1)

    def test_select_invalid(self):
        self.pr.select_track(99)
        self.assertEqual(self.pr._selected_track, 0)

    def test_total_notes(self):
        self.assertGreater(self.pr.total_notes, 0)

    def test_add_note(self):
        before = self.pr.total_notes
        self.pr.add_note(0, NoteName.C, 4, 16, 2)
        self.assertEqual(self.pr.total_notes, before + 1)

    def test_track_note_count(self):
        track = self.pr.selected_track
        self.assertGreater(track.note_count, 0)

    def test_track_volume_bar(self):
        track = self.pr._tracks[0]
        self.assertIn("█", track.volume_bar)

    def test_midi_note_display(self):
        note = MidiNote(NoteName.C, 4, 0, 2)
        self.assertEqual(note.display_name, "C4")

    def test_midi_note_end_beat(self):
        note = MidiNote(NoteName.C, 4, 4, 2)
        self.assertEqual(note.end_beat, 6)

    def test_time_signature(self):
        ts = TimeSignature(4, 4)
        self.assertEqual(ts.display, "4/4")

    def test_clips(self):
        self.assertGreater(len(self.pr._clips), 0)

    def test_selected_notes_count(self):
        self.assertEqual(self.pr.selected_notes_count, 0)

    def test_render(self):
        lines = self.pr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PIANO ROLL" in l for l in lines))

    def test_render_clip_detail(self):
        lines = self.pr.render_clip_detail()
        self.assertGreater(len(lines), 2)


class TestSystemDashboard(unittest.TestCase):
    def setUp(self):
        self.sd = SystemDashboard()

    def test_initial_state(self):
        self.assertGreater(len(self.sd._widgets), 0)
        self.assertGreater(len(self.sd._metrics), 0)

    def test_select_widget(self):
        self.sd.select_widget(1)
        self.assertEqual(self.sd._selected_widget, 1)

    def test_select_invalid(self):
        self.sd.select_widget(99)
        self.assertEqual(self.sd._selected_widget, 0)

    def test_enabled_widgets(self):
        self.assertGreater(self.sd.enabled_widgets, 0)

    def test_toggle_widget(self):
        self.sd.toggle_widget(0)
        self.assertFalse(self.sd._widgets[0].enabled)

    def test_export_dashboard(self):
        name = self.sd.export_dashboard(ExportFormat.HTML)
        self.assertIn("html", name)

    def test_metric_display(self):
        m = self.sd._metrics["cpu_usage"]
        self.assertIn("%", m.display)

    def test_metric_bar(self):
        m = self.sd._metrics["cpu_usage"]
        self.assertIn("█", m.bar)

    def test_metric_sparkline(self):
        m = self.sd._metrics["cpu_usage"]
        self.assertEqual(len(m.sparkline), 16)

    def test_processes(self):
        self.assertGreater(len(self.sd._processes), 0)

    def test_process_info(self):
        p = self.sd._processes[0]
        self.assertEqual(p.name, "nyrqis-compositor")

    def test_render(self):
        lines = self.sd.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DASHBOARD" in l for l in lines))

    def test_render_widget_detail(self):
        self.sd.select_widget(0)
        lines = self.sd.render_widget_detail()
        self.assertGreater(len(lines), 3)

    def test_generated_exports(self):
        self.assertGreater(len(self.sd._generated_exports), 0)


class TestAdventureBuilder(unittest.TestCase):
    def setUp(self):
        self.ab = AdventureBuilder()

    def test_initial_state(self):
        self.assertIsNotNone(self.ab._project)

    def test_project_name(self):
        self.assertEqual(self.ab._project.name, "Dragon's Keep")

    def test_rooms(self):
        self.assertGreater(len(self.ab._project.rooms), 0)

    def test_items(self):
        self.assertGreater(len(self.ab._project.items), 0)

    def test_npcs(self):
        self.assertGreater(len(self.ab._project.npcs), 0)

    def test_events(self):
        self.assertGreater(len(self.ab._project.events), 0)

    def test_total_entities(self):
        self.assertGreater(self.ab._project.total_entities, 0)

    def test_select_room(self):
        self.ab.select_room(1)
        self.assertEqual(self.ab._selected_room, 1)

    def test_select_item(self):
        self.ab.select_item(1)
        self.assertEqual(self.ab._selected_item, 1)

    def test_select_npc(self):
        self.ab.select_npc(1)
        self.assertEqual(self.ab._selected_npc, 1)

    def test_add_room(self):
        before = len(self.ab._project.rooms)
        self.ab.add_room(RoomDef("test", "Test Room", "A test room."))
        self.assertEqual(len(self.ab._project.rooms), before + 1)

    def test_add_item(self):
        before = len(self.ab._project.items)
        self.ab.add_item(ItemDef("test", "Test Item", "A test item.", ItemType.TOOL))
        self.assertEqual(len(self.ab._project.items), before + 1)

    def test_add_npc(self):
        before = len(self.ab._project.npcs)
        self.ab.add_npc(NPCDef("test", "Test NPC", "A test NPC.", NPCRole.NEUTRAL))
        self.assertEqual(len(self.ab._project.npcs), before + 1)

    def test_room_exits(self):
        r = self.ab._project.rooms[0]
        self.assertGreater(r.exit_count, 0)

    def test_room_is_start(self):
        r = self.ab._project.rooms[0]
        self.assertTrue(r.is_start)

    def test_item_damage(self):
        item = self.ab._project.items[1]
        self.assertEqual(item.damage, 25)

    def test_npc_health(self):
        npc = self.ab._project.npcs[0]
        self.assertGreater(npc.health, 0)

    def test_npc_dialogue(self):
        npc = self.ab._project.npcs[1]
        self.assertGreater(len(npc.dialogue), 0)

    def test_event_trigger(self):
        e = self.ab._project.events[0]
        self.assertEqual(e.trigger, "npc_defeated")

    def test_render(self):
        lines = self.ab.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("ADVENTURE BUILDER" in l for l in lines))

    def test_render_room_detail(self):
        self.ab.select_room(0)
        lines = self.ab.render_room_detail()
        self.assertGreater(len(lines), 3)

    def test_render_npc_detail(self):
        self.ab.select_npc(0)
        lines = self.ab.render_npc_detail()
        self.assertGreater(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
