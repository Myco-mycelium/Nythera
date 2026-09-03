from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class DiffMode(Enum):
    SIDE_BY_SIDE = "side-by-side"
    UNIFIED = "unified"
    INLINE = "inline"
    CONTEXT = "context"


class DiffLineType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    HEADER = "header"


class FileType(Enum):
    TOML = "toml"
    YAML = "yaml"
    JSON = "json"
    INI = "ini"
    CONF = "conf"
    ENV = "env"
    TEXT = "text"


@dataclass
class DiffLine:
    left_num: int
    right_num: int
    left_text: str
    right_text: str
    line_type: DiffLineType

    @property
    def icon(self) -> str:
        icons = {"added": "+", "removed": "-", "changed": "~", "unchanged": " ", "header": "@"}
        return icons.get(self.line_type.value, " ")


@dataclass
class DiffHunk:
    header: str
    lines: list = field(default_factory=list)
    start_left: int = 0
    start_right: int = 0

    @property
    def added(self) -> int:
        return sum(1 for l in self.lines if l.line_type == DiffLineType.ADDED)

    @property
    def removed(self) -> int:
        return sum(1 for l in self.lines if l.line_type == DiffLineType.REMOVED)


@dataclass
class DiffFile:
    name: str
    file_type: FileType
    old_content: str
    new_content: str
    hunks: list = field(default_factory=list)
    is_identical: bool = False
    timestamp: float = 0

    @property
    def total_changes(self) -> int:
        return sum(h.added + h.removed for h in self.hunks)


@dataclass
class DiffPreset:
    name: str
    left_label: str
    right_label: str
    description: str


class ConfigDiff:
    def __init__(self):
        self._files: list[DiffFile] = []
        self._selected_file: int = 0
        self._mode: DiffMode = DiffMode.SIDE_BY_SIDE
        self._show_line_numbers: bool = True
        self._ignore_whitespace: bool = False
        self._ignore_case: bool = False
        self._context_lines: int = 3
        self._presets: list[DiffPreset] = []
        self._selected_hunk: int = 0
        self._view: str = "diff"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._presets = [
            DiffPreset("Before/After Update", "v1.0", "v1.1", "Compare config before and after system update"),
            DiffPreset("Production vs Dev", "production", "development", "Compare production and development configs"),
            DiffPreset("Default vs Custom", "default", "custom", "Compare default and custom configurations"),
        ]

        hunks1 = [
            DiffHunk("@@ -1,10 +1,12 @@", [
                DiffLine(1, 1, "[compositor]", "[compositor]", DiffLineType.UNCHANGED),
                DiffLine(2, 2, "vsync = true", "vsync = true", DiffLineType.UNCHANGED),
                DiffLine(3, 3, "refresh_rate = 120", "refresh_rate = 144", DiffLineType.CHANGED),
                DiffLine(4, 4, "backend = \"vulkan\"", "backend = \"vulkan\"", DiffLineType.UNCHANGED),
                DiffLine(5, 5, "", "", DiffLineType.UNCHANGED),
                DiffLine(6, 6, "[render]", "[render]", DiffLineType.UNCHANGED),
                DiffLine(7, 7, "vsync_method = \"fifo\"", "vsync_method = \"mailbox\"", DiffLineType.CHANGED),
                DiffLine(0, 8, "", "triple_buffer = true", DiffLineType.ADDED),
            ], 1, 1),
        ]
        self._files.append(DiffFile("compositor.toml", FileType.TOML, "[compositor]\nvsync = true\nrefresh_rate = 120\nbackend = \"vulkan\"\n\n[render]\nvsync_method = \"fifo\"",
                                     "[compositor]\nvsync = true\nrefresh_rate = 144\nbackend = \"vulkan\"\n\n[render]\nvsync_method = \"mailbox\"\ntriple_buffer = true", hunks1, timestamp=now - 3600))

        hunks2 = [
            DiffHunk("@@ -1,8 +1,9 @@", [
                DiffLine(1, 1, "[shell]", "[shell]", DiffLineType.UNCHANGED),
                DiffLine(2, 2, "theme = \"dracula\"", "theme = \"nord\"", DiffLineType.CHANGED),
                DiffLine(3, 3, "font = \"JetBrains Mono\"", "font = \"JetBrains Mono\"", DiffLineType.UNCHANGED),
                DiffLine(4, 4, "font_size = 14", "font_size = 14", DiffLineType.UNCHANGED),
                DiffLine(5, 5, "", "", DiffLineType.UNCHANGED),
                DiffLine(6, 6, "[dock]", "[dock]", DiffLineType.UNCHANGED),
                DiffLine(7, 7, "position = \"bottom\"", "position = \"bottom\"", DiffLineType.UNCHANGED),
                DiffLine(8, 8, "auto_hide = false", "auto_hide = true", DiffLineType.CHANGED),
                DiffLine(0, 9, "", "show_icons = true", DiffLineType.ADDED),
            ], 1, 1),
        ]
        self._files.append(DiffFile("shell.toml", FileType.TOML, "[shell]\ntheme = \"dracula\"\nfont = \"JetBrains Mono\"\nfont_size = 14\n\n[dock]\nposition = \"bottom\"\nauto_hide = false",
                                     "[shell]\ntheme = \"nord\"\nfont = \"JetBrains Mono\"\nfont_size = 14\n\n[dock]\nposition = \"bottom\"\nauto_hide = true\nshow_icons = true", hunks2, timestamp=now - 7200))

        hunks3 = [
            DiffHunk("@@ -1,6 +1,6 @@", [
                DiffLine(1, 1, "LOG_LEVEL=info", "LOG_LEVEL=debug", DiffLineType.CHANGED),
                DiffLine(2, 2, "DB_HOST=localhost", "DB_HOST=localhost", DiffLineType.UNCHANGED),
                DiffLine(3, 3, "DB_PORT=5432", "DB_PORT=5432", DiffLineType.UNCHANGED),
                DiffLine(4, 4, "CACHE_TTL=3600", "CACHE_TTL=1800", DiffLineType.CHANGED),
                DiffLine(5, 5, "MAX_CONNECTIONS=100", "MAX_CONNECTIONS=200", DiffLineType.CHANGED),
                DiffLine(6, 6, "SECRET_KEY=old_key_here", "SECRET_KEY=new_secret_key_here", DiffLineType.CHANGED),
            ], 1, 1),
        ]
        self._files.append(DiffFile(".env", FileType.ENV, "LOG_LEVEL=info\nDB_HOST=localhost\nDB_PORT=5432\nCACHE_TTL=3600\nMAX_CONNECTIONS=100\nSECRET_KEY=old_key_here",
                                     "LOG_LEVEL=debug\nDB_HOST=localhost\nDB_PORT=5432\nCACHE_TTL=1800\nMAX_CONNECTIONS=200\nSECRET_KEY=new_secret_key_here", hunks3, timestamp=now - 1800))

    @property
    def selected_file(self) -> Optional[DiffFile]:
        if 0 <= self._selected_file < len(self._files):
            return self._files[self._selected_file]
        return None

    @property
    def total_changes(self) -> int:
        return sum(f.total_changes for f in self._files)

    @property
    def identical_files(self) -> int:
        return sum(1 for f in self._files if f.is_identical)

    def select_file(self, idx: int):
        if 0 <= idx < len(self._files):
            self._selected_file = idx

    def compare_texts(self, text_a: str, text_b: str) -> list:
        lines_a = text_a.split("\n")
        lines_b = text_b.split("\n")
        hunks = []
        current_lines = []
        start = 0
        max_lines = max(len(lines_a), len(lines_b))
        for i in range(max_lines):
            a = lines_a[i] if i < len(lines_a) else ""
            b = lines_b[i] if i < len(lines_b) else ""
            if a == b:
                current_lines.append(DiffLine(i + 1, i + 1, a, b, DiffLineType.UNCHANGED))
            else:
                if not current_lines:
                    start = i
                if a and b:
                    current_lines.append(DiffLine(i + 1, i + 1, a, b, DiffLineType.CHANGED))
                elif a:
                    current_lines.append(DiffLine(i + 1, 0, a, "", DiffLineType.REMOVED))
                else:
                    current_lines.append(DiffLine(0, i + 1, "", b, DiffLineType.ADDED))
        if current_lines:
            hunks.append(DiffHunk(f"@@ -1,{len(lines_a)} +1,{len(lines_b)} @@", current_lines, 1, 1))
        return hunks

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CONFIG DIFF TOOL                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Mode: {self._mode.value}  Context: {self._context_lines}  Line#: {'ON' if self._show_line_numbers else 'OFF'}  Ignore WS: {'ON' if self._ignore_whitespace else 'OFF'}")
        lines.append(f"  Files: {len(self._files)}  Changes: {self.total_changes}  Identical: {self.identical_files}")
        lines.append("")
        lines.append("  ── Files ──")
        for i, f in enumerate(self._files):
            sel = "▶" if i == self._selected_file else " "
            status = "✅" if f.is_identical else f"📝 {f.total_changes} changes"
            lines.append(f"  {sel} {f.name}  [{f.file_type.value}]  {status}  {f.age_display if hasattr(f, 'age_display') else ''}")
        lines.append("")
        lines.append("  ── Diff ──")
        f = self.selected_file
        if f:
            if self._mode == DiffMode.SIDE_BY_SIDE:
                for hunk in f.hunks:
                    lines.append(f"  {hunk.header}")
                    for dl in hunk.lines:
                        left = dl.left_text[:35] if dl.left_text else ""
                        right = dl.right_text[:35] if dl.right_text else ""
                        ln = f"{dl.left_num:>4}" if dl.left_num else "    "
                        rn = f"{dl.right_num:>4}" if dl.right_num else "    "
                        marker = dl.icon
                        lines.append(f"  {ln} {marker} {left:<35s}│ {rn} {marker} {right}")
            else:
                for hunk in f.hunks:
                    lines.append(f"  {hunk.header}")
                    for dl in hunk.lines:
                        if dl.line_type == DiffLineType.ADDED:
                            lines.append(f"  + {dl.right_text}")
                        elif dl.line_type == DiffLineType.REMOVED:
                            lines.append(f"  - {dl.left_text}")
                        elif dl.line_type == DiffLineType.CHANGED:
                            lines.append(f"  - {dl.left_text}")
                            lines.append(f"  + {dl.right_text}")
                        else:
                            lines.append(f"    {dl.left_text}")
        lines.append("")
        lines.append("  ── Presets ──")
        for p in self._presets:
            lines.append(f"  📋 {p.name}: {p.left_label} vs {p.right_label}")
        lines.append("")
        lines.append("  [M]ode  [N]umbers  [W]hitespace  [C]ontext  [I]nline  [S]ave  [E]xport")
        return lines

    def render_file_detail(self) -> list:
        f = self.selected_file
        if not f:
            return ["  No file selected"]
        lines = []
        lines.append(f"  ── {f.name} ({f.file_type.value}) ──")
        lines.append(f"  Changes: {f.total_changes}")
        lines.append(f"  Identical: {'Yes' if f.is_identical else 'No'}")
        lines.append("")
        lines.append("  Old content:")
        for line in f.old_content.split("\n"):
            lines.append(f"  │ {line}")
        lines.append("")
        lines.append("  New content:")
        for line in f.new_content.split("\n"):
            lines.append(f"  │ {line}")
        return lines
