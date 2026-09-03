"""Git GUI Client — Branch management, diff viewer, and merge conflict resolution.

Features:
- Commit history graph with branches and merges
- Branch management: create, switch, delete, merge, rebase
- Diff viewer: unified, split, and file-level diffs
- Staging area with partial staging
- Remote tracking and push/pull operations
- Merge conflict resolution with side-by-side view
- Tag management
- Stash support
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class CommitType(Enum):
    REGULAR = "regular"
    MERGE = "merge"
    REVERT = "revert"
    INITIAL = "initial"
    TAG = "tag"


class DiffLineType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"
    HEADER = "header"
    HUNK = "hunk"


class ConflictSide(Enum):
    MINE = "mine"
    THEIRS = "theirs"
    BASE = "base"


@dataclass
class DiffLine:
    line_type: DiffLineType
    content: str = ""
    old_line: int = 0
    new_line: int = 0

    @property
    def prefix(self) -> str:
        prefixes = {
            DiffLineType.ADDED: "+",
            DiffLineType.REMOVED: "-",
            DiffLineType.CONTEXT: " ",
            DiffLineType.HEADER: "@",
            DiffLineType.HUNK: "@",
        }
        return prefixes.get(self.line_type, " ")

    @property
    def color_hint(self) -> str:
        hints = {
            DiffLineType.ADDED: "green",
            DiffLineType.REMOVED: "red",
            DiffLineType.CONTEXT: "white",
            DiffLineType.HEADER: "cyan",
            DiffLineType.HUNK: "cyan",
        }
        return hints.get(self.line_type, "white")


@dataclass
class DiffHunk:
    header: str = ""
    lines: List[DiffLine] = field(default_factory=list)
    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0

    @property
    def added_count(self) -> int:
        return sum(1 for l in self.lines if l.line_type == DiffLineType.ADDED)

    @property
    def removed_count(self) -> int:
        return sum(1 for l in self.lines if l.line_type == DiffLineType.REMOVED)


@dataclass
class DiffFile:
    path: str = ""
    old_path: str = ""
    status: str = "modified"  # added, modified, deleted, renamed
    hunks: List[DiffHunk] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {"added": "A", "modified": "M", "deleted": "D", "renamed": "R"}
        return icons.get(self.status, "?")


@dataclass
class GitCommit:
    sha: str = ""
    short_sha: str = ""
    message: str = ""
    author: str = ""
    email: str = ""
    timestamp: float = 0.0
    branch: str = "main"
    parents: List[str] = field(default_factory=list)
    commit_type: CommitType = CommitType.REGULAR
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    tags: List[str] = field(default_factory=list)

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def relative_time(self) -> str:
        ago = time.time() - self.timestamp
        if ago < 60:
            return "just now"
        if ago < 3600:
            return f"{ago / 60:.0f}m ago"
        if ago < 86400:
            return f"{ago / 3600:.1f}h ago"
        return f"{ago / 86400:.0f}d ago"

    @property
    def type_icon(self) -> str:
        icons = {
            CommitType.REGULAR: "●",
            CommitType.MERGE: "⚡",
            CommitType.REVERT: "↩",
            CommitType.INITIAL: "★",
            CommitType.TAG: "🏷",
        }
        return icons.get(self.commit_type, "●")

    @property
    def stats_str(self) -> str:
        parts = []
        if self.files_changed:
            parts.append(f"{self.files_changed} files")
        if self.insertions:
            parts.append(f"+{self.insertions}")
        if self.deletions:
            parts.append(f"-{self.deletions}")
        return " ".join(parts)


@dataclass
class GitBranch:
    name: str = ""
    is_current: bool = False
    is_remote: bool = False
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    last_commit: Optional[GitCommit] = None
    protected: bool = False

    @property
    def display_name(self) -> str:
        prefix = "  "
        if self.is_current:
            prefix = "* "
        elif self.is_remote:
            prefix = "  remotes/"
        return f"{prefix}{self.name}"

    @property
    def status_str(self) -> str:
        parts = []
        if self.ahead > 0:
            parts.append(f"↑{self.ahead}")
        if self.behind > 0:
            parts.append(f"↓{self.behind}")
        return " ".join(parts) if parts else ""

    @property
    def protection_icon(self) -> str:
        return "🔒" if self.protected else ""


@dataclass
class StagedFile:
    path: str = ""
    status: str = "M"
    staged: bool = True
    hunks: List[DiffHunk] = field(default_factory=list)

    @property
    def status_color(self) -> str:
        colors = {"A": "green", "M": "yellow", "D": "red", "R": "blue", "?": "white"}
        return colors.get(self.status, "white")


@dataclass
class ConflictFile:
    path: str = ""
    mine_lines: List[str] = field(default_factory=list)
    their_lines: List[str] = field(default_factory=list)
    base_lines: List[str] = field(default_factory=list)
    resolved: bool = False
    resolution: str = ""  # mine, theirs, both, manual

    @property
    def status_icon(self) -> str:
        if self.resolved:
            return "✅"
        return "⚠️"

    @property
    def conflict_count(self) -> int:
        return sum(1 for l in self.mine_lines if l.startswith("<<<<<<<"))


@dataclass
class GitTag:
    name: str = ""
    sha: str = ""
    message: str = ""
    annotated: bool = False
    timestamp: float = 0.0


@dataclass
class GitStash:
    message: str = ""
    branch: str = ""
    timestamp: float = 0.0
    files_changed: int = 0

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))


class GitClient:
    def __init__(self):
        self._commits: List[GitCommit] = []
        self._branches: List[GitBranch] = []
        self._staged_files: List[StagedFile] = []
        self._conflicts: List[ConflictFile] = []
        self._tags: List[GitTag] = []
        self._stashes: List[GitStash] = []
        self._selected_commit: int = 0
        self._selected_file: int = 0
        self._view_mode: str = "log"  # log, branches, staging, conflicts, tags, stash, diff
        self._diff_mode: str = "unified"  # unified, split, stats
        self._show_graph: bool = True
        self._current_branch: str = "main"
        self._remote_url: str = "https://github.com/Myco-mycelium/Nythera.git"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        authors = [
            ("Buffy", "buffy@nyrqis.dev"),
            ("Nyx", "nyx@nyrqis.dev"),
            ("CoBot", "bot@nyrqis.dev"),
        ]

        # Commits
        commit_data = [
            ("a1b2c3d", "Add workflow automation builder, font manager, and network topology mapper", "Buffy", "main", 1311, 0, CommitType.MERGE, ["x0y1z2w"]),
            ("b2c3d4e", "Add REST API tester, mind map editor, and system journal viewer", "Buffy", "main", 1500, 100, CommitType.REGULAR, ["a1b2c3d"]),
            ("c3d4e5f", "Add disk analyzer, markdown wiki, and cloud storage manager", "Nyx", "main", 1200, 50, CommitType.REGULAR, ["b2c3d4e"]),
            ("d4e5f6g", "Add 3D scene editor, code review tool, and music streaming server", "Buffy", "main", 1400, 0, CommitType.REGULAR, ["c3d4e5f"]),
            ("e5f6g7h", "Add CI/CD pipeline visualizer, PDF editor, and home automation dashboard", "CoBot", "main", 1100, 80, CommitType.REGULAR, ["d4e5f6g"]),
            ("f6g7h8i", "Add firewall manager, video editor, and container manager", "Buffy", "main", 1300, 30, CommitType.REGULAR, ["e5f6g7h"]),
            ("g7h8i9j", "Add backend abstraction layer, vector editor, terminal mux, DB client", "Nyx", "main", 1600, 100, CommitType.REGULAR, ["f6g7h8i"]),
            ("h8i9j0k", "Add shell renderer, DAW audio workstation, and packet analyzer", "CoBot", "main", 1800, 200, CommitType.REGULAR, ["g7h8i9j"]),
            ("i9j0k1l", "Add synthesizer pro, config diff tool, and puzzle solver", "Buffy", "main", 2000, 100, CommitType.REGULAR, ["h8i9j0k"]),
            ("j0k1l2m", "Initial Nyrqis OS project setup", "Buffy", "main", 500, 0, CommitType.INITIAL, []),
            ("x0y1z2w", "Merge feature/ui-modules into main", "Buffy", "main", 0, 0, CommitType.MERGE, ["i9j0k1l", "k1l2m3n"]),
            ("k1l2m3n", "Implement GPU HAL with Vulkan/EGL/GBM backends", "Nyx", "feature/ui-modules", 3000, 200, CommitType.REGULAR, ["l2m3n4o"]),
            ("l2m3n4o", "Wire DRM crate to real ioctl for atomic modesetting", "Nyx", "feature/ui-modules", 800, 100, CommitType.REGULAR, []),
        ]

        for i, (sha, msg, author, branch, ins, deletions, ctype, parents) in enumerate(commit_data):
            email = next(e for n, e in authors if n == author)
            self._commits.append(GitCommit(
                sha=sha + "abcdef" * 2,
                short_sha=sha,
                message=msg,
                author=author,
                email=email,
                timestamp=now - random.uniform(3600, 86400 * 14),
                branch=branch,
                parents=parents,
                commit_type=ctype,
                files_changed=random.randint(1, 10),
                insertions=ins,
                deletions=deletions,
            ))

        # Branches
        self._branches = [
            GitBranch("main", is_current=True, upstream="origin/main", ahead=0, behind=0, protected=True),
            GitBranch("feature/ui-modules", upstream="origin/feature/ui-modules", ahead=0, behind=0),
            GitBranch("feature/vulkan-renderer", upstream="origin/feature/vulkan-renderer", ahead=3, behind=0),
            GitBranch("bugfix/wayland-bridge", upstream="origin/bugfix/wayland-bridge", ahead=0, behind=2),
            GitBranch("release/v1.0", ahead=0, behind=0),
            GitBranch("origin/main", is_remote=True),
            GitBranch("origin/feature/ui-modules", is_remote=True),
        ]

        # Staged files
        self._staged_files = [
            StagedFile("ui/new_app.py", "A", staged=True),
            StagedFile("ui/existing.py", "M", staged=True),
            StagedFile("tests/test_new.py", "A", staged=False),
            StagedFile("docs/changelog.md", "M", staged=True),
            StagedFile("config/settings.toml", "D", staged=False),
        ]

        # Conflicts
        self._conflicts = [
            ConflictFile(
                path="ui/compositor.py",
                mine_lines=[
                    "<<<<<<< HEAD",
                    "    def render_frame(self) -> Frame:",
                    "        return self.vulkan_renderer.render()",
                    "=======",
                    "    def render_frame(self, flags: int = 0) -> Frame:",
                    "        return self.vulkan_renderer.render(flags)",
                    ">>>>>>> feature/vulkan-renderer",
                ],
                their_lines=[],
                base_lines=[],
            ),
            ConflictFile(
                path="Cargo.toml",
                mine_lines=[
                    'vulkan = "0.28"',
                    "<<<<<<< HEAD",
                    'wayland = "1.21"',
                    "=======",
                    'wayland = "1.22"',
                    ">>>>>>> feature/vulkan-renderer",
                ],
            ),
        ]

        # Tags
        self._tags = [
            GitTag("v0.1.0", "j0k1l2m", "Initial development release", True, now - 86400 * 30),
            GitTag("v1.0.0-rc1", "i9j0k1l", "First release candidate", True, now - 86400 * 7),
            GitTag("v1.0.0-rc2", "a1b2c3d", "Second release candidate", True, now - 3600 * 12),
        ]

        # Stashes
        self._stashes = [
            GitStash("WIP: try alternative GPU sync", "feature/vulkan-renderer", now - 7200, 3),
            GitStash("Debug: add frame timing logs", "main", now - 86400, 5),
        ]

    @property
    def current_branch_name(self) -> str:
        return self._current_branch

    @property
    def selected_commit(self) -> Optional[GitCommit]:
        if 0 <= self._selected_commit < len(self._commits):
            return self._commits[self._selected_commit]
        return None

    def select_commit(self, idx: int):
        if 0 <= idx < len(self._commits):
            self._selected_commit = idx

    def select_file(self, idx: int):
        if 0 <= idx < len(self._staged_files):
            self._selected_file = idx

    def set_view(self, mode: str):
        if mode in ("log", "branches", "staging", "conflicts", "tags", "stash", "diff"):
            self._view_mode = mode

    def toggle_graph(self):
        self._show_graph = not self._show_graph

    def toggle_staged(self, idx: int):
        if 0 <= idx < len(self._staged_files):
            self._staged_files[idx].staged = not self._staged_files[idx].staged

    def resolve_conflict(self, idx: int, resolution: str):
        if 0 <= idx < len(self._conflicts):
            self._conflicts[idx].resolved = True
            self._conflicts[idx].resolution = resolution

    @property
    def total_commits(self) -> int:
        return len(self._commits)

    @property
    def total_branches(self) -> int:
        return sum(1 for b in self._branches if not b.is_remote)

    @property
    def staged_count(self) -> int:
        return sum(1 for f in self._staged_files if f.staged)

    @property
    def conflict_count(self) -> int:
        return sum(1 for c in self._conflicts if not c.resolved)

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS GIT CLIENT                                      ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🔀 {self._current_branch}  📡 {self._remote_url.split('/')[-2]}/{self._remote_url.split('/')[-1]}  📝 {self.total_commits} commits  🏷 {len(self._tags)} tags")
        lines.append("")

        if self._view_mode == "log":
            lines.append("  ── Commit Log ──")
            for i, commit in enumerate(self._commits[:15]):
                sel = "▶" if i == self._selected_commit else " "
                graph = "│ " if self._show_graph else ""
                if commit.commit_type == CommitType.MERGE:
                    graph = "⚡ " if self._show_graph else ""
                lines.append(f"  {sel}{graph}{commit.short_sha} {commit.message[:50]}")
                lines.append(f"    {commit.author} {commit.relative_time} {commit.stats_str} {commit.branch}")

        elif self._view_mode == "branches":
            lines.append("  ── Branches ──")
            for b in self._branches:
                status = f"  {b.status_str}" if b.status_str else ""
                prot = b.protection_icon
                lines.append(f"  {b.display_name}{prot}{status}")
                if b.last_commit:
                    lines.append(f"    └─ {b.last_commit.short_sha} {b.last_commit.relative_time}")

        elif self._view_mode == "staging":
            lines.append("  ── Staging Area ──")
            lines.append(f"  Staged: {self.staged_count}/{len(self._staged_files)}")
            for i, sf in enumerate(self._staged_files):
                sel = "▶" if i == self._selected_file else " "
                staged = "●" if sf.staged else "○"
                lines.append(f"  {sel} {staged} [{sf.status}] {sf.path}")

        elif self._view_mode == "conflicts":
            lines.append("  ── Merge Conflicts ──")
            lines.append(f"  ⚠️ {self.conflict_count} unresolved")
            for i, cf in enumerate(self._conflicts):
                lines.append(f"  {cf.status_icon} {cf.path}")
                if not cf.resolved:
                    for line in cf.mine_lines[:6]:
                        lines.append(f"      {line}")
                    lines.append("      [M]ine [T]heirs [B]oth [R]esolve")

        elif self._view_mode == "tags":
            lines.append("  ── Tags ──")
            for tag in self._tags:
                ann = "📝" if tag.annotated else "📌"
                lines.append(f"  {ann} {tag.name} ({tag.sha}) {tag.message}")
                lines.append(f"      {time.strftime('%Y-%m-%d', time.localtime(tag.timestamp))}")

        elif self._view_mode == "stash":
            lines.append("  ── Stash List ──")
            for i, stash in enumerate(self._stashes):
                lines.append(f"  📦 stash@{{{i}}}: {stash.message}")
                lines.append(f"      Branch: {stash.branch}  {stash.time_str}  {stash.files_changed} files")

        elif self._view_mode == "diff":
            lines.append("  ── Diff Viewer ──")
            commit = self.selected_commit
            if commit:
                lines.append(f"  {commit.short_sha}: {commit.message}")
                lines.append(f"  Author: {commit.author}  Files: {commit.files_changed}  +{commit.insertions} -{commit.deletions}")
                lines.append("")
                # Simulated diff
                lines.append("  @@ -10,6 +10,8 @@ class Compositor:")
                lines.append("     def render(self):")
                lines.append("  +    # New: GPU-accelerated rendering")
                lines.append("  +    self.vulkan.begin_frame()")
                lines.append("      self.composite_layers()")
                lines.append("  +    self.vulkan.end_frame()")
                lines.append("      return frame")

        lines.append("")
        lines.append("  [L]og [B]ranches [S]taging [C]onflicts [T]ags [H] stash [D]iff [↑↓]Nav [G]raph")
        return lines
