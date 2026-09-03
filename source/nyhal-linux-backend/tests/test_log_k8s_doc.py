"""Tests for log aggregator, k8s dashboard, and doc editor."""
import unittest
import time

from ui.log_aggregator import (
    LogAggregator, LogEntry, LogSource, AlertRule, LogPattern, LogLevel,
)
from ui.k8s_dashboard import (
    K8sDashboard, Pod, PodPhase, Container, Node, Deployment, Service, K8sEvent,
    RestartPolicy,
)
from ui.doc_editor import (
    DocEditor, Document, Block, BlockType, TextRun, ImageBlock, TableCell,
    TableRow, TextAlign, ListStyle,
)


# ─── Log Aggregator Tests ─────────────────────────────────────────────

class TestLogLevel(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(len(LogLevel), 6)

    def test_icon(self):
        self.assertIn(LogLevel.ERROR.icon, "❌")


class TestLogEntry(unittest.TestCase):
    def test_time_str(self):
        entry = LogEntry(timestamp=time.time(), source="test", level=LogLevel.INFO, message="hello")
        self.assertIn(":", entry.time_str)

    def test_truncated_message(self):
        entry = LogEntry(timestamp=time.time(), source="test", level=LogLevel.INFO, message="x" * 100)
        self.assertTrue(len(entry.truncated_message) <= 75)
        self.assertIn("...", entry.truncated_message)

    def test_short_message(self):
        entry = LogEntry(timestamp=time.time(), source="test", level=LogLevel.INFO, message="hi")
        self.assertEqual(entry.truncated_message, "hi")


class TestLogSource(unittest.TestCase):
    def test_status_icon(self):
        src = LogSource("test", "file")
        self.assertEqual(src.status_icon, "🟢")

    def test_disabled(self):
        src = LogSource("test", "file", enabled=False)
        self.assertEqual(src.status_icon, "⏸")

    def test_rate_bar(self):
        src = LogSource("test", "file")
        src.rate_per_sec = 50
        bar = src.rate_bar
        self.assertIn("█", bar)
        self.assertEqual(len(bar), 20)

    def test_type_icon(self):
        src = LogSource("test", "systemd")
        self.assertEqual(src.type_icon, "🔧")


class TestAlertRule(unittest.TestCase):
    def test_status_icon(self):
        rule = AlertRule("test", threshold=10)
        self.assertEqual(rule.status_icon, "🟢")

    def test_disabled(self):
        rule = AlertRule("test", enabled=False)
        self.assertEqual(rule.status_icon, "⏸")

    def test_action_icon(self):
        rule = AlertRule("test", action="webhook")
        self.assertEqual(rule.action_icon, "🪝")


class TestLogPattern(unittest.TestCase):
    def test_frequency_bar(self):
        p = LogPattern("test", r"foo", count=50)
        bar = p.frequency_bar
        self.assertIn("█", bar)


class TestLogAggregator(unittest.TestCase):
    def setUp(self):
        self.agg = LogAggregator()

    def test_initial_state(self):
        self.assertGreater(len(self.agg._entries), 0)
        self.assertGreater(len(self.agg._sources), 0)
        self.assertGreater(len(self.agg._alert_rules), 0)

    def test_render(self):
        lines = self.agg.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("NYRQIS LOG AGGREGATOR" in l for l in lines))

    def test_filter_level(self):
        self.agg.set_filter_level(LogLevel.ERROR)
        for e in self.agg.filtered_entries:
            self.assertEqual(e.level, LogLevel.ERROR)

    def test_filter_source(self):
        self.agg.set_filter_source("syslog")
        for e in self.agg.filtered_entries:
            self.assertEqual(e.source, "syslog")

    def test_search(self):
        self.agg.set_search("nginx")
        entries = self.agg.filtered_entries
        for e in entries:
            self.assertIn("nginx", e.message.lower() + e.source.lower())

    def test_toggle_tail(self):
        self.assertFalse(self.agg._tail_mode)
        self.agg.toggle_tail()
        self.assertTrue(self.agg._tail_mode)

    def test_cycle_view(self):
        self.agg.cycle_view()
        self.assertNotEqual(self.agg._view_mode, "logs")

    def test_total_errors(self):
        self.assertGreaterEqual(self.agg.total_errors, 0)

    def test_entries_per_second(self):
        eps = self.agg.entries_per_second
        self.assertGreaterEqual(eps, 0)

    def test_level_distribution(self):
        dist = self.agg.level_distribution
        self.assertEqual(len(dist), 6)

    def test_source_distribution(self):
        dist = self.agg.source_distribution
        self.assertGreater(len(dist), 0)

    def test_select_entry(self):
        self.agg.select_entry(5)
        self.assertEqual(self.agg._selected_entry, 5)

    def test_render_sources(self):
        self.agg._view_mode = "sources"
        lines = self.agg.render()
        self.assertTrue(any("Log Sources" in l for l in lines))

    def test_render_alerts(self):
        self.agg._view_mode = "alerts"
        lines = self.agg.render()
        self.assertTrue(any("Alert Rules" in l for l in lines))

    def test_render_patterns(self):
        self.agg._view_mode = "patterns"
        lines = self.agg.render()
        self.assertTrue(any("Detected Patterns" in l for l in lines))

    def test_render_stats(self):
        self.agg._view_mode = "stats"
        lines = self.agg.render()
        self.assertTrue(any("Log Statistics" in l for l in lines))

    def test_clear_logs(self):
        self.agg.clear_logs()
        self.assertEqual(len(self.agg._entries), 0)

    def test_toggle_alert_rule(self):
        self.agg.toggle_alert_rule(0)
        self.assertFalse(self.agg._alert_rules[0].enabled)


# ─── Kubernetes Dashboard Tests ──────────────────────────────────────

class TestPodPhase(unittest.TestCase):
    def test_icon(self):
        self.assertEqual(PodPhase.RUNNING.icon, "🟢")


class TestContainer(unittest.TestCase):
    def test_cpu_bar(self):
        c = Container(name="test", cpu_limit_m=1000, cpu_used_m=500)
        bar = c.cpu_bar
        self.assertEqual(len(bar), 20)

    def test_mem_pct(self):
        c = Container(name="test", mem_limit_mb=1000, mem_used_mb=250)
        self.assertAlmostEqual(c.mem_pct, 25.0)


class TestPod(unittest.TestCase):
    def test_age_str(self):
        pod = Pod(name="test", created_at=time.time() - 7200)
        self.assertIn("h", pod.age_str)

    def test_cpu_bar(self):
        pod = Pod(name="test", containers=[
            Container(name="c1", cpu_limit_m=1000, cpu_used_m=500),
        ])
        self.assertEqual(len(pod.cpu_bar), 20)

    def test_ready_containers(self):
        pod = Pod(name="test", containers=[
            Container(name="c1", ready=True),
            Container(name="c2", ready=False),
        ])
        self.assertEqual(pod.ready_containers, 1)


class TestNode(unittest.TestCase):
    def test_status_icon(self):
        n = Node(name="test", status="Ready")
        self.assertEqual(n.status_icon, "🟢")

    def test_cpu_pct(self):
        n = Node(name="test", cpu_alloc_m=8000, cpu_used_m=4000)
        self.assertAlmostEqual(n.cpu_pct, 50.0)


class TestDeployment(unittest.TestCase):
    def test_status_icon(self):
        d = Deployment(name="test", replicas_desired=3, replicas_ready=3)
        self.assertEqual(d.status_icon, "✅")

    def test_replica_bar(self):
        d = Deployment(name="test", replicas_desired=3, replicas_ready=3, replicas_updated=2)
        bar = d.replica_bar
        self.assertIn("🟢", bar)
        self.assertIn("🔵", bar)

    def test_rollout_pct(self):
        d = Deployment(name="test", rollout_step=2, rollout_total=4)
        self.assertEqual(d.rollout_pct, 50)


class TestK8sDashboard(unittest.TestCase):
    def setUp(self):
        self.dash = K8sDashboard()

    def test_initial_state(self):
        self.assertGreater(len(self.dash._pods), 0)
        self.assertGreater(len(self.dash._nodes), 0)
        self.assertGreater(len(self.dash._deployments), 0)

    def test_render(self):
        lines = self.dash.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("KUBERNETES" in l for l in lines))

    def test_total_pods(self):
        self.assertEqual(self.dash.total_pods, len(self.dash._pods))

    def test_running_pods(self):
        self.assertGreater(self.dash.running_pods, 0)

    def test_filtered_pods(self):
        self.dash.set_namespace("database")
        pods = self.dash.filtered_pods
        for p in pods:
            self.assertEqual(p.namespace, "database")

    def test_render_pods(self):
        self.dash._view_mode = "pods"
        lines = self.dash.render()
        self.assertTrue(any("Pods" in l for l in lines))

    def test_render_nodes(self):
        self.dash._view_mode = "nodes"
        lines = self.dash.render()
        self.assertTrue(any("Nodes" in l for l in lines))

    def test_render_deployments(self):
        self.dash._view_mode = "deployments"
        lines = self.dash.render()
        self.assertTrue(any("Deployments" in l for l in lines))

    def test_render_services(self):
        self.dash._view_mode = "services"
        lines = self.dash.render()
        self.assertTrue(any("Services" in l for l in lines))

    def test_render_events(self):
        self.dash._view_mode = "events"
        lines = self.dash.render()
        self.assertTrue(any("Events" in l for l in lines))

    def test_namespaces(self):
        ns = self.dash.namespaces
        self.assertIn("database", ns)


# ─── Document Editor Tests ───────────────────────────────────────────

class TestTextRun(unittest.TestCase):
    def test_preview(self):
        run = TextRun(text="hello", bold=True)
        self.assertIn("**", run.preview)

    def test_format_tags(self):
        run = TextRun(text="test", bold=True, italic=True, code=True)
        tags = run.format_tags
        self.assertIn("B", tags)
        self.assertIn("I", tags)
        self.assertIn("<>", tags)

    def test_link(self):
        run = TextRun(text="click", link="https://example.com")
        self.assertIn("🔗", run.format_tags)


class TestBlock(unittest.TestCase):
    def test_text(self):
        b = Block(BlockType.PARAGRAPH, runs=[TextRun(text="hello"), TextRun(text="world")])
        self.assertEqual(b.text, "hello world")

    def test_word_count(self):
        b = Block(BlockType.PARAGRAPH, runs=[TextRun(text="one two three")])
        self.assertEqual(b.word_count, 3)

    def test_type_icon(self):
        b = Block(BlockType.HEADING, level=2)
        self.assertEqual(b.type_icon, "H2")

    def test_table_icon(self):
        b = Block(BlockType.TABLE)
        self.assertEqual(b.type_icon, "▦")


class TestDocument(unittest.TestCase):
    def test_word_count(self):
        doc = Document("Test")
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="hello world")]))
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="foo bar baz")]))
        self.assertEqual(doc.word_count, 5)

    def test_toc_entries(self):
        doc = Document("Test")
        doc.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Title")], level=1))
        doc.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Section")], level=2))
        toc = doc.toc_entries
        self.assertEqual(len(toc), 2)
        self.assertEqual(toc[0], (1, "Title"))

    def test_remove_block(self):
        doc = Document("Test")
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="a")]))
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="b")]))
        doc.remove_block(0)
        self.assertEqual(len(doc.blocks), 1)

    def test_move_block(self):
        doc = Document("Test")
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="a")]))
        doc.add_block(Block(BlockType.PARAGRAPH, runs=[TextRun(text="b")]))
        doc.move_block(0, 1)
        self.assertEqual(doc.blocks[0].text, "b")

    def test_to_markdown(self):
        doc = Document("Test")
        doc.add_block(Block(BlockType.HEADING, runs=[TextRun(text="Title")], level=1))
        md = doc.to_markdown()
        self.assertIn("# Title", md)


class TestDocEditor(unittest.TestCase):
    def setUp(self):
        self.editor = DocEditor()

    def test_initial_state(self):
        self.assertGreater(self.editor.document_count, 0)

    def test_render(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DOCUMENT EDITOR" in l for l in lines))

    def test_select_document(self):
        self.editor.select_document(1)
        self.assertEqual(self.editor._current_doc, 1)

    def test_set_view(self):
        self.editor.set_view("outline")
        self.assertEqual(self.editor._view_mode, "outline")

    def test_render_outline(self):
        self.editor._view_mode = "outline"
        lines = self.editor.render()
        self.assertTrue(any("Outline" in l for l in lines))

    def test_render_preview(self):
        self.editor._view_mode = "preview"
        lines = self.editor.render()
        self.assertTrue(any("Preview" in l for l in lines))

    def test_render_export(self):
        self.editor._view_mode = "export"
        lines = self.editor.render()
        self.assertTrue(any("Export" in l for l in lines))

    def test_current_doc(self):
        doc = self.editor.current_doc
        self.assertIsNotNone(doc)
        self.assertGreater(doc.block_count, 0)

    def test_cycle_zoom(self):
        self.editor.cycle_zoom()
        self.assertNotEqual(self.editor._zoom, 100)


if __name__ == "__main__":
    unittest.main()
