"""Tests for ChessEngine, DrumPadSampler, LiveProfiler"""
import time
import unittest

from ui.chess_engine import (
    ChessEngine, ChessPiece, ChessMove, Player, MoveAnalysis,
    PieceType, PieceColor, GameStatus, AILevel, MoveType
)
from ui.drum_pad import (
    DrumPadSampler, DrumPadSound, SamplePad, PadSound,
    PadLayout, SampleRate
)
from ui.live_profiler import (
    LiveProfiler, HardwareMetric, LiveDataPoint, ProfilerAlert,
    ProfilerView, AlertSeverity, GraphStyle
)


class TestChessEngine(unittest.TestCase):
    def setUp(self):
        self.ce = ChessEngine()

    def test_initial_state(self):
        self.assertEqual(self.ce._current_player, PieceColor.WHITE)
        self.assertEqual(self.ce._status, GameStatus.ACTIVE)

    def test_board_setup(self):
        # Check corners
        self.assertIsNotNone(self.ce.get_piece(0, 0))
        self.assertIsNotNone(self.ce.get_piece(7, 7))
        self.assertIsNone(self.ce.get_piece(3, 3))

    def test_piece_placement(self):
        self.assertEqual(self.ce.get_piece(0, 0).piece_type, PieceType.ROOK)
        self.assertEqual(self.ce.get_piece(0, 4).piece_type, PieceType.KING)
        self.assertEqual(self.ce.get_piece(6, 0).piece_type, PieceType.PAWN)

    def test_get_legal_moves_pawn(self):
        moves = self.ce.get_legal_moves(6, 0)  # White pawn
        self.assertIn((5, 0), moves)
        self.assertIn((4, 0), moves)

    def test_get_legal_moves_knight(self):
        moves = self.ce.get_legal_moves(7, 1)  # White knight
        self.assertIn((5, 0), moves)
        self.assertIn((5, 2), moves)

    def test_get_legal_moves_bishop(self):
        moves = self.ce.get_legal_moves(7, 2)  # White bishop
        # Bishop has no diagonal moves due to pawns blocking
        self.assertIsInstance(moves, list)

    def test_make_move(self):
        result = self.ce.make_move(6, 0, 4, 0)  # e2-e4
        self.assertIn("pawn", result)
        self.assertEqual(self.ce._current_player, PieceColor.BLACK)

    def test_make_move_wrong_color(self):
        result = self.ce.make_move(1, 0, 3, 0)  # Black pawn on white's turn
        self.assertIn("Not your piece", result)

    def test_make_move_illegal(self):
        result = self.ce.make_move(6, 0, 3, 0)  # Can't jump over
        self.assertIn("Illegal", result)

    def test_ai_move(self):
        self.ce.make_move(6, 4, 4, 4)  # e4
        result = self.ce.ai_move()
        self.assertIn("Moved", result)

    def test_piece_symbol(self):
        wp = ChessPiece(PieceType.KING, PieceColor.WHITE)
        self.assertEqual(wp.symbol, "♔")
        bp = ChessPiece(PieceType.KING, PieceColor.BLACK)
        self.assertEqual(bp.symbol, "♚")

    def test_move_number(self):
        self.ce.make_move(6, 4, 4, 4)
        self.ce.ai_move()
        self.assertEqual(self.ce._move_number, 2)

    def test_captured_pieces(self):
        # Set up a capture scenario
        self.ce.make_move(6, 4, 4, 4)  # e4
        self.ce.ai_move()
        self.ce.make_move(7, 5, 4, 2)  # Bc4 (bishop out)
        self.ce.ai_move()
        # Nothing captured yet
        self.assertEqual(len(self.ce._captured_black), 0)

    def test_render(self):
        lines = self.ce.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("CHESS" in l for l in lines))

    def test_render_analysis(self):
        lines = self.ce.render_analysis()
        self.assertGreater(len(lines), 3)


class TestDrumPadSampler(unittest.TestCase):
    def setUp(self):
        self.dp = DrumPadSampler()

    def test_initial_state(self):
        self.assertEqual(self.dp.total_pads, 16)
        self.assertEqual(self.dp._selected_pad, 0)

    def test_select_pad(self):
        self.dp.select_pad(5)
        self.assertEqual(self.dp._selected_pad, 5)

    def test_select_invalid(self):
        self.dp.select_pad(99)
        self.assertEqual(self.dp._selected_pad, 0)

    def test_hit_pad(self):
        self.dp.hit_pad(0, 100)
        self.assertEqual(self.dp._pads[0].velocity, 100)

    def test_toggle_mute(self):
        self.dp.toggle_mute(0)
        self.assertTrue(self.dp._pads[0].is_muted)
        self.dp.toggle_mute(0)
        self.assertFalse(self.dp._pads[0].is_muted)

    def test_active_pads(self):
        self.dp.hit_pad(0, 100)
        self.dp.hit_pad(1, 80)
        self.assertEqual(self.dp.active_pads, 2)

    def test_pad_velocity_bar(self):
        pad = self.dp._pads[0]
        pad.velocity = 80
        self.assertIn("█", pad.velocity_bar)

    def test_pad_volume_bar(self):
        pad = self.dp._pads[0]
        self.assertIn("█", pad.volume_bar)

    def test_pad_icon(self):
        pad = self.dp._pads[0]
        self.assertIn(pad.icon, ["🥁", "🪘", "🔔", "👏", "🔫", "💥", "🛎️", "🫙", "🎸", "🎤", "🎹", "✨", "🎵", "🎻", "🪵"])

    def test_pad_age(self):
        pad = self.dp._pads[0]
        pad.last_hit_time = time.time() - 1
        self.assertGreater(pad.age_ms, 0)

    def test_samples(self):
        self.assertGreater(len(self.dp._samples), 0)

    def test_sample_waveform(self):
        s = self.dp._samples[0]
        self.assertGreater(len(s.waveform_str), 0)

    def test_sample_duration(self):
        s = self.dp._samples[0]
        self.assertGreater(len(s.duration_display), 0)

    def test_render(self):
        lines = self.dp.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DRUM PAD" in l for l in lines))


class TestLiveProfiler(unittest.TestCase):
    def setUp(self):
        self.lp = LiveProfiler()

    def test_initial_state(self):
        self.assertGreater(len(self.lp._metrics), 0)
        self.assertGreater(len(self.lp._alerts), 0)

    def test_uptime(self):
        self.assertIn("d", self.lp.uptime_display)

    def test_unacked_alerts(self):
        self.assertGreater(self.lp.unacked_alerts, 0)

    def test_acknowledge_alert(self):
        self.lp.acknowledge_alert(0)
        self.assertTrue(self.lp._alerts[0].acknowledged)

    def test_metric_current_display(self):
        m = self.lp._metrics["cpu_usage"]
        self.assertIn("%", m.current_display)

    def test_metric_bar(self):
        m = self.lp._metrics["cpu_usage"]
        self.assertIn("█", m.bar)

    def test_metric_sparkline(self):
        m = self.lp._metrics["cpu_usage"]
        self.assertEqual(len(m.sparkline), 32)

    def test_metric_status(self):
        m = self.lp._metrics["cpu_usage"]
        self.assertIn(m.status, ["🟢", "🟡", "🔴"])

    def test_metric_thresholds(self):
        m = self.lp._metrics["cpu_usage"]
        self.assertGreater(m.threshold_warn, 0)

    def test_render(self):
        lines = self.lp.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PROFILER" in l for l in lines))

    def test_render_metric_detail(self):
        lines = self.lp.render_metric_detail("cpu_usage")
        self.assertGreater(len(lines), 3)

    def test_render_alerts(self):
        lines = self.lp.render_alerts()
        self.assertGreater(len(lines), 3)

    def test_graph_styles(self):
        self.assertEqual(len(GraphStyle), 4)

    def test_profiler_views(self):
        self.assertEqual(len(ProfilerView), 8)

    def test_alert_severity(self):
        a = self.lp._alerts[0]
        self.assertIn(a.icon, ["ℹ️", "⚠️", "🚨"])


if __name__ == "__main__":
    unittest.main()
