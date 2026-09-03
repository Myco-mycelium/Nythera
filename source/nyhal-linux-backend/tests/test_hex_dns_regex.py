"""Tests for hex editor, DNS lookup, and regex tester."""
import unittest
import time

from ui.hex_editor import (
    HexEditor, HexBuffer, Bookmark, Annotation, ByteStats, DiffResult, ViewMode,
)
from ui.dns_lookup import (
    DNSLookup, DNSQuery, DNSRecord, DNSPropagationNode, DNSBenchmarkResult,
)
from ui.regex_tester import (
    RegexTester, RegexTest, RegexMatch, RegexPattern, RegexCheatsheetEntry, RegexFlag,
)


# ─── Hex Editor Tests ────────────────────────────────────────────────

class TestHexBuffer(unittest.TestCase):
    def test_size(self):
        buf = HexBuffer(b"Hello World")
        self.assertEqual(buf.size, 11)

    def test_size_str(self):
        buf = HexBuffer(b"Hello World")
        self.assertIn("B", buf.size_str)

    def test_get_byte(self):
        buf = HexBuffer(b"ABC")
        self.assertEqual(buf.get_byte(0), 0x41)

    def test_get_line(self):
        buf = HexBuffer(b"ABCDEF")
        raw, ascii_str = buf.get_line(0, 3)
        self.assertEqual(len(raw), 3)
        self.assertEqual(ascii_str, "ABC")


class TestByteStats(unittest.TestCase):
    def test_printable_pct(self):
        s = ByteStats(total_bytes=100, printable=75)
        self.assertAlmostEqual(s.printable_pct, 75.0)

    def test_top_bytes(self):
        s = ByteStats(frequency={"00": 50, "FF": 30, "41": 20})
        top = s.top_bytes
        self.assertEqual(top[0][0], "00")


class TestDiffResult(unittest.TestCase):
    def test_hex(self):
        d = DiffResult(old_byte=0x41, new_byte=0xFF, old_hex="41", new_hex="FF")
        self.assertEqual(d.old_hex, "41")
        self.assertEqual(d.new_hex, "FF")


class TestHexEditor(unittest.TestCase):
    def setUp(self):
        self.editor = HexEditor()

    def test_initial_state(self):
        self.assertGreater(len(self.editor._buffers), 0)
        self.assertGreater(len(self.editor._bookmarks), 0)

    def test_render_hex(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("HEX EDITOR" in l for l in lines))

    def test_render_stats(self):
        self.editor._view_mode = ViewMode.STATS
        lines = self.editor.render()
        self.assertTrue(any("Statistics" in l for l in lines))

    def test_render_diff(self):
        self.editor._view_mode = ViewMode.DIFF
        lines = self.editor.render()
        self.assertTrue(any("Diff" in l for l in lines))

    def test_jump_to(self):
        self.editor.jump_to(100)
        self.assertEqual(self.editor._cursor_offset, 100)

    def test_select_bookmark(self):
        self.editor.select_bookmark(0)
        self.assertEqual(self.editor._cursor_offset, self.editor._bookmarks[0].offset)

    def test_total_lines(self):
        self.assertGreater(self.editor.total_lines, 0)


# ─── DNS Lookup Tests ────────────────────────────────────────────────

class TestDNSRecord(unittest.TestCase):
    def test_ttl_str(self):
        r = DNSRecord(ttl=3600)
        self.assertEqual(r.ttl_str, "1h")

    def test_ttl_bar(self):
        r = DNSRecord(ttl=3600)
        bar = r.ttl_bar
        self.assertEqual(len(bar), 20)

    def test_type_icon(self):
        r = DNSRecord(record_type="MX")
        self.assertEqual(r.type_icon, "📧")


class TestDNSQuery(unittest.TestCase):
    def test_time_str(self):
        q = DNSQuery(timestamp=time.time())
        self.assertIn(":", q.time_str)

    def test_status_icon(self):
        q = DNSQuery(status="NOERROR")
        self.assertEqual(q.status_icon, "✅")


class TestDNSBenchmarkResult(unittest.TestCase):
    def test_speed_bar(self):
        b = DNSBenchmarkResult(avg_ms=50)
        bar = b.speed_bar
        self.assertEqual(len(bar), 20)

    def test_success_rate(self):
        b = DNSBenchmarkResult(queries=100, failures=5)
        self.assertAlmostEqual(b.success_rate, 95.0)


class TestDNSLookup(unittest.TestCase):
    def setUp(self):
        self.dns = DNSLookup()

    def test_initial_state(self):
        self.assertGreater(len(self.dns._queries), 0)
        self.assertGreater(len(self.dns._propagation), 0)

    def test_render_lookup(self):
        lines = self.dns.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DNS LOOKUP" in l for l in lines))

    def test_render_history(self):
        self.dns.set_view("history")
        lines = self.dns.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_propagation(self):
        self.dns.set_view("propagation")
        lines = self.dns.render()
        self.assertTrue(any("Propagation" in l for l in lines))

    def test_render_benchmark(self):
        self.dns.set_view("benchmark")
        lines = self.dns.render()
        self.assertTrue(any("Benchmark" in l for l in lines))

    def test_selected_query(self):
        self.dns.select_query(0)
        q = self.dns.selected_query
        self.assertIsNotNone(q)


# ─── Regex Tester Tests ──────────────────────────────────────────────

class TestRegexMatch(unittest.TestCase):
    def test_span_str(self):
        m = RegexMatch(start=5, end=10)
        self.assertEqual(m.span_str, "[5:10]")

    def test_groups_str(self):
        m = RegexMatch(groups=["a", "b"])
        self.assertIn("a", m.groups_str)


class TestRegexPattern(unittest.TestCase):
    def test_preview(self):
        p = RegexPattern(pattern=r"\d+", flags=["g", "i"])
        self.assertIn("\\d+", p.preview)


class TestRegexCheatsheetEntry(unittest.TestCase):
    def test_display(self):
        e = RegexCheatsheetEntry(token="\\d", description="Digit", example="\\d+ matches 123")
        self.assertEqual(e.token, "\\d")


class TestRegexTester(unittest.TestCase):
    def setUp(self):
        self.tester = RegexTester()

    def test_initial_state(self):
        self.assertGreater(len(self.tester._history), 0)
        self.assertGreater(len(self.tester._patterns), 0)
        self.assertGreater(len(self.tester._cheatsheet), 0)

    def test_render_test(self):
        lines = self.tester.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("REGEX TESTER" in l for l in lines))

    def test_render_history(self):
        self.tester.set_view("history")
        lines = self.tester.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_library(self):
        self.tester.set_view("library")
        lines = self.tester.render()
        self.assertTrue(any("Library" in l for l in lines))

    def test_render_cheatsheet(self):
        self.tester.set_view("cheatsheet")
        lines = self.tester.render()
        self.assertTrue(any("Cheatsheet" in l for l in lines))

    def test_render_replace(self):
        self.tester.set_view("replace")
        lines = self.tester.render()
        self.assertTrue(any("Replace" in l for l in lines))

    def test_toggle_flag(self):
        self.tester.toggle_flag("i")
        self.assertIn("i", self.tester._current_flags)
        self.tester.toggle_flag("i")
        self.assertNotIn("i", self.tester._current_flags)

    def test_active_flags(self):
        self.assertIn("g", self.tester.active_flags)


if __name__ == "__main__":
    unittest.main()
