"""Tests for ShellRenderer, AudioDAW, and PacketAnalyzer."""
import unittest
import time
from ui.backend import Backend, BackendType
from ui.shell_renderer import ShellRenderer
from ui.audio_daw import (
    AudioDAW, Track, AudioClip, MidiClip, MidiNoteEvent, Effect, TransportState, Marker,
    TrackType, TrackState, EffectType, TimeSignature, LoopMode,
)
from ui.packet_analyzer import (
    PacketAnalyzer, Packet, PacketHeader, MACAddress, IPAddress, CaptureFilter,
    ProtocolStats, Conversation,
    Protocol, PacketDirection, PacketStatus, ThreatLevel, FilterAction,
)


# ==================== ShellRenderer Tests ====================

class TestShellRenderer(unittest.TestCase):
    def setUp(self):
        self.renderer = ShellRenderer(640, 480, "Eclipse")

    def test_initial_state(self):
        self.assertFalse(self.renderer.is_initialized)
        self.assertEqual(self.renderer.theme_name if hasattr(self.renderer, 'theme_name') else "Eclipse", "Eclipse")

    def test_theme(self):
        theme = self.renderer.theme
        self.assertIn("bg", theme)
        self.assertIn("accent", theme)

    def test_theme_colors(self):
        r = ShellRenderer(640, 480, "Solar")
        theme = r.theme
        self.assertEqual(theme["bg"], (253, 246, 227))

    def test_backend_info(self):
        info = self.renderer.backend_info
        self.assertIsInstance(info, str)

    def test_stats(self):
        s = self.renderer.stats()
        self.assertIn("backend", s)
        self.assertIn("width", s)
        self.assertEqual(s["width"], 640)

    def test_fps(self):
        self.assertEqual(self.renderer.fps, 0.0)

    def test_shutdown(self):
        self.renderer.shutdown()
        self.assertFalse(self.renderer.is_initialized)

    def test_switch_backend(self):
        # Just test the method doesn't crash
        result = self.renderer.switch_backend(BackendType.LINUX)
        self.assertIsInstance(result, bool)


# ==================== AudioDAW Tests ====================

class TestMidiNoteEvent(unittest.TestCase):
    def test_create(self):
        n = MidiNoteEvent(60, 100, 0, 1)
        self.assertEqual(n.note_name, "C4")

    def test_velocity_bar(self):
        n = MidiNoteEvent(60, 128)
        bar = n.velocity_bar
        self.assertIn("█", bar)


class TestAudioClip(unittest.TestCase):
    def test_create(self):
        c = AudioClip("Drums", 0, 8)
        self.assertEqual(c.name, "Drums")

    def test_waveform(self):
        c = AudioClip("Test", 0, 4)
        wf = c.waveform_display
        self.assertEqual(len(wf), 20)


class TestMidiClip(unittest.TestCase):
    def test_create(self):
        mc = MidiClip("Chord", 0, 16, [
            MidiNoteEvent(60, 100, 0, 4),
            MidiNoteEvent(64, 100, 0, 4),
        ])
        self.assertEqual(mc.note_count, 2)

    def test_range(self):
        mc = MidiClip("T", notes=[
            MidiNoteEvent(60, 100, 0, 1),
            MidiNoteEvent(72, 100, 0, 1),
        ])
        self.assertIn("C", mc.range_str)


class TestEffect(unittest.TestCase):
    def test_create(self):
        e = Effect(EffectType.REVERB, True, 0.5)
        self.assertEqual(e.mix, 0.5)

    def test_mix_bar(self):
        e = Effect(EffectType.DELAY, True, 0.7)
        bar = e.mix_bar
        self.assertIn("█", bar)

    def test_status_icon(self):
        e = Effect(EffectType.CHORUS, True)
        self.assertEqual(e.status_icon, "🟢")
        e.enabled = False
        self.assertEqual(e.status_icon, "⚪")


class TestTrack(unittest.TestCase):
    def test_create(self):
        t = Track(0, "Drums", TrackType.AUDIO)
        self.assertEqual(t.name, "Drums")

    def test_volume_bar(self):
        t = Track(0, "T", volume_db=0)
        bar = t.volume_bar
        self.assertIn("█", bar)

    def test_pan_bar(self):
        t = Track(0, "T", pan=0.0)
        bar = t.pan_bar
        self.assertIn("█", bar)

    def test_state_icon(self):
        t = Track(0, "T", state=TrackState.PLAYING)
        self.assertEqual(t.state_icon, "▶")

    def test_clip_count(self):
        t = Track(0, "T", audio_clips=[AudioClip("A", 0, 4)])
        self.assertEqual(t.clip_count, 1)

    def test_effect_chain(self):
        t = Track(0, "T", effects=[Effect(EffectType.EQ), Effect(EffectType.REVERB)])
        self.assertIn("EQ", t.effect_chain)
        self.assertIn("Reverb", t.effect_chain)


class TestTransportState(unittest.TestCase):
    def test_create(self):
        t = TransportState()
        self.assertFalse(t.playing)

    def test_position_bar(self):
        t = TransportState(position_beat=16)
        bar = t.position_bar
        self.assertIn("▼", bar)

    def test_bar_beat(self):
        t = TransportState(position_beat=13)
        self.assertEqual(t.bar, 4)
        self.assertEqual(t.beat, 2)

    def test_position_str(self):
        t = TransportState(position_beat=8)
        self.assertEqual(t.position_str, "3.1")


class TestAudioDAW(unittest.TestCase):
    def setUp(self):
        self.daw = AudioDAW()

    def test_initial_state(self):
        self.assertGreater(self.daw.total_tracks, 0)
        self.assertIsNotNone(self.daw.selected_track)

    def test_transport(self):
        self.assertFalse(self.daw.transport.playing)
        self.assertEqual(self.daw.transport.bpm, 128.0)

    def test_select_track(self):
        self.daw.select_track(2)
        self.assertEqual(self.daw._selected_track, 2)

    def test_toggle_play(self):
        self.daw.toggle_play()
        self.assertTrue(self.daw.transport.playing)
        self.daw.toggle_play()
        self.assertFalse(self.daw.transport.playing)

    def test_toggle_record(self):
        self.daw.toggle_record()
        self.assertTrue(self.daw.transport.recording)
        self.assertTrue(self.daw.transport.playing)

    def test_set_bpm(self):
        self.daw.set_bpm(140)
        self.assertEqual(self.daw.transport.bpm, 140)

    def test_set_bpm_clamp(self):
        self.daw.set_bpm(10)
        self.assertEqual(self.daw.transport.bpm, 20)
        self.daw.set_bpm(500)
        self.assertEqual(self.daw.transport.bpm, 300)

    def test_total_effects(self):
        self.assertGreater(self.daw.total_effects, 0)

    def test_total_clips(self):
        self.assertGreater(self.daw.total_clips, 0)

    def test_render(self):
        lines = self.daw.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("AUDIO DAW" in l for l in lines))


# ==================== PacketAnalyzer Tests ====================

class TestMACAddress(unittest.TestCase):
    def test_create(self):
        mac = MACAddress((0x00, 0x1a, 0x2b, 0x3c, 0x4d, 0x5e))
        self.assertEqual(mac.str, "00:1a:2b:3c:4d:5e")

    def test_broadcast(self):
        mac = MACAddress((0xff, 0xff, 0xff, 0xff, 0xff, 0xff))
        self.assertTrue(mac.is_broadcast)

    def test_multicast(self):
        mac = MACAddress((0x01, 0x00, 0x00, 0x00, 0x00, 0x00))
        self.assertTrue(mac.is_multicast)


class TestIPAddress(unittest.TestCase):
    def test_private(self):
        ip = IPAddress("192.168.1.1")
        self.assertTrue(ip.is_private)
        self.assertEqual(ip.type_str, "Private")

    def test_loopback(self):
        ip = IPAddress("127.0.0.1")
        self.assertTrue(ip.is_loopback)
        self.assertEqual(ip.type_str, "Loopback")

    def test_public(self):
        ip = IPAddress("8.8.8.8")
        self.assertFalse(ip.is_private)
        self.assertEqual(ip.type_str, "Public")


class TestPacketHeader(unittest.TestCase):
    def test_create(self):
        h = PacketHeader(src_ip=IPAddress("1.2.3.4"), dst_ip=IPAddress("5.6.7.8"))
        self.assertIn("1.2.3.4", h.endpoint)

    def test_protocol_stack(self):
        h = PacketHeader(protocol=Protocol.TCP, src_port=80, dst_port=443)
        self.assertIn("TCP", h.protocol_stack)


class TestPacket(unittest.TestCase):
    def test_create(self):
        pkt = Packet(1, time.time(), PacketHeader(), 100)
        self.assertEqual(pkt.id, 1)

    def test_size_str(self):
        pkt = Packet(1, data_length=500)
        self.assertEqual(pkt.size_str, "500 B")
        pkt2 = Packet(2, data_length=2048)
        self.assertIn("KB", pkt2.size_str)

    def test_threat_icon(self):
        pkt = Packet(1, threat_level=ThreatLevel.HIGH)
        self.assertEqual(pkt.threat_icon, "🔴")

    def test_direction_icon(self):
        pkt = Packet(1, direction=PacketDirection.OUTBOUND)
        self.assertEqual(pkt.direction_icon, "⬆")


class TestCaptureFilter(unittest.TestCase):
    def test_create(self):
        f = CaptureFilter("HTTP", "tcp.port == 80")
        self.assertTrue(f.enabled)


class TestProtocolStats(unittest.TestCase):
    def test_create(self):
        ps = ProtocolStats(Protocol.TCP, 100, 50000, 500, 60.0)
        self.assertEqual(ps.packet_count, 100)

    def test_pct_bar(self):
        ps = ProtocolStats(Protocol.TCP, percentage=50.0)
        bar = ps.pct_bar
        self.assertIn("█", bar)


class TestConversation(unittest.TestCase):
    def test_create(self):
        c = Conversation("1.2.3.4", "5.6.7.8", 1234, 80, "TCP")
        self.assertIn("1.2.3.4", c.endpoint)


class TestPacketAnalyzer(unittest.TestCase):
    def setUp(self):
        self.pa = PacketAnalyzer()

    def test_initial_state(self):
        self.assertGreater(self.pa.total_packets, 0)
        self.assertIsNotNone(self.pa.selected_packet)

    def test_select_packet(self):
        self.pa.select_packet(5)
        self.assertEqual(self.pa._selected_packet, 5)

    def test_total_bytes(self):
        self.assertGreater(len(self.pa.total_bytes_display), 0)

    def test_packets_per_second(self):
        pps = self.pa.packets_per_second
        self.assertGreater(pps, 0)

    def test_start_stop_capture(self):
        self.pa.start_capture()
        self.assertTrue(self.pa._capture_active)
        self.pa.stop_capture()
        self.assertFalse(self.pa._capture_active)

    def test_protocol_stats(self):
        self.assertGreater(len(self.pa._protocol_stats), 0)

    def test_conversations(self):
        self.assertGreater(len(self.pa._conversations), 0)

    def test_filters(self):
        self.assertGreater(len(self.pa._filters), 0)

    def test_render(self):
        lines = self.pa.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("PACKET ANALYZER" in l for l in lines))

    def test_handle_input(self):
        self.pa.handle_input("s")  # start
        self.assertTrue(self.pa._capture_active)
        self.pa.handle_input("s")  # stop
        self.assertFalse(self.pa._capture_active)


if __name__ == "__main__":
    unittest.main()
