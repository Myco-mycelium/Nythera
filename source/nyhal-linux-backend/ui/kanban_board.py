"""Kanban Board — Drag-and-drop columns, labels, and due dates.

Features:
- Multiple boards with customizable columns
- Cards with title, description, labels, due dates, and assignees
- Priority levels (critical, high, medium, low)
- Subtask tracking
- Comment threads
- WIP (Work In Progress) limits
- Archive completed cards
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class CardPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def icon(self) -> str:
        icons = {
            CardPriority.CRITICAL: "🔴", CardPriority.HIGH: "🟠",
            CardPriority.MEDIUM: "🟡", CardPriority.LOW: "🟢",
        }
        return icons.get(self, "?")


@dataclass
class Label:
    name: str = ""
    color: str = "#999"


@dataclass
class Subtask:
    title: str = ""
    done: bool = False

    @property
    def checkbox(self) -> str:
        return "☑" if self.done else "☐"


@dataclass
class Comment:
    author: str = ""
    text: str = ""
    timestamp: float = 0.0

    @property
    def time_str(self) -> str:
        return time.strftime("%b %d %H:%M", time.localtime(self.timestamp))

    @property
    def preview(self) -> str:
        return self.text[:60] + "..." if len(self.text) > 60 else self.text


@dataclass
class Card:
    id: int = 0
    title: str = ""
    description: str = ""
    labels: List[Label] = field(default_factory=list)
    priority: CardPriority = CardPriority.MEDIUM
    assignee: str = ""
    due_date: float = 0.0
    story_points: int = 0
    subtasks: List[Subtask] = field(default_factory=list)
    comments: List[Comment] = field(default_factory=list)
    created_at: float = 0.0
    column_name: str = ""
    checklist_total: int = 0
    checklist_done: int = 0

    @property
    def due_str(self) -> str:
        if self.due_date == 0:
            return ""
        return time.strftime("%b %d", time.localtime(self.due_date))

    @property
    def is_overdue(self) -> bool:
        return self.due_date > 0 and time.time() > self.due_date

    @property
    def due_status(self) -> str:
        if self.due_date == 0:
            return ""
        if self.is_overdue:
            return "🔴"
        remaining = self.due_date - time.time()
        if remaining < 86400:
            return "🟡"
        return "🟢"

    @property
    def label_str(self) -> str:
        return " ".join(f"[{l.name}]" for l in self.labels) if self.labels else ""

    @property
    def subtask_progress(self) -> str:
        total = len(self.subtasks)
        done = sum(1 for s in self.subtasks if s.done)
        if total == 0:
            return ""
        return f"☑{done}/{total}"

    @property
    def points_str(self) -> str:
        return f"⭐{self.story_points}" if self.story_points else ""

    @property
    def comment_count(self) -> str:
        return f"💬{len(self.comments)}" if self.comments else ""


@dataclass
class Column:
    name: str = ""
    cards: List[Card] = field(default_factory=list)
    wip_limit: int = 0  # 0 = unlimited

    @property
    def card_count(self) -> int:
        return len(self.cards)

    @property
    def wip_status(self) -> str:
        if self.wip_limit == 0:
            return f"{self.card_count}"
        if self.card_count > self.wip_limit:
            return f"{self.card_count}/{self.wip_limit} 🚨"
        return f"{self.card_count}/{self.wip_limit}"

    @property
    def wip_bar(self) -> str:
        if self.wip_limit == 0:
            return ""
        filled = min(self.wip_limit, self.card_count)
        return "█" * filled + "░" * (self.wip_limit - filled)

    @property
    def over_limit(self) -> bool:
        return self.wip_limit > 0 and self.card_count > self.wip_limit


@dataclass
class Board:
    name: str = ""
    columns: List[Column] = field(default_factory=list)
    created_at: float = 0.0

    @property
    def total_cards(self) -> int:
        return sum(c.card_count for c in self.columns)


class KanbanBoard:
    def __init__(self):
        self._boards: List[Board] = []
        self._current_board: int = 0
        self._selected_column: int = 0
        self._selected_card: int = 0
        self._view_mode: str = "board"  # board, card, stats, members
        self._show_archive: bool = False
        self._members: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        labels = {
            "bug": Label("bug", "#E74C3C"),
            "feature": Label("feature", "#3498DB"),
            "docs": Label("docs", "#2ECC71"),
            "urgent": Label("urgent", "#E74C3C"),
            "backend": Label("backend", "#9B59B6"),
            "frontend": Label("frontend", "#F39C12"),
            "gpu": Label("gpu", "#1ABC9C"),
            "security": Label("security", "#E67E22"),
        }

        self._members = ["Buffy", "Nyx", "CoBot", "Grace", "Eve"]

        # Nyrqis OS Development Board
        board = Board("Nyrqis OS v1.0", created_at=now - 86400 * 60)

        # Backlog
        backlog = Column("Backlog", wip_limit=0)
        backlog.cards = [
            Card(1, "Implement clipboard sharing", "Share clipboard between compositor and Wayland clients",
                 [labels["feature"], labels["backend"]], CardPriority.MEDIUM, "Buffy",
                 now + 86400 * 14, 5),
            Card(2, "Add Bluetooth support", "Bluetooth audio and HID device support",
                 [labels["feature"]], CardPriority.LOW, "Nyx",
                 now + 86400 * 30, 8),
            Card(3, "Wayland screen sharing", "Implement xdg-desktop-portal screen sharing",
                 [labels["feature"], labels["gpu"]], CardPriority.MEDIUM, "Grace",
                 now + 86400 * 21, 8),
            Card(4, "Package manager UI", "Graphical package management interface",
                 [labels["feature"], labels["frontend"]], CardPriority.MEDIUM, "CoBot",
                 story_points=13),
        ]

        # To Do
        todo = Column("To Do", wip_limit=5)
        todo.cards = [
            Card(5, "Fix DRM memory leak", "Memory leak in DRM buffer allocation on suspend",
                 [labels["bug"], labels["gpu"], labels["urgent"]], CardPriority.CRITICAL, "Nyx",
                 now + 86400 * 2, 3,
                 subtasks=[Subtask("Reproduce on AMD", True), Subtask("Reproduce on Intel", True),
                           Subtask("Fix buffer release", False), Subtask("Add regression test", False)]),
            Card(6, "Write compositor API docs", "Document the compositor public API for third-party use",
                 [labels["docs"]], CardPriority.HIGH, "Buffy",
                 now + 86400 * 7, 5,
                 comments=[Comment("Buffy", "Start with the surface management API", now - 3600),
                           Comment("Grace", "I can help with examples", now - 1800)]),
            Card(7, "EGL fallback optimization", "Optimize the EGL fallback path for low-end GPUs",
                 [labels["gpu"], labels["backend"]], CardPriority.MEDIUM, "Grace",
                 now + 86400 * 10, 5),
            Card(8, "Security audit fix: buffer overflow", "Fix buffer overflow in DRM ioctl handler",
                 [labels["security"], labels["urgent"], labels["bug"]], CardPriority.CRITICAL, "Eve",
                 now + 86400, 2),
        ]

        # In Progress
        in_progress = Column("In Progress", wip_limit=3)
        in_progress.cards = [
            Card(9, "Vulkan renderer integration", "Integrate the Vulkan renderer into the compositor pipeline",
                 [labels["gpu"], labels["feature"]], CardPriority.HIGH, "Nyx",
                 now + 86400 * 3, 13,
                 subtasks=[Subtask("Create Vulkan surface", True), Subtask("Implement swapchain", True),
                           Subtask("Layer composition", False), Subtask("Frame sync", False),
                           Subtask("Fallback detection", False)],
                 comments=[Comment("Nyx", "Vulkan surface creation is done, working on swapchain", now - 7200)]),
            Card(10, "Shell backend abstraction", "Wire Backend abstraction into shell.py and compositor.py",
                 [labels["backend"], labels["feature"]], CardPriority.HIGH, "Buffy",
                 now + 86400 * 2, 8,
                 subtasks=[Subtask("Create Backend ABC", True), Subtask("LinuxBackend impl", True),
                           Subtask("NyrqisBackend impl", True), Subtask("Wire into shell", False)]),
        ]

        # Review
        review = Column("Review", wip_limit=3)
        review.cards = [
            Card(11, "Wayland bridge reconnection", "Fix Wayland bridge disconnects on suspend/resume",
                 [labels["bug"], labels["backend"]], CardPriority.HIGH, "Nyx",
                 now + 86400, 3,
                 comments=[Comment("Buffy", "Looks good, one minor comment on the reconnect logic", now - 3600),
                           Comment("Nyx", "Fixed, ready for re-review", now - 1800)]),
            Card(12, "Update changelog for v1.0-rc2", "Document all changes for the release candidate",
                 [labels["docs"]], CardPriority.MEDIUM, "CoBot",
                 now + 86400 * 5, 2),
        ]

        # Done
        done = Column("Done", wip_limit=0)
        done.cards = [
            Card(13, "Implement DRM atomic modesetting", "Wire DRM crate to real ioctl for atomic modesetting",
                 [labels["backend"], labels["gpu"]], CardPriority.HIGH, "Nyx",
                 story_points=8, checklist_done=3, checklist_total=3),
            Card(14, "Build compositor framework", "Initial compositor with DRM/KMS backend",
                 [labels["backend"], labels["gpu"], labels["feature"]], CardPriority.CRITICAL, "Nyx",
                 story_points=13, checklist_done=5, checklist_total=5),
            Card(15, "Shell UI with 169 apps", "Complete shell application suite",
                 [labels["feature"], labels["frontend"]], CardPriority.HIGH, "Buffy",
                 story_points=34, checklist_done=10, checklist_total=10),
            Card(16, "Write HAL documentation", "Document the hardware abstraction layer",
                 [labels["docs"]], CardPriority.MEDIUM, "Grace",
                 story_points=3),
        ]

        board.columns = [backlog, todo, in_progress, review, done]
        self._boards.append(board)

        # Maintenance Board
        maint = Board("Maintenance", created_at=now - 86400 * 30)
        maint.columns = [
            Column("Issues", wip_limit=0, cards=[
                Card(20, "Memory leak in compositor", "[Compositor] Memory usage grows over time",
                     [labels["bug"]], CardPriority.HIGH, "Nyx", story_points=5),
                Card(21, "Slow file manager search", "File search takes 5+ seconds on large directories",
                     [labels["bug"]], CardPriority.MEDIUM, "CoBot", story_points=3),
            ]),
            Column("In Progress", wip_limit=3, cards=[
                Card(22, "Improve test coverage", "Increase test coverage to 90%",
                     [labels["feature"]], CardPriority.MEDIUM, "Buffy", story_points=8),
            ]),
            Column("Done", wip_limit=0, cards=[
                Card(23, "Fix cross-compile for ARM", "Fix cross-compilation for Raspberry Pi",
                     [labels["bug"]], CardPriority.HIGH, "Nyx", story_points=5),
            ]),
        ]
        self._boards.append(maint)

    @property
    def current_board(self) -> Optional[Board]:
        if 0 <= self._current_board < len(self._boards):
            return self._boards[self._current_board]
        return None

    @property
    def selected_card(self) -> Optional[Card]:
        board = self.current_board
        if board and 0 <= self._selected_column < len(board.columns):
            col = board.columns[self._selected_column]
            if 0 <= self._selected_card < len(col.cards):
                return col.cards[self._selected_card]
        return None

    @property
    def total_cards(self) -> int:
        return sum(b.total_cards for b in self._boards)

    @property
    def overdue_count(self) -> int:
        count = 0
        for b in self._boards:
            for col in b.columns:
                for card in col.cards:
                    if card.is_overdue:
                        count += 1
        return count

    def select_board(self, idx: int):
        if 0 <= idx < len(self._boards):
            self._current_board = idx

    def select_column(self, idx: int):
        board = self.current_board
        if board and 0 <= idx < len(board.columns):
            self._selected_column = idx
            self._selected_card = 0

    def select_card(self, idx: int):
        board = self.current_board
        if board and self._selected_column < len(board.columns):
            col = board.columns[self._selected_column]
            if 0 <= idx < len(col.cards):
                self._selected_card = idx

    def set_view(self, mode: str):
        if mode in ("board", "card", "stats", "members"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS KANBAN BOARD                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        board = self.current_board
        board_name = board.name if board else "None"
        lines.append(f"  📋 {board_name}  🃏 {self.total_cards} cards  ⚠️ {self.overdue_count} overdue  👥 {len(self._members)} members")
        lines.append("")

        if self._view_mode == "board" and board:
            # Column headers
            header = "  "
            for col in board.columns:
                wip = col.wip_status
                over = " 🚨" if col.over_limit else ""
                header += f" {col.name}({wip}){over} |"
            lines.append(header)

            # Card previews per column
            max_cards = max((col.card_count for col in board.columns), default=0)
            for row in range(min(max_cards, 6)):
                row_line = "  "
                for col in board.columns:
                    if row < col.card_count:
                        card = col.cards[row]
                        sel = "▸" if (board.columns.index(col) == self._selected_column and
                                       row == self._selected_card) else " "
                        pri = card.priority.icon
                        pts = card.points_str
                        due = card.due_status
                        sub = card.subtask_progress
                        row_line += f" {sel}{pri}{card.title[:18]:<18s}{pts}{due}{sub} |"
                    else:
                        row_line += " " * 24 + "|"
                lines.append(row_line)

        elif self._view_mode == "card":
            card = self.selected_card
            if card:
                lines.append(f"  ── {card.title} ──")
                lines.append(f"  Priority: {card.priority.icon} {card.priority.value}")
                lines.append(f"  Assignee: {card.assignee}")
                if card.due_date:
                    lines.append(f"  Due: {card.due_str} {card.due_status}")
                if card.labels:
                    lines.append(f"  Labels: {card.label_str}")
                if card.story_points:
                    lines.append(f"  Points: {card.points_str}")
                if card.description:
                    lines.append(f"  {card.description}")
                if card.subtasks:
                    lines.append("")
                    lines.append("  ── Subtasks ──")
                    for st in card.subtasks:
                        lines.append(f"  {st.checkbox} {st.title}")
                if card.comments:
                    lines.append("")
                    lines.append("  ── Comments ──")
                    for c in card.comments:
                        lines.append(f"  💬 {c.author} ({c.time_str}): {c.preview}")

        elif self._view_mode == "stats":
            lines.append("  ── Board Statistics ──")
            if board:
                for col in board.columns:
                    bar = "█" * col.card_count + "░" * max(0, 10 - col.card_count)
                    lines.append(f"  {col.name:<15s} [{bar}] {col.card_count} cards")

                lines.append("")
                # Priority distribution
                all_cards = [c for col in board.columns for c in col.cards]
                pri_counts = {}
                for c in all_cards:
                    pri_counts[c.priority] = pri_counts.get(c.priority, 0) + 1
                for pri in CardPriority:
                    cnt = pri_counts.get(pri, 0)
                    bar = "█" * cnt + "░" * max(0, 10 - cnt)
                    lines.append(f"  {pri.icon} {pri.value:<10s} [{bar}] {cnt}")

        elif self._view_mode == "members":
            lines.append("  ── Team Members ──")
            for member in self._members:
                assigned = 0
                for b in self._boards:
                    for col in b.columns:
                        assigned += sum(1 for c in col.cards if c.assignee == member)
                lines.append(f"  👤 {member:<16s} Assigned: {assigned} cards")

        lines.append("")
        lines.append("  [B]oard [C]ard [S]tats [M]embers [←→]Column [↑↓]Card [↑↓]Move")
        return lines
