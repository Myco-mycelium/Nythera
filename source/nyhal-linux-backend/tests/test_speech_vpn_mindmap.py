"""
Tests for Speech Tools, VPN Manager, and Mind Map.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.speech_tools import (
    SpeechTools, Voice, SpeechEntry, PronunciationEntry,
    VoiceType, SpeechLanguage, SpeechStatus
)
from ui.vpn_manager import (
    VPNManager, VPNProfile, VPNServer, VPNStats, ConnectionLog,
    VPNProtocol, VPNStatus, VPNRegion
)
from ui.mindmap import (
    MindMap, MindNode, MindConnection,
    NodeType, LayoutType
)


# ─── Speech Tools Tests ──────────────────────────────────────────────────


class TestSpeechTools(unittest.TestCase):

    def setUp(self):
        self.st = SpeechTools()

    def test_initial_state(self):
        self.assertEqual(self.st.tts_status, SpeechStatus.IDLE)
        self.assertEqual(self.st.stt_status, SpeechStatus.IDLE)

    def test_speak(self):
        entry = self.st.speak("Hello, world!")
        self.assertIsNotNone(entry)
        self.assertTrue(entry.is_tts)
        self.assertEqual(self.st.tts_status, SpeechStatus.SPEAKING)

    def test_stop_speaking(self):
        self.st.speak("Test")
        self.st.stop_speaking()
        self.assertEqual(self.st.tts_status, SpeechStatus.IDLE)

    def test_pause_resume(self):
        self.st.speak("Test")
        self.st.pause_speaking()
        self.assertEqual(self.st.tts_status, SpeechStatus.PAUSED)
        self.st.resume_speaking()
        self.assertEqual(self.st.tts_status, SpeechStatus.SPEAKING)

    def test_speak_empty(self):
        result = self.st.speak("")
        self.assertIsNone(result)

    def test_set_speed(self):
        speed = self.st.set_speed(1.5)
        self.assertEqual(speed, 1.5)

    def test_set_speed_clamp(self):
        speed = self.st.set_speed(5.0)
        self.assertEqual(speed, 3.0)

    def test_set_pitch(self):
        pitch = self.st.set_pitch(1.2)
        self.assertEqual(pitch, 1.2)

    def test_set_volume(self):
        vol = self.st.set_volume(0.8)
        self.assertEqual(vol, 0.8)

    def test_voices(self):
        voices = self.st.voices
        self.assertGreater(len(voices), 0)

    def test_select_voice(self):
        result = self.st.select_voice(1)
        self.assertTrue(result)
        self.assertEqual(self.st.current_voice.name, "Sarah")

    def test_start_listening(self):
        self.st.start_listening()
        self.assertEqual(self.st.stt_status, SpeechStatus.RECORDING)

    def test_stop_listening(self):
        self.st.start_listening()
        self.st.add_transcription("Hello world")
        entry = self.st.stop_listening()
        self.assertIsNotNone(entry)
        self.assertFalse(entry.is_tts)

    def test_history(self):
        self.st.speak("Test 1")
        self.st.speak("Test 2")
        self.assertEqual(len(self.st.history), 2)

    def test_clear_history(self):
        self.st.speak("Test")
        count = self.st.clear_history()
        self.assertEqual(count, 1)
        self.assertEqual(len(self.st.history), 0)

    def test_pronunciations(self):
        pron = self.st.add_pronunciation("test", "TEST")
        self.assertIsNotNone(pron)

    def test_render_tts(self):
        lines = self.st.render_tts()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_stt(self):
        lines = self.st.render_stt()
        self.assertIsInstance(lines, list)

    def test_render_voices(self):
        lines = self.st.render_voices()
        self.assertIsInstance(lines, list)

    def test_render_history(self):
        lines = self.st.render_history()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.st.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_speak(self):
        self.st._tts_text = "Hello"
        self.st.handle_key(" ")
        self.assertEqual(self.st.tts_status, SpeechStatus.SPEAKING)

    def test_handle_key_stop(self):
        self.st.speak("Test")
        self.st.handle_key("s")
        self.assertEqual(self.st.tts_status, SpeechStatus.IDLE)


class TestVoice(unittest.TestCase):

    def test_display(self):
        v = Voice("David", VoiceType.MALE, SpeechLanguage.ENGLISH)
        self.assertEqual(v.display, "David (Male)")

    def test_lang_code(self):
        v = Voice("David", VoiceType.MALE, SpeechLanguage.ENGLISH)
        self.assertEqual(v.lang_code, "en-US")


class TestSpeechEntry(unittest.TestCase):

    def test_preview(self):
        e = SpeechEntry(text="Hello world", is_tts=True)
        self.assertEqual(e.preview, "Hello world")

    def test_preview_long(self):
        e = SpeechEntry(text="x" * 100, is_tts=True)
        self.assertIn("...", e.preview)

    def test_duration_str(self):
        e = SpeechEntry(text="", duration_seconds=125)
        self.assertEqual(e.duration_str, "2m 5s")


class TestPronunciationEntry(unittest.TestCase):

    def test_pronunciation(self):
        p = PronunciationEntry("Nyrqis", "NIR-kiss")
        self.assertEqual(p.word, "Nyrqis")


# ─── VPN Manager Tests ───────────────────────────────────────────────────


class TestVPNManager(unittest.TestCase):

    def setUp(self):
        self.vpn = VPNManager()

    def test_initial_state(self):
        self.assertFalse(self.vpn.is_connected)
        self.assertGreater(len(self.vpn.profiles), 0)

    def test_connect(self):
        result = self.vpn.connect()
        self.assertTrue(result)
        self.assertTrue(self.vpn.is_connected)

    def test_disconnect(self):
        self.vpn.connect()
        result = self.vpn.disconnect()
        self.assertTrue(result)
        self.assertFalse(self.vpn.is_connected)

    def test_connect_profile(self):
        profile = self.vpn.profiles[0]
        result = self.vpn.connect(profile.profile_id)
        self.assertTrue(result)

    def test_create_profile(self):
        initial = len(self.vpn.profiles)
        profile = self.vpn.create_profile("New VPN")
        self.assertEqual(len(self.vpn.profiles), initial + 1)

    def test_delete_profile(self):
        profile = self.vpn.create_profile("Delete Me")
        result = self.vpn.delete_profile(profile.profile_id)
        self.assertTrue(result)

    def test_toggle_auto_connect(self):
        profile = self.vpn.profiles[0]
        was = profile.auto_connect
        result = self.vpn.toggle_auto_connect(profile.profile_id)
        self.assertNotEqual(result, was)

    def test_toggle_kill_switch(self):
        profile = self.vpn.profiles[0]
        was = profile.kill_switch
        result = self.vpn.toggle_kill_switch(profile.profile_id)
        self.assertNotEqual(result, was)

    def test_servers(self):
        servers = self.vpn.servers
        self.assertGreater(len(servers), 0)

    def test_stats(self):
        self.vpn.connect()
        self.vpn.update_stats()
        self.assertGreater(self.vpn.stats.bytes_sent, 0)

    def test_logs(self):
        logs = self.vpn.logs
        self.assertGreater(len(logs), 0)

    def test_render_profiles(self):
        lines = self.vpn.render_profiles()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_servers(self):
        lines = self.vpn.render_servers()
        self.assertIsInstance(lines, list)

    def test_render_stats(self):
        lines = self.vpn.render_stats()
        self.assertIsInstance(lines, list)

    def test_render_logs(self):
        lines = self.vpn.render_logs()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.vpn.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_connect(self):
        self.vpn.handle_key("Enter")
        self.assertTrue(self.vpn.is_connected)

    def test_handle_key_disconnect(self):
        self.vpn.connect()
        self.vpn.handle_key("d")
        self.assertFalse(self.vpn.is_connected)


class TestVPNProfile(unittest.TestCase):

    def test_status_icon(self):
        p = VPNProfile(name="Test", status=VPNStatus.CONNECTED)
        self.assertEqual(p.status_icon, "🟢")

    def test_server_str(self):
        s = VPNServer("NY", VPNRegion.US_EAST)
        p = VPNProfile(name="Test", server=s)
        self.assertIn("NY", p.server_str)


class TestVPNServer(unittest.TestCase):

    def test_display(self):
        s = VPNServer("New York", VPNRegion.US_EAST)
        self.assertIn("New York", s.display)

    def test_load_bar(self):
        s = VPNServer("NY", VPNRegion.US_EAST, load_percent=50)
        bar = s.load_bar
        self.assertEqual(len(bar), 10)


class TestVPNStats(unittest.TestCase):

    def test_bytes_sent_str(self):
        s = VPNStats(bytes_sent=1024 * 1024)
        self.assertIn("MB", s.bytes_sent_str)

    def test_duration_str(self):
        s = VPNStats(duration_seconds=3661)
        self.assertIn("1h", s.duration_str)


class TestConnectionLog(unittest.TestCase):

    def test_time_str(self):
        log = ConnectionLog(event="Test")
        self.assertIn(":", log.time_str)

    def test_icon_error(self):
        log = ConnectionLog(event="Error", is_error=True)
        self.assertEqual(log.icon, "❌")


# ─── Mind Map Tests ──────────────────────────────────────────────────────


class TestMindMap(unittest.TestCase):

    def setUp(self):
        self.mm = MindMap()

    def test_initial_state(self):
        self.assertGreater(self.mm.node_count, 0)
        self.assertIsNotNone(self.mm.get_root())

    def test_add_node(self):
        initial = self.mm.node_count
        node = self.mm.add_node("New Idea", "root")
        self.assertEqual(self.mm.node_count, initial + 1)

    def test_delete_node(self):
        node = self.mm.add_node("To Delete", "root")
        result = self.mm.delete_node(node.node_id)
        self.assertTrue(result)

    def test_cannot_delete_root(self):
        result = self.mm.delete_node("root")
        self.assertFalse(result)

    def test_get_children(self):
        children = self.mm.get_children("root")
        self.assertGreater(len(children), 0)

    def test_get_node(self):
        node = self.mm.get_node("root")
        self.assertIsNotNone(node)
        self.assertEqual(node.text, "Nyrqis OS")

    def test_update_text(self):
        result = self.mm.update_node_text("root", "New Root")
        self.assertTrue(result)
        self.assertEqual(self.mm.get_root().text, "New Root")

    def test_update_notes(self):
        result = self.mm.update_node_notes("root", "My notes")
        self.assertTrue(result)

    def test_update_color(self):
        result = self.mm.update_node_color("root", "#FF0000")
        self.assertTrue(result)

    def test_toggle_collapse(self):
        node = self.mm.get_node("arch")
        result = self.mm.toggle_collapse("arch")
        self.assertTrue(result)
        self.assertTrue(node.collapsed)

    def test_selection(self):
        self.mm.select_up()
        self.mm.select_down()

    def test_select_parent(self):
        self.mm.select(3)  # Select a child
        self.mm.select_parent()

    def test_select_child(self):
        self.mm.select(0)  # Select root
        self.mm.select_child()

    def test_search(self):
        results = self.mm.search("Wayland")
        self.assertGreater(len(results), 0)

    def test_export_outline(self):
        outline = self.mm.export_outline()
        self.assertIn("Nyrqis OS", outline)

    def test_export_markdown(self):
        md = self.mm.export_markdown()
        self.assertIn("# Nyrqis OS", md)

    def test_visible_nodes(self):
        visible = self.mm.visible_nodes
        self.assertGreater(len(visible), 0)

    def test_render(self):
        lines = self.mm.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_undo(self):
        self.mm.add_node("Test", "root")
        result = self.mm.undo()
        self.assertTrue(result)

    def test_redo(self):
        self.mm.add_node("Test", "root")
        self.mm.undo()
        result = self.mm.redo()
        self.assertTrue(result)


class TestMindNode(unittest.TestCase):

    def test_display(self):
        n = MindNode("Test", NodeType.LEAF)
        self.assertIn("Test", n.display)

    def test_node_id(self):
        n = MindNode("Test")
        self.assertIsNotNone(n.node_id)
        self.assertEqual(len(n.node_id), 8)

    def test_icon(self):
        n = MindNode("Test", NodeType.ROOT)
        self.assertEqual(n.icon, "🍄")


class TestMindConnection(unittest.TestCase):

    def test_connection_id(self):
        c = MindConnection("a", "b")
        self.assertIsNotNone(c.connection_id)


if __name__ == "__main__":
    unittest.main()
