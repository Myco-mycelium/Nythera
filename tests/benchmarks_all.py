#!/usr/bin/env python3
"""Benchmark suite for Nyrqis compositor backends and code generators.

Profiles PIL compositor, SDL2 compositor, and all three code generators
(Rust, C++, Python) against the real .nstudio fixtures.

Usage:
    python3 tests/benchmarks_all.py [--repeat N] [--fixtures DIR]

Output: tab-separated results suitable for appending to BENCHMARK_RESULTS.md.
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Tuple

# Ensure the backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.nstudio import load as nstudio_load


def find_fixtures(search_dirs=None):
    """Find .nstudio fixture files."""
    if search_dirs is None:
        search_dirs = [
            os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend", "tests", "fixtures", "nstudio"),
            os.path.join(os.path.dirname(__file__), "..", "source", "nyhal-linux-backend", "tests", "fixtures"),
        ]
    fixtures = {}
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".nstudio"):
                path = os.path.join(d, f)
                try:
                    doc = nstudio_load(path)
                    fixtures[f] = {
                        "path": path,
                        "screens": len(doc.screens),
                        "components": len(doc.component_ids()),
                        "behaviors": len(doc.behaviors),
                    }
                except Exception:
                    pass
    return fixtures


def bench_pil_compositor(doc, repeat=3):
    """Benchmark the PIL compositor."""
    from ui.compositor import Compositor
    comp = Compositor(theme_name="Eclipse", scale=1.0)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        for screen in doc.screens:
            comp.render_screen(doc, screen_id=screen.id)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000  # ms


def bench_sdl_compositor(doc, repeat=3):
    """Benchmark the SDL2 compositor (headless)."""
    try:
        from ui.compositor_sdl import SDLCompositor, HAS_SDL2
        if not HAS_SDL2:
            return None
    except ImportError:
        return None
    comp = SDLCompositor(theme_name="Eclipse", scale=1.0, headless=True)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        for screen in doc.screens:
            comp.render_screen(doc, screen_id=screen.id)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000  # ms


def bench_rust_generator(doc, repeat=3):
    """Benchmark the Rust code generator."""
    try:
        from tools.generate_rust import generate_document as gen_rust
    except ImportError:
        # Rust generator may be in Nyforge repo
        nyforge_path = os.path.join(os.path.dirname(__file__), "..", "..", "Nyforge", "Nyforge", "tools")
        if os.path.isdir(nyforge_path):
            sys.path.insert(0, nyforge_path)
            from generate_rust import generate_document as gen_rust
        else:
            return None
    doc_dict = _doc_to_dict(doc)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        gen_rust(doc_dict)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000  # ms


def bench_cpp_generator(doc, repeat=3):
    """Benchmark the C++ code generator."""
    from tools.generate_cpp import generate_document as gen_cpp
    doc_dict = _doc_to_dict(doc)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        gen_cpp(doc_dict)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000  # ms


def bench_python_generator(doc, repeat=3):
    """Benchmark the Python code generator."""
    from tools.generate_python import generate_document as gen_py
    doc_dict = _doc_to_dict(doc)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        gen_py(doc_dict)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000  # ms


def bench_pil_solar(doc, repeat=3):
    """Benchmark the PIL compositor with Solar theme."""
    from ui.compositor import Compositor
    comp = Compositor(theme_name="Solar", scale=1.0)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        for screen in doc.screens:
            comp.render_screen(doc, screen_id=screen.id)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000


def bench_pil_2x(doc, repeat=3):
    """Benchmark the PIL compositor at 2x scale."""
    from ui.compositor import Compositor
    comp = Compositor(theme_name="Eclipse", scale=2.0)
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        for screen in doc.screens:
            comp.render_screen(doc, screen_id=screen.id)
        times.append(time.perf_counter() - t0)
    return min(times) * 1000


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
    """Convert a NstudioComponent to a dict."""
    return {
        "id": comp.id,
        "type": comp.type,
        "layout": comp.layout,
        "properties": comp.properties,
        "events": comp.events,
        "children": [_comp_to_dict(c) for c in comp.children],
    }


BENCHMARKS = [
    ("PIL Eclipse 1x", bench_pil_compositor),
    ("PIL Solar 1x", bench_pil_solar),
    ("PIL Eclipse 2x", bench_pil_2x),
    ("SDL2 headless", bench_sdl_compositor),
    ("Rust generator", bench_rust_generator),
    ("C++ generator", bench_cpp_generator),
    ("Python generator", bench_python_generator),
]


def main():
    parser = argparse.ArgumentParser(description="Benchmark all backends")
    parser.add_argument("--repeat", type=int, default=3, help="Repeat count (default: 3)")
    parser.add_argument("--fixtures", default=None, help="Fixtures directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    search_dirs = [args.fixtures] if args.fixtures else None
    fixtures = find_fixtures(search_dirs)

    if not fixtures:
        print("No fixtures found.", file=sys.stderr)
        return 1

    results = []
    for name, info in fixtures.items():
        doc = nstudio_load(info["path"])
        row = {"fixture": name, "screens": info["screens"],
               "components": info["components"], "behaviors": info["behaviors"]}
        for bench_name, bench_fn in BENCHMARKS:
            try:
                ms = bench_fn(doc, repeat=args.repeat)
                row[bench_name] = round(ms, 2) if ms is not None else "N/A"
            except Exception as e:
                row[bench_name] = f"ERROR: {e}"
        results.append(row)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Tab-separated output
        headers = ["fixture", "screens", "components", "behaviors"] + [b[0] for b in BENCHMARKS]
        print("\t".join(headers))
        for row in results:
            vals = [str(row.get(h, "")) for h in headers]
            print("\t".join(vals))

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
