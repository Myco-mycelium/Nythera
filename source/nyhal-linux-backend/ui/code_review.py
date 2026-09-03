"""Code Review Tool — diff comments, approval workflow, inline suggestions for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class ReviewStatus(Enum):
    PENDING = "Pending"
    REVIEWING = "Reviewing"
    APPROVED = "Approved"
    CHANGES_REQUESTED = "Changes Requested"
    MERGED = "Merged"
    CLOSED = "Closed"


class CommentType(Enum):
    LINE_COMMENT = "Line Comment"
    FILE_COMMENT = "File Comment"
    GENERAL_COMMENT = "General Comment"
    SUGGESTION = "Suggestion"
    BUG_REPORT = "Bug Report"
    QUESTION = "Question"
    PRAISE = "Praise"


class Severity(Enum):
    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"
    CRITICAL = "Critical"


class DiffType(Enum):
    ADDED = "Added"
    REMOVED = "Removed"
    MODIFIED = "Modified"
    RENAMED = "Renamed"
    UNCHANGED = "Unchanged"


@dataclass
class DiffLine:
    line_num: int
    content: str
    diff_type: DiffType = DiffType.UNCHANGED

    @property
    def prefix(self) -> str:
        icons = {
            DiffType.ADDED: "+",
            DiffType.REMOVED: "-",
            DiffType.MODIFIED: "~",
            DiffType.UNCHANGED: " ",
        }
        return icons.get(self.diff_type, " ")

    @property
    def color_hint(self) -> str:
        hints = {
            DiffType.ADDED: "green",
            DiffType.REMOVED: "red",
            DiffType.MODIFIED: "yellow",
        }
        return hints.get(self.diff_type, "white")


@dataclass
class InlineSuggestion:
    line_num: int
    old_code: str
    new_code: str
    reason: str = ""
    applied: bool = False

    @property
    def status_icon(self) -> str:
        return "✅" if self.applied else "💡"


@dataclass
class ReviewComment:
    id: int
    author: str = ""
    comment_type: CommentType = CommentType.LINE_COMMENT
    file_path: str = ""
    line_num: int = 0
    end_line: int = 0
    body: str = ""
    created_at: float = 0.0
    resolved: bool = False
    replies: List["ReviewComment"] = field(default_factory=list)
    suggestion: Optional[InlineSuggestion] = None
    severity: Severity = Severity.INFO
    reactions: Dict[str, int] = field(default_factory=dict)

    @property
    def type_icon(self) -> str:
        icons = {
            CommentType.LINE_COMMENT: "💬",
            CommentType.FILE_COMMENT: "📄",
            CommentType.GENERAL_COMMENT: "💭",
            CommentType.SUGGESTION: "💡",
            CommentType.BUG_REPORT: "🐛",
            CommentType.QUESTION: "❓",
            CommentType.PRAISE: "🎉",
        }
        return icons.get(self.comment_type, "💬")

    @property
    def severity_icon(self) -> str:
        icons = {Severity.INFO: "ℹ️", Severity.WARNING: "⚠️", Severity.ERROR: "❌", Severity.CRITICAL: "🚨"}
        return icons.get(self.severity, "")

    @property
    def status_str(self) -> str:
        if self.resolved:
            return "✅ Resolved"
        return "🔴 Open"

    @property
    def time_ago(self) -> str:
        if self.created_at <= 0:
            return ""
        delta = time.time() - self.created_at
        if delta < 60:
            return f"{delta:.0f}s ago"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class FileDiff:
    file_path: str
    old_path: str = ""
    diff_type: DiffType = DiffType.MODIFIED
    lines: List[DiffLine] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    comments: List[ReviewComment] = field(default_factory=list)
    suggestions: List[InlineSuggestion] = field(default_factory=list)
    binary: bool = False

    @property
    def change_summary(self) -> str:
        return f"+{self.additions} -{self.deletions}"

    @property
    def has_comments(self) -> bool:
        return len(self.comments) > 0

    @property
    def type_icon(self) -> str:
        icons = {
            DiffType.ADDED: "🆕", DiffType.REMOVED: "🗑", DiffType.MODIFIED: "📝",
            DiffType.RENAMED: "📛",
        }
        return icons.get(self.diff_type, "📄")


@dataclass
class Reviewer:
    name: str
    status: ReviewStatus = ReviewStatus.PENDING
    approved: bool = False
    comments_count: int = 0
    reviewed_at: float = 0.0

    @property
    def status_icon(self) -> str:
        icons = {
            ReviewStatus.PENDING: "⏳",
            ReviewStatus.REVIEWING: "👀",
            ReviewStatus.APPROVED: "✅",
            ReviewStatus.CHANGES_REQUESTED: "🔄",
        }
        return icons.get(self.status, "?")


@dataclass
class PullRequest:
    id: int
    title: str = ""
    description: str = ""
    author: str = ""
    source_branch: str = ""
    target_branch: str = "main"
    status: ReviewStatus = ReviewStatus.PENDING
    created_at: float = 0.0
    updated_at: float = 0.0
    merged_at: float = 0.0
    reviewers: List[Reviewer] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0

    @property
    def status_icon(self) -> str:
        icons = {
            ReviewStatus.PENDING: "⏳", ReviewStatus.REVIEWING: "👀",
            ReviewStatus.APPROVED: "✅", ReviewStatus.CHANGES_REQUESTED: "🔄",
            ReviewStatus.MERGED: "🟣", ReviewStatus.CLOSED: "⚫",
        }
        return icons.get(self.status, "?")

    @property
    def approval_count(self) -> int:
        return sum(1 for r in self.reviewers if r.approved)

    @property
    def approvals_str(self) -> str:
        return f"{self.approval_count}/{len(self.reviewers)}"

    @property
    def change_stats(self) -> str:
        return f"+{self.additions} -{self.deletions} ({self.files_changed} files)"


class CodeReview:
    def __init__(self):
        self._prs: List[PullRequest] = []
        self._file_diffs: List[FileDiff] = []
        self._selected_pr: int = 0
        self._selected_file: int = 0
        self._selected_comment: int = -1
        self._view_mode: str = "diff"
        self._show_suggestions: bool = True
        self._show_resolved: bool = True
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._prs = [
            PullRequest(42, "Fix compositor memory leak in frame submission",
                        "Resolves issue #38. The compositor was not properly releasing SHM buffers after frame submission, causing memory to grow unbounded.",
                        "developer", "fix/memory-leak", "main", ReviewStatus.REVIEWING,
                        now - 7200, now - 3600, reviewers=[
                            Reviewer("Alice", ReviewStatus.APPROVED, True, 3, now - 5400),
                            Reviewer("Bob", ReviewStatus.REVIEWING, False, 1, now - 3600),
                        ], labels=["bug", "compositor", "critical"],
                        commits=3, additions=45, deletions=12, files_changed=4),
            PullRequest(43, "Add Wayland bridge protocol support",
                        "Implements wl_bridge protocol for cross-compositor communication. Enables running Nyrqis apps on other Wayland compositors.",
                        "contributor", "feature/wayland-bridge", "main", ReviewStatus.APPROVED,
                        now - 14400, now - 10800, now - 7200,
                        reviewers=[
                            Reviewer("Alice", ReviewStatus.APPROVED, True, 5, now - 10800),
                            Reviewer("Carol", ReviewStatus.APPROVED, True, 2, now - 9000),
                        ], labels=["feature", "wayland"],
                        commits=8, additions=892, deletions=34, files_changed=15),
            PullRequest(44, "Update security audit dependencies",
                        "Bumps openssl-sys to 0.9.82 and fixes CVE-2026-1234.",
                        "security-bot", "deps/security-audit", "main", ReviewStatus.CHANGES_REQUESTED,
                        now - 3600, now - 1800, reviewers=[
                            Reviewer("Alice", ReviewStatus.CHANGES_REQUESTED, False, 4, now - 1800),
                        ], labels=["security", "dependencies"],
                        commits=2, additions=8, deletions=8, files_changed=3),
        ]

        # File diffs for PR 42
        self._file_diffs = [
            FileDiff("rust/compositor/src/lib.rs", diff_type=DiffType.MODIFIED, additions=18, deletions=8,
                     lines=[
                         DiffLine(142, "    fn submit_frame(&mut self, surface_id: u32) -> i32 {", DiffType.UNCHANGED),
                         DiffLine(143, "        let surface = self.get_surface_mut(surface_id);", DiffType.UNCHANGED),
                         DiffLine(144, "        if surface.is_none() {", DiffType.UNCHANGED),
                         DiffLine(145, "            return -1;", DiffType.REMOVED),
                         DiffLine(145, "            return -1; // invalid surface", DiffType.ADDED),
                         DiffLine(146, "        }", DiffType.UNCHANGED),
                         DiffLine(147, "        let surface = surface.unwrap();", DiffType.UNCHANGED),
                         DiffLine(148, "", DiffType.UNCHANGED),
                        DiffLine(149, "        // Release SHM buffer after submission", DiffType.ADDED),
                        DiffLine(150, "        if surface.buffer_fd >= 0 {", DiffType.ADDED),
                        DiffLine(151, "            unsafe { libc::close(surface.buffer_fd); }", DiffType.ADDED),
                        DiffLine(152, "            surface.buffer_fd = -1;", DiffType.ADDED),
                        DiffLine(153, "        }", DiffType.ADDED),
                        DiffLine(154, "", DiffType.ADDED),
                         DiffLine(155, "        self.composite_surface(surface)", DiffType.UNCHANGED),
                         DiffLine(156, "    }", DiffType.UNCHANGED),
                     ],
                     comments=[
                         ReviewComment(1, "Alice", CommentType.LINE_COMMENT,
                                       "rust/compositor/src/lib.rs", 149, 153,
                                       "Good catch on the buffer leak. Should we also handle the case where close() fails?",
                                       now - 5400, severity=Severity.INFO,
                                       replies=[
                                           ReviewComment(2, "developer", CommentType.LINE_COMMENT,
                                                         body="Added error handling in the latest commit.", created_at=now - 4800),
                                       ]),
                     ],
                     suggestions=[
                         InlineSuggestion(149, "", "// Release SHM buffer after submission to prevent memory leak",
                                          "Buffer must be closed to avoid fd leak", True),
                     ]),
            FileDiff("rust/compositor/src/wayland/mod.rs", diff_type=DiffType.MODIFIED, additions=12, deletions=3),
            FileDiff("tests/test_compositor.py", diff_type=DiffType.MODIFIED, additions=15, deletions=1,
                     comments=[
                         ReviewComment(3, "Bob", CommentType.PRAISE,
                                       "tests/test_compositor.py", 0, 0,
                                       "Great test coverage! The memory leak regression test is exactly what we needed.",
                                       now - 3600, severity=Severity.INFO),
                     ]),
            FileDiff("CHANGELOG.md", diff_type=DiffType.MODIFIED, additions=5, deletions=0),
        ]

    @property
    def selected_pr(self) -> Optional[PullRequest]:
        if 0 <= self._selected_pr < len(self._prs):
            return self._prs[self._selected_pr]
        return None

    @property
    def total_prs(self) -> int:
        return len(self._prs)

    @property
    def open_prs(self) -> int:
        return sum(1 for p in self._prs if p.status not in (ReviewStatus.MERGED, ReviewStatus.CLOSED))

    def select_pr(self, idx: int):
        if 0 <= idx < len(self._prs):
            self._selected_pr = idx

    def approve_pr(self):
        pr = self.selected_pr
        if pr:
            pr.status = ReviewStatus.APPROVED
            self._history.append(f"Approved PR #{pr.id}")

    def request_changes(self):
        pr = self.selected_pr
        if pr:
            pr.status = ReviewStatus.CHANGES_REQUESTED
            self._history.append(f"Changes requested on PR #{pr.id}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "a":
            self.approve_pr()
        elif key == "c":
            self.request_changes()
        elif key == "s":
            self._show_suggestions = not self._show_suggestions

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CODE REVIEW                                        ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  PRs: {self.total_prs} ({self.open_prs} open)  Suggestions: {'ON' if self._show_suggestions else 'OFF'}")
        lines.append("")

        # PR list
        lines.append("  ── Pull Requests ──")
        for i, pr in enumerate(self._prs):
            sel = "▶" if i == self._selected_pr else " "
            labels = " ".join(f"[{l}]" for l in pr.labels[:3])
            lines.append(f"  {sel} {pr.status_icon} #{pr.id}  {pr.title[:50]}")
            lines.append(f"      {pr.author} → {pr.target_branch}  {pr.change_stats}  Approvals: {pr.approvals_str}  {labels}")
        lines.append("")

        # Selected PR
        pr = self.selected_pr
        if pr:
            lines.append(f"  ── PR #{pr.id}: {pr.title} ──")
            lines.append(f"  {pr.status.value}  {pr.author} → {pr.target_branch}  {pr.commits} commits  {pr.change_stats}")
            lines.append(f"  {pr.description[:70]}")
            lines.append("")

            # Reviewers
            if pr.reviewers:
                lines.append("  ── Reviewers ──")
                for r in pr.reviewers:
                    lines.append(f"  {r.status_icon} {r.name}  {r.comments_count} comments  {r.status.value}")
                lines.append("")

            # File diffs
            lines.append("  ── Files Changed ──")
            for i, fd in enumerate(self._file_diffs):
                sel = "▶" if i == self._selected_file else " "
                comment_icon = "💬" if fd.has_comments else "  "
                lines.append(f"  {sel} {fd.type_icon} {fd.file_path}  {fd.change_summary}  {comment_icon}")
            lines.append("")

            # Diff detail
            fd = self._file_diffs[self._selected_file] if self._selected_file < len(self._file_diffs) else None
            if fd:
                lines.append(f"  ── {fd.file_path} ──")
                for dl in fd.lines[:12]:
                    line = f"  {dl.prefix} {dl.line_num:>4d}  {dl.content[:60]}"
                    lines.append(line)
                if len(fd.lines) > 12:
                    lines.append(f"  ... ({len(fd.lines) - 12} more lines)")
                lines.append("")

                # Comments on this file
                if fd.comments:
                    lines.append("  ── Comments ──")
                    for c in fd.comments:
                        lines.append(f"  {c.type_icon} {c.severity_icon} {c.author} ({c.time_ago}): {c.body[:60]}")
                        for reply in c.replies:
                            lines.append(f"      ↳ {reply.author}: {reply.body[:55]}")
                    lines.append("")

                # Suggestions
                if self._show_suggestions and fd.suggestions:
                    lines.append("  ── Suggestions ──")
                    for s in fd.suggestions:
                        lines.append(f"  {s.status_icon} Line {s.line_num}: {s.reason[:50]}")
                    lines.append("")

        lines.append("  [A]pprove [C]hange Request [S]uggestions [↑↓]File [←→]PR")
        return lines
