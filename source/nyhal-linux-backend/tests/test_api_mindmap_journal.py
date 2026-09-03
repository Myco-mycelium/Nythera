"""Tests for API Tester, Mind Map Editor, and System Journal."""
import unittest
import time
from ui.api_tester import (
    ApiTester, ApiRequest, RequestCollection, HttpResponse, AuthConfig,
    RequestBody, Environment, KeyValuePair,
    HttpMethod, AuthType, BodyType, ResponseStatus,
)
from ui.mindmap import (
    MindMapEditor, MindMap, MindNode, Connection, MindMapLayout,
    NodeShape, NodeStyle, ExportFormat,
)
from ui.system_journal import (
    SystemJournal, JournalEntry, ServiceUnit,
    LogLevel, ServiceState, UnitType,
)


# ==================== ApiTester Tests ====================

class TestKeyValuePair(unittest.TestCase):
    def test_create(self):
        kv = KeyValuePair("Accept", "application/json")
        self.assertEqual(kv.key, "Accept")

    def test_masked(self):
        auth = AuthConfig(AuthType.BEARER, "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4ifQ.abc123def456")
        self.assertIn("****", auth.masked_token)


class TestHttpResponse(unittest.TestCase):
    def test_success(self):
        r = HttpResponse(200, "OK")
        self.assertEqual(r.status_category, ResponseStatus.SUCCESS)
        self.assertEqual(r.status_icon, "✅")

    def test_client_error(self):
        r = HttpResponse(404, "Not Found")
        self.assertEqual(r.status_category, ResponseStatus.CLIENT_ERROR)

    def test_server_error(self):
        r = HttpResponse(500, "Internal Server Error")
        self.assertEqual(r.status_category, ResponseStatus.SERVER_ERROR)

    def test_size_str(self):
        r = HttpResponse(size_bytes=1500)
        self.assertIn("KB", r.size_str)

    def test_time_str(self):
        r = HttpResponse(time_ms=5.0)
        self.assertIn("ms", r.time_str)


class TestApiRequest(unittest.TestCase):
    def test_create(self):
        r = ApiRequest(1, "Test", HttpMethod.GET, "/api/test")
        self.assertEqual(r.method, HttpMethod.GET)

    def test_method_icon(self):
        r = ApiRequest(1, "T", HttpMethod.POST)
        self.assertEqual(r.method_icon, "🔵")


class TestApiTester(unittest.TestCase):
    def setUp(self):
        self.tester = ApiTester()

    def test_initial_state(self):
        self.assertGreater(len(self.tester._collections), 0)

    def test_total_requests(self):
        self.assertGreater(self.tester.total_requests, 0)

    def test_select_collection(self):
        self.tester.select_collection(1)
        self.assertEqual(self.tester._selected_collection, 1)

    def test_send_request(self):
        self.tester.select_request(0)
        self.tester.send_request()
        req = self.tester.selected_request
        self.assertIsNotNone(req.response)

    def test_environments(self):
        self.assertGreater(len(self.tester._environments), 0)

    def test_render(self):
        lines = self.tester.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("REST API TESTER" in l for l in lines))


# ==================== MindMap Tests ====================

class TestMindNode(unittest.TestCase):
    def test_create(self):
        n = MindNode(0, "Root", 0, 0)
        self.assertEqual(n.text, "Root")

    def test_priority_icon(self):
        n = MindNode(0, "T", priority=3)
        self.assertEqual(n.priority_icon, "🔴")

    def test_child_count(self):
        n = MindNode(0, "T", children_ids=[1, 2, 3])
        self.assertEqual(n.child_count, 3)

    def test_style_icon(self):
        n = MindNode(0, "T", style=NodeStyle.IDEA)
        self.assertEqual(n.style_icon, "💡")


class TestMindMap(unittest.TestCase):
    def test_node_count(self):
        m = MindMap("T", nodes=[MindNode(0, "Root"), MindNode(1, "Child")])
        self.assertEqual(m.node_count, 2)

    def test_connection_count(self):
        m = MindMap("T", connections=[Connection(0, 1)])
        self.assertEqual(m.connection_count, 1)


class TestMindMapEditor(unittest.TestCase):
    def setUp(self):
        self.editor = MindMapEditor()

    def test_initial_state(self):
        self.assertGreater(len(self.editor._maps), 0)
        self.assertGreater(self.editor.total_nodes, 0)

    def test_select_map(self):
        self.editor.select_map(1)
        self.assertEqual(self.editor._selected_map, 1)

    def test_selected_node(self):
        node = self.editor.selected_node
        self.assertIsNotNone(node)

    def test_add_child(self):
        m = self.editor.selected_map
        count = m.node_count
        self.editor.add_child()
        self.assertEqual(m.node_count, count + 1)

    def test_delete_node(self):
        m = self.editor.selected_map
        self.editor.select_node(1)  # not root
        count = m.node_count
        self.editor.delete_node()
        self.assertEqual(m.node_count, count - 1)

    def test_render(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("MIND MAP EDITOR" in l for l in lines))


# ==================== SystemJournal Tests ====================

class TestJournalEntry(unittest.TestCase):
    def test_create(self):
        e = JournalEntry(time.time(), "nyrqis", "systemd", 1234, LogLevel.INFO, "Test message")
        self.assertEqual(e.service, "systemd")

    def test_level_icon(self):
        e = JournalEntry(level=LogLevel.ERROR)
        self.assertEqual(e.level_icon, "❌")

    def test_time_str(self):
        e = JournalEntry(time.time())
        self.assertIn(":", e.time_str)


class TestServiceUnit(unittest.TestCase):
    def test_create(self):
        s = ServiceUnit("test.service", state=ServiceState.ACTIVE)
        self.assertEqual(s.state_icon, "🟢")

    def test_memory_str(self):
        s = ServiceUnit("T", memory_current=500 * 1024**2)
        self.assertIn("MB", s.memory_str)

    def test_memory_bar(self):
        s = ServiceUnit("T", memory_current=250 * 1024**2)
        bar = s.memory_bar
        self.assertIn("█", bar)

    def test_cpu_bar(self):
        s = ServiceUnit("T", cpu_usage=50)
        bar = s.cpu_bar
        self.assertIn("█", bar)


class TestSystemJournal(unittest.TestCase):
    def setUp(self):
        self.journal = SystemJournal()

    def test_initial_state(self):
        self.assertGreater(len(self.journal._entries), 0)
        self.assertGreater(len(self.journal._services), 0)

    def test_total_entries(self):
        self.assertGreater(self.journal.total_entries, 0)

    def test_active_services(self):
        self.assertGreater(self.journal.active_services, 0)

    def test_failed_services(self):
        self.assertGreaterEqual(self.journal.failed_services, 0)

    def test_select_entry(self):
        self.journal.select_entry(5)
        self.assertEqual(self.journal._selected_entry, 5)

    def test_set_filter(self):
        self.journal.set_filter(level=LogLevel.EMERGENCY)
        self.assertEqual(self.journal._filter_level, LogLevel.EMERGENCY)
        self.assertGreaterEqual(self.journal.total_entries, 0)

    def test_clear_filter(self):
        self.journal.set_filter(level=LogLevel.ERROR)
        self.journal.set_filter()  # clear
        self.assertIsNone(self.journal._filter_level)

    def test_render(self):
        lines = self.journal.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("SYSTEM JOURNAL" in l for l in lines))


class TestLogLevel(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(LogLevel.INFO.value, "info")
        self.assertEqual(LogLevel.ERROR.value, "err")


class TestServiceState(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ServiceState.ACTIVE.value, "active")
        self.assertEqual(ServiceState.FAILED.value, "failed")


if __name__ == "__main__":
    unittest.main()
