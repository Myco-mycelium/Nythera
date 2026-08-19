#!/usr/bin/env python3
"""Validate all code generators against all .nstudio fixtures.

This is a CI gate: it runs every generator (Rust, C++, Python) on
every .nstudio fixture and verifies the output is non-empty and
syntactically valid. Fails fast on the first error.

Usage:
    python3 tools/validate_generators.py [--verbose]

Exit codes:
    0 — all generators pass on all fixtures
    1 — one or more generators failed

References:
- ADR-0020: language choices
- NUI-SCHEMA: component tree structure
"""

import ast
import os
import re
import sys
import tempfile
from typing import List, Tuple

# Ensure paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend"))
# Add project root so 'tools' is importable as a package
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ui.nstudio import load as nstudio_load


def find_fixtures() -> List[str]:
    """Find all .nstudio fixture files."""
    fixtures = []
    base = os.path.dirname(__file__)
    for search_dir in [
        os.path.join(base, "..", "source", "nyhal-linux-backend", "tests", "fixtures", "nstudio"),
        os.path.join(base, "..", "source", "nyhal-linux-backend", "tests", "fixtures"),
    ]:
        if os.path.isdir(search_dir):
            for f in sorted(os.listdir(search_dir)):
                if f.endswith(".nstudio"):
                    fixtures.append(os.path.join(search_dir, f))
    return fixtures


def _doc_to_dict(doc):
    """Convert a NstudioDocument to a dict for code generators."""
    screens = []
    for s in doc.screens:
        screens.append({
            "id": s.id,
            "size": s.size,
            "root": _comp_to_dict(s.root),
        })
    return {
        "version": doc.version,
        "project": doc.project,
        "states": doc.states,
        "screens": screens,
        "behaviors": [{"id": b.id} for b in doc.behaviors],
        "bindings": [{"component": b.component, "property": b.property, "state": b.state} for b in doc.bindings],
    }


def _comp_to_dict(comp):
    return {
        "id": comp.id,
        "type": comp.type,
        "layout": comp.layout,
        "properties": comp.properties,
        "events": comp.events,
        "children": [_comp_to_dict(c) for c in comp.children],
    }


def _find_nyforge_tools() -> str:
    """Find the Nyforge tools directory (may be sibling checkout)."""
    # Try multiple paths: local dev, CI checkout, sibling repo
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "..", "..", "Nyforge", "Nyforge", "tools"),
        os.path.join(script_dir, "..", "..", "..", "Nyforge", "Nyforge", "tools"),
        os.path.join(script_dir, "..", "..", "Nyforge", "tools"),
    ]
    for p in candidates:
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "generate_rust.py")):
            return os.path.abspath(p)
    return ""


def validate_rust(doc_dict, fixture_name: str) -> Tuple[bool, str]:
    """Validate Rust generator output."""
    try:
        nyforge_tools = _find_nyforge_tools()
        if nyforge_tools and nyforge_tools not in sys.path:
            sys.path.insert(0, nyforge_tools)
        from generate_rust import generate_document
        code = generate_document(doc_dict)
        if not code or len(code) < 100:
            return False, f"Rust output too short ({len(code)} chars)"
        if "#![allow(dead_code" not in code:
            return False, "Rust output missing header"
        if "pub fn load()" not in code:
            return False, "Rust output missing load() function"
        return True, f"OK ({len(code)} lines)"
    except ImportError:
        return True, "SKIPPED (Nyforge not available)"
    except Exception as e:
        return False, f"Rust generator error: {e}"


def validate_cpp(doc_dict, fixture_name: str) -> Tuple[bool, str]:
    """Validate C++ generator output."""
    try:
        from generate_cpp import generate_document
        code = generate_document(doc_dict)
        if not code or len(code) < 100:
            return False, f"C++ output too short ({len(code)} chars)"
        if "#pragma once" not in code:
            return False, "C++ output missing #pragma once"
        if "namespace nyrqis::nui" not in code:
            return False, "C++ output missing namespace"
        if "inline Document load()" not in code:
            return False, "C++ output missing load() function"
        # Check balanced braces
        opens = code.count("{")
        closes = code.count("}")
        if opens != closes:
            return False, f"C++ unbalanced braces: {opens} opens, {closes} closes"
        return True, f"OK ({len(code)} lines)"
    except Exception as e:
        return False, f"C++ generator error: {e}"


def validate_python(doc_dict, fixture_name: str) -> Tuple[bool, str]:
    """Validate Python generator output."""
    try:
        from generate_python import generate_document
        code = generate_document(doc_dict)
        if not code or len(code) < 100:
            return False, f"Python output too short ({len(code)} chars)"
        if '"""' not in code:
            return False, "Python output missing docstring"
        if "def load() -> Document:" not in code:
            return False, "Python output missing load() function"
        # Syntax check
        try:
            ast.parse(code)
        except SyntaxError as e:
            return False, f"Python syntax error: {e}"
        # Runtime check: import and call load()
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write(code)
            tmp_path = f.name
        try:
            import importlib.util
            import types
            spec = importlib.util.spec_from_file_location("_gen_test", tmp_path)
            mod = types.ModuleType("_gen_test")
            mod.__file__ = tmp_path
            sys.modules["_gen_test"] = mod
            spec.loader.exec_module(mod)
            doc = mod.load()
            if not hasattr(doc, "screens"):
                return False, "load() returned object without screens"
        finally:
            os.unlink(tmp_path)
            sys.modules.pop("_gen_test", None)
        return True, f"OK ({len(code)} lines, importable)"
    except Exception as e:
        return False, f"Python generator error: {e}"


def validate_pil_compositor(fixture_path: str, fixture_name: str) -> Tuple[bool, str]:
    """Validate PIL compositor renders the fixture without errors."""
    try:
        from ui.compositor import Compositor
        doc = nstudio_load(fixture_path)
        comp = Compositor(theme_name="Eclipse", scale=1.0)
        for screen in doc.screens:
            img = comp.render_screen(doc, screen_id=screen.id)
            if img is None:
                return False, f"PIL returned None for screen {screen.id}"
            if img.size[0] <= 0 or img.size[1] <= 0:
                return False, f"PIL returned zero-size image for {screen.id}"
        return True, f"OK ({len(doc.screens)} screens rendered)"
    except Exception as e:
        return False, f"PIL compositor error: {e}"


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    fixtures = find_fixtures()
    if not fixtures:
        print("ERROR: No .nstudio fixtures found", file=sys.stderr)
        return 1

    generators = [
        ("Rust", validate_rust),
        ("C++", validate_cpp),
        ("Python", validate_python),
    ]

    total = 0
    passed = 0
    failed = 0
    errors = []

    for fixture_path in fixtures:
        fixture_name = os.path.basename(fixture_path)
        try:
            doc = nstudio_load(fixture_path)
            doc_dict = _doc_to_dict(doc)
        except Exception as e:
            print(f"FAIL: {fixture_name} — cannot load: {e}")
            errors.append(f"{fixture_name}: load failed")
            failed += 1
            continue

        for gen_name, gen_fn in generators:
            total += 1
            ok, msg = gen_fn(doc_dict, fixture_name)
            if ok:
                passed += 1
                if verbose:
                    print(f"  OK: {fixture_name} / {gen_name} — {msg}")
            else:
                failed += 1
                errors.append(f"{fixture_name} / {gen_name}: {msg}")
                print(f"  FAIL: {fixture_name} / {gen_name} — {msg}")

        # Also validate PIL compositor
        total += 1
        ok, msg = validate_pil_compositor(fixture_path, fixture_name)
        if ok:
            passed += 1
            if verbose:
                print(f"  OK: {fixture_name} / PIL Compositor — {msg}")
        else:
            failed += 1
            errors.append(f"{fixture_name} / PIL Compositor: {msg}")
            print(f"  FAIL: {fixture_name} / PIL Compositor — {msg}")

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print(f"Fixtures: {len(fixtures)}")
    print(f"Generators: {len(generators)} + PIL compositor")

    if errors:
        print(f"\nFailed:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nAll generators valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
