"""
Tests for Feed Reader, Todo Manager, and Help System.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.feed_reader import (
    FeedReader, Feed, Article, FeedCategory, Article as Art
)
from ui.todo_manager import (
    TodoManager, Task, Project, SubTask,
    Priority, TaskStatus, Recurrence
)
from ui.help_system import (
    HelpSystem, HelpArticle, ShortcutEntry, Tutorial, HelpCategory
)


# ─── Feed Reader Tests ───────────────────────────────────────────────────


class TestFeedReader(unittest.TestCase):

    def setUp(self):
        self.fr = FeedReader()

    def test_initial_state(self):
        self.assertGreater(len(self.fr.feeds), 0)
        self.assertGreater(self.fr.total_articles, 0)

    def test_feeds(self):
        feeds = self.fr.feeds
        self.assertGreater(len(feeds), 0)

    def test_add_feed(self):
        initial = len(self.fr.feeds)
        feed = self.fr.add_feed("Test Feed", "https://test.com/rss")
        self.assertEqual(len(self.fr.feeds), initial + 1)

    def test_remove_feed(self):
        feed = self.fr.feeds[0]
        result = self.fr.remove_feed(feed.feed_id)
        self.assertTrue(result)

    def test_get_feed(self):
        feed = self.fr.feeds[0]
        found = self.fr.get_feed(feed.feed_id)
        self.assertIsNotNone(found)

    def test_select_feed(self):
        feed = self.fr.feeds[0]
        self.fr.select_feed(feed.feed_id)
        self.assertEqual(self.fr.current_feed.feed_id, feed.feed_id)

    def test_select_all(self):
        self.fr.select_feed(self.fr.feeds[0].feed_id)
        self.fr.select_all_feeds()
        self.assertIsNone(self.fr.current_feed)

    def test_get_articles(self):
        articles = self.fr.get_articles()
        self.assertGreater(len(articles), 0)

    def test_mark_read(self):
        articles = self.fr.get_articles()
        unread = [a for a in articles if not a.is_read]
        if unread:
            self.fr.mark_read(unread[0].article_id)
            self.assertTrue(unread[0].is_read)

    def test_mark_unread(self):
        articles = self.fr.get_articles()
        read = [a for a in articles if a.is_read]
        if read:
            self.fr.mark_unread(read[0].article_id)
            self.assertFalse(read[0].is_read)

    def test_toggle_star(self):
        articles = self.fr.get_articles()
        if articles:
            was = articles[0].is_starred
            self.fr.toggle_star(articles[0].article_id)
            self.assertNotEqual(articles[0].is_starred, was)

    def test_mark_all_read(self):
        count = self.fr.mark_all_read()
        self.assertEqual(self.fr.total_unread, 0)

    def test_search(self):
        results = self.fr.set_search("Nyrqis")
        articles = self.fr.get_articles()
        self.assertGreater(len(articles), 0)

    def test_filter_starred(self):
        self.fr.toggle_filter_starred()
        self.assertTrue(self.fr._filter_starred)

    def test_open_article(self):
        articles = self.fr.get_articles()
        if articles:
            a = self.fr.open_article(articles[0].article_id)
            self.assertIsNotNone(a)
            self.assertEqual(self.fr.view_mode, "article")

    def test_close_article(self):
        self.fr.open_article()
        self.fr.close_article()
        self.assertEqual(self.fr.view_mode, "list")

    def test_selection(self):
        self.fr.select_up()
        self.fr.select_down()

    def test_render_list(self):
        lines = self.fr.render_article_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_feed_list(self):
        lines = self.fr.render_feed_list()
        self.assertIsInstance(lines, list)

    def test_render_article(self):
        self.fr.open_article()
        lines = self.fr.render_article()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.fr.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_list(self):
        self.fr.handle_key("ArrowDown")
        self.fr.handle_key("ArrowUp")
        self.fr.handle_key("Enter")
        self.assertEqual(self.fr.view_mode, "article")

    def test_handle_key_article(self):
        self.fr.open_article()
        self.fr.handle_key("Escape")
        self.assertEqual(self.fr.view_mode, "list")


class TestArticle(unittest.TestCase):

    def test_display_title_unread(self):
        a = Art(title="Test", url="u", is_read=False)
        self.assertIn("●", a.display_title)

    def test_display_title_read(self):
        a = Art(title="Test", url="u", is_read=True)
        self.assertIn("  ", a.display_title)

    def test_starred(self):
        a = Art(title="Test", url="u", is_starred=True)
        self.assertIn("⭐", a.display_title)

    def test_time_ago(self):
        a = Art(title="Test", url="u", published=time.time() - 3600)
        self.assertIn("h ago", a.time_ago)

    def test_summary_preview(self):
        a = Art(title="Test", url="u", summary="This is a test summary with some content")
        self.assertIn("test", a.summary_preview)


class TestFeed(unittest.TestCase):

    def test_badge(self):
        f = Feed(title="Test", url="u", unread_count=5)
        self.assertIn("5", f.badge)

    def test_badge_zero(self):
        f = Feed(title="Test", url="u", unread_count=0)
        self.assertEqual(f.badge, "")


# ─── Todo Manager Tests ──────────────────────────────────────────────────


class TestTodoManager(unittest.TestCase):

    def setUp(self):
        self.tm = TodoManager()

    def test_initial_state(self):
        self.assertGreater(len(self.tm.get_tasks()), 0)
        self.assertGreater(len(self.tm.projects), 0)

    def test_create_task(self):
        initial = len(self.tm.get_tasks())
        task = self.tm.create_task("New Task")
        self.assertEqual(task.title, "New Task")

    def test_update_task(self):
        task = self.tm.create_task("Update Me")
        result = self.tm.update_task(task.task_id, title="Updated")
        self.assertTrue(result)
        self.assertEqual(task.title, "Updated")

    def test_delete_task(self):
        task = self.tm.create_task("Delete Me")
        result = self.tm.delete_task(task.task_id)
        self.assertTrue(result)
        self.assertIsNone(self.tm.get_task(task.task_id))

    def test_complete_task(self):
        task = self.tm.create_task("Complete Me")
        result = self.tm.complete_task(task.task_id)
        self.assertTrue(result)
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_add_subtask(self):
        task = self.tm.create_task("With Subtasks")
        st = self.tm.add_subtask(task.task_id, "Subtask 1")
        self.assertIsNotNone(st)
        self.assertEqual(len(task.subtasks), 1)

    def test_toggle_subtask(self):
        task = self.tm.create_task("Toggle Sub")
        st = self.tm.add_subtask(task.task_id, "Toggle Me")
        self.tm.toggle_subtask(task.task_id, st.subtask_id)
        self.assertTrue(st.completed)

    def test_get_tasks(self):
        tasks = self.tm.get_tasks()
        self.assertIsInstance(tasks, list)

    def test_filter_priority(self):
        self.tm._filter_priority = Priority.P1
        tasks = self.tm.get_tasks()
        for t in tasks:
            self.assertEqual(t.priority, Priority.P1)
        self.tm._filter_priority = None

    def test_select_project(self):
        proj = self.tm.projects[0]
        self.tm.select_project(proj.project_id)
        self.assertEqual(self.tm.current_project.project_id, proj.project_id)

    def test_select_all_projects(self):
        self.tm.select_project(self.tm.projects[0].project_id)
        self.tm.select_all_projects()
        self.assertIsNone(self.tm.current_project)

    def test_get_stats(self):
        stats = self.tm.get_stats()
        self.assertIn("total", stats)
        self.assertIn("completed", stats)
        self.assertIn("overdue", stats)

    def test_cycle_view(self):
        self.tm.cycle_view()
        self.assertEqual(self.tm.view_mode, "board")
        self.tm.cycle_view()
        self.assertEqual(self.tm.view_mode, "stats")
        self.tm.cycle_view()
        self.assertEqual(self.tm.view_mode, "list")

    def test_selection(self):
        self.tm.select_up()
        self.tm.select_down()

    def test_open_selected(self):
        tasks = self.tm.get_tasks()
        if tasks:
            task = self.tm.open_selected()
            self.assertIsNotNone(task)
            self.assertEqual(self.tm.view_mode, "detail")

    def test_render_list(self):
        lines = self.tm.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_board(self):
        lines = self.tm.render_board()
        self.assertIsInstance(lines, list)

    def test_render_detail(self):
        self.tm.open_selected()
        lines = self.tm.render_detail()
        self.assertIsInstance(lines, list)

    def test_render_stats(self):
        lines = self.tm.render_stats()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.tm.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_list(self):
        self.tm.handle_key("ArrowDown")
        self.tm.handle_key("ArrowUp")
        self.tm.handle_key("Enter")
        self.assertEqual(self.tm.view_mode, "detail")

    def test_handle_key_detail(self):
        self.tm.open_selected()
        self.tm.handle_key("Escape")
        self.assertEqual(self.tm.view_mode, "list")

    def test_create_project(self):
        initial = len(self.tm.projects)
        proj = self.tm.create_project("Test Project")
        self.assertEqual(len(self.tm.projects), initial + 1)


class TestTask(unittest.TestCase):

    def test_is_overdue(self):
        t = Task(title="T", due_date=time.time() - 3600, status=TaskStatus.TODO)
        self.assertTrue(t.is_overdue)

    def test_not_overdue_done(self):
        t = Task(title="T", due_date=time.time() - 3600, status=TaskStatus.DONE)
        self.assertFalse(t.is_overdue)

    def test_due_str_overdue(self):
        t = Task(title="T", due_date=time.time() - 86400 * 2, status=TaskStatus.TODO)
        self.assertIn("overdue", t.due_str)

    def test_due_str_today(self):
        t = Task(title="T", due_date=time.time() + 3600, status=TaskStatus.TODO)
        self.assertIn("today", t.due_str)

    def test_priority_icon(self):
        t = Task(title="T", priority=Priority.P1)
        self.assertEqual(t.priority_icon, "🔴")

    def test_status_icon(self):
        t = Task(title="T", status=TaskStatus.DONE)
        self.assertEqual(t.status_icon, "☑")

    def test_completion_pct(self):
        t = Task(title="T")
        t.subtasks = [SubTask("A", completed=True), SubTask("B", completed=False)]
        self.assertEqual(t.completion_pct, 50.0)

    def test_subtask_summary(self):
        t = Task(title="T")
        t.subtasks = [SubTask("A", completed=True), SubTask("B", completed=False)]
        self.assertEqual(t.subtask_summary, "1/2")


class TestSubTask(unittest.TestCase):

    def test_subtask_id(self):
        st = SubTask(title="Test")
        self.assertIsNotNone(st.subtask_id)
        self.assertEqual(len(st.subtask_id), 6)


class TestProject(unittest.TestCase):

    def test_project_id(self):
        p = Project(name="Test")
        self.assertIsNotNone(p.project_id)


# ─── Help System Tests ───────────────────────────────────────────────────


class TestHelpSystem(unittest.TestCase):

    def setUp(self):
        self.hs = HelpSystem()

    def test_initial_state(self):
        self.assertEqual(self.hs.view_mode, "home")

    def test_articles(self):
        articles = self.hs.get_articles()
        self.assertGreater(len(articles), 0)

    def test_articles_by_category(self):
        articles = self.hs.get_articles(HelpCategory.GETTING_STARTED)
        self.assertGreater(len(articles), 0)

    def test_open_article(self):
        articles = self.hs.get_articles()
        a = self.hs.open_article(articles[0].article_id)
        self.assertIsNotNone(a)
        self.assertEqual(self.hs.view_mode, "article")

    def test_close_article(self):
        self.hs.open_article(self.hs.get_articles()[0].article_id)
        self.hs.close_article()
        self.assertEqual(self.hs.view_mode, "home")

    def test_search(self):
        self.hs.set_search("terminal")
        articles = self.hs.get_articles()
        self.assertGreater(len(articles), 0)

    def test_shortcuts(self):
        shortcuts = self.hs.shortcuts
        self.assertGreater(len(shortcuts), 0)

    def test_shortcuts_by_category(self):
        cats = self.hs.get_shortcuts_by_category()
        self.assertGreater(len(cats), 0)
        self.assertIn("System", cats)

    def test_tutorials(self):
        tutorials = self.hs.tutorials
        self.assertGreater(len(tutorials), 0)

    def test_start_tutorial(self):
        t = self.hs.start_tutorial(0)
        self.assertIsNotNone(t)
        self.assertEqual(self.hs.view_mode, "tutorial")

    def test_tutorial_next_step(self):
        self.hs.start_tutorial(0)
        result = self.hs.next_tutorial_step()
        self.assertTrue(result)
        self.assertEqual(self.hs._current_tutorial.current_step, 1)

    def test_tutorial_prev_step(self):
        self.hs.start_tutorial(0)
        self.hs.next_tutorial_step()
        result = self.hs.prev_tutorial_step()
        self.assertTrue(result)
        self.assertEqual(self.hs._current_tutorial.current_step, 0)

    def test_tutorial_complete(self):
        self.hs.start_tutorial(0)
        for _ in range(20):
            self.hs.next_tutorial_step()
        self.assertTrue(self.hs._current_tutorial.completed)

    def test_close_tutorial(self):
        self.hs.start_tutorial(0)
        self.hs.close_tutorial()
        self.assertEqual(self.hs.view_mode, "home")

    def test_scroll(self):
        self.hs.scroll(5)
        self.hs.scroll(-3)

    def test_render_home(self):
        lines = self.hs.render_home()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_article_list(self):
        lines = self.hs.render_article_list()
        self.assertIsInstance(lines, list)

    def test_render_article(self):
        self.hs.open_article(self.hs.get_articles()[0].article_id)
        lines = self.hs.render_article()
        self.assertIsInstance(lines, list)

    def test_render_shortcuts(self):
        lines = self.hs.render_shortcuts()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_tutorial(self):
        self.hs.start_tutorial(0)
        lines = self.hs.render_tutorial()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.hs.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_home(self):
        self.hs.handle_key("ArrowDown")
        self.hs.handle_key("ArrowUp")
        self.hs.handle_key("Enter")
        self.assertEqual(self.hs.view_mode, "article_list")

    def test_handle_key_article_list(self):
        self.hs._view_mode = "article_list"
        self.hs.handle_key("Escape")
        self.assertEqual(self.hs.view_mode, "home")

    def test_handle_key_article(self):
        self.hs.open_article(self.hs.get_articles()[0].article_id)
        self.hs.handle_key("Escape")
        self.assertEqual(self.hs.view_mode, "home")

    def test_handle_key_shortcuts(self):
        self.hs._view_mode = "shortcuts"
        self.hs.handle_key("Escape")
        self.assertEqual(self.hs.view_mode, "home")

    def test_handle_key_tutorial(self):
        self.hs.start_tutorial(0)
        self.hs.handle_key("Escape")
        self.assertEqual(self.hs.view_mode, "home")


class TestHelpArticle(unittest.TestCase):

    def test_article_id(self):
        a = HelpArticle("My Article", HelpCategory.FAQ, "Content")
        self.assertEqual(a.article_id, "my_article")


class TestShortcutEntry(unittest.TestCase):

    def test_shortcut(self):
        s = ShortcutEntry("Close Window", "Ctrl+Q", "Window")
        self.assertEqual(s.action, "Close Window")
        self.assertEqual(s.keys, "Ctrl+Q")


class TestTutorial(unittest.TestCase):

    def test_progress(self):
        t = Tutorial("Test", ["Step 1", "Step 2", "Step 3"])
        self.assertEqual(t.progress, 0.0)

    def test_next_step(self):
        t = Tutorial("Test", ["Step 1", "Step 2"])
        result = t.next_step()
        self.assertTrue(result)
        self.assertEqual(t.current_step, 1)

    def test_prev_step(self):
        t = Tutorial("Test", ["Step 1", "Step 2"])
        t.next_step()
        result = t.prev_step()
        self.assertTrue(result)
        self.assertEqual(t.current_step, 0)

    def test_prev_at_start(self):
        t = Tutorial("Test", ["Step 1"])
        result = t.prev_step()
        self.assertFalse(result)

    def test_complete(self):
        t = Tutorial("Test", ["Step 1"])
        t.next_step()
        self.assertTrue(t.completed)

    def test_progress_str(self):
        t = Tutorial("Test", ["Step 1", "Step 2"])
        t.next_step()
        self.assertEqual(t.progress_str, "1/2")


if __name__ == "__main__":
    unittest.main()
