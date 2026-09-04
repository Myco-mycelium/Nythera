"""
Nyrqis OS - Shell Script Editor
Syntax highlighting, snippets, and debugger.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class ShellType(Enum):
    BASH = "bash"
    ZSH = "zsh"
    FISH = "fish"
    SH = "sh"
    POWERSHELL = "powershell"
    NUSH = "nush"


class TokenType(Enum):
    KEYWORD = "keyword"
    VARIABLE = "variable"
    STRING = "string"
    COMMENT = "comment"
    COMMAND = "command"
    OPERATOR = "operator"
    NUMBER = "number"
    PARAMETER = "parameter"
    FUNCTION = "function"
    REGEX = "regex"


@dataclass
class Token:
    text: str
    token_type: TokenType = TokenType.COMMAND
    line: int = 0
    column: int = 0

    @property
    def color(self) -> str:
        colors = {
            TokenType.KEYWORD: "#c678dd", TokenType.VARIABLE: "#e06c75",
            TokenType.STRING: "#98c379", TokenType.COMMENT: "#5c6370",
            TokenType.COMMAND: "#61afef", TokenType.OPERATOR: "#56b6c2",
            TokenType.NUMBER: "#d19a66", TokenType.PARAMETER: "#e5c07b",
            TokenType.FUNCTION: "#61afef", TokenType.REGEX: "#56b6c2",
        }
        return colors.get(self.token_type, "#abb2bf")


@dataclass
class ShellSnippet:
    name: str
    trigger: str = ""
    description: str = ""
    code: str = ""
    category: str = "General"
    shell: ShellType = ShellType.BASH
    use_count: int = 0

    @property
    def preview(self) -> str:
        return self.code[:80].replace("\n", " ")


@dataclass
class Breakpoint:
    line: int = 0
    enabled: bool = True
    condition: str = ""
    hit_count: int = 0
    log_message: str = ""


@dataclass
class DebugState:
    running: bool = False
    current_line: int = 0
    variables: Dict[str, str] = field(default_factory=list)
    call_stack: List[str] = field(default_factory=list)
    breakpoints: List[Breakpoint] = field(default_factory=list)
    output: List[str] = field(default_factory=list)
    watch_expressions: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.running:
            return "▶ Running"
        elif self.current_line > 0:
            return "⏸ Paused"
        return "⏹ Stopped"


@dataclass
class ShellDocument:
    name: str = ""
    content: str = ""
    file_path: str = ""
    shell_type: ShellType = ShellType.BASH
    modified: bool = False
    encoding: str = "utf-8"
    line_ending: str = "LF"
    word_wrap: bool = True
    tab_size: int = 4
    indent_with_spaces: bool = False

    @property
    def line_count(self) -> int:
        return len(self.content.split("\n"))

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    @property
    def cursor_position(self) -> str:
        return "1:1"


class ShellEditor:
    def __init__(self):
        self.documents: List[ShellDocument] = []
        self.current_document: Optional[ShellDocument] = None
        self.snippets: List[ShellSnippet] = []
        self.debug_state = DebugState()
        self.cursor_line: int = 1
        self.cursor_col: int = 1
        self.selection_start: Optional[Tuple[int, int]] = None
        self.selection_end: Optional[Tuple[int, int]] = None
        self.font_family: str = "JetBrains Mono"
        self.font_size: int = 14
        self.show_line_numbers: bool = True
        self.highlight_current_line: bool = True
        self.auto_indent: bool = True
        self.auto_complete: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        script1 = '''#!/usr/bin/env bash
# Nyrqis OS - Build Script
# Compiles and installs Nyrqis components

set -euo pipefail

NCLUDE_DIR="/opt/Nyrqis"
BUILD_DIR="${INCLUDE_DIR}/target"
LOG_FILE="/tmp/nyrqis-build.log"

echo "🔨 Building Nyrqis OS..."
cd "${INCLUDE_DIR}"

# Build compositor
echo "Building compositor..."
cargo build --release --bin nyrqis-compositor 2>&1 | tee -a "${LOG_FILE}"

# Build shell
echo "Building shell..."
cargo build --release --bin nyrqis-shell 2>&1 | tee -a "${LOG_FILE}"

# Install
echo "Installing..."
sudo cp "${BUILD_DIR}/release/nyrqis-compositor" /usr/bin/
sudo cp "${BUILD_DIR}/release/nyrqis-shell" /usr/bin/

echo "✅ Build complete!"
'''
        script2 = '''#!/usr/bin/env bash
# Health check script for Nyrqis services

check_service() {
    local name="$1"
    if systemctl is-active --quiet "${name}"; then
        echo "✅ ${name}: running"
    else
        echo "❌ ${name}: not running"
        return 1
    fi
}

check_port() {
    local port="$1"
    if ss -tln | grep -q ":${port}"; then
        echo "✅ Port ${port}: listening"
    else
        echo "❌ Port ${port}: not listening"
    fi
}

echo "Nyrqis OS Health Check"
echo "======================"

check_service "nyrqis-compositor"
check_service "nyrqis-shell"
check_service "NetworkManager"
check_port 22
check_port 80
'''
        self.documents = [
            ShellDocument(name="build.sh", content=script1,
                          file_path="/opt/Nyrqis/scripts/build.sh"),
            ShellDocument(name="healthcheck.sh", content=script2,
                          file_path="/usr/local/bin/healthcheck.sh"),
        ]
        self.current_document = self.documents[0]

        self.snippets = [
            ShellSnippet(name="Shebang", trigger="#!", code="#!/usr/bin/env bash",
                          description="Add shebang line", category="General"),
            ShellSnippet(name="If Statement", trigger="if",
                          code='if [[ "${1}" == "value" ]]; then\n    echo "yes"\nelse\n    echo "no"\nfi',
                          description="Conditional block", category="Control Flow"),
            ShellSnippet(name="For Loop", trigger="for",
                          code='for item in "${items[@]}"; do\n    echo "${item}"\ndone',
                          description="Iterate over items", category="Control Flow"),
            ShellSnippet(name="While Loop", trigger="while",
                          code='while [[ ${count} -lt 10 ]]; do\n    count=$((count + 1))\ndone',
                          description="While loop", category="Control Flow"),
            ShellSnippet(name="Function", trigger="fn",
                          code='function_name() {\n    local arg="$1"\n    echo "${arg}"\n}',
                          description="Define function", category="Functions"),
            ShellSnippet(name="Case Statement", trigger="case",
                          code='case "${option}" in\n    1) echo "one" ;;\n    2) echo "two" ;;\n    *) echo "other" ;;\nesac',
                          description="Pattern matching", category="Control Flow"),
            ShellSnippet(name="Read Input", trigger="read",
                          code='read -r -p "Enter value: " user_input',
                          description="Read user input", category="I/O"),
            ShellSnippet(name="Here Document", trigger="heredoc",
                          code='cat << EOF\nLine 1\nLine 2\nEOF',
                          description="Here document", category="I/O"),
            ShellSnippet(name="File Test", trigger="test",
                          code='if [[ -f "${file}" ]]; then\n    echo "File exists"\nfi',
                          description="Test file existence", category="Tests"),
            ShellSnippet(name="Exit Trap", trigger="trap",
                          code='cleanup() {\n    echo "Cleaning up..."\n}\ntrap cleanup EXIT',
                          description="Exit handler", category="Patterns"),
        ]

        self.debug_state = DebugState(
            breakpoints=[Breakpoint(line=10, enabled=True), Breakpoint(line=15, enabled=True)],
            variables={"BUILD_DIR": "/opt/Nyrqis/target", "LOG_FILE": "/tmp/nyrqis-build.log"},
            call_stack=["main", "build_compositor"],
            output=["[debug] Starting build...", "[debug] Checking dependencies..."],
            watch_expressions=["$?", "${#items[@]}"])

    def new_document(self, name: str = "", content: str = "",
                      shell_type: ShellType = ShellType.BASH) -> ShellDocument:
        doc = ShellDocument(name=name or f"untitled-{len(self.documents) + 1}",
                             content=content, shell_type=shell_type)
        self.documents.append(doc)
        return doc

    def close_document(self, name: str) -> bool:
        for i, d in enumerate(self.documents):
            if d.name == name:
                del self.documents[i]
                if self.current_document and self.current_document.name == name:
                    self.current_document = self.documents[0] if self.documents else None
                return True
        return False

    def insert_text(self, text: str) -> bool:
        if self.current_document:
            lines = self.current_document.content.split("\n")
            if self.cursor_line <= len(lines):
                line = lines[self.cursor_line - 1]
                lines[self.cursor_line - 1] = line[:self.cursor_col - 1] + text + line[self.cursor_col - 1:]
                self.current_document.content = "\n".join(lines)
                self.current_document.modified = True
                return True
        return False

    def insert_snippet(self, snippet_name: str) -> bool:
        snippet = next((s for s in self.snippets if s.name == snippet_name), None)
        if snippet:
            snippet.use_count += 1
            self.insert_text(snippet.code)
            return True
        return False

    def goto_line(self, line: int) -> bool:
        if self.current_document:
            max_line = self.current_document.line_count
            if 1 <= line <= max_line:
                self.cursor_line = line
                self.cursor_col = 1
                return True
        return False

    def search(self, query: str) -> List[Tuple[int, str]]:
        if not self.current_document:
            return []
        results = []
        for i, line in enumerate(self.current_document.content.split("\n"), 1):
            if query.lower() in line.lower():
                results.append((i, line.strip()))
        return results

    def toggle_breakpoint(self, line: int) -> bool:
        bp = next((b for b in self.debug_state.breakpoints if b.line == line), None)
        if bp:
            bp.enabled = not bp.enabled
        else:
            self.debug_state.breakpoints.append(Breakpoint(line=line))
        return True

    def start_debug(self) -> bool:
        self.debug_state.running = True
        self.debug_state.current_line = 1
        return True

    def step_over(self) -> bool:
        if self.debug_state.running:
            self.debug_state.current_line += 1
            return True
        return False

    def stop_debug(self) -> bool:
        self.debug_state.running = False
        self.debug_state.current_line = 0
        return True

    def get_stats(self) -> Dict:
        return {
            "documents": len(self.documents),
            "snippets": len(self.snippets),
            "breakpoints": len(self.debug_state.breakpoints),
            "total_lines": sum(d.line_count for d in self.documents),
        }
