from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class SnippetLanguage(Enum):
    PYTHON = "python"
    RUST = "rust"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    BASH = "bash"
    SQL = "sql"
    HTML = "html"
    CSS = "css"
    YAML = "yaml"
    JSON = "json"
    TOML = "toml"
    MARKDOWN = "markdown"
    C = "c"
    CPP = "cpp"
    JAVA = "java"
    OTHER = "other"


class SnippetCategory(Enum):
    UTILITY = "utility"
    DATA_STRUCTURE = "data-structure"
    ALGORITHM = "algorithm"
    PATTERN = "pattern"
    COMMAND = "command"
    TEMPLATE = "template"
    SNIPPET = "snippet"
    CONFIG = "config"
    QUERY = "query"
    SHELL = "shell"
    OTHER = "other"


class SortMode(Enum):
    NAME = "name"
    LANGUAGE = "language"
    CREATED = "created"
    UPDATED = "updated"
    USES = "uses"
    FAVORITES = "favorites"


@dataclass
class Snippet:
    name: str
    code: str
    language: SnippetLanguage
    category: SnippetCategory
    description: str = ""
    tags: list = field(default_factory=list)
    shortcut: str = ""
    use_count: int = 0
    is_favorite: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    author: str = "user"

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = time.time()

    @property
    def preview(self) -> str:
        lines = self.code.split("\n")
        return lines[0][:60] if lines else ""

    @property
    def line_count(self) -> int:
        return len(self.code.split("\n"))

    @property
    def word_count(self) -> int:
        return len(self.code.split())

    @property
    def char_count(self) -> int:
        return len(self.code)

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.updated_at) / 86400)
        if age == 0:
            return "today"
        if age == 1:
            return "yesterday"
        return f"{age}d ago"


@dataclass
class Collection:
    name: str
    description: str
    snippet_ids: list = field(default_factory=list)
    is_public: bool = False
    created_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def count(self) -> int:
        return len(self.snippet_ids)


class SnippetLibrary:
    def __init__(self):
        self._snippets: list[Snippet] = []
        self._selected: int = 0
        self._collections: list[Collection] = []
        self._search_query: str = ""
        self._filter_language: Optional[SnippetLanguage] = None
        self._filter_category: Optional[SnippetCategory] = None
        self._sort_mode: SortMode = SortMode.UPDATED
        self._show_preview: bool = True
        self._view: str = "library"
        self._clipboard: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        samples = [
            Snippet("Fibonacci Generator", "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b\n\nprint(list(fibonacci(10)))", SnippetLanguage.PYTHON, SnippetCategory.ALGORITHM, "Generate Fibonacci sequence", ["math", "generator"], use_count=15, is_favorite=True, created_at=now - 86400 * 30),
            Snippet("HashMap with Default", "from collections import defaultdict\ndef defaultdict_factory():\n    return defaultdict(int)", SnippetLanguage.PYTHON, SnippetCategory.UTILITY, "Default dict factory function", ["collections"], use_count=8),
            Snippet("HTTP Server", 'use std::net::TcpListener;\nuse std::io::Read;\n\nfn main() {\n    let listener = TcpListener::bind("127.0.0.1:8080").unwrap();\n    for stream in listener.incoming() {\n        // handle connection\n    }\n}', SnippetLanguage.RUST, SnippetCategory.TEMPLATE, "Basic TCP server in Rust", ["network", "server"], use_count=22, is_favorite=True, created_at=now - 86400 * 15),
            Snippet("React Hook", 'import { useState, useEffect } from "react";\n\nexport function useFetch(url) {\n  const [data, setData] = useState(null);\n  const [loading, setLoading] = useState(true);\n  const [error, setError] = useState(null);\n\n  useEffect(() => {\n    fetch(url)\n      .then(res => res.json())\n      .then(setData)\n      .catch(setError)\n      .finally(() => setLoading(false));\n  }, [url]);\n\n  return { data, loading, error };\n}', SnippetLanguage.JAVASCRIPT, SnippetCategory.PATTERN, "Custom fetch hook for React", ["react", "hooks", "api"], use_count=45, is_favorite=True, created_at=now - 86400 * 7),
            Snippet("Find and Replace", 'sed -i \'s/old/new/g\' file.txt', SnippetLanguage.BASH, SnippetCategory.COMMAND, "In-place sed replacement", ["sed", "text"]),
            Snippet("JSON Schema Validator", 'const schema = {\n  type: "object",\n  properties: {\n    name: { type: "string" },\n    age: { type: "number" }\n  },\n  required: ["name"]\n};', SnippetLanguage.JSON, SnippetCategory.CONFIG, "Basic JSON schema template", ["schema", "validation"]),
            Snippet("Database Migration", 'CREATE TABLE users (\n    id SERIAL PRIMARY KEY,\n    username VARCHAR(50) UNIQUE NOT NULL,\n    email VARCHAR(100) UNIQUE NOT NULL,\n    created_at TIMESTAMP DEFAULT NOW(),\n    updated_at TIMESTAMP DEFAULT NOW()\n);', SnippetLanguage.SQL, SnippetCategory.QUERY, "Create users table migration", ["database", "migration"], use_count=12),
            Snippet("Docker Compose", 'version: "3.8"\nservices:\n  app:\n    build: .\n    ports:\n      - "8080:8080"\n    volumes:\n      - .:/app\n    environment:\n      - NODE_ENV=development', SnippetLanguage.YAML, SnippetCategory.CONFIG, "Development Docker Compose setup", ["docker", "devops"], use_count=30, is_favorite=True),
            Snippet("Go Error Handler", 'if err != nil {\n    return fmt.Errorf("operation failed: %w", err)\n}', SnippetLanguage.GO, SnippetCategory.PATTERN, "Go error wrapping pattern", ["error", "pattern"]),
            Snippet("Git Hooks", '#!/bin/bash\n# Pre-commit hook\ngit diff --cached --name-only | grep -q "\.py$"\nif [ $? -eq 0 ]; then\n    python -m pytest tests/ -v\nfi', SnippetLanguage.BASH, SnippetCategory.TEMPLATE, "Pre-commit hook for Python", ["git", "hooks"], use_count=18),
            Snippet("TypeScript Interface", 'interface User {\n  id: number;\n  name: string;\n  email: string;\n  roles: ("admin" | "user" | "guest")[];\n  createdAt: Date;\n}', SnippetLanguage.TYPESCRIPT, SnippetCategory.DATA_STRUCTURE, "User interface definition", ["typescript", "types"]),
            Snippet("CSS Grid Layout", '.grid-container {\n  display: grid;\n  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));\n  gap: 1rem;\n  padding: 1rem;\n}', SnippetLanguage.CSS, SnippetCategory.TEMPLATE, "Responsive CSS Grid layout", ["css", "layout"]),
        ]
        self._snippets = samples

        self._collections = [
            Collection("Favorites", "My most used snippets", [s.name for s in samples if s.is_favorite]),
            Collection("Python Utils", "Python utility snippets", ["Fibonacci Generator", "HashMap with Default"]),
            Collection("Web Dev", "Web development templates", ["React Hook", "CSS Grid Layout", "Docker Compose"]),
            Collection("DevOps", "DevOps and infrastructure", ["Docker Compose", "Git Hooks", "Find and Replace"]),
        ]

    @property
    def selected_snippet(self) -> Optional[Snippet]:
        if 0 <= self._selected < len(self._snippets):
            return self._snippets[self._selected]
        return None

    @property
    def total_snippets(self) -> int:
        return len(self._snippets)

    @property
    def favorites_count(self) -> int:
        return sum(1 for s in self._snippets if s.is_favorite)

    @property
    def total_uses(self) -> int:
        return sum(s.use_count for s in self._snippets)

    @property
    def languages_used(self) -> set:
        return set(s.language for s in self._snippets)

    def select(self, idx: int):
        if 0 <= idx < len(self._snippets):
            self._selected = idx

    def search(self, query: str) -> list:
        self._search_query = query
        return [s for s in self._snippets if query.lower() in s.name.lower() or query.lower() in s.code.lower() or query.lower() in s.description.lower() or query.lower() in " ".join(s.tags)]

    def filter_by_language(self, lang: SnippetLanguage):
        self._filter_language = lang

    def filter_by_category(self, cat: SnippetCategory):
        self._filter_category = cat

    def toggle_favorite(self):
        s = self.selected_snippet
        if s:
            s.is_favorite = not s.is_favorite

    def use_snippet(self, idx: int):
        if 0 <= idx < len(self._snippets):
            self._snippets[idx].use_count += 1
            self._clipboard = self._snippets[idx].code

    def add_snippet(self, snippet: Snippet):
        self._snippets.append(snippet)
        self._selected = len(self._snippets) - 1

    def delete_snippet(self, idx: int) -> bool:
        if 0 <= idx < len(self._snippets):
            self._snippets.pop(idx)
            if self._selected >= len(self._snippets):
                self._selected = max(0, len(self._snippets) - 1)
            return True
        return False

    def get_filtered(self) -> list:
        result = self._snippets
        if self._filter_language:
            result = [s for s in result if s.language == self._filter_language]
        if self._filter_category:
            result = [s for s in result if s.category == self._filter_category]
        return result

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CODE SNIPPET LIBRARY                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Snippets: {self.total_snippets}  ⭐ {self.favorites_count}  📋 {self.total_uses} uses  📂 {len(self.languages_used)} languages")
        lines.append("")
        filtered = self.get_filtered()
        for i, s in enumerate(filtered[:15]):
            sel = "▶" if i == self._selected else " "
            fav = "⭐" if s.is_favorite else " "
            lang_icons = {"python": "🐍", "rust": "🦀", "javascript": "📜", "typescript": "🔷", "go": "🐹", "bash": "🖥️", "sql": "💾", "html": "🌐", "css": "🎨", "yaml": "⚙️", "json": "📋", "toml": "🔧"}
            icon = lang_icons.get(s.language.value, "📄")
            lines.append(f"  {sel}{fav} {icon} {s.name}  [{s.language.value}]  {s.use_count} uses  {s.line_count} lines")
            lines.append(f"    {s.preview}")
        lines.append("")
        lines.append("  ── Collections ──")
        for c in self._collections:
            lines.append(f"  📁 {c.name} ({c.count})  {c.description}")
        lines.append("")
        lines.append("  [S]earch  [L]anguage  [C]ategory  [A]dd  [D]elete  [F]avorite  [U]se")
        return lines

    def render_snippet(self) -> list:
        s = self.selected_snippet
        if not s:
            return ["  No snippet selected"]
        lines = []
        lines.append(f"  ── {s.name} ({s.language.value}) ──")
        lines.append(f"  Category: {s.category.value}  Author: {s.author}")
        lines.append(f"  Description: {s.description}")
        lines.append(f"  Tags: {', '.join(s.tags) if s.tags else 'none'}")
        lines.append(f"  Uses: {s.use_count}  Lines: {s.line_count}  Words: {s.word_count}  Chars: {s.char_count}")
        lines.append(f"  Created: {time.strftime('%Y-%m-%d', time.localtime(s.created_at))}  Updated: {s.age_display}")
        if s.shortcut:
            lines.append(f"  Shortcut: {s.shortcut}")
        lines.append("")
        lines.append("  ── Code ──")
        for line in s.code.split("\n"):
            lines.append(f"  │ {line}")
        return lines
