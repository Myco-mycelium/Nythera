#!/usr/bin/env python3
"""Tests for the Rust code generator (tools/generate_rust.py)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.generate_rust import (
    escape_rust_string,
    generate_component,
    generate_document,
)


class TestEscapeRustString(unittest.TestCase):
    """String escaping for Rust literals."""

    def test_simple_string(self):
        self.assertEqual(escape_rust_string("hello"), "hello")

    def test_quotes(self):
        self.assertEqual(escape_rust_string('say "hi"'), 'say \\"hi\\"')

    def test_backslash(self):
        self.assertEqual(escape_rust_string("path\\to"), "path\\\\to")

    def test_newline(self):
        self.assertEqual(escape_rust_string("line1\nline2"), "line1\\nline2")


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
        self.assertIn('id: "btn1"', code)
        self.assertIn('component_type: "Button"', code)
        self.assertIn("x: 10f64", code)
        self.assertIn('("text", "Click")', code)

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
        self.assertIn('id: "box"', code)
        self.assertIn('id: "child1"', code)

    def test_component_with_events(self):
        comp = {
            "id": "btn",
            "type": "Button",
            "layout": {},
            "events": {"click": "handleClick"},
        }
        code = generate_component(comp)
        self.assertIn('("click", "handleClick")', code)

    def test_empty_component(self):
        comp = {"id": "empty", "type": "Text", "layout": {}}
        code = generate_component(comp)
        self.assertIn('id: "empty"', code)
        self.assertIn("properties: &[],", code)
        self.assertIn("children: &[],", code)


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

    def test_header(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("#![allow(dead_code", code)

    def test_structs_defined(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("pub struct Layout {", code)
        self.assertIn("pub struct Component<", code)
        self.assertIn("pub struct Screen<", code)
        self.assertIn("pub struct Document<", code)

    def test_screen_factory(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("pub fn screen_main()", code)
        self.assertIn('id: "main"', code)
        self.assertIn("width: 800f64", code)

    def test_document_constructor(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("pub fn load()", code)
        self.assertIn('name: "TestProject"', code)
        self.assertIn('version: "1.0.0"', code)

    def test_state_constants(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("pub mod state {", code)
        self.assertIn('pub const THEME: &str = "Eclipse";', code)

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
        self.assertIn("screen_main()", code)
        self.assertIn("screen_lock()", code)


class TestGenerateRustWithFixture(unittest.TestCase):
    """Generate Rust from real .nstudio fixtures."""

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

        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        self.assertIn("pub fn load()", code)
        self.assertIn("screen_desktop", code)
        self.assertIn("screen_lock", code)
        self.assertGreater(code.count('id: "'), 30)

    def test_generate_to_file(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        with tempfile.NamedTemporaryFile(suffix=".rs", delete=False, mode="w") as f:
            f.write(code)
            out_path = f.name
        try:
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 1000)
            with open(out_path, "r") as f:
                content = f.read()
            self.assertIn("#![allow(dead_code", content)
            self.assertIn("pub fn load()", content)
        finally:
            os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
