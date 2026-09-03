"""Tests for Disk Analyzer, Markdown Wiki, and Cloud Storage."""
import unittest
import time
from ui.disk_analyzer import (
    DiskAnalyzer, FileEntry, CleanupSuggestion, DiskPartition, DuplicateGroup,
    FileType, CleanupCategory,
)
from ui.markdown_wiki import (
    MarkdownWiki, WikiPage, TOCEntry, WikiLink, SearchMatch, WikiStats,
    PageStatus, LinkType,
)
from ui.cloud_storage import (
    CloudStorage, CloudFile, Bucket, SyncTask, BandwidthSample,
    CloudProvider, SyncStatus, StorageClass, Permission,
)


# ==================== DiskAnalyzer Tests ====================

class TestFileEntry(unittest.TestCase):
    def test_size_human(self):
        f = FileEntry("t", "/t", 1500)
        self.assertEqual(f.size_human, "1.5 KB")

    def test_size_large(self):
        f = FileEntry("t", "/t", 2 * 1024**3)
        self.assertIn("GB", f.size_human)

    def test_is_duplicate(self):
        f = FileEntry("t", "/t", duplicate_of="/other")
        self.assertTrue(f.is_duplicate)

    def test_type_icon(self):
        f = FileEntry("t", "/t", file_type=FileType.DIRECTORY)
        self.assertEqual(f.type_icon, "📁")


class TestCleanupSuggestion(unittest.TestCase):
    def test_size_human(self):
        s = CleanupSuggestion(CleanupCategory.TEMP_FILES, "test", 500 * 1024**2)
        self.assertIn("MB", s.size_human)

    def test_category_icon(self):
        s = CleanupSuggestion(CleanupCategory.DUPLICATES, "test")
        self.assertEqual(s.category_icon, "👯")


class TestDiskPartition(unittest.TestCase):
    def test_usage_percent(self):
        p = DiskPartition("/", "/dev/sda1", total_bytes=100, used_bytes=75)
        self.assertAlmostEqual(p.usage_percent, 75.0)

    def test_usage_bar(self):
        p = DiskPartition("/", "/dev/sda1", total_bytes=100, used_bytes=50)
        bar = p.usage_bar
        self.assertIn("█", bar)


class TestDuplicateGroup(unittest.TestCase):
    def test_wasted(self):
        d = DuplicateGroup("hash", [
            FileEntry("a", "/a", 1024),
            FileEntry("b", "/b", 1024),
        ])
        self.assertEqual(d.wasted_bytes, 1024)

    def test_wasted_human(self):
        d = DuplicateGroup("hash", [
            FileEntry("a", "/a", 5 * 1024**2),
            FileEntry("b", "/b", 5 * 1024**2),
        ])
        self.assertIn("MB", d.wasted_human)


class TestDiskAnalyzer(unittest.TestCase):
    def setUp(self):
        self.da = DiskAnalyzer()

    def test_initial_state(self):
        self.assertIsNotNone(self.da._root)
        self.assertGreater(len(self.da._partitions), 0)

    def test_partitions(self):
        self.assertGreater(len(self.da._partitions), 0)

    def test_duplicates(self):
        self.assertGreater(len(self.da._duplicates), 0)

    def test_suggestions(self):
        self.assertGreater(len(self.da._suggestions), 0)

    def test_total_cleanup(self):
        self.assertIsInstance(self.da.total_cleanup_savings, str)

    def test_total_duplicates_wasted(self):
        self.assertIsInstance(self.da.total_duplicates_wasted, str)

    def test_render(self):
        lines = self.da.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("DISK USAGE ANALYZER" in l for l in lines))


# ==================== MarkdownWiki Tests ====================

class TestWikiLink(unittest.TestCase):
    def test_create(self):
        wl = WikiLink("page", "text", LinkType.INTERNAL)
        self.assertEqual(wl.icon, "📄")

    def test_external(self):
        wl = WikiLink("http://example.com", "link", LinkType.EXTERNAL)
        self.assertEqual(wl.icon, "🌐")


class TestTOCEntry(unittest.TestCase):
    def test_indent(self):
        e = TOCEntry(1, "Title")
        self.assertEqual(e.indent, "")

    def test_bullet(self):
        self.assertEqual(TOCEntry(1, "T").bullet, "•")
        self.assertEqual(TOCEntry(2, "T").bullet, "◦")


class TestWikiPage(unittest.TestCase):
    def test_status_icon(self):
        p = WikiPage(status=PageStatus.PUBLISHED)
        self.assertEqual(p.status_icon, "✅")

    def test_extract_toc(self):
        p = WikiPage(raw_markdown="# Title\n## Subtitle\n### Detail")
        p.extract_toc()
        self.assertEqual(len(p.toc), 3)

    def test_extract_links(self):
        p = WikiPage(raw_markdown="See [page](other-page) and [web](http://example.com)")
        p.extract_links()
        self.assertEqual(len(p.links), 2)
        self.assertEqual(p.links[0].link_type, LinkType.INTERNAL)
        self.assertEqual(p.links[1].link_type, LinkType.EXTERNAL)

    def test_extract_words(self):
        p = WikiPage(raw_markdown="# Title\n\nThis is a test with some words in it.")
        p.extract_words()
        self.assertGreater(p.word_count, 0)

    def test_preview(self):
        p = WikiPage(raw_markdown="# Title\nSome content here")
        self.assertIn("Title", p.preview)


class TestSearchMatch(unittest.TestCase):
    def test_create(self):
        m = SearchMatch("Page", 5, "some line")
        self.assertEqual(m.line_num, 5)


class TestMarkdownWiki(unittest.TestCase):
    def setUp(self):
        self.wiki = MarkdownWiki()

    def test_initial_state(self):
        self.assertGreater(self.wiki.total_pages, 0)
        self.assertGreater(self.wiki.published_pages, 0)

    def test_stats(self):
        s = self.wiki.stats
        self.assertGreater(s.total_pages, 0)
        self.assertGreater(s.total_words, 0)

    def test_select_page(self):
        self.wiki.select_page(2)
        self.assertEqual(self.wiki._selected_page, 2)

    def test_search(self):
        self.wiki.search("compositor")
        self.assertGreater(len(self.wiki._search_results), 0)

    def test_selected_page(self):
        p = self.wiki.selected_page
        self.assertIsNotNone(p)
        self.assertGreater(len(p.toc), 0)

    def test_render(self):
        lines = self.wiki.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("MARKDOWN WIKI" in l for l in lines))


# ==================== CloudStorage Tests ====================

class TestCloudFile(unittest.TestCase):
    def test_size_human(self):
        f = CloudFile("test", size_bytes=1500)
        self.assertEqual(f.size_human, "1.5 KB")

    def test_icon(self):
        f = CloudFile("test.py")
        self.assertEqual(f.icon, "🐍")

    def test_sync_icon(self):
        f = CloudFile("test", synced=True)
        self.assertEqual(f.sync_icon, "✅")


class TestBucket(unittest.TestCase):
    def test_size_human(self):
        b = Bucket("test", total_bytes=5 * 1024**3)
        self.assertIn("GB", b.size_human)

    def test_provider_icon(self):
        b = Bucket("test", provider=CloudProvider.AWS_S3)
        self.assertEqual(b.provider_icon, "🟠")


class TestSyncTask(unittest.TestCase):
    def test_progress_bar(self):
        s = SyncTask("test", progress=0.5)
        bar = s.progress_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_status_icon(self):
        s = SyncTask("test", status=SyncStatus.SYNCING)
        self.assertEqual(s.status_icon, "🔄")


class TestBandwidthSample(unittest.TestCase):
    def test_total(self):
        b = BandwidthSample(upload_bytes=100, download_bytes=200)
        self.assertEqual(b.total, 300)


class TestCloudStorage(unittest.TestCase):
    def setUp(self):
        self.cs = CloudStorage()

    def test_initial_state(self):
        self.assertGreater(len(self.cs._buckets), 0)
        self.assertGreater(len(self.cs._files), 0)

    def test_total_storage(self):
        self.assertIsInstance(self.cs.total_storage, str)

    def test_total_objects(self):
        self.assertGreater(self.cs.total_objects, 0)

    def test_active_syncs(self):
        self.assertGreaterEqual(self.cs.active_syncs, 0)

    def test_select_bucket(self):
        self.cs.select_bucket(2)
        self.assertEqual(self.cs._selected_bucket, 2)

    def test_render(self):
        lines = self.cs.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("CLOUD STORAGE" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
