from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DiffType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


class DiffMode(Enum):
    SIDE_BY_SIDE = "side-by-side"
    UNIFIED = "unified"
    INLINE = "inline"


class Highlight(Enum):
    WORD = "word"
    CHAR = "char"
    LINE = "line"
    NONE = "none"


@dataclass
class DiffLine:
    left: str
    right: str
    diff_type: DiffType
    left_num: int = 0
    right_num: int = 0


@dataclass
class DiffHunk:
    header: str
    lines: list
    start_left: int = 0
    start_right: int = 0


@dataclass
class DiffFile:
    name: str
    language: str
    hunks: list = field(default_factory=list)
    added: int = 0
    removed: int = 0
    modified: int = 0


@dataclass
class MergeConflict:
    file_name: str
    line_start: int
    ours: str
    theirs: str
    base: str = ""
    resolved: bool = False
    resolution: str = ""


class DiffTool:
    def __init__(self):
        self._files: list[DiffFile] = []
        self._selected: int = 0
        self._mode: DiffMode = DiffMode.SIDE_BY_SIDE
        self._highlight: Highlight = Highlight.WORD
        self._show_line_numbers: bool = True
        self._ignore_whitespace: bool = False
        self._ignore_case: bool = False
        self._conflicts: list[MergeConflict] = []
        self._base_text: str = ""
        self._left_text: str = ""
        self._right_text: str = ""
        self._create_samples()

    def _create_samples(self):
        # Sample diff: config file change
        hunks1 = [
            DiffHunk("@@ -1,8 +1,10 @@", [
                DiffLine("name = \"Nyrqis\"", "name = \"Nyrqis OS\"", DiffType.MODIFIED, 1, 1),
                DiffLine("version = \"1.0\"", "version = \"1.1\"", DiffType.MODIFIED, 2, 2),
                DiffLine("debug = false", "debug = true", DiffType.MODIFIED, 3, 3),
                DiffLine("", "", DiffType.UNCHANGED, 4, 4),
                DiffLine("kernel = \"6.11\"", "kernel = \"6.12\"", DiffType.MODIFIED, 5, 5),
                DiffLine("theme = \"dark\"", "theme = \"dark\"", DiffType.UNCHANGED, 6, 6),
                DiffLine("", "new_option = \"value\"", DiffType.ADDED, 0, 7),
                DiffLine("", "", DiffType.UNCHANGED, 7, 8),
                DiffLine("old_feature = true", "", DiffType.REMOVED, 8, 0),
            ], start_left=1, start_right=1),
        ]
        file1 = DiffFile("nyrqis.toml", "toml", hunks1, added=1, removed=1, modified=4)
        self._files.append(file1)

        # Sample diff: python file
        hunks2 = [
            DiffHunk("@@ -10,5 +10,7 @@", [
                DiffLine("def init():", "def init():", DiffType.UNCHANGED, 10, 10),
                DiffLine("    pass", "    setup_logging()", DiffType.MODIFIED, 11, 11),
                DiffLine("", "    load_plugins()", DiffType.ADDED, 0, 12),
                DiffLine("", "", DiffType.UNCHANGED, 12, 13),
                DiffLine("# old comment", "", DiffType.REMOVED, 13, 0),
            ], start_left=10, start_right=10),
        ]
        file2 = DiffFile("main.py", "python", hunks2, added=1, removed=1, modified=1)
        self._files.append(file2)

        # Sample diff: rust file
        hunks3 = [
            DiffHunk("@@ -1,6 +1,8 @@", [
                DiffLine("use std::io;", "use std::io;", DiffType.UNCHANGED, 1, 1),
                DiffLine("use std::fs;", "use std::fs;", DiffType.UNCHANGED, 2, 2),
                DiffLine("", "use std::collections::HashMap;", DiffType.ADDED, 0, 3),
                DiffLine("", "", DiffType.UNCHANGED, 3, 4),
                DiffLine("fn main() {", "fn main() {", DiffType.UNCHANGED, 4, 5),
                DiffLine("    println!(\"hello\");", "    println!(\"hello world\");", DiffType.MODIFIED, 5, 6),
                DiffLine("}", "}", DiffType.UNCHANGED, 6, 7),
            ], start_left=1, start_right=1),
        ]
        file3 = DiffFile("src/main.rs", "rust", hunks3, added=1, removed=0, modified=1)
        self._files.append(file3)

        # Merge conflicts
        self._conflicts = [
            MergeConflict("config.toml", 15, "timeout = 30", "timeout = 60", "timeout = 30"),
            MergeConflict("src/main.rs", 42, "fn process() {\n    handle_fast()", "fn process() {\n    handle_safe()", "fn process() {\n    handle_default()"),
        ]

    @property
    def selected_file(self) -> Optional[DiffFile]:
        if 0 <= self._selected < len(self._files):
            return self._files[self._selected]
        return None

    @property
    def total_changes(self) -> int:
        return sum(f.added + f.removed + f.modified for f in self._files)

    @property
    def total_conflicts(self) -> int:
        return len([c for c in self._conflicts if not c.resolved])

    def select(self, idx: int):
        if 0 <= idx < len(self._files):
            self._selected = idx

    def resolve_conflict(self, idx: int, resolution: str) -> bool:
        if 0 <= idx < len(self._conflicts):
            self._conflicts[idx].resolved = True
            self._conflicts[idx].resolution = resolution
            return True
        return False

    def merge_ours(self, idx: int) -> bool:
        return self.resolve_conflict(idx, "ours")

    def merge_theirs(self, idx: int) -> bool:
        return self.resolve_conflict(idx, "theirs")

    def merge_both(self, idx: int) -> bool:
        return self.resolve_conflict(idx, "both")

    def merge_base(self, idx: int) -> bool:
        return self.resolve_conflict(idx, "base")

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                       NYRQIS DIFF TOOL                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        mode_icons = {DiffMode.SIDE_BY_SIDE: "▐▐", DiffMode.UNIFIED: "══", DiffMode.INLINE: "──"}
        lines.append(f"  Mode: {mode_icons.get(self._mode, '')} {self._mode.value}  Highlight: {self._highlight.value}")
        lines.append(f"  Files: {len(self._files)}  Changes: {self.total_changes}  Conflicts: {self.total_conflicts}")
        lines.append("")
        for i, f in enumerate(self._files):
            sel = "▶" if i == self._selected else " "
            lang_icons = {"toml": "⚙️", "python": "🐍", "rust": "🦀", "javascript": "📜", "markdown": "📝"}
            icon = lang_icons.get(f.language, "📄")
            lines.append(f"  {sel} {icon} {f.name}")
            lines.append(f"    +{f.added} -{f.removed} ~{f.modified} ({f.language})")
        lines.append("")
        lines.append("  [N]ext mode  [W]hitespace  [L]ine numbers  [H]ighlight")
        return lines

    def render_file_diff(self, idx: int = -1) -> list:
        if idx < 0:
            idx = self._selected
        if idx >= len(self._files):
            return ["  No file selected"]
        f = self._files[idx]
        lines = []
        lines.append(f"  ── {f.name} ({f.language}) ── {self._mode.value} ──")
        lines.append("")
        for hunk in f.hunks:
            lines.append(f"  {hunk.header}")
            for dl in hunk.lines:
                type_colors = {"added": "+", "removed": "-", "modified": "~", "unchanged": " "}
                prefix = type_colors.get(dl.diff_type.value, " ")
                if self._mode == DiffMode.SIDE_BY_SIDE:
                    lnum = f"{dl.left_num:>4}" if dl.left_num else "    "
                    rnum = f"{dl.right_num:>4}" if dl.right_num else "    "
                    left_text = dl.left.ljust(35)[:35]
                    right_text = dl.right
                    lines.append(f"  {lnum} {prefix} {left_text}│ {rnum} {prefix} {right_text}")
                else:
                    if dl.diff_type == DiffType.REMOVED:
                        lines.append(f"  - {dl.left}")
                    elif dl.diff_type == DiffType.ADDED:
                        lines.append(f"  + {dl.right}")
                    elif dl.diff_type == DiffType.MODIFIED:
                        lines.append(f"  - {dl.left}")
                        lines.append(f"  + {dl.right}")
                    else:
                        lines.append(f"    {dl.left}")
        lines.append("")
        return lines

    def render_conflicts(self) -> list:
        lines = []
        lines.append("  ── Merge Conflicts ──")
        lines.append("")
        for i, c in enumerate(self._conflicts):
            status = "✅" if c.resolved else "⚠️"
            lines.append(f"  {status} {c.file_name}:{c.line_start}")
            if not c.resolved:
                lines.append(f"    Ours:   {c.ours[:50]}")
                lines.append(f"    Theirs: {c.theirs[:50]}")
                lines.append(f"    Base:   {c.base[:50]}")
            else:
                lines.append(f"    Resolution: {c.resolution}")
            lines.append("")
        return lines

    def compare(self, text_a: str, text_b: str):
        self._left_text = text_a
        self._right_text = text_b
        lines_a = text_a.split("\n")
        lines_b = text_b.split("\n")
        hunks = []
        current_hunk_lines = []
        hunk_start = 0
        for i, (a, b) in enumerate(zip(lines_a, lines_b)):
            if a == b:
                current_hunk_lines.append(DiffLine(a, b, DiffType.UNCHANGED, i + 1, i + 1))
            else:
                if not current_hunk_lines:
                    hunk_start = i
                current_hunk_lines.append(DiffLine(a, b, DiffType.MODIFIED, i + 1, i + 1))
        if current_hunk_lines:
            hunks.append(DiffHunk(f"@@ -{hunk_start + 1},{len(lines_a)} +{hunk_start + 1},{len(lines_b)} @@", current_hunk_lines, hunk_start + 1, hunk_start + 1))
        return hunks
