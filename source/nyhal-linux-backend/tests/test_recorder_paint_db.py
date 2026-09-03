"""
Tests for Screen Recorder, Paint App, and Database Viewer.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.screen_recorder import (
    ScreenRecorder, Recording, RecordingRegion,
    RecordingFormat, QualityPreset, RecordingStatus, AnnotationType
)
from ui.paint_app import (
    PaintApp, CanvasState, Layer, DrawStroke, DrawPoint,
    BrushType, ShapeType, ExportFormat
)
from ui.database_viewer import (
    DatabaseViewer, Table, Column, Index, QueryResult,
    QueryHistoryEntry, ViewMode
)


# ─── Screen Recorder Tests ───────────────────────────────────────────────


class TestScreenRecorder(unittest.TestCase):

    def setUp(self):
        self.sr = ScreenRecorder()

    def test_initial_state(self):
        self.assertEqual(self.sr.status, RecordingStatus.IDLE)
        self.assertFalse(self.sr.is_recording)

    def test_start_recording(self):
        self.sr.start_recording()
        self.assertTrue(self.sr.is_recording)
        self.assertEqual(self.sr.status, RecordingStatus.RECORDING)

    def test_pause_recording(self):
        self.sr.start_recording()
        self.sr.pause_recording()
        self.assertTrue(self.sr.is_paused)

    def test_resume_recording(self):
        self.sr.start_recording()
        self.sr.pause_recording()
        self.sr.resume_recording()
        self.assertTrue(self.sr.is_recording)

    def test_stop_recording(self):
        self.sr.start_recording()
        time.sleep(0.01)
        rec = self.sr.stop_recording()
        self.assertIsNotNone(rec)
        self.assertEqual(self.sr.status, RecordingStatus.IDLE)

    def test_elapsed_str(self):
        self.sr.start_recording()
        elapsed = self.sr.elapsed_str
        self.assertIsInstance(elapsed, str)

    def test_set_format(self):
        self.sr.set_format(RecordingFormat.MP4)
        self.assertEqual(self.sr.format, RecordingFormat.MP4)

    def test_set_quality(self):
        self.sr.set_quality(QualityPreset.ULTRA)
        self.assertEqual(self.sr.quality, QualityPreset.ULTRA)

    def test_set_frame_rate(self):
        self.sr.set_frame_rate(60)
        self.assertEqual(self.sr.frame_rate, 60)

    def test_toggle_audio(self):
        result = self.sr.toggle_system_audio()
        self.assertFalse(result)

    def test_toggle_microphone(self):
        result = self.sr.toggle_microphone()
        self.assertTrue(result)

    def test_annotations(self):
        ann = self.sr.add_annotation(AnnotationType.ARROW, x=10, y=10, x2=50, y2=50)
        self.assertEqual(len(self.sr.annotations), 1)
        self.sr.clear_annotations()
        self.assertEqual(len(self.sr.annotations), 0)

    def test_recordings_history(self):
        self.assertGreater(self.sr.total_recordings, 0)

    def test_total_duration(self):
        self.assertGreater(self.sr.total_duration, 0)

    def test_total_size(self):
        self.assertGreater(self.sr.total_size, 0)

    def test_delete_recording(self):
        rec = self.sr.recordings[0]
        result = self.sr.delete_recording(rec.recording_id)
        self.assertTrue(result)

    def test_render_controls(self):
        lines = self.sr.render_controls()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_history(self):
        lines = self.sr.render_history()
        self.assertIsInstance(lines, list)

    def test_render_settings(self):
        lines = self.sr.render_settings()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.sr.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_start(self):
        self.sr.handle_key(" ")
        self.assertTrue(self.sr.is_recording)

    def test_handle_key_pause(self):
        self.sr.start_recording()
        self.sr.handle_key(" ")
        self.assertTrue(self.sr.is_paused)

    def test_handle_key_stop(self):
        self.sr.start_recording()
        self.sr.handle_key("s")
        self.sr.handle_key("f")  # cycle format
        self.sr.handle_key("h")  # history


class TestRecording(unittest.TestCase):

    def test_duration_str(self):
        r = Recording(filename="t", duration=125.5)
        self.assertEqual(r.duration_str, "2:05")

    def test_size_str(self):
        r = Recording(filename="t", file_size=5 * 1024 * 1024)
        self.assertEqual(r.size_str, "5.0 MB")

    def test_fps_str(self):
        r = Recording(filename="t", frame_rate=30)
        self.assertEqual(r.fps_str, "30 fps")


class TestRecordingRegion(unittest.TestCase):

    def test_resolution(self):
        r = RecordingRegion(width=1920, height=1080)
        self.assertEqual(r.resolution_str, "1920x1080")

    def test_area(self):
        r = RecordingRegion(width=100, height=100)
        self.assertEqual(r.area, 10000)


class TestAnnotation(unittest.TestCase):

    def test_icon(self):
        from ui.screen_recorder import Annotation
        a = Annotation(ann_type=AnnotationType.ARROW)
        self.assertEqual(a.icon, "➡️")


# ─── Paint App Tests ─────────────────────────────────────────────────────


class TestPaintApp(unittest.TestCase):

    def setUp(self):
        self.pa = PaintApp()

    def test_initial_state(self):
        self.assertEqual(self.pa.view_mode, "canvas")
        self.assertGreater(self.pa.layer_count, 0)

    def test_start_stroke(self):
        stroke = self.pa.start_stroke(10, 10)
        self.assertIsNotNone(stroke)
        self.assertEqual(len(stroke.points), 1)

    def test_continue_stroke(self):
        self.pa.start_stroke(10, 10)
        self.pa.continue_stroke(20, 20)
        layer = self.pa.active_layer
        self.assertEqual(len(layer.strokes[-1].points), 2)

    def test_end_stroke(self):
        self.pa.start_stroke(10, 10)
        self.pa.end_stroke()

    def test_add_shape(self):
        stroke = self.pa.add_shape(ShapeType.RECTANGLE, 10, 10, 100, 100)
        self.assertIsNotNone(stroke)

    def test_undo(self):
        self.pa.start_stroke(10, 10)
        self.pa.end_stroke()
        result = self.pa.undo()
        self.assertTrue(result)

    def test_redo(self):
        self.pa.start_stroke(10, 10)
        self.pa.end_stroke()
        self.pa.undo()
        result = self.pa.redo()
        self.assertTrue(result)

    def test_add_layer(self):
        initial = self.pa.layer_count
        self.pa.add_layer("New Layer")
        self.assertEqual(self.pa.layer_count, initial + 1)

    def test_remove_layer(self):
        initial = self.pa.layer_count
        self.pa.remove_layer(0)
        self.assertEqual(self.pa.layer_count, initial - 1)

    def test_cannot_remove_last(self):
        while self.pa.layer_count > 1:
            self.pa.remove_layer(0)
        result = self.pa.remove_layer(0)
        self.assertFalse(result)

    def test_toggle_layer_visibility(self):
        result = self.pa.toggle_layer_visibility(0)
        self.assertFalse(result)  # Was visible, now hidden

    def test_set_brush_type(self):
        self.pa.set_brush_type(BrushType.ERASER)
        self.assertEqual(self.pa.brush_type, BrushType.ERASER)

    def test_set_color(self):
        self.pa.set_color("#FF0000")
        self.assertEqual(self.pa.brush_color, "#FF0000")

    def test_zoom(self):
        self.pa.zoom_in()
        self.assertGreater(self.pa.canvas.zoom, 1.0)
        self.pa.zoom_out()
        self.pa.zoom_reset()
        self.assertEqual(self.pa.canvas.zoom, 1.0)

    def test_toggle_grid(self):
        result = self.pa.toggle_grid()
        self.assertTrue(result)

    def test_clear_canvas(self):
        self.pa.start_stroke(10, 10)
        self.pa.end_stroke()
        self.pa.clear_canvas()
        layer = self.pa.active_layer
        self.assertEqual(len(layer.strokes), 0)

    def test_render_canvas(self):
        lines = self.pa.render_canvas()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_layers(self):
        lines = self.pa.render_layers()
        self.assertIsInstance(lines, list)

    def test_render_color(self):
        lines = self.pa.render_color()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.pa.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_brush(self):
        self.pa.handle_key("b")
        # Brush type should have changed

    def test_handle_key_layers(self):
        self.pa.handle_key("l")
        self.assertEqual(self.pa.view_mode, "layers")

    def test_handle_key_color(self):
        self.pa.handle_key("c")
        self.assertEqual(self.pa.view_mode, "color")


class TestLayer(unittest.TestCase):

    def test_layer_id(self):
        l = Layer(name="Test")
        self.assertIsNotNone(l.layer_id)
        self.assertEqual(len(l.layer_id), 6)

    def test_stroke_count(self):
        l = Layer(name="Test")
        l.strokes.append(DrawStroke())
        self.assertEqual(l.stroke_count, 1)


class TestDrawStroke(unittest.TestCase):

    def test_bounding_box(self):
        s = DrawStroke(points=[DrawPoint(0, 0), DrawPoint(10, 10)])
        box = s.bounding_box
        self.assertEqual(box, (0, 0, 10, 10))

    def test_length(self):
        s = DrawStroke(points=[DrawPoint(0, 0), DrawPoint(1, 1), DrawPoint(2, 2)])
        self.assertEqual(s.length, 3)


class TestCanvasState(unittest.TestCase):

    def test_zoom_str(self):
        c = CanvasState(zoom=1.5)
        self.assertEqual(c.zoom_str, "150%")


# ─── Database Viewer Tests ───────────────────────────────────────────────


class TestDatabaseViewer(unittest.TestCase):

    def setUp(self):
        self.db = DatabaseViewer()

    def test_initial_state(self):
        self.assertEqual(self.db.view_mode, ViewMode.BROWSER)
        self.assertGreater(self.db.total_tables, 0)

    def test_tables(self):
        tables = self.db.tables
        self.assertGreater(len(tables), 0)

    def test_select_table(self):
        result = self.db.select_table(0)
        self.assertTrue(result)
        self.assertIsNotNone(self.db.current_table)

    def test_execute_query(self):
        result = self.db.execute_query("SELECT * FROM users LIMIT 10")
        self.assertFalse(result.is_error)
        self.assertEqual(result.row_count, 10)

    def test_execute_count(self):
        result = self.db.execute_query("SELECT COUNT(*) FROM users")
        self.assertFalse(result.is_error)

    def test_execute_error(self):
        result = self.db.execute_query("DROP TABLE nonexistent")
        self.assertTrue(result.is_error)

    def test_query_history(self):
        self.assertGreater(len(self.db.query_history), 0)

    def test_total_rows(self):
        self.assertGreater(self.db.total_rows, 0)

    def test_set_view(self):
        self.db.set_view(ViewMode.SCHEMA)
        self.assertEqual(self.db.view_mode, ViewMode.SCHEMA)

    def test_cycle_view(self):
        self.db.cycle_view()
        self.assertEqual(self.db.view_mode, ViewMode.SCHEMA)
        self.db.cycle_view()
        self.assertEqual(self.db.view_mode, ViewMode.QUERY)

    def test_selection(self):
        self.db.select_up()
        self.db.select_down()

    def test_render_browser(self):
        lines = self.db.render_browser()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_schema(self):
        lines = self.db.render_schema()
        self.assertIsInstance(lines, list)

    def test_render_query(self):
        lines = self.db.render_query()
        self.assertIsInstance(lines, list)

    def test_render_history(self):
        lines = self.db.render_history()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.db.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_browser(self):
        self.db.handle_key("ArrowDown")
        self.db.handle_key("ArrowUp")
        self.db.handle_key("s")
        self.assertEqual(self.db.view_mode, ViewMode.SCHEMA)

    def test_handle_key_query(self):
        self.db._view_mode = ViewMode.QUERY
        self.db.handle_key("Escape")
        self.assertEqual(self.db.view_mode, ViewMode.BROWSER)


class TestTable(unittest.TestCase):

    def test_size_str(self):
        t = Table(name="t", estimated_size=5 * 1024 * 1024)
        self.assertEqual(t.size_str, "5.0 MB")

    def test_column_names(self):
        cols = [Column("id"), Column("name")]
        t = Table(name="t", columns=cols)
        self.assertEqual(t.column_names, ["id", "name"])


class TestColumn(unittest.TestCase):

    def test_definition(self):
        c = Column("id", "INTEGER", False, True)
        self.assertIn("PRIMARY KEY", c.definition)


class TestQueryResult(unittest.TestCase):

    def test_time_str(self):
        r = QueryResult(execution_time_ms=500)
        self.assertIn("ms", r.time_str)

    def test_to_csv(self):
        r = QueryResult(columns=["a", "b"], rows=[{"a": "1", "b": "2"}])
        csv = r.to_csv()
        self.assertIn("a", csv)
        self.assertIn("1", csv)


class TestQueryHistoryEntry(unittest.TestCase):

    def test_preview(self):
        e = QueryHistoryEntry(query="SELECT * FROM users LIMIT 10")
        self.assertIn("SELECT", e.preview)

    def test_time_ago(self):
        e = QueryHistoryEntry(query="test", timestamp=time.time() - 3600)
        self.assertIn("h ago", e.time_ago)


if __name__ == "__main__":
    unittest.main()
