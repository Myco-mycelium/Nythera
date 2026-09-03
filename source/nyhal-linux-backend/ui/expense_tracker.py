"""Expense Tracker — Budget categories, charts, and recurring transactions.

Features:
- Transaction tracking with categories and tags
- Budget management with per-category limits
- Recurring transaction support
- Income vs expense overview
- Monthly/yearly summaries
- Category breakdown with visual charts
- Savings goals tracking
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class TransactionType(Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"

    @property
    def icon(self) -> str:
        icons = {
            TransactionType.INCOME: "💰", TransactionType.EXPENSE: "💸",
            TransactionType.TRANSFER: "🔄",
        }
        return icons.get(self, "?")


class Recurrence(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    @property
    def icon(self) -> str:
        icons = {
            Recurrence.NONE: "", Recurrence.DAILY: "📅",
            Recurrence.WEEKLY: "📆", Recurrence.MONTHLY: "🗓", Recurrence.YEARLY: "🎆",
        }
        return icons.get(self, "")


@dataclass
class Category:
    name: str = ""
    icon: str = ""
    color: str = "#666"
    budget_limit: float = 0.0
    spent: float = 0.0
    is_income: bool = False

    @property
    def remaining(self) -> float:
        return self.budget_limit - self.spent

    @property
    def usage_pct(self) -> float:
        if self.budget_limit == 0:
            return 0.0
        return min(100.0, self.spent / self.budget_limit * 100)

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.usage_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        if self.usage_pct >= 100:
            return "🔴"
        if self.usage_pct >= 80:
            return "🟡"
        return "🟢"


@dataclass
class Transaction:
    id: int = 0
    description: str = ""
    amount: float = 0.0
    category: str = ""
    tx_type: TransactionType = TransactionType.EXPENSE
    timestamp: float = 0.0
    tags: List[str] = field(default_factory=list)
    recurring: Recurrence = Recurrence.NONE
    merchant: str = ""
    account: str = "checking"
    notes: str = ""
    is_reconciled: bool = False

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.timestamp))

    @property
    def short_time(self) -> str:
        return time.strftime("%b %d", time.localtime(self.timestamp))

    @property
    def amount_str(self) -> str:
        if self.tx_type == TransactionType.EXPENSE:
            return f"-${abs(self.amount):.2f}"
        return f"+${self.amount:.2f}"

    @property
    def amount_color(self) -> str:
        if self.tx_type == TransactionType.INCOME:
            return "green"
        return "red"

    @property
    def recurring_str(self) -> str:
        return self.recurring.icon


@dataclass
class Budget:
    name: str = ""
    total_income: float = 0.0
    total_expenses: float = 0.0
    month: str = ""

    @property
    def balance(self) -> float:
        return self.total_income - self.total_expenses

    @property
    def savings_rate(self) -> float:
        if self.total_income == 0:
            return 0.0
        return self.balance / self.total_income * 100

    @property
    def savings_bar(self) -> str:
        pct = max(0, min(100, int(self.savings_rate)))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def expense_bar(self) -> str:
        if self.total_income == 0:
            return ""
        pct = min(100, int(self.total_expenses / self.total_income * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)


@dataclass
class SavingsGoal:
    name: str = ""
    target: float = 0.0
    current: float = 0.0
    deadline: str = ""
    icon: str = "🎯"

    @property
    def progress_pct(self) -> float:
        if self.target == 0:
            return 0.0
        return min(100, self.current / self.target * 100)

    @property
    def progress_bar(self) -> str:
        filled = min(20, int(self.progress_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def remaining_str(self) -> str:
        return f"${self.target - self.current:,.2f}"


class ExpenseTracker:
    def __init__(self):
        self._transactions: List[Transaction] = []
        self._categories: List[Category] = []
        self._budget: Optional[Budget] = None
        self._goals: List[SavingsGoal] = []
        self._selected_tx: int = 0
        self._selected_category: int = 0
        self._view_mode: str = "overview"  # overview, transactions, categories, goals, recurring, charts
        self._month_filter: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Categories
        self._categories = [
            Category("Housing", "🏠", "#4A90D9", 2000, 1850),
            Category("Food & Dining", "🍽", "#F5A623", 800, 650),
            Category("Transportation", "🚗", "#7ED321", 500, 380),
            Category("Utilities", "⚡", "#9B59B6", 300, 275),
            Category("Entertainment", "🎬", "#E74C3C", 400, 320),
            Category("Shopping", "🛍", "#1ABC9C", 600, 450),
            Category("Healthcare", "🏥", "#3498DB", 200, 120),
            Category("Education", "📚", "#F39C12", 300, 150),
            Category("Subscriptions", "📱", "#E67E22", 150, 145),
            Category("Income", "💰", "#2ECC71", 0, 8500, is_income=True),
        ]

        # Transactions
        tx_data = [
            (1, "Monthly Rent", 1850, "Housing", TransactionType.EXPENSE, now - 86400 * 2, [], Recurrence.MONTHLY, "Nyrqis Apartments"),
            (2, "Grocery Store", 127.45, "Food & Dining", TransactionType.EXPENSE, now - 86400, ["groceries"], Recurrence.NONE, "Whole Foods"),
            (3, "Salary Deposit", 8500, "Income", TransactionType.INCOME, now - 86400 * 3, ["salary"], Recurrence.MONTHLY, "Nyrqis Corp"),
            (4, "Electric Bill", 95.20, "Utilities", TransactionType.EXPENSE, now - 86400 * 4, ["utilities"], Recurrence.MONTHLY, "Pacific Gas"),
            (5, "Netflix", 15.99, "Subscriptions", TransactionType.EXPENSE, now - 86400 * 5, ["streaming"], Recurrence.MONTHLY, "Netflix"),
            (6, "Gas Station", 52.30, "Transportation", TransactionType.EXPENSE, now - 86400, ["fuel"], Recurrence.NONE, "Shell"),
            (7, "Restaurant", 68.50, "Food & Dining", TransactionType.EXPENSE, now - 7200, ["dining out"], Recurrence.NONE, "Nobu"),
            (8, "Movie Tickets", 32.00, "Entertainment", TransactionType.EXPENSE, now - 86400 * 2, ["movies"], Recurrence.NONE, "AMC"),
            (9, "Amazon Order", 89.99, "Shopping", TransactionType.EXPENSE, now - 86400 * 6, ["online"], Recurrence.NONE, "Amazon"),
            (10, "Doctor Visit", 120.00, "Healthcare", TransactionType.EXPENSE, now - 86400 * 10, ["medical"], Recurrence.NONE, "Dr. Smith"),
            (11, "Online Course", 149.99, "Education", TransactionType.EXPENSE, now - 86400 * 15, ["learning"], Recurrence.NONE, "Udemy"),
            (12, "Freelance Payment", 2000, "Income", TransactionType.INCOME, now - 86400 * 7, ["freelance"], Recurrence.NONE, "Client Inc"),
            (13, "Gym Membership", 49.99, "Healthcare", TransactionType.EXPENSE, now - 86400 * 8, ["fitness"], Recurrence.MONTHLY, "FitLife"),
            (14, "Internet Bill", 79.99, "Utilities", TransactionType.EXPENSE, now - 86400 * 4, ["utilities", "internet"], Recurrence.MONTHLY, "Comcast"),
            (15, "Coffee Shop", 12.50, "Food & Dining", TransactionType.EXPENSE, now - 3600, ["coffee"], Recurrence.NONE, "Starbucks"),
            (16, "Bus Pass", 100, "Transportation", TransactionType.EXPENSE, now - 86400 * 30, ["transit"], Recurrence.MONTHLY, "Muni"),
            (17, "Spotify", 10.99, "Subscriptions", TransactionType.EXPENSE, now - 86400 * 5, ["music"], Recurrence.MONTHLY, "Spotify"),
            (18, "Dividend Income", 250, "Income", TransactionType.INCOME, now - 86400 * 15, ["investments"], Recurrence.MONTHLY, "Vanguard"),
        ]

        for (id_, desc, amt, cat, typ, ts, tags, recur, merchant) in tx_data:
            self._transactions.append(Transaction(
                id=id_, description=desc, amount=amt, category=cat,
                tx_type=typ, timestamp=ts, tags=tags, recurring=recur,
                merchant=merchant, is_reconciled=random.random() > 0.3,
            ))
        self._transactions.sort(key=lambda t: t.timestamp, reverse=True)

        # Monthly budget
        income = sum(t.amount for t in self._transactions if t.tx_type == TransactionType.INCOME)
        expenses = sum(t.amount for t in self._transactions if t.tx_type == TransactionType.EXPENSE)
        self._budget = Budget(
            name="Monthly Budget",
            total_income=income,
            total_expenses=expenses,
            month=time.strftime("%B %Y"),
        )

        # Savings goals
        self._goals = [
            SavingsGoal("Emergency Fund", 15000, 8500, "Dec 2026", "🛡"),
            SavingsGoal("New GPU", 2000, 1200, "Sep 2026", "🖥"),
            SavingsGoal("Vacation", 5000, 2100, "Mar 2027", "✈️"),
            SavingsGoal("Home Office Setup", 3000, 3000, "Jun 2026", "🖥"),
        ]

    @property
    def total_income(self) -> float:
        return sum(t.amount for t in self._transactions if t.tx_type == TransactionType.INCOME)

    @property
    def total_expenses(self) -> float:
        return sum(t.amount for t in self._transactions if t.tx_type == TransactionType.EXPENSE)

    @property
    def balance(self) -> float:
        return self.total_income - self.total_expenses

    @property
    def savings_rate(self) -> float:
        if self.total_income == 0:
            return 0.0
        return self.balance / self.total_income * 100

    @property
    def recurring_count(self) -> int:
        return sum(1 for t in self._transactions if t.recurring != Recurrence.NONE)

    def select_tx(self, idx: int):
        if 0 <= idx < len(self._transactions):
            self._selected_tx = idx

    def set_view(self, mode: str):
        if mode in ("overview", "transactions", "categories", "goals", "recurring", "charts"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS EXPENSE TRACKER                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  💰 Income: ${self.total_income:,.2f}  💸 Expenses: ${self.total_expenses:,.2f}  🏦 Balance: ${self.balance:,.2f}  📊 Savings: {self.savings_rate:.0f}%  🔄 {self.recurring_count} recurring")
        lines.append("")

        if self._view_mode == "overview":
            # Budget bar
            bar = "█" * int(self.savings_rate / 5) + "░" * (20 - int(self.savings_rate / 5))
            lines.append(f"  ── Monthly Summary ──")
            lines.append(f"  Income:     ${self.total_income:>10,.2f}")
            lines.append(f"  Expenses:   ${self.total_expenses:>10,.2f}")
            lines.append(f"  Savings:    ${self.balance:>10,.2f}  [{bar}] {self.savings_rate:.0f}%")
            lines.append("")

            # Top expenses
            expenses = sorted([t for t in self._transactions if t.tx_type == TransactionType.EXPENSE],
                              key=lambda t: t.amount, reverse=True)
            lines.append("  ── Top Expenses ──")
            for t in expenses[:8]:
                cat = next((c for c in self._categories if c.name == t.category), None)
                icon = cat.icon if cat else "💸"
                lines.append(f"  {icon} {t.description:<28s} {t.amount_str:>10s}  {t.short_time}")

        elif self._view_mode == "transactions":
            lines.append("  ── Recent Transactions ──")
            for i, t in enumerate(self._transactions[:15]):
                sel = "▶" if i == self._selected_tx else " "
                cat = next((c for c in self._categories if c.name == t.category), None)
                icon = cat.icon if cat else "💸"
                rec = t.recurring_str
                lines.append(f"  {sel}{icon} {t.description:<28s} {t.amount_str:>10s}  {t.short_time} {rec}")

        elif self._view_mode == "categories":
            lines.append("  ── Budget Categories ──")
            for i, c in enumerate(self._categories):
                if c.is_income:
                    continue
                lines.append(f"  {c.status_icon} {c.icon} {c.name:<20s} [{c.usage_bar}] ${c.spent:,.0f}/${c.budget_limit:,.0f} ({c.usage_pct:.0f}%)")

        elif self._view_mode == "goals":
            lines.append("  ── Savings Goals ──")
            for g in self._goals:
                done = "✅" if g.progress_pct >= 100 else "🎯"
                lines.append(f"  {done} {g.icon} {g.name}")
                lines.append(f"      [{g.progress_bar}] ${g.current:,.0f} / ${g.target:,.0f} ({g.progress_pct:.0f}%)  Remaining: {g.remaining_str}  Deadline: {g.deadline}")

        elif self._view_mode == "recurring":
            lines.append("  ── Recurring Transactions ──")
            recurring = [t for t in self._transactions if t.recurring != Recurrence.NONE]
            for t in recurring:
                lines.append(f"  {t.recurring_str} {t.merchant:<20s} {t.amount_str:>10s}  {t.recurring.value}  {t.category}")

        elif self._view_mode == "charts":
            lines.append("  ── Spending by Category ──")
            cat_totals = {}
            for t in self._transactions:
                if t.tx_type == TransactionType.EXPENSE:
                    cat_totals[t.category] = cat_totals.get(t.category, 0) + t.amount
            sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])
            max_val = max(v for _, v in sorted_cats) if sorted_cats else 1
            for cat_name, total in sorted_cats:
                cat = next((c for c in self._categories if c.name == cat_name), None)
                icon = cat.icon if cat else "💸"
                bar_len = int(total / max_val * 30)
                bar = "█" * bar_len + "░" * (30 - bar_len)
                lines.append(f"  {icon} {cat_name:<18s} [{bar}] ${total:,.0f}")

        lines.append("")
        lines.append("  [O]verview [T]ransactions [C]ategories [G]oals [R]ecurring [H] Charts [↑↓]Nav")
        return lines
