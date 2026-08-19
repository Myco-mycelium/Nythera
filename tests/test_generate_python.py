#!/usr/bin/env python3
"""Tests for the Python code generator (tools/generate_python.py)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.generate_python import (
    escape_py_string,
    generate_component,
    generate_document,
)


class TestEscapePyString(unittest.TestCase):
    """String escaping for Python literals."""

    def test_simple_string(self):
        self.assertEqual(escape_py_string("hello"), "hello")

    def test_quotes(self):
        self.assertEqual(escape_py_string('say "hi"'), 'say \\"hi\\"')

    def test_backslash(self):
        self.assertEqual(escape_py_string("path\\to"), "path\\\\to")

    def test_newline(self):
        self.assertEqual(escape_py_string("line1\nline2"), "line1\\nline2")

    def test_tab(self):
        self.assertEqual(escape_py_string("col1\tcol2"), "col1\\tcol2")


class TestGenerateComponent(unittest.TestCase):
    """Component code generation."""

    def test_simple_component(self):
        comp = {
            "id": "btn1",
            "type": "Button",
            "layout": {"x": 10, "y": 20, "width": 100, "height": 30},
            "properties": {"text": "Click"},
        }
        code = generate_component(comp)
        self.assertIn('id="btn1"', code)
        self.assertIn('type="Button"', code)
        self.assertIn("x=10", code)
        self.assertIn('"text": "Click"', code)

    def test_component_with_children(self):
        comp = {
            "id": "box",
            "type": "Container",
            "layout": {},
            "children": [
                {
                    "id": "child1",
                    "type": "Button",
                    "layout": {},
                }
            ],
        }
        code = generate_component(comp)
        self.assertIn('id="box"', code)
        self.assertIn('id="child1"', code)

    def test_component_with_events(self):
        comp = {
            "id": "btn",
            "type": "Button",
            "layout": {},
            "events": {"click": "handleClick"},
        }
        code = generate_component(comp)
        self.assertIn('"click": "handleClick"', code)

    def test_empty_component(self):
        comp = {"id": "empty", "type": "Text", "layout": {}}
        code = generate_component(comp)
        self.assertIn('id="empty"', code)
        self.assertIn("properties={}", code)
        self.assertIn("children=[]", code)


class TestGenerateDocument(unittest.TestCase):
    """Full document code generation."""

    def _minimal_doc(self):
        return {
            "version": "1.0.0",
            "project": {"name": "TestProject"},
            "states": {"theme": "Eclipse"},
            "screens": [
                {
                    "id": "main",
                    "size": {"width": 800, "height": 600},
                    "root": {
                        "id": "root",
                        "type": "Window",
                        "layout": {"x": 0, "y": 0, "width": 800, "height": 600},
                        "children": [
                            {
                                "id": "btn",
                                "type": "Button",
                                "layout": {"x": 10, "y": 10, "width": 120, "height": 36},
                                "properties": {"text": "OK"},
                            }
                        ],
                    },
                }
            ],
        }

    def test_module_docstring(self):
        code = generate_document(self._minimal_doc())
        self.assertIn('"""', code)
        self.assertIn("TestProject", code)

    def test_imports(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("from dataclasses import dataclass", code)
        self.assertIn("from typing import Any, Dict, List", code)

    def test_dataclasses(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("@dataclass(frozen=True)", code)
        self.assertIn("class Layout:", code)
        self.assertIn("class Component:", code)
        self.assertIn("class Screen:", code)
        self.assertIn("class Document:", code)

    def test_screen_factory(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("def make_screen_main()", code)
        self.assertIn('id="main"', code)
        self.assertIn("width=800", code)
        self.assertIn("height=600", code)

    def test_document_constructor(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("def load() -> Document:", code)
        self.assertIn('name="TestProject"', code)
        self.assertIn('version="1.0.0"', code)

    def test_state_constants(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("class State:", code)
        self.assertIn('theme: str = "Eclipse"', code)
        self.assertIn("STATE = State()", code)

    def test_multiple_screens(self):
        doc = self._minimal_doc()
        doc["screens"].append({
            "id": "lock",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "lock_root",
                "type": "LockScreen",
                "layout": {},
            },
        })
        code = generate_document(doc)
        self.assertIn("make_screen_main()", code)
        self.assertIn("make_screen_lock()", code)


class TestGeneratePythonWithFixture(unittest.TestCase):
    """Generate Python from real .nstudio fixtures."""

    def _find_fixture(self, name):
        base = os.path.dirname(__file__)
        for candidate in [
            os.path.join(base, "fixtures", "nstudio", name),
            os.path.join(base, "..", "source", "nyhal-linux-backend", "tests", "fixtures", "nstudio", name),
            os.path.join(base, "..", "source", "nyhal-linux-backend", "tests", "fixtures", name),
        ]:
            if os.path.exists(candidate):
                return candidate
        return None

    def test_generate_from_fixture(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        import json
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        self.assertIn("def load()", code)
        self.assertIn("make_screen_desktop", code)
        self.assertIn("make_screen_lock", code)

    def test_generated_module_loads(self):
        """The generated Python module should be importable and loadable."""
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        import json
        import importlib
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write(code)
            out_path = f.name
        try:
            # Import the generated module
            import importlib.util
            import types
            spec = importlib.util.spec_from_file_location("gen_module", out_path)
            mod = types.ModuleType("gen_module")
            mod.__file__ = out_path
            sys.modules["gen_module"] = mod
            spec.loader.exec_module(mod)

            # Call load()
            loaded = mod.load()
            self.assertEqual(loaded.version, "1.0.0")
            self.assertEqual(loaded.name, "Nyrqis Desktop Shell")
            self.assertEqual(len(loaded.screens), 2)
            self.assertEqual(loaded.screens[0].id, "desktop")
            self.assertEqual(loaded.screens[1].id, "lock")
        finally:
            os.unlink(out_path)

    def test_generate_to_file(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        import json
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write(code)
            out_path = f.name
        try:
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 1000)
        finally:
            os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
