"""Tests for NetworkMonitor, MidiController, FileEncryption"""
import time
import unittest

from ui.network_monitor import (
    NetworkMonitor, NetworkInterface, InterfaceType, InterfaceStatus,
    TrafficSample, ProtocolStats, ConnectionEntry, Protocol, GraphType
)
from ui.midi_controller import (
    MidiController, MidiNote, InstrumentPreset, SequencerStep,
    NoteName, InstrumentType, TimeSignature, VelocityCurve, Scale
)
from ui.file_encryption import (
    FileEncryption, EncryptedFile, EncryptionKey, OperationLog,
    EncryptionAlgorithm, KeyDerivation, FileStatus, IntegrityStatus, OperationType
)


class TestNetworkMonitor(unittest.TestCase):
    def setUp(self):
        self.nm = NetworkMonitor()

    def test_initial_state(self):
        self.assertGreater(len(self.nm._interfaces), 0)
        self.assertEqual(self.nm._selected_interface, 0)

    def test_select_interface(self):
        self.nm.select_interface(1)
        self.assertEqual(self.nm._selected_interface, 1)
        self.assertEqual(self.nm.selected_interface.name, "wlan0")

    def test_select_invalid(self):
        self.nm.select_interface(99)
        self.assertEqual(self.nm._selected_interface, 0)

    def test_total_interfaces(self):
        self.assertEqual(self.nm.total_interfaces, 5)

    def test_up_interfaces(self):
        self.assertEqual(self.nm.up_interfaces, 5)

    def test_total_connections(self):
        self.assertGreater(self.nm.total_connections, 0)

    def test_total_rx(self):
        self.assertGreater(len(self.nm.total_rx_display), 0)

    def test_total_tx(self):
        self.assertGreater(len(self.nm.total_tx_display), 0)

    def test_sparkline(self):
        spark = self.nm.get_sparkline("eth0", True)
        self.assertEqual(len(spark), 32)

    def test_sparkline_empty(self):
        spark = self.nm.get_sparkline("nonexistent", True)
        self.assertIn("░", spark)

    def test_interface_rate_display(self):
        iface = NetworkInterface("test", InterfaceType.ETHERNET, InterfaceStatus.UP, "", "192.168.1.1", "", 1000, rx_rate_bps=1_500_000_000)
        self.assertIn("Gbps", iface.rx_rate_display)

    def test_interface_bytes_display(self):
        iface = NetworkInterface("test", InterfaceType.ETHERNET, InterfaceStatus.UP, "", "192.168.1.1", "", 1000, rx_bytes=2_147_483_648)
        self.assertIn("GB", iface.total_rx_display)

    def test_interface_uptime(self):
        iface = NetworkInterface("test", InterfaceType.ETHERNET, InterfaceStatus.UP, "", "192.168.1.1", "", 1000, connected_since=time.time() - 7200)
        self.assertIn("h", iface.uptime_display)

    def test_render(self):
        lines = self.nm.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("MONITOR" in l for l in lines))

    def test_render_connections(self):
        lines = self.nm.render_connections()
        self.assertGreater(len(lines), 3)

    def test_render_protocols(self):
        lines = self.nm.render_protocols()
        self.assertGreater(len(lines), 5)

    def test_render_detail(self):
        self.nm.select_interface(0)
        lines = self.nm.render_interface_detail()
        self.assertGreater(len(lines), 5)


class TestMidiController(unittest.TestCase):
    def setUp(self):
        self.mc = MidiController()

    def test_initial_state(self):
        self.assertGreater(len(self.mc._presets), 0)
        self.assertFalse(self.mc._is_playing)

    def test_select_preset(self):
        self.mc.select_preset(1)
        self.assertEqual(self.mc._selected_preset, 1)
        self.assertEqual(self.mc.selected_preset.name, "Electric Bass")

    def test_select_invalid(self):
        self.mc.select_preset(99)
        self.assertEqual(self.mc._selected_preset, 0)

    def test_total_presets(self):
        self.assertEqual(self.mc.total_presets, 8)

    def test_steps_with_notes(self):
        self.assertGreater(self.mc.steps_with_notes, 0)

    def test_total_notes(self):
        self.assertGreater(self.mc.total_notes, 0)

    def test_play_stop(self):
        self.mc.start_playback()
        self.assertTrue(self.mc._is_playing)
        self.mc.stop_playback()
        self.assertFalse(self.mc._is_playing)

    def test_note_on_off(self):
        self.mc.note_on(NoteName.C, 4, 100)
        self.assertEqual(len(self.mc._active_notes), 1)
        self.mc.note_off(NoteName.C, 4)
        self.assertEqual(len(self.mc._active_notes), 0)

    def test_add_note_to_step(self):
        note = MidiNote(NoteName.D, 4, 80, 0, 1)
        self.mc.add_note_to_step(5, note)
        self.assertEqual(len(self.mc._sequencer_steps[5].notes), 1)

    def test_remove_note_from_step(self):
        self.assertTrue(self.mc.remove_note_from_step(0, 0))

    def test_clear_step(self):
        self.mc.clear_step(0)
        self.assertEqual(len(self.mc._sequencer_steps[0].notes), 0)

    def test_midi_note_number(self):
        note = MidiNote(NoteName.C, 4, 100, 0, 1)
        self.assertEqual(note.midi_number, 60)

    def test_midi_note_display(self):
        note = MidiNote(NoteName.A, 3, 100, 0, 1)
        self.assertEqual(note.display_name, "A3")

    def test_velocity_display(self):
        note = MidiNote(NoteName.C, 4, 120, 0, 1)
        self.assertEqual(note.velocity_display, "fff")

    def test_scales(self):
        self.assertGreater(len(self.mc._scales), 0)

    def test_preset_volume_bar(self):
        preset = self.mc.selected_preset
        self.assertIn("█", preset.volume_bar)

    def test_preset_pan(self):
        preset = self.mc.selected_preset
        self.assertIn("■", preset.pan_display)

    def test_render(self):
        lines = self.mc.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("MIDI" in l for l in lines))

    def test_render_sequencer(self):
        lines = self.mc.render_sequencer()
        self.assertGreater(len(lines), 5)

    def test_render_preset_detail(self):
        self.mc.select_preset(0)
        lines = self.mc.render_preset_detail()
        self.assertGreater(len(lines), 3)


class TestFileEncryption(unittest.TestCase):
    def setUp(self):
        self.fe = FileEncryption()

    def test_initial_state(self):
        self.assertGreater(len(self.fe._files), 0)
        self.assertEqual(self.fe._selected_file, 0)

    def test_select_file(self):
        self.fe.select_file(1)
        self.assertEqual(self.fe._selected_file, 1)

    def test_select_invalid(self):
        self.fe.select_file(99)
        self.assertEqual(self.fe._selected_file, 0)

    def test_total_files(self):
        self.assertEqual(self.fe.total_files, 6)

    def test_encrypted_count(self):
        self.assertGreater(self.fe.encrypted_count, 0)

    def test_corrupted_count(self):
        self.assertGreater(self.fe.corrupted_count, 0)

    def test_total_size(self):
        self.assertGreater(self.fe.total_size, 0)
        self.assertIn("GB", self.fe.total_size_display)

    def test_encrypt_file(self):
        self.assertTrue(self.fe.encrypt_file(3))  # unencrypted file

    def test_decrypt_file(self):
        self.assertTrue(self.fe.decrypt_file(0))

    def test_verify_file(self):
        self.assertTrue(self.fe.verify_file(0))

    def test_compute_checksum(self):
        result = FileEncryption.compute_checksum("test data")
        self.assertEqual(len(result), 64)

    def test_file_display_size(self):
        f = EncryptedFile("test", 2_147_483_648, 2_147_483_904, EncryptionAlgorithm.AES_256_GCM, KeyDerivation.ARGON2ID, FileStatus.ENCRYPTED, IntegrityStatus.VALID, time.time())
        self.assertIn("GB", f.display_size)

    def test_file_overhead(self):
        f = EncryptedFile("test", 1000, 1264, EncryptionAlgorithm.AES_256_GCM, KeyDerivation.ARGON2ID, FileStatus.ENCRYPTED, IntegrityStatus.VALID, time.time())
        self.assertIn("26.4%", f.overhead_percent)

    def test_key_age(self):
        key = EncryptionKey("Test", EncryptionAlgorithm.AES_256_GCM, 256, "AA:BB", time.time() - 86400 * 30)
        self.assertEqual(key.age_days, 30)

    def test_key_expired(self):
        key = EncryptionKey("Test", EncryptionAlgorithm.AES_256_GCM, 256, "AA:BB", time.time() - 86400 * 100, expires_at=time.time() - 86400)
        self.assertTrue(key.is_expired)

    def test_operation_log(self):
        self.assertGreater(len(self.fe._operation_log), 0)

    def test_select_key(self):
        self.fe.select_key(1)
        self.assertEqual(self.fe._selected_key, 1)

    def test_render(self):
        lines = self.fe.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("ENCRYPTION" in l for l in lines))

    def test_render_file_detail(self):
        self.fe.select_file(0)
        lines = self.fe.render_file_detail()
        self.assertGreater(len(lines), 5)

    def test_render_keys(self):
        lines = self.fe.render_keys()
        self.assertGreater(len(lines), 5)

    def test_render_log(self):
        lines = self.fe.render_log()
        self.assertGreater(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
