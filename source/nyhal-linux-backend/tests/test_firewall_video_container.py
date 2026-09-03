"""Tests for Firewall, Video Editor, and Container Manager."""
import unittest
import time
from ui.firewall import (
    FirewallManager, FirewallRule, TrafficLog, ThreatEvent, FirewallProfile,
    RuleAction, Protocol, Direction, ThreatCategory, ChainType, ProfileType,
)
from ui.video_editor import (
    VideoEditor, VideoClip, AudioClip, Transition, TextOverlay, ExportPreset, Marker,
    TransitionType, EffectType, ExportFormat, Resolution, AudioCodec,
)
from ui.container_manager import (
    ContainerManager, Container, Image, Volume2, Network, Port, ResourceUsage,
    ContainerRuntime, ContainerState, ImageType,
)


# ==================== Firewall Tests ====================

class TestFirewallRule(unittest.TestCase):
    def test_create(self):
        r = FirewallRule(1, "Allow SSH", RuleAction.ALLOW, Protocol.SSH, dest_port=22)
        self.assertEqual(r.name, "Allow SSH")

    def test_action_icon(self):
        r = FirewallRule(1, "T", RuleAction.ALLOW)
        self.assertEqual(r.action_icon, "🟢")

    def test_direction_icon(self):
        r = FirewallRule(1, "T", direction=Direction.INBOUND)
        self.assertEqual(r.direction_icon, "⬇")

    def test_port_str(self):
        r = FirewallRule(1, "T", dest_port=443)
        self.assertEqual(r.port_str, ":443")

    def test_hit_bar(self):
        r = FirewallRule(1, "T", hit_count=500)
        bar = r.hit_bar
        self.assertIn("█", bar)


class TestTrafficLog(unittest.TestCase):
    def test_create(self):
        log = TrafficLog(time.time(), "1.2.3.4", "5.6.7.8")
        self.assertIn(":", log.time_str)

    def test_bytes_str(self):
        log = TrafficLog(bytes_transferred=500)
        self.assertEqual(log.bytes_str, "500 B")
        log2 = TrafficLog(bytes_transferred=2048)
        self.assertIn("KB", log2.bytes_str)


class TestThreatEvent(unittest.TestCase):
    def test_create(self):
        t = ThreatEvent(time.time(), ThreatCategory.PORT_SCAN, "1.2.3.4", "Test", 3, True)
        self.assertTrue(t.blocked)

    def test_severity_bar(self):
        t = ThreatEvent(severity=4)
        self.assertEqual(t.severity_bar, "████░")


class TestFirewallProfile(unittest.TestCase):
    def test_active(self):
        p = FirewallProfile("Home", active=True)
        self.assertEqual(p.status_icon, "🟢")


class TestFirewallManager(unittest.TestCase):
    def setUp(self):
        self.fw = FirewallManager()

    def test_initial_state(self):
        self.assertTrue(self.fw.enabled)
        self.assertGreater(self.fw.total_rules, 0)

    def test_selected_rule(self):
        rule = self.fw.selected_rule
        self.assertIsNotNone(rule)

    def test_select_rule(self):
        self.fw.select_rule(3)
        self.assertEqual(self.fw._selected_rule, 3)

    def test_blocked_count(self):
        self.assertGreater(self.fw.blocked_count, 0)

    def test_total_threats(self):
        self.assertGreater(self.fw.total_threats, 0)

    def test_blocked_threats(self):
        self.assertGreater(self.fw.blocked_threats, 0)

    def test_add_rule(self):
        count = self.fw.total_rules
        self.fw.add_rule("Test Rule", RuleAction.ALLOW, 8080)
        self.assertEqual(self.fw.total_rules, count + 1)

    def test_delete_rule(self):
        count = self.fw.total_rules
        self.fw.delete_rule(0)
        self.assertEqual(self.fw.total_rules, count - 1)

    def test_toggle_rule(self):
        rule = self.fw.selected_rule
        old = rule.enabled
        self.fw.toggle_rule()
        self.assertNotEqual(rule.enabled, old)

    def test_render(self):
        lines = self.fw.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("FIREWALL MANAGER" in l for l in lines))


# ==================== VideoEditor Tests ====================

class TestVideoClip(unittest.TestCase):
    def test_create(self):
        c = VideoClip(1, "Test", duration_s=10.0)
        self.assertEqual(c.duration_str, "0:10.00")

    def test_resolution(self):
        c = VideoClip(1, "T", width=1920, height=1080)
        self.assertEqual(c.resolution_str, "1920x1080")

    def test_bitrate_str(self):
        c = VideoClip(1, "T", bitrate=8000)
        self.assertIn("kbps", c.bitrate_str)
        c2 = VideoClip(2, "T", bitrate=15000)
        self.assertIn("Mbps", c2.bitrate_str)

    def test_thumbnail(self):
        c = VideoClip(1, "T")
        self.assertIn("▓", c.thumbnail)


class TestTransition(unittest.TestCase):
    def test_create(self):
        t = Transition(1, 2, TransitionType.CROSSFADE, 0.5)
        self.assertEqual(t.duration_s, 0.5)


class TestTextOverlay(unittest.TestCase):
    def test_create(self):
        t = TextOverlay("Hello", 0, 90)
        self.assertEqual(t.text, "Hello")


class TestExportPreset(unittest.TestCase):
    def test_create(self):
        p = ExportPreset("YouTube", ExportFormat.MP4_H264, Resolution.R_1080P)
        self.assertEqual(p.name, "YouTube")

    def test_quality_bar(self):
        p = ExportPreset("T", quality="High")
        bar = p.quality_bar
        self.assertIn("█", bar)

    def test_estimated_size(self):
        p = ExportPreset("T", video_bitrate=8000, audio_bitrate=192)
        self.assertIn("KB/s", p.estimated_size)


class TestVideoEditor(unittest.TestCase):
    def setUp(self):
        self.ed = VideoEditor()

    def test_initial_state(self):
        self.assertGreater(self.ed.total_clips, 0)

    def test_selected_clip(self):
        clip = self.ed.selected_clip
        self.assertIsNotNone(clip)

    def test_playhead(self):
        ph = self.ed.playhead_time
        self.assertIn(":", ph)

    def test_total_duration(self):
        td = self.ed.total_duration
        self.assertIn(":", td)

    def test_toggle_play(self):
        self.ed.toggle_play()
        self.assertTrue(self.ed._playing)
        self.ed.toggle_play()
        self.assertFalse(self.ed._playing)

    def test_select_clip(self):
        self.ed.select_clip(0, 2)
        self.assertEqual(self.ed._selected_track, 0)
        self.assertEqual(self.ed._selected_clip_idx, 2)

    def test_add_clip(self):
        count = self.ed.total_clips
        self.ed.add_clip()
        self.assertEqual(self.ed.total_clips, count + 1)

    def test_transitions(self):
        self.assertGreater(len(self.ed._transitions), 0)

    def test_text_overlays(self):
        self.assertGreater(len(self.ed._text_overlays), 0)

    def test_export_presets(self):
        self.assertGreater(len(self.ed._export_presets), 0)

    def test_render(self):
        lines = self.ed.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("VIDEO EDITOR" in l for l in lines))


# ==================== ContainerManager Tests ====================

class TestPort(unittest.TestCase):
    def test_create(self):
        p = Port(8080, 80)
        self.assertEqual(p.mapping, "0.0.0.0:8080→80/tcp")


class TestResourceUsage(unittest.TestCase):
    def test_create(self):
        r = ResourceUsage(cpu_percent=5.0, memory_mb=256)
        self.assertEqual(r.memory_str, "256 MB")

    def test_cpu_bar(self):
        r = ResourceUsage(cpu_percent=50)
        bar = r.cpu_bar
        self.assertIn("█", bar)

    def test_memory_bar(self):
        r = ResourceUsage(memory_percent=75)
        bar = r.memory_bar
        self.assertIn("█", bar)

    def test_network_str(self):
        r = ResourceUsage(network_rx_bytes=1024 * 1024, network_tx_bytes=512 * 1024)
        ns = r.network_str
        self.assertIn("↓", ns)
        self.assertIn("↑", ns)

    def test_uptime_str(self):
        r = ResourceUsage(uptime_s=3660)
        self.assertEqual(r.uptime_str, "1h 1m")


class TestContainer(unittest.TestCase):
    def test_create(self):
        c = Container("abc123", "test")
        self.assertEqual(c.short_id, "abc123")

    def test_state_icon(self):
        c = Container("abc", "T", state=ContainerState.RUNNING)
        self.assertEqual(c.state_icon, "🟢")

    def test_age_str(self):
        c = Container("abc", "T", created=time.time() - 7200)
        self.assertIn("h ago", c.age_str)


class TestImage(unittest.TestCase):
    def test_create(self):
        i = Image("sha256:abc", "nginx", "alpine", 42)
        self.assertEqual(i.full_name, "nginx:alpine")

    def test_size_str(self):
        i = Image("sha256:abc", "T", size_mb=1500)
        self.assertIn("GB", i.size_str)


class TestContainerManager(unittest.TestCase):
    def setUp(self):
        self.mgr = ContainerManager()

    def test_initial_state(self):
        self.assertGreater(self.mgr.total_containers, 0)
        self.assertGreater(self.mgr.running_containers, 0)

    def test_selected_container(self):
        c = self.mgr.selected_container
        self.assertIsNotNone(c)

    def test_select_container(self):
        self.mgr.select_container(3)
        self.assertEqual(self.mgr._selected_container, 3)

    def test_total_images(self):
        self.assertGreater(self.mgr.total_images, 0)

    def test_total_disk(self):
        self.assertGreater(len(self.mgr.total_disk_usage), 0)

    def test_start_stop(self):
        self.mgr.stop_container(0)
        c = self.mgr._containers[0]
        self.assertEqual(c.state, ContainerState.STOPPED)
        self.mgr.start_container(0)
        self.assertEqual(c.state, ContainerState.RUNNING)

    def test_restart(self):
        self.mgr.restart_container(0)
        c = self.mgr._containers[0]
        self.assertEqual(c.state, ContainerState.RESTARTING)

    def test_volumes(self):
        self.assertGreater(len(self.mgr._volumes), 0)

    def test_networks(self):
        self.assertGreater(len(self.mgr._networks), 0)

    def test_render(self):
        lines = self.mgr.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("CONTAINER MANAGER" in l for l in lines))


class TestContainerRuntime(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ContainerRuntime.DOCKER.value, "Docker")
        self.assertEqual(ContainerRuntime.PODMAN.value, "Podman")


if __name__ == "__main__":
    unittest.main()
