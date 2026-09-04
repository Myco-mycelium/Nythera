import unittest
import time
import hashlib


class TestLanguageLearning(unittest.TestCase):
    def setUp(self):
        from ui.language_learning import LanguageLearningApp, Flashcard, Deck, SpacedRepetitionLevel
        self.app = LanguageLearningApp()
        self.Flashcard = Flashcard
        self.Deck = Deck
        self.SRL = SpacedRepetitionLevel

    def test_initial_state(self):
        self.assertGreater(len(self.app.decks), 0)
        self.assertIsNotNone(self.app.current_deck)

    def test_deck_stats(self):
        deck = self.app.decks[0]
        self.assertEqual(deck.name, "Spanish Verbs")
        self.assertGreater(deck.total_cards, 0)
        self.assertGreaterEqual(deck.new_cards, 0)
        self.assertGreaterEqual(deck.learning_cards, 0)

    def test_due_cards(self):
        due = self.app.get_all_due_cards()
        self.assertIsInstance(due, list)

    def test_start_session(self):
        session = self.app.start_session("Spanish Verbs")
        self.assertIsNotNone(session)
        self.assertEqual(session.deck_name, "Spanish Verbs")

    def test_next_card(self):
        self.app.start_session("Spanish Verbs")
        card = self.app.next_card()
        self.assertIsNotNone(card)
        self.assertIsInstance(card.front, str)
        self.assertIsInstance(card.back, str)

    def test_show_answer(self):
        self.app.start_session("Spanish Verbs")
        self.app.next_card()
        answer = self.app.show_answer()
        self.assertEqual(answer, self.app.current_card.back)
        self.assertTrue(self.app.showing_answer)

    def test_grade_card(self):
        self.app.start_session("Spanish Verbs")
        self.app.next_card()
        result = self.app.grade_card(4)
        self.assertTrue(result)
        self.assertGreater(self.app.current_session.cards_studied, 0)

    def test_grade_low_quality(self):
        self.app.start_session("Spanish Verbs")
        card = self.app.next_card()
        old_reps = card.repetitions
        self.app.grade_card(1)
        self.assertEqual(card.repetitions, 0)

    def test_end_session(self):
        self.app.start_session("Spanish Verbs")
        self.app.next_card()
        self.app.grade_card(4)
        session = self.app.end_session()
        self.assertIsNotNone(session)
        self.assertEqual(len(self.app.sessions), 1)

    def test_language_stats(self):
        stats = self.app.get_language_stats()
        self.assertIn("es", stats)
        self.assertIn("total", stats["es"])

    def test_search_cards(self):
        results = self.app.search_cards("hola")
        self.assertIsInstance(results, list)

    def test_add_card(self):
        initial = self.app.decks[0].total_cards
        result = self.app.add_card("Spanish Verbs", "gato", "cat")
        self.assertTrue(result)
        self.assertEqual(self.app.decks[0].total_cards, initial + 1)

    def test_study_stats(self):
        stats = self.app.get_study_stats()
        self.assertIn("total_cards", stats)
        self.assertIn("total_due", stats)

    def test_flashcard_accuracy(self):
        card = self.Flashcard(front="test", back="prueba")
        self.assertEqual(card.accuracy, 0.0)
        card.total_reviews = 10
        card.correct_reviews = 8
        self.assertAlmostEqual(card.accuracy, 0.8)

    def test_flashcard_level_icon(self):
        card = self.Flashcard(front="a", back="b", level=self.SRL.NEW)
        self.assertEqual(card.level_icon, "🆕")
        card.level = self.SRL.MATURE
        self.assertEqual(card.level_icon, "🌳")

    def test_is_due(self):
        card = self.Flashcard(front="a", back="b")
        card.next_review = time.time() - 1
        self.assertTrue(card.is_due)
        card.next_review = time.time() + 3600
        self.assertFalse(card.is_due)

    def test_multiple_decks(self):
        deck_names = [d.name for d in self.app.decks]
        self.assertIn("French Basics", deck_names)
        self.assertIn("Japanese Basics", deck_names)


class TestFileIntegrity(unittest.TestCase):
    def setUp(self):
        from ui.file_integrity import FileIntegrityChecker, HashAlgorithm
        self.checker = FileIntegrityChecker()
        self.HA = HashAlgorithm

    def test_initial_state(self):
        self.assertGreater(len(self.checker.records), 0)
        self.assertGreater(len(self.checker.rules), 0)

    def test_compute_hash(self):
        h = self.checker.compute_hash(b"test data", self.HA.SHA256)
        self.assertEqual(len(h), 64)

    def test_verify_ok(self):
        path = "/etc/hosts"
        data = b"hosts"
        result = self.checker.verify_file(path, data)
        self.assertEqual(result["status"], "ok")

    def test_verify_mismatch(self):
        path = "/etc/hosts"
        data = b"tampered"
        result = self.checker.verify_file(path, data)
        self.assertEqual(result["status"], "mismatch")
        self.assertGreater(len(self.checker.alerts), 0)

    def test_verify_unknown_file(self):
        result = self.checker.verify_file("/nonexistent", b"data")
        self.assertEqual(result["status"], "unknown")

    def test_run_scan(self):
        result = self.checker.run_scan()
        self.assertGreater(result.files_scanned, 0)
        self.assertEqual(result.status, "⚠️ Changes Detected")

    def test_run_scan_by_rule(self):
        result = self.checker.run_scan("System Config")
        self.assertGreater(result.files_scanned, 0)

    def test_acknowledge_alert(self):
        initial_unack = sum(1 for a in self.checker.alerts if not a.acknowledged)
        self.checker.acknowledge_alert(0)
        new_unack = sum(1 for a in self.checker.alerts if not a.acknowledged)
        self.assertLess(new_unack, initial_unack)

    def test_add_rule(self):
        rule = self.checker.add_monitor_rule("Custom", ["/data"])
        self.assertEqual(rule.name, "Custom")
        self.assertIn(rule, self.checker.rules)

    def test_summary(self):
        summary = self.checker.get_summary()
        self.assertIn("total_files", summary)
        self.assertIn("unack_alerts", summary)

    def test_alert_severity_icon(self):
        from ui.file_integrity import Alert, AlertSeverity
        alert = Alert(timestamp=time.time(), file_path="/test", message="test",
                       severity=AlertSeverity.CRITICAL)
        self.assertEqual(alert.severity_icon, "🚨")

    def test_file_status_icon(self):
        from ui.file_integrity import FileRecord, FileStatus
        rec = FileRecord(path="/test", status=FileStatus.OK)
        self.assertEqual(rec.status_icon, "✅")
        rec.status = FileStatus.MODIFIED
        self.assertEqual(rec.status_icon, "⚠️")


class TestDatabaseClient(unittest.TestCase):
    def setUp(self):
        from ui.db_client import DatabaseClient, QueryStatus, ExportFormat
        self.client = DatabaseClient()
        self.QS = QueryStatus
        self.EF = ExportFormat

    def test_initial_state(self):
        self.assertGreater(len(self.client.connections), 0)
        self.assertGreater(len(self.client.tables), 0)
        self.assertIsNotNone(self.client.current_connection)

    def test_execute_select(self):
        result = self.client.execute_query("SELECT * FROM users;")
        self.assertEqual(result.status, self.QS.SUCCESS)
        self.assertTrue(result.is_select)
        self.assertGreater(result.row_count, 0)

    def test_execute_insert(self):
        result = self.client.execute_query("INSERT INTO users (username) VALUES ('test');")
        self.assertEqual(result.status, self.QS.SUCCESS)
        self.assertGreater(result.affected_rows, 0)

    def test_execute_invalid(self):
        result = self.client.execute_query("INVALID QUERY;")
        self.assertEqual(result.status, self.QS.SUCCESS)

    def test_get_schema(self):
        tables = self.client.get_schema()
        self.assertGreater(len(tables), 0)

    def test_get_schema_single(self):
        tables = self.client.get_schema("users")
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].name, "users")

    def test_foreign_keys(self):
        fks = self.client.get_foreign_keys("sessions")
        self.assertGreater(len(fks), 0)
        self.assertEqual(fks[0][1], "users")

    def test_export_json(self):
        data = [{"id": 1, "name": "test"}]
        result = self.client.export_data(self.EF.JSON, data)
        self.assertIn("test", result)

    def test_export_csv(self):
        data = [{"id": 1, "name": "test"}]
        result = self.client.export_data(self.EF.CSV, data)
        self.assertIn("id,name", result)

    def test_export_markdown(self):
        data = [{"id": 1, "name": "test"}]
        result = self.client.export_data(self.EF.MARKDOWN, data)
        self.assertIn("| id", result)

    def test_save_query(self):
        sq = self.client.save_query("Test", "SELECT 1;")
        self.assertEqual(sq.name, "Test")
        self.assertIn(sq, self.client.saved_queries)

    def test_table_stats(self):
        stats = self.client.get_table_stats()
        self.assertIn("tables", stats)
        self.assertIn("total_rows", stats)

    def test_table_size_display(self):
        from ui.db_client import Table
        t = Table(name="test", size_bytes=2048)
        self.assertEqual(t.size_display, "2.0 KB")
        t.size_bytes = 500
        self.assertEqual(t.size_display, "500 B")
        t.size_bytes = 5 * 1024 * 1024
        self.assertEqual(t.size_display, "5.0 MB")

    def test_query_history(self):
        self.client.execute_query("SELECT 1;")
        self.client.execute_query("SELECT 2;")
        self.assertEqual(len(self.client.query_history), 2)

    def test_query_result_status_icon(self):
        from ui.db_client import QueryResult
        qr = QueryResult(query="SELECT 1", status=self.QS.SUCCESS)
        self.assertEqual(qr.status_icon, "✅")


if __name__ == "__main__":
    unittest.main()
