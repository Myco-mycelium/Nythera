"""Tests for git client, geo map, and AI chat."""
import unittest
import time
import math

from ui.git_client import (
    GitClient, GitCommit, GitBranch, StagedFile, ConflictFile, GitTag,
    GitStash, DiffLine, DiffHunk, DiffFile, CommitType, DiffLineType,
)
from ui.geo_map import (
    GeoMap, Marker, Route, MapArea, Measurement, LatLng,
    MarkerCategory, MapLayer, RouteMode, RoutePoint,
)
from ui.ai_chat import (
    AIChat, AIModel, ChatMessage, Conversation, PromptTemplate,
    ChatStats, MessageRole, ModelCapability,
)


# ─── Git Client Tests ─────────────────────────────────────────────────

class TestDiffLine(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(DiffLine(DiffLineType.ADDED).prefix, "+")
        self.assertEqual(DiffLine(DiffLineType.REMOVED).prefix, "-")
        self.assertEqual(DiffLine(DiffLineType.CONTEXT).prefix, " ")


class TestDiffHunk(unittest.TestCase):
    def test_counts(self):
        hunk = DiffHunk(lines=[
            DiffLine(DiffLineType.ADDED),
            DiffLine(DiffLineType.ADDED),
            DiffLine(DiffLineType.REMOVED),
            DiffLine(DiffLineType.CONTEXT),
        ])
        self.assertEqual(hunk.added_count, 2)
        self.assertEqual(hunk.removed_count, 1)


class TestGitCommit(unittest.TestCase):
    def test_time_str(self):
        c = GitCommit(timestamp=time.time())
        self.assertIn(":", c.time_str)

    def test_relative_time(self):
        c = GitCommit(timestamp=time.time() - 7200)
        self.assertIn("h ago", c.relative_time)

    def test_stats_str(self):
        c = GitCommit(files_changed=5, insertions=100, deletions=20)
        stats = c.stats_str
        self.assertIn("5 files", stats)
        self.assertIn("+100", stats)
        self.assertIn("-20", stats)


class TestGitBranch(unittest.TestCase):
    def test_display_name(self):
        b = GitBranch("main", is_current=True)
        self.assertTrue(b.display_name.startswith("*"))

    def test_status_str(self):
        b = GitBranch("test", ahead=3, behind=2)
        self.assertIn("↑3", b.status_str)
        self.assertIn("↓2", b.status_str)

    def test_protection(self):
        b = GitBranch("main", protected=True)
        self.assertEqual(b.protection_icon, "🔒")


class TestGitClient(unittest.TestCase):
    def setUp(self):
        self.client = GitClient()

    def test_initial_state(self):
        self.assertGreater(len(self.client._commits), 0)
        self.assertGreater(len(self.client._branches), 0)

    def test_render(self):
        lines = self.client.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("GIT CLIENT" in l for l in lines))

    def test_render_branches(self):
        self.client.set_view("branches")
        lines = self.client.render()
        self.assertTrue(any("Branches" in l for l in lines))

    def test_render_staging(self):
        self.client.set_view("staging")
        lines = self.client.render()
        self.assertTrue(any("Staging" in l for l in lines))

    def test_render_conflicts(self):
        self.client.set_view("conflicts")
        lines = self.client.render()
        self.assertTrue(any("Conflicts" in l for l in lines))

    def test_render_tags(self):
        self.client.set_view("tags")
        lines = self.client.render()
        self.assertTrue(any("Tags" in l for l in lines))

    def test_render_stash(self):
        self.client.set_view("stash")
        lines = self.client.render()
        self.assertTrue(any("Stash" in l for l in lines))

    def test_render_diff(self):
        self.client.set_view("diff")
        lines = self.client.render()
        self.assertTrue(any("Diff" in l for l in lines))

    def test_select_commit(self):
        self.client.select_commit(5)
        self.assertEqual(self.client._selected_commit, 5)

    def test_toggle_staged(self):
        self.client.toggle_staged(0)
        self.assertFalse(self.client._staged_files[0].staged)

    def test_resolve_conflict(self):
        self.client.resolve_conflict(0, "mine")
        self.assertTrue(self.client._conflicts[0].resolved)

    def test_staged_count(self):
        self.assertGreater(self.client.staged_count, 0)

    def test_conflict_count(self):
        self.assertGreater(self.client.conflict_count, 0)

    def test_toggle_graph(self):
        self.client.toggle_graph()
        self.assertFalse(self.client._show_graph)


# ─── Geo Map Tests ───────────────────────────────────────────────────

class TestLatLng(unittest.TestCase):
    def test_display(self):
        ll = LatLng(37.7749, -122.4194)
        self.assertIn("N", ll.lat_str)
        self.assertIn("W", ll.lng_str)

    def test_distance(self):
        a = LatLng(37.7749, -122.4194)
        b = LatLng(37.7849, -122.4094)
        d = a.distance_to(b)
        self.assertGreater(d, 0)
        self.assertLess(d, 5)


class TestMarker(unittest.TestCase):
    def test_category_icon(self):
        m = Marker(category=MarkerCategory.STAR)
        self.assertEqual(m.category_icon, "⭐")


class TestRoute(unittest.TestCase):
    def test_duration_str(self):
        r = Route(duration_min=90)
        self.assertIn("h", r.duration_str)

    def test_distance_str(self):
        r = Route(distance_km=0.5)
        self.assertIn("m", r.distance_str)

    def test_long_distance(self):
        r = Route(distance_km=15.3)
        self.assertIn("km", r.distance_str)


class TestMapArea(unittest.TestCase):
    def test_point_count(self):
        a = MapArea(points=[LatLng(0, 0), LatLng(1, 1), LatLng(2, 0)])
        self.assertEqual(a.point_count, 3)


class TestMeasurement(unittest.TestCase):
    def test_distance(self):
        m = Measurement(start=LatLng(0, 0), end=LatLng(1, 1))
        self.assertGreater(m.distance_km, 0)


class TestGeoMap(unittest.TestCase):
    def setUp(self):
        self.map = GeoMap()

    def test_initial_state(self):
        self.assertGreater(len(self.map._markers), 0)
        self.assertGreater(len(self.map._routes), 0)

    def test_render(self):
        lines = self.map.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("GEO MAP" in l for l in lines))

    def test_render_markers(self):
        self.map.set_view("markers")
        lines = self.map.render()
        self.assertTrue(any("Markers" in l for l in lines))

    def test_render_routes(self):
        self.map.set_view("routes")
        lines = self.map.render()
        self.assertTrue(any("Routes" in l for l in lines))

    def test_render_layers(self):
        self.map.set_view("layers")
        lines = self.map.render()
        self.assertTrue(any("Layers" in l for l in lines))

    def test_render_measurements(self):
        self.map.set_view("measurements")
        lines = self.map.render()
        self.assertTrue(any("Measurements" in l for l in lines))

    def test_toggle_layer(self):
        initial = len(self.map._active_layers)
        self.map.toggle_layer(MapLayer.TRAFFIC)
        self.assertEqual(len(self.map._active_layers), initial + 1)
        self.map.toggle_layer(MapLayer.TRAFFIC)
        self.assertEqual(len(self.map._active_layers), initial)

    def test_zoom_in_out(self):
        z = self.map.zoom
        self.map.zoom_in()
        self.assertEqual(self.map.zoom, z + 1)
        self.map.zoom_out()
        self.assertEqual(self.map.zoom, z)

    def test_set_zoom(self):
        self.map.set_zoom(5)
        self.assertEqual(self.map.zoom, 5)
        self.map.set_zoom(25)
        self.assertEqual(self.map.zoom, 20)


# ─── AI Chat Tests ───────────────────────────────────────────────────

class TestAIModel(unittest.TestCase):
    def test_capability_icons(self):
        m = AIModel(capabilities=[ModelCapability.CHAT, ModelCapability.CODE])
        icons = m.capability_icons
        self.assertIn("💬", icons)
        self.assertIn("💻", icons)

    def test_cost_str(self):
        m = AIModel(cost_per_1k=0.0)
        self.assertEqual(m.cost_str, "free")

    def test_speed_bar(self):
        m = AIModel(speed_tokens_per_sec=50)
        bar = m.speed_bar
        self.assertIn("█", bar)
        self.assertEqual(len(bar), 20)


class TestChatMessage(unittest.TestCase):
    def test_time_str(self):
        msg = ChatMessage(timestamp=time.time())
        self.assertIn(":", msg.time_str)

    def test_token_str(self):
        msg = ChatMessage(tokens_used=500)
        self.assertIn("500", msg.token_str)

    def test_truncated(self):
        msg = ChatMessage(content="x" * 200)
        self.assertTrue(len(msg.truncated) <= 123)


class TestPromptTemplate(unittest.TestCase):
    def test_preview(self):
        t = PromptTemplate(prompt="a" * 100)
        self.assertIn("...", t.preview)


class TestConversation(unittest.TestCase):
    def test_message_count(self):
        c = Conversation(messages=[
            ChatMessage(role=MessageRole.USER, content="hi"),
            ChatMessage(role=MessageRole.ASSISTANT, content="hello"),
        ])
        self.assertEqual(c.message_count, 2)

    def test_last_message(self):
        c = Conversation(messages=[
            ChatMessage(content="last message"),
        ])
        self.assertEqual(c.last_message_preview, "last message")


class TestAIChat(unittest.TestCase):
    def setUp(self):
        self.chat = AIChat()

    def test_initial_state(self):
        self.assertGreater(len(self.chat._models), 0)
        self.assertGreater(len(self.chat._conversations), 0)
        self.assertGreater(len(self.chat._templates), 0)

    def test_render(self):
        lines = self.chat.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("AI CHAT" in l for l in lines))

    def test_render_history(self):
        self.chat.set_view("history")
        lines = self.chat.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_models(self):
        self.chat.set_view("models")
        lines = self.chat.render()
        self.assertTrue(any("Models" in l for l in lines))

    def test_render_templates(self):
        self.chat.set_view("templates")
        lines = self.chat.render()
        self.assertTrue(any("Templates" in l for l in lines))

    def test_render_stats(self):
        self.chat.set_view("stats")
        lines = self.chat.render()
        self.assertTrue(any("Statistics" in l for l in lines))

    def test_select_model(self):
        self.chat.select_model(3)
        self.assertEqual(self.chat._selected_model, 3)

    def test_select_conversation(self):
        self.chat.select_conversation(1)
        self.assertEqual(self.chat._current_conversation, 1)

    def test_set_temperature(self):
        self.chat.set_temperature(1.5)
        self.assertEqual(self.chat._temperature, 1.5)
        self.chat.set_temperature(5.0)
        self.assertEqual(self.chat._temperature, 2.0)
        self.chat.set_temperature(-1.0)
        self.assertEqual(self.chat._temperature, 0.0)

    def test_current_conversation(self):
        conv = self.chat.current_conversation
        self.assertIsNotNone(conv)
        self.assertGreater(conv.message_count, 0)


if __name__ == "__main__":
    unittest.main()
