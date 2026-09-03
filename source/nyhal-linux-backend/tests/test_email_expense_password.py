"""Tests for email client, expense tracker, and password manager."""
import unittest
import time

from ui.email_client import (
    EmailClient, Email, EmailAddress, EmailFolder, Attachment,
    EmailPriority, EmailFlag,
)
from ui.expense_tracker import (
    ExpenseTracker, Transaction, Category, Budget, SavingsGoal,
    TransactionType, Recurrence,
)
from ui.password_manager import (
    PasswordManager, VaultEntry, PasswordGenerator, AutoFillEntry,
    VaultCategory,
)


# ─── Email Client Tests ──────────────────────────────────────────────

class TestEmailAddress(unittest.TestCase):
    def test_display(self):
        addr = EmailAddress(name="Alice", address="alice@test.com")
        self.assertIn("Alice", addr.display)
        self.assertIn("alice@test.com", addr.display)

    def test_short(self):
        addr = EmailAddress(name="Alice", address="alice@test.com")
        self.assertEqual(addr.short, "Alice")


class TestAttachment(unittest.TestCase):
    def test_size_str(self):
        att = Attachment("file.pdf", 1500000)
        self.assertIn("MB", att.size_str)

    def test_icon(self):
        att = Attachment("script.py")
        self.assertEqual(att.icon, "🐍")


class TestEmail(unittest.TestCase):
    def test_time_str(self):
        e = Email(timestamp=time.time())
        self.assertIn(":", e.time_str)

    def test_preview(self):
        e = Email(body_text="Hello world test content here")
        self.assertEqual(e.preview, "Hello world test content here")

    def test_long_preview(self):
        e = Email(body_text="x" * 100)
        self.assertTrue(len(e.preview) <= 83)

    def test_to_str(self):
        e = Email(to=[EmailAddress(name="A", address="a@b.com"),
                      EmailAddress(name="B", address="b@b.com")])
        self.assertIn("A", e.to_str)

    def test_attachment_str(self):
        e = Email(attachments=[Attachment("a.pdf"), Attachment("b.pdf")])
        self.assertIn("2", e.attachment_str)


class TestEmailClient(unittest.TestCase):
    def setUp(self):
        self.client = EmailClient()

    def test_initial_state(self):
        self.assertGreater(len(self.client._emails), 0)
        self.assertGreater(len(self.client._folders), 0)
        self.assertGreater(len(self.client._contacts), 0)

    def test_render(self):
        lines = self.client.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("EMAIL CLIENT" in l for l in lines))

    def test_render_read(self):
        self.client.set_view("read")
        self.client._selected_email = 0
        lines = self.client.render()
        self.assertTrue(any("From:" in l for l in lines))

    def test_render_compose(self):
        self.client.set_view("compose")
        lines = self.client.render()
        self.assertTrue(any("Compose" in l for l in lines))

    def test_render_contacts(self):
        self.client.set_view("contacts")
        lines = self.client.render()
        self.assertTrue(any("Contacts" in l for l in lines))

    def test_render_folders(self):
        self.client.set_view("folders")
        lines = self.client.render()
        self.assertTrue(any("Folders" in l for l in lines))

    def test_toggle_starred(self):
        self.client._selected_email = 0
        initial = self.client._emails[0].is_starred
        self.client.toggle_starred()
        self.assertNotEqual(self.client._emails[0].is_starred, initial)

    def test_total_unread(self):
        self.assertGreater(self.client.total_unread, 0)

    def test_filtered_search(self):
        self.client._search_text = "compositor"
        filtered = self.client.filtered_emails
        for e in filtered:
            self.assertTrue("compositor" in e.subject.lower() or "compositor" in e.body_text.lower())


# ─── Expense Tracker Tests ───────────────────────────────────────────

class TestCategory(unittest.TestCase):
    def test_usage_pct(self):
        c = Category("Test", "🧪", "#fff", budget_limit=100, spent=50)
        self.assertAlmostEqual(c.usage_pct, 50.0)

    def test_status_icon(self):
        c = Category("Test", "🧪", "#fff", budget_limit=100, spent=95)
        self.assertIn("🟡", c.status_icon)

    def test_over_budget(self):
        c = Category("Test", "🧪", "#fff", budget_limit=100, spent=110)
        self.assertIn("🔴", c.status_icon)


class TestTransaction(unittest.TestCase):
    def test_amount_str_expense(self):
        t = Transaction(amount=50.0, tx_type=TransactionType.EXPENSE)
        self.assertIn("-", t.amount_str)

    def test_amount_str_income(self):
        t = Transaction(amount=100.0, tx_type=TransactionType.INCOME)
        self.assertIn("+", t.amount_str)


class TestSavingsGoal(unittest.TestCase):
    def test_progress_pct(self):
        g = SavingsGoal("Test", target=1000, current=500)
        self.assertAlmostEqual(g.progress_pct, 50.0)

    def test_progress_bar(self):
        g = SavingsGoal("Test", target=100, current=50)
        bar = g.progress_bar
        self.assertIn("█", bar)
        self.assertEqual(len(bar), 20)


class TestExpenseTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = ExpenseTracker()

    def test_initial_state(self):
        self.assertGreater(len(self.tracker._transactions), 0)
        self.assertGreater(len(self.tracker._categories), 0)
        self.assertGreater(len(self.tracker._goals), 0)

    def test_render(self):
        lines = self.tracker.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("EXPENSE TRACKER" in l for l in lines))

    def test_render_transactions(self):
        self.tracker.set_view("transactions")
        lines = self.tracker.render()
        self.assertTrue(any("Transactions" in l for l in lines))

    def test_render_categories(self):
        self.tracker.set_view("categories")
        lines = self.tracker.render()
        self.assertTrue(any("Categories" in l for l in lines))

    def test_render_goals(self):
        self.tracker.set_view("goals")
        lines = self.tracker.render()
        self.assertTrue(any("Goals" in l for l in lines))

    def test_render_recurring(self):
        self.tracker.set_view("recurring")
        lines = self.tracker.render()
        self.assertTrue(any("Recurring" in l for l in lines))

    def test_render_charts(self):
        self.tracker.set_view("charts")
        lines = self.tracker.render()
        self.assertTrue(any("Category" in l for l in lines))

    def test_total_income(self):
        self.assertGreater(self.tracker.total_income, 0)

    def test_total_expenses(self):
        self.assertGreater(self.tracker.total_expenses, 0)

    def test_balance(self):
        balance = self.tracker.balance
        self.assertIsNotNone(balance)

    def test_savings_rate(self):
        rate = self.tracker.savings_rate
        self.assertGreaterEqual(rate, -100)
        self.assertLessEqual(rate, 100)

    def test_recurring_count(self):
        self.assertGreater(self.tracker.recurring_count, 0)


# ─── Password Manager Tests ─────────────────────────────────────────

class TestVaultEntry(unittest.TestCase):
    def test_strength(self):
        e = VaultEntry(password="Str0ng!P@ssw0rd#2024")
        self.assertGreater(e.strength, 60)

    def test_weak_password(self):
        e = VaultEntry(password="abc")
        self.assertLess(e.strength, 30)

    def test_strength_bar(self):
        e = VaultEntry(password="StrongPass123!")
        bar = e.strength_bar
        self.assertEqual(len(bar), 20)

    def test_password_masked(self):
        e = VaultEntry(password="SuperSecret12345")
        masked = e.password_masked
        self.assertTrue(all(c == "•" for c in masked))
        self.assertEqual(len(masked), 16)

    def test_domain(self):
        e = VaultEntry(url="https://github.com/login")
        self.assertEqual(e.domain, "github.com")

    def test_has_totp(self):
        e = VaultEntry(totp_secret="JBSWY3DPEHPK3PXP")
        self.assertTrue(e.has_totp)

    def test_breach_icon(self):
        e = VaultEntry(breach_status="safe")
        self.assertEqual(e.breach_icon, "✅")


class TestPasswordGenerator(unittest.TestCase):
    def test_generate(self):
        gen = PasswordGenerator(length=20)
        pwd = gen.generate()
        self.assertEqual(len(pwd), 20)

    def test_charset(self):
        gen = PasswordGenerator(uppercase=True, lowercase=True, digits=True, symbols=True)
        self.assertGreater(len(gen.charset), 50)

    def test_strength_pct(self):
        gen = PasswordGenerator(length=20, uppercase=True, lowercase=True, digits=True, symbols=True)
        self.assertGreater(gen.strength_pct, 60)


class TestPasswordManager(unittest.TestCase):
    def setUp(self):
        self.pm = PasswordManager()

    def test_initial_state(self):
        self.assertGreater(len(self.pm._entries), 0)
        self.assertGreater(len(self.pm._autofill), 0)

    def test_render(self):
        lines = self.pm.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PASSWORD MANAGER" in l for l in lines))

    def test_render_generator(self):
        self.pm.set_view("generator")
        lines = self.pm.render()
        self.assertTrue(any("Generator" in l for l in lines))

    def test_render_audit(self):
        self.pm.set_view("audit")
        lines = self.pm.render()
        self.assertTrue(any("Audit" in l for l in lines))

    def test_render_autofill(self):
        self.pm.set_view("autofill")
        lines = self.pm.render()
        self.assertTrue(any("Auto-fill" in l for l in lines))

    def test_generate_password(self):
        pwd = self.pm.generate_password()
        self.assertEqual(len(pwd), 20)
        self.assertEqual(pwd, self.pm._last_generated)

    def test_total_entries(self):
        self.assertGreater(self.pm.total_entries, 0)

    def test_toggle_show(self):
        self.assertFalse(self.pm._show_passwords)
        self.pm.toggle_show_passwords()
        self.assertTrue(self.pm._show_passwords)

    def test_filtered_search(self):
        self.pm._search_text = "github"
        filtered = self.pm.filtered_entries
        self.assertTrue(any("github" in e.name.lower() for e in filtered))


if __name__ == "__main__":
    unittest.main()
