#!/usr/bin/env python3
"""Tests for the C++ code generator (tools/generate_cpp.py)."""

import os
import sys
import tempfile
import unittest

# Add tools to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.generate_cpp import (
    escape_cpp_string,
    generate_component,
    generate_document,
)


class TestEscapeCppString(unittest.TestCase):
    """String escaping for C++ literals."""

    def test_simple_string(self):
        self.assertEqual(escape_cpp_string("hello"), "hello")

    def test_quotes(self):
        self.assertEqual(escape_cpp_string('say "hi"'), 'say \\"hi\\"')

    def test_backslash(self):
        self.assertEqual(escape_cpp_string("path\\to"), "path\\\\to")

    def test_newline(self):
        self.assertEqual(escape_cpp_string("line1\nline2"), "line1\\nline2")

    def test_tab(self):
        self.assertEqual(escape_cpp_string("col1\tcol2"), "col1\\tcol2")


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
        self.assertIn('.id = "btn1"', code)
        self.assertIn('.type = "Button"', code)
        self.assertIn(".x = 10", code)
        self.assertIn('Property{"text", "Click"}', code)

    def test_component_with_children(self):
        comp = {
            "id": "box",
            "type": "Container",
            "layout": {"x": 0, "y": 0, "width": 200, "height": 100},
            "children": [
                {
                    "id": "child1",
                    "type": "Button",
                    "layout": {"x": 10, "y": 10, "width": 80, "height": 30},
                }
            ],
        }
        code = generate_component(comp)
        self.assertIn('.id = "box"', code)
        self.assertIn('.id = "child1"', code)
        self.assertIn('.type = "Button"', code)

    def test_component_with_events(self):
        comp = {
            "id": "btn",
            "type": "Button",
            "layout": {},
            "events": {"click": "handleClick"},
        }
        code = generate_component(comp)
        self.assertIn('Property{"click", "handleClick"}', code)

    def test_empty_component(self):
        comp = {"id": "empty", "type": "Text", "layout": {}}
        code = generate_component(comp)
        self.assertIn('.id = "empty"', code)
        self.assertIn(".properties = {},", code)
        self.assertIn(".children = {},", code)


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

    def test_header_guard(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("#pragma once", code)

    def test_includes(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("#include <vector>", code)
        self.assertIn("#include <string_view>", code)

    def test_namespace(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("namespace nyrqis::nui {", code)
        self.assertIn("}  // namespace nyrqis::nui", code)

    def test_structs_defined(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("struct Layout {", code)
        self.assertIn("struct Property {", code)
        self.assertIn("struct Component {", code)
        self.assertIn("struct Screen {", code)
        self.assertIn("struct Document {", code)

    def test_screen_factory(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("make_screen_main()", code)
        self.assertIn('.id = "main"', code)
        self.assertIn(".width = 800", code)
        self.assertIn(".height = 600", code)

    def test_document_constructor(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("inline Document load()", code)
        self.assertIn('name = "TestProject"', code)
        self.assertIn('version = "1.0.0"', code)

    def test_state_constants(self):
        code = generate_document(self._minimal_doc())
        self.assertIn("struct State {", code)
        self.assertIn('static constexpr auto theme = "Eclipse";', code)

    def test_multiple_screens(self):
        doc = self._minimal_doc()
        doc["screens"].append({
            "id": "lock",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "lock_root",
                "type": "LockScreen",
                "layout": {"x": 0, "y": 0, "width": 1920, "height": 1080},
            },
        })
        code = generate_document(doc)
        self.assertIn("make_screen_main()", code)
        self.assertIn("make_screen_lock()", code)


class TestGenerateCppWithFixture(unittest.TestCase):
    """Generate C++ from real .nstudio fixtures."""

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
        self.assertIn("nyrqis::nui", code)
        self.assertIn("make_screen_desktop", code)
        self.assertIn("make_screen_lock", code)
        # Should have all components
        self.assertGreater(code.count(".id ="), 30)

    def test_generate_to_file(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        import json
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)

        code = generate_document(doc)
        with tempfile.NamedTemporaryFile(suffix=".hpp", delete=False, mode="w") as f:
            f.write(code)
            out_path = f.name
        try:
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 1000)
            # Verify it's valid C++ (basic syntax checks)
            with open(out_path, "r") as f:
                content = f.read()
            self.assertIn("#pragma once", content)
            self.assertIn("namespace nyrqis::nui", content)
            self.assertIn("}  // namespace nyrqis::nui", content)
        finally:
            os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
